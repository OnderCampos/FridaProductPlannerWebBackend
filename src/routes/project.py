from fastapi import APIRouter, HTTPException, Path, Header
from fastapi.responses import JSONResponse
from typing import Optional

from src.schemas.resources_request import (
    GetProjectsRequest,
    GetProjectRequest,
    CreateProjectRequest,
    UpdateProjectRequest,
    DeleteProjectRequest
)
from src.schemas.response import ResponseModel
from src.schemas.resources_response import (
    GetProjectsResponse,
    GetProjectResponse,
    ProjectResponse
)
from src.schemas.member_schemas import TeamMemberCreateRequest, TeamMemberUpdate

from src.utils.auth import validate_user_and_get_data

from src.utils.projects import (
    get_all_projects_for_user,
    get_project_by_id,
    create_project,
    update_project,
    delete_project,
    get_project_for_user
)
from src.utils.members import (
    get_project_members as get_team_members,
    create_team_member,
    update_team_member,
    remove_team_member,
    check_member_exists,
    format_team_members_response,
    format_team_member_response
)
from src.utils.epics import get_epics_for_project_with_auth

router = APIRouter()

@router.get(
    "/{project_id}",
    response_description="Get a single project by ID with authentication.",
)
async def get_project_route(
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> GetProjectResponse:
    """
    Retrieves a single project by ID. Requires authentication and user must own the project.
    
    Args:
        project_id (str): The project ID to retrieve
        authorization (str): Authorization header with Bearer token (e.g., "Bearer your_token_here")
    
    Returns:
        GetProjectResponse: The project data
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    
    try:
        response = get_project_by_id(project_id, user_data.user_id)
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get(
    "/",
    response_description="Get all projects for the user.",
)
async def get_projects_route(
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> GetProjectsResponse:
    """
    Retrieves all projects for the authenticated user.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    try:
        response = get_all_projects_for_user(user_data.user_id)
        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/",
    response_description="Create a new project.",
)
async def create_project_route(
    req: CreateProjectRequest,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ProjectResponse:
    """
    Creates a new project with name, description, and user-provided project key.
    """
    print(f"[DEBUG] Creating project route with request: {req}")
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    
    try:
        response = await create_project(
            user_data=user_data,
            name=req.name,
            description=req.description,
            project_key=req.project_key,
        )
        return JSONResponse(
            status_code=201 if response.success else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put(
    "/{project_id}",
    response_description="Update an existing project.",
)
async def update_project_route(
    project_id: str = Path(..., description="The project ID"),
    req: UpdateProjectRequest = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ProjectResponse:
    """
    Updates an existing project. User must own the project.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    
    try:
        response = update_project(
            project_id=project_id,
            user_id=user_data.user_id,
            name=req.name if req else None,
            description=req.description if req else None,
            project_key=req.project_key if req else None
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete(
    "/{project_id}",
    response_description="Delete a project.",
)
async def delete_project_route(
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ProjectResponse:
    """
    Deletes a project. User must own the project.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    
    try:
        response = delete_project(
            project_id=project_id,
            user_id=user_data.user_id
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{project_id}/epics",
    response_description="Get all epics for a project.",
)
async def get_project_epics_route(
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
):
    """
    Retrieves all epics for a specific project. User must own the project.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    
    try:
        response = get_epics_for_project_with_auth(project_id, user_data.user_id)
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get(
    "/{project_id}/members",
    response_description="Get detailed project info including epics.",
)
async def get_project_members_route(
    project_id: str = Path(..., description="The project ID"),
    status: Optional[str] = None,
    role: Optional[str] = None,
    seniority: Optional[str] = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
):
    """
    Retrieves detailed project info including members. User must own the project.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")
    
    try:
        project_response = get_project_for_user(project_id, user_data.user_id)
        if not project_response.success:
            status_code = 404 if "not found" in project_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=project_response.dict(),
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{project_id}/members",
    response_description="Add a member directly to a project.",
)
async def add_project_member_route(
    project_id: str = Path(..., description="The project ID"),
    req: TeamMemberCreateRequest = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
):
    """
    Adds a member directly to a project without invitations.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        project_response = get_project_for_user(project_id, user_data.user_id)
        if not project_response.success:
            status_code = 404 if "not found" in project_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=project_response.dict(),
            )

        if check_member_exists(project_id, req.email):
            raise HTTPException(status_code=409, detail="Member already exists for this project")

        avatar = req.avatar
        if not avatar:
            parts = [part for part in req.name.strip().split(" ") if part]
            initials = "".join([part[0].upper() for part in parts[:2]])
            avatar = initials

        created_member = create_team_member(
            project_id=project_id,
            user_id=None,
            name=req.name,
            email=req.email,
            role=req.role,
            seniority=req.seniority,
            avatar=avatar
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{project_id}/members/{member_id}",
    response_description="Update a project member.",
)
async def update_project_member_route(
    project_id: str = Path(..., description="The project ID"),
    member_id: str = Path(..., description="The member ID"),
    req: TeamMemberUpdate = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
):
    """
    Updates role or seniority for a project member.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    if not req or (req.role is None and req.seniority is None):
        raise HTTPException(status_code=400, detail="At least one field (role, seniority) is required")

    try:
        project_response = get_project_for_user(project_id, user_data.user_id)
        if not project_response.success:
            status_code = 404 if "not found" in project_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=project_response.dict(),
            )

        updated_member = update_team_member(
            project_id=project_id,
            member_id=member_id,
            role=req.role,
            seniority=req.seniority
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{project_id}/members/{member_id}",
    response_description="Remove a member from a project.",
)
async def remove_project_member_route(
    project_id: str = Path(..., description="The project ID"),
    member_id: str = Path(..., description="The member ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
):
    """
    Removes a member from a project.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        project_response = get_project_for_user(project_id, user_data.user_id)
        if not project_response.success:
            status_code = 404 if "not found" in project_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=project_response.dict(),
            )

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
