from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import logging

from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
from src.utils.permissions import get_project_access


def _current_timestamp_iso() -> str:
    """Generate current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


def get_epics_for_project(project_id: str, user_id: str = None) -> List[Dict[str, Any]]:
    """
    Retrieves all epics for a specific project.
    
    Args:
        project_id (str): The project ID
        user_id (str, optional): The user ID for additional verification
        
    Returns:
        List[Dict[str, Any]]: List of epic documents with their IDs
    """
    try:
        epics_ref = FIRESTORE_CLIENT.collection("epics").where("project_id", "==", project_id)
        epics_docs = epics_ref.get()
        
        project_epics = []
        for doc in epics_docs:
            epic_data = doc.to_dict()
            epic_data["id"] = doc.id
            project_epics.append(epic_data)
        
        print(f"[DEBUG] Found {len(project_epics)} epics for project {project_id}")
        return project_epics
        
    except Exception as epic_error:
        logging.warning(f"Error retrieving epics for project {project_id}: {epic_error}")
        return []  # Return empty list if epics can't be retrieved


def get_epics_for_project_with_auth(project_id: str, user_id: str, user_email: Optional[str] = None) -> ResponseModel:
    """
    Retrieves all epics for a specific project, ensuring the user owns the project.
    
    Args:
        project_id (str): The project ID
        user_id (str): The user ID
        
    Returns:
        ResponseModel: Response containing the project's epics
    """
    try:
        access = get_project_access(project_id, user_id, user_email)
        if not access.success:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't have access to this project",
                data=None
            )
        
        # Get epics for the project using the utility function
        all_epics = get_epics_for_project(project_id, user_id)
        
        return ResponseModel(
            success=True,
            message="Epics retrieved successfully",
            data=all_epics
        )
    except Exception as e:
        logging.error(f"Error retrieving epics for project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving epics: {str(e)}",
            data=None
        )


def delete_epics_for_project(project_id: str, user_id: str = None) -> ResponseModel:
    """
    Deletes all epics for a project when the project is deleted.
    This is called internally by delete_project.
    
    Args:
        project_id (str): The project ID
        user_id (str, optional): The user ID (for verification)
        
    Returns:
        ResponseModel: Response confirming epic deletion
    """
    try:
        # Get all epics for the project
        epics_ref = FIRESTORE_CLIENT.collection("epics").where("project_id", "==", project_id)
        epics_docs = epics_ref.get()
        
        # Delete each epic
        deleted_count = 0
        for doc in epics_docs:
            doc.reference.delete()
            deleted_count += 1
        
        return ResponseModel(
            success=True,
            message=f"Deleted {deleted_count} epics for project",
            data={"deleted_count": deleted_count}
        )
    except Exception as e:
        logging.error(f"Error deleting epics for project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting epics: {str(e)}",
            data=None
        )

def get_epic_by_id(epic_id: str) -> ResponseModel:
    """
    Retrieves a specific epic by its ID.

    Args:
        epic_id (str): The epic ID

    Returns:
        ResponseModel: Response containing the epic data or an error message
    """
    try:
        epic_ref = FIRESTORE_CLIENT.collection("epics").document(epic_id)
        epic_doc = epic_ref.get()

        if not epic_doc.exists:
            return ResponseModel(
                success=False,
                message="Epic not found",
                data=None
            )

        epic_data = epic_doc.to_dict()
        epic_data["id"] = epic_id

        return ResponseModel(
            success=True,
            message="Epic retrieved successfully",
            data=epic_data
        )
    except Exception as e:
        logging.error(f"Error retrieving epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving epic: {str(e)}",
            data=None
        )

def create_epic(project_id: str, user_id: str, name: str, description: str) -> ResponseModel:
    """
    Creates a new epic for a project.
    
    Args:
        project_id (str): The project ID
        user_id (str): The user ID (owner)
        name (str): Epic name
        description (str): Epic description
        
    Returns:
        ResponseModel: Response containing the created epic
    """
    try:
        now = _current_timestamp_iso()
        epic_data = {
            "project_id": project_id,
            "user_id": user_id,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
        }
        
        # Add epic to epics collection
        epic_doc_ref = FIRESTORE_CLIENT.collection("epics").add(epic_data)
        epic_id = epic_doc_ref[1].id
        epic_data["id"] = epic_id
        
        return ResponseModel(
            success=True,
            message="Epic created successfully",
            data=epic_data
        )
    except Exception as e:
        logging.error(f"Error creating epic: {e}")
        return ResponseModel(
            success=False,
            message=f"Error creating epic: {str(e)}",
            data=None
        )


def update_epic(epic_id: str, user_id: str, name: str = None, description: str = None) -> ResponseModel:
    """
    Updates an existing epic.
    
    Args:
        epic_id (str): The epic ID
        user_id (str): The user ID (for ownership verification)
        name (str, optional): New epic name
        description (str, optional): New epic description
        
    Returns:
        ResponseModel: Response containing the updated epic
    """
    try:
        epic_ref = FIRESTORE_CLIENT.collection("epics").document(epic_id)
        epic_doc = epic_ref.get()
        
        if not epic_doc.exists:
            return ResponseModel(
                success=False,
                message="Epic not found",
                data=None
            )
        
        epic_data = epic_doc.to_dict()
        
        # Check if user owns the epic
        if epic_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this epic",
                data=None
            )
        
        # Build update data
        update_data = {"updated_at": _current_timestamp_iso()}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        
        if len(update_data) == 1:  # Only updated_at
            return ResponseModel(
                success=False,
                message="No fields to update",
                data=None
            )
        
        # Update the document
        epic_ref.update(update_data)
        
        # Get updated epic
        updated_doc = epic_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data["id"] = epic_id
        
        return ResponseModel(
            success=True,
            message="Epic updated successfully",
            data=updated_data
        )
    except Exception as e:
        logging.error(f"Error updating epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating epic: {str(e)}",
            data=None
        )


def update_epic_status(epic_id: str, user_id: str, status: str) -> ResponseModel:
    """
    Updates the status of an epic.
    """
    try:
        epic_ref = FIRESTORE_CLIENT.collection("epics").document(epic_id)
        epic_doc = epic_ref.get()

        if not epic_doc.exists:
            return ResponseModel(
                success=False,
                message="Epic not found",
                data=None
            )

        epic_data = epic_doc.to_dict()
        if epic_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this epic",
                data=None
            )

        update_data = {
            "status": status,
            "updated_at": _current_timestamp_iso(),
        }
        epic_ref.update(update_data)

        updated_doc = epic_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data["id"] = epic_id

        return ResponseModel(
            success=True,
            message="Epic status updated successfully",
            data=updated_data
        )
    except Exception as e:
        logging.error(f"Error updating epic status {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating epic status: {str(e)}",
            data=None
        )


def delete_epic(epic_id: str, user_id: str) -> ResponseModel:
    """
    Deletes a specific epic.
    
    Args:
        epic_id (str): The epic ID
        user_id (str): The user ID (for ownership verification)
        
    Returns:
        ResponseModel: Response confirming deletion
    """
    try:
        epic_ref = FIRESTORE_CLIENT.collection("epics").document(epic_id)
        epic_doc = epic_ref.get()
        
        if not epic_doc.exists:
            return ResponseModel(
                success=False,
                message="Epic not found",
                data=None
            )
        
        epic_data = epic_doc.to_dict()
        
        # Check if user owns the epic
        if epic_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this epic",
                data=None
            )
        
        # Delete the epic
        epic_ref.delete()
        
        return ResponseModel(
            success=True,
            message="Epic deleted successfully",
            data=None
        )
    except Exception as e:
        logging.error(f"Error deleting epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting epic: {str(e)}",
            data=None
        )
