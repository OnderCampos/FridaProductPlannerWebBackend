import logging

from src.intelligence.graphs.epic_graph import run_epic_generation_graph
from src.schemas.function_result import FunctionResult
from src.schemas.user_data import UserData
from src.utils.core.validation_utils import has_expected_epic_structure


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
    
    try:
        graph_state = run_epic_generation_graph(
            user_data=user_data,
            project_name=project_name,
            project_description=project_description,
            language=language,
            use_knowledge_base=use_knowledge_base,
        )

        graph_error = graph_state.get("error")
        if graph_error:
            print(f"[DEBUG] Epic graph failed: {graph_error}")
            return FunctionResult.error_result(
                message="Error generating epics",
                error=str(graph_error),
                metadata={
                    "project_name": project_name,
                    "language": language,
                },
            )

        epics_response = graph_state.get("final_response")
        expected_keys = ["epics", "project_description", "technical_stack", "roles"]
        if epics_response and has_expected_epic_structure(epics_response, expected_keys):
            used_knowledge_base = bool(graph_state.get("used_knowledge_base"))
            success_message = (
                "Epics generated successfully using knowledge base"
                if used_knowledge_base
                else "Epics generated successfully"
            )
            return FunctionResult.success_result(
                message=success_message,
                data=epics_response,
                metadata={
                    "project_name": project_name,
                    "used_knowledge_base": used_knowledge_base,
                    "language": language,
                },
            )

        print("[DEBUG] No valid response received from epic generation graph")
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

