from typing import Dict, List, Optional, Any
import logging

from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.knowledge_base_utils import get_knowledge_base_id_for_user
from src.utils.epics import get_epic_by_id
from src.utils.projects import get_project_by_id
from src.utils.templates import (
    get_selected_template_by_project,
    generate_template_formating,
)
from src.utils.user_stories import create_multiple_user_stories, _current_timestamp_iso
from src.prompts.user_story_generation import (
    IDENTIFY_EPIC_USERS_AND_FUNCTIONALITY_PROMPT,
    GENERATE_EPIC_PROMPT,
)
from src.utils.general import get_code_block
import json


def transform_user_story_to_structured_format(story_data: Dict[str, Any], template_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Transforms a user story from flat format to structured format with separate fields array.
    
    Args:
        story_data (Dict[str, Any]): Raw user story data from LLM
        template_data (Dict[str, Any], optional): Template data for field descriptions
        
    Returns:
        Dict[str, Any]: Structured user story with separate fields array
    """
    # Core user story fields that should not go into the fields array
    core_fields = {
        "epic",
        "user_story",
        "description",
        "user_story_id",
        "id",
        "epic_id",
        "user_id",
        "created_at",
        "createdDate",
        "created_date",
        "updated_at",
        "order",
        "dependencies",
        "effortHours",
        "effort_hours",
        "fields",
    }
    
    # Base structured story
    structured_story = {
        "id": story_data.get("id", ""),
        "epic_id": story_data.get("epic_id", ""),
        "user_id": story_data.get("user_id", ""),
        "created_at": story_data.get("created_at", ""),
        "updated_at": story_data.get("updated_at", ""),
        "createdDate": story_data.get("createdDate", story_data.get("created_at", "")),
        "epic": story_data.get("epic", ""),
        "user_story": story_data.get("user_story", ""),
        "description": story_data.get("description", ""),
        "user_story_id": story_data.get("user_story_id", ""),
        "order": story_data.get("order", 0),
        "dependencies": story_data.get("dependencies", []),
        "effortHours": story_data.get("effortHours", story_data.get("effort_hours", 0)),
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
    
    # Transform template fields into structured format
    for key, value in story_data.items():
        if key not in core_fields and value:  # Skip core fields and empty values
            template_info = template_fields_map.get(key, {})
            field_obj = {
                "name": template_info.get("name", key.replace("_", " ").title()),
                "key": key,
                "value": str(value),
                "description": template_info.get("description", f"Custom field: {key}")
            }
            structured_story["fields"].append(field_obj)
    
    return structured_story


async def generate_analysis(
    user_data: UserData,
    epic_id: str,
) -> ResponseModel:
    """
    Step 1: Analyzes the epic and project description to identify main functionalities and users.

    Args:
        user_data (UserData): User authentication data
        project_description (str): The project description
        epic (str): The epic to analyze
        be_creative (bool): Whether to be creative in analysis
        language (str): Language for the response

    Returns:
        ResponseModel: Analysis results including functionalities, users, and workflows
    """

    try:
        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success:
            return ResponseModel(
                success=False,
                message=f"Epic not found: {epic_response.message}",
                data=None
            )
        
        epic = epic_response.data
        project_response = get_project_by_id(epic["project_id"], user_data.get_user_id())
        if not project_response.success:
            return ResponseModel(
                success=False,
                message=f"Project not found: {project_response.message}",
                data=None
            )
        
        project = project_response.data

        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)

        # Create analysis prompt
        analysis_prompt = IDENTIFY_EPIC_USERS_AND_FUNCTIONALITY_PROMPT.format(
            epic=epic, project_description=project["description"]
        )

        # Get analysis from Azure services
        response = await azure_services.simple_completion(analysis_prompt)

        analysis = get_code_block(response)
        if analysis:
            analysis = json.loads(analysis)
            users = analysis.get("epic_analysis", {}).get("users", [])
            functionalities = analysis.get("epic_analysis", {}).get(
                "functionalities", []
            )

            # Return the populated values
            return ResponseModel(
                success=True,
                message="Successfully identified users and functionalities",
                data={
                    "users": users,
                    "functionalities": functionalities,
                },
            )
        else:
            # Handle case where analysis failed to parse
            print("Warning: Could not parse user analysis as JSON, using empty lists")
            return ResponseModel(
                success=True,
                message="Analysis completed but could not parse structured data",
                data={"users": [], "functionalities": [], "raw_response": response},
            )

    except Exception as e:
        logging.error(f"Error in generate_analysis: {e}")
        return ResponseModel(
            success=False, message=f"Error generating analysis: {str(e)}", data=None
        )


async def generate_user_stories(
    user_data: UserData,
    epic_id: str,
    functionality: Optional[str] = None,
    functionalities: Optional[List] = None,
) -> ResponseModel:
    """
    Step 2: Generates detailed user stories based on analysis from Step 1.

    Args:
        user_data (UserData): User authentication data
        project_description (str): The project description
        epic (str): The epic to create user stories for
        functionality (str, optional): Specific functionality to focus on
        users (List, optional): List of user roles from Step 1
        functionalities (List, optional): List of functionalities from Step 1
        template (str, optional): User story template to use
        use_knowledge_base (bool): Whether to use knowledge base
        be_creative (bool): Whether to be creative
        language (str): Language for the response

    Returns:
        ResponseModel: Generated user stories
    """
    try:
        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success:
            return ResponseModel(
                success=False,
                message=f"Epic not found: {epic_response.message}",
                data=None
            )
        
        epic = epic_response.data
        project_response = get_project_by_id(epic["project_id"], user_data.get_user_id())
        if not project_response.success:
            return ResponseModel(
                success=False,
                message=f"Project not found: {project_response.message}",
                data=None
            )
        
        project = project_response.data

        # Initialize template variables with defaults
        template_field_keys = []
        template_fields_json = ""
        fields_description = ""
        
        selected_template_response = get_selected_template_by_project(
            epic["project_id"],
            user_data.get_user_id()
        )
        print("[DEBUG] Selected template response:", selected_template_response)
        
        # Handle template formatting with proper error handling
        try:
            template_data = selected_template_response.data if selected_template_response.success else {}
            print("[DEBUG] Template data:", template_data)
            if template_data:  # Only process if we have template data
                template_field_keys, template_fields_json, fields_description = generate_template_formating(template_data)
                print("[DEBUG] Template field keys:", template_field_keys)
            else:
                print("[DEBUG] No template data available, using defaults")
        except Exception as template_error:
            print(f"[DEBUG] Error in template formatting: {template_error}")
            # Reset to defaults if template formatting fails
            template_field_keys = []
            template_fields_json = ""
            fields_description = ""

        detailed_expected_keys = [
            "epic",
            "user_story",
            "description",
            "user_story_id",
            "order",
            "dependencies",
            "story_points"
        ] + template_field_keys

        print("[DEBUG] Detailed expected keys:", detailed_expected_keys)

        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        # Include the user analysis in the prompt
        print("Generating user stories for functionality")
        prompt = GENERATE_EPIC_PROMPT.format(
            functionality=functionality,
            users=project.get("roles", []) if isinstance(project, dict) else [],
            epic=epic,
            functionalities=functionalities,
            template_field_keys=template_field_keys,
            template_fields_json=template_fields_json,
            fields_description=fields_description,
        )
        print("[DEBUG] User story generation prompt prepared")
        print("[DEBUG] Prompt content:", prompt)
        response = await azure_services.completion_without_knowledge_base(
            prompt, "user_stories", expected_keys=detailed_expected_keys
        )

        if response:
            print("[DEBUG] User stories generated successfully")
            print(f"[DEBUG] Generated user stories response: {response}")
            
            # Save generated user stories to Firestore with structured format
            print(f"[DEBUG] Saving {len(response)} user stories to Firestore")
            template_data = selected_template_response.data if selected_template_response.success else {}
            save_result = create_multiple_user_stories(epic_id, user_data.get_user_id(), response, template_data)
            
            if save_result.success:
                print("[DEBUG] User stories saved successfully")
                
                # Return the structured user stories directly (already in structured format from storage)
                return ResponseModel(
                    success=True,
                    message=f"User stories generated and saved successfully for functionality",
                    data={
                        "user_stories": save_result.data,
                        "generated_count": len(response)
                    },
                )
            else:
                print(f"[DEBUG] Failed to save user stories: {save_result.message}")
                
                # Transform generated stories to structured format even if saving failed
                structured_stories = []
                template_data = selected_template_response.data if selected_template_response.success else {}
                
                for story in response:
                    # Add missing fields for unsaved stories
                    now = _current_timestamp_iso()
                    story_with_meta = {
                        **story,
                        "epic_id": epic_id,
                        "user_id": user_data.get_user_id(),
                        "id": "",  # No ID since not saved
                        "created_at": now,
                        "updated_at": now,
                        "createdDate": now,
                        "effortHours": story.get("effortHours", story.get("effort_hours", 0)),
                        "storyPoints": story.get("story_points", 0),
                    }
                    structured_story = transform_user_story_to_structured_format(story_with_meta, template_data)
                    structured_stories.append(structured_story)
                
                return ResponseModel(
                    success=True,
                    message=f"User stories generated successfully, but failed to save: {save_result.message}",
                    data={
                        "user_stories": structured_stories,
                        "generated_count": len(response)
                    },
                )
        
        else:
            return ResponseModel(
                success=False,
                message="Failed to generate user stories",
                error="No valid response received from LLM",
            )
    except Exception as e:
        logging.error(f"Error in generate_user_stories: {e}")
        print(f"[DEBUG] Exception in generate_user_stories: {e}")
        return ResponseModel(
            success=False, message=f"Error generating user stories: {str(e)}", data=None
        )
