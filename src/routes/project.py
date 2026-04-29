from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import Optional
import logging
import re
import secrets
import string

from src.schemas.resources_request import (
    GetProjectsRequest,
    GetProjectRequest,
    CreateProjectRequest,
    CreateProjectFromDescriptionRequest,
    AcceptProjectSpecificationRequest,
    CreateProjectFromFigmaRequest,
    UpdateProjectRequest,
    DeleteProjectRequest,
    ProjectClarificationRequest,
    StartProjectClarificationRequest,
    GenerateProjectSpecFromFigmaRequest,
)
from src.schemas.jira_import import ImportProjectFromJiraRequest, ListJiraProjectsRequest
from src.schemas.response import ResponseModel
from src.schemas.resources_response import (
    GetProjectsResponse,
    GetProjectResponse,
    ProjectResponse
)
from src.schemas.member_schemas import ProjectInvitationRequest, TeamMemberCreateRequest, TeamMemberUpdate

from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user

from src.utils.planning.projects import (
    get_all_projects_for_user,
    get_project_by_id,
    update_project,
    delete_project,
    get_project_for_user,
)

from src.utils.ai.project_creation_qa.clarification import start_clarification, submit_answers

from src.services.workflows.project_creation.common import (
    create_project_record,
    ProjectKeyConflictError,
    ProjectRecordCreationError,
)
from src.services.workflows.project_creation.orchestrator import (
    ProjectCreationOrchestrator,
    ProjectOrchestrationError,
)
from src.services.workflows.project_import.orchestrator import (
    ProjectImportOrchestrator,
    ProjectImportOrchestrationError,
)
from src.services.workflows.project_creation.project_creation_by_document.initialization import (
    DocumentTextError,
    extract_text_from_bytes,
    start_file_extraction,
)
from src.services.workflows.project_creation.finalization import (
    EpicGenerationFailedError,
    ProjectFinalizationError,
    ProjectNotFoundError,
    ProjectNotReadyError,
)
from src.services.workflows.project_import.project_import_from_jira.initialization import (
    JiraApiError,
    JiraAuthenticationError,
    JiraImportError,
    list_jira_projects,
)
from src.services.notifications import NotificationService
from src.utils.ai.project_creation_source_spec import generate_spec_from_source


from src.utils.authz.permissions import get_project_access, get_global_user_role
from src.utils.authz.project_memberships import normalize_membership_role
from src.utils.authz.admin_utils import create_firebase_user, get_firebase_user_by_email
from src.utils.planning.members import (
    get_project_members as get_team_members,
    create_team_member,
    get_member_by_id,
    update_team_member,
    remove_team_member,
    check_member_exists,
    format_team_members_response,
    format_team_member_response
)
from src.utils.planning.epics import get_epics_for_project_with_auth, get_epics_for_project
from src.utils.planning.user_stories import get_user_stories_by_epic
from src.utils.planning.invitations import (
    create_invitation,
    get_project_invitations,
    get_invitation_by_id,
    cancel_invitation,
    resend_invitation,
    check_pending_invitation,
    check_and_expire_invitations,
)

router = APIRouter()
logger = logging.getLogger(__name__)
notification_service = NotificationService()
SOFTTEK_EMAIL_DOMAIN = "@softtek.com"


def _derive_project_name(description: str) -> str:
    cleaned = " ".join((description or "").strip().split())
    if not cleaned:
        return "New Project"

    first_sentence = re.split(r"[.!?]", cleaned, maxsplit=1)[0].strip()
    name = first_sentence or cleaned
    if len(name) > 60:
        name = f"{name[:57].rstrip()}..."
    return name


def _sanitize_project_key(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _derive_project_key_base(name: str, description: str) -> str:
    candidates = re.findall(r"[A-Za-z0-9]+", (name or ""))
    if not candidates:
        candidates = re.findall(r"[A-Za-z0-9]+", (description or ""))

    acronym = "".join(word[0].upper() for word in candidates if word)
    acronym = (acronym + "PRJ")[:3]
    return _sanitize_project_key(acronym)


def _random_project_key_suffix(length: int = 3) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(1, length)))


def _normalize_invitation_payload(invitation: dict) -> dict:
    payload = dict(invitation or {})
    payload["projectId"] = payload.get("projectId") or payload.get("project_id")
    payload["memberType"] = payload.get("memberType") or payload.get("member_type") or "member"
    payload["invitedBy"] = payload.get("invitedBy") or payload.get("invited_by")
    payload["invitedByName"] = payload.get("invitedByName") or payload.get("invited_by_name")
    payload["invitedDate"] = payload.get("invitedDate") or payload.get("invited_date")
    payload["expiresDate"] = payload.get("expiresDate") or payload.get("expires_date")
    payload["responseDate"] = payload.get("responseDate") or payload.get("response_date")
    return payload


def _requester_member_type(access_data: Optional[dict]) -> str:
    if (access_data or {}).get("is_owner"):
        return "leader"
    return normalize_membership_role((access_data or {}).get("member_type"))


def _normalize_email(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _is_softtek_email(email: Optional[str]) -> bool:
    return _normalize_email(email).endswith(SOFTTEK_EMAIL_DOMAIN)


def _derive_account_username(email: Optional[str]) -> str:
    clean_email = _normalize_email(email)
    local_part = clean_email.split("@", 1)[0] if clean_email else ""
    return local_part or "user"


async def _ensure_invited_account_if_needed(
    *,
    email: str,
    name: str,
    role: str,
    seniority: str,
    team_id: Optional[str],
) -> dict:
    clean_email = _normalize_email(email)
    existing_user = get_firebase_user_by_email(clean_email)
    if existing_user:
        return {
            "created": False,
            "user_id": existing_user.uid,
            "username": _derive_account_username(clean_email),
            "password": None,
        }

    if not _is_softtek_email(clean_email):
        return {
            "created": False,
            "user_id": None,
            "username": _derive_account_username(clean_email),
            "password": None,
        }

    account_username = _derive_account_username(clean_email)
    creation_response = await create_firebase_user(
        {
            "email": clean_email,
            "password": account_username,
            "team_id": team_id,
            "name": name,
            "role": role,
            "seniority": seniority,
        }
    )
    if not creation_response.success:
        raise HTTPException(status_code=500, detail="Failed to auto-create invited account")

    created_user_id = None
    if isinstance(creation_response.data, dict):
        created_user_id = creation_response.data.get("user_id")

    return {
        "created": True,
        "user_id": created_user_id,
        "username": account_username,
        "password": account_username,
    }

@router.get(
    "/{project_id}",
    response_description="Get a single project by ID with authentication.",
)
async def get_project_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
) -> GetProjectResponse:
    """
    Retrieves a single project by ID. Requires authentication and user must own the project.
    
    Args:
        project_id (str): The project ID to retrieve
        authorization (str): Authorization header with Bearer token (e.g., "Bearer your_token_here")
    
    Returns:
        GetProjectResponse: The project data
    """
    try:
        response = get_project_by_id(project_id, user_data.user_id, allow_member=True, user_email=user_data.email)
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get project")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get(
    "/",
    response_description="Get all projects for the user.",
)
async def get_projects_route(
    user_data: UserData = Depends(get_current_user),
) -> GetProjectsResponse:
    """
    Retrieves all projects for the authenticated user.
    """
    try:
        response = get_all_projects_for_user(
            user_data.user_id,
            include_member_projects=True,
            user_email=user_data.email
        )
        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list projects")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post(
    "/",
    response_description="Create a new project.",
)
async def create_project_route(
    req: CreateProjectFromDescriptionRequest,
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    Creates a new project from a description.

    The client can optionally provide `name` and/or `project_key`; if omitted, the backend
    will generate reasonable defaults.
    """
    role_info = get_global_user_role(user_data)
    if role_info.get("role") == "member":
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create projects")

    try:
        description = (req.description or "").strip()
        if not description:
            raise HTTPException(status_code=400, detail="description is required")

        name = (req.name or "").strip() or _derive_project_name(description)
        requested_key = _sanitize_project_key(req.project_key or "")

        if requested_key:
            project_record = create_project_record(
                user_data=user_data,
                name=name,
                description=description,
                project_key=requested_key,
                creation_status="created",
                creation_source="manual",
            )
        else:
            base_key = _derive_project_key_base(name, description)
            project_record = None
            last_exc: Optional[Exception] = None
            candidates = [base_key]
            candidates.extend(f"{base_key[:2]}{_random_project_key_suffix(1)}" for _ in range(25))
            candidates.extend(_random_project_key_suffix(3) for _ in range(25))

            for candidate in candidates:
                try:
                    project_record = create_project_record(
                        user_data=user_data,
                        name=name,
                        description=description,
                        project_key=candidate,
                        creation_status="created",
                        creation_source="manual",
                    )
                    last_exc = None
                    break
                except ProjectKeyConflictError as exc:
                    last_exc = exc

            if project_record is None:
                raise ProjectRecordCreationError(str(last_exc or "Failed to generate unique project key"))

        data = {
            "project": project_record.project.dict(),
            "clarification": None,
        }
        response = ResponseModel(
            success=True,
            message="Project created.",
            data=data,
        )
        return JSONResponse(status_code=201, content=response.dict())
    except (ProjectOrchestrationError, ProjectRecordCreationError) as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create project")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.patch(
    "/{project_id}",
    response_description="Update an existing project.",
)
async def update_project_route(
    project_id: str = Path(..., description="The project ID"),
    req: UpdateProjectRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    Updates an existing project. User must own the project.
    """
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update projects")

    try:
        response = update_project(
            project_id=project_id,
            user_id=user_data.user_id,
            name=req.name if req else None,
            description=req.description if req else None,
            project_key=req.project_key if req else None,
            tech_stack=req.tech_stack if req else None
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update project")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete(
    "/{project_id}",
    response_description="Delete a project.",
)
async def delete_project_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    Deletes a project. User must own the project.
    """
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot delete projects")

    try:
        response = delete_project(
            project_id=project_id,
            user_id=user_data.user_id
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete project")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{project_id}/epics",
    response_description="Get all epics for a project.",
)
async def get_project_epics_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Retrieves all epics for a specific project. User must own the project.
    """
    try:
        response = get_epics_for_project_with_auth(project_id, user_data.user_id, user_data.email)
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get project epics")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get(
    "/{project_id}/members",
    response_description="Get detailed project info including epics.",
)
async def get_project_members_route(
    project_id: str = Path(..., description="The project ID"),
    status: Optional[str] = None,
    role: Optional[str] = None,
    seniority: Optional[str] = None,
    user_data: UserData = Depends(get_current_user),
):
    """
    Retrieves detailed project info including members. User must own the project.
    """
    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=access.dict(),
            )

        members = get_team_members(project_id, status=status, role=role, seniority=seniority)
        response = ResponseModel(
            success=True,
            message="",
            data=format_team_members_response(members)
        )
        return JSONResponse(
            status_code=200,
            content=response.dict()
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get project members")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/members",
    response_description="Add a member directly to a project.",
)
async def add_project_member_route(
    project_id: str = Path(..., description="The project ID"),
    req: TeamMemberCreateRequest = None,
    user_data: UserData = Depends(get_current_user),
):
    """
    Adds a member directly to a project without invitations.
    """
    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=access.dict(),
            )
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot add members")
        if req.member_type == "leader":
            raise HTTPException(status_code=400, detail="The project leader is always the project creator")
        if req.member_type == "coleader" and not access.data.get("can_manage_member_types"):
            raise HTTPException(status_code=403, detail="Forbidden: Only the leader can assign coleaders")

        if check_member_exists(project_id, req.email):
            raise HTTPException(status_code=409, detail="Member already exists for this project")

        avatar = req.avatar
        if not avatar:
            parts = [part for part in req.name.strip().split(" ") if part]
            initials = "".join([part[0].upper() for part in parts[:2]])
            avatar = initials

        account_provisioning = await _ensure_invited_account_if_needed(
            email=req.email,
            name=req.name,
            role=req.role,
            seniority=req.seniority,
            team_id=user_data.get_team_id(),
        )
        if account_provisioning.get("created"):
            notification_service.try_send_account_created(
                account_name=req.name,
                account_email=_normalize_email(req.email),
                password=account_provisioning.get("password") or "",
            )

        created_member = create_team_member(
            project_id=project_id,
            user_id=account_provisioning.get("user_id"),
            name=req.name,
            email=req.email,
            role=req.role,
            seniority=req.seniority,
            avatar=avatar,
            member_type=req.member_type,
        )
        project_name = str((access.data or {}).get("project", {}).get("name") or "").strip() or "your project"
        added_by_name = user_data.get_user_name() or user_data.get_email() or "A FridaPlatform administrator"
        notification_service.try_send_project_member_added(
            member_name=req.name,
            member_email=req.email,
            project_name=project_name,
            added_by_name=added_by_name,
            role=req.role,
            seniority=req.seniority,
        )

        response = ResponseModel(
            success=True,
            message="",
            data=format_team_member_response(created_member)
        )
        return JSONResponse(
            status_code=201,
            content=response.dict()
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to add project member")
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================================
# Project Generation by Q&A endpoints
# ====================================

"""
 Workflow:
    1. `POST /project/creation/qa` with project metadata.
    2. `POST /project/{project_id}/clarification/answer` repeatedly until spec is ready.
    3. `POST /project/{project_id}/spec/accept` to finalize and generate epics.
"""

@router.post(
    "/creation/qa",
    response_description="Start Q&A-based project creation.",
)
async def create_project_from_qa_route(
    req: CreateProjectRequest,
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    Start project creation using the Q&A workflow.

    This endpoint creates a project draft from `name`, `project_key`, and 
    `description`, then returns the project creation response used to continue the
    clarification flow.
    """
    role_info = get_global_user_role(user_data)
    if role_info.get("role") == "member":
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create projects")

    try:
        data = await ProjectCreationOrchestrator("qa").initialize_project(
            user_data=user_data,
            name=req.name,
            description=req.description,
            project_key=req.project_key,
        )
        response = ResponseModel(
            success=True,
            message="Project created. Clarification started.",
            data=data.dict(),
        )
        return JSONResponse(status_code=201, content=response.dict())
    except (ProjectOrchestrationError, ProjectRecordCreationError) as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create project")
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================================
# Project Import from Jira endpoints
# ====================================


@router.post(
    "/creation/jira/projects",
    response_description="List Jira projects available to the given credentials.",
)
async def list_jira_projects_route(
    req: ListJiraProjectsRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Fetches Jira projects for the UI flow.

    The user must be a project leader to use this endpoint.
    """
    role_info = get_global_user_role(user_data)
    if role_info.get("role") == "member":
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create projects")

    try:
        projects = await list_jira_projects(
            jira_base_url=req.jira_base_url,
            jira_email=req.jira_email,
            jira_api_token=req.jira_api_token,
        )
        response = ResponseModel(
            success=True,
            message="Jira projects retrieved.",
            data=projects,
        )
        return JSONResponse(status_code=200, content=response.dict())
    except JiraAuthenticationError as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=401, content=response.dict())
    except (JiraImportError, JiraApiError) as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list Jira projects")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/creation/jira",
    response_description="Import a Jira project and create epics/user stories.",
)
async def import_project_from_jira_route(
    req: ImportProjectFromJiraRequest,
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    Imports Epics + User Stories from Jira into a new Product Planner project.

    The user must be a project leader to start an import.
    """
    role_info = get_global_user_role(user_data)
    if role_info.get("role") == "member":
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create projects")

    try:
        data = await ProjectImportOrchestrator("jira").import_project(
            user_data=user_data,
            name=req.name,
            description=req.description,
            project_key=req.project_key,
            jira_base_url=req.jira_base_url,
            jira_email=req.jira_email,
            jira_api_token=req.jira_api_token,
            jira_project_key=req.jira_project_key,
            issue_types=req.issue_types,
        )
        response = ResponseModel(
            success=True,
            message="Project imported from Jira.",
            data=data.dict(),
        )
        return JSONResponse(status_code=201, content=response.dict())
    except JiraAuthenticationError as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=401, content=response.dict())
    except (JiraImportError, JiraApiError, ProjectImportOrchestrationError, ProjectRecordCreationError) as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to import project from Jira")
        raise HTTPException(status_code=500, detail="Internal server error")
     

@router.post(
    "/{project_id}/clarification/start",
    response_description="Start clarification for an existing project.",
)
async def start_project_clarification_route(
    project_id: str = Path(..., description="The project ID"),
    req: StartProjectClarificationRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Starts the Q&A clarification flow for an existing project.

    This is intended for batch epic generation from the Epics page (Q&A mode).
    """
    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(status_code=status_code, content=access.dict())
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update projects")

        project = access.data.get("project") or {}

        description = ""
        if req and req.description is not None:
            description = str(req.description or "")
        if not description.strip():
            description = str(project.get("description") or "")

        description = description.strip()
        if not description:
            raise HTTPException(status_code=400, detail="description is required")

        response = await start_clarification(
            user_data=user_data,
            project_id=project_id,
            description=description,
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to start project clarification")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/clarification/answer",
    response_description="Submit clarification answers and fetch next questions or spec.",
)
async def submit_project_clarification_route(
    project_id: str = Path(..., description="The project ID"),
    req: ProjectClarificationRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not req.answers:
        raise HTTPException(status_code=400, detail="answers are required")

    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(status_code=status_code, content=access.dict())
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update projects")

        response = await submit_answers(
            user_data=user_data,
            project_id=project_id,
            answers=[answer.dict() for answer in req.answers],
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to submit project clarification")
        raise HTTPException(status_code=500, detail="Internal server error")
     


# =========================================
# Project Generation by File Upload GOOd ONe 
# =========================================
"""
Steps:
1) POST /project/creation/file with name, project_key, description (optional), and a PDF/DOCX file
2) Review spec text/url returned (status=spec_ready)
3) POST /project/{project_id}/spec/accept to finalize and generate epics
"""

@router.post(
    "/creation/file",
    response_description="Start file-based project creation (PDF/DOCX).",
)
async def create_project_from_file_route(
    name: str = Form(...),
    project_key: str = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    First step
    Start project creation from an uploaded PDF/DOCX file.

    Flow:
    1) Accept multipart form-data with `name`, `project_key`, optional `description`, and `file`.
    2) Extract text from the file and build a source payload.
    3) Delegate to the file-based AI creation flow.

    Returns:
        ProjectResponse: Creation response with clarification/spec state.
    """
    role_info = get_global_user_role(user_data)
    if role_info.get("role") == "member":
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create projects")

    if not file:
        raise HTTPException(status_code=400, detail="file is required")

    try:
        file_bytes = await file.read()
    except Exception:
        logger.exception("Failed to read uploaded file")
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    try:
        data = await ProjectCreationOrchestrator("file").initialize_project(
            user_data=user_data,
            name=name,
            description=description or "",
            project_key=project_key,
            file_bytes=file_bytes,
            filename=file.filename,
            content_type=file.content_type,
        )
        response = ResponseModel(
            success=True,
            message="Project created. File extraction started.",
            data=data.dict(),
        )
        return JSONResponse(status_code=201, content=response.dict())
    except (DocumentTextError, ProjectOrchestrationError, ProjectRecordCreationError) as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create project from file")
        raise HTTPException(status_code=500, detail="Internal server error")
     

@router.post(
    "/{project_id}/spec/source/file",
    response_description="Generate a specification from a document for an existing project.",
)
async def generate_project_spec_from_file_route(
    project_id: str = Path(..., description="The project ID"),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Generates a spec from an uploaded PDF/DOCX file for an existing project.

    Intended for batch epic creation from the Epics page (Document mode).
    """
    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(status_code=status_code, content=access.dict())
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update projects")

        project = access.data.get("project") or {}

        if not file:
            raise HTTPException(status_code=400, detail="file is required")

        try:
            file_bytes = await file.read()
        except Exception:
            logger.exception("Failed to read uploaded file")
            raise HTTPException(status_code=400, detail="Failed to read uploaded file")

        document_text = extract_text_from_bytes(
            file_bytes,
            filename=file.filename,
            content_type=file.content_type,
        )
        if not document_text:
            raise DocumentTextError("Uploaded file contains no readable text")

        source_payload = {
            "filename": file.filename,
            "content_type": file.content_type,
            "size_bytes": len(file_bytes),
            "text_excerpt": document_text[:2000],
        }

        project_name = str(project.get("name") or "").strip() or "Project"
        effective_description = (description or project.get("description") or "").strip()

        clarification = await start_file_extraction(
            user_data=user_data,
            project_id=project_id,
            project_name=project_name,
            description=effective_description,
            document_text=document_text,
            source_payload=source_payload,
        )

        response = ResponseModel(
            success=True,
            message="Specification generated",
            data=clarification.dict(),
        )
        return JSONResponse(status_code=200, content=response.dict())
    except DocumentTextError as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to generate project spec from file")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/spec/accept",
    response_description="Accept specification document and finalize project.",
)
async def accept_project_spec_route(
    project_id: str = Path(..., description="The project ID"),
    req: AcceptProjectSpecificationRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Accepts the generated specification document for a project and finalizes the project.

    This endpoint is the final step in the file/Figma-based project creation flows:
    once a project is in a "spec_ready" state, the project lead can accept the spec to
    trigger downstream generation (e.g., epics) via `finalize_project_from_spec`.

    Authorization:
        - Requires authentication.
        - Only the project lead (`is_lead`) may accept/finalize the spec.

    Args:
        project_id (str): Target project ID.
        user_data (UserData): Authenticated user context (injected by `get_current_user`).

    Returns:
        ResponseModel: Serialized response payload wrapped in a JSON response.
            - 200 when the project was finalized successfully.
            - 400 when finalization fails (e.g., invalid spec state).
            - 403 when the user is authenticated but not allowed.
            - 404 when the project is not found (as reported by access check).

    Raises:
        HTTPException: For authorization failures, invalid input, or internal errors.
    """
    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(status_code=status_code, content=access.dict())
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update projects")

        try:
            data = await ProjectCreationOrchestrator().complete(
                user_data=user_data,
                project_id=project_id,
                spec_text_override=(req.spec_text if req else None),
            )
        except ProjectNotFoundError as exc:
            response = ResponseModel(success=False, message=str(exc), data=None)
            return JSONResponse(status_code=404, content=response.dict())
        except ProjectNotReadyError as exc:
            response = ResponseModel(success=False, message=str(exc), data=None)
            return JSONResponse(status_code=400, content=response.dict())
        except EpicGenerationFailedError as exc:
            response = ResponseModel(success=False, message=str(exc), data=None)
            return JSONResponse(status_code=400, content=response.dict())
        except ProjectFinalizationError as exc:
            response = ResponseModel(success=False, message=str(exc), data=None)
            return JSONResponse(status_code=400, content=response.dict())

        response = ResponseModel(success=True, message="Project finalized successfully", data=data.dict())
        return JSONResponse(status_code=200, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to accept project spec")
        raise HTTPException(status_code=500, detail="Internal server error")


# ======================================
# Project Generation by Figma endpoints
# ======================================

@router.post(
    "/{project_id}/spec/source/figma",
    response_description="Generate a specification from a Figma link for an existing project.",
)
async def generate_project_spec_from_figma_route(
    project_id: str = Path(..., description="The project ID"),
    req: GenerateProjectSpecFromFigmaRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Generates a spec from a Figma URL/notes for an existing project.

    Intended for batch epic creation from the Epics page (Figma mode).
    """
    if not req or not (req.figma_url or "").strip():
        raise HTTPException(status_code=400, detail="figma_url is required")

    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(status_code=status_code, content=access.dict())
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update projects")

        project = access.data.get("project") or {}

        figma_payload = {
            "url": (req.figma_url or "").strip(),
            "notes": req.figma_notes,
        }

        effective_description = (req.description or project.get("description") or "").strip()
        response = await generate_spec_from_source(
            user_data=user_data,
            project_id=project_id,
            description=effective_description,
            source_type="figma",
            source_payload=figma_payload,
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to generate project spec from figma")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/creation/figma",
    response_description="Start Figma-based project creation.",
)
async def create_project_from_figma_route(
    req: CreateProjectFromFigmaRequest,
    user_data: UserData = Depends(get_current_user),
) -> ProjectResponse:
    """
    Steps:
    1) POST /project/creation/figma with name, project_key, figma_url (optional notes)
    2) Review spec text/url returned (status=spec_ready)
    3) POST /project/{project_id}/spec/accept to finalize and generate epics
    """
    role_info = get_global_user_role(user_data)
    if role_info.get("role") == "member":
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create projects")

    figma_payload = {
        "url": req.figma_url,
        "notes": req.figma_notes,
    }

    try:
        data = await ProjectCreationOrchestrator("figma").initialize_project(
            user_data=user_data,
            name=req.name,
            project_key=req.project_key,
            description=req.description or "",
            figma_payload=figma_payload,
        )
        response = ResponseModel(
            success=True,
            message="Project created. Figma link processed.",
            data=data.dict(),
        )
        return JSONResponse(status_code=201, content=response.dict())
    except (ProjectOrchestrationError, ProjectRecordCreationError) as exc:
        response = ResponseModel(success=False, message=str(exc), data=None)
        return JSONResponse(status_code=400, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create project from figma")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{project_id}/stats-timeline/",
    response_description="Get timeline-ready stats payload for a project.",
)
async def get_project_stats_timeline_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())

    try:
        epics = get_epics_for_project(project_id, user_data.user_id) or []
        payload_epics = []

        for epic in epics:
            epic_id = epic.get("id")
            stories_response = get_user_stories_by_epic(epic_id, user_data.user_id, allow_member=True)
            stories = stories_response.data if stories_response and stories_response.success else []

            normalized_stories = []
            for story in stories or []:
                effort_hours = story.get("effortHours")
                if effort_hours is None:
                    effort_hours = story.get("effort_hours")
                normalized_stories.append({
                    "id": story.get("id"),
                    "user_story_id": story.get("user_story_id"),
                    "status": story.get("status") or "To Do",
                    "createdDate": story.get("createdDate") or story.get("created_at"),
                    "startDate": story.get("startDate") or story.get("start_date"),
                    "dueDate": story.get("dueDate") or story.get("due_date"),
                    "sprint_id": story.get("sprint_id"),
                    "effortHours": effort_hours,
                    "effort_hours": effort_hours,
                })

            payload_epics.append({
                "id": epic_id,
                "name": epic.get("name"),
                "status": epic.get("status") or "To Do",
                "userStories": normalized_stories,
            })

        response = ResponseModel(
            success=True,
            message="Stats timeline fetched successfully",
            data={
                "projectId": project_id,
                "epics": payload_epics,
            },
        )
        return JSONResponse(status_code=200, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get project stats timeline")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/members/invite",
    response_description="Invite a new member to a project.",
)
async def invite_project_member_route(
    req: ProjectInvitationRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot invite members")

    name = req.name
    email = req.email
    role = req.role
    seniority = req.seniority
    member_type = req.member_type

    try:
        if check_member_exists(project_id, email):
            raise HTTPException(status_code=409, detail="Member already exists for this project")
        if check_pending_invitation(project_id, email):
            raise HTTPException(status_code=409, detail="A pending invitation already exists for this email")
        if member_type == "leader":
            raise HTTPException(status_code=400, detail="The project leader is always the project creator")
        if member_type == "coleader" and not access.data.get("can_manage_member_types"):
            raise HTTPException(status_code=403, detail="Forbidden: Only the leader can assign coleaders")

        account_provisioning = await _ensure_invited_account_if_needed(
            email=email,
            name=name,
            role=role,
            seniority=seniority,
            team_id=user_data.get_team_id(),
        )
        if account_provisioning.get("created"):
            notification_service.try_send_account_created(
                account_name=name,
                account_email=_normalize_email(email),
                password=account_provisioning.get("password") or "",
            )

        invitation = create_invitation(
            project_id=project_id,
            invited_by=user_data.user_id,
            invited_by_name=user_data.get_user_name(),
            name=name,
            email=email,
            role=role,
            seniority=seniority,
            member_type=member_type,
        )
        invitation = _normalize_invitation_payload(invitation)
        project_name = str((access.data or {}).get("project", {}).get("name") or "").strip() or "your project"
        inviter_name = user_data.get_user_name() or user_data.get_email() or "A FridaPlatform administrator"
        notification_service.try_send_project_invitation(
            invitee_name=name,
            invitee_email=email,
            project_name=project_name,
            inviter_name=inviter_name,
            role=role,
            seniority=seniority,
            expires_at=invitation.get("expiresDate") or invitation.get("expires_date"),
        )
        invitation["accountCreated"] = bool(account_provisioning.get("created"))
        if account_provisioning.get("created"):
            invitation["generatedCredentials"] = {
                "email": _normalize_email(email),
                "password": account_provisioning.get("password"),
            }

        return JSONResponse(
            status_code=201,
            content=ResponseModel(
                success=True,
                message="Invitation sent successfully",
                data=invitation,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to invite project member")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{project_id}/invitations",
    response_description="List project invitations.",
)
async def get_project_invitations_route(
    project_id: str = Path(..., description="The project ID"),
    status: Optional[str] = None,
    user_data: UserData = Depends(get_current_user),
):
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot view invitations")

    try:
        check_and_expire_invitations(project_id)
        invitations = get_project_invitations(project_id, status=status)
        invitations = [_normalize_invitation_payload(invitation) for invitation in invitations]
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="Invitations fetched successfully",
                data=invitations,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get project invitations")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{project_id}/invitations/{invitation_id}",
    response_description="Cancel a project invitation.",
)
async def cancel_project_invitation_route(
    project_id: str = Path(..., description="The project ID"),
    invitation_id: str = Path(..., description="Invitation ID"),
    user_data: UserData = Depends(get_current_user),
):
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot cancel invitations")

    invitation = get_invitation_by_id(invitation_id)
    if not invitation or invitation.get("project_id") != project_id:
        return JSONResponse(
            status_code=404,
            content=ResponseModel(success=False, message="Invitation not found", data=None).dict(),
        )

    try:
        cancelled = cancel_invitation(invitation_id)
        if not cancelled:
            return JSONResponse(
                status_code=400,
                content=ResponseModel(
                    success=False,
                    message="Invitation cannot be cancelled",
                    data=None,
                ).dict(),
            )
        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="Invitation cancelled successfully",
                data=None,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to cancel project invitation")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/invitations/{invitation_id}/resend",
    response_description="Resend a project invitation.",
)
async def resend_project_invitation_route(
    project_id: str = Path(..., description="The project ID"),
    invitation_id: str = Path(..., description="Invitation ID"),
    user_data: UserData = Depends(get_current_user),
):
    access = get_project_access(project_id, user_data.user_id, user_data.email)
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot resend invitations")

    invitation = get_invitation_by_id(invitation_id)
    if not invitation or invitation.get("project_id") != project_id:
        return JSONResponse(
            status_code=404,
            content=ResponseModel(success=False, message="Invitation not found", data=None).dict(),
        )

    try:
        resent = resend_invitation(invitation_id)
        if not resent:
            return JSONResponse(
                status_code=400,
                content=ResponseModel(
                    success=False,
                    message="Invitation cannot be resent",
                    data=None,
                ).dict(),
            )

        updated_invitation, plain_token = resent
        payload = _normalize_invitation_payload(updated_invitation)
        payload["invitation_token"] = plain_token
        project_name = str((access.data or {}).get("project", {}).get("name") or "").strip() or "your project"
        inviter_name = user_data.get_user_name() or user_data.get_email() or "A FridaPlatform administrator"
        notification_service.try_send_project_invitation(
            invitee_name=str(payload.get("name") or "").strip() or str(payload.get("email") or "").strip(),
            invitee_email=str(payload.get("email") or "").strip(),
            project_name=project_name,
            inviter_name=inviter_name,
            role=str(payload.get("role") or "").strip(),
            seniority=str(payload.get("seniority") or "").strip(),
            expires_at=payload.get("expiresDate") or payload.get("expires_date"),
        )

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="Invitation resent successfully",
                data=payload,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to resend project invitation")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{project_id}/members/{member_id}",
    response_description="Update a project member.",
)
async def update_project_member_route(
    project_id: str = Path(..., description="The project ID"),
    member_id: str = Path(..., description="The member ID"),
    req: TeamMemberUpdate = None,
    user_data: UserData = Depends(get_current_user),
):
    """
    Updates role or seniority for a project member.
    """
    if not req or (req.role is None and req.seniority is None and req.member_type is None):
        raise HTTPException(status_code=400, detail="At least one field (role, seniority, member_type) is required")

    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=access.dict(),
            )
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update members")

        existing_member = get_member_by_id(project_id, member_id)
        if not existing_member:
            raise HTTPException(status_code=404, detail="Member not found")

        requester_member_type = _requester_member_type(access.data)
        target_member_type = normalize_membership_role(existing_member.get("member_type"))
        requested_member_type = normalize_membership_role(req.member_type) if req.member_type is not None else None

        if target_member_type == "leader":
            raise HTTPException(status_code=403, detail="The project leader cannot be updated here")
        if target_member_type == "coleader" and requester_member_type != "leader":
            raise HTTPException(status_code=403, detail="Forbidden: Only the leader can update a coleader")
        if requested_member_type == "leader":
            raise HTTPException(status_code=400, detail="The project leader is always the project creator")
        if requested_member_type == "coleader" and not access.data.get("can_manage_member_types"):
            raise HTTPException(status_code=403, detail="Forbidden: Only the leader can assign coleaders")
        if (
            requested_member_type is not None
            and requested_member_type != target_member_type
            and not access.data.get("can_manage_member_types")
        ):
            raise HTTPException(status_code=403, detail="Forbidden: Only the leader can change member types")

        updated_member = update_team_member(
            project_id=project_id,
            member_id=member_id,
            role=req.role,
            seniority=req.seniority,
            member_type=req.member_type,
        )

        if not updated_member:
            raise HTTPException(status_code=404, detail="Member not found")

        response = ResponseModel(
            success=True,
            message="",
            data=format_team_member_response(updated_member)
        )
        return JSONResponse(
            status_code=200,
            content=response.dict()
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update project member")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{project_id}/members/{member_id}",
    response_description="Remove a member from a project.",
)
async def remove_project_member_route(
    project_id: str = Path(..., description="The project ID"),
    member_id: str = Path(..., description="The member ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Removes a member from a project.
    """
    try:
        access = get_project_access(project_id, user_data.user_id, user_data.email)
        if not access.success:
            status_code = 404 if "not found" in access.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=access.dict(),
            )
        if not access.data.get("is_lead"):
            raise HTTPException(status_code=403, detail="Forbidden: Team members cannot remove members")

        existing_member = get_member_by_id(project_id, member_id)
        if not existing_member:
            raise HTTPException(status_code=404, detail="Member not found")

        requester_member_type = _requester_member_type(access.data)
        target_member_type = normalize_membership_role(existing_member.get("member_type"))
        if target_member_type == "leader":
            raise HTTPException(status_code=403, detail="The project leader cannot be removed")
        if target_member_type == "coleader" and requester_member_type != "leader":
            raise HTTPException(status_code=403, detail="Forbidden: Only the leader can remove a coleader")

        removed = remove_team_member(project_id, member_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Member not found")

        response = ResponseModel(
            success=True,
            message="",
            data=None
        )
        return JSONResponse(
            status_code=200,
            content=response.dict()
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to remove project member")
        raise HTTPException(status_code=500, detail="Internal server error")
