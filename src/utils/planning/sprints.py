from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import logging

from src.services.notifications import NotificationService
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
from src.schemas.workflow_status import coerce_workflow_status
from src.utils.authz.users import get_user_profile
from src.utils.planning.projects import get_project_for_user
from src.utils.authz.permissions import get_project_access
from src.utils.planning.user_stories import get_user_story_by_id, get_user_stories_by_epic
from src.utils.planning.subtask_generation import get_subtasks_by_user_story
from src.utils.planning.epics import get_epics_for_project, get_epic_by_id


SPRINTS_COLLECTION = "sprints"
SPRINT_ITEMS_COLLECTION = "sprint_items"


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_sprint_payload(sprint_data: Dict[str, Any]) -> Dict[str, Any]:
    if "lengthDays" not in sprint_data and "length_days" in sprint_data:
        sprint_data["lengthDays"] = sprint_data.get("length_days")
    return sprint_data

def _get_project_or_error(project_id: str, user_id: str, allow_members: bool = False, user_email: Optional[str] = None) -> ResponseModel:
    if allow_members:
        access = get_project_access(project_id, user_id, user_email)
        if not access.success:
            return ResponseModel(success=False, message=access.message, data=None)
        project_data = access.data.get("project") if isinstance(access.data, dict) else access.data
        return ResponseModel(success=True, message="Project access granted", data=project_data)
    return get_project_for_user(project_id, user_id)

def _maybe_sprint_assignment_notification(
    *,
    project_id: str,
    item_type: str,
    item_id: str,
    old_sprint_id: Optional[str] = None,
    new_sprint_id: Optional[str] = None,
    user_id: str,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        if old_sprint_id == new_sprint_id:
            return NotificationService()._notification_result(False, "skipped", "sprint_unchanged", "Sprint assignment did not change.")

        old_sprint_name = "Backlog"
        if old_sprint_id:
            old_sprint_doc = FIRESTORE_CLIENT.collection("sprints").document(old_sprint_id).get()
            old_sprint_name = old_sprint_doc.to_dict().get("name", "Sprint") if old_sprint_doc.exists else "Backlog"

        new_sprint_name = "Backlog"
        if new_sprint_id:
            new_sprint_doc = FIRESTORE_CLIENT.collection("sprints").document(new_sprint_id).get()
            new_sprint_name = new_sprint_doc.to_dict().get("name", "Sprint") if new_sprint_doc.exists else "Backlog"

        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
        if not project_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "project_missing", "Project not found")

        project_data = project_doc.to_dict() or {}
        project_name = project_data.get("name" or "").strip()
        leader_email = project_data.get("projectLead", "")

        leader_id = project_data.get("user_id", "")
        leader_doc = FIRESTORE_CLIENT.collection("users").document(leader_id).get()
        if not leader_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "admin_not_found", "Admin Project not found")

        leader_data = leader_doc.to_dict() or {}
        leader_name = leader_data.get("name", "")

        # If the leader has made the change , we do not send the email
        # if user_email and user_email.lower() == leader_email.lower():
        #     return NotificationService()._notification_result(False, "skipped", "user_is_admin", "User is the admin, no email needed")

        collection_name = "user_stories" if item_type.lower() == "story" else "subtasks"
        item_doc = FIRESTORE_CLIENT.collection(collection_name).document(item_id).get()

        item_title = "Unknown Item"
        epic_name = "N/A"

        if item_doc.exists:
            item_data = item_doc.to_dict()
            item_title = str(item_data.get("user_story") or "").strip()

            # If it is a story, we get its epic_id
            epic_id = item_data.get("epic_id")
            if epic_id:
                epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
                
                if epic_doc.exists:
                    epic_data = epic_doc.to_dict() or {}
                    epic_name = str(epic_data.get("name") or "").strip()

        user_story_item = ""
        if item_type.lower() == "story":
            user_story_item = "User Story"

        display_title = f"[{user_story_item.capitalize()}] {item_title}"

        actor_name = str(user_name or user_email or "A User").strip()
        if not actor_name or actor_name == "None":
            actor_profile = get_user_profile(user_id=user_id, email=user_email)
            actor_name = str(
                (actor_profile or {}).get("name")
                or (actor_profile or {}).get("email")
                or user_email
                or user_id
                or "A FridaPlatform administrator"
            ).strip()

        sent = NotificationService().try_send_sprint_assignment(
            leader_email=leader_email, 
            leader_name=leader_name,               
            changer_name=actor_name,
            project_name=project_name,
            epic_name=epic_name,
            item_title=display_title,     
            old_sprint_name=old_sprint_name,
            new_sprint_name=new_sprint_name
        )

        if not sent:
            return NotificationService()._notification_result(
                False,
                "failed",
                "notification_provider_failed",
                "Sprint assignment email failed."
            )

        return NotificationService()._notification_result(
            True, 
            "sent", 
            "sent", 
            f"Sprint assignment email sent to {leader_email}."
        )
    except Exception as e:
        import traceback
        print(f"🔥 ERROR EN NOTIFICACIÓN DE SPRINT: {e}")
        traceback.print_exc()

        return NotificationService()._notification_result(
            False,
            "failed",
            "notification_provider_failed",
            "Sprint assignment email failed."
        )

def _get_sprint_doc(sprint_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    sprint_ref = FIRESTORE_CLIENT.collection(SPRINTS_COLLECTION).document(sprint_id)
    sprint_doc = sprint_ref.get()
    if not sprint_doc.exists:
        return None, None
    sprint_data = sprint_doc.to_dict()
    sprint_data["id"] = sprint_doc.id
    _normalize_sprint_payload(sprint_data)
    return sprint_data, sprint_ref


def _get_subtask_by_id(subtask_id: str, user_id: str, allow_member: bool = False, user_email: Optional[str] = None) -> ResponseModel:
    try:
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()
        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        subtask_data = subtask_doc.to_dict()
        subtask_data["id"] = subtask_doc.id

        if subtask_data.get("user_id") != user_id and not allow_member:
            return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)
        if subtask_data.get("user_id") != user_id and allow_member:
            project_id = subtask_data.get("project_id")
            if project_id:
                access = get_project_access(project_id, user_id, user_email)
                if not access.success:
                    return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)
            else:
                story_id = subtask_data.get("user_story_id")
                if not story_id:
                    return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)
                story_response = get_user_story_by_id(story_id, user_id, allow_member=True, user_email=user_email)
                if not story_response.success:
                    return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        return ResponseModel(success=True, message="Subtask retrieved successfully", data=subtask_data)
    except Exception as e:
        logging.error(f"Error retrieving subtask {subtask_id}: {e}")
        return ResponseModel(success=False, message=f"Error retrieving subtask: {str(e)}", data=None)


def _get_story_for_project(story_id: str, project_id: str, user_id: str, allow_member: bool = False, user_email: Optional[str] = None) -> ResponseModel:
    story_response = get_user_story_by_id(story_id, user_id, allow_member=allow_member, user_email=user_email)
    if not story_response.success:
        return story_response

    story = story_response.data
    epic_id = story.get("epic_id")
    if not epic_id:
        return ResponseModel(success=False, message="User story missing epic association", data=None)

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        return ResponseModel(success=False, message="Epic not found for user story", data=None)

    epic = epic_response.data
    if epic.get("project_id") != project_id:
        return ResponseModel(success=False, message="User story does not belong to this project", data=None)

    return ResponseModel(success=True, message="User story retrieved successfully", data=story)


def _get_subtask_for_project(subtask_id: str, project_id: str, user_id: str, allow_member: bool = False, user_email: Optional[str] = None) -> ResponseModel:
    subtask_response = _get_subtask_by_id(subtask_id, user_id, allow_member=allow_member, user_email=user_email)
    if not subtask_response.success:
        return subtask_response

    subtask = subtask_response.data
    direct_project_id = subtask.get("project_id")
    if direct_project_id and direct_project_id == project_id:
        return ResponseModel(success=True, message="Subtask retrieved successfully", data=subtask)

    story_id = subtask.get("user_story_id")
    if not story_id:
        return ResponseModel(success=False, message="Subtask missing user story association", data=None)

    story_response = _get_story_for_project(story_id, project_id, user_id, allow_member=allow_member, user_email=user_email)
    if not story_response.success:
        return ResponseModel(success=False, message=story_response.message, data=None)

    return ResponseModel(success=True, message="Subtask retrieved successfully", data=subtask)


def get_sprints_for_project(
    project_id: str,
    user_id: str,
    include_counts: bool = True,
    allow_members: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id, allow_members=allow_members, user_email=user_email)
        if not project_response.success:
            return project_response

        sprint_docs = FIRESTORE_CLIENT.collection(SPRINTS_COLLECTION).where(
            "project_id", "==", project_id
        ).get()

        sprints: List[Dict[str, Any]] = []
        for doc in sprint_docs:
            sprint_data = doc.to_dict()
            sprint_data["id"] = doc.id
            _normalize_sprint_payload(sprint_data)
            sprints.append(sprint_data)

        sprints.sort(key=lambda x: x.get("order", 0))

        if include_counts:
            counts: Dict[str, int] = {}
            assignments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
                "project_id", "==", project_id
            ).get()
            for doc in assignments:
                assignment = doc.to_dict()
                sprint_id = assignment.get("sprint_id")
                if not sprint_id:
                    continue
                counts[sprint_id] = counts.get(sprint_id, 0) + 1

            for sprint in sprints:
                sprint["itemsCount"] = counts.get(sprint.get("id"), 0)

        sprint_payload: List[Dict[str, Any]] = []
        for sprint in sprints:
            payload = {
                "id": sprint.get("id"),
                "name": sprint.get("name"),
                "lengthDays": sprint.get("lengthDays"),
                "startDate": sprint.get("startDate"),
                "endDate": sprint.get("endDate"),
            }
            if include_counts:
                payload["itemsCount"] = sprint.get("itemsCount", 0)
            sprint_payload.append(payload)

        return ResponseModel(
            success=True,
            message="Sprints retrieved successfully",
            data=sprint_payload
        )
    except Exception as e:
        logging.error(f"Error retrieving sprints for project {project_id}: {e}")
        return ResponseModel(success=False, message=f"Error retrieving sprints: {str(e)}", data=None)


def create_sprint(
    project_id: str,
    user_id: str,
    name: str,
    length_days: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        if not name:
            return ResponseModel(success=False, message="name is required", data=None)
        if length_days is None:
            return ResponseModel(success=False, message="lengthDays is required", data=None)
        if length_days <= 0:
            return ResponseModel(success=False, message="lengthDays must be greater than 0", data=None)

        existing = FIRESTORE_CLIENT.collection(SPRINTS_COLLECTION).where(
            "project_id", "==", project_id
        ).get()
        max_order = 0
        for doc in existing:
            data = doc.to_dict()
            max_order = max(max_order, data.get("order", 0))

        now = _current_timestamp_iso()
        sprint_data = {
            "project_id": project_id,
            "user_id": user_id,
            "name": name,
            "lengthDays": length_days,
            "startDate": start_date,
            "endDate": end_date,
            "order": max_order + 1,
            "created_at": now,
            "updated_at": now,
        }

        doc_ref = FIRESTORE_CLIENT.collection(SPRINTS_COLLECTION).add(sprint_data)
        sprint_data["id"] = doc_ref[1].id

        return ResponseModel(success=True, message="Sprint created successfully", data=sprint_data)
    except Exception as e:
        logging.error(f"Error creating sprint for project {project_id}: {e}")
        return ResponseModel(success=False, message=f"Error creating sprint: {str(e)}", data=None)


def update_sprint(
    sprint_id: str,
    project_id: str,
    user_id: str,
    name: Optional[str] = None,
    length_days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        sprint_data, sprint_ref = _get_sprint_doc(sprint_id)
        if not sprint_data or not sprint_ref:
            return ResponseModel(success=False, message="Sprint not found", data=None)

        if sprint_data.get("project_id") != project_id:
            return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
        if sprint_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)
        if length_days is not None and length_days <= 0:
            return ResponseModel(success=False, message="lengthDays must be greater than 0", data=None)

        update_data: Dict[str, Any] = {"updated_at": _current_timestamp_iso()}
        if name is not None:
            update_data["name"] = name
        if length_days is not None:
            update_data["lengthDays"] = length_days
        if start_date is not None:
            update_data["startDate"] = start_date
        if end_date is not None:
            update_data["endDate"] = end_date

        if len(update_data) == 1:
            return ResponseModel(success=False, message="No fields to update", data=None)

        sprint_ref.update(update_data)

        updated = sprint_ref.get().to_dict()
        updated["id"] = sprint_id
        _normalize_sprint_payload(updated)

        return ResponseModel(success=True, message="Sprint updated successfully", data=updated)
    except Exception as e:
        logging.error(f"Error updating sprint {sprint_id}: {e}")
        return ResponseModel(success=False, message=f"Error updating sprint: {str(e)}", data=None)


def delete_sprint(
    sprint_id: str,
    project_id: str,
    user_id: str,
    unassign_items: bool = True,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        sprint_data, sprint_ref = _get_sprint_doc(sprint_id)
        if not sprint_data or not sprint_ref:
            return ResponseModel(success=False, message="Sprint not found", data=None)

        if sprint_data.get("project_id") != project_id:
            return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
        if sprint_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)

        deleted_assignments = 0
        if unassign_items:
            assignments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
                "sprint_id", "==", sprint_id
            ).get()
            for doc in assignments:
                doc.reference.delete()
                deleted_assignments += 1

        sprint_ref.delete()

        return ResponseModel(
            success=True,
            message="Sprint deleted successfully",
            data={"unassigned_count": deleted_assignments} if unassign_items else None,
        )
    except Exception as e:
        logging.error(f"Error deleting sprint {sprint_id}: {e}")
        return ResponseModel(success=False, message=f"Error deleting sprint: {str(e)}", data=None)


# def assign_item_to_sprint(
#     sprint_id: str,
#     project_id: str,
#     user_id: str,
#     item_type: str,
#     item_id: str,
# ) -> ResponseModel:
#     try:
#         project_response = _get_project_or_error(project_id, user_id)
#         if not project_response.success:
#             return project_response

#         sprint_data, _ = _get_sprint_doc(sprint_id)
#         if not sprint_data:
#             return ResponseModel(success=False, message="Sprint not found", data=None)
#         if sprint_data.get("project_id") != project_id:
#             return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
#         if sprint_data.get("user_id") != user_id:
#             return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)

#         normalized_type = (item_type or "").strip().lower()
#         if normalized_type not in {"story", "subtask"}:
#             return ResponseModel(success=False, message="Invalid item type", data=None)

#         if normalized_type == "story":
#             story_response = _get_story_for_project(item_id, project_id, user_id)
#             if not story_response.success:
#                 return ResponseModel(success=False, message=story_response.message, data=None)
#         else:
#             subtask_response = _get_subtask_for_project(item_id, project_id, user_id)
#             if not subtask_response.success:
#                 return ResponseModel(success=False, message=subtask_response.message, data=None)

#         existing_assignment = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
#             "item_type", "==", normalized_type
#         ).where(
#             "item_id", "==", item_id
#         ).get()
#         if existing_assignment:
#             return ResponseModel(success=False, message="Item is already assigned to a sprint", data=None)

#         current_items = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
#             "sprint_id", "==", sprint_id
#         ).get()
#         max_order = 0
#         for doc in current_items:
#             data = doc.to_dict()
#             max_order = max(max_order, data.get("order", 0))

#         now = _current_timestamp_iso()
#         assignment_data = {
#             "project_id": project_id,
#             "sprint_id": sprint_id,
#             "user_id": user_id,
#             "item_type": normalized_type,
#             "item_id": item_id,
#             "order": max_order + 1,
#             "created_at": now,
#             "updated_at": now,
#         }

#         doc_ref = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).add(assignment_data)
#         assignment_data["id"] = doc_ref[1].id

#         return ResponseModel(success=True, message="Item assigned successfully", data=assignment_data)
#     except Exception as e:
#         logging.error(f"Error assigning item to sprint {sprint_id}: {e}")
#         return ResponseModel(success=False, message=f"Error assigning item: {str(e)}", data=None)

def assign_item_to_sprint(
    sprint_id: str,
    project_id: str,
    user_id: str,
    user_email: str,
    user_name: str,
    item_type: str,
    item_id: str,
    include_subtasks: bool = False,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        sprint_data, _ = _get_sprint_doc(sprint_id)
        if not sprint_data:
            return ResponseModel(success=False, message="Sprint not found", data=None)
        if sprint_data.get("project_id") != project_id:
            return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
        if sprint_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)

        normalized_type = (item_type or "").strip().lower()

        # Handle of the story or subtask 
        if normalized_type == "story":
            story_res = _get_story_for_project(item_id, project_id, user_id)
            if not story_res.success: return story_res
        else:
            subtask_res = _get_subtask_for_project(item_id, project_id, user_id)
            if not subtask_res.success: return subtask_res

        # Reassign (If already exists in another sprint, move it)
        existing_assigments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
            'project_id', '==', project_id
        ).where(
            'item_type', '==', normalized_type
        ).where(
            'item_id', '==', item_id
        ).get()

        old_sprint_id = None
        for doc in existing_assigments:
            old_sprint_id = doc.to_dict().get("sprint_id")
            doc.reference.delete()

        # Calculate order and create new assign
        current_items = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
            'sprint_id', '==', sprint_id
        ).get()
        max_order = max([doc.to_dict().get("order", 0) for doc in current_items], default=0)

        now = _current_timestamp_iso()

        assignment_data = {
            "project_id": project_id,
            "sprint_id": sprint_id,
            "user_id": user_id,
            "item_type": normalized_type,
            "item_id": item_id,
            "order": max_order + 1,
            "created_at": now,
            "updated_at": now,
        }

        doc_ref = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).add(assignment_data)
        # doc_ref[1] is the reference of the documento created
        assignment_data["id"] = doc_ref[1].id

        if normalized_type == "story" and include_subtasks:
            # Search all subtasks of the story
            subtask_res = get_subtasks_by_user_story(item_id, user_id)

            # LOG DE DEPURACIÓN
            #print(f"DEBUG: Story {item_id} - Encontradas {len(subtask_res.data) if subtask_res.data else 0} subtareas")

            if subtask_res.success and subtask_res.data:
                for subtask in subtask_res.data:
                    # Llamada recursiva o directa para asignar cada subtarea al mismo sprint
                    # Usamos recursividad simple para mantener la lógica de 'order'
                    assign_item_to_sprint(
                        sprint_id, 
                        project_id, 
                        user_id, 
                        user_email, 
                        user_name,
                        "subtask", 
                        subtask["id"], include_subtasks=False
                    )

        assigment_sprint_notification = _maybe_sprint_assignment_notification(
            project_id=project_id,
            item_type=normalized_type,
            item_id=item_id,
            old_sprint_id=old_sprint_id,
            new_sprint_id=sprint_id,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name
        )
        assignment_data["assigment_sprint_notification"] = assigment_sprint_notification

        return ResponseModel(
            success=True,
            message="Item assigned successfully",
            data=assignment_data
        )
    except Exception as e:
        logging.error(f"Error assigning item to sprint {sprint_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error assigning item: {str(e)}",
            data=None
        )

def unassign_item_from_sprint(
    sprint_id: str,
    project_id: str,
    user_id: str,
    user_email: str,
    user_name: str,
    item_type: str,
    item_id: str,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        sprint_data, _ = _get_sprint_doc(sprint_id)
        if not sprint_data:
            return ResponseModel(success=False, message="Sprint not found", data=None)
        if sprint_data.get("project_id") != project_id:
            return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
        if sprint_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)

        normalized_type = (item_type or "").strip().lower()
        if normalized_type not in {"story", "subtask"}:
            return ResponseModel(success=False, message="Invalid item type", data=None)

        assignments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
            "sprint_id", "==", sprint_id
        ).where(
            "item_type", "==", normalized_type
        ).where(
            "item_id", "==", item_id
        ).get()

        if not assignments:
            return ResponseModel(success=False, message="Assignment not found", data=None)

        deleted = 0
        for doc in assignments:
            doc.reference.delete()
            deleted += 1

        unassign_item_notification = _maybe_sprint_assignment_notification(
            project_id=project_id,
            item_type=normalized_type,
            item_id=item_id,
            old_sprint_id=sprint_id,  
            new_sprint_id=None,       
            user_id=user_id,
            user_email=user_email,
            user_name=user_name
        )

        return ResponseModel(
            success=True,
            message="Item unassigned successfully",
            data={"deleted_count": deleted, "unassign_item_notification": unassign_item_notification},
        )
    except Exception as e:
        logging.error(f"Error unassigning item from sprint {sprint_id}: {e}")
        return ResponseModel(success=False, message=f"Error unassigning item: {str(e)}", data=None)

def get_sprint_items(
    sprint_id: str,
    project_id: str,
    user_id: str,
    allow_members: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id, allow_members=allow_members, user_email=user_email)
        if not project_response.success:
            return project_response

        sprint_data, _ = _get_sprint_doc(sprint_id)
        if not sprint_data:
            return ResponseModel(success=False, message="Sprint not found", data=None)
        if sprint_data.get("project_id") != project_id:
            return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
        if sprint_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)

        assignments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
            "sprint_id", "==", sprint_id
        ).get()

        items_with_order: List[Tuple[int, Dict[str, Any]]] = []
        story_cache: Dict[str, Dict[str, Any]] = {}
        epic_cache: Dict[str, str] = {}

        for doc in assignments:
            assignment = doc.to_dict()
            item_type = assignment.get("item_type")
            item_id = assignment.get("item_id")
            item_order = assignment.get("order", 0)
            if not item_type or not item_id:
                continue

            if item_type == "story":
                if item_id not in story_cache:
                    story_response = _get_story_for_project(item_id, project_id, user_id, allow_member=allow_members, user_email=user_email)
                    if not story_response.success:
                        continue
                    story_cache[item_id] = story_response.data

                story = story_cache[item_id]
                epic_id = story.get("epic_id")
                if epic_id and epic_id not in epic_cache:
                    epic_response = get_epic_by_id(epic_id)
                    if epic_response.success:
                        epic_cache[epic_id] = epic_response.data.get("name", "")
                title = story.get("user_story") or story.get("user_story_id") or ""
                items_with_order.append((item_order, {
                    "type": "story",
                    "id": item_id,
                    "item_id": item_id,
                    "title": title,
                    "user_story_id": story.get("user_story_id"),
                    "epicName": epic_cache.get(epic_id, "") if epic_id else "",
                    "source": "story",
                    "storyId": item_id,
                    "story_id": item_id,
                    "status": coerce_workflow_status(story.get("status"), default="To Do"),
                    "assignee": story.get("assignee"),
                    "startDate": story.get("startDate"),
                    "createdDate": story.get("createdDate"),
                    "created_at": story.get("created_at"),
                    "effortHours": story.get("effortHours"),
                    "effort_hours": story.get("effort_hours"),
                }))
            elif item_type == "subtask":
                subtask_response = _get_subtask_for_project(item_id, project_id, user_id, allow_member=allow_members, user_email=user_email)
                if not subtask_response.success:
                    continue
                subtask = subtask_response.data
                story_id = subtask.get("user_story_id")
                story_title = ""
                if story_id:
                    if story_id not in story_cache:
                        story_response = _get_story_for_project(story_id, project_id, user_id, allow_member=allow_members, user_email=user_email)
                        if story_response.success:
                            story_cache[story_id] = story_response.data
                    story = story_cache.get(story_id)
                    if story:
                        story_title = story.get("user_story") or story.get("user_story_id") or ""

                items_with_order.append((item_order, {
                    "type": "subtask",
                    "id": item_id,
                    "item_id": item_id,
                    "title": subtask.get("title", ""),
                    "task_id": subtask.get("task_id"),
                    "storyTitle": story_title,
                    "storyId": story_id,
                    "story_id": story_id,
                    "source": "story" if story_id else "project",
                    "taskType": subtask.get("task_type") or subtask.get("type"),
                    "task_type": subtask.get("task_type") or subtask.get("type"),
                    "status": coerce_workflow_status(subtask.get("status"), default="To Do"),
                    "assignee": subtask.get("assignee"),
                    "createdDate": subtask.get("createdDate"),
                    "created_at": subtask.get("created_at"),
                    "estimated_hours": subtask.get("estimated_hours"),
                }))

        items_with_order.sort(key=lambda pair: pair[0])
        items = [item for _, item in items_with_order]
        return ResponseModel(success=True, message="Sprint items retrieved successfully", data=items)
    except Exception as e:
        logging.error(f"Error retrieving sprint items for sprint {sprint_id}: {e}")
        return ResponseModel(success=False, message=f"Error retrieving sprint items: {str(e)}", data=None)


def list_available_items(
    project_id: str,
    user_id: str,
    search: Optional[str] = None,
    types: Optional[List[str]] = None,
    epic_id: Optional[str] = None,
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        epics = get_epics_for_project(project_id, user_id)
        epic_name_map: Dict[str, str] = {
            epic.get("id"): epic.get("name", "") for epic in epics
        }

        stories: List[Dict[str, Any]] = []
        for epic in epics:
            stories_response = get_user_stories_by_epic(epic.get("id"), user_id)
            if not stories_response.success:
                continue
            for story in stories_response.data or []:
                stories.append(story)

        story_map: Dict[str, Dict[str, Any]] = {story.get("id"): story for story in stories if story.get("id")}

        subtasks: List[Dict[str, Any]] = []
        for story in stories:
            story_id = story.get("id")
            if not story_id:
                continue
            subtasks_response = get_subtasks_by_user_story(story_id, user_id)
            if subtasks_response.success:
                for subtask in subtasks_response.data or []:
                    subtasks.append(subtask)

        standalone_subtasks_docs = FIRESTORE_CLIENT.collection("subtasks").where(
            "project_id", "==", project_id
        ).get()
        seen_subtask_ids = {str(subtask.get("id") or "") for subtask in subtasks}
        for doc in standalone_subtasks_docs:
            subtask_data = doc.to_dict()
            if doc.id in seen_subtask_ids:
                continue
            if str(subtask_data.get("user_story_id") or "").strip():
                continue
            subtask_data["id"] = doc.id
            subtasks.append(subtask_data)

        assignments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
            "project_id", "==", project_id
        ).get()
        assigned_keys = {
            f"{doc.to_dict().get('item_type')}:{doc.to_dict().get('item_id')}"
            for doc in assignments
        }

        search_term = (search or "").strip().lower()
        normalized_types = {t.strip().lower() for t in (types or []) if t}
        if not normalized_types:
            normalized_types = {"story", "subtask"}

        available_items: List[Dict[str, Any]] = []

        if "story" in normalized_types:
            for story in stories:
                story_id = story.get("id")
                if not story_id:
                    continue
                if f"story:{story_id}" in assigned_keys:
                    continue
                if epic_id and story.get("epic_id") != epic_id:
                    continue
                title = story.get("user_story") or story.get("user_story_id") or ""
                if search_term and search_term not in title.lower():
                    continue
                available_items.append({
                    "type": "story",
                    "id": story_id,
                    "item_id": story_id,
                    "title": title,
                    "user_story_id": story.get("user_story_id"),
                    "epicName": epic_name_map.get(story.get("epic_id"), ""),
                    "source": "story",
                    "storyId": story_id,
                    "story_id": story_id,
                    "status": coerce_workflow_status(story.get("status"), default="To Do"),
                    "assignee": story.get("assignee"),
                    "startDate": story.get("startDate"),
                    "createdDate": story.get("createdDate"),
                    "created_at": story.get("created_at"),
                    "effortHours": story.get("effortHours"),
                    "effort_hours": story.get("effort_hours"),
                })

        if "subtask" in normalized_types:
            for subtask in subtasks:
                subtask_id = subtask.get("id")
                if not subtask_id:
                    continue
                if f"subtask:{subtask_id}" in assigned_keys:
                    continue
                story_id = subtask.get("user_story_id")
                story = story_map.get(story_id) if story_id else None
                title = subtask.get("title") or subtask.get("description") or ""
                if search_term and search_term not in title.lower():
                    continue
                if story and epic_id and story.get("epic_id") != epic_id:
                    continue
                story_title = (
                    (story.get("user_story") or story.get("user_story_id") or "")
                    if story
                    else ""
                )
                available_items.append({
                    "type": "subtask",
                    "id": subtask_id,
                    "item_id": subtask_id,
                    "title": title,
                    "task_id": subtask.get("task_id"),
                    "storyTitle": story_title,
                    "storyId": story_id,
                    "story_id": story_id,
                    "source": "story" if story else "project",
                    "taskType": subtask.get("task_type") or subtask.get("type"),
                    "task_type": subtask.get("task_type") or subtask.get("type"),
                    "status": coerce_workflow_status(subtask.get("status"), default="To Do"),
                    "assignee": subtask.get("assignee"),
                    "createdDate": subtask.get("createdDate"),
                    "created_at": subtask.get("created_at"),
                    "estimated_hours": subtask.get("estimated_hours"),
                })

        return ResponseModel(success=True, message="Available items retrieved successfully", data=available_items)
    except Exception as e:
        logging.error(f"Error retrieving available items for project {project_id}: {e}")
        return ResponseModel(success=False, message=f"Error retrieving available items: {str(e)}", data=None)


def bulk_update_sprint_items(
    sprint_id: str,
    project_id: str,
    user_id: str,
    assign_items: Optional[List[Dict[str, str]]] = None,
    unassign_items: Optional[List[Dict[str, str]]] = None,
) -> ResponseModel:
    try:
        results = {
            "assigned": [],
            "unassigned": [],
            "errors": [],
        }

        for item in assign_items or []:
            item_type = item.get("type")
            item_id = item.get("id")
            if not item_type or not item_id:
                results["errors"].append({"action": "assign", "item": item, "error": "type and id are required"})
                continue
            response = assign_item_to_sprint(sprint_id, project_id, user_id, item_type, item_id)
            if response.success:
                results["assigned"].append(item)
            else:
                results["errors"].append({"action": "assign", "item": item, "error": response.message})

        for item in unassign_items or []:
            item_type = item.get("type")
            item_id = item.get("id")
            if not item_type or not item_id:
                results["errors"].append({"action": "unassign", "item": item, "error": "type and id are required"})
                continue
            response = unassign_item_from_sprint(sprint_id, project_id, user_id, item_type, item_id)
            if response.success:
                results["unassigned"].append(item)
            else:
                results["errors"].append({"action": "unassign", "item": item, "error": response.message})

        return ResponseModel(success=True, message="Bulk update completed", data=results)
    except Exception as e:
        logging.error(f"Error performing bulk update for sprint {sprint_id}: {e}")
        return ResponseModel(success=False, message=f"Error performing bulk update: {str(e)}", data=None)


def reorder_sprints(project_id: str, user_id: str, order: List[str]) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        sprint_docs = FIRESTORE_CLIENT.collection(SPRINTS_COLLECTION).where(
            "project_id", "==", project_id
        ).get()
        existing_ids = [doc.id for doc in sprint_docs]

        if len(order) != len(set(order)):
            return ResponseModel(success=False, message="Order list contains duplicate sprint IDs", data=None)
        if set(order) != set(existing_ids):
            return ResponseModel(success=False, message="Order list must include all sprint IDs", data=None)

        now = _current_timestamp_iso()
        for index, sprint_id in enumerate(order):
            FIRESTORE_CLIENT.collection(SPRINTS_COLLECTION).document(sprint_id).update({
                "order": index + 1,
                "updated_at": now,
            })

        return ResponseModel(success=True, message="Sprint order updated successfully", data={"order": order})
    except Exception as e:
        logging.error(f"Error reordering sprints for project {project_id}: {e}")
        return ResponseModel(success=False, message=f"Error reordering sprints: {str(e)}", data=None)


def _parse_order_token(token: str) -> Tuple[Optional[str], str]:
    if not token:
        return None, ""
    lowered = token.lower()
    if lowered.startswith("story-"):
        return "story", token[6:]
    if lowered.startswith("subtask-"):
        return "subtask", token[8:]
    if lowered.startswith("sub-"):
        return "subtask", token[4:]
    if ":" in token:
        parts = token.split(":", 1)
        return parts[0].strip().lower(), parts[1]
    return None, token


def reorder_sprint_items(
    sprint_id: str,
    project_id: str,
    user_id: str,
    order: List[str],
) -> ResponseModel:
    try:
        project_response = _get_project_or_error(project_id, user_id)
        if not project_response.success:
            return project_response

        sprint_data, _ = _get_sprint_doc(sprint_id)
        if not sprint_data:
            return ResponseModel(success=False, message="Sprint not found", data=None)
        if sprint_data.get("project_id") != project_id:
            return ResponseModel(success=False, message="Sprint does not belong to this project", data=None)
        if sprint_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this sprint", data=None)

        assignments = FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).where(
            "sprint_id", "==", sprint_id
        ).get()
        assignment_map: Dict[Tuple[str, str], str] = {}
        for doc in assignments:
            assignment = doc.to_dict()
            item_type = assignment.get("item_type")
            item_id = assignment.get("item_id")
            if item_type and item_id:
                assignment_map[(item_type, item_id)] = doc.id

        parsed_order: List[Tuple[str, str]] = []
        for token in order:
            parsed_type, parsed_id = _parse_order_token(token)
            if not parsed_id:
                return ResponseModel(success=False, message="Invalid item id in order list", data=None)
            if parsed_type is None:
                matches = [key for key in assignment_map.keys() if key[1] == parsed_id]
                if len(matches) != 1:
                    return ResponseModel(success=False, message="Ambiguous item id in order list", data=None)
                parsed_order.append(matches[0])
            else:
                parsed_type = parsed_type.lower()
                if parsed_type not in {"story", "subtask"}:
                    return ResponseModel(success=False, message="Invalid item type in order list", data=None)
                parsed_order.append((parsed_type, parsed_id))

        if len(parsed_order) != len(set(parsed_order)):
            return ResponseModel(success=False, message="Order list contains duplicate items", data=None)
        if set(parsed_order) != set(assignment_map.keys()):
            return ResponseModel(success=False, message="Order list must include all sprint items", data=None)

        now = _current_timestamp_iso()
        for index, (item_type, item_id) in enumerate(parsed_order):
            assignment_id = assignment_map.get((item_type, item_id))
            if assignment_id:
                FIRESTORE_CLIENT.collection(SPRINT_ITEMS_COLLECTION).document(assignment_id).update({
                    "order": index + 1,
                    "updated_at": now,
                })

        return ResponseModel(success=True, message="Sprint items reordered successfully", data={"order": order})
    except Exception as e:
        logging.error(f"Error reordering items for sprint {sprint_id}: {e}")
        return ResponseModel(success=False, message=f"Error reordering sprint items: {str(e)}", data=None)
