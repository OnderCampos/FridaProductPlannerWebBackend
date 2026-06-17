from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from src.schemas.response import ResponseModel
from src.schemas.task_schemas import (
    BatchCreateTasksRequest,
    CreateTaskRequest,
    UpdateTaskFieldsRequest,
    UpdateTaskStatusRequest,
)
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user
from src.utils.authz.permissions import get_project_access
from src.utils.planning.assignees import (
    build_frida_assignee_update,
    build_member_lookup,
    is_frida_assignee_id,
    is_frida_assignee_name,
)
from src.utils.planning.members import get_member_by_id
from src.utils.planning.subtask_generation import (
    batch_create_project_tasks_from_text,
    create_project_task,
    delete_subtasks_by_user_story,
    get_subtask_by_id,
    list_tasks_for_project,
    update_subtask_fields,
    update_subtask_status,
)


router = APIRouter()


def _status_from_response(response: ResponseModel, success_code: int = 200) -> int:
    if response.success:
        return success_code
    message = (response.message or "").lower()
    if "not found" in message:
        return 404
    if "unauthorized" in message:
        return 403
    return 400


def _require_project_lead(project_id: str, user_data: UserData) -> None:
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        raise HTTPException(status_code=status_code, detail=access.message)
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot perform this action")


def _build_task_assignee_update(project_id: str, req: UpdateTaskFieldsRequest) -> dict:
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
        update_data = {
            "assigneeId": req.assigneeId,
            "assignee": member.get("name") or "",
            "assigned_to": req.assigneeId,
            "assigneeEmail": member.get("email"),
            "assignee_email": member.get("email"),
        }
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

    update_data = {
        "assignee": assignee_value or assignee_email,
    }
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


@router.get(
    "/{project_id}/tasks",
    response_description="List all tasks for a project",
)
async def list_tasks_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    response = list_tasks_for_project(
        project_id=project_id,
        user_id=user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )


@router.post(
    "/{project_id}/tasks",
    response_description="Create an independent project task",
)
async def create_task_route(
    req: CreateTaskRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    response = await create_project_task(
        project_id=project_id,
        user_data=user_data,
        task_data=req.model_dump(),
    )
    return JSONResponse(
        status_code=_status_from_response(response, success_code=201),
        content=response.dict(),
    )


@router.post(
    "/{project_id}/tasks/batch",
    response_description="Create multiple independent project tasks from freeform text",
)
async def batch_create_tasks_route(
    req: BatchCreateTasksRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    response = await batch_create_project_tasks_from_text(
        project_id=project_id,
        user_data=user_data,
        source_text=req.source_text,
    )
    return JSONResponse(
        status_code=_status_from_response(response, success_code=201),
        content=response.dict(),
    )


@router.patch(
    "/{project_id}/tasks/{task_id}/status",
    response_description="Update task status",
)
async def update_task_status_route(
    req: UpdateTaskStatusRequest,
    project_id: str = Path(..., description="The project ID"),
    task_id: str = Path(..., description="The task ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    task_response = get_subtask_by_id(
        task_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not task_response.success:
        return JSONResponse(
            status_code=_status_from_response(task_response),
            content=task_response.dict(),
        )
    if str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")

    response = update_subtask_status(
        subtask_id=task_id,
        user_id=user_data.get_user_id(),
        status=req.status,
        completed_date=req.completed_date,
        user_name=user_data.get_user_name(),
        user_email=user_data.get_email()
    )
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )


@router.patch(
    "/{project_id}/tasks/{task_id}",
    response_description="Update task fields",
)
async def update_task_fields_route(
    req: UpdateTaskFieldsRequest,
    project_id: str = Path(..., description="The project ID"),
    task_id: str = Path(..., description="The task ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    task_response = get_subtask_by_id(
        task_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not task_response.success:
        return JSONResponse(
            status_code=_status_from_response(task_response),
            content=task_response.dict(),
        )
    if str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")

    payload = req.model_dump(exclude_none=True)
    if req.assignee is not None or req.assigneeId is not None or req.assignee_email is not None:
        assignee_update = _build_task_assignee_update(project_id, req)
        payload.update(assignee_update)

    response = update_subtask_fields(
        subtask_id=task_id,
        user_id=user_data.get_user_id(),
        update_data=payload,
        user_name=user_data.get_user_name(),
        user_email=user_data.get_email(),
    )
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )


@router.delete(
    "/{project_id}/tasks/{task_id}",
    response_description="Delete a task",
)
async def delete_task_route(
    project_id: str = Path(..., description="The project ID"),
    task_id: str = Path(..., description="The task ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    task_response = get_subtask_by_id(
        task_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not task_response.success:
        return JSONResponse(
            status_code=_status_from_response(task_response),
            content=task_response.dict(),
        )
    if str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")

    response = delete_subtasks_by_user_story(task_id, user_data.get_user_id())
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )
