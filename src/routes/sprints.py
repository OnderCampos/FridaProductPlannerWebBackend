from fastapi import APIRouter, HTTPException, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from typing import Optional

from src.schemas.response import ResponseModel
from src.schemas.sprint_schemas import (
    CreateSprintRequest,
    UpdateSprintRequest,
    SprintItemAssignmentRequest,
    SprintItemsBulkRequest,
    SprintItemsOrderRequest,
    SprintOrderRequest,
)
from src.utils.auth import validate_user_and_get_data
from src.utils.permissions import get_project_access
from src.utils.sprints import (
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


def _get_user_data_or_401(authorization: Optional[str]):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    return validate_user_and_get_data(token)


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
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Returns all sprints for a project with item counts.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{project_id}/sprints",
    response_description="Create a sprint for a project",
)
async def create_sprint_route(
    req: CreateSprintRequest,
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Creates a new sprint for the project.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{project_id}/sprints/order",
    response_description="Reorder sprints within a project",
)
async def reorder_sprints_route(
    req: SprintOrderRequest,
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Reorders sprints within a project.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{project_id}/sprints/{sprint_id}",
    response_description="Update a sprint",
)
async def update_sprint_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    req: UpdateSprintRequest = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Updates sprint fields (partial update).
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{project_id}/sprints/{sprint_id}",
    response_description="Delete a sprint",
)
async def delete_sprint_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Deletes a sprint and unassigns all items from it.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{project_id}/sprints/{sprint_id}/items",
    response_description="List items assigned to a sprint",
)
async def list_sprint_items_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Returns all stories and subtasks assigned to the sprint.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{project_id}/sprints/{sprint_id}/items",
    response_description="Assign an item to a sprint",
)
async def assign_sprint_item_route(
    req: SprintItemAssignmentRequest,
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Assigns a user story or subtask to a sprint.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{project_id}/sprints/{sprint_id}/items",
    response_description="Unassign an item from a sprint",
)
async def unassign_sprint_item_route(
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    request: Request = None,
    item_type: Optional[str] = Query(None, alias="type"),
    item_id: Optional[str] = Query(None, alias="id"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Unassigns a user story or subtask from a sprint.
    """
    user_data = _get_user_data_or_401(authorization)

    body = {}
    if request is not None:
        try:
            body = await request.json()
        except Exception:
            body = {}

    resolved_type = body.get("type") or item_type
    resolved_id = body.get("id") or item_id

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{project_id}/sprints/available-items",
    response_description="List items available for sprint planning",
)
async def list_available_items_route(
    project_id: str = Path(..., description="The project ID"),
    search: Optional[str] = Query(None, description="Optional search text"),
    types: Optional[str] = Query(None, description="Comma-separated list of types (story,subtask)"),
    epic_id: Optional[str] = Query(None, alias="epicId", description="Filter by epic ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Returns unassigned user stories and subtasks for the project.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{project_id}/sprints/{sprint_id}/items/bulk",
    response_description="Bulk assign/unassign sprint items",
)
async def bulk_update_sprint_items_route(
    req: SprintItemsBulkRequest,
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Bulk assign/unassign items to a sprint.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{project_id}/sprints/{sprint_id}/items/order",
    response_description="Reorder items within a sprint",
)
async def reorder_sprint_items_route(
    req: SprintItemsOrderRequest,
    project_id: str = Path(..., description="The project ID"),
    sprint_id: str = Path(..., description="The sprint ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication"),
):
    """
    Reorders items within a sprint. Accepts IDs or typed tokens like story-<id>, sub-<id>.
    """
    user_data = _get_user_data_or_401(authorization)

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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
