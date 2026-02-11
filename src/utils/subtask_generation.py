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
        subtask_ids = []

        for doc in subtasks_docs:
            subtask_data = doc.to_dict()
            subtask_data["id"] = doc.id
            subtask_data["sprint_id"] = None
            subtasks.append(subtask_data)
            subtask_ids.append(doc.id)

        if not subtasks:
            return ResponseModel(success=True, message="No subtasks found", data=[])

        # Search all assigments
        # Firestore allow use 'in' to search 30 IDs 
        if subtask_ids:
            # Dividimos en chunks de 30 si tienes muchísimas tareas
            assignments = FIRESTORE_CLIENT.collection('sprint_items') \
                .where('item_type', '==', 'subtask') \
                .where('item_id', 'in', subtask_ids).get()

            # Create a fast map {item_id: sprint_id}
            sprint_map = {doc.to_dict()['item_id']: doc.to_dict()['sprint_id'] for doc in assignments}

            # We cross-reference the data.
            for subtask in subtasks:
                subtask["sprint_id"] = sprint_map.get(subtask["id"])

        # Sort by order field
        subtasks.sort(key=lambda x: x.get("order", 0))

        # Search in sprint_items
        assigment_query = FIRESTORE_CLIENT.collection('sprint_items').where(
            'item_type', '==', 'subtask'
        ).where(
            'item_id', '==', subtask_data
        )

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

def update_subtask_fields(subtask_id: str, user_id: str, update_data: Dict) -> ResponseModel:
    """ 
    Updates fields of a subtask.

    Args:
        subtask_id (str): The subtask ID
        user_id (str): The user ID for verification
        update_data (Dict): Dictionary of fields to update
    Returns:
        ResponseModel: Response containing the updated subtask
    Exception:
        ResponseModel: In case of error, returns failure response
    """
    try:
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()

        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        current_data = subtask_doc.to_dict()

        if current_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        allowed_fields = {"title", "description", "estimated_hours", "complexity", "dependencies"}
        filtered_update = {k: v for k, v in update_data.items() if k in allowed_fields}

        if not filtered_update:
            return ResponseModel(success=False, message="No valid fields to update", data=None)

        # Add updated_at timestamp
        filtered_update["updated_at"] = _current_timestamp_iso()

        # Update the subtask
        subtask_ref.update(filtered_update)

        # Return the updated subtask
        updated_doc = subtask_ref.get()
        final_data = updated_doc.to_dict()
        final_data["id"] = subtask_id

        return ResponseModel(
            success=True,
            message="Subtask updated successfully",
            data=final_data
        )
    except Exception as e:
        logging.error(f"Error updating subtask fields: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating subtask fields: {str(e)}",
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

def create_subtask_for_user_story(story_id: str, user_id: str, subtask_data: Dict[str, Any]) -> ResponseModel:
    """
    Creates a new subtask for a user story.
    
    Args:
        story_id (str): The user story ID
        user_id (str): The user ID
        subtask_data (Dict[str, Any]): Dictionary containing subtask data
    Returns:
        ResponseModel: Response containing the created subtask
    Exception:
        ResponseModel: In case of error, returns failure response
    """
    try:
        # Get existing subtasks to calculate the next 'order'
        existing_subtasks_query = FIRESTORE_CLIENT.collection("subtasks") \
            .where("user_story_id", "==", story_id).get()

        # Calculamos el siguiente número de orden
        # Explicación del codigo:
        # Obtenemos la lista de órdenes actuales de las subtareas existentes
        # Usamos una comprensión de listas para extraer el campo "order" de cada documento, proporcionando un valor predeterminado de 0 si no existe
        # Luego, calculamos el siguiente número de orden tomando el máximo de los órdenes actuales y sumando 1. Si no hay subtareas existentes, 
        # el valor predeterminado para el máximo será 0, por lo que el siguiente orden comenzará en 1.
        # Esto asegura que cada nueva subtask tenga un número de orden único y secuencial dentro de la historia de usuario.
        # Si ya existen subtareas con órdenes 1, 2 y 3, el siguiente orden será 4. Si no existen subtareas, el siguiente orden será 1.
        current_orders = [doc.to_dict().get("order", 0) for doc in existing_subtasks_query]
        next_order = max(current_orders, default=0) + 1

        # Prepare the document
        now = _current_timestamp_iso()
        new_subtask = {
            "user_story_id": story_id,
            "user_id": user_id,
            "title": subtask_data.get("title", ""),
            "order": next_order,
            "description": subtask_data.get("description", ""),
            "estimated_hours": subtask_data.get("estimated_hours", 0),
            "complexity": subtask_data.get("complexity", "Medium"),
            "dependencies": subtask_data.get("dependencies", []),
            "status": subtask_data.get("status", "To Do"), # Default status for new subtasks
            "completed_date": None,  # No completion date initially
            "created_at": now,
            "updated_at": now,
            #"assigned": subtask_data.get("assigned", None)
        }

        # Add to subtasks collection 
        doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(new_subtask)

        # The id generated by firestore
        new_subtask["id"] = doc_ref[1].id

        return ResponseModel(
            success=True,
            message="Subtask created successfully",
            data=new_subtask
        )
    except Exception as e:
        logging.error(f"Error creating subtask: {e}")
        return ResponseModel(
            success=False,
            message=f"Error creating subtask: {str(e)}",
            data=None
        )

def delete_subtasks_by_user_story(subtask_id: str, user_id: str) -> ResponseModel:
    """ 
    Deletes a subtask by its ID.    

    Args:
        subtask_id (str): The subtask ID    
        user_id (str): The user ID for verification
    Returns:
        ResponseModel: Response indicating success or failure of deletion
    Exception:
        ResponseModel: In case of error, returns failure response
    """
    try:
        # Delete the subtask
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()

        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        subtask_data = subtask_doc.to_dict()

        if subtask_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        subtask_ref.delete()

        return ResponseModel(success=True, message="Subtask deleted successfully", data=None)   
    except Exception as e:
        logging.error(f"Error deleting subtask: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting subtask: {str(e)}",
            data=None
        )