from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from typing import Optional
import logging

from src.schemas.response import ResponseModel
from src.schemas.sprint_schemas import (
    CreateSprintRequest,
    UpdateSprintRequest,
    SprintItemAssignmentRequest,
    SprintItemsBulkRequest,
    SprintItemsOrderRequest,
    SprintOrderRequest,
)
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user
from src.utils.authz.permissions import get_project_access
from src.utils.planning.sprints import (
    get_sprints_for_project,
    create_sprint,
    update_sprint,
    delete_sprint,
    get_sprint_items,
    assign_item_to_sprint,
    unassign_item_from_sprint,
    list_available_items,
    bulk_update_sprint_items,
    reorder_sprint_items,
    reorder_sprints,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _status_from_response(response: ResponseModel, success_code: int = 200) -> int:
    if response.success:
        return success_code
    message = (response.message or "").lower()
    if "not found" in message:
        return 404
    if "unauthorized" in message:
        return 403
    return 400


@router.get(
    "/{project_id}/sprints",
    response_description="List sprints for a project",
)
async def list_sprints_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Returns all sprints for a project with item counts.
    """
    try:
        response = get_sprints_for_project(
            project_id,
            user_data.get_user_id(),
            include_counts=True,
            allow_members=True,
            user_email=user_data.get_email(),
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list sprints")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/sprints",
    response_description="Create a sprint for a project",
)
async def create_sprint_route(
    req: CreateSprintRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Creates a new sprint for the project.
    """
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot create sprints")

    try:
        response = create_sprint(
            project_id=project_id,
            user_id=user_data.get_user_id(),
            name=req.name,
            length_days=req.lengthDays,
            start_date=req.startDate,
            end_date=req.endDate,
        )
        return JSONResponse(
            status_code=_status_from_response(response, success_code=201),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create sprint")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{project_id}/sprints/order",
    response_description="Reorder sprints within a project",
)
async def reorder_sprints_route(
    req: SprintOrderRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Reorders sprints within a project.
    """
    if not req.order:
        raise HTTPException(status_code=400, detail="order is required")

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot reorder sprints")

    try:
        response = reorder_sprints(
            project_id=project_id,
            user_id=user_data.get_user_id(),
            order=req.order,
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to reorder sprints")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{project_id}/sprints/{sprint_id}",
    response_description="Update a sprint",
)
async def update_sprint_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    req: UpdateSprintRequest = None,
    user_data: UserData = Depends(get_current_user),
):
    """
    Updates sprint fields (partial update).
    """
    if not req or (req.name is None and req.lengthDays is None and req.startDate is None and req.endDate is None):
        raise HTTPException(status_code=400, detail="At least one field is required")

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update sprints")

    try:
        response = update_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            name=req.name,
            length_days=req.lengthDays,
            start_date=req.startDate,
            end_date=req.endDate,
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update sprint")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{project_id}/sprints/{sprint_id}",
    response_description="Delete a sprint",
)
async def delete_sprint_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Deletes a sprint and unassigns all items from it.
    """
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot delete sprints")

    try:
        response = delete_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            unassign_items=True,
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete sprint")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{project_id}/sprints/{sprint_id}/items",
    response_description="List items assigned to a sprint",
)
async def list_sprint_items_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Returns all stories and subtasks assigned to the sprint.
    """
    try:
        response = get_sprint_items(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            allow_members=True,
            user_email=user_data.get_email(),
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list sprint items")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/sprints/{sprint_id}/items",
    response_description="Assign an item to a sprint",
)
async def assign_sprint_item_route(
    req: SprintItemAssignmentRequest,
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Assigns a user story or subtask to a sprint.
    """
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot assign sprint items")

    try:
        response = assign_item_to_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            item_type=req.type,
            item_id=req.id,
            include_subtasks=req.include_subtasks
        )
        return JSONResponse(
            status_code=_status_from_response(response, success_code=201),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to assign sprint item")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{project_id}/sprints/{sprint_id}/items",
    response_description="Unassign an item from a sprint",
)
async def unassign_sprint_item_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    req: SprintItemAssignmentRequest = None,
    item_type: Optional[str] = Query(None, alias="type"),
    item_id: Optional[str] = Query(None, alias="id"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Unassigns a user story or subtask from a sprint.
    """
    resolved_type = (req.type if req else None) or item_type
    resolved_id = (req.id if req else None) or item_id

    if not resolved_type or not resolved_id:
        raise HTTPException(status_code=400, detail="type and id are required")

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot unassign sprint items")

    try:
        response = unassign_item_from_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            item_type=resolved_type,
            item_id=resolved_id,
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to unassign sprint item")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{project_id}/sprints/available-items",
    response_description="List items available for sprint planning",
)
async def list_available_items_route(
    project_id: str = Path(..., description="The project ID"),
    search: Optional[str] = Query(None, description="Optional search text"),
    types: Optional[str] = Query(None, description="Comma-separated list of types (story,subtask)"),
    epic_id: Optional[str] = Query(None, alias="epicId", description="Filter by epic ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Returns unassigned user stories and subtasks for the project.
    """
    parsed_types = None
    if types:
        parsed_types = [item.strip() for item in types.split(",") if item.strip()]

    try:
        response = list_available_items(
            project_id=project_id,
            user_id=user_data.get_user_id(),
            search=search,
            types=parsed_types,
            epic_id=epic_id,
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list available sprint items")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/sprints/{sprint_id}/items/bulk",
    response_description="Bulk assign/unassign sprint items",
)
async def bulk_update_sprint_items_route(
    req: SprintItemsBulkRequest,
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Bulk assign/unassign items to a sprint.
    """
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot update sprint items")

    try:
        response = bulk_update_sprint_items(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            assign_items=[item.dict() for item in (req.assign or [])],
            unassign_items=[item.dict() for item in (req.unassign or [])],
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to bulk update sprint items")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/{project_id}/sprints/{sprint_id}/items/order",
    response_description="Reorder items within a sprint",
)
async def reorder_sprint_items_route(
    req: SprintItemsOrderRequest,
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    user_data: UserData = Depends(get_current_user),
):
    """
    Reorders items within a sprint. Accepts IDs or typed tokens like story-<id>, sub-<id>.
    """
    if not req.order:
        raise HTTPException(status_code=400, detail="order is required")

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        return JSONResponse(status_code=status_code, content=access.dict())
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot reorder sprint items")

    try:
        response = reorder_sprint_items(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_data.get_user_id(),
            order=req.order,
        )
        return JSONResponse(
            status_code=_status_from_response(response),
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to reorder sprint items")
        raise HTTPException(status_code=500, detail="Internal server error")

