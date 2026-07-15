
import os
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Path, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from typing import List, Optional
from pydantic import ValidationError

from src.schemas.resources_request import (
    GenerateAnalysisRequest,
    GenerateUserStoriesRequest,
    GetUserStoriesByEpicRequest,
    UpdateUserStoryAssigneeRequest,
    GenerateUserStoryDependenciesRequest,
    CreateUserStoryManualRequest,
    StartUserStoryQaRequest,
    UserStoryQaAnswersRequest,
    AcceptUserStoryQaRequest,
    ExpandUserStoriesRequest,
    StartUserStoryDocumentRequest,
    UserStoryDocumentAnswersRequest,
)
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user
from src.utils.planning.user_story_generation import (
    generate_analysis,
    generate_user_stories
)
from src.utils.planning.user_story_dependencies import (
    generate_and_persist_user_story_dependencies,
    generate_user_story_dependencies,
)
from src.utils.planning.user_stories import (
    create_multiple_user_stories,
    create_user_story,
    get_user_stories_by_epic,
    get_user_story_by_id,
    update_user_story,
    update_user_story_fields,
)
from src.utils.planning.epics import get_epic_by_id
from src.utils.planning.projects import get_project_by_id
from src.utils.authz.permissions import get_project_access, get_project_id_for_story
from src.utils.planning.assignees import (
    FRIDA_ASSIGNEE_ID,
    build_frida_assignee_update,
    build_member_lookup,
    is_frida_assignee_id,
    is_frida_assignee_name,
)
from src.utils.planning.members import get_member_by_id, get_project_members
from src.utils.planning.subtask_generation import (
    generate_subtasks_for_user_story,
    create_subtask_for_user_story_with_agent,
    get_subtasks_by_user_story,
    update_subtask_status,
    update_subtask_fields,
    delete_subtasks_by_user_story
)

from src.utils.ai.user_story_creation_qa import (
    delete_user_story_draft,
    get_user_story_draft,
    start_user_story_qa,
    submit_user_story_qa_answers,
)
from src.utils.ai.user_story_expansion import expand_user_stories
from src.services.workflows.user_story_document_generation.orchestrator import (
    start_user_story_document_draft,
    submit_user_story_document_answers,
)
from src.utils.documents.user_story_document import (
    DOCX_MEDIA_TYPE,
    build_user_story_document_download_response,
    build_user_story_document_download_response_with_wireframes,
    build_user_story_document_format_test_bytes,
    upload_user_story_document_wireframe_images,
)

router = APIRouter()

def _build_story_assignee_update(project_id: str, req: UpdateUserStoryAssigneeRequest) -> dict:
    update_data: dict = {}

    if (
        is_frida_assignee_id(req.assigneeId)
        or is_frida_assignee_name(req.assignee)
        or is_frida_assignee_name(req.assignee_email)
    ):
        return build_frida_assignee_update()

    if req.assigneeId:
        member = get_member_by_id(project_id, req.assigneeId) if project_id else None
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        update_data["assigneeId"] = req.assigneeId
        update_data["assignee"] = member.get("name")
        update_data["assigned_to"] = req.assigneeId
        if member.get("email"):
            update_data["assigneeEmail"] = member.get("email")
            update_data["assignee_email"] = member.get("email")
        return update_data

    assignee_email = str(req.assignee_email or "").strip()
    assignee_value = str(req.assignee or "").strip()
    resolved_assignee = assignee_email or assignee_value
    if not resolved_assignee or resolved_assignee.lower() == "unassigned":
        return {
            "assignee": "",
            "assigneeId": None,
            "assigned_to": None,
            "assigneeEmail": None,
            "assignee_email": None,
        }

    update_data["assignee"] = assignee_value or assignee_email
    if assignee_email:
        update_data["assigneeEmail"] = assignee_email
        update_data["assignee_email"] = assignee_email
        member_lookup = build_member_lookup(project_id)
        member = member_lookup.get("by_email", {}).get(assignee_email.lower())
        if member:
            update_data["assignee"] = member.get("name") or assignee_email
            update_data["assigneeId"] = member.get("id")
            update_data["assigned_to"] = member.get("id")

    return update_data


def _story_identifier_map(user_stories: List[dict]) -> dict:
    identifiers = {}
    for story in user_stories or []:
        if not isinstance(story, dict):
            continue
        story_id = str(story.get("id") or "").strip()
        user_story_id = str(story.get("user_story_id") or "").strip()
        if story_id:
            identifiers[story_id] = story
        if user_story_id:
            identifiers[user_story_id] = story
    return identifiers


async def _refresh_epic_story_dependencies(epic_id: str, user_data: UserData) -> List[dict]:
    existing = get_user_stories_by_epic(epic_id, user_data.get_user_id(), allow_member=True)
    existing_stories = existing.data if existing and existing.success and isinstance(existing.data, list) else []
    if not existing_stories:
        return []

    dependencies_response = await generate_and_persist_user_story_dependencies(
        user_data=user_data,
        epic_id=epic_id,
        user_stories=existing_stories,
    )
    if (
        dependencies_response.success
        and isinstance(dependencies_response.data, dict)
        and isinstance(dependencies_response.data.get("user_stories"), list)
    ):
        return dependencies_response.data.get("user_stories") or []

    return existing_stories


def _select_updated_stories(created_stories: List[dict], refreshed_stories: List[dict]) -> List[dict]:
    if not created_stories or not refreshed_stories:
        return created_stories

    refreshed_by_identifier = _story_identifier_map(refreshed_stories)
    updated_stories: List[dict] = []
    for story in created_stories:
        if not isinstance(story, dict):
            updated_stories.append(story)
            continue

        story_id = str(story.get("id") or "").strip()
        user_story_id = str(story.get("user_story_id") or "").strip()
        updated_story = (
            refreshed_by_identifier.get(story_id)
            or refreshed_by_identifier.get(user_story_id)
            or story
        )
        updated_stories.append(updated_story)

    return updated_stories


def _require_project_lead(project_id: str, user_data):
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        raise HTTPException(status_code=status_code, detail=access.message)
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot perform this action")

async def _parse_expand_user_stories_request(request: Request) -> tuple[ExpandUserStoriesRequest, List[UploadFile]]:
    content_type = str(request.headers.get("content-type") or "").lower()
    files: List[UploadFile] = []

    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_payload = {
            "epic_id": form.get("epic_id"),
            "instruction": form.get("instruction"),
            "max_new_stories": form.get("max_new_stories"),
        }
        files = [
            item
            for item in form.getlist("files")
            if isinstance(item, UploadFile)
        ]
    else:
        try:
            raw_payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid request body") from exc
        if not isinstance(raw_payload, dict):
            raise HTTPException(status_code=400, detail="Invalid request body")

    raw_max_new_stories = raw_payload.get("max_new_stories")

    payload = {
        "epic_id": str(raw_payload.get("epic_id") or "").strip(),
        "instruction": str(raw_payload.get("instruction") or "").strip() or None,
        "max_new_stories": None if raw_max_new_stories in {None, ""} else raw_max_new_stories,
    }

    try:
        return ExpandUserStoriesRequest(**payload), files
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid expand request payload") from exc

@router.post(
    "/user-story-generation-step-1/",
    response_description="Step 1: Analyzes epic and generates main functionalities and user identification.",
)
async def generate_analysis_route(
    req: GenerateAnalysisRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Step 1 of user story generation: Analyzes the epic and project description to identify:
    - Main functionalities
    - User roles and personas
    - Key workflows
    This prepares the foundation for detailed user story generation.
    """
    try:
        epic_response = get_epic_by_id(req.epic_id)
        if not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        response = await generate_analysis(
            user_data=user_data,
            epic_id=req.epic_id,
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/user-story-generation-step-2/",
    response_description="Step 2: Generates detailed user stories based on analysis from Step 1.",
)
async def generate_user_stories_route(
    req: GenerateUserStoriesRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Step 2 of user story generation: Creates detailed user stories based on:
    - Results from Step 1 analysis
    - Identified users and functionalities
    - Project context and requirements
    This generates the final user stories ready for development.
    """
    try:
        epic_response = get_epic_by_id(req.epic_id)
        if not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        response = await generate_user_stories(
            user_data=user_data,
            epic_id=req.epic_id,
            functionality=req.functionality,
            functionalities=req.functionalities,
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post(
    "/user-story-generation/",
    response_description="Single-step: Analyze epic, brainstorm, synthesize, and return user stories.",
)
async def generate_user_stories_single_route(
    req: GenerateUserStoriesRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Single-step user story generation using the agent graph:
    - Analyze epic
    - Brainstorm user stories
    - Synthesize and dedupe
    - Generate dependencies
    Returns the same data structure as Step 2.
    """
    try:
        epic_response = get_epic_by_id(req.epic_id)
        if not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        response = await generate_user_stories(
            user_data=user_data,
            epic_id=req.epic_id,
            functionality=req.functionality,
            functionalities=req.functionalities,
        )

        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.dict(),
            )

        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post(
    "/user-story-dependencies/",
    response_description="Generate dependencies between user stories.",
)
async def generate_user_story_dependencies_route(
    req: GenerateUserStoryDependenciesRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Generates dependencies between user stories for an epic.
    """
    if not req.user_stories:
        raise HTTPException(status_code=400, detail="user_stories is required")

    try:
        epic_response = get_epic_by_id(req.epic_id)
        if not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")

        _require_project_lead(epic_response.data.get("project_id"), user_data)

        response = await generate_user_story_dependencies(
            user_data=user_data,
            epic_id=req.epic_id,
            user_stories=[story.dict() for story in req.user_stories],
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/manual/",
    response_description="Create a user story manually for an epic.",
)
async def create_user_story_manual_route(
    req: CreateUserStoryManualRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not (req.epic_id or "").strip():
        raise HTTPException(status_code=400, detail="epic_id is required")
    if not (req.user_story or "").strip():
        raise HTTPException(status_code=400, detail="user_story is required")
    if not (req.description or "").strip():
        raise HTTPException(status_code=400, detail="description is required")

    epic_response = get_epic_by_id(req.epic_id)
    if not epic_response.success:
        raise HTTPException(status_code=404, detail="Epic not found")
    project_id = epic_response.data.get("project_id")
    _require_project_lead(project_id, user_data)

    payload = req.model_dump(exclude={"epic_id"})
    payload["epic"] = epic_response.data.get("name") or epic_response.data.get("epic") or ""
    payload["dependencies"] = payload.get("dependencies") or []

    response = create_user_story(req.epic_id, user_data.get_user_id(), payload)
    if response.success and isinstance(response.data, dict):
        refreshed_stories = await _refresh_epic_story_dependencies(req.epic_id, user_data)
        updated_stories = _select_updated_stories([response.data], refreshed_stories)
        response.data = updated_stories[0] if updated_stories else response.data

    return JSONResponse(
        status_code=201 if response.success else 400,
        content=response.dict(),
    )


@router.post(
    "/qa/start/",
    response_description="Start Q&A flow to create a single user story.",
)
async def start_user_story_qa_route(
    req: StartUserStoryQaRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not (req.epic_id or "").strip():
        raise HTTPException(status_code=400, detail="epic_id is required")

    epic_response = get_epic_by_id(req.epic_id)
    if not epic_response.success:
        raise HTTPException(status_code=404, detail="Epic not found")
    project_id = epic_response.data.get("project_id")
    _require_project_lead(project_id, user_data)

    project_response = get_project_by_id(project_id, user_data.get_user_id(), allow_member=True, user_email=user_data.get_email())
    project_description = ""
    if project_response and project_response.success and project_response.data:
        project_description = str(project_response.data.get("description") or "")

    existing = get_user_stories_by_epic(req.epic_id, user_data.get_user_id(), allow_member=True)
    existing_stories = existing.data if existing and existing.success and isinstance(existing.data, list) else []

    response = await start_user_story_qa(
        user_data=user_data,
        epic_id=req.epic_id,
        project_description=project_description,
        epic_name=str(epic_response.data.get("name") or ""),
        epic_description=str(epic_response.data.get("description") or ""),
        goal=str(req.goal or ""),
        existing_stories=existing_stories,
    )
    return JSONResponse(status_code=200 if response.success else 400, content=response.dict())


@router.post(
    "/qa/answer/",
    response_description="Submit answers for a user story Q&A draft.",
)
async def submit_user_story_qa_answers_route(
    req: UserStoryQaAnswersRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not (req.draft_id or "").strip():
        raise HTTPException(status_code=400, detail="draft_id is required")

    draft_response = get_user_story_draft(req.draft_id)
    if not draft_response.success:
        return JSONResponse(status_code=404, content=draft_response.dict())

    epic_id = str((draft_response.data or {}).get("epic_id") or "").strip()
    if not epic_id:
        raise HTTPException(status_code=400, detail="Draft is missing epic_id")

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        raise HTTPException(status_code=404, detail="Epic not found")
    project_id = epic_response.data.get("project_id")
    _require_project_lead(project_id, user_data)

    existing = get_user_stories_by_epic(epic_id, user_data.get_user_id(), allow_member=True)
    existing_stories = existing.data if existing and existing.success and isinstance(existing.data, list) else []

    answers_payload = [ans.model_dump() for ans in (req.answers or [])]
    response = await submit_user_story_qa_answers(
        user_data=user_data,
        draft_id=req.draft_id,
        answers=answers_payload,
        existing_stories=existing_stories,
    )
    return JSONResponse(status_code=200 if response.success else 400, content=response.dict())


@router.post(
    "/qa/accept/",
    response_description="Accept a user story draft and create the story.",
)
async def accept_user_story_qa_route(
    req: AcceptUserStoryQaRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not (req.draft_id or "").strip():
        raise HTTPException(status_code=400, detail="draft_id is required")

    draft_response = get_user_story_draft(req.draft_id)
    if not draft_response.success:
        return JSONResponse(status_code=404, content=draft_response.dict())

    draft_data = draft_response.data or {}
    status = str(draft_data.get("status") or "")
    if status != "story_ready":
        return JSONResponse(
            status_code=409,
            content=ResponseModel(success=False, message="Draft is not ready to accept", data=None).dict(),
        )

    epic_id = str(draft_data.get("epic_id") or "").strip()
    if not epic_id:
        raise HTTPException(status_code=400, detail="Draft is missing epic_id")

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        raise HTTPException(status_code=404, detail="Epic not found")
    project_id = epic_response.data.get("project_id")
    _require_project_lead(project_id, user_data)

    story_draft = draft_data.get("story_draft") or {}
    if not isinstance(story_draft, dict):
        story_draft = {}

    payload = {
        "epic": str(draft_data.get("epic_name") or epic_response.data.get("name") or ""),
        "user_story": str(story_draft.get("user_story") or "").strip(),
        "description": str(story_draft.get("description") or "").strip(),
        "order": story_draft.get("order", 0),
        "dependencies": story_draft.get("dependencies") or [],
        "effortHours": story_draft.get("effortHours", 0),
        "story_points": story_draft.get("story_points", 0),
    }
    if isinstance(story_draft.get("acceptanceCriteria"), list):
        payload["acceptanceCriteria"] = story_draft.get("acceptanceCriteria")
    if isinstance(story_draft.get("outOfScope"), list):
        payload["outOfScope"] = story_draft.get("outOfScope")
    if isinstance(story_draft.get("document"), dict):
        payload["document"] = story_draft.get("document")
    if not payload["user_story"] or not payload["description"]:
        return JSONResponse(
            status_code=400,
            content=ResponseModel(success=False, message="Draft is missing required fields", data=None).dict(),
        )

    response = create_user_story(epic_id, user_data.get_user_id(), payload)
    if response.success:
        if isinstance(response.data, dict):
            refreshed_stories = await _refresh_epic_story_dependencies(epic_id, user_data)
            updated_stories = _select_updated_stories([response.data], refreshed_stories)
            response.data = updated_stories[0] if updated_stories else response.data
        delete_user_story_draft(req.draft_id)
        return JSONResponse(status_code=201, content=response.dict())
    return JSONResponse(status_code=400, content=response.dict())


@router.post(
    "/document/start/",
    response_description="Start document generation for a user story (may ask clarification questions).",
)
async def start_user_story_document_route(
    req: StartUserStoryDocumentRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not (req.story_id or "").strip():
        raise HTTPException(status_code=400, detail="story_id is required")

    response = await start_user_story_document_draft(user_data=user_data, story_id=req.story_id)
    return JSONResponse(status_code=200 if response.success else 400, content=response.dict())


@router.post(
    "/document/answer/",
    response_description="Submit document clarification answers for a user story document draft.",
)
async def submit_user_story_document_answers_route(
    req: UserStoryDocumentAnswersRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not req or not (req.draft_id or "").strip():
        raise HTTPException(status_code=400, detail="draft_id is required")

    answers_payload = [ans.model_dump() for ans in (req.answers or [])]
    response = await submit_user_story_document_answers(
        user_data=user_data,
        draft_id=req.draft_id,
        answers=answers_payload,
    )
    return JSONResponse(status_code=200 if response.success else 400, content=response.dict())


@router.post(
    "/document/upload/{draft_id}/wireframe/",
    response_description="Upload wireframe/mockup images for a user story document draft.",
)
async def upload_user_story_document_wireframe_route(
    draft_id: str = Path(..., description="The user story document draft ID"),
    files: List[UploadFile] = File(..., description="One or more image files (PNG/JPG)."),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    if not (draft_id or "").strip():
        raise HTTPException(status_code=400, detail="draft_id is required")
    response = await upload_user_story_document_wireframe_images(user_data=user_data, draft_id=draft_id, files=files)
    return JSONResponse(status_code=200 if response.success else 400, content=response.dict())


@router.get(
    "/document/download/{draft_id}/",
    response_description="Download a generated Word document (DOCX) for a user story draft.",
)
async def download_user_story_document_route(
    draft_id: str = Path(..., description="The user story document draft ID"),
    user_data: UserData = Depends(get_current_user),
):
    if not (draft_id or "").strip():
        raise HTTPException(status_code=400, detail="draft_id is required")
    return await build_user_story_document_download_response(user_data=user_data, draft_id=draft_id)


@router.post(
    "/document/download/{draft_id}/wireframe/",
    response_description="Download a generated Word document (DOCX) embedding wireframe images from this request (no storage).",
)
async def download_user_story_document_with_wireframes_route(
    draft_id: str = Path(..., description="The user story document draft ID"),
    files: List[UploadFile] = File(..., description="One or more image files (PNG/JPG)."),
    user_data: UserData = Depends(get_current_user),
):
    if not (draft_id or "").strip():
        raise HTTPException(status_code=400, detail="draft_id is required")
    return await build_user_story_document_download_response_with_wireframes(
        user_data=user_data,
        draft_id=draft_id,
        files=files,
    )


def _docx_format_test_enabled() -> bool:
    value = str(os.getenv("ENABLE_DOCX_FORMAT_TEST") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


@router.get(
    "/document/test/",
    response_description="Download a fast format-test DOCX (feature-flagged).",
)
async def download_user_story_document_format_test_route(
    user_data: UserData = Depends(get_current_user),
):
    if not _docx_format_test_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    doc_bytes, filename = build_user_story_document_format_test_bytes()
    encoded = quote(filename)
    return StreamingResponse(
        BytesIO(doc_bytes),
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.post(
    "/expand/",
    response_description="Expand user stories for an epic using AI (agentic).",
)
async def expand_user_stories_route(
    request: Request,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    req, files = await _parse_expand_user_stories_request(request)
    if not req or not (req.epic_id or "").strip():
        raise HTTPException(status_code=400, detail="epic_id is required")

    epic_response = get_epic_by_id(req.epic_id)
    if not epic_response.success:
        raise HTTPException(status_code=404, detail="Epic not found")
    project_id = epic_response.data.get("project_id")
    _require_project_lead(project_id, user_data)

    project_response = get_project_by_id(project_id, user_data.get_user_id(), allow_member=True, user_email=user_data.get_email())
    project_description = ""
    if project_response and project_response.success and project_response.data:
        project_description = str(project_response.data.get("description") or "")

    existing = get_user_stories_by_epic(req.epic_id, user_data.get_user_id(), allow_member=True)
    existing_stories = existing.data if existing and existing.success and isinstance(existing.data, list) else []

    expanded_response = await expand_user_stories(
        user_data=user_data,
        project_description=project_description,
        epic_name=str(epic_response.data.get("name") or ""),
        epic_description=str(epic_response.data.get("description") or ""),
        existing_stories=existing_stories,
        instruction=str(req.instruction or ""),
        max_new_stories=int(req.max_new_stories or 5),
        image_files=files,
    )
    if not expanded_response.success:
        return JSONResponse(status_code=400, content=expanded_response.dict())

    expanded_payload = expanded_response.data if isinstance(expanded_response.data, dict) else {}
    new_stories = expanded_payload.get("user_stories") if isinstance(expanded_payload, dict) else None
    if not isinstance(new_stories, list) or not new_stories:
        return JSONResponse(
            status_code=200,
            content=ResponseModel(success=True, message="No new user stories generated", data={"user_stories": [], "generated_count": 0}).dict(),
        )

    save_result = create_multiple_user_stories(
        req.epic_id,
        user_data.get_user_id(),
        new_stories,
        template_data=None,
    )
    if not save_result.success:
        return JSONResponse(status_code=400, content=save_result.dict())

    saved_stories = save_result.data if isinstance(save_result.data, list) else []
    refreshed_stories = await _refresh_epic_story_dependencies(req.epic_id, user_data)
    updated_stories = _select_updated_stories(saved_stories, refreshed_stories)

    return JSONResponse(
        status_code=200,
        content=ResponseModel(
            success=True,
            message=save_result.message,
            data={"user_stories": updated_stories, "generated_count": len(updated_stories)},
        ).dict(),
    )


@router.get(
    "/{story_id}/",
    response_description="Get a user story by ID",
)
async def get_user_story_route(
    story_id: str = Path(..., description="The user story ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Retrieves a single user story by its ID.
    Requires authentication and verifies that the user owns the user story.
    """
    try:
        response = get_user_story_by_id(story_id, user_data.get_user_id(), allow_member=True, user_email=user_data.get_email())
        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 403,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{story_id}/",
    response_description="Assign or reassign a user story",
)
async def update_user_story_assignee_route(
    story_id: str = Path(..., description="The user story ID"),
    req: UpdateUserStoryAssigneeRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Assigns or reassigns a user story to a team member.
    """
    if not req or (req.assigneeId is None and req.assignee is None and req.assignee_email is None):
        raise HTTPException(status_code=400, detail="assigneeId, assignee, or assignee_email is required")

    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        project_id = epic_response.data.get("project_id")
        update_data = _build_story_assignee_update(project_id, req)

        response = update_user_story(
            story_id,
            user_data.get_user_id(),
            update_data,
            user_email=user_data.get_email(),
            user_name=user_data.get_user_name(),
        )

        status_code = 200
        if not response.success:
            if "not found" in response.message.lower():
                status_code = 404
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 400

        return JSONResponse(
            status_code=status_code,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{story_id}/assignee/",
    response_description="Assign or reassign a user story",
)
async def update_user_story_assignee_name_route(
    story_id: str = Path(..., description="The user story ID"),
    req: UpdateUserStoryAssigneeRequest = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Assigns or reassigns a user story to a team member via assignee name or ID.
    """
    if not req or (req.assigneeId is None and req.assignee is None and req.assignee_email is None):
        raise HTTPException(status_code=400, detail="assigneeId, assignee, or assignee_email is required")

    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        project_id = epic_response.data.get("project_id")
        update_data = _build_story_assignee_update(project_id, req)

        response = update_user_story(
            story_id,
            user_data.get_user_id(),
            update_data,
            user_email=user_data.get_email(),
            user_name=user_data.get_user_name(),
        )
        if not response.success:
            status_code = 200
            if "not found" in response.message.lower():
                status_code = 404
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 400
            return JSONResponse(
                status_code=status_code,
                content=response.dict(),
            )

        assignee_payload = {
            "id": story_id,
            "assignee": update_data.get("assignee", response.data.get("assignee"))
        }
        if "assigneeId" in update_data or response.data.get("assigneeId"):
            assignee_payload["assigneeId"] = update_data.get("assigneeId", response.data.get("assigneeId"))
        elif str(assignee_payload.get("assignee") or "").strip().lower() == "frida":
            assignee_payload["assigneeId"] = FRIDA_ASSIGNEE_ID
        if response.data.get("assignment_notification") is not None:
            assignee_payload["assignment_notification"] = response.data.get("assignment_notification")

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="",
                data=assignee_payload
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.patch(
    "/{story_id}/fields/",
    response_description="Update fields of a user story",
)
async def update_user_story_fields_route(
    story_id: str = Path(..., description="The user story ID"),
    request: Request = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Updates fields of a specific user story.
    """
    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        update_data = await request.json()
        response = update_user_story_fields(
            story_id,
            user_data.get_user_id(),
            update_data,
            user_email=user_data.get_email(),
            user_name=user_data.get_user_name(),
        )

        if not response.success:
            status_code = 200
            if "not found" in response.message.lower():
                status_code = 404
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 400
            return JSONResponse(
                status_code=status_code,
                content=response.dict(),
            )

        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post(
    "/{story_id}/subtasks/",
    response_description="Generate subtasks for a user story",
)
async def generate_subtasks_route(
    story_id: str = Path(..., description="The user story ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Generates subtasks for a user story using AI analysis.
    Each subtask includes:
    - order: Sequential number for execution order
    - title: Short, clear title (3-8 words)
    - description: Clear, actionable task description
    - estimated_hours: Time estimate to complete the subtask
    - complexity: Low, Medium, or High
    - dependencies: Array of order numbers of prerequisite subtasks
    
    Requires authentication and verifies that the user owns the user story.
    """
    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        response = await generate_subtasks_for_user_story(
            user_data,
            story_id,
            allow_member=True,
            user_email=user_data.get_email(),
        )
        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post(
    "/{story_id}/subtasks-manually/",
    response_description="Create a subtask manually for a user story",
)
async def create_subtask_route(
    story_id: str = Path(..., description="The user story ID"),
    request: Request = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        subtask_data = await request.json()
        response = await create_subtask_for_user_story_with_agent(story_id, user_data, subtask_data)

        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get(
    "/{story_id}/subtasks/",
    response_description="Get subtasks for a user story",
)
async def get_subtasks_route(
    story_id: str = Path(..., description="The user story ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Retrieves all subtasks for a user story.
    Returns previously generated subtasks ordered by execution sequence with:
    - order: Sequential execution order
    - title: Short task title
    - description: Task description
    - estimated_hours: Time estimate
    - complexity: Low, Medium, or High
    - dependencies: Array of prerequisite subtask order numbers
    
    Requires authentication and verifies that the user owns the user story.
    """
    try:
        response = get_subtasks_by_user_story(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{story_id}/subtasks/{subtask_id}/status/",
    response_description="Update the status of a subtask",
)
async def update_subtask_status_route(
    story_id: str = Path(..., description="The user story ID"),
    subtask_id: str = Path(..., description="The subtask ID"),
    request: Request = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Updates the status of a specific subtask within a user story.
    Automatically sets the completion date when status is changed to "Done".
    
    Valid status values:
    - "To Do"
    - "In Progress"
    - "In Review"
    - "Stopped"
    - "Done"
    
    Request body:
    {
        "status": "In Progress",
        "completed_date": null  // Optional, auto-set when status is "Done"
    }
    
    Requires authentication and verifies that the user owns the subtask.
    """
    # Auth handled by dependency
    
    # Extract token from "Bearer <token>" format
    try:
        # Parse request body
        body = await request.json()
        status = body.get("status")
        completed_date = body.get("completed_date")
        
        if not status:
            raise HTTPException(status_code=400, detail="Status field is required")
        
        response = update_subtask_status(subtask_id, user_data.get_user_id(), status, completed_date, user_name=user_data.get_user_name(), user_email=user_data.get_email())
        
        status_code = 200
        if not response.success:
            if "not found" in response.message.lower():
                status_code = 404
            elif "invalid status" in response.message.lower():
                status_code = 400
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 500
        
        return JSONResponse(
            status_code=status_code,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.patch(
    "/{story_id}/subtasks/{subtask_id}/fields/",
    response_description="Update fields of a subtask",
)
async def update_subtask_fields_route(
    story_id: str = Path(..., description="The user story ID"),
    subtask_id: str = Path(..., description="The subtask ID"),
    request: Request = None,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Updates fields of a specific subtask within a user story.
    """
    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        project_id = get_project_id_for_story(story_id)

        update_data = await request.json()

        assignee_name = update_data.get("assignee")
        if assignee_name is not None:
            if not assignee_name or assignee_name.lower() == "unassigned":
                update_data["assignee"] = ""
                update_data["assigneeEmail"] = None
                update_data["assignee_email"] = None
                update_data["assigneeId"] = None
                update_data["assigned_to"] = None
            else:
                members = get_project_members(project_id)
                for member in members:
                    if member.get("name") == assignee_name:
                        update_data["assigneeEmail"] = member.get("email")
                        update_data["assignee_email"] = member.get("email")
                        update_data["assigneeId"] = member.get("id")
                        update_data["assigned_to"] = member.get("id")
                        break

        response = update_subtask_fields(subtask_id, user_data.get_user_id(), update_data, user_name=user_data.get_user_name(), user_email=user_data.get_email())

        if not response.success:
            raise HTTPException(status_code=400, detail=response.message)

        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete(
    "/{story_id}/subtasks/{subtask_id}/",
    response_description="Delete a subtask",
)
async def delete_subtask_route(
    story_id: str = Path(..., description="The user story ID"),
    subtask_id: str = Path(..., description="The subtask ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Deletes a specific subtask within a user story.
    """
    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email()
        )
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        epic_id = story_response.data.get("epic_id")
        epic_response = get_epic_by_id(epic_id) if epic_id else None
        if not epic_response or not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")
        _require_project_lead(epic_response.data.get("project_id"), user_data)

        response = delete_subtasks_by_user_story(subtask_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

