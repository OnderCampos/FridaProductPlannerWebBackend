from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
import logging

from src.schemas.response import ResponseModel
from src.schemas.resources_request import EpicCreateRequest, EpicUpdateRequest
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user
from src.utils.planning.user_stories import get_user_stories_by_epic_with_auth
from src.utils.authz.permissions import get_project_access
from src.utils.planning.epics import get_epic_by_id, create_epic, update_epic, delete_epic


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/{epic_id}/",
    response_description="Get epic by id"
)
async def get_epic_by_id_route(
    epic_id: str = Path(..., description="The epic ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    try:
        response = get_epic_by_id(epic_id)
        if response.success:
            project_id = response.data.get("project_id")
            access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
            if not access.success:
                return JSONResponse(
                    status_code=403 if "unauthorized" in access.message.lower() else 404,
                    content=access.dict(),
                )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get epic by id")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{epic_id}/user-stories/",
    response_description="Get all user stories for a specific epic.",
)
async def get_user_stories_by_epic_route(
    epic_id: str = Path(..., description="The epic ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Retrieves all user stories for a specific epic.
    Requires authentication and user must own the project/epic.
    """
    try:
        response = get_user_stories_by_epic_with_auth(epic_id, user_data.get_user_id(), user_data.get_email())
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get user stories by epic")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/epic-manually/",
    response_description="Create an epic manually for a project.",
)
async def create_epic_manually_route(
    req: EpicCreateRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create epics")

    try:
        response = create_epic(project_id, user_data.get_user_id(), req.model_dump())
        return JSONResponse(
            status_code=201 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create epic")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{project_id}/epic/{epic_id}",
    response_description="Update an epic for a project.",
)
async def update_epic_route(
    req: EpicUpdateRequest,
    project_id: str = Path(..., description="The project ID"),
    epic_id: str = Path(..., description="The epic ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update epics")

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        return JSONResponse(status_code=404, content=epic_response.dict())
    if epic_response.data.get("project_id") != project_id:
        return JSONResponse(
            status_code=404,
            content=ResponseModel(success=False, message="Epic not found for this project", data=None).dict(),
        )

    try:
        response = update_epic(epic_id, user_data.get_user_id(), req.model_dump(exclude_unset=True))
        if not response.success and "unauthorized" in response.message.lower():
            return JSONResponse(status_code=403, content=response.dict())
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update epic")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{project_id}/epic/{epic_id}",
    response_description="Delete an epic for a project.",
)
async def delete_epic_route(
    project_id: str = Path(..., description="The project ID"),
    epic_id: str = Path(..., description="The epic ID"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot delete epics")

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        return JSONResponse(status_code=404, content=epic_response.dict())
    if epic_response.data.get("project_id") != project_id:
        return JSONResponse(
            status_code=404,
            content=ResponseModel(success=False, message="Epic not found for this project", data=None).dict(),
        )

    try:
        response = delete_epic(epic_id, user_data.get_user_id())
        if not response.success and "unauthorized" in response.message.lower():
            return JSONResponse(status_code=403, content=response.dict())
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete epic")
        raise HTTPException(status_code=500, detail="Internal server error")

