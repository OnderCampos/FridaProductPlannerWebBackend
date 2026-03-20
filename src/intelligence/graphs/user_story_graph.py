import logging
from typing import Any, Dict, List, Optional, TypedDict

from src.intelligence.agents.json_executor import execute_json_agent, parse_json_response
from src.intelligence.agents.user_story_generation.analysis_agent import (
    USER_STORY_ANALYSIS_AGENT,
)
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

    analysis: Dict[str, Any]
    users: List[Any]
    analyzed_functionalities: List[Any]
    functionality_worklist: List[str]
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


def _analyze_node(state: UserStoryGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    epic = state.get("epic") or {}
    project = state.get("project") or {}

    raw_response = USER_STORY_ANALYSIS_AGENT.bind_context({"user_data": user_data}).execute(
        epic=epic,
        project_description=project.get("description", ""),
    )
    analysis = parse_json_response(raw_response)
    if not isinstance(analysis, dict):
        return {
            "analysis": {},
            "users": [],
            "analyzed_functionalities": [],
        }

    users = analysis.get("epic_analysis", {}).get("users", [])
    functionalities = analysis.get("epic_analysis", {}).get("functionalities", [])
    return {
        "analysis": analysis,
        "users": users if isinstance(users, list) else [],
        "analyzed_functionalities": functionalities if isinstance(functionalities, list) else [],
    }


def _brainstorm_node(state: UserStoryGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    epic = state.get("epic") or {}
    project = state.get("project") or {}
    users = state.get("users") or []
    analyzed_functionalities = state.get("analyzed_functionalities") or []

    requested_functionality = state.get("requested_functionality")
    requested_functionalities = state.get("requested_functionalities") or []

    worklist: List[str] = []
    if isinstance(requested_functionality, str) and requested_functionality.strip():
        worklist.append(requested_functionality.strip())
    for item in requested_functionalities:
        value = _normalize_functionality_text(item)
        if value:
            worklist.append(value)
    if not worklist:
        for item in analyzed_functionalities:
            value = _normalize_functionality_text(item)
            if value:
                worklist.append(value)
    if not worklist:
        worklist.append("Core epic functionality")

    dedup_worklist: List[str] = []
    seen = set()
    for item in worklist:
        key = item.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        dedup_worklist.append(item)
    worklist = dedup_worklist

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

    brainstorm_batches: List[List[Dict[str, Any]]] = []
    functionalities_context = analyzed_functionalities or requested_functionalities or worklist
    users_context = users or (project.get("roles", []) if isinstance(project, dict) else [])

    for current_functionality in worklist:
        stories_batch = execute_json_agent(
            agent=USER_STORY_GENERATION_AGENT,
            prompt_kwargs={
                "functionality": current_functionality,
                "users": users_context,
                "epic": epic,
                "functionalities": functionalities_context,
                "template_field_keys": template_field_keys,
                "template_fields_json": template_fields_json,
                "fields_description": fields_description,
            },
            key="user_stories",
            expected_keys=detailed_expected_keys,
            context={"user_data": user_data},
        )
        if isinstance(stories_batch, list) and stories_batch:
            brainstorm_batches.append(stories_batch)

    if not brainstorm_batches:
        return {"error": "Failed to generate user stories during brainstorm step."}

    return {
        "functionality_worklist": worklist,
        "brainstorm_batches": brainstorm_batches,
    }


def _synthesize_node(state: UserStoryGraphState) -> Dict[str, Any]:
    brainstorm_batches = state.get("brainstorm_batches") or []
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
        story_id = str(story.get("user_story_id") or "").strip().lower()
        story_title = str(story.get("user_story") or "").strip().lower()
        dedupe_key = story_id or story_title
        if not dedupe_key:
            dedupe_key = str(story).lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(dict(story))

    for index, story in enumerate(deduped, start=1):
        story["order"] = index

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
        _analyze_node,
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
    graph.add_node("analyze", _analyze_node)
    graph.add_node("brainstorm", _brainstorm_node)
    graph.add_node("synthesize", _synthesize_node)
    graph.add_node("validate", _validate_node)

    graph.add_edge(START, "load_context")
    graph.add_conditional_edges(
        "load_context",
        _route_on_error,
        {"ok": "analyze", "error": END},
    )
    graph.add_conditional_edges(
        "analyze",
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
