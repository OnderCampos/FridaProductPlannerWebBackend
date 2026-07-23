from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import logging
import traceback

from src.schemas.resources_request import BacklogStatusUpdateRequest
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.schemas.workflow_status import coerce_workflow_status, normalize_workflow_status
from src.services.notifications import NotificationService
from src.utils.authz.auth import get_current_user
from src.utils.authz.users import get_user_profile
from src.utils.planning.projects import get_all_projects_for_user
from src.utils.planning.epics import get_epics_for_project
from src.utils.planning.user_stories import _maybe_send_user_story_updated_notification, get_user_stories_by_epic
from src.utils.planning.subtask_generation import _maybe_send_subtask_updated_notification
from src.utils.planning.subtask_generation import get_subtasks_by_user_story
from src.utils.planning.members import get_project_members
from src.utils.planning.assignees import (
    build_member_lookup_from_members,
    assignee_matches,
    ensure_assignee_email,
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
    return normalize_workflow_status(value)


def _status_from_message(message: str) -> int:
    text = (message or "").lower()
    if "not found" in text:
        return 404
    if "unauthorized" in text:
        return 403
    return 400


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _build_story_backlog_item(story: dict, project_id: str, project_name: str, project_key: str, epic_name: str) -> dict:
    fields = story.get("fields") or []
    title = story.get("title") or _extract_field_value(fields, "title") or story.get("user_story") or story.get("user_story_id")
    priority = story.get("priority") or _extract_field_value(fields, "priority")
    story_points = story.get("storyPoints") or story.get("story_points") or _extract_field_value(fields, "storyPoints", "story_points", "storypoints")
    due_date = story.get("dueDate") or story.get("due_date") or _extract_field_value(fields, "dueDate", "due_date", "duedate")
    status = coerce_workflow_status(
        story.get("status") or _extract_field_value(fields, "status"),
        default="To Do",
    )
    return {
        "id": story.get("id"),
        "user_story_id": story.get("user_story_id"),
        "epic_id": story.get("epic_id"),
        "epic_name": epic_name,
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
    }


def _build_subtask_backlog_item(
    subtask: dict,
    project_id: str,
    project_name: str,
    project_key: str,
    epic_name: str = "",
    story_id: Optional[str] = None,
    story_title: Optional[str] = None,
) -> dict:
    return {
        "id": subtask.get("id"),
        "task_id": subtask.get("task_id"),
        "epic_id": subtask.get("epic_id"),
        "epic_name": epic_name or subtask.get("epic_name") or "",
        "story_id": story_id,
        "story_title": story_title,
        "title": subtask.get("title") or subtask.get("description") or "Untitled task",
        "description": subtask.get("description") or "",
        "tips_markdown": subtask.get("tips_markdown"),
        "status": coerce_workflow_status(subtask.get("status"), default="To Do"),
        "estimated_hours": subtask.get("estimated_hours"),
        "complexity": subtask.get("complexity"),
        "type": subtask.get("task_type") or subtask.get("type") or subtask.get("complexity"),
        "task_type": subtask.get("task_type"),
        "created_at": subtask.get("created_at") or subtask.get("createdDate") or subtask.get("created_date"),
        "createdDate": subtask.get("createdDate") or subtask.get("created_at") or subtask.get("created_date"),
        "assignee": subtask.get("assignee_email") or subtask.get("assignee"),
        "sprint_id": subtask.get("sprint_id"),
        "project_id": project_id,
        "project_name": project_name,
        "project_key": project_key,
        "source": subtask.get("source") or "project",
    }


def _get_epic_doc_map(epic_ids: set[str]) -> dict[str, dict]:
    epic_map: dict[str, dict] = {}
    for epic_id in epic_ids:
        if not epic_id:
            continue
        epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
        if not epic_doc.exists:
            continue
        epic_data = epic_doc.to_dict() or {}
        epic_data["id"] = epic_doc.id
        epic_map[epic_doc.id] = epic_data
    return epic_map


def _unique_docs_by_id(docs) -> list:
    unique_docs = []
    seen_ids: set[str] = set()
    for doc in docs or []:
        doc_id = getattr(doc, "id", None)
        if not doc_id or doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)
        unique_docs.append(doc)
    return unique_docs


@router.get(
    "/backlog",
    response_description="Get backlog items across projects",
)
async def get_backlog_route(
    assignee_email: Optional[str] = Query(None, alias="assignee_email", description="Assignee email"),
    user_data: UserData = Depends(get_current_user),
) -> ResponseModel:
    """
    Retrieves epics, stories, and subtasks for all projects owned by the user.
    Optionally filters by assignee email.
    """
    try:
        projects_response = get_all_projects_for_user(
            user_data.user_id,
            include_member_projects=True,
            user_email=user_data.get_email(),
            include_team_members=False,
        )
        if not projects_response.success:
            return JSONResponse(
                status_code=404,
                content=projects_response.dict(),
            )

        backlog_epics = []
        backlog_stories = []
        backlog_subtasks = []
        seen_story_ids: set[str] = set()
        seen_subtask_ids: set[str] = set()
        project_scopes = {}

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

            filter_email = assignee_email
            if not is_lead:
                filter_email = user_data.get_email()

            members = project.get("teamMembers")
            if members is None:
                members = get_project_members(project_id)
            member_lookup = build_member_lookup_from_members(members)

            project_scopes[project_id] = {
                "project_id": project_id,
                "project_name": project_name,
                "project_key": project_key,
                "filter_email": str(filter_email or "").strip().lower() or None,
                "member_lookup": member_lookup,
                "is_lead": is_lead,
            }

        filtered_project_scopes = {
            project_id: scope
            for project_id, scope in project_scopes.items()
            if scope.get("filter_email")
        }

        if filtered_project_scopes:
            epic_map_by_id: dict[str, dict] = {}
            stories_by_id: dict[str, dict] = {}
            queried_story_ids = set()
            unique_filter_emails = {
                str(scope.get("filter_email") or "").strip().lower()
                for scope in filtered_project_scopes.values()
                if str(scope.get("filter_email") or "").strip()
            }

            for filter_email in unique_filter_emails:
                story_docs = list(FIRESTORE_CLIENT.collection("user_stories").where("assignee_email", "==", filter_email).get())
                legacy_story_docs = FIRESTORE_CLIENT.collection("user_stories").where("assigneeEmail", "==", filter_email).get()
                if legacy_story_docs:
                    story_docs.extend(legacy_story_docs)
                story_docs = _unique_docs_by_id(story_docs)
                candidate_stories = []
                unresolved_epic_ids = set()

                for doc in story_docs or []:
                    story = doc.to_dict() or {}
                    story["id"] = doc.id
                    epic_id = str(story.get("epic_id") or "").strip()
                    if epic_id and epic_id not in epic_map_by_id:
                        unresolved_epic_ids.add(epic_id)
                    candidate_stories.append(story)

                if unresolved_epic_ids:
                    epic_map_by_id.update(_get_epic_doc_map(unresolved_epic_ids))

                for story in candidate_stories:
                    epic_id = str(story.get("epic_id") or "").strip()
                    epic = epic_map_by_id.get(epic_id) or {}
                    project_id = str(epic.get("project_id") or "").strip()
                    if not project_id or project_id not in filtered_project_scopes:
                        continue

                    scope = filtered_project_scopes[project_id]
                    ensure_assignee_email(story, scope["member_lookup"])
                    if not assignee_matches(story, scope["filter_email"], scope["member_lookup"]):
                        continue
                    if story.get("id") in queried_story_ids or story.get("id") in seen_story_ids:
                        continue

                    queried_story_ids.add(story.get("id"))
                    seen_story_ids.add(story.get("id"))
                    stories_by_id[story.get("id")] = story
                    backlog_stories.append(
                        _build_story_backlog_item(
                            story,
                            project_id=project_id,
                            project_name=scope["project_name"],
                            project_key=scope["project_key"],
                            epic_name=str(epic.get("name") or ""),
                        )
                    )

            for project_id, scope in filtered_project_scopes.items():
                subtask_docs = list(
                    FIRESTORE_CLIENT.collection("subtasks")
                    .where("project_id", "==", project_id)
                    .where("assignee_email", "==", scope["filter_email"])
                    .get()
                )
                legacy_subtask_docs = FIRESTORE_CLIENT.collection("subtasks") \
                    .where("project_id", "==", project_id) \
                    .where("assigneeEmail", "==", scope["filter_email"]) \
                    .get()
                if legacy_subtask_docs:
                    subtask_docs.extend(legacy_subtask_docs)
                subtask_docs = _unique_docs_by_id(subtask_docs)

                missing_story_ids = set()

                for doc in subtask_docs or []:
                    subtask = doc.to_dict() or {}
                    subtask["id"] = doc.id
                    story_id = str(subtask.get("user_story_id") or "").strip()
                    if story_id and story_id not in stories_by_id:
                        missing_story_ids.add(story_id)

                for story_id in missing_story_ids:
                    story_doc = FIRESTORE_CLIENT.collection("user_stories").document(story_id).get()
                    if not story_doc.exists:
                        continue
                    story = story_doc.to_dict() or {}
                    story["id"] = story_doc.id
                    stories_by_id[story_doc.id] = story
                    epic_id = str(story.get("epic_id") or "").strip()
                    if epic_id and epic_id not in epic_map_by_id:
                        epic_map_by_id.update(_get_epic_doc_map({epic_id}))

                for doc in subtask_docs or []:
                    subtask = doc.to_dict() or {}
                    subtask["id"] = doc.id
                    if subtask.get("id") in seen_subtask_ids:
                        continue
                    ensure_assignee_email(subtask, scope["member_lookup"])
                    if not assignee_matches(subtask, scope["filter_email"], scope["member_lookup"]):
                        continue

                    story_id = str(subtask.get("user_story_id") or "").strip() or None
                    story = stories_by_id.get(story_id or "") or {}
                    epic_id = str(
                        subtask.get("epic_id")
                        or story.get("epic_id")
                        or ""
                    ).strip()
                    epic = epic_map_by_id.get(epic_id) or {}
                    if epic and str(epic.get("project_id") or "").strip() != project_id:
                        continue

                    fields = story.get("fields") or []
                    story_title = (
                        story.get("title")
                        or _extract_field_value(fields, "title")
                        or story.get("user_story")
                        or story.get("user_story_id")
                    ) if story_id else None

                    backlog_subtasks.append(
                        _build_subtask_backlog_item(
                            subtask,
                            project_id=project_id,
                            project_name=scope["project_name"],
                            project_key=scope["project_key"],
                            epic_name=str(epic.get("name") or subtask.get("epic_name") or ""),
                            story_id=story_id,
                            story_title=story_title,
                        )
                    )
                    seen_subtask_ids.add(subtask.get("id"))

        for project_id, scope in project_scopes.items():
            if scope.get("filter_email"):
                continue

            project_name = scope["project_name"]
            project_key = scope["project_key"]
            member_lookup = scope["member_lookup"]

            epics = get_epics_for_project(project_id, user_data.user_id)
            epic_name_map = {epic.get("id"): epic.get("name", "") for epic in epics}


            for epic in epics:
                stories_response = get_user_stories_by_epic(epic.get("id"), user_data.user_id, allow_member=True)
                stories = stories_response.data or []
                for story in stories:
                    ensure_assignee_email(story, member_lookup)
                    if story.get("id") in seen_story_ids:
                        continue
                    seen_story_ids.add(story.get("id"))
                    backlog_stories.append(
                        _build_story_backlog_item(
                            story,
                            project_id=project_id,
                            project_name=project_name,
                            project_key=project_key,
                            epic_name=epic_name_map.get(story.get("epic_id"), ""),
                        )
                    )

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
                        ensure_assignee_email(subtask, member_lookup)
                        if subtask.get("id") in seen_subtask_ids:
                            continue
                        fields = story.get("fields") or []
                        story_title = story.get("title") or _extract_field_value(fields, "title") or story.get("user_story") or story.get("user_story_id")
                        backlog_subtasks.append(
                            _build_subtask_backlog_item(
                                subtask,
                                project_id=project_id,
                                project_name=project_name,
                                project_key=project_key,
                                epic_name=epic_name_map.get(story.get("epic_id"), ""),
                                story_id=story_id,
                                story_title=story_title,
                            )
                        )
                        seen_subtask_ids.add(subtask.get("id"))

            standalone_subtasks_docs = FIRESTORE_CLIENT.collection("subtasks").where(
                "project_id", "==", project_id
            ).get()
            for doc in standalone_subtasks_docs:
                subtask = doc.to_dict() or {}
                if str(subtask.get("user_story_id") or "").strip():
                    continue

                subtask["id"] = doc.id
                if subtask.get("id") in seen_subtask_ids:
                    continue
                ensure_assignee_email(subtask, member_lookup)
                backlog_subtasks.append(
                    _build_subtask_backlog_item(
                        subtask,
                        project_id=project_id,
                        project_name=project_name,
                        project_key=project_key,
                    )
                )
                seen_subtask_ids.add(subtask.get("id"))

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
        project_id = item_data.get("project_id")
        story_id = item_data.get("user_story_id")
        if not project_id and story_id:
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
        ensure_assignee_email(item_data, member_lookup)
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
    if normalized_type == "story":
        current_status = coerce_workflow_status(item_data.get("status"), default="To Do")
        is_moving_to_started = status != "To Do" and current_status == "To Do"
        has_start_date = bool(item_data.get("startDate") or item_data.get("start_date"))
        if is_moving_to_started and not has_start_date:
            # Keep Backlog transitions consistent with the User Story details modal
            # and give the sprint timeline a real start point.
            update_data["startDate"] = _current_timestamp_iso()
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

