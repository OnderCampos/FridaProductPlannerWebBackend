import json
import logging
from typing import Any, Dict, List, Optional, TypedDict

from src.intelligence.agents.document_extraction.document_description_agent import (
    DOCUMENT_DESCRIPTION_AGENT,
)
from src.intelligence.agents.document_extraction.document_epic_extraction_agent import (
    DOCUMENT_EPIC_EXTRACTION_AGENT,
)
from src.intelligence.agents.document_extraction.document_user_story_extraction_agent import (
    DOCUMENT_USER_STORY_EXTRACTION_AGENT,
)
from src.intelligence.agents.epic_generation.epic_agent import PROJECT_SUMMARY_AGENT
from src.intelligence.agents.json_executor import execute_json_agent, parse_json_response
from src.schemas.user_data import UserData

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover
    END = None
    START = None
    StateGraph = None

logger = logging.getLogger(__name__)


MAX_LENGTH = 7000


class DocumentExtractionState(TypedDict, total=False):
    user_data: Optional[UserData]
    project_name: str
    document_text: str
    language: str

    prepared_text: str
    project_description: str
    roles: List[str]
    technical_stack: List[str]
    epics: List[Dict[str, Any]]
    user_stories: List[Dict[str, Any]]

    error: str


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _split_text(text: str, size: int) -> List[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] if text else []


def _prepare_text_node(state: DocumentExtractionState) -> Dict[str, Any]:
    project_name = state.get("project_name", "")
    document_text = state.get("document_text", "")
    language = state.get("language", "English")
    user_data = state.get("user_data")

    combined_text = f"Project Name: {project_name}\n\n{document_text}".strip()
    text_for_analysis = combined_text

    if len(combined_text) > MAX_LENGTH:
        parts = _split_text(combined_text, MAX_LENGTH)
        if parts:
            summary_agent = PROJECT_SUMMARY_AGENT.bind_context({"user_data": user_data})
            merged_text = parts[0]
            for next_part in parts[1:]:
                merged_text = summary_agent.execute(
                    current=merged_text,
                    next=next_part,
                    language=language,
                )
                merged_text = _to_text(merged_text)
            text_for_analysis = merged_text

    return {"prepared_text": text_for_analysis}


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = []
    seen = set()
    for item in value:
        text = str(item).strip()
        if text:
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
    return normalized


def _description_node(state: DocumentExtractionState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    prepared_text = state.get("prepared_text", "")
    language = state.get("language", "English")

    raw = execute_json_agent(
        agent=DOCUMENT_DESCRIPTION_AGENT,
        prompt_kwargs={"text": prepared_text, "language": language},
        context={"user_data": user_data},
    )
    parsed = raw if isinstance(raw, dict) else parse_json_response(raw)
    if not isinstance(parsed, dict):
        return {"error": "Description extraction did not return valid JSON."}

    description = str(parsed.get("project_description") or "").strip()
    roles = _normalize_list(parsed.get("roles"))
    tech_stack = _normalize_list(parsed.get("technical_stack"))

    if not description:
        return {"error": "Document did not yield a project description."}

    return {
        "project_description": description,
        "roles": roles,
        "technical_stack": tech_stack,
    }


def _normalize_epics(raw_epics: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_epics, list):
        return []
    normalized = []
    for item in raw_epics:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        roles = _normalize_list(item.get("roles"))
        technologies = _normalize_list(item.get("technologies"))
        keywords = _normalize_list(item.get("keywords"))
        if not name and not description:
            continue
        if not name:
            name = "Untitled Epic"
        normalized.append(
            {
                "name": name,
                "description": description,
                "roles": roles,
                "technologies": technologies,
                "keywords": keywords,
            }
        )
    return normalized


def _epics_node(state: DocumentExtractionState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    prepared_text = state.get("prepared_text", "")
    language = state.get("language", "English")
    project_description = state.get("project_description", "")

    raw_epics = execute_json_agent(
        agent=DOCUMENT_EPIC_EXTRACTION_AGENT,
        prompt_kwargs={
            "text": prepared_text,
            "project_description": project_description,
            "language": language,
        },
        key="epics",
        context={"user_data": user_data},
    )

    epics = _normalize_epics(raw_epics)
    if not epics:
        return {"error": "Epic extraction returned no epics."}
    return {"epics": epics}


def _normalize_user_stories(raw_stories: Any, epic_names: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(raw_stories, list):
        return []

    normalized: List[Dict[str, Any]] = []
    epic_lookup = {name.lower(): name for name in epic_names}
    fallback_epic = epic_names[0] if epic_names else ""

    per_epic_order: Dict[str, int] = {}

    for item in raw_stories:
        if not isinstance(item, dict):
            continue
        epic_name = str(item.get("epic") or "").strip()
        epic_key = epic_name.lower()
        if epic_key not in epic_lookup:
            if not fallback_epic:
                continue
            epic_name = fallback_epic
        else:
            epic_name = epic_lookup[epic_key]

        user_story = str(item.get("user_story") or "").strip()
        description = str(item.get("description") or "").strip()
        if not user_story:
            continue

        story_id = str(item.get("user_story_id") or "").strip()
        if not story_id:
            story_id = user_story.lower().replace(" ", "_")[:40]

        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list):
            dependencies = []
        dependencies = [str(dep).strip() for dep in dependencies if str(dep).strip()]

        effort_hours = item.get("effortHours")
        try:
            effort_hours = float(effort_hours) if effort_hours is not None else 0
        except (TypeError, ValueError):
            effort_hours = 0

        per_epic_order[epic_name] = per_epic_order.get(epic_name, 0) + 1
        order = item.get("order")
        if not isinstance(order, int):
            order = per_epic_order[epic_name]

        normalized.append(
            {
                "epic": epic_name,
                "user_story": user_story,
                "description": description,
                "user_story_id": story_id,
                "order": order,
                "dependencies": dependencies,
                "effortHours": effort_hours,
            }
        )

    return normalized


def _user_stories_node(state: DocumentExtractionState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    prepared_text = state.get("prepared_text", "")
    language = state.get("language", "English")
    epics = state.get("epics") or []
    epic_names = [str(epic.get("name") or "").strip() for epic in epics if epic.get("name")]

    if not epic_names:
        return {"error": "Cannot extract user stories without epic names."}

    raw_stories = execute_json_agent(
        agent=DOCUMENT_USER_STORY_EXTRACTION_AGENT,
        prompt_kwargs={
            "text": prepared_text,
            "epics": epics,
            "language": language,
        },
        key="user_stories",
        context={"user_data": user_data},
    )

    stories = _normalize_user_stories(raw_stories, epic_names)
    if not stories:
        return {"error": "User story extraction returned no stories."}
    return {"user_stories": stories}


def _validate_node(state: DocumentExtractionState) -> Dict[str, Any]:
    if not state.get("project_description"):
        return {"error": "Missing extracted project description."}
    if not state.get("epics"):
        return {"error": "Missing extracted epics."}
    if not state.get("user_stories"):
        return {"error": "Missing extracted user stories."}
    return {}


def _route_on_error(state: DocumentExtractionState) -> str:
    return "error" if state.get("error") else "ok"


def _run_sequential_fallback(initial_state: DocumentExtractionState) -> DocumentExtractionState:
    state: DocumentExtractionState = dict(initial_state)
    for node in (
        _prepare_text_node,
        _description_node,
        _epics_node,
        _user_stories_node,
        _validate_node,
    ):
        updates = node(state)
        state.update(updates or {})
        if state.get("error"):
            break
    return state


def run_document_extraction_graph(
    user_data: Optional[UserData],
    project_name: str,
    document_text: str,
    language: str = "English",
) -> DocumentExtractionState:
    initial_state: DocumentExtractionState = {
        "user_data": user_data,
        "project_name": project_name or "",
        "document_text": document_text or "",
        "language": language or "English",
    }

    if StateGraph is None or START is None or END is None:
        return _run_sequential_fallback(initial_state)

    graph = StateGraph(DocumentExtractionState)
    graph.add_node("prepare_text", _prepare_text_node)
    graph.add_node("description", _description_node)
    graph.add_node("epics", _epics_node)
    graph.add_node("user_stories", _user_stories_node)
    graph.add_node("validate", _validate_node)

    graph.add_edge(START, "prepare_text")
    graph.add_conditional_edges(
        "prepare_text",
        _route_on_error,
        {"ok": "description", "error": END},
    )
    graph.add_conditional_edges(
        "description",
        _route_on_error,
        {"ok": "epics", "error": END},
    )
    graph.add_conditional_edges(
        "epics",
        _route_on_error,
        {"ok": "user_stories", "error": END},
    )
    graph.add_conditional_edges(
        "user_stories",
        _route_on_error,
        {"ok": "validate", "error": END},
    )
    graph.add_edge("validate", END)

    app = graph.compile()
    final_state = app.invoke(initial_state, config={"recursion_limit": 40})
    return final_state
