import json
from typing import Dict, List, Optional, Any
import logging

from src.intelligence.agents.json_executor import parse_json_response
from src.intelligence.graphs.user_story_graph import run_user_story_generation_graph
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.utils.planning.epics import get_epic_by_id
from src.utils.planning.projects import get_project_by_id
from src.utils.planning.user_stories import create_multiple_user_stories, _current_timestamp_iso


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
        "acceptanceCriteria",
        "outOfScope",
        "document",
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
        "acceptanceCriteria": story_data.get("acceptanceCriteria", []),
        "outOfScope": story_data.get("outOfScope", []),
        "document": story_data.get("document", {}),
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


def _normalize_string_list(value: Any) -> List[str]:
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
            if len(line) >= 3 and line[1] in {".", ")"} and line[2] == " ":
                line = line[3:].strip()
        if line:
            items.append(line)
    return items


def _normalize_positive_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric > 0 else 0.0


def _normalize_story_points(value: Any) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return numeric if numeric > 0 else 0


async def _enrich_acceptance_and_scope(
    *,
    user_data: UserData,
    epic: Dict[str, Any],
    project: Dict[str, Any],
    stories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not stories:
        return stories

    payload = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        payload.append(
            {
                "user_story_id": str(story.get("user_story_id") or "").strip(),
                "user_story": str(story.get("user_story") or "").strip(),
                "description": str(story.get("description") or "").strip(),
            }
        )

    prompt = f"""
You are a senior QA/product analyst.
For EACH user story below, generate:
1) acceptanceCriteria: 3-6 short, testable bullet points
2) outOfScope: 1-4 bullet points (use ["N/A"] if truly none)

Project context:
{str(project.get("description") or "").strip()}

Epic context:
{json.dumps(epic, ensure_ascii=False)}

User stories (in order):
{json.dumps(payload, ensure_ascii=False)}

Return ONLY valid JSON with EXACTLY {len(payload)} items in the same order:
{{
  "items": [
    {{
      "acceptanceCriteria": ["..."],
      "outOfScope": ["..."]
    }}
  ]
  }}
""".strip()

    parsed: Any = None
    try:
        azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
        raw = await azure.simple_completion(prompt, model_tier="mini")
        parsed = parse_json_response(raw)
    except Exception as exc:
        logging.warning("Failed to enrich acceptanceCriteria/outOfScope: %s", exc)
        parsed = {}

    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or len(items) != len(payload):
        enriched: List[Dict[str, Any]] = []
        for story in stories:
            next_story = dict(story) if isinstance(story, dict) else story
            if isinstance(next_story, dict):
                next_story["acceptanceCriteria"] = next_story.get("acceptanceCriteria") or ["Not provided."]
                next_story["outOfScope"] = next_story.get("outOfScope") or ["N/A"]
            enriched.append(next_story)
        return enriched

    enriched: List[Dict[str, Any]] = []
    for story, extra in zip(stories, items):
        next_story = dict(story) if isinstance(story, dict) else story
        if not isinstance(next_story, dict) or not isinstance(extra, dict):
            enriched.append(next_story)
            continue

        acceptance = _normalize_string_list(extra.get("acceptanceCriteria"))
        if len(acceptance) < 1:
            acceptance = ["Not provided."]
        out_scope = _normalize_string_list(extra.get("outOfScope"))
        if len(out_scope) < 1:
            out_scope = ["N/A"]

        next_story["acceptanceCriteria"] = acceptance
        next_story["outOfScope"] = out_scope
        enriched.append(next_story)

    return enriched


async def enrich_user_story_details(
    *,
    user_data: UserData,
    epic: Dict[str, Any],
    project: Dict[str, Any],
    stories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not stories:
        return stories

    payload: List[Dict[str, str]] = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        payload.append(
            {
                "user_story_id": str(story.get("user_story_id") or "").strip(),
                "user_story": str(story.get("user_story") or "").strip(),
                "description": str(story.get("description") or "").strip(),
                "existing_effort_hours": str(story.get("effortHours") or story.get("effort_hours") or "").strip(),
                "existing_story_points": str(story.get("storyPoints") or story.get("story_points") or "").strip(),
            }
        )

    if len(payload) != len(stories):
        return stories

    prompt = f"""
You are a senior product owner and QA analyst.

For EACH user story below, generate detailed requirement content that can be stored directly in a planning tool.

Return for every item:
- effortHours: a positive numeric effort estimate in hours when one is missing or zero
- storyPoints: a positive integer story point estimate when one is missing or zero
- acceptanceCriteria: 3-6 short, testable bullet points
- outOfScope: 1-4 bullet points (use ["N/A"] if truly none)
- document: an object with these exact string keys:
  - description_and_scope
  - out_of_scope
  - preconditions
  - entry_points
  - output_points
  - success_flow
  - wireframe_mockup
  - field_description
  - api_description
  - acceptance_criteria
  - test_scenarios
  - benefits
  - estimation_dev

Rules:
- Keep the same language as the input/context.
- Be concrete and product-focused, not implementation-heavy.
- Use concise bullet-style text where it helps readability.
- If a detail is unknown, use "N/A".
- If an existing estimate is already provided and greater than zero, preserve it unless it is clearly inconsistent.
- acceptance_criteria in the document must align with acceptanceCriteria.
- out_of_scope in the document must align with outOfScope.
- test_scenarios should cover happy path, edge cases, and failure/validation cases when relevant.
- estimation_dev should be a short estimate note, not a commitment.
- Return ONLY valid JSON.

Project context:
{str(project.get("description") or "").strip()}

Epic context:
{json.dumps(epic, ensure_ascii=False)}

User stories (same order must be preserved):
{json.dumps(payload, ensure_ascii=False)}

Expected JSON format:
{{
  "items": [
    {{
      "effortHours": 6,
      "storyPoints": 3,
      "acceptanceCriteria": ["..."],
      "outOfScope": ["..."],
      "document": {{
        "description_and_scope": "...",
        "out_of_scope": "...",
        "preconditions": "...",
        "entry_points": "...",
        "output_points": "...",
        "success_flow": "...",
        "wireframe_mockup": "...",
        "field_description": "...",
        "api_description": "...",
        "acceptance_criteria": "...",
        "test_scenarios": "...",
        "benefits": "...",
        "estimation_dev": "..."
      }}
    }}
  ]
}}
""".strip()

    parsed: Any = None
    try:
        azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
        raw = await azure.simple_completion(prompt, model_tier="mini")
        parsed = parse_json_response(raw)
    except Exception as exc:
        logging.warning("Failed to enrich user story details: %s", exc)
        parsed = {}

    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or len(items) != len(stories):
        return await _enrich_acceptance_and_scope(
            user_data=user_data,
            epic=epic,
            project=project,
            stories=stories,
        )

    enriched: List[Dict[str, Any]] = []
    for story, extra in zip(stories, items):
        next_story = dict(story) if isinstance(story, dict) else story
        if not isinstance(next_story, dict) or not isinstance(extra, dict):
            enriched.append(next_story)
            continue

        effort_hours = _normalize_positive_float(extra.get("effortHours"))
        if effort_hours <= 0:
            effort_hours = _normalize_positive_float(
                next_story.get("effortHours", next_story.get("effort_hours"))
            )

        story_points = _normalize_story_points(extra.get("storyPoints"))
        if story_points <= 0:
            story_points = _normalize_story_points(
                next_story.get("storyPoints", next_story.get("story_points"))
            )

        acceptance = _normalize_string_list(extra.get("acceptanceCriteria"))
        if not acceptance:
            acceptance = next_story.get("acceptanceCriteria") or ["Not provided."]

        out_scope = _normalize_string_list(extra.get("outOfScope"))
        if not out_scope:
            out_scope = next_story.get("outOfScope") or ["N/A"]

        raw_document = extra.get("document")
        document = raw_document if isinstance(raw_document, dict) else {}

        normalized_document = {
            "description_and_scope": str(document.get("description_and_scope") or next_story.get("description") or "").strip(),
            "out_of_scope": str(document.get("out_of_scope") or "\n".join(f"- {item}" for item in out_scope)).strip(),
            "preconditions": str(document.get("preconditions") or "N/A").strip(),
            "entry_points": str(document.get("entry_points") or "N/A").strip(),
            "output_points": str(document.get("output_points") or "N/A").strip(),
            "success_flow": str(document.get("success_flow") or "N/A").strip(),
            "wireframe_mockup": str(document.get("wireframe_mockup") or "N/A").strip(),
            "field_description": str(document.get("field_description") or "N/A").strip(),
            "api_description": str(document.get("api_description") or "N/A").strip(),
            "acceptance_criteria": str(document.get("acceptance_criteria") or "\n".join(f"- {item}" for item in acceptance)).strip(),
            "test_scenarios": str(document.get("test_scenarios") or "N/A").strip(),
            "benefits": str(document.get("benefits") or "N/A").strip(),
            "estimation_dev": str(document.get("estimation_dev") or "N/A").strip(),
        }

        next_story["effortHours"] = effort_hours
        if story_points > 0:
            next_story["story_points"] = story_points
            next_story["storyPoints"] = story_points
        next_story["acceptanceCriteria"] = acceptance
        next_story["outOfScope"] = out_scope
        next_story["document"] = normalized_document
        enriched.append(next_story)

    return enriched


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

        role_values = epic.get("roles") or project.get("roles") or []
        users = []
        if isinstance(role_values, list):
            for role in role_values:
                role_name = str(role or "").strip()
                if not role_name:
                    continue
                users.append(
                    {
                        "role": role_name,
                        "permissions": "",
                        "interactions": "",
                        "needs": "",
                    }
                )

        return ResponseModel(
            success=True,
            message="Epic analysis compatibility response generated successfully",
            data={
                "users": users,
                "functionalities": [],
            },
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
        graph_state = run_user_story_generation_graph(
            user_data=user_data,
            epic_id=epic_id,
            functionality=functionality,
            functionalities=functionalities,
        )

        graph_error = graph_state.get("error")
        if graph_error:
            return ResponseModel(
                success=False,
                message="Failed to generate user stories",
                error=str(graph_error),
            )

        response = graph_state.get("synthesized_user_stories") or []
        template_data = graph_state.get("template_data") or {}

        if response:
            print("[DEBUG] User stories generated successfully via graph")
            print(f"[DEBUG] Generated user stories response: {response}")

            print(f"[DEBUG] Saving {len(response)} user stories to Firestore")
            save_result = create_multiple_user_stories(
                epic_id,
                user_data.get_user_id(),
                response,
                template_data,
            )

            if save_result.success:
                saved_stories = save_result.data if isinstance(save_result.data, list) else []

                print("[DEBUG] User stories saved successfully")
                return ResponseModel(
                    success=True,
                    message="User stories generated and saved successfully for functionality",
                    data={
                        "user_stories": saved_stories,
                        "generated_count": len(response),
                    },
                )

            print(f"[DEBUG] Failed to save user stories: {save_result.message}")

            structured_stories = []
            for story in response:
                now = _current_timestamp_iso()
                story_with_meta = {
                    **story,
                    "epic_id": epic_id,
                    "user_id": user_data.get_user_id(),
                    "id": "",
                    "created_at": now,
                    "updated_at": now,
                    "createdDate": now,
                    "effortHours": story.get("effortHours", story.get("effort_hours", 0)),
                    "storyPoints": story.get("story_points", 0),
                }
                structured_story = transform_user_story_to_structured_format(
                    story_with_meta,
                    template_data,
                )
                structured_stories.append(structured_story)

            return ResponseModel(
                success=True,
                message=f"User stories generated successfully, but failed to save: {save_result.message}",
                data={
                    "user_stories": structured_stories,
                    "generated_count": len(response),
                },
            )

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
