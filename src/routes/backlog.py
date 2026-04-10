from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import logging
import traceback

from src.schemas.resources_request import BacklogStatusUpdateRequest
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.notifications import NotificationService
from src.utils.authz.auth import get_current_user
from src.utils.authz.users import get_user_profile
from src.utils.planning.projects import get_all_projects_for_user
from src.utils.planning.epics import get_epics_for_project
from src.utils.planning.user_stories import _maybe_send_user_story_updated_notification, get_user_stories_by_epic
from src.utils.planning.subtask_generation import get_subtasks_by_user_story
from src.utils.planning.members import get_project_members
from src.utils.planning.assignees import (
    build_member_lookup_from_members,
    assignee_matches,
    normalize_assignee_fields,
)
from src.utils.authz.permissions import get_project_access
from src.services.setup.firebase_setup import FIRESTORE_CLIENT

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_key(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(ch for ch in str(value).lower().strip() if ch.isalnum())


def _extract_field_value(fields, *keys):
    if not fields:
        return None
    normalized = {}
    for field in fields:
        field_key = field.get("key") or field.get("name")
        if not field_key:
            continue
        normalized[_normalize_key(field_key)] = field.get("value")
    for key in keys:
        candidate = normalized.get(_normalize_key(key))
        if candidate not in (None, ""):
            return candidate
    return None


def _normalize_status(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"to do", "todo"}:
        return "To Do"
    if normalized in {"in progress", "in_progress", "inprogress"}:
        return "In Progress"
    if normalized in {"in review", "in_review", "inreview"}:
        return "In Review"
    if normalized == "stopped":
        return "Stopped"
    if normalized == "done":
        return "Done"
    return None


def _status_from_message(message: str) -> int:
    text = (message or "").lower()
    if "not found" in text:
        return 404
    if "unauthorized" in text:
        return 403
    return 400


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _maybe_send_subtask_updated_notification(
    *,
    previous_subtask: Dict[str, Any],
    updated_subtask: Dict[str, Any],
    user_id: str,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        fields_to_watch = {
            "status": "Status"
        }

        changes = {}
        for db_key, display_name in fields_to_watch.items():
            old_val = str(previous_subtask.get(db_key) or "N/A").strip()
            new_val = str(updated_subtask.get(db_key) or "N/A").strip()

            if old_val != new_val and old_val.lower() != new_val.lower():
                changes[display_name] = {"old": old_val, "new": new_val}

        if not changes:
            return NotificationService()._notification_result(False, "skipped", "no_changes", "No relevant fields were changed")

        story_id = str(updated_subtask.get("user_story_id") or previous_subtask.get("user_story_id") or "").strip()
        if not story_id:
            return NotificationService()._notification_result(False, "skipped", "story_missing", "User Story ID is missing in subtask.")

        story_doc = FIRESTORE_CLIENT.collection("user_stories").document(story_id).get()
        if not story_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "story_not_found", "Parent Story not found.")
        
        story_data = story_doc.to_dict() or {}
        parent_story_title = str(story_data.get("user_story") or story_data.get("title") or "").strip()
        epic_id = str(story_data.get("epic_id") or "").strip()

        if not epic_id:
            return NotificationService()._notification_result(False, "skipped", "epic_missing", "Epic ID is missing in parent story.")

        epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
        epic_name = ""
        project_id = ""
        if epic_doc.exists:
            epic_data = epic_doc.to_dict() or {}
            epic_name = str(epic_data.get("epic") or epic_data.get("name") or "").strip()
            project_id = str(epic_data.get("project_id") or "").strip()

        if not project_id:
            return NotificationService()._notification_result(False, "skipped", "project_missing", "Project ID missing in epic.")

        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
        if not project_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "project_not_found", "Project not found")

        project_data = project_doc.to_dict() or {}
        project_name = str(project_data.get("name") or "").strip()
        leader_email = project_data.get("projectLead", "")

        leader_id = project_data.get("user_id", "")
        leader_doc = FIRESTORE_CLIENT.collection("users").document(leader_id).get()
        if not leader_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "admin_not_found", "Admin Project not found")

        # If the leader has made the change , we do not send the email
        if user_email and user_email.lower() == leader_email.lower():
                return NotificationService()._notification_result(False, "skipped", "user_is_admin", "User is the admin, no email needed")

        leader_data = leader_doc.to_dict() or {}
        leader_name = leader_data.get("name", "")

        actor_name = str(user_name or user_email or "A User").strip()
        if not actor_name or actor_name == "None":
            actor_profile = get_user_profile(user_id=user_id, email=user_email)
            actor_name = str((actor_profile or {}).get("name") or user_email or "A User").strip()

        subtask_title = str(updated_subtask.get("title") or "").strip()
        display_title = f"[Subtask] {subtask_title} (De: {parent_story_title})"

        NotificationService().try_send_subtask_updated(
            leader_email=leader_email, 
            leader_name=leader_name,               
            changer_name=actor_name,
            project_name=project_name,
            epic_name=epic_name,
            story_title=display_title,
            changes=changes
        )

        return NotificationService()._notification_result(True, "sent", "sent", f"Subtask update email sent to {leader_email}.")
    except Exception as e:
        print(f"ERROR EN NOTIFICACIÓN DE SUBTAREA: {e}")
        traceback.print_exc()
        return NotificationService()._notification_result(False, "failed", "error", "Subtask notification failed.")

@router.get(
    "/backlog",
    response_description="Get backlog items across projects",
)
async def get_backlog_route(
    assignee_email: Optional[str] = Query(None, alias="assignee_email", description="Assignee email"),
    assigneeEmail: Optional[str] = Query(None, description="Assignee email"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Retrieves epics, stories, and subtasks for all projects owned by the user.
    Optionally filters by assignee email.
    """
    resolved_email = assignee_email or assigneeEmail

    try:
        projects_response = get_all_projects_for_user(
            user_data.user_id,
            include_member_projects=True,
            user_email=user_data.get_email(),
        )
        if not projects_response.success:
            return JSONResponse(
                status_code=404,
                content=projects_response.dict(),
            )

        backlog_epics = []
        backlog_stories = []
        backlog_subtasks = []

        for project in projects_response.data or []:
            project_id = project.get("id") or project.get("project_id")
            if not project_id:
                continue

            project_name = project.get("name")
            project_key = project.get("project_key")

            access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
            if not access.success:
                continue
            is_lead = access.data.get("is_lead")

            filter_email = resolved_email
            if not is_lead:
                filter_email = user_data.get_email()

            members = project.get("teamMembers")
            if members is None:
                members = get_project_members(project_id)
            member_lookup = build_member_lookup_from_members(members)

            epics = get_epics_for_project(project_id, user_data.user_id)
            epic_name_map = {epic.get("id"): epic.get("name", "") for epic in epics}

            for epic in epics:
                normalize_assignee_fields(epic, member_lookup)
                if filter_email and not assignee_matches(epic, filter_email, member_lookup):
                    continue

                backlog_epics.append({
                    "id": epic.get("id"),
                    "epic_id": epic.get("id"),
                    "epic_name": epic.get("name"),
                    "story_id": None,
                    "story_title": None,
                    "name": epic.get("name"),
                    "description": epic.get("description"),
                    "status": epic.get("status") or "To Do",
                    "priority": epic.get("priority") or _extract_field_value(epic.get("fields"), "priority"),
                    "storyPoints": epic.get("storyPoints") or epic.get("story_points") or _extract_field_value(epic.get("fields"), "storyPoints", "story_points", "storypoints"),
                    "dueDate": epic.get("dueDate") or epic.get("due_date") or _extract_field_value(epic.get("fields"), "dueDate", "due_date", "duedate"),
                    "assignee": epic.get("assignee_email") or epic.get("assignee"),
                    "project_id": project_id,
                    "project_name": project_name,
                    "project_key": project_key,
                })

            for epic in epics:
                stories_response = get_user_stories_by_epic(epic.get("id"), user_data.user_id, allow_member=True)
                stories = stories_response.data or []
                for story in stories:
                    normalize_assignee_fields(story, member_lookup)
                    if filter_email and not assignee_matches(story, filter_email, member_lookup):
                        continue

                    fields = story.get("fields") or []
                    title = story.get("title") or _extract_field_value(fields, "title") or story.get("user_story") or story.get("user_story_id")
                    priority = story.get("priority") or _extract_field_value(fields, "priority")
                    story_points = story.get("storyPoints") or story.get("story_points") or _extract_field_value(fields, "storyPoints", "story_points", "storypoints")
                    due_date = story.get("dueDate") or story.get("due_date") or _extract_field_value(fields, "dueDate", "due_date", "duedate")
                    status = story.get("status") or _extract_field_value(fields, "status") or "To Do"
                    backlog_stories.append({
                        "id": story.get("id"),
                        "epic_id": story.get("epic_id"),
                        "epic_name": epic_name_map.get(story.get("epic_id"), ""),
                        "story_id": story.get("id"),
                        "story_title": title,
                        "title": title,
                        "user_story": story.get("user_story"),
                        "status": status,
                        "priority": priority,
                        "storyPoints": story_points,
                        "dueDate": due_date,
                        "effort_hours": story.get("effort_hours") or story.get("effortHours"),
                        "assignee": story.get("assignee_email") or story.get("assignee"),
                        "project_id": project_id,
                        "project_name": project_name,
                        "project_key": project_key,
                    })

                    story_id = story.get("id")
                    if not story_id:
                        continue
                    subtasks_response = get_subtasks_by_user_story(
                        story_id,
                        user_data.user_id,
                        allow_member=True,
                        user_email=user_data.get_email()
                    )
                    if not subtasks_response.success:
                        continue
                    for subtask in subtasks_response.data or []:
                        normalize_assignee_fields(subtask, member_lookup)
                        if filter_email and not assignee_matches(subtask, filter_email, member_lookup):
                            continue

                        backlog_subtasks.append({
                            "id": subtask.get("id"),
                            "epic_id": story.get("epic_id"),
                            "epic_name": epic_name_map.get(story.get("epic_id"), ""),
                            "story_id": story_id,
                            "story_title": title,
                            "title": subtask.get("title") or subtask.get("description"),
                            "status": subtask.get("status") or "To Do",
                            "estimated_hours": subtask.get("estimated_hours"),
                            "type": subtask.get("type") or subtask.get("complexity"),
                            "created_at": subtask.get("created_at") or subtask.get("createdDate") or subtask.get("created_date"),
                            "assignee": subtask.get("assignee_email") or subtask.get("assignee"),
                            "project_id": project_id,
                            "project_name": project_name,
                            "project_key": project_key,
                        })

        response = ResponseModel(
            success=True,
            message="Backlog loaded",
            data={
                "epics": backlog_epics,
                "stories": backlog_stories,
                "subtasks": backlog_subtasks,
            },
        )
        return JSONResponse(status_code=200, content=response.dict())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to load backlog")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/backlog/items/status",
    response_description="Update backlog item status",
)
async def update_backlog_item_status(
    req: BacklogStatusUpdateRequest,
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Updates status for an epic, story, or subtask.
    """
    item_type = req.item_type
    item_id = req.item_id
    raw_status = req.status
    status = _normalize_status(raw_status)

    if not item_type or not item_id:
        return JSONResponse(
            status_code=400,
            content=ResponseModel(
                success=False,
                message="item_type and item_id are required",
                data=None,
            ).dict(),
        )

    if raw_status is None:
        return JSONResponse(
            status_code=400,
            content=ResponseModel(
                success=False,
                message="status is required",
                data=None,
            ).dict(),
        )

    if status is None:
        return JSONResponse(
            status_code=400,
            content=ResponseModel(
                success=False,
                message="Invalid status. Expected: To Do, In Progress, In Review, Stopped, Done.",
                data=None,
            ).dict(),
        )

    normalized_type = str(item_type).strip().lower()
    if normalized_type not in {"epic", "story", "subtask"}:
        return JSONResponse(
            status_code=400,
            content=ResponseModel(
                success=False,
                message="Invalid item_type. Expected epic, story, or subtask.",
                data=None,
            ).dict(),
        )

    collection_map = {
        "epic": "epics",
        "story": "user_stories",
        "subtask": "subtasks",
    }
    collection_name = collection_map.get(normalized_type)
    item_ref = FIRESTORE_CLIENT.collection(collection_name).document(item_id)
    item_doc = item_ref.get()
    if not item_doc.exists:
        return JSONResponse(
            status_code=404,
            content=ResponseModel(success=False, message="Item not found", data=None).dict(),
        )

    item_data = item_doc.to_dict()
    project_id = None

    if normalized_type == "epic":
        project_id = item_data.get("project_id")
    elif normalized_type == "story":
        epic_id = item_data.get("epic_id")
        if epic_id:
            epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
            if epic_doc.exists:
                project_id = epic_doc.to_dict().get("project_id")
    else:
        story_id = item_data.get("user_story_id")
        if story_id:
            story_doc = FIRESTORE_CLIENT.collection("user_stories").document(story_id).get()
            if story_doc.exists:
                epic_id = story_doc.to_dict().get("epic_id")
                if epic_id:
                    epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
                    if epic_doc.exists:
                        project_id = epic_doc.to_dict().get("project_id")

    if not project_id:
        return JSONResponse(
            status_code=404,
            content=ResponseModel(success=False, message="Project not found for item", data=None).dict(),
        )

    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        return JSONResponse(
            status_code=_status_from_message(access.message),
            content=access.dict(),
        )

    if not access.data.get("is_lead"):
        members = get_project_members(project_id)
        member_lookup = build_member_lookup_from_members(members)
        normalize_assignee_fields(item_data, member_lookup)
        if not assignee_matches(item_data, user_data.get_email(), member_lookup):
            return JSONResponse(
                status_code=403,
                content=ResponseModel(
                    success=False,
                    message="Forbidden: Team members can only update assigned items",
                    data=None,
                ).dict(),
            )

    update_data = {
        "status": status,
        "updated_at": _current_timestamp_iso(),
    }
    if normalized_type == "subtask":
        if status == "Done":
            update_data["completed_date"] = _current_timestamp_iso()
        else:
            update_data["completed_date"] = None

    item_ref.update(update_data)

    updated_item_data = {**item_data, **update_data}

    try:
        if normalized_type == "story":
            _maybe_send_user_story_updated_notification(
                previous_story=item_data,
                updated_story=updated_item_data,
                user_id=user_data.get_user_id(),
                user_email=user_data.get_email(),
                user_name=user_data.get_user_name()
            )
        elif normalized_type == "subtask":
            _maybe_send_subtask_updated_notification(
                previous_subtask=item_data,
                updated_subtask=updated_item_data,
                user_id=user_data.get_user_id(),
                user_email=user_data.get_email(),
                user_name=user_data.get_user_name()
            )
    except Exception as e:
        logging.error(f"Error disparando notificación desde backlog: {e}")

    return JSONResponse(
        status_code=200,
        content=ResponseModel(
            success=True,
            message="Status updated",
            data={"updated": True},
        ).dict(),
    )

