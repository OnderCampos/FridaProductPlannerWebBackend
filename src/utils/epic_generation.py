from typing import Dict, List
import logging

from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.schemas.function_result import FunctionResult
from src.utils.knowledge_base_utils import get_knowledge_base_id_for_user, format_context_by_args
from src.prompts.epic_generation import (
    GENERATE_EPICS_PROMPT,
    SUMMARIZE_PROJECT_DESCRIPTION_PROMPT,
    GENERATE_KEYWORDS_FOR_KBS_PROMPT
)
from src.schemas.user_data import UserData


async def generate_epics(
    user_data: UserData = None,
    project_name: str = "", 
    project_description: str = "",
    language: str = "English",
    use_knowledge_base: bool = False
) -> FunctionResult:
    """
    Generate epics for a project based on project name and description.
    
    Args:
        project_name (str): The name of the project
        project_description (str): The description of the project
        user_data (Dict, optional): User data containing user_id and other details
        language (str): The language for generated epics (default: "English")
        use_knowledge_base (bool): Whether to use knowledge base for context
        
    Returns:
        FunctionResult: A standardized response containing generated epics or error information
    """
    print(f"[DEBUG] Starting epic generation for project: {project_name}")
    MAX_LENGTH = 5000  # Define maximum length for text processing
    
    try:
        # Prepare text for analysis (combine project name and description)
        combined_text = f"Project Name: {project_name}\n\nProject Description: {project_description}"
        
        # Initialize Azure services
        knowledge_base_id = None
        if use_knowledge_base and user_data and user_data.get_user_id():
            knowledge_base_id = get_knowledge_base_id_for_user(user_data.get_user_id())

        azure_services = AzureChatService(LLMOPS_API_KEY, user_data or {}, knowledge_base_id)
        
        print(f"[DEBUG] Generating epics for project: {project_name}")
        print(f"[DEBUG] Combined text size: {len(combined_text)} characters")
        
        # Step 1: Split and merge if text is too large
        text_for_analysis = combined_text
        if len(combined_text) > MAX_LENGTH:
            print("[DEBUG] Text is too large, splitting and merging...")
            parts = [combined_text[i:i+MAX_LENGTH] for i in range(0, len(combined_text), MAX_LENGTH)]
            merged_text = parts[0]
            
            for next_part in parts[1:]:
                    merge_prompt = SUMMARIZE_PROJECT_DESCRIPTION_PROMPT.format(
                        current=merged_text, 
                        next=next_part, 
                        language=language
                    )
                    merged_text = await azure_services.simple_completion(merge_prompt)
            
            text_for_analysis = merged_text
        
        print(f"[DEBUG] Text prepared for analysis. Length: {len(text_for_analysis)} characters")
        
        # Prepare epic generation prompt and expected keys (used in both knowledge base and fallback paths)
        epic_generation_prompt = GENERATE_EPICS_PROMPT.format(
            text=text_for_analysis,
            language=language
        )
        expected_keys = ["epics", "project_description", "technical_stack", "roles"]
        
        epics_response = None
        
        # Step 3: Try with knowledge base if requested
        if use_knowledge_base and knowledge_base_id:
            try:
                questions_prompt = GENERATE_KEYWORDS_FOR_KBS_PROMPT.format(text=text_for_analysis)
                questions = await azure_services.simple_completion(questions_prompt)
                
                # Get answers from knowledge base
                answers = await azure_services.simple_kb_completion(questions)
                
                print(f"[DEBUG] Generated answers from knowledge base: {answers}")
                
                if not answers.is_error():
                    # Use context formatting utility
                    answers_data = answers.get_data()
                    print(f"[DEBUG] Knowledge base answers data: {answers_data}")
                    
                    print(f"[DEBUG] Enhanced prompt with knowledge base context")
                    
                    # Generate epics with knowledge base context
                    response = await azure_services.completion_without_knowledge_base(
                        epic_generation_prompt, None, expected_keys=expected_keys, return_full_response=True
                    )
                    
                    if response:
                        print("[DEBUG] Successfully generated epics using knowledge base")
                        return FunctionResult.success_result(
                            message="Epics generated successfully using knowledge base",
                            data=response,
                            metadata={
                                "project_name": project_name,
                                "used_knowledge_base": True,
                                "language": language
                            }
                        )
                else:
                    print(f"[DEBUG] Knowledge base query failed: {answers.get_error()}")
                        
            except Exception as e:
                print(f"[DEBUG] Error during knowledge base interaction: {str(e)}")
                logging.error(f"Knowledge base error in generate_epics: {e}")
        
        # Step 4: Generate epics without knowledge base (fallback or primary path)
        try:
            epics_response = await azure_services.completion_without_knowledge_base(
                epic_generation_prompt, None, expected_keys=expected_keys, return_full_response=True
            )
            
            if epics_response:
                print("[DEBUG] Successfully generated epics without knowledge base")
                return FunctionResult.success_result(
                    message="Epics generated successfully",
                    data=epics_response,
                    metadata={
                        "project_name": project_name,
                        "used_knowledge_base": False,
                        "language": language
                    }
                )
                
        except Exception as e:
            print(f"[DEBUG] Error during epic generation: {str(e)}")
            logging.error(f"Epic generation error: {e}")
            return FunctionResult.error_result(
                message="Error generating epics",
                error=str(e),
                metadata={
                    "project_name": project_name,
                    "language": language
                }
            )
        
        # Step 5: No valid response received
        print("[DEBUG] No valid response received from epic generation")
        return FunctionResult.error_result(
            message="No valid response received from epic generation",
            error="No data extracted from LLM response",
            metadata={
                "project_name": project_name,
                "language": language
            }
        )
        
    except Exception as e:
        logging.error(f"Unexpected error in generate_epics: {e}")
        return FunctionResult.error_result(
            message="Unexpected error during epic generation",
            error=str(e),
            metadata={
                "project_name": project_name,
                "language": language
            }
        )

