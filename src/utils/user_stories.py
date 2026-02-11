from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import logging

from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel


def _current_timestamp_iso() -> str:
    """Generate current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


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

    story_data["createdDate"] = created_date
    story_data["effortHours"] = effort_hours
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
        }

        raw_effort = user_story_data.get("effortHours")
        if raw_effort is None:
            raw_effort = user_story_data.get("effort_hours")
        effort_hours = _parse_effort_hours(raw_effort)
        
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
            "created_at": now,
            "createdDate": now,
            "updated_at": now,
            "effortHours": effort_hours,
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


def get_user_story_by_id(story_id: str, user_id: str) -> ResponseModel:
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
        
        # Verify ownership
        if story_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this user story",
                data=None
            )

        # Search in sprint_items
        assignment_query = FIRESTORE_CLIENT.collection('sprint_items').where(
            'item_type', '==', 'story'
        ).where(
            'item_id', '==', story_id
        ).limit(1).get()

        # Extract sprint id
        story_data["sprint_id"] = None 
        if assignment_query:
            assignment_data = assignment_query[0].to_dict()
            story_data["sprint_id"] = assignment_data.get("sprint_id")

        print(f"DEBUG SPRINT ID: {story_data['sprint_id']}")

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


def get_user_stories_by_epic(epic_id: str, user_id: str = None) -> ResponseModel:
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
        if user_id:
            query = query.where("user_id", "==", user_id)
        
        user_stories_docs = query.get()
        
        user_stories = []
        for doc in user_stories_docs:
            story_data = doc.to_dict()
            story_data["id"] = doc.id
            _normalize_story_payload(story_data)
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


def get_user_stories_by_epic_with_auth(epic_id: str, user_id: str) -> ResponseModel:
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
        from src.utils.epics import get_epic_by_id
        from src.utils.projects import get_project_by_id
        
        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success:
            return ResponseModel(
                success=False,
                message="Epic not found",
                data=None
            )
        
        epic = epic_response.data
        project_response = get_project_by_id(epic["project_id"], user_id)
        if not project_response.success:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this project/epic",
                data=None
            )
        
        # Get user stories for the epic
        stories_result = get_user_stories_by_epic(epic_id, user_id)
        
        # Stories are already in structured format from Firestore, no transformation needed
        return stories_result
        
    except Exception as e:
        logging.error(f"Error retrieving user stories for epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving user stories: {str(e)}",
            data=None
        )


def update_user_story(story_id: str, user_id: str, update_data: Dict[str, Any]) -> ResponseModel:
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

def update_user_story_fields(story_id: str, user_id: str, update_data: Dict[str, Any]) -> ResponseModel:
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
        if user_story_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this user story", data=None)

        allowed_fields = {"user_story", "epic", "title", "description", "priority", "storyPoints", "dueDate"}
        filtered_update = {k: v for k, v in update_data.items() if k in allowed_fields}

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
