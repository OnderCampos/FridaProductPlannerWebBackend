from datetime import datetime, timezone
import re
from typing import List, Dict, Any, Optional
import logging
import traceback

from src.services.notifications import NotificationService
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
from src.utils.authz.permissions import get_project_access, get_project_id_for_epic
from src.utils.authz.users import get_user_profile
from src.utils.planning.assignees import build_member_lookup, normalize_assignee_fields


def _current_timestamp_iso() -> str:
    """Generate current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


def _normalize_field_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return ""
    key = key.replace(" ", "_")
    key = re.sub(r"[^a-z0-9_]+", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key


def _find_story_field_value(fields: Any, candidate_keys: List[str]) -> Optional[Any]:
    if not isinstance(fields, list):
        return None
    normalized_candidates = {_normalize_field_key(k) for k in candidate_keys if str(k or "").strip()}
    if not normalized_candidates:
        return None
    for item in fields:
        if not isinstance(item, dict):
            continue
        raw_key = item.get("key") or item.get("name")
        normalized_key = _normalize_field_key(raw_key)
        if normalized_key in normalized_candidates:
            return item.get("value")
    return None


def _extract_markdown_section_list(text: str, headings: List[str]) -> List[str]:
    """
    Best-effort extraction of bullet items under a heading inside a free-form description.
    Example supported formats:
      Acceptance Criteria:
      - item
      - item
    """
    if not text:
        return []

    heading_set = {str(h or "").strip().lower() for h in headings if str(h or "").strip()}
    if not heading_set:
        return []

    lines = [str(line) for line in str(text).splitlines()]
    start_index: Optional[int] = None
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        normalized = line.lower().rstrip(":").strip()
        if normalized in heading_set:
            start_index = index + 1
            break

    if start_index is None:
        return []

    collected: List[str] = []
    for raw_line in lines[start_index:]:
        line = str(raw_line).strip()
        if not line:
            if collected:
                break
            continue

        normalized = line.lower().rstrip(":").strip()
        if normalized in heading_set:
            break
        if normalized in {
            "out of scope",
            "out_of_scope",
            "acceptance criteria",
            "acceptance_criteria",
            "dependencies",
            "description",
        }:
            break

        collected.append(line)

    return _normalize_string_list("\n".join(collected))


def _normalize_story_payload(story_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure user story payload includes effortHours and createdDate."""
    created_date = story_data.get("createdDate")
    if created_date is None:
        created_date = story_data.get("created_date")
    if created_date is None:
        created_date = story_data.get("created_at", "")

    effort_hours = story_data.get("effortHours")
    if effort_hours is None:
        effort_hours = story_data.get("effort_hours")
    if effort_hours is None:
        effort_hours = 0

    # Normalize acceptance/out-of-scope fields for backward compatibility:
    # - some flows store them as strings in `document` or inside `fields`
    # - some older stories omitted them entirely
    document = story_data.get("document") if isinstance(story_data.get("document"), dict) else {}
    fields = story_data.get("fields")

    acceptance_source = (
        story_data.get("acceptanceCriteria")
        or story_data.get("acceptance_criteria")
        or document.get("acceptance_criteria")
        or document.get("acceptanceCriteria")
        or _find_story_field_value(fields, ["acceptanceCriteria", "acceptance_criteria", "acceptance criteria"])
    )
    acceptance = _normalize_string_list(acceptance_source)
    if not acceptance:
        acceptance = _extract_markdown_section_list(
            str(story_data.get("description") or ""),
            ["acceptance criteria", "acceptance_criteria"],
        )
    if not acceptance:
        acceptance = ["Not provided."]

    out_scope_source = (
        story_data.get("outOfScope")
        or story_data.get("out_of_scope")
        or document.get("out_of_scope")
        or document.get("outOfScope")
        or _find_story_field_value(fields, ["outOfScope", "out_of_scope", "out of scope"])
    )
    out_scope = _normalize_string_list(out_scope_source)
    if not out_scope:
        out_scope = _extract_markdown_section_list(
            str(story_data.get("description") or ""),
            ["out of scope", "out_of_scope"],
        )
    if not out_scope:
        out_scope = ["N/A"]

    story_data["createdDate"] = created_date
    story_data["effortHours"] = effort_hours
    story_data["acceptanceCriteria"] = acceptance
    story_data["outOfScope"] = out_scope
    return story_data


def _parse_effort_hours(raw_effort: Optional[Any]) -> float:
    if raw_effort is None:
        return 0
    try:
        return float(raw_effort)
    except (TypeError, ValueError):
        return 0


def _parse_order_value(raw_order: Optional[Any]) -> float:
    if raw_order is None:
        return 0
    try:
        return float(raw_order)
    except (TypeError, ValueError):
        return 0


def _normalize_string_list(value: Optional[Any]) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []

    items: List[str] = []
    for raw_line in text.splitlines():
        line = str(raw_line).strip()
        if not line:
            continue
        for prefix in ("- ", "* ", "• "):
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
                break
        if line and line[0].isdigit():
            # Remove common numbering formats like "1. " or "1) "
            if len(line) >= 3 and line[1] in {".", ")"} and line[2] == " ":
                line = line[3:].strip()
        if line:
            items.append(line)
    return items

def _attach_story_sprint_assignment(story_data: Dict[str, Any], story_id: str) -> Dict[str, Any]:
    """Attach the assigned sprint ID for a story when present."""
    assignment_query = (
        FIRESTORE_CLIENT.collection("sprint_items")
        .where("item_type", "==", "story")
        .where("item_id", "==", story_id)
        .limit(1)
        .get()
    )

    story_data["sprint_id"] = None
    if assignment_query:
        assignment_data = assignment_query[0].to_dict()
        story_data["sprint_id"] = assignment_data.get("sprint_id")

    return story_data

"""
---------------------------------------------------------------------------------------------------------------------------------------
---------------------------------------------------------------------------------------------------------------------------------------
---------------------------------------------------------------------------------------------------------------------------------------
USER STORY NOTIFICATIONS
----------------------------------------------------------------------------------------------------------------------------------------
-----------------------------------------------------------------------------------------------------------------------------------------
-----------------------------------------------------------------------------------------------------------------------------------------
"""
def _maybe_send_user_story_assignment_notification(
    *,
    previous_story: Dict[str, Any],
    updated_story: Dict[str, Any],
    user_id: str,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        epic_id = str(updated_story.get("epic_id") or previous_story.get("epic_id") or "").strip()
        if not epic_id:
            return NotificationService()._notification_result(
                False,
                "skipped",
                "epic_missing",
                "Assignment email was not sent because the story epic could not be resolved.",
            )

        project_id = get_project_id_for_epic(epic_id)
        if not project_id:
            return NotificationService()._notification_result(
                False,
                "skipped",
                "project_missing",
                "Assignment email was not sent because the project could not be resolved.",
            )

        member_lookup = build_member_lookup(project_id)
        previous_assignee = normalize_assignee_fields(dict(previous_story or {}), member_lookup)
        updated_assignee = normalize_assignee_fields(dict(updated_story or {}), member_lookup)

        updated_assignee_name = str(updated_assignee.get("assignee") or "").strip()
        updated_assignee_id = str(updated_assignee.get("assignee_id") or "").strip()
        if not updated_assignee_name and not updated_assignee_id:
            return NotificationService()._notification_result(
                False,
                "skipped",
                "assignee_cleared",
                "",
            )

        previous_email = str(previous_assignee.get("assignee_email") or "").strip().lower()
        updated_email = str(updated_assignee.get("assignee_email") or "").strip().lower()
        if not updated_email:
            return NotificationService()._notification_result(
                False,
                "skipped",
                "assignee_email_missing",
                "Assignment email was not sent because the assigned member does not have a resolved email address.",
            )
        if updated_email == previous_email:
            return NotificationService()._notification_result(
                False,
                "skipped",
                "assignee_unchanged",
                "Assignment email was not sent because the assignee did not change.",
            )

        actor_email = str(user_email or "").strip().lower()
        if actor_email and updated_email == actor_email:
            return NotificationService()._notification_result(
                False,
                "skipped",
                "self_assigned",
                "Assignment email was not sent because the user assigned the story to themselves.",
            )

        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
        project_name = ""
        if project_doc.exists:
            project_name = str((project_doc.to_dict() or {}).get("name") or "").strip()

        epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
        epic_name = ""
        if epic_doc.exists:
            epic_payload = epic_doc.to_dict() or {}
            epic_name = str(epic_payload.get("epic") or epic_payload.get("name") or "").strip()

        assignee_name = str(
            updated_assignee.get("assignee")
            or member_lookup.get("by_email", {}).get(updated_email, {}).get("name")
            or updated_email
        ).strip()
        story_title = str(updated_story.get("user_story") or updated_story.get("title") or "").strip()
        story_reference = str(updated_story.get("user_story_id") or updated_story.get("id") or "").strip()

        actor_name = str(user_name or "").strip()
        if not actor_name:
            actor_profile = get_user_profile(user_id=user_id, email=user_email)
            actor_name = str(
                (actor_profile or {}).get("name")
                or (actor_profile or {}).get("email")
                or user_email
                or user_id
                or "A FridaPlatform administrator"
            ).strip()

        NotificationService().try_send_user_story_assignment(
            assignee_name=assignee_name,
            assignee_email=updated_email,
            project_name=project_name,
            epic_name=epic_name,
            story_title=story_title,
            story_reference=story_reference,
            assigned_by_name=actor_name,
        )
        return NotificationService()._notification_result(
            True,
            "sent",
            "sent",
            f"Assignment email sent to {updated_email}.",
        )
    except Exception:
        return NotificationService()._notification_result(
            False,
            "failed",
            "notification_provider_failed",
            "Assignment email was not sent because the notification provider failed.",
        )

def _maybe_send_user_story_updated_notification(
    *,
    previous_story: Dict[str, Any],
    updated_story: Dict[str, Any],
    user_id: str,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        fields_to_watch = {
            "status": "Status",
            "effortHours": "Effort Hours",
            "storyPoints": "Story Points",
            "priority": "Priority"
        }

        changes = {}
        for db_key, display_name in fields_to_watch.items():
            old_val = str(previous_story.get(db_key) or "N/A").strip()
            new_val = str(updated_story.get(db_key) or "N/A").strip()

            if old_val != new_val and old_val.lower() != new_val.lower():
                changes[display_name] = {"old": old_val, "new": new_val}

        if not changes:
            return NotificationService()._notification_result(False, "skipped", "no_changes", "No relevant fields were changed")

        # Get epic id to find the project
        epic_id = str(updated_story.get("epic_id") or previous_story.get("epic_id") or "").strip()
        if not epic_id:
            return NotificationService()._notification_result(False, "skipped", "epic_missing", "Epic ID is missing.")

        epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
        epic_name = ""
        if epic_doc.exists:
            epic_data = epic_doc.to_dict() or {}
            epic_name = str(epic_data.get("epic") or epic_data.get("name") or "").strip()

        project_id = get_project_id_for_epic(epic_id)
        if not project_id:
            return NotificationService()._notification_result(False, "skipped", "project_missing", "Project missing")

        # Extract project document
        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
        if not project_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "project_not_found", "Project not found")

        project_data = project_doc.to_dict() or {}
        project_name = str(project_data.get("name") or "").strip()

        # Get project leader
        leader_email = project_data.get("projectLead", "")
        leader_id = project_data.get("user_id", "")
        leader_doc = FIRESTORE_CLIENT.collection("users").document(leader_id).get()
        if not leader_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "admin_not_found", "Admin Project not found")

        leader_data = leader_doc.to_dict() or {}
        leader_name = leader_data.get("name", "")

        # If the leader has made the change , we do not send the email
        if user_email and user_email.lower() == leader_email.lower():
            return NotificationService()._notification_result(False, "skipped", "user_is_admin", "User is the admin, no email needed")

        # Get who has made the change
        user_story_title = str(updated_story.get("user_story") or "").strip()
        actor_name = str(user_name or user_email or "A User").strip()
        if not actor_name:
            actor_profile = get_user_profile(user_id=user_id, email=user_email)
            actor_name = str(
                (actor_profile or {}).get("name")
                or (actor_profile or {}).get("email")
                or user_email
                or user_id
                or "A FridaPlatform administrator"
            ).strip()

        NotificationService().try_send_user_story_updated(
            leader_email=leader_email,
            leader_name=leader_name,
            changer_name=actor_name,
            project_name=project_name,
            epic_name=epic_name,
            story_title=user_story_title,
            changes=changes
        )

        return NotificationService()._notification_result(
            True, 
            "sent", 
            "sent", 
            f"Update email sent to {leader_email} with changes: {list(changes.keys())}"
        )
    except Exception as e:
        print(f"ERROR EN NOTIFICACIÓN DE STATUS: {e}")
        traceback.print_exc()

        return NotificationService()._notification_result(
            False,
            "failed",
            "notification_provider_failed",
            "Assignment email was not sent because the notification provider failed.",
        )

def create_user_story(epic_id: str, user_id: str, user_story_data: Dict[str, Any], template_data: Dict[str, Any] = None) -> ResponseModel:
    """
    Creates a new user story in Firestore using structured format with fields array.
    
    Args:
        epic_id (str): The epic ID this user story belongs to
        user_id (str): The user ID who owns this user story
        user_story_data (Dict[str, Any]): The user story data from LLM generation
        template_data (Dict[str, Any], optional): Template data for field descriptions
        
    Returns:
        ResponseModel: Response containing the created user story
    """
    try:
        now = _current_timestamp_iso()
        
        # Core user story fields that should not go into the fields array
        core_fields = {
            "epic",
            "user_story",
            "description",
            "user_story_id",
            "order",
            "dependencies",
            "effortHours",
            "effort_hours",
            "createdDate",
            "created_date",
            "story_points",  
            "storyPoints",
            "document",
            "acceptanceCriteria",
            "acceptance_criteria",
            "outOfScope",
            "out_of_scope",
        }

        raw_effort = user_story_data.get("effortHours")
        if raw_effort is None:
            raw_effort = user_story_data.get("effort_hours")
        effort_hours = _parse_effort_hours(raw_effort)

        try:
            story_points = int(user_story_data.get("story_points", 0))
        except (ValueError, TypeError):
            story_points = 0
        
        # Prepare structured user story document
        story_document = {
            "epic_id": epic_id,
            "user_id": user_id,
            "epic": user_story_data.get("epic", ""),
            "user_story": user_story_data.get("user_story", ""),
            "description": user_story_data.get("description", ""),
            "user_story_id": user_story_data.get("user_story_id", ""),
            "order": user_story_data.get("order", 0),
            "dependencies": user_story_data.get("dependencies", []),
            "document": user_story_data.get("document") if isinstance(user_story_data.get("document"), dict) else {},
            "acceptanceCriteria": _normalize_string_list(
                user_story_data.get("acceptanceCriteria") or user_story_data.get("acceptance_criteria")
            ),
            "outOfScope": _normalize_string_list(
                user_story_data.get("outOfScope") or user_story_data.get("out_of_scope")
            ),
            "created_at": now,
            "createdDate": now,
            "updated_at": now,
            "effortHours": effort_hours,
            "storyPoints": story_points,
            "fields": []
        }
        
        # Get template fields for descriptions
        template_fields_map = {}
        if template_data and template_data.get("fields"):
            for field in template_data["fields"]:
                key = field["name"].lower().replace(" ", "_")
                template_fields_map[key] = {
                    "name": field["name"],
                    "description": field.get("description", "")
                }
        
        # Transform template fields into structured format for storage
        for key, value in user_story_data.items():
            if key not in core_fields and value:  # Skip core fields and empty values
                template_info = template_fields_map.get(key, {})
                field_obj = {
                    "name": template_info.get("name", key.replace("_", " ").title()),
                    "key": key,
                    "value": str(value),
                    "description": template_info.get("description", f"Custom field: {key}")
                }
                story_document["fields"].append(field_obj)
        
        # Add to user_stories collection
        doc_ref = FIRESTORE_CLIENT.collection("user_stories").add(story_document)
        firestore_id = doc_ref[1].id
        story_document["id"] = firestore_id
        
        return ResponseModel(
            success=True,
            message="User story created successfully",
            data=story_document
        )
    except Exception as e:
        logging.error(f"Error creating user story: {e}")
        return ResponseModel(
            success=False,
            message=f"Error creating user story: {str(e)}",
            data=None
        )


def create_multiple_user_stories(epic_id: str, user_id: str, user_stories_list: List[Dict[str, Any]], template_data: Dict[str, Any] = None) -> ResponseModel:
    """
    Creates multiple user stories in Firestore using structured format.
    
    Args:
        epic_id (str): The epic ID these user stories belong to
        user_id (str): The user ID who owns these user stories
        user_stories_list (List[Dict[str, Any]]): List of user story data from LLM generation
        template_data (Dict[str, Any], optional): Template data for field descriptions
        
    Returns:
        ResponseModel: Response containing the created user stories
    """
    try:
        created_stories = []
        
        for story_data in user_stories_list:
            result = create_user_story(epic_id, user_id, story_data, template_data)
            if result.success:
                created_stories.append(result.data)
            else:
                logging.warning(f"Failed to create user story: {result.message}")
        
        # Return the created stories in structured format
        return ResponseModel(
            success=True,
            message=f"Created {len(created_stories)} user stories successfully",
            data=created_stories
        )
    except Exception as e:
        logging.error(f"Error creating multiple user stories: {e}")
        return ResponseModel(
            success=False,
            message=f"Error creating user stories: {str(e)}",
            data=None
        )


def get_user_story_by_id(story_id: str, user_id: str, allow_member: bool = False, user_email: Optional[str] = None) -> ResponseModel:
    """
    Retrieves a single user story by ID with user authentication.
    
    Args:
        story_id (str): The user story ID
        user_id (str): The user ID for ownership verification
        
    Returns:
        ResponseModel: Response containing the user story
    """
    try:
        story_ref = FIRESTORE_CLIENT.collection("user_stories").document(story_id)
        story_doc = story_ref.get()
        
        if not story_doc.exists:
            return ResponseModel(
                success=False,
                message="User story not found",
                data=None
            )
        
        story_data = story_doc.to_dict()
        story_data["id"] = story_doc.id
        _normalize_story_payload(story_data)
        
        # Verify ownership or membership
        if story_data.get("user_id") != user_id:
            if not allow_member:
                return ResponseModel(
                    success=False,
                    message="Unauthorized: You don't own this user story",
                    data=None
                )
            project_id = get_project_id_for_epic(story_data.get("epic_id"))
            if not project_id:
                return ResponseModel(
                    success=False,
                    message="Unauthorized: You don't have access to this user story",
                    data=None
                )
            access = get_project_access(project_id, user_id, user_email)
            if not access.success:
                return ResponseModel(
                    success=False,
                    message="Unauthorized: You don't have access to this user story",
                    data=None
                )

        _attach_story_sprint_assignment(story_data, story_id)

        return ResponseModel(
            success=True,
            message="User story retrieved successfully",
            data=story_data
        )
    except Exception as e:
        logging.error(f"Error retrieving user story {story_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving user story: {str(e)}",
            data=None
        )


def get_user_stories_by_epic(epic_id: str, user_id: str = None, allow_member: bool = False) -> ResponseModel:
    """
    Retrieves all user stories for a specific epic, ordered by their order field.
    
    Args:
        epic_id (str): The epic ID
        user_id (str, optional): The user ID for additional verification
        
    Returns:
        ResponseModel: Response containing the user stories ordered by execution sequence
    """
    try:
        query = FIRESTORE_CLIENT.collection("user_stories").where("epic_id", "==", epic_id)
        
        # Add user filter if provided
        if user_id and not allow_member:
            query = query.where("user_id", "==", user_id)
        
        user_stories_docs = query.get()
        
        user_stories = []
        for doc in user_stories_docs:
            story_data = doc.to_dict()
            # story_data["id"] = doc.id
            story_id = doc.id 

            subtasks_docs = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", story_id).get()

            # Sum estimate_hours from subtasks
            total_subtask_hours = sum(float(s.to_dict().get("estimated_hours", 0)) for s in subtasks_docs)

            story_data["id"] = story_id

            _normalize_story_payload(story_data)
            _attach_story_sprint_assignment(story_data, story_id)

            story_data["effortHours"] = story_data.get("effortHours", 0) + total_subtask_hours

            user_stories.append(story_data)
        
        # Sort by order field
        user_stories.sort(key=lambda x: _parse_order_value(x.get("order")))
        
        return ResponseModel(
            success=True,
            message=f"Retrieved {len(user_stories)} user stories for epic",
            data=user_stories
        )
        
    except Exception as e:
        logging.error(f"Error retrieving user stories for epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving user stories: {str(e)}",
            data=None
        )

def get_user_stories_by_epic_with_auth(epic_id: str, user_id: str, user_email: Optional[str] = None) -> ResponseModel:
    """
    Retrieves all user stories for a specific epic, ensuring the user owns the epic.
    
    Args:
        epic_id (str): The epic ID
        user_id (str): The user ID
        
    Returns:
        ResponseModel: Response containing the user stories
    """
    try:
        # First verify the user owns the epic (through project ownership)
        from src.utils.planning.epics import get_epic_by_id
        
        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success:
            return ResponseModel(
                success=False,
                message="Epic not found",
                data=None
            )
        
        epic = epic_response.data
        access = get_project_access(epic["project_id"], user_id, user_email)
        if not access.success:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this project/epic",
                data=None
            )
        
        # Get user stories for the epic
        stories_result = get_user_stories_by_epic(epic_id, user_id, allow_member=True)
        
        # Stories are already in structured format from Firestore, no transformation needed
        return stories_result
        
    except Exception as e:
        logging.error(f"Error retrieving user stories for epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving user stories: {str(e)}",
            data=None
        )


def update_user_story(
    story_id: str,
    user_id: str,
    update_data: Dict[str, Any],
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> ResponseModel:
    """
    Updates an existing user story.
    
    Args:
        story_id (str): The user story ID
        user_id (str): The user ID (for ownership verification)
        update_data (Dict[str, Any]): Data to update
        
    Returns:
        ResponseModel: Response containing the updated user story
    """
    try:
        story_ref = FIRESTORE_CLIENT.collection("user_stories").document(story_id)
        story_doc = story_ref.get()
        
        if not story_doc.exists:
            return ResponseModel(
                success=False,
                message="User story not found",
                data=None
            )
        
        story_data = story_doc.to_dict()
        previous_story_data = dict(story_data or {})
        
        # Check if user owns the user story
        if story_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this user story",
                data=None
            )
        
        # Add updated_at timestamp
        update_data["updated_at"] = _current_timestamp_iso()
        
        # Update the document
        story_ref.update(update_data)
        
        # Get updated user story
        updated_doc = story_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data["id"] = story_id
        _normalize_story_payload(updated_data)
        assignment_notification = _maybe_send_user_story_assignment_notification(
            previous_story=previous_story_data,
            updated_story=updated_data,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
        )
        updated_data["assignment_notification"] = assignment_notification
        
        return ResponseModel(
            success=True,
            message="User story updated successfully",
            data=updated_data
        )
    except Exception as e:
        logging.error(f"Error updating user story {story_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating user story: {str(e)}",
            data=None
        )

def update_user_story_fields(
    story_id: str,
    user_id: str,
    update_data: Dict[str, Any],
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> ResponseModel:
    """
    Updates the fields array of an existing user story.
    
    Args:
        story_id (str): The user story ID
        user_id (str): The user ID (for ownership verification)
        update_data (Dict[str, Any]): Data to update in fields array
        
    Returns:
        ResponseModel: Response containing the updated user story
    """
    try:
        user_story_ref = FIRESTORE_CLIENT.collection("user_stories").document(story_id)
        user_story_doc = user_story_ref.get()

        if not user_story_doc.exists:
            return ResponseModel(success=False, message="User story not found", data=None)

        user_story_data = user_story_doc.to_dict()
        previous_story_data = dict(user_story_data or {})
        if user_story_data.get("user_id") != user_id:
            project_id = get_project_id_for_epic(user_story_data.get("epic_id"))
            access = get_project_access(project_id, user_id, user_email) if project_id else None
            if not access or not access.success or not (access.data or {}).get("is_lead"):
                return ResponseModel(success=False, message="Unauthorized: You don't own this user story", data=None)

        allowed_fields = {
            "user_story",
            "epic",
            "title",
            "description",
            "priority",
            "priority_level",
            "status",
            "story_status",
            "assignee",
            "assignee_id",
            "assigned_to",
            "assignedTo",
            "assigneeId",
            "assigneeEmail",
            "assignee_email",
            "storyPoints",
            "story_points",
            "dueDate",
            "effortHours",
            "effort_hours",
            "startDate",
            "start_date",
            "acceptanceCriteria",
            "acceptance_criteria",
            "outOfScope",
            "out_of_scope",
        }
        filtered_update = {k: v for k, v in update_data.items() if k in allowed_fields}

        # Accept snake_case/camelCase aliases and persist canonical keys.
        if "story_points" in filtered_update and "storyPoints" not in filtered_update:
            filtered_update["storyPoints"] = filtered_update.pop("story_points")

        if "effort_hours" in filtered_update and "effortHours" not in filtered_update:
            filtered_update["effortHours"] = filtered_update.pop("effort_hours")

        if "story_status" in filtered_update and "status" not in filtered_update:
            filtered_update["status"] = filtered_update.pop("story_status")

        if "priority_level" in filtered_update and "priority" not in filtered_update:
            filtered_update["priority"] = filtered_update.pop("priority_level")

        if "start_date" in filtered_update and "startDate" not in filtered_update:
            filtered_update["startDate"] = filtered_update.pop("start_date")

        if "assignedTo" in filtered_update and "assigned_to" not in filtered_update:
            filtered_update["assigned_to"] = filtered_update.pop("assignedTo")

        if "assignee_id" in filtered_update and "assigneeId" not in filtered_update:
            filtered_update["assigneeId"] = filtered_update.pop("assignee_id")

        if "assignee_email" in filtered_update and "assigneeEmail" not in filtered_update:
            filtered_update["assigneeEmail"] = filtered_update.pop("assignee_email")

        if "acceptance_criteria" in filtered_update and "acceptanceCriteria" not in filtered_update:
            filtered_update["acceptanceCriteria"] = filtered_update.pop("acceptance_criteria")

        if "out_of_scope" in filtered_update and "outOfScope" not in filtered_update:
            filtered_update["outOfScope"] = filtered_update.pop("out_of_scope")

        if "acceptanceCriteria" in filtered_update:
            filtered_update["acceptanceCriteria"] = _normalize_string_list(filtered_update.get("acceptanceCriteria"))

        if "outOfScope" in filtered_update:
            filtered_update["outOfScope"] = _normalize_string_list(filtered_update.get("outOfScope"))

        incoming_status = filtered_update.get("status")
        if isinstance(incoming_status, str):
            normalized_status = incoming_status.strip().lower().replace("_", " ")
            status_map = {
                "todo": "To Do",
                "to do": "To Do",
                "in progress": "In Progress",
                "inprogress": "In Progress",
                "in review": "In Review",
                "inreview": "In Review",
                "stopped": "Stopped",
                "done": "Done",
            }
            canonical_status = status_map.get(normalized_status)
            if canonical_status is None:
                return ResponseModel(
                    success=False,
                    message="Invalid status value",
                    data={"valid_statuses": ["To Do", "In Progress", "In Review", "Stopped", "Done"]},
                )
            filtered_update["status"] = canonical_status
            incoming_status = canonical_status
        existing_start_date = user_story_data.get("startDate") or user_story_data.get("start_date")
        if (
            isinstance(incoming_status, str)
            and not existing_start_date
            and "startDate" not in filtered_update
        ):
            normalized_status = incoming_status.strip().lower()
            if normalized_status not in {"to do", "todo", "backlog"}:
                filtered_update["startDate"] = _current_timestamp_iso()

        if not filtered_update:
            return ResponseModel(success=False, message="No valid fields to update", data=None)

        filtered_update["updated_at"] = _current_timestamp_iso()

        # Execute the update
        user_story_ref.update(filtered_update)

        # Get the result and normalize it
        updated_doc = user_story_ref.get()
        final_data = updated_doc.to_dict()
        final_data["id"] = story_id

        _normalize_story_payload(final_data)
        assignment_notification = _maybe_send_user_story_assignment_notification(
            previous_story=previous_story_data,
            updated_story=final_data,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
        )
        final_data["assignment_notification"] = assignment_notification

        updated_notification = _maybe_send_user_story_updated_notification(
            previous_story=previous_story_data,
            updated_story=final_data,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name
        )
        final_data["updated_notification"] = updated_notification

        return ResponseModel(
            success=True, 
            message="User story fields updated successfully", 
            data=final_data
        )
    except Exception as e:
        logging.error(f"Error updating user story fields: {e}")
        return ResponseModel(success=False, message=f"Error updating user story fields: {str(e)}", data=None)

def delete_user_story(story_id: str, user_id: str) -> ResponseModel:
    """
    Deletes a specific user story.
    
    Args:
        story_id (str): The user story ID
        user_id (str): The user ID (for ownership verification)
        
    Returns:
        ResponseModel: Response confirming deletion
    """
    try:
        story_ref = FIRESTORE_CLIENT.collection("user_stories").document(story_id)
        story_doc = story_ref.get()
        
        if not story_doc.exists:
            return ResponseModel(
                success=False,
                message="User story not found",
                data=None
            )
        
        story_data = story_doc.to_dict()
        
        # Check if user owns the user story
        if story_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this user story",
                data=None
            )
        
        # Delete the user story
        story_ref.delete()
        
        return ResponseModel(
            success=True,
            message="User story deleted successfully",
            data=None
        )
    except Exception as e:
        logging.error(f"Error deleting user story {story_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting user story: {str(e)}",
            data=None
        )


def delete_user_stories_by_epic(epic_id: str, user_id: str = None) -> ResponseModel:
    """
    Deletes all user stories for an epic (called when epic is deleted).
    
    Args:
        epic_id (str): The epic ID
        user_id (str, optional): The user ID (for verification)
        
    Returns:
        ResponseModel: Response confirming deletion
    """
    try:
        query = FIRESTORE_CLIENT.collection("user_stories").where("epic_id", "==", epic_id)
        
        # Add user filter if provided
        if user_id:
            query = query.where("user_id", "==", user_id)
        
        user_stories_docs = query.get()
        
        # Delete each user story
        deleted_count = 0
        for doc in user_stories_docs:
            doc.reference.delete()
            deleted_count += 1
        
        return ResponseModel(
            success=True,
            message=f"Deleted {deleted_count} user stories for epic",
            data={"deleted_count": deleted_count}
        )
    except Exception as e:
        logging.error(f"Error deleting user stories for epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting user stories: {str(e)}",
            data=None
        )
