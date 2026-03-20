from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional

from src.schemas.assistant_schemas import AssistantChatRequest, AssistantExecuteActionRequest
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user
from src.utils.ai.assistant_chat_history import (
    ASSISTANT_CHAT_HISTORY_LIMIT,
    append_assistant_chat_messages,
    get_assistant_chat_history,
)
from src.utils.planning.epics import get_epic_by_id
from src.utils.authz.permissions import get_project_access
from src.utils.authz.permissions import get_project_id_for_story, get_project_id_for_subtask
from src.utils.ai.project_chat_agent import run_project_chat_agent, execute_project_chat_action

router = APIRouter()


def _resolve_action_project_id(action_data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(action_data, dict):
        return None

    payload = action_data.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    project_id = str(payload.get("project_id") or "").strip()
    if project_id:
        return project_id

    epic_id = str(payload.get("epic_id") or "").strip()
    if epic_id:
        epic_response = get_epic_by_id(epic_id)
        if epic_response.success and isinstance(epic_response.data, dict):
            return epic_response.data.get("project_id")

    story_id = str(payload.get("story_id") or "").strip()
    if story_id:
        return get_project_id_for_story(story_id)

    subtask_id = str(payload.get("subtask_id") or "").strip()
    if subtask_id:
        return get_project_id_for_subtask(subtask_id)

    item_type = str(payload.get("item_type") or "").strip().lower()
    item_id = str(payload.get("item_id") or "").strip()
    if item_type and item_id:
        if item_type == "epic":
            epic_response = get_epic_by_id(item_id)
            if epic_response.success and isinstance(epic_response.data, dict):
                return epic_response.data.get("project_id")
        if item_type == "story":
            return get_project_id_for_story(item_id)
        if item_type == "subtask":
            return get_project_id_for_subtask(item_id)

    target_epic_id = str(payload.get("target_epic_id") or "").strip()
    if target_epic_id:
        target_epic_response = get_epic_by_id(target_epic_id)
        if target_epic_response.success and isinstance(target_epic_response.data, dict):
            return target_epic_response.data.get("project_id")

    return None


@router.post(
    "/chat",
    response_description="Project assistant chat with agentic tool-calling workflow.",
)
async def assistant_chat_route(
    req: AssistantChatRequest,
    user_data: UserData = Depends(get_current_user),
):
    project_id = (req.project_id or "").strip()

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        return JSONResponse(
            status_code=403,
            content=ResponseModel(success=False, message=access.message, data=None).dict(),
        )

    stored_history = get_assistant_chat_history(
        user_id=user_data.get_user_id(),
        project_id=project_id,
        limit=ASSISTANT_CHAT_HISTORY_LIMIT,
    )
    incoming_history = [item.model_dump() for item in req.history][-ASSISTANT_CHAT_HISTORY_LIMIT:]
    history_for_agent = stored_history if stored_history else incoming_history

    response = run_project_chat_agent(
        user_data=user_data,
        message=req.message,
        project_id=project_id,
        history=history_for_agent,
    )

    if not response.success:
        status_code = 400
        message_text = (response.message or "").lower()
        if "unauthorized" in message_text:
            status_code = 403
        elif "required" in message_text:
            status_code = 422
        return JSONResponse(
            status_code=status_code,
            content=response.dict(),
        )

    answer_text = ""
    if isinstance(response.data, dict):
        answer_text = str(response.data.get("answer") or "").strip()

    persisted_history = append_assistant_chat_messages(
        user_id=user_data.get_user_id(),
        project_id=project_id,
        new_messages=[
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": answer_text},
        ],
        limit=ASSISTANT_CHAT_HISTORY_LIMIT,
    )
    if isinstance(response.data, dict):
        response.data["history"] = persisted_history

    return JSONResponse(
        status_code=200,
        content=response.dict(),
    )


@router.get(
    "/chat/history",
    response_description="Load persisted assistant chat history for the current user and project.",
)
async def assistant_chat_history_route(
    project_id: str,
    user_data: UserData = Depends(get_current_user),
):
    project_id_value = (project_id or "").strip()
    if not project_id_value:
        return JSONResponse(
            status_code=422,
            content=ResponseModel(success=False, message="project_id is required", data=None).dict(),
        )

    access = get_project_access(project_id_value, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        return JSONResponse(
            status_code=403,
            content=ResponseModel(success=False, message=access.message, data=None).dict(),
        )

    history = get_assistant_chat_history(
        user_id=user_data.get_user_id(),
        project_id=project_id_value,
        limit=ASSISTANT_CHAT_HISTORY_LIMIT,
    )
    response = ResponseModel(
        success=True,
        message="Assistant chat history loaded",
        data={
            "project_id": project_id_value,
            "messages": history,
        },
    )
    return JSONResponse(status_code=200, content=response.dict())


@router.post(
    "/actions/execute",
    response_description="Execute a confirmed assistant action.",
)
async def assistant_execute_action_route(
    req: AssistantExecuteActionRequest,
    user_data: UserData = Depends(get_current_user),
):
    action_data = req.action.model_dump()

    response = execute_project_chat_action(
        user_data=user_data,
        action=action_data,
    )

    if not response.success:
        action_project_id = _resolve_action_project_id(action_data)
        if action_project_id:
            append_assistant_chat_messages(
                user_id=user_data.get_user_id(),
                project_id=action_project_id,
                new_messages=[{"role": "assistant", "content": f"Action failed: {response.message}"}],
                limit=ASSISTANT_CHAT_HISTORY_LIMIT,
            )

        status_code = 400
        message_text = (response.message or "").lower()
        if "unauthorized" in message_text or "forbidden" in message_text:
            status_code = 403
        elif "not found" in message_text:
            status_code = 404
        elif "required" in message_text or "invalid" in message_text:
            status_code = 422
        return JSONResponse(
            status_code=status_code,
            content=response.dict(),
        )

    action_project_id = _resolve_action_project_id(action_data)
    if action_project_id:
        append_assistant_chat_messages(
            user_id=user_data.get_user_id(),
            project_id=action_project_id,
            new_messages=[{"role": "assistant", "content": response.message or "Action executed"}],
            limit=ASSISTANT_CHAT_HISTORY_LIMIT,
        )

    return JSONResponse(
        status_code=200,
        content=response.dict(),
    )

