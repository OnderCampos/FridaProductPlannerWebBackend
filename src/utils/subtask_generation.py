from typing import Dict, Any, List
import logging
import json
from datetime import datetime, timezone

from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.user_stories import get_user_story_by_id
from src.prompts.subtask_generation import GENERATE_SUBTASKS_PROMPT
from src.utils.general import get_code_block


def _current_timestamp_iso() -> str:
    """Generate current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


def save_subtasks_to_firestore(user_story_id: str, user_id: str, subtasks: List[Dict[str, Any]]) -> ResponseModel:
    """
    Saves subtasks to Firestore.
    
    Args:
        user_story_id (str): The user story ID
        user_id (str): The user ID
        subtasks (List[Dict[str, Any]]): List of subtasks to save
        
    Returns:
        ResponseModel: Response containing the saved subtasks
    """
    try:
        saved_subtasks = []
        now = _current_timestamp_iso()
        
        # First, delete any existing subtasks for this user story
        existing_subtasks = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", user_story_id).get()
        for doc in existing_subtasks:
            doc.reference.delete()
        
        # Save new subtasks
        for subtask_data in subtasks:
            subtask_document = {
                "user_story_id": user_story_id,
                "user_id": user_id,
                "order": subtask_data.get("order", 0),
                "title": subtask_data.get("title", ""),
                "description": subtask_data.get("description", ""),
                "estimated_hours": subtask_data.get("estimated_hours", 0),
                "complexity": subtask_data.get("complexity", "Medium"),
                "dependencies": subtask_data.get("dependencies", []),
                "status": "To Do",  # Default status for new subtasks
                "completed_date": None,  # No completion date initially
                "created_at": now,
                "updated_at": now,
            }
            
            # Add to subtasks collection
            doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(subtask_document)
            firestore_id = doc_ref[1].id
            subtask_document["id"] = firestore_id
            saved_subtasks.append(subtask_document)
        
        return ResponseModel(
            success=True,
            message=f"Successfully saved {len(saved_subtasks)} subtasks",
            data=saved_subtasks
        )
    except Exception as e:
        logging.error(f"Error saving subtasks: {e}")
        return ResponseModel(
            success=False,
            message=f"Error saving subtasks: {str(e)}",
            data=None
        )


def get_subtasks_by_user_story(user_story_id: str, user_id: str) -> ResponseModel:
    """
    Retrieves all subtasks for a user story.
    
    Args:
        user_story_id (str): The user story ID
        user_id (str): The user ID for verification
        
    Returns:
        ResponseModel: Response containing the subtasks ordered by their order field
    """
    try:
        subtasks_docs = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", user_story_id).where("user_id", "==", user_id).get()
        
        subtasks = []
        for doc in subtasks_docs:
            subtask_data = doc.to_dict()
            subtask_data["id"] = doc.id
            subtasks.append(subtask_data)
        
        # Sort by order field
        subtasks.sort(key=lambda x: x.get("order", 0))
        
        return ResponseModel(
            success=True,
            message=f"Retrieved {len(subtasks)} subtasks",
            data=subtasks
        )
    except Exception as e:
        logging.error(f"Error retrieving subtasks: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving subtasks: {str(e)}",
            data=None
        )


def update_subtask_status(subtask_id: str, user_id: str, status: str, completed_date: str = None) -> ResponseModel:
    """
    Updates the status of a subtask.
    
    Args:
        subtask_id (str): The subtask ID
        user_id (str): The user ID for verification
        status (str): The new status
        completed_date (str, optional): Completion date if status is "Done"
        
    Returns:
        ResponseModel: Response containing the updated subtask
    """
    try:
        # Valid status values
        valid_statuses = ["To Do", "In Progress", "Testing", "Done", "Rework", "Blocked"]
        
        if status not in valid_statuses:
            return ResponseModel(
                success=False,
                message="Invalid status value",
                data={"valid_statuses": valid_statuses}
            )
        
        # Get the subtask
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()
        
        if not subtask_doc.exists:
            return ResponseModel(
                success=False,
                message="Subtask not found",
                data=None
            )
        
        subtask_data = subtask_doc.to_dict()
        
        # Verify ownership
        if subtask_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this subtask",
                data=None
            )
        
        # Prepare update data
        now = _current_timestamp_iso()
        update_data = {
            "status": status,
            "updated_at": now
        }
        
        # Handle completion date logic
        if status == "Done":
            update_data["completed_date"] = completed_date if completed_date else now
        else:
            update_data["completed_date"] = None
        
        # Update the subtask
        subtask_ref.update(update_data)
        
        # Get updated subtask
        updated_doc = subtask_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data["id"] = updated_doc.id
        
        return ResponseModel(
            success=True,
            message="Subtask status updated successfully",
            data=updated_data
        )
    except Exception as e:
        logging.error(f"Error updating subtask status: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating subtask status: {str(e)}",
            data=None
        )



async def generate_subtasks_for_user_story(
    user_data: UserData,
    story_id: str,
) -> ResponseModel:
    """
    Generates subtasks for a user story using LLM analysis.

    Args:
        user_data (UserData): User authentication data
        story_id (str): The user story ID

    Returns:
        ResponseModel: Generated subtasks with description, estimated_hours, and complexity
    """
    try:
        # Get the user story
        story_response = get_user_story_by_id(story_id, user_data.get_user_id())
        if not story_response.success:
            return ResponseModel(
                success=False,
                message=f"User story not found: {story_response.message}",
                data=None
            )
        
        story_data = story_response.data
        
        # Prepare additional fields text
        additional_fields_text = ""
        if story_data.get("fields"):
            for field in story_data["fields"]:
                additional_fields_text += f"- {field['name']}: {field['value']}\n"
        
        # Initialize Azure Chat Service without knowledge base
        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        
        # Create subtask generation prompt
        prompt = GENERATE_SUBTASKS_PROMPT.format(
            user_story=story_data.get("user_story", ""),
            description=story_data.get("description", ""),
            epic=story_data.get("epic", ""),
            user_story_id=story_data.get("user_story_id", ""),
            additional_fields=additional_fields_text if additional_fields_text else "No additional fields"
        )
        
        print("[DEBUG] Generating subtasks for user story")
        print(f"[DEBUG] Prompt: {prompt[:200]}...")
        
        # Get subtasks from Azure services (without knowledge base)
        response = await azure_services.simple_completion(prompt)
        
        print(f"[DEBUG] LLM Response: {response[:200]}...")
        
        # Parse the response
        subtasks_json = get_code_block(response)
        if subtasks_json:
            try:
                subtasks_data = json.loads(subtasks_json)
                subtasks = subtasks_data.get("subtasks", [])
                
                if subtasks:
                    # Save subtasks to Firestore
                    print(f"[DEBUG] Saving {len(subtasks)} subtasks to Firestore")
                    save_result = save_subtasks_to_firestore(story_id, user_data.get_user_id(), subtasks)
                    
                    if save_result.success:
                        return ResponseModel(
                            success=True,
                            message=f"Successfully generated and saved {len(subtasks)} subtasks",
                            data={
                                "user_story_id": story_id,
                                "user_story": story_data.get("user_story", ""),
                                "subtasks": save_result.data
                            },
                        )
                    else:
                        # Return generated subtasks even if save failed
                        return ResponseModel(
                            success=True,
                            message=f"Successfully generated {len(subtasks)} subtasks, but failed to save: {save_result.message}",
                            data={
                                "user_story_id": story_id,
                                "user_story": story_data.get("user_story", ""),
                                "subtasks": subtasks
                            },
                        )
                else:
                    return ResponseModel(
                        success=False,
                        message="No subtasks were generated",
                        data=None
                    )
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse subtasks JSON: {e}")
                return ResponseModel(
                    success=False,
                    message=f"Failed to parse subtasks response: {str(e)}",
                    data={"raw_response": response}
                )
        else:
            # Try to parse directly if no code block found
            try:
                subtasks_data = json.loads(response)
                subtasks = subtasks_data.get("subtasks", [])
                
                if subtasks:
                    # Save subtasks to Firestore
                    print(f"[DEBUG] Saving {len(subtasks)} subtasks to Firestore")
                    save_result = save_subtasks_to_firestore(story_id, user_data.get_user_id(), subtasks)
                    
                    if save_result.success:
                        return ResponseModel(
                            success=True,
                            message=f"Successfully generated and saved {len(subtasks)} subtasks",
                            data={
                                "user_story_id": story_id,
                                "user_story": story_data.get("user_story", ""),
                                "subtasks": save_result.data
                            },
                        )
                    else:
                        # Return generated subtasks even if save failed
                        return ResponseModel(
                            success=True,
                            message=f"Successfully generated {len(subtasks)} subtasks, but failed to save: {save_result.message}",
                            data={
                                "user_story_id": story_id,
                                "user_story": story_data.get("user_story", ""),
                                "subtasks": subtasks
                            },
                        )
            except json.JSONDecodeError:
                pass
            
            return ResponseModel(
                success=False,
                message="Could not parse subtasks from LLM response",
                data={"raw_response": response}
            )
            
    except Exception as e:
        logging.error(f"Error in generate_subtasks_for_user_story: {e}")
        print(f"[DEBUG] Exception in generate_subtasks_for_user_story: {e}")
        return ResponseModel(
            success=False, 
            message=f"Error generating subtasks: {str(e)}", 
            data=None
        )
