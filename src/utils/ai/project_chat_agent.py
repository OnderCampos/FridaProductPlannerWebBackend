import json
import logging
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, TypedDict, Annotated

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.setup.variables_setup import gpt40_mini_client
from src.services.setup.language_setup import get_default_llm_language, normalize_language
from src.utils.planning.epics import (
    create_epic,
    delete_epic,
    get_epic_by_id,
    get_epics_for_project_with_auth,
    update_epic,
    update_epic_status,
)
from src.utils.planning.members import format_team_members_response, get_project_members, get_member_by_id
from src.utils.authz.permissions import get_project_access, get_project_id_for_story, get_project_id_for_subtask
from src.utils.planning.projects import get_project_by_id
from src.utils.planning.sprints import (
    assign_item_to_sprint,
    create_sprint,
    delete_sprint,
    get_sprint_items,
    get_sprints_for_project,
    unassign_item_from_sprint,
    update_sprint,
)
from src.utils.planning.subtask_generation import (
    create_subtask_for_user_story,
    delete_subtasks_by_user_story,
    get_subtasks_by_user_story,
    update_subtask_fields,
    update_subtask_status,
)
from src.utils.planning.user_stories import (
    create_user_story,
    get_user_story_by_id,
    get_user_stories_by_epic_with_auth,
    update_user_story,
    update_user_story_fields,
)

try:
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
except Exception as import_error:  # pragma: no cover - defensive import guard
    END = None
    START = None
    StateGraph = None
    add_messages = None
    LANGGRAPH_IMPORT_ERROR = import_error
else:
    LANGGRAPH_IMPORT_ERROR = None


if add_messages is not None:
    class AgentState(TypedDict):
        messages: Annotated[List[BaseMessage], add_messages]
else:
    class AgentState(TypedDict):
        messages: List[BaseMessage]


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content)


def _to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _story_title(story: Dict[str, Any]) -> str:
    return (
        story.get("title")
        or story.get("user_story")
        or story.get("user_story_id")
        or "Untitled story"
    )


def _story_status(story: Dict[str, Any]) -> str:
    return story.get("status") or "To Do"


def _normalize_history(history: Optional[List[Dict[str, Any]]]) -> List[BaseMessage]:
    messages: List[BaseMessage] = []
    for item in history or []:
        role = (item.get("role") or "").strip().lower()
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


VALID_ITEM_STATUSES = {
    "To Do",
    "In Progress",
    "In Review",
    "Stopped",
    "Done",
    "Testing",
    "Rework",
    "Blocked",
}


def _normalize_item_status(status_value: Any) -> Optional[str]:
    if status_value is None:
        return None

    normalized = str(status_value).strip().lower().replace("_", " ")
    status_map = {
        "todo": "To Do",
        "to do": "To Do",
        "in progress": "In Progress",
        "inprogress": "In Progress",
        "in review": "In Review",
        "inreview": "In Review",
        "stopped": "Stopped",
        "done": "Done",
        "testing": "Testing",
        "rework": "Rework",
        "blocked": "Blocked",
    }
    status = status_map.get(normalized)
    if status in VALID_ITEM_STATUSES:
        return status
    return None


def _project_id_for_item(item_type: str, item_id: str) -> Optional[str]:
    normalized_type = (item_type or "").strip().lower()

    if normalized_type == "epic":
        epic_response = get_epic_by_id(item_id)
        if epic_response.success and isinstance(epic_response.data, dict):
            return epic_response.data.get("project_id")
        return None

    if normalized_type == "story":
        return get_project_id_for_story(item_id)

    if normalized_type == "subtask":
        return get_project_id_for_subtask(item_id)

    return None


def _require_project_lead_access(user_data: UserData, project_id: str) -> ResponseModel:
    if not project_id:
        return ResponseModel(success=False, message="project_id is required", data=None)

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        return ResponseModel(success=False, message=access.message, data=access.data)
    if not access.data.get("is_lead"):
        return ResponseModel(
            success=False,
            message="Forbidden: Action requires project lead permissions",
            data=None,
        )
    return ResponseModel(success=True, message="Project lead access granted", data=access.data)


def _action_result(
    action_type: str,
    action_id: Optional[str],
    operation_response: ResponseModel,
) -> ResponseModel:
    payload = {
        "action_type": action_type,
        "action_id": action_id,
        "result": operation_response.data,
    }
    if not operation_response.success:
        return ResponseModel(
            success=False,
            message=operation_response.message or "Failed to execute action",
            data=payload,
        )
    return ResponseModel(
        success=True,
        message=operation_response.message or "Action executed successfully",
        data=payload,
    )


def execute_project_chat_action(user_data: UserData, action: Dict[str, Any]) -> ResponseModel:
    if not isinstance(action, dict):
        return ResponseModel(success=False, message="action is required", data=None)

    action_type = str(action.get("action_type") or "").strip()
    action_id = action.get("action_id")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}

    if not action_type:
        return ResponseModel(success=False, message="action_type is required", data=None)

    user_id = user_data.get_user_id()
    user_email = user_data.get_email()

    if action_type == "update_item_status":
        item_type = str(payload.get("item_type") or "").strip().lower()
        item_id = str(payload.get("item_id") or "").strip()
        status = _normalize_item_status(payload.get("status"))

        if item_type not in {"epic", "story", "subtask"}:
            return ResponseModel(success=False, message="Invalid item_type", data=None)
        if not item_id:
            return ResponseModel(success=False, message="item_id is required", data=None)
        if status is None:
            return ResponseModel(success=False, message="Invalid status value", data=None)

        project_id = _project_id_for_item(item_type, item_id)
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        if item_type == "epic":
            operation = update_epic_status(item_id, user_id, status)
        elif item_type == "story":
            operation = update_user_story(
                item_id,
                user_id,
                {"status": status},
                user_email=user_email,
                user_name=user_data.get_user_name(),
            )
        else:
            operation = update_subtask_status(item_id, user_id, status)

        return _action_result(action_type, action_id, operation)

    if action_type == "create_epic":
        project_id = str(payload.get("project_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not project_id:
            return ResponseModel(success=False, message="project_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        access = _require_project_lead_access(user_data, project_id)
        if not access.success:
            return access

        name = str(fields.get("name") or "").strip()
        description = str(fields.get("description") or "").strip()
        if not name:
            return ResponseModel(success=False, message="Epic name is required", data=None)

        operation = create_epic(
            project_id,
            user_id,
            {
                "name": name,
                "description": description,
                "labels": fields.get("labels") if isinstance(fields.get("labels"), list) else [],
                "priority": str(fields.get("priority") or "").strip() or "Medium",
                "status": _normalize_item_status(fields.get("status")) or "To Do",
                "storyPoints": fields.get("storyPoints") or fields.get("story_points") or 0,
            },
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "delete_epic":
        epic_id = str(payload.get("epic_id") or "").strip()
        if not epic_id:
            return ResponseModel(success=False, message="epic_id is required", data=None)

        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success or not isinstance(epic_response.data, dict):
            return ResponseModel(success=False, message="Epic not found", data=None)

        access = _require_project_lead_access(user_data, epic_response.data.get("project_id") or "")
        if not access.success:
            return access

        operation = delete_epic(epic_id, user_id)
        return _action_result(action_type, action_id, operation)

    if action_type == "create_sprint":
        project_id = str(payload.get("project_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not project_id:
            return ResponseModel(success=False, message="project_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        access = _require_project_lead_access(user_data, project_id)
        if not access.success:
            return access

        name = str(fields.get("name") or "").strip()
        if not name:
            return ResponseModel(success=False, message="Sprint name is required", data=None)

        raw_length_days = fields.get("lengthDays", fields.get("length_days"))
        try:
            length_days = int(raw_length_days)
        except (TypeError, ValueError):
            return ResponseModel(success=False, message="lengthDays must be a number", data=None)

        operation = create_sprint(
            project_id=project_id,
            user_id=user_id,
            name=name,
            length_days=length_days,
            start_date=str(fields.get("startDate") or fields.get("start_date") or "").strip() or None,
            end_date=str(fields.get("endDate") or fields.get("end_date") or "").strip() or None,
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "update_sprint":
        project_id = str(payload.get("project_id") or "").strip()
        sprint_id = str(payload.get("sprint_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not project_id or not sprint_id:
            return ResponseModel(success=False, message="project_id and sprint_id are required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        access = _require_project_lead_access(user_data, project_id)
        if not access.success:
            return access

        raw_length_days = fields.get("lengthDays", fields.get("length_days"))
        length_days: Optional[int] = None
        if raw_length_days is not None:
            try:
                length_days = int(raw_length_days)
            except (TypeError, ValueError):
                return ResponseModel(success=False, message="lengthDays must be a number", data=None)

        operation = update_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_id,
            name=str(fields.get("name")).strip() if fields.get("name") is not None else None,
            length_days=length_days,
            start_date=str(fields.get("startDate") or fields.get("start_date")).strip()
            if fields.get("startDate") is not None or fields.get("start_date") is not None
            else None,
            end_date=str(fields.get("endDate") or fields.get("end_date")).strip()
            if fields.get("endDate") is not None or fields.get("end_date") is not None
            else None,
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "delete_sprint":
        project_id = str(payload.get("project_id") or "").strip()
        sprint_id = str(payload.get("sprint_id") or "").strip()
        if not project_id or not sprint_id:
            return ResponseModel(success=False, message="project_id and sprint_id are required", data=None)

        access = _require_project_lead_access(user_data, project_id)
        if not access.success:
            return access

        operation = delete_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_id,
            unassign_items=bool(payload.get("unassign_items", True)),
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "create_user_story":
        epic_id = str(payload.get("epic_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not epic_id:
            return ResponseModel(success=False, message="epic_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success or not isinstance(epic_response.data, dict):
            return ResponseModel(success=False, message="Epic not found", data=None)

        access = _require_project_lead_access(user_data, epic_response.data.get("project_id") or "")
        if not access.success:
            return access

        story_title = str(
            fields.get("user_story")
            or fields.get("title")
            or ""
        ).strip()
        description = str(fields.get("description") or "").strip()
        if not story_title or not description:
            return ResponseModel(
                success=False,
                message="User story title and description are required",
                data=None,
            )

        operation = create_user_story(
            epic_id,
            user_id,
            {
                "epic": str(epic_response.data.get("name") or "").strip(),
                "user_story": story_title,
                "description": description,
                "priority": fields.get("priority"),
                "story_points": fields.get("storyPoints", fields.get("story_points", 0)),
                "dueDate": fields.get("dueDate", fields.get("due_date")),
                "status": _normalize_item_status(fields.get("status")) or fields.get("status") or "To Do",
                "effortHours": fields.get("effortHours", fields.get("effort_hours", 0)),
                "dependencies": fields.get("dependencies") if isinstance(fields.get("dependencies"), list) else [],
            },
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "update_user_story":
        story_id = str(payload.get("story_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not story_id:
            return ResponseModel(success=False, message="story_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        story_response = get_user_story_by_id(
            story_id,
            user_id,
            allow_member=True,
            user_email=user_email,
        )
        if not story_response.success:
            return ResponseModel(success=False, message=story_response.message, data=None)

        epic_response = get_epic_by_id((story_response.data or {}).get("epic_id"))
        if not epic_response.success or not isinstance(epic_response.data, dict):
            return ResponseModel(success=False, message="Epic not found for story", data=None)

        project_id = epic_response.data.get("project_id")
        if not project_id:
            return ResponseModel(success=False, message="Project not found for story", data=None)
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        allowed_field_keys = {"title", "user_story", "description", "priority", "storyPoints", "dueDate", "status"}
        filtered_fields = {
            key: value for key, value in fields.items() if key in allowed_field_keys
        }

        if not filtered_fields:
            return ResponseModel(
                success=False,
                message="No supported story fields provided",
                data=None,
            )

        latest_result: Optional[ResponseModel] = None

        non_status_fields = {
            key: value for key, value in filtered_fields.items() if key != "status"
        }
        if non_status_fields:
            fields_response = update_user_story_fields(
                story_id,
                user_id,
                non_status_fields,
                user_email=user_email,
                user_name=user_data.get_user_name(),
            )
            if not fields_response.success:
                return _action_result(action_type, action_id, fields_response)
            latest_result = fields_response

        if "status" in filtered_fields:
            normalized_status = _normalize_item_status(filtered_fields.get("status"))
            if normalized_status is None:
                return ResponseModel(success=False, message="Invalid status value", data=None)
            status_response = update_user_story(
                story_id,
                user_id,
                {"status": normalized_status},
                user_email=user_email,
                user_name=user_data.get_user_name(),
            )
            if not status_response.success:
                return _action_result(action_type, action_id, status_response)
            latest_result = status_response

        if latest_result is None:
            return ResponseModel(
                success=False,
                message="No valid update fields were provided",
                data=None,
            )

        return _action_result(action_type, action_id, latest_result)

    if action_type == "assign_user_story":
        story_id = str(payload.get("story_id") or "").strip()
        assignee = str(payload.get("assignee") or "").strip()
        assignee_id = str(payload.get("assignee_id") or "").strip()

        if not story_id:
            return ResponseModel(success=False, message="story_id is required", data=None)
        if not assignee and not assignee_id:
            return ResponseModel(
                success=False,
                message="Either assignee or assignee_id is required",
                data=None,
            )

        story_response = get_user_story_by_id(
            story_id,
            user_id,
            allow_member=True,
            user_email=user_email,
        )
        if not story_response.success:
            return ResponseModel(success=False, message=story_response.message, data=None)

        epic_response = get_epic_by_id((story_response.data or {}).get("epic_id"))
        if not epic_response.success or not isinstance(epic_response.data, dict):
            return ResponseModel(success=False, message="Epic not found for story", data=None)

        project_id = epic_response.data.get("project_id")
        if not project_id:
            return ResponseModel(success=False, message="Project not found for story", data=None)
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        update_data: Dict[str, Any] = {}
        if assignee_id:
            member = get_member_by_id(project_id, assignee_id)
            if not member:
                return ResponseModel(success=False, message="Member not found", data=None)
            update_data["assigneeId"] = assignee_id
            update_data["assignee"] = member.get("name")
            update_data["assigned_to"] = assignee_id
        elif assignee:
            update_data["assignee"] = assignee

        operation = update_user_story(
            story_id,
            user_id,
            update_data,
            user_email=user_email,
            user_name=user_data.get_user_name(),
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "move_story_to_epic":
        story_id = str(payload.get("story_id") or "").strip()
        target_epic_id = str(payload.get("target_epic_id") or "").strip()

        if not story_id or not target_epic_id:
            return ResponseModel(
                success=False,
                message="story_id and target_epic_id are required",
                data=None,
            )

        story_response = get_user_story_by_id(
            story_id,
            user_id,
            allow_member=True,
            user_email=user_email,
        )
        if not story_response.success:
            return ResponseModel(success=False, message=story_response.message, data=None)

        story_epic_id = (story_response.data or {}).get("epic_id")
        source_epic_response = get_epic_by_id(story_epic_id)
        target_epic_response = get_epic_by_id(target_epic_id)

        if not source_epic_response.success or not target_epic_response.success:
            return ResponseModel(success=False, message="Source or target epic not found", data=None)

        source_project_id = (source_epic_response.data or {}).get("project_id")
        target_project_id = (target_epic_response.data or {}).get("project_id")
        if not source_project_id or source_project_id != target_project_id:
            return ResponseModel(
                success=False,
                message="Story can only be moved to an epic in the same project",
                data=None,
            )

        access = _require_project_lead_access(user_data, source_project_id)
        if not access.success:
            return access

        operation = update_user_story(
            story_id,
            user_id,
            {"epic_id": target_epic_id},
            user_email=user_email,
            user_name=user_data.get_user_name(),
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "update_epic":
        epic_id = str(payload.get("epic_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not epic_id:
            return ResponseModel(success=False, message="epic_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success or not isinstance(epic_response.data, dict):
            return ResponseModel(success=False, message="Epic not found", data=None)

        project_id = epic_response.data.get("project_id")
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        update_fields = {}
        if "name" in fields:
            update_fields["name"] = fields.get("name")
        if "description" in fields:
            update_fields["description"] = fields.get("description")
        if "labels" in fields:
            update_fields["labels"] = fields.get("labels")

        latest_result: Optional[ResponseModel] = None

        if update_fields:
            update_response = update_epic(epic_id, user_id, update_fields)
            if not update_response.success:
                return _action_result(action_type, action_id, update_response)
            latest_result = update_response

        if "status" in fields:
            normalized_status = _normalize_item_status(fields.get("status"))
            if normalized_status is None:
                return ResponseModel(success=False, message="Invalid status value", data=None)
            status_response = update_epic_status(epic_id, user_id, normalized_status)
            if not status_response.success:
                return _action_result(action_type, action_id, status_response)
            latest_result = status_response

        if latest_result is None:
            return ResponseModel(
                success=False,
                message="No supported epic fields provided",
                data=None,
            )

        return _action_result(action_type, action_id, latest_result)

    if action_type == "update_subtask":
        subtask_id = str(payload.get("subtask_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not subtask_id:
            return ResponseModel(success=False, message="subtask_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        project_id = get_project_id_for_subtask(subtask_id)
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        non_status_fields = {key: value for key, value in fields.items() if key != "status"}
        latest_result: Optional[ResponseModel] = None

        if non_status_fields:
            fields_response = update_subtask_fields(subtask_id, user_id, non_status_fields)
            if not fields_response.success:
                return _action_result(action_type, action_id, fields_response)
            latest_result = fields_response

        if "status" in fields:
            normalized_status = _normalize_item_status(fields.get("status"))
            if normalized_status is None:
                return ResponseModel(success=False, message="Invalid status value", data=None)
            status_response = update_subtask_status(subtask_id, user_id, normalized_status)
            if not status_response.success:
                return _action_result(action_type, action_id, status_response)
            latest_result = status_response

        if latest_result is None:
            return ResponseModel(success=False, message="No valid subtask fields provided", data=None)

        return _action_result(action_type, action_id, latest_result)

    if action_type == "create_subtask":
        story_id = str(payload.get("story_id") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

        if not story_id:
            return ResponseModel(success=False, message="story_id is required", data=None)
        if not fields:
            return ResponseModel(success=False, message="fields are required", data=None)

        project_id = get_project_id_for_story(story_id)
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        title = str(fields.get("title") or "").strip()
        if not title:
            return ResponseModel(success=False, message="Subtask title is required", data=None)

        operation = create_subtask_for_user_story(
            story_id,
            user_id,
            {
                "title": title,
                "description": str(fields.get("description") or "").strip(),
                "estimated_hours": fields.get("estimated_hours", 0),
                "complexity": str(fields.get("complexity") or "Medium").strip() or "Medium",
                "dependencies": fields.get("dependencies") if isinstance(fields.get("dependencies"), list) else [],
                "status": _normalize_item_status(fields.get("status")) or fields.get("status") or "To Do",
            },
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "assign_sprint_item":
        project_id = str(payload.get("project_id") or "").strip()
        sprint_id = str(payload.get("sprint_id") or "").strip()
        item_type = str(payload.get("item_type") or "").strip().lower()
        item_id = str(payload.get("item_id") or "").strip()
        include_subtasks = bool(payload.get("include_subtasks", False))

        if not project_id or not sprint_id or not item_type or not item_id:
            return ResponseModel(
                success=False,
                message="project_id, sprint_id, item_type and item_id are required",
                data=None,
            )

        access = _require_project_lead_access(user_data, project_id)
        if not access.success:
            return access

        operation = assign_item_to_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_id,
            item_type=item_type,
            item_id=item_id,
            include_subtasks=include_subtasks,
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "unassign_sprint_item":
        project_id = str(payload.get("project_id") or "").strip()
        sprint_id = str(payload.get("sprint_id") or "").strip()
        item_type = str(payload.get("item_type") or "").strip().lower()
        item_id = str(payload.get("item_id") or "").strip()

        if not project_id or not sprint_id or not item_type or not item_id:
            return ResponseModel(
                success=False,
                message="project_id, sprint_id, item_type and item_id are required",
                data=None,
            )

        access = _require_project_lead_access(user_data, project_id)
        if not access.success:
            return access

        operation = unassign_item_from_sprint(
            sprint_id=sprint_id,
            project_id=project_id,
            user_id=user_id,
            item_type=item_type,
            item_id=item_id,
        )
        return _action_result(action_type, action_id, operation)

    if action_type == "delete_subtask":
        subtask_id = str(payload.get("subtask_id") or "").strip()
        if not subtask_id:
            return ResponseModel(success=False, message="subtask_id is required", data=None)

        project_id = get_project_id_for_subtask(subtask_id)
        access = _require_project_lead_access(user_data, project_id or "")
        if not access.success:
            return access

        operation = delete_subtasks_by_user_story(subtask_id, user_id)
        return _action_result(action_type, action_id, operation)

    return ResponseModel(success=False, message=f"Unsupported action_type: {action_type}", data=None)


def run_project_chat_agent(
    user_data: UserData,
    message: str,
    project_id: str,
    history: Optional[List[Dict[str, Any]]] = None,
    language: Optional[str] = None,
) -> ResponseModel:
    if LANGGRAPH_IMPORT_ERROR is not None:
        return ResponseModel(
            success=False,
            message=f"LangGraph is required for agentic chat flow: {LANGGRAPH_IMPORT_ERROR}",
            data=None,
        )

    user_message = (message or "").strip()
    if not user_message:
        return ResponseModel(success=False, message="message is required", data=None)

    focused_project_id = (project_id or "").strip()
    if not focused_project_id:
        return ResponseModel(success=False, message="project_id is required", data=None)

    access_response = get_project_access(
        focused_project_id,
        user_data.get_user_id(),
        user_data.get_email(),
    )
    if not access_response.success:
        return ResponseModel(
            success=False,
            message=access_response.message or "Unauthorized: You don't have access to this project",
            data=None,
        )

    focused_project_response = get_project_by_id(
        focused_project_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not focused_project_response.success or not isinstance(focused_project_response.data, dict):
        return ResponseModel(
            success=False,
            message=focused_project_response.message or "Failed to load project details",
            data=None,
        )

    assigned_projects: List[Dict[str, Any]] = [focused_project_response.data]
    assigned_project_ids: Set[str] = {focused_project_id}

    referenced_projects: Set[str] = set()
    project_cache: Dict[str, Dict[str, Any]] = {focused_project_id: focused_project_response.data}
    sprint_cache: Dict[str, List[Dict[str, Any]]] = {}
    pending_actions: List[Dict[str, Any]] = []
    pending_action_fingerprints: Set[str] = set()

    def _resolve_project_arg(project_id_arg: Optional[str]) -> Optional[str]:
        candidate = (project_id_arg or "").strip()
        if not candidate:
            return focused_project_id
        if candidate != focused_project_id:
            return None
        return focused_project_id

    def _load_project(project_id_arg: str) -> Optional[Dict[str, Any]]:
        if project_id_arg in project_cache:
            return project_cache[project_id_arg]

        if project_id_arg not in assigned_project_ids:
            return None

        project_response = get_project_by_id(
            project_id_arg,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email(),
        )
        if not project_response.success or not isinstance(project_response.data, dict):
            return None
        project_cache[project_id_arg] = project_response.data
        return project_response.data

    def _project_scope(project_id_arg: Optional[str]) -> List[str]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return []
        return [resolved_project_id]

    def _load_project_sprints(project_id_arg: str) -> List[Dict[str, Any]]:
        if project_id_arg in sprint_cache:
            return sprint_cache[project_id_arg]

        if project_id_arg not in assigned_project_ids:
            return []

        sprint_response = get_sprints_for_project(
            project_id=project_id_arg,
            user_id=user_data.get_user_id(),
            include_counts=True,
            allow_members=True,
            user_email=user_data.get_email(),
        )
        if not sprint_response.success or not isinstance(sprint_response.data, list):
            return []

        sprint_cache[project_id_arg] = sprint_response.data
        return sprint_response.data

    def _resolve_sprint_project(
        sprint_id_arg: str,
        project_id_arg: Optional[str] = None,
    ) -> Optional[str]:
        scope = _project_scope(project_id_arg)
        if project_id_arg and not scope:
            return None

        for scoped_project_id in scope:
            for sprint in _load_project_sprints(scoped_project_id):
                if sprint.get("id") == sprint_id_arg:
                    return scoped_project_id
        return None

    def _find_story_candidates(
        story_query_arg: str,
        project_id_arg: Optional[str] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        text = (story_query_arg or "").strip().lower()
        if not text:
            return []

        scope = _project_scope(project_id_arg if project_id_arg is not None else focused_project_id)
        candidates: List[Dict[str, Any]] = []

        query_tokens = [token for token in text.split() if token]
        for scoped_project_id in scope:
            loaded_project = _load_project(scoped_project_id)
            if not loaded_project:
                continue

            project_name = loaded_project.get("name")
            project_key = loaded_project.get("project_key")
            for epic in loaded_project.get("epics") or []:
                epic_id = epic.get("id")
                epic_name = epic.get("name")
                for story in epic.get("userStories") or []:
                    story_id = story.get("id")
                    if not story_id:
                        continue

                    story_title = _story_title(story)
                    story_key = story.get("user_story_id") or ""
                    story_description = story.get("description") or ""
                    title_lower = story_title.lower()
                    key_lower = str(story_key).lower()
                    description_lower = story_description.lower()
                    combined = f"{title_lower} {key_lower} {description_lower}"

                    score = 0
                    match_basis = ""
                    if text == title_lower or text == key_lower:
                        score = 120
                        match_basis = "exact"
                    elif text in title_lower:
                        score = 100
                        match_basis = "title_contains"
                    elif text in key_lower:
                        score = 95
                        match_basis = "id_contains"
                    elif text in description_lower:
                        score = 80
                        match_basis = "description_contains"
                    elif query_tokens:
                        token_hits = sum(1 for token in query_tokens if token in combined)
                        if token_hits > 0:
                            coverage = token_hits / len(query_tokens)
                            if coverage >= 0.7:
                                score = 60 + token_hits
                                match_basis = "token_similarity"

                    if score <= 0:
                        continue

                    candidates.append(
                        {
                            "story_id": story_id,
                            "story_title": story_title,
                            "user_story_id": story_key,
                            "status": _story_status(story),
                            "epic_id": epic_id,
                            "epic_name": epic_name,
                            "project_id": scoped_project_id,
                            "project_name": project_name,
                            "project_key": project_key,
                            "score": score,
                            "match_basis": match_basis,
                        }
                    )

        candidates.sort(key=lambda item: item.get("score", 0), reverse=True)
        max_items = max(1, min(limit, 20))
        return candidates[:max_items]

    def _find_epic_candidates(
        epic_query_arg: str,
        project_id_arg: Optional[str] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        text = (epic_query_arg or "").strip().lower()
        if not text:
            return []

        scope = _project_scope(project_id_arg if project_id_arg is not None else focused_project_id)
        candidates: List[Dict[str, Any]] = []
        query_tokens = [token for token in text.split() if token]

        for scoped_project_id in scope:
            loaded_project = _load_project(scoped_project_id)
            if not loaded_project:
                continue

            project_name = loaded_project.get("name")
            project_key = loaded_project.get("project_key")
            for epic in loaded_project.get("epics") or []:
                epic_id = str(epic.get("id") or "").strip()
                epic_name = str(epic.get("name") or "").strip()
                epic_description = str(epic.get("description") or "").strip()
                if not epic_id or not epic_name:
                    continue

                name_lower = epic_name.lower()
                description_lower = epic_description.lower()
                combined = f"{name_lower} {description_lower}"

                score = 0
                match_basis = ""
                if text == epic_id.lower() or text == name_lower:
                    score = 120
                    match_basis = "exact"
                elif text in name_lower:
                    score = 100
                    match_basis = "name_contains"
                elif text in description_lower:
                    score = 80
                    match_basis = "description_contains"
                elif query_tokens:
                    token_hits = sum(1 for token in query_tokens if token in combined)
                    if token_hits > 0:
                        coverage = token_hits / len(query_tokens)
                        if coverage >= 0.7:
                            score = 60 + token_hits
                            match_basis = "token_similarity"

                if score <= 0:
                    continue

                candidates.append(
                    {
                        "epic_id": epic_id,
                        "epic_name": epic_name,
                        "project_id": scoped_project_id,
                        "project_name": project_name,
                        "project_key": project_key,
                        "status": epic.get("status"),
                        "score": score,
                        "match_basis": match_basis,
                    }
                )

        candidates.sort(key=lambda item: item.get("score", 0), reverse=True)
        return candidates[: max(1, min(limit, 20))]

    def _resolve_epic_for_action(
        epic_id_arg: Optional[str] = None,
        epic_query_arg: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        epic_id_value = (epic_id_arg or "").strip()
        if epic_id_value:
            epic_response = get_epic_by_id(epic_id_value)
            if not epic_response.success or not isinstance(epic_response.data, dict):
                return {"error": "Epic not found or not accessible"}

            epic_project_id = epic_response.data.get("project_id")
            if epic_project_id not in assigned_project_ids:
                return {"error": "Epic not found or not accessible"}
            if project_id_arg and epic_project_id != project_id_arg:
                return {"error": "Epic not found for the specified project"}

            return {
                "epic_id": epic_id_value,
                "project_id": epic_project_id,
                "epic_name": epic_response.data.get("name"),
            }

        query_value = (epic_query_arg or "").strip()
        if not query_value:
            return {"error": "epic_id or epic_query is required"}

        candidates = _find_epic_candidates(query_value, project_id_arg=project_id_arg, limit=8)
        if not candidates:
            return {"error": f"No epics matched '{query_value}' in this project"}
        if len(candidates) == 1:
            selected = candidates[0]
            return {
                "epic_id": selected.get("epic_id"),
                "project_id": selected.get("project_id"),
                "epic_name": selected.get("epic_name"),
            }

        top = candidates[0]
        second = candidates[1]
        top_score = int(top.get("score", 0))
        second_score = int(second.get("score", 0))
        if top_score >= 100 and top_score >= second_score + 15:
            return {
                "epic_id": top.get("epic_id"),
                "project_id": top.get("project_id"),
                "epic_name": top.get("epic_name"),
            }

        return {
            "error": "Multiple epics matched your reference. Please clarify which one you mean.",
            "requires_disambiguation": True,
            "candidates": candidates[:5],
        }

    def _find_sprint_candidates(
        sprint_query_arg: str,
        project_id_arg: Optional[str] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        text = (sprint_query_arg or "").strip().lower()
        if not text:
            return []

        scope = _project_scope(project_id_arg if project_id_arg is not None else focused_project_id)
        candidates: List[Dict[str, Any]] = []
        query_tokens = [token for token in text.split() if token]

        for scoped_project_id in scope:
            project = _load_project(scoped_project_id)
            if not project:
                continue

            for sprint in _load_project_sprints(scoped_project_id):
                sprint_id = str(sprint.get("id") or "").strip()
                sprint_name = str(sprint.get("name") or "").strip()
                if not sprint_id or not sprint_name:
                    continue

                name_lower = sprint_name.lower()
                combined = " ".join(
                    [
                        name_lower,
                        str(sprint.get("startDate") or "").lower(),
                        str(sprint.get("endDate") or "").lower(),
                    ]
                )

                score = 0
                match_basis = ""
                if text == sprint_id.lower() or text == name_lower:
                    score = 120
                    match_basis = "exact"
                elif text in name_lower:
                    score = 100
                    match_basis = "name_contains"
                elif query_tokens:
                    token_hits = sum(1 for token in query_tokens if token in combined)
                    if token_hits > 0:
                        coverage = token_hits / len(query_tokens)
                        if coverage >= 0.7:
                            score = 60 + token_hits
                            match_basis = "token_similarity"

                if score <= 0:
                    continue

                candidates.append(
                    {
                        "sprint_id": sprint_id,
                        "sprint_name": sprint_name,
                        "project_id": scoped_project_id,
                        "project_name": project.get("name"),
                        "project_key": project.get("project_key"),
                        "startDate": sprint.get("startDate"),
                        "endDate": sprint.get("endDate"),
                        "lengthDays": sprint.get("lengthDays"),
                        "score": score,
                        "match_basis": match_basis,
                    }
                )

        candidates.sort(key=lambda item: item.get("score", 0), reverse=True)
        return candidates[: max(1, min(limit, 20))]

    def _resolve_sprint_for_action(
        sprint_id_arg: Optional[str] = None,
        sprint_query_arg: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        sprint_id_value = (sprint_id_arg or "").strip()
        if sprint_id_value:
            sprint_project_id = _resolve_sprint_project(sprint_id_value, project_id_arg)
            if not sprint_project_id:
                return {"error": "Sprint not found or not accessible"}

            sprint_name = ""
            for sprint in _load_project_sprints(sprint_project_id):
                if sprint.get("id") == sprint_id_value:
                    sprint_name = sprint.get("name") or ""
                    break
            return {
                "sprint_id": sprint_id_value,
                "project_id": sprint_project_id,
                "sprint_name": sprint_name,
            }

        query_value = (sprint_query_arg or "").strip()
        if not query_value:
            return {"error": "sprint_id or sprint_query is required"}

        candidates = _find_sprint_candidates(query_value, project_id_arg=project_id_arg, limit=8)
        if not candidates:
            return {"error": f"No sprints matched '{query_value}' in this project"}
        if len(candidates) == 1:
            selected = candidates[0]
            return {
                "sprint_id": selected.get("sprint_id"),
                "project_id": selected.get("project_id"),
                "sprint_name": selected.get("sprint_name"),
            }

        top = candidates[0]
        second = candidates[1]
        top_score = int(top.get("score", 0))
        second_score = int(second.get("score", 0))
        if top_score >= 100 and top_score >= second_score + 15:
            return {
                "sprint_id": top.get("sprint_id"),
                "project_id": top.get("project_id"),
                "sprint_name": top.get("sprint_name"),
            }

        return {
            "error": "Multiple sprints matched your reference. Please clarify which one you mean.",
            "requires_disambiguation": True,
            "candidates": candidates[:5],
        }

    def _resolve_story_for_action(
        story_id_arg: Optional[str] = None,
        story_query_arg: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        story_id_value = (story_id_arg or "").strip()
        if story_id_value:
            project_for_story = _project_for_story(story_id_value)
            if not project_for_story:
                return {"error": "Story not found or not accessible"}

            story_response = get_user_story_by_id(
                story_id_value,
                user_data.get_user_id(),
                allow_member=True,
                user_email=user_data.get_email(),
            )
            if not story_response.success:
                return {"error": story_response.message}

            story_data = story_response.data or {}
            return {
                "story_id": story_id_value,
                "project_id": project_for_story,
                "story_title": _story_title(story_data),
                "user_story_id": story_data.get("user_story_id"),
            }

        query_value = (story_query_arg or "").strip()
        if not query_value:
            return {"error": "story_id or story_query is required"}

        candidates = _find_story_candidates(
            query_value,
            project_id_arg=project_id_arg,
            limit=8,
        )
        if not candidates:
            return {
                "error": f"No user stories matched '{query_value}' in your accessible projects"
            }

        if len(candidates) == 1:
            selected = candidates[0]
            return {
                "story_id": selected.get("story_id"),
                "project_id": selected.get("project_id"),
                "story_title": selected.get("story_title"),
                "user_story_id": selected.get("user_story_id"),
            }

        top = candidates[0]
        second = candidates[1]
        top_score = int(top.get("score", 0))
        second_score = int(second.get("score", 0))
        if top_score >= 100 and top_score >= second_score + 15:
            return {
                "story_id": top.get("story_id"),
                "project_id": top.get("project_id"),
                "story_title": top.get("story_title"),
                "user_story_id": top.get("user_story_id"),
            }

        return {
            "error": "Multiple stories matched your reference. Please clarify which one you mean.",
            "requires_disambiguation": True,
            "candidates": [
                {
                    "story_id": candidate.get("story_id"),
                    "story_title": candidate.get("story_title"),
                    "user_story_id": candidate.get("user_story_id"),
                    "project_id": candidate.get("project_id"),
                    "project_key": candidate.get("project_key"),
                    "project_name": candidate.get("project_name"),
                    "epic_id": candidate.get("epic_id"),
                    "epic_name": candidate.get("epic_name"),
                    "status": candidate.get("status"),
                }
                for candidate in candidates[:5]
            ],
        }

    def _project_for_story(story_id_arg: str) -> Optional[str]:
        story_response = get_user_story_by_id(
            story_id_arg,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email(),
        )
        if not story_response.success or not isinstance(story_response.data, dict):
            return None

        epic_response = get_epic_by_id(story_response.data.get("epic_id"))
        if not epic_response.success or not isinstance(epic_response.data, dict):
            return None

        project_for_story = epic_response.data.get("project_id")
        if project_for_story not in assigned_project_ids:
            return None
        return project_for_story

    def _project_for_subtask(subtask_id_arg: str) -> Optional[str]:
        project_for_subtask = get_project_id_for_subtask(subtask_id_arg)
        if project_for_subtask not in assigned_project_ids:
            return None
        return project_for_subtask

    def _validate_lead_for_project(project_id_arg: str) -> Optional[str]:
        if project_id_arg not in assigned_project_ids:
            return "Project not found or not accessible"

        access = get_project_access(
            project_id_arg,
            user_data.get_user_id(),
            user_data.get_email(),
        )
        if not access.success:
            return access.message or "Project access denied"
        if not access.data.get("is_lead"):
            return "Action requires project lead permissions"
        return None

    def _register_pending_action(
        action_type: str,
        title: str,
        summary: str,
        payload: Dict[str, Any],
        project_id_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        fingerprint = json.dumps(
            {
                "action_type": action_type,
                "payload": payload,
            },
            sort_keys=True,
            default=str,
        )

        if fingerprint in pending_action_fingerprints:
            for existing in pending_actions:
                if (
                    existing.get("action_type") == action_type
                    and json.dumps(existing.get("payload", {}), sort_keys=True, default=str)
                    == json.dumps(payload, sort_keys=True, default=str)
                ):
                    return existing

        action = {
            "action_id": f"act_{uuid4().hex[:12]}",
            "action_type": action_type,
            "title": title,
            "summary": summary,
            "payload": payload,
            "requires_confirmation": True,
        }
        pending_action_fingerprints.add(fingerprint)
        pending_actions.append(action)

        if project_id_hint:
            referenced_projects.add(project_id_hint)

        return action

    def _normalize_stories(stories: List[Dict[str, Any]], limit: int = 100) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for story in stories[: max(1, min(limit, 500))]:
            normalized.append(
                {
                    "id": story.get("id"),
                    "user_story_id": story.get("user_story_id"),
                    "title": _story_title(story),
                    "description": story.get("description"),
                    "status": _story_status(story),
                    "priority": story.get("priority"),
                    "assignee": story.get("assignee"),
                    "storyPoints": story.get("storyPoints"),
                    "dueDate": story.get("dueDate") or story.get("due_date"),
                    "createdDate": story.get("createdDate") or story.get("created_at"),
                    "effortHours": story.get("effortHours") or story.get("effort_hours"),
                    "dependencies": story.get("dependencies") or [],
                }
            )
        return normalized

    def _normalize_subtasks(tasks: List[Dict[str, Any]], limit: int = 150) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for task in tasks[: max(1, min(limit, 500))]:
            normalized.append(
                {
                    "id": task.get("id"),
                    "order": task.get("order"),
                    "title": task.get("title"),
                    "description": task.get("description"),
                    "status": task.get("status"),
                    "estimated_hours": task.get("estimated_hours"),
                    "complexity": task.get("complexity"),
                    "assignee": task.get("assignee"),
                    "dueDate": task.get("dueDate") or task.get("due_date"),
                    "createdDate": task.get("createdDate") or task.get("created_at"),
                    "completedDate": task.get("completedDate") or task.get("completed_date"),
                }
            )
        return normalized

    def get_project_details(project_id_arg: Optional[str] = None) -> Dict[str, Any]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return {"error": "Project not found or not accessible"}

        project = _load_project(resolved_project_id)
        if not project:
            return {"error": "Failed to load project details"}

        referenced_projects.add(resolved_project_id)
        epics = project.get("epics") or []
        stories = [story for epic in epics for story in (epic.get("userStories") or [])]
        done_stories = sum(1 for story in stories if _story_status(story).lower() == "done")
        done_epics = sum(1 for epic in epics if str(epic.get("status", "")).lower() == "done")

        epics_preview = [
            {
                "id": epic.get("id"),
                "name": epic.get("name"),
                "status": epic.get("status"),
                "priority": epic.get("priority"),
                "story_count": len(epic.get("userStories") or []),
            }
            for epic in epics[:50]
        ]

        return {
            "project": {
                "id": project.get("id"),
                "name": project.get("name"),
                "project_key": project.get("project_key"),
                "description": project.get("description"),
                "status": project.get("status"),
                "projectLead": project.get("projectLead"),
                "techStack": project.get("technical_stack") or project.get("techStack") or [],
                "roles": project.get("roles") or [],
            },
            "metrics": {
                "epics_total": len(epics),
                "epics_done": done_epics,
                "stories_total": len(stories),
                "stories_done": done_stories,
            },
            "epics_preview": epics_preview,
        }

    def get_project_members_tool(project_id_arg: Optional[str] = None) -> Dict[str, Any]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return {"error": "Project not found or not accessible"}

        members = get_project_members(resolved_project_id)
        referenced_projects.add(resolved_project_id)
        return {
            "project_id": resolved_project_id,
            "count": len(members),
            "members": format_team_members_response(members),
        }

    def get_project_epics_tool(
        project_id_arg: Optional[str] = None,
        include_user_stories: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return {"error": "Project not found or not accessible"}

        response = get_epics_for_project_with_auth(
            resolved_project_id, user_data.get_user_id(), user_data.get_email()
        )
        if not response.success:
            return {"error": response.message}

        referenced_projects.add(resolved_project_id)
        epics = response.data or []
        normalized_epics: List[Dict[str, Any]] = []
        max_items = max(1, min(limit, 500))
        for epic in epics[:max_items]:
            epic_entry = {
                "id": epic.get("id"),
                "name": epic.get("name"),
                "description": epic.get("description"),
                "status": epic.get("status"),
                "priority": epic.get("priority"),
                "assignee": epic.get("assignee"),
                "labels": epic.get("labels") or [],
                "story_points": epic.get("storyPoints") or epic.get("story_points"),
                "dueDate": epic.get("dueDate") or epic.get("due_date"),
            }
            if include_user_stories and epic.get("id"):
                stories_response = get_user_stories_by_epic_with_auth(
                    epic.get("id"),
                    user_data.get_user_id(),
                    user_data.get_email(),
                )
                if stories_response.success:
                    stories = stories_response.data or []
                    epic_entry["user_stories"] = _normalize_stories(stories, limit=80)
                    epic_entry["story_count"] = len(stories)
                else:
                    epic_entry["user_stories"] = []
                    epic_entry["story_count"] = 0
            normalized_epics.append(epic_entry)

        return {
            "project_id": resolved_project_id,
            "count": len(normalized_epics),
            "epics": normalized_epics,
        }

    def get_epic_user_stories_tool(
        epic_id: str,
        include_subtasks: bool = False,
        limit: int = 120,
    ) -> Dict[str, Any]:
        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success:
            return {"error": "Epic not found"}

        epic_data = epic_response.data or {}
        epic_project_id = epic_data.get("project_id")
        if not epic_project_id or epic_project_id not in assigned_project_ids:
            return {"error": "Epic not found or not accessible"}

        stories_response = get_user_stories_by_epic_with_auth(
            epic_id, user_data.get_user_id(), user_data.get_email()
        )
        if not stories_response.success:
            return {"error": stories_response.message}

        referenced_projects.add(epic_project_id)
        stories = stories_response.data or []
        normalized_stories = _normalize_stories(stories, limit=limit)

        if include_subtasks:
            for story in normalized_stories[:40]:
                story_id = story.get("id")
                if not story_id:
                    story["subtasks"] = []
                    continue
                subtasks_response = get_subtasks_by_user_story(
                    story_id,
                    user_data.get_user_id(),
                    allow_member=True,
                    user_email=user_data.get_email(),
                )
                if subtasks_response.success:
                    story["subtasks"] = _normalize_subtasks(subtasks_response.data or [], limit=80)
                else:
                    story["subtasks"] = []

        return {
            "epic": {
                "id": epic_data.get("id"),
                "name": epic_data.get("name"),
                "project_id": epic_project_id,
            },
            "count": len(normalized_stories),
            "stories": normalized_stories,
        }

    def get_story_subtasks_tool(story_id: str, limit: int = 150) -> Dict[str, Any]:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email(),
        )
        if not story_response.success:
            return {"error": story_response.message}

        story_data = story_response.data or {}
        epic_response = get_epic_by_id(story_data.get("epic_id"))
        project_for_story = None
        if epic_response.success and isinstance(epic_response.data, dict):
            project_for_story = epic_response.data.get("project_id")
            if project_for_story and project_for_story not in assigned_project_ids:
                return {"error": "Story not accessible"}

        subtasks_response = get_subtasks_by_user_story(
            story_id,
            user_data.get_user_id(),
            allow_member=True,
            user_email=user_data.get_email(),
        )
        if not subtasks_response.success:
            return {"error": subtasks_response.message}

        if project_for_story:
            referenced_projects.add(project_for_story)

        subtasks = subtasks_response.data or []
        return {
            "story": {
                "id": story_data.get("id"),
                "title": _story_title(story_data),
                "status": _story_status(story_data),
            },
            "count": len(subtasks),
            "subtasks": _normalize_subtasks(subtasks, limit=limit),
        }

    def get_project_sprints_tool(
        project_id_arg: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return {"error": "Project not found or not accessible"}

        sprints = _load_project_sprints(resolved_project_id)
        referenced_projects.add(resolved_project_id)
        max_items = max(1, min(limit, 500))
        return {
            "project_id": resolved_project_id,
            "count": len(sprints),
            "sprints": sprints[:max_items],
        }

    def get_sprint_items_tool(
        sprint_id: str,
        project_id_arg: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        sprint_id_value = (sprint_id or "").strip()
        if not sprint_id_value:
            return {"error": "sprint_id is required"}

        sprint_project_id = _resolve_sprint_project(sprint_id_value, project_id_arg)
        if not sprint_project_id:
            if project_id_arg and project_id_arg not in assigned_project_ids:
                return {"error": "Project not found or not accessible"}
            return {"error": "Sprint not found or not accessible"}

        sprint_items_response = get_sprint_items(
            sprint_id=sprint_id_value,
            project_id=sprint_project_id,
            user_id=user_data.get_user_id(),
            allow_members=True,
            user_email=user_data.get_email(),
        )
        if not sprint_items_response.success:
            return {"error": sprint_items_response.message}

        referenced_projects.add(sprint_project_id)
        items = sprint_items_response.data or []
        max_items = max(1, min(limit, 500))
        stories_count = sum(1 for item in items if item.get("type") == "story")
        subtasks_count = sum(1 for item in items if item.get("type") == "subtask")
        return {
            "project_id": sprint_project_id,
            "sprint_id": sprint_id_value,
            "count": len(items),
            "stories_count": stories_count,
            "subtasks_count": subtasks_count,
            "items": items[:max_items],
        }

    def search_sprints_tool(
        query: str,
        project_id_arg: Optional[str] = None,
        limit: int = 40,
    ) -> Dict[str, Any]:
        text = (query or "").strip().lower()
        if not text:
            return {"error": "query is required"}

        scope_project_id = _resolve_project_arg(project_id_arg)
        scope = _project_scope(project_id_arg)
        if project_id_arg and not scope:
            return {"error": "Project not found or not accessible"}

        hits: List[Dict[str, Any]] = []
        for scoped_project_id in scope:
            project = _load_project(scoped_project_id)
            if not project:
                continue

            sprints = _load_project_sprints(scoped_project_id)
            if not sprints:
                continue

            referenced_projects.add(scoped_project_id)
            for sprint in sprints:
                sprint_name = sprint.get("name") or ""
                if text not in sprint_name.lower():
                    continue
                hits.append(
                    {
                        "project_id": scoped_project_id,
                        "project_name": project.get("name"),
                        "project_key": project.get("project_key"),
                        "sprint_id": sprint.get("id"),
                        "sprint_name": sprint_name,
                        "lengthDays": sprint.get("lengthDays"),
                        "startDate": sprint.get("startDate"),
                        "endDate": sprint.get("endDate"),
                        "itemsCount": sprint.get("itemsCount"),
                    }
                )

        max_items = max(1, min(limit, 500))
        return {
            "query": query,
            "count": len(hits),
            "hits": hits[:max_items],
            "scope_project_id": scope_project_id or focused_project_id,
        }

    def propose_update_item_status_tool(
        item_type: str,
        item_id: str,
        status: str,
    ) -> Dict[str, Any]:
        normalized_type = (item_type or "").strip().lower()
        if normalized_type not in {"epic", "story", "subtask"}:
            return {"error": "item_type must be epic, story, or subtask"}

        item_id_value = (item_id or "").strip()
        if not item_id_value:
            return {"error": "item_id is required"}

        normalized_status = _normalize_item_status(status)
        if normalized_status is None:
            return {"error": "Invalid status value"}

        project_for_item: Optional[str] = None
        if normalized_type == "epic":
            epic_response = get_epic_by_id(item_id_value)
            if not epic_response.success or not isinstance(epic_response.data, dict):
                return {"error": "Epic not found or not accessible"}
            project_for_item = epic_response.data.get("project_id")
        elif normalized_type == "story":
            project_for_item = _project_for_story(item_id_value)
        else:
            project_for_item = _project_for_subtask(item_id_value)

        if not project_for_item:
            return {"error": "Item not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_item)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="update_item_status",
            title=f"Change {normalized_type} status",
            summary=f"Set {normalized_type} {item_id_value} status to '{normalized_status}'.",
            payload={
                "item_type": normalized_type,
                "item_id": item_id_value,
                "status": normalized_status,
            },
            project_id_hint=project_for_item,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_assign_user_story_tool(
        assignee: Optional[str] = None,
        assignee_id: Optional[str] = None,
        story_id: Optional[str] = None,
        story_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        assignee_value = (assignee or "").strip()
        assignee_id_value = (assignee_id or "").strip()
        if not assignee_value and not assignee_id_value:
            return {"error": "Either assignee or assignee_id is required"}

        resolved_story = _resolve_story_for_action(
            story_id_arg=story_id,
            story_query_arg=story_query,
            project_id_arg=project_id_arg,
        )
        if resolved_story.get("error"):
            return resolved_story

        story_id_value = resolved_story.get("story_id")
        project_for_story = resolved_story.get("project_id")
        if not story_id_value or not project_for_story:
            return {"error": "Story not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_story)
        if lead_error:
            return {"error": lead_error}

        assignee_descriptor = assignee_value or assignee_id_value
        action = _register_pending_action(
            action_type="assign_user_story",
            title="Assign user story",
            summary=(
                f"Assign story '{resolved_story.get('story_title') or story_id_value}' "
                f"({story_id_value}) to {assignee_descriptor}."
            ),
            payload={
                "story_id": story_id_value,
                "assignee": assignee_value or None,
                "assignee_id": assignee_id_value or None,
            },
            project_id_hint=project_for_story,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_move_story_to_epic_tool(
        target_epic_id: str,
        story_id: Optional[str] = None,
        story_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_epic_id_value = (target_epic_id or "").strip()
        if not target_epic_id_value:
            return {"error": "target_epic_id is required"}

        resolved_story = _resolve_story_for_action(
            story_id_arg=story_id,
            story_query_arg=story_query,
            project_id_arg=project_id_arg,
        )
        if resolved_story.get("error"):
            return resolved_story

        story_id_value = resolved_story.get("story_id")
        project_for_story = resolved_story.get("project_id")
        if not story_id_value or not project_for_story:
            return {"error": "Story not found or not accessible"}

        target_epic_response = get_epic_by_id(target_epic_id_value)
        if not target_epic_response.success or not isinstance(target_epic_response.data, dict):
            return {"error": "Target epic not found or not accessible"}

        target_project_id = target_epic_response.data.get("project_id")
        if target_project_id != project_for_story:
            return {"error": "Story can only be moved to an epic in the same project"}

        lead_error = _validate_lead_for_project(project_for_story)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="move_story_to_epic",
            title="Move story to epic",
            summary=(
                f"Move story '{resolved_story.get('story_title') or story_id_value}' "
                f"({story_id_value}) to epic {target_epic_id_value}."
            ),
            payload={
                "story_id": story_id_value,
                "target_epic_id": target_epic_id_value,
            },
            project_id_hint=project_for_story,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_update_user_story_tool(
        story_id: Optional[str] = None,
        story_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        project_id: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        user_story: Optional[str] = None,
        userStory: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        storyPoints: Optional[Any] = None,
        story_points: Optional[Any] = None,
        dueDate: Optional[str] = None,
        due_date: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        proposal_fields: Dict[str, Any] = {}
        if isinstance(fields, dict):
            proposal_fields.update(fields)

        direct_fields = {
            "title": title,
            "user_story": user_story if user_story is not None else userStory,
            "description": description,
            "priority": priority,
            "storyPoints": storyPoints if storyPoints is not None else story_points,
            "dueDate": dueDate if dueDate is not None else due_date,
            "status": status,
        }
        for field_key, field_value in direct_fields.items():
            if field_value is None:
                continue
            if isinstance(field_value, str):
                cleaned = field_value.strip()
                if not cleaned:
                    continue
                proposal_fields[field_key] = cleaned
            else:
                proposal_fields[field_key] = field_value

        # Recovery path for malformed tool calls where description text is passed in
        # story_query while story_id is already present.
        story_query_value = (story_query or "").strip()
        story_id_value = (story_id or "").strip()
        if (
            not proposal_fields
            and story_id_value
            and story_query_value
            and len(story_query_value.split()) >= 6
        ):
            proposal_fields["description"] = story_query_value

        if not proposal_fields:
            return {
                "error": "No story fields were provided. Use fields={...} or direct keys like description, status, title."
            }

        scoped_project_id = (project_id_arg or project_id or "").strip() or None

        resolved_story = _resolve_story_for_action(
            story_id_arg=story_id,
            story_query_arg=story_query,
            project_id_arg=scoped_project_id,
        )
        if resolved_story.get("error"):
            return resolved_story

        story_id_value = resolved_story.get("story_id")
        project_for_story = resolved_story.get("project_id")
        if not story_id_value or not project_for_story:
            return {"error": "Story not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_story)
        if lead_error:
            return {"error": lead_error}

        allowed_field_keys = {"title", "user_story", "description", "priority", "storyPoints", "dueDate", "status"}
        proposal_fields = {key: value for key, value in proposal_fields.items() if key in allowed_field_keys}
        if not proposal_fields:
            return {"error": "No supported story fields provided"}

        if "status" in proposal_fields:
            normalized_status = _normalize_item_status(proposal_fields.get("status"))
            if normalized_status is None:
                return {"error": "Invalid status value"}
            proposal_fields["status"] = normalized_status

        action = _register_pending_action(
            action_type="update_user_story",
            title="Update user story",
            summary=(
                f"Update story '{resolved_story.get('story_title') or story_id_value}' "
                f"({story_id_value}) fields: {', '.join(sorted(proposal_fields.keys()))}."
            ),
            payload={
                "story_id": story_id_value,
                "fields": proposal_fields,
            },
            project_id_hint=project_for_story,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_create_epic_tool(
        name: str,
        description: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        labels: Optional[List[str]] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        storyPoints: Optional[Any] = None,
        story_points: Optional[Any] = None,
    ) -> Dict[str, Any]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return {"error": "Project not found or not accessible"}

        lead_error = _validate_lead_for_project(resolved_project_id)
        if lead_error:
            return {"error": lead_error}

        cleaned_name = str(name or "").strip()
        if not cleaned_name:
            return {"error": "Epic name is required"}

        proposal_fields: Dict[str, Any] = {
            "name": cleaned_name,
            "description": str(description or "").strip(),
            "labels": labels if isinstance(labels, list) else [],
            "priority": str(priority or "").strip() or "Medium",
            "status": _normalize_item_status(status) or "To Do",
            "storyPoints": storyPoints if storyPoints is not None else story_points if story_points is not None else 0,
        }

        action = _register_pending_action(
            action_type="create_epic",
            title="Create epic",
            summary=f"Create epic '{cleaned_name}' in the current project.",
            payload={
                "project_id": resolved_project_id,
                "fields": proposal_fields,
            },
            project_id_hint=resolved_project_id,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_update_epic_tool(
        epic_id: Optional[str] = None,
        epic_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        proposal_fields: Dict[str, Any] = {}
        if isinstance(fields, dict):
            proposal_fields.update(fields)

        direct_fields = {
            "name": name,
            "description": description,
            "labels": labels,
            "status": status,
        }
        for field_key, field_value in direct_fields.items():
            if field_value is None:
                continue
            if isinstance(field_value, str):
                cleaned = field_value.strip()
                if not cleaned:
                    continue
                proposal_fields[field_key] = cleaned
            else:
                proposal_fields[field_key] = field_value

        if not proposal_fields:
            return {"error": "No supported epic fields provided"}

        resolved_epic = _resolve_epic_for_action(
            epic_id_arg=epic_id,
            epic_query_arg=epic_query,
            project_id_arg=(project_id_arg or "").strip() or None,
        )
        if resolved_epic.get("error"):
            return resolved_epic

        epic_id_value = str(resolved_epic.get("epic_id") or "").strip()
        project_for_epic = resolved_epic.get("project_id")
        if not epic_id_value or not project_for_epic:
            return {"error": "Epic not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_epic)
        if lead_error:
            return {"error": lead_error}

        allowed_field_keys = {"name", "description", "labels", "status"}
        proposal_fields = {
            key: value for key, value in dict(proposal_fields).items() if key in allowed_field_keys
        }
        if not proposal_fields:
            return {"error": "No supported epic fields provided"}

        if "status" in proposal_fields:
            normalized_status = _normalize_item_status(proposal_fields.get("status"))
            if normalized_status is None:
                return {"error": "Invalid status value"}
            proposal_fields["status"] = normalized_status

        action = _register_pending_action(
            action_type="update_epic",
            title="Update epic",
            summary=(
                f"Update epic '{resolved_epic.get('epic_name') or epic_id_value}' "
                f"fields: {', '.join(sorted(proposal_fields.keys()))}."
            ),
            payload={
                "epic_id": epic_id_value,
                "fields": proposal_fields,
            },
            project_id_hint=project_for_epic,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_delete_epic_tool(
        epic_id: Optional[str] = None,
        epic_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_epic = _resolve_epic_for_action(
            epic_id_arg=epic_id,
            epic_query_arg=epic_query,
            project_id_arg=(project_id_arg or "").strip() or None,
        )
        if resolved_epic.get("error"):
            return resolved_epic

        epic_id_value = str(resolved_epic.get("epic_id") or "").strip()
        project_for_epic = resolved_epic.get("project_id")
        if not epic_id_value or not project_for_epic:
            return {"error": "Epic not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_epic)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="delete_epic",
            title="Delete epic",
            summary=f"Delete epic '{resolved_epic.get('epic_name') or epic_id_value}'.",
            payload={"epic_id": epic_id_value},
            project_id_hint=project_for_epic,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_create_sprint_tool(
        name: str,
        lengthDays: Any,
        project_id_arg: Optional[str] = None,
        startDate: Optional[str] = None,
        start_date: Optional[str] = None,
        endDate: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_project_id = _resolve_project_arg(project_id_arg)
        if not resolved_project_id:
            return {"error": "Project not found or not accessible"}

        lead_error = _validate_lead_for_project(resolved_project_id)
        if lead_error:
            return {"error": lead_error}

        cleaned_name = str(name or "").strip()
        if not cleaned_name:
            return {"error": "Sprint name is required"}

        try:
            length_days = int(lengthDays)
        except (TypeError, ValueError):
            return {"error": "lengthDays must be a number"}

        action = _register_pending_action(
            action_type="create_sprint",
            title="Create sprint",
            summary=f"Create sprint '{cleaned_name}' with length {length_days} days.",
            payload={
                "project_id": resolved_project_id,
                "fields": {
                    "name": cleaned_name,
                    "lengthDays": length_days,
                    "startDate": str(startDate if startDate is not None else start_date or "").strip() or None,
                    "endDate": str(endDate if endDate is not None else end_date or "").strip() or None,
                },
            },
            project_id_hint=resolved_project_id,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_update_sprint_tool(
        sprint_id: Optional[str] = None,
        sprint_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        lengthDays: Optional[Any] = None,
        length_days: Optional[Any] = None,
        startDate: Optional[str] = None,
        start_date: Optional[str] = None,
        endDate: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        proposal_fields: Dict[str, Any] = {}
        if isinstance(fields, dict):
            proposal_fields.update(fields)

        direct_fields = {
            "name": name,
            "lengthDays": lengthDays if lengthDays is not None else length_days,
            "startDate": startDate if startDate is not None else start_date,
            "endDate": endDate if endDate is not None else end_date,
        }
        for field_key, field_value in direct_fields.items():
            if field_value is None:
                continue
            if isinstance(field_value, str):
                cleaned = field_value.strip()
                if not cleaned:
                    continue
                proposal_fields[field_key] = cleaned
            else:
                proposal_fields[field_key] = field_value

        if not proposal_fields:
            return {"error": "No supported sprint fields provided"}

        scoped_project_id = (project_id_arg or "").strip() or None
        resolved_sprint = _resolve_sprint_for_action(
            sprint_id_arg=sprint_id,
            sprint_query_arg=sprint_query,
            project_id_arg=scoped_project_id,
        )
        if resolved_sprint.get("error"):
            return resolved_sprint

        sprint_id_value = str(resolved_sprint.get("sprint_id") or "").strip()
        project_for_sprint = resolved_sprint.get("project_id")
        if not sprint_id_value or not project_for_sprint:
            return {"error": "Sprint not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_sprint)
        if lead_error:
            return {"error": lead_error}

        allowed_field_keys = {"name", "lengthDays", "startDate", "endDate"}
        proposal_fields = {
            key: value for key, value in proposal_fields.items() if key in allowed_field_keys
        }
        if not proposal_fields:
            return {"error": "No supported sprint fields provided"}

        if "lengthDays" in proposal_fields:
            try:
                proposal_fields["lengthDays"] = int(proposal_fields["lengthDays"])
            except (TypeError, ValueError):
                return {"error": "lengthDays must be a number"}

        action = _register_pending_action(
            action_type="update_sprint",
            title="Update sprint",
            summary=(
                f"Update sprint '{resolved_sprint.get('sprint_name') or sprint_id_value}' "
                f"fields: {', '.join(sorted(proposal_fields.keys()))}."
            ),
            payload={
                "project_id": project_for_sprint,
                "sprint_id": sprint_id_value,
                "fields": proposal_fields,
            },
            project_id_hint=project_for_sprint,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_delete_sprint_tool(
        sprint_id: Optional[str] = None,
        sprint_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        unassign_items: bool = True,
    ) -> Dict[str, Any]:
        scoped_project_id = (project_id_arg or "").strip() or None
        resolved_sprint = _resolve_sprint_for_action(
            sprint_id_arg=sprint_id,
            sprint_query_arg=sprint_query,
            project_id_arg=scoped_project_id,
        )
        if resolved_sprint.get("error"):
            return resolved_sprint

        sprint_id_value = str(resolved_sprint.get("sprint_id") or "").strip()
        project_for_sprint = resolved_sprint.get("project_id")
        if not sprint_id_value or not project_for_sprint:
            return {"error": "Sprint not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_sprint)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="delete_sprint",
            title="Delete sprint",
            summary=f"Delete sprint '{resolved_sprint.get('sprint_name') or sprint_id_value}'.",
            payload={
                "project_id": project_for_sprint,
                "sprint_id": sprint_id_value,
                "unassign_items": bool(unassign_items),
            },
            project_id_hint=project_for_sprint,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_create_user_story_tool(
        epic_id: Optional[str] = None,
        epic_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        user_story: Optional[str] = None,
        userStory: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        storyPoints: Optional[Any] = None,
        story_points: Optional[Any] = None,
        dueDate: Optional[str] = None,
        due_date: Optional[str] = None,
        status: Optional[str] = None,
        effortHours: Optional[Any] = None,
        effort_hours: Optional[Any] = None,
    ) -> Dict[str, Any]:
        proposal_fields: Dict[str, Any] = {}
        if isinstance(fields, dict):
            proposal_fields.update(fields)

        direct_fields = {
            "user_story": user_story if user_story is not None else userStory if userStory is not None else title,
            "description": description,
            "priority": priority,
            "storyPoints": storyPoints if storyPoints is not None else story_points,
            "dueDate": dueDate if dueDate is not None else due_date,
            "status": status,
            "effortHours": effortHours if effortHours is not None else effort_hours,
        }
        for field_key, field_value in direct_fields.items():
            if field_value is None:
                continue
            if isinstance(field_value, str):
                cleaned = field_value.strip()
                if not cleaned:
                    continue
                proposal_fields[field_key] = cleaned
            else:
                proposal_fields[field_key] = field_value

        resolved_epic = _resolve_epic_for_action(
            epic_id_arg=epic_id,
            epic_query_arg=epic_query,
            project_id_arg=(project_id_arg or "").strip() or None,
        )
        if resolved_epic.get("error"):
            return resolved_epic

        epic_id_value = str(resolved_epic.get("epic_id") or "").strip()
        project_for_epic = resolved_epic.get("project_id")
        if not epic_id_value or not project_for_epic:
            return {"error": "Epic not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_epic)
        if lead_error:
            return {"error": lead_error}

        if not str(proposal_fields.get("user_story") or "").strip():
            return {"error": "User story title is required"}
        if not str(proposal_fields.get("description") or "").strip():
            return {"error": "User story description is required"}

        if "status" in proposal_fields:
            normalized_status = _normalize_item_status(proposal_fields.get("status"))
            if normalized_status is None:
                return {"error": "Invalid status value"}
            proposal_fields["status"] = normalized_status

        action = _register_pending_action(
            action_type="create_user_story",
            title="Create user story",
            summary=(
                f"Create user story '{proposal_fields.get('user_story')}' "
                f"in epic '{resolved_epic.get('epic_name') or epic_id_value}'."
            ),
            payload={
                "epic_id": epic_id_value,
                "fields": proposal_fields,
            },
            project_id_hint=project_for_epic,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_update_subtask_tool(
        subtask_id: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        subtask_id_value = (subtask_id or "").strip()
        if not subtask_id_value:
            return {"error": "subtask_id is required"}
        if not isinstance(fields, dict) or not fields:
            return {"error": "fields are required"}

        project_for_subtask = _project_for_subtask(subtask_id_value)
        if not project_for_subtask:
            return {"error": "Subtask not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_subtask)
        if lead_error:
            return {"error": lead_error}

        allowed_field_keys = {"title", "description", "estimated_hours", "complexity", "dependencies", "status"}
        proposal_fields = {
            key: value for key, value in dict(fields).items() if key in allowed_field_keys
        }
        if not proposal_fields:
            return {"error": "No supported subtask fields provided"}

        if "status" in proposal_fields:
            normalized_status = _normalize_item_status(proposal_fields.get("status"))
            if normalized_status is None:
                return {"error": "Invalid status value"}
            proposal_fields["status"] = normalized_status

        action = _register_pending_action(
            action_type="update_subtask",
            title="Update subtask",
            summary=f"Update subtask {subtask_id_value} fields: {', '.join(sorted(proposal_fields.keys()))}.",
            payload={
                "subtask_id": subtask_id_value,
                "fields": proposal_fields,
            },
            project_id_hint=project_for_subtask,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_create_subtask_tool(
        story_id: Optional[str] = None,
        story_query: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        estimated_hours: Optional[Any] = None,
        complexity: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        proposal_fields: Dict[str, Any] = {}
        if isinstance(fields, dict):
            proposal_fields.update(fields)

        direct_fields = {
            "title": title,
            "description": description,
            "estimated_hours": estimated_hours,
            "complexity": complexity,
            "status": status,
        }
        for field_key, field_value in direct_fields.items():
            if field_value is None:
                continue
            if isinstance(field_value, str):
                cleaned = field_value.strip()
                if not cleaned:
                    continue
                proposal_fields[field_key] = cleaned
            else:
                proposal_fields[field_key] = field_value

        if not str(proposal_fields.get("title") or "").strip():
            return {"error": "Subtask title is required"}

        resolved_story = _resolve_story_for_action(
            story_id_arg=story_id,
            story_query_arg=story_query,
            project_id_arg=(project_id_arg or "").strip() or None,
        )
        if resolved_story.get("error"):
            return resolved_story

        story_id_value = str(resolved_story.get("story_id") or "").strip()
        project_for_story = resolved_story.get("project_id")
        if not story_id_value or not project_for_story:
            return {"error": "Story not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_story)
        if lead_error:
            return {"error": lead_error}

        if "status" in proposal_fields:
            normalized_status = _normalize_item_status(proposal_fields.get("status"))
            if normalized_status is None:
                return {"error": "Invalid status value"}
            proposal_fields["status"] = normalized_status

        action = _register_pending_action(
            action_type="create_subtask",
            title="Create subtask",
            summary=(
                f"Create subtask '{proposal_fields.get('title')}' "
                f"for story '{resolved_story.get('story_title') or story_id_value}'."
            ),
            payload={
                "story_id": story_id_value,
                "fields": proposal_fields,
            },
            project_id_hint=project_for_story,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_delete_subtask_tool(subtask_id: str) -> Dict[str, Any]:
        subtask_id_value = (subtask_id or "").strip()
        if not subtask_id_value:
            return {"error": "subtask_id is required"}

        project_for_subtask = _project_for_subtask(subtask_id_value)
        if not project_for_subtask:
            return {"error": "Subtask not found or not accessible"}

        lead_error = _validate_lead_for_project(project_for_subtask)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="delete_subtask",
            title="Delete subtask",
            summary=f"Delete subtask {subtask_id_value}.",
            payload={"subtask_id": subtask_id_value},
            project_id_hint=project_for_subtask,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_assign_sprint_item_tool(
        sprint_id: Optional[str] = None,
        sprint_query: Optional[str] = None,
        item_type: Optional[str] = None,
        item_id: Optional[str] = None,
        project_id_arg: Optional[str] = None,
        include_subtasks: bool = False,
    ) -> Dict[str, Any]:
        resolved_sprint = _resolve_sprint_for_action(
            sprint_id_arg=sprint_id,
            sprint_query_arg=sprint_query,
            project_id_arg=(project_id_arg or "").strip() or None,
        )
        if resolved_sprint.get("error"):
            return resolved_sprint

        project_id_value = str(resolved_sprint.get("project_id") or "").strip()
        sprint_id_value = str(resolved_sprint.get("sprint_id") or "").strip()
        item_type_value = (item_type or "").strip().lower()
        item_id_value = (item_id or "").strip()

        if not project_id_value or not sprint_id_value or not item_type_value or not item_id_value:
            return {"error": "sprint_id, item_type and item_id are required"}
        if item_type_value not in {"story", "subtask"}:
            return {"error": "item_type must be story or subtask"}

        lead_error = _validate_lead_for_project(project_id_value)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="assign_sprint_item",
            title="Assign sprint item",
            summary=f"Assign {item_type_value} {item_id_value} to sprint {sprint_id_value}.",
            payload={
                "project_id": project_id_value,
                "sprint_id": sprint_id_value,
                "item_type": item_type_value,
                "item_id": item_id_value,
                "include_subtasks": bool(include_subtasks),
            },
            project_id_hint=project_id_value,
        )
        return {"confirmation_required": True, "pending_action": action}

    def propose_unassign_sprint_item_tool(
        sprint_id: Optional[str] = None,
        sprint_query: Optional[str] = None,
        item_type: Optional[str] = None,
        item_id: Optional[str] = None,
        project_id_arg: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_sprint = _resolve_sprint_for_action(
            sprint_id_arg=sprint_id,
            sprint_query_arg=sprint_query,
            project_id_arg=(project_id_arg or "").strip() or None,
        )
        if resolved_sprint.get("error"):
            return resolved_sprint

        project_id_value = str(resolved_sprint.get("project_id") or "").strip()
        sprint_id_value = str(resolved_sprint.get("sprint_id") or "").strip()
        item_type_value = (item_type or "").strip().lower()
        item_id_value = (item_id or "").strip()

        if not project_id_value or not sprint_id_value or not item_type_value or not item_id_value:
            return {"error": "sprint_id, item_type and item_id are required"}
        if item_type_value not in {"story", "subtask"}:
            return {"error": "item_type must be story or subtask"}

        lead_error = _validate_lead_for_project(project_id_value)
        if lead_error:
            return {"error": lead_error}

        action = _register_pending_action(
            action_type="unassign_sprint_item",
            title="Unassign sprint item",
            summary=f"Unassign {item_type_value} {item_id_value} from sprint {sprint_id_value}.",
            payload={
                "project_id": project_id_value,
                "sprint_id": sprint_id_value,
                "item_type": item_type_value,
                "item_id": item_id_value,
            },
            project_id_hint=project_id_value,
        )
        return {"confirmation_required": True, "pending_action": action}

    def find_delayed_user_stories_tool(
        days_overdue: int = 14,
        project_id_arg: Optional[str] = None,
        include_done: bool = False,
        limit: int = 60,
    ) -> Dict[str, Any]:
        scope_project_id = _resolve_project_arg(project_id_arg)
        scope = _project_scope(project_id_arg)
        if project_id_arg and not scope:
            return {"error": "Project not found or not accessible"}

        now = datetime.now(timezone.utc)
        delayed: List[Dict[str, Any]] = []
        for scoped_project_id in scope:
            project = _load_project(scoped_project_id)
            if not project:
                continue
            referenced_projects.add(scoped_project_id)

            for epic in project.get("epics") or []:
                epic_name = epic.get("name")
                for story in epic.get("userStories") or []:
                    due_date = _parse_date(story.get("dueDate") or story.get("due_date"))
                    if due_date is None:
                        continue
                    status = _story_status(story)
                    if not include_done and status.lower() == "done":
                        continue
                    delta_days = int((now - due_date).total_seconds() // 86400)
                    if delta_days < int(days_overdue):
                        continue
                    delayed.append(
                        {
                            "project_id": scoped_project_id,
                            "project_name": project.get("name"),
                            "project_key": project.get("project_key"),
                            "epic_id": epic.get("id"),
                            "epic_name": epic_name,
                            "story_id": story.get("id"),
                            "title": _story_title(story),
                            "status": status,
                            "assignee": story.get("assignee"),
                            "due_date": story.get("dueDate") or story.get("due_date"),
                            "days_overdue": delta_days,
                        }
                    )

        delayed.sort(key=lambda item: item.get("days_overdue", 0), reverse=True)
        max_items = max(1, min(limit, 500))
        return {
            "count": len(delayed),
            "items": delayed[:max_items],
            "days_overdue_threshold": int(days_overdue),
            "scope_project_id": scope_project_id or focused_project_id,
        }

    def search_work_items_tool(
        query: str,
        project_id_arg: Optional[str] = None,
        limit: int = 30,
    ) -> Dict[str, Any]:
        text = (query or "").strip().lower()
        if not text:
            return {"error": "query is required"}

        scope_project_id = _resolve_project_arg(project_id_arg)
        scope = _project_scope(project_id_arg)
        if project_id_arg and not scope:
            return {"error": "Project not found or not accessible"}

        hits: List[Dict[str, Any]] = []
        for scoped_project_id in scope:
            project = _load_project(scoped_project_id)
            if not project:
                continue
            referenced_projects.add(scoped_project_id)

            project_name = project.get("name") or ""
            if text in project_name.lower():
                hits.append(
                    {
                        "type": "project",
                        "project_id": scoped_project_id,
                        "project_name": project_name,
                        "project_key": project.get("project_key"),
                        "match": project_name,
                    }
                )

            for epic in project.get("epics") or []:
                epic_name = epic.get("name") or ""
                epic_description = epic.get("description") or ""
                if text in epic_name.lower() or text in epic_description.lower():
                    hits.append(
                        {
                            "type": "epic",
                            "project_id": scoped_project_id,
                            "project_name": project_name,
                            "epic_id": epic.get("id"),
                            "epic_name": epic_name,
                            "match": epic_name if text in epic_name.lower() else epic_description[:200],
                        }
                    )

                for story in epic.get("userStories") or []:
                    story_title = _story_title(story)
                    story_description = story.get("description") or ""
                    if text in story_title.lower() or text in story_description.lower():
                        hits.append(
                            {
                                "type": "story",
                                "project_id": scoped_project_id,
                                "project_name": project_name,
                                "epic_id": epic.get("id"),
                                "epic_name": epic_name,
                                "story_id": story.get("id"),
                                "title": story_title,
                                "status": _story_status(story),
                                "match": story_title if text in story_title.lower() else story_description[:200],
                            }
                        )

                    for task in story.get("subTasks") or []:
                        task_title = task.get("title") or ""
                        task_description = task.get("description") or ""
                        if text in task_title.lower() or text in task_description.lower():
                            hits.append(
                                {
                                    "type": "subtask",
                                    "project_id": scoped_project_id,
                                    "project_name": project_name,
                                    "story_id": story.get("id"),
                                    "story_title": story_title,
                                    "subtask_id": task.get("id"),
                                    "title": task_title,
                                    "status": task.get("status"),
                                    "match": task_title if text in task_title.lower() else task_description[:200],
                                }
                            )

        max_items = max(1, min(limit, 500))
        return {
            "query": query,
            "count": len(hits),
            "hits": hits[:max_items],
            "scope_project_id": scope_project_id or focused_project_id,
        }

    tools = [
        StructuredTool.from_function(
            func=get_project_details,
            name="get_project_details",
            description="Get detailed information for the current scoped project, including metrics and epics summary.",
        ),
        StructuredTool.from_function(
            func=get_project_members_tool,
            name="get_project_members",
            description="Get team members for the current scoped project.",
        ),
        StructuredTool.from_function(
            func=get_project_epics_tool,
            name="get_project_epics",
            description="Get epics for the current scoped project, optionally including user stories.",
        ),
        StructuredTool.from_function(
            func=get_epic_user_stories_tool,
            name="get_epic_user_stories",
            description="Get user stories for an epic, optionally including subtasks.",
        ),
        StructuredTool.from_function(
            func=get_story_subtasks_tool,
            name="get_story_subtasks",
            description="Get subtasks for a specific user story.",
        ),
        StructuredTool.from_function(
            func=get_project_sprints_tool,
            name="get_project_sprints",
            description="List sprints for the current scoped project, including date range and item counts.",
        ),
        StructuredTool.from_function(
            func=get_sprint_items_tool,
            name="get_sprint_items",
            description="Get stories and subtasks assigned to a sprint.",
        ),
        StructuredTool.from_function(
            func=search_sprints_tool,
            name="search_sprints",
            description="Search sprint names within the current scoped project.",
        ),
        StructuredTool.from_function(
            func=propose_update_item_status_tool,
            name="propose_update_item_status",
            description="Propose changing status for an epic, story, or subtask. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_assign_user_story_tool,
            name="propose_assign_user_story",
            description=(
                "Propose assigning a user story to a member. "
                "Accepts story_id or story_query (story title/key/description text). "
                "This does not execute the change."
            ),
        ),
        StructuredTool.from_function(
            func=propose_move_story_to_epic_tool,
            name="propose_move_story_to_epic",
            description=(
                "Propose moving a user story to another epic in the same project. "
                "Accepts story_id or story_query (story title/key/description text). "
                "This does not execute the change."
            ),
        ),
        StructuredTool.from_function(
            func=propose_update_user_story_tool,
            name="propose_update_user_story",
            description=(
                "Propose updating user story fields. "
                "Accepts story_id or story_query (story title/key/description text). "
                "For updates, prefer fields={...}, but direct keys like description/status/title are also accepted. "
                "This does not execute the change."
            ),
        ),
        StructuredTool.from_function(
            func=propose_create_epic_tool,
            name="propose_create_epic",
            description="Propose creating a new epic in the current scoped project. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_update_epic_tool,
            name="propose_update_epic",
            description="Propose updating epic fields by epic_id or epic_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_delete_epic_tool,
            name="propose_delete_epic",
            description="Propose deleting an epic by epic_id or epic_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_create_sprint_tool,
            name="propose_create_sprint",
            description="Propose creating a sprint in the current scoped project. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_update_sprint_tool,
            name="propose_update_sprint",
            description="Propose updating a sprint by sprint_id or sprint_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_delete_sprint_tool,
            name="propose_delete_sprint",
            description="Propose deleting a sprint by sprint_id or sprint_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_create_user_story_tool,
            name="propose_create_user_story",
            description="Propose creating a user story in an epic by epic_id or epic_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_update_subtask_tool,
            name="propose_update_subtask",
            description="Propose updating subtask fields. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_create_subtask_tool,
            name="propose_create_subtask",
            description="Propose creating a subtask for a story by story_id or story_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_delete_subtask_tool,
            name="propose_delete_subtask",
            description="Propose deleting a subtask by subtask_id. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_assign_sprint_item_tool,
            name="propose_assign_sprint_item",
            description="Propose assigning a story or subtask to a sprint by sprint_id or sprint_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=propose_unassign_sprint_item_tool,
            name="propose_unassign_sprint_item",
            description="Propose removing a story or subtask from a sprint by sprint_id or sprint_query. This does not execute the change.",
        ),
        StructuredTool.from_function(
            func=find_delayed_user_stories_tool,
            name="find_delayed_user_stories",
            description="Find delayed user stories based on overdue days within the current scoped project.",
        ),
        StructuredTool.from_function(
            func=search_work_items_tool,
            name="search_work_items",
            description="Search epics, stories, and subtasks by keyword within the current scoped project.",
        ),
    ]

    tool_map = {tool.name: tool for tool in tools}
    llm = gpt40_mini_client.bind_tools(tools)

    def _assistant_node(state: AgentState) -> Dict[str, List[BaseMessage]]:
        response = llm.invoke(state["messages"])
        return {"messages": [response]}

    def _tools_node(state: AgentState) -> Dict[str, List[BaseMessage]]:
        last_message = state["messages"][-1]
        tool_messages: List[ToolMessage] = []

        if not isinstance(last_message, AIMessage):
            return {"messages": tool_messages}

        for index, tool_call in enumerate(last_message.tool_calls or []):
            tool_name = tool_call.get("name")
            tool_call_id = tool_call.get("id") or f"tool_call_{index}"
            tool_args = tool_call.get("args") or {}

            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except Exception:
                    tool_args = {}

            tool = tool_map.get(tool_name)
            if tool is None:
                result = {"error": f"Unknown tool: {tool_name}"}
            else:
                try:
                    result = tool.invoke(tool_args)
                except Exception as tool_error:  # pragma: no cover - runtime guard
                    logging.exception("Tool execution failed: %s", tool_name)
                    result = {"error": f"Tool execution failed for {tool_name}: {tool_error}"}

            tool_messages.append(
                ToolMessage(
                    content=_to_json(result),
                    tool_call_id=tool_call_id,
                )
            )

        return {"messages": tool_messages}

    def _route_after_assistant(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return "end"

    graph = StateGraph(AgentState)
    graph.add_node("assistant", _assistant_node)
    graph.add_node("tools", _tools_node)
    graph.add_edge(START, "assistant")
    graph.add_conditional_edges(
        "assistant",
        _route_after_assistant,
        {
            "tools": "tools",
            "end": END,
        },
    )
    graph.add_edge("tools", "assistant")
    app = graph.compile()

    project_catalog = [
        f"{project.get('project_key') or 'N/A'}:{project.get('name')} ({project.get('id')})"
        for project in assigned_projects
    ]
    scoped_hint = (
        f"Chat scope is locked to project {focused_project_id}. "
        "Do not access, compare, or reference any other project."
    )
    effective_language = normalize_language(language, default=get_default_llm_language())
    system_prompt = (
        "You are Product Planner Assistant. Use tool calls for factual data. "
        "Do not invent project, sprint, epic, story, member, status, or date details. "
        "For any requested modification, use propose_* tools only. "
        "Never claim a mutation was executed from chat; say confirmation is required and wait for apply. "
        "Do not ask for free-text confirmation like 'reply yes/no'. "
        "Always generate a pending action via propose_* tools so confirmation happens with the UI buttons. "
        "You can help with most planner UI operations, including creating, updating, deleting, assigning, moving, and sprint planning actions when matching tools exist. "
        "When a user references a story by title, short key, or description, pass that text as story_query instead of asking for story_id first. "
        "When a user references an epic or sprint by name, prefer epic_query or sprint_query instead of asking for raw IDs first. "
        "Only ask for clarification when tool output indicates multiple matches. "
        "If data is missing, say so explicitly. "
        "When the user asks for metrics, compute them from tool output. "
        f"Respond in {effective_language}. Keep responses concise and structured.\n"
        f"{scoped_hint}\n"
        "Current project context:\n"
        + "\n".join(project_catalog)
    )

    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
    messages.extend(_normalize_history(history))
    messages.append(HumanMessage(content=user_message))

    try:
        final_state = app.invoke({"messages": messages}, config={"recursion_limit": 14})
    except Exception as agent_error:  # pragma: no cover - runtime guard
        logging.exception("Project chat agent failed")
        return ResponseModel(
            success=False,
            message=f"Agent execution failed: {agent_error}",
            data=None,
        )

    final_messages = final_state.get("messages") or []
    final_answer = ""
    for current_message in reversed(final_messages):
        if isinstance(current_message, AIMessage) and not current_message.tool_calls:
            final_answer = _message_content_to_text(current_message.content).strip()
            if final_answer:
                break

    if not final_answer:
        final_answer = "I couldn't generate a response for that question."

    referenced = sorted(referenced_projects)
    if not referenced:
        referenced = [focused_project_id]

    return ResponseModel(
        success=True,
        message="Assistant response generated",
        data={
            "answer": final_answer,
            "language": effective_language,
            "referenced_projects": referenced,
            "pending_actions": pending_actions,
        },
    )
