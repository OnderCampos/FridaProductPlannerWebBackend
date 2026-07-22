import logging
from typing import Any, Dict, List, Optional, TypedDict

from src.intelligence.agents.json_executor import execute_agent, parse_json_response
from src.intelligence.agents.user_story_generation.generation_agent import (
    USER_STORY_GENERATION_AGENT,
)
from src.schemas.user_data import UserData
from src.utils.planning.epics import get_epic_by_id
from src.utils.planning.projects import get_project_by_id
from src.utils.planning.templates import (
    generate_template_formating,
    get_selected_template_by_project,
)

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - defensive fallback
    END = None
    START = None
    StateGraph = None

logger = logging.getLogger(__name__)


class UserStoryGraphState(TypedDict, total=False):
    user_data: UserData
    epic_id: str
    requested_functionality: Optional[str]
    requested_functionalities: Optional[List[Any]]

    epic: Dict[str, Any]
    project: Dict[str, Any]
    template_data: Dict[str, Any]
    template_field_keys: List[str]
    template_fields_json: str
    fields_description: str
    detailed_expected_keys: List[str]

    brainstorm_batches: List[List[Dict[str, Any]]]
    synthesized_user_stories: List[Dict[str, Any]]

    error: str


def _normalize_functionality_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("name", "title", "description", "functionality"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _load_context_node(state: UserStoryGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    epic_id = state.get("epic_id", "")
    if not user_data or not epic_id:
        return {"error": "Missing user_data or epic_id."}

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        return {"error": f"Epic not found: {epic_response.message}"}

    epic = epic_response.data
    project_response = get_project_by_id(epic["project_id"], user_data.get_user_id())
    if not project_response.success:
        return {"error": f"Project not found: {project_response.message}"}
    project = project_response.data

    template_field_keys: List[str] = []
    template_fields_json = ""
    fields_description = ""
    template_data: Dict[str, Any] = {}
    selected_template_response = get_selected_template_by_project(
        epic["project_id"],
        user_data.get_user_id(),
    )
    if selected_template_response.success and isinstance(selected_template_response.data, dict):
        template_data = selected_template_response.data
        try:
            (
                template_field_keys,
                template_fields_json,
                fields_description,
            ) = generate_template_formating(template_data)
        except Exception:
            template_field_keys = []
            template_fields_json = ""
            fields_description = ""

    # Always request acceptance criteria and out-of-scope in the generation graph, even if the
    # selected template does not explicitly include them.
    always_fields = [
        (
            "Acceptance Criteria",
            "acceptance_criteria",
            "3-6 short, testable bullet points (as a markdown list string).",
        ),
        (
            "Out of Scope",
            "out_of_scope",
            "1-4 bullet points (use 'N/A' if truly none; markdown list string).",
        ),
    ]
    existing_keys = {str(k or "").strip() for k in template_field_keys if str(k or "").strip()}
    for name, key, description in always_fields:
        if key in existing_keys:
            continue
        template_field_keys.append(key)
        template_fields_json = (template_fields_json or "") + f'            "{key}": "",\n'
        fields_description = (fields_description or "").rstrip() + f"\n- {name} ({key}): {description}"
        existing_keys.add(key)

    detailed_expected_keys = [
        "epic",
        "user_story",
        "description",
        "user_story_id",
        "order",
        "dependencies",
        "story_points",
        "effortHours",
    ] + template_field_keys

    return {
        "epic": epic,
        "project": project,
        "template_data": template_data,
        "template_field_keys": template_field_keys,
        "template_fields_json": template_fields_json,
        "fields_description": fields_description,
        "detailed_expected_keys": detailed_expected_keys,
    }


def _brainstorm_node(state: UserStoryGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    epic = state.get("epic") or {}
    project = state.get("project") or {}

    detailed_expected_keys = state.get("detailed_expected_keys") or [
        "epic",
        "user_story",
        "description",
        "user_story_id",
        "order",
        "dependencies",
        "story_points",
        "effortHours",
    ]
    template_field_keys = state.get("template_field_keys") or []
    template_fields_json = state.get("template_fields_json", "")
    fields_description = state.get("fields_description", "")

    stories_batch = execute_agent(
        agent=USER_STORY_GENERATION_AGENT,
        prompt_kwargs={
            "epic": epic,
            "project_name": str(project.get("name") or "").strip(),
            "project_description": str(project.get("description") or "").strip(),
            "template_field_keys": template_field_keys,
            "template_fields_json": template_fields_json,
            "fields_description": fields_description,
        },
        key="user_stories",
        attempts=1,
        expected_keys=[],
        context={"user_data": user_data},
    )

    brainstorm_batches: List[List[Dict[str, Any]]] = []
    if isinstance(stories_batch, list) and stories_batch:
        brainstorm_batches.append(stories_batch)

    if not brainstorm_batches:
        return {"error": "Failed to generate user stories during brainstorm step."}

    return {"brainstorm_batches": brainstorm_batches}


def _normalize_story_list(value: Any) -> List[str]:
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
                line = line[len(prefix):].strip()
                break
        if line:
            items.append(line)
    return items


def _normalize_story_document(story: Dict[str, Any], acceptance: List[str], out_of_scope: List[str]) -> Dict[str, str]:
    raw_document = story.get("document")
    document = raw_document if isinstance(raw_document, dict) else {}
    description = str(story.get("description") or "").strip()
    return {
        "description_and_scope": str(document.get("description_and_scope") or description or "N/A").strip(),
        "out_of_scope": str(document.get("out_of_scope") or "\n".join(f"- {item}" for item in out_of_scope) or "N/A").strip(),
        "preconditions": str(document.get("preconditions") or "N/A").strip(),
        "entry_points": str(document.get("entry_points") or "N/A").strip(),
        "output_points": str(document.get("output_points") or "N/A").strip(),
        "success_flow": str(document.get("success_flow") or "N/A").strip(),
        "wireframe_mockup": str(document.get("wireframe_mockup") or "N/A").strip(),
        "field_description": str(document.get("field_description") or "N/A").strip(),
        "api_description": str(document.get("api_description") or "N/A").strip(),
        "acceptance_criteria": str(document.get("acceptance_criteria") or "\n".join(f"- {item}" for item in acceptance) or "N/A").strip(),
        "test_scenarios": str(document.get("test_scenarios") or "N/A").strip(),
        "benefits": str(document.get("benefits") or "N/A").strip(),
        "estimation_dev": str(document.get("estimation_dev") or "N/A").strip(),
    }


def _normalize_generated_story(
    story: Dict[str, Any],
    *,
    epic_name: str,
    order: int,
) -> Optional[Dict[str, Any]]:
    user_story = str(story.get("user_story") or "").strip()
    description = str(story.get("description") or "").strip()
    user_story_id = str(story.get("user_story_id") or "").strip()
    if not user_story or not description or not user_story_id:
        return None

    acceptance = _normalize_story_list(story.get("acceptanceCriteria"))
    if not acceptance:
        acceptance = ["Not provided."]

    out_of_scope = _normalize_story_list(story.get("outOfScope"))
    if not out_of_scope:
        out_of_scope = ["N/A"]

    dependencies = _normalize_story_list(story.get("dependencies"))

    normalized = dict(story)
    normalized["epic"] = str(story.get("epic") or epic_name).strip()
    normalized["user_story"] = user_story
    normalized["description"] = description
    normalized["user_story_id"] = user_story_id
    normalized["order"] = order
    normalized["story_points"] = int(story.get("story_points") or story.get("storyPoints") or 0)
    normalized["effortHours"] = float(story.get("effortHours") or story.get("effort_hours") or 0)
    normalized["dependencies"] = dependencies
    normalized["acceptanceCriteria"] = acceptance
    normalized["outOfScope"] = out_of_scope
    normalized["document"] = _normalize_story_document(normalized, acceptance, out_of_scope)
    return normalized


def _synthesize_node(state: UserStoryGraphState) -> Dict[str, Any]:
    brainstorm_batches = state.get("brainstorm_batches") or []
    epic = state.get("epic") or {}
    epic_name = str(epic.get("name") or epic.get("epic") or "").strip()
    flattened: List[Dict[str, Any]] = []
    for batch in brainstorm_batches:
        if not isinstance(batch, list):
            continue
        for story in batch:
            if isinstance(story, dict):
                flattened.append(story)

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for story in flattened:
        if not isinstance(story, dict):
            continue
        story_id = str(story.get("user_story_id") or "").strip().lower()
        story_title = str(story.get("user_story") or "").strip().lower()
        dedupe_key = story_id or story_title
        if not dedupe_key:
            dedupe_key = str(story).lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_story = _normalize_generated_story(
            dict(story),
            epic_name=epic_name,
            order=len(deduped) + 1,
        )
        if normalized_story is None:
            continue
        deduped.append(normalized_story)

    if not deduped:
        return {"error": "Synthesis produced no user stories."}
    return {"synthesized_user_stories": deduped}


def _validate_node(state: UserStoryGraphState) -> Dict[str, Any]:
    stories = state.get("synthesized_user_stories")
    if not isinstance(stories, list) or not stories:
        return {"error": "No synthesized user stories available."}
    return {}


def _route_on_error(state: UserStoryGraphState) -> str:
    return "error" if state.get("error") else "ok"


def _run_sequential_fallback(initial_state: UserStoryGraphState) -> UserStoryGraphState:
    state: UserStoryGraphState = dict(initial_state)
    for node in (
        _load_context_node,
        _brainstorm_node,
        _synthesize_node,
        _validate_node,
    ):
        updates = node(state)
        state.update(updates or {})
        if state.get("error"):
            break
    return state


def run_user_story_generation_graph(
    user_data: UserData,
    epic_id: str,
    functionality: Optional[str] = None,
    functionalities: Optional[List[Any]] = None,
) -> UserStoryGraphState:
    initial_state: UserStoryGraphState = {
        "user_data": user_data,
        "epic_id": epic_id,
        "requested_functionality": functionality,
        "requested_functionalities": functionalities or [],
    }

    if StateGraph is None or START is None or END is None:
        return _run_sequential_fallback(initial_state)

    graph = StateGraph(UserStoryGraphState)
    graph.add_node("load_context", _load_context_node)
    graph.add_node("brainstorm", _brainstorm_node)
    graph.add_node("synthesize", _synthesize_node)
    graph.add_node("validate", _validate_node)

    graph.add_edge(START, "load_context")
    graph.add_conditional_edges(
        "load_context",
        _route_on_error,
        {"ok": "brainstorm", "error": END},
    )
    graph.add_conditional_edges(
        "brainstorm",
        _route_on_error,
        {"ok": "synthesize", "error": END},
    )
    graph.add_conditional_edges(
        "synthesize",
        _route_on_error,
        {"ok": "validate", "error": END},
    )
    graph.add_edge("validate", END)

    app = graph.compile()
    final_state = app.invoke(initial_state, config={"recursion_limit": 40})
    return final_state
