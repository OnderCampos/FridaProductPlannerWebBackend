import json
import logging
import inspect
from typing import Any, Dict, List, Optional, TypedDict

from src.intelligence.agents.epic_generation.epic_agent import (
    KB_KEYWORDS_AGENT,
    PROJECT_SUMMARY_AGENT,
)
from src.intelligence.agents.epic_generation.epic_role_brainstorm_agent import (
    build_epic_role_brainstorm_agent,
)
from src.intelligence.agents.epic_generation.epic_scope_analysis_agent import (
    EPIC_SCOPE_ANALYSIS_AGENT,
)
from src.intelligence.agents.epic_generation.epic_synthesis_agent import (
    EPIC_SYNTHESIS_AGENT,
)
from src.intelligence.agents.json_executor import execute_agent
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.services.setup.language_setup import get_default_llm_language, normalize_language
from src.utils.ai.knowledge_base_utils import get_knowledge_base_id_for_user
from src.utils.core.validation_utils import has_expected_epic_structure

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - defensive fallback
    END = None
    START = None
    StateGraph = None

logger = logging.getLogger(__name__)


MAX_LENGTH = 5000
FINAL_EXPECTED_KEYS = ["epics", "project_description", "technical_stack", "roles"]


class EpicGraphState(TypedDict, total=False):
    user_data: Optional[UserData]
    project_name: str
    project_description: str
    language: str
    use_knowledge_base: bool

    combined_text: str
    text_for_analysis: str
    scope_analysis: Dict[str, Any]
    role_brainstorms: List[Dict[str, Any]]
    kb_context: str
    used_knowledge_base: bool

    final_response: Dict[str, Any]
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


def _normalize_scope_analysis(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}

    constraints = payload.get("constraints")
    if not isinstance(constraints, dict):
        constraints = {}

    normalized = {
        "objectives": payload.get("objectives") if isinstance(payload.get("objectives"), list) else [],
        "target_users_roles": payload.get("target_users_roles")
        if isinstance(payload.get("target_users_roles"), list)
        else [],
        "constraints": {
            "security": constraints.get("security") if isinstance(constraints.get("security"), list) else [],
            "compliance": constraints.get("compliance") if isinstance(constraints.get("compliance"), list) else [],
            "platform": constraints.get("platform") if isinstance(constraints.get("platform"), list) else [],
            "timeline": constraints.get("timeline") if isinstance(constraints.get("timeline"), list) else [],
            "integrations": constraints.get("integrations") if isinstance(constraints.get("integrations"), list) else [],
        },
        "non_goals": payload.get("non_goals") if isinstance(payload.get("non_goals"), list) else [],
        "capabilities_implied": payload.get("capabilities_implied")
        if isinstance(payload.get("capabilities_implied"), list)
        else [],
        "risks_open_questions": payload.get("risks_open_questions")
        if isinstance(payload.get("risks_open_questions"), list)
        else [],
        "domain_terms": payload.get("domain_terms") if isinstance(payload.get("domain_terms"), list) else [],
    }
    return normalized


def _prepare_input_node(state: EpicGraphState) -> Dict[str, Any]:
    project_name = state.get("project_name", "")
    project_description = state.get("project_description", "")
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    user_data = state.get("user_data")

    combined_text = f"Project Name: {project_name}\n\nProject Description: {project_description}"
    text_for_analysis = combined_text

    if len(combined_text) > MAX_LENGTH:
        parts = [combined_text[i:i + MAX_LENGTH] for i in range(0, len(combined_text), MAX_LENGTH)]
        merged_text = parts[0]
        summary_agent = PROJECT_SUMMARY_AGENT.bind_context({"user_data": user_data})
        for next_part in parts[1:]:
            merged_text = summary_agent.execute(
                current=merged_text,
                next=next_part,
                language=language,
            )
            merged_text = _to_text(merged_text)
        text_for_analysis = merged_text

    return {
        "combined_text": combined_text,
        "text_for_analysis": text_for_analysis,
    }


def _scope_analysis_node(state: EpicGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    text_for_analysis = state.get("text_for_analysis", "")
    language = normalize_language(state.get("language"), default=get_default_llm_language())

    try:
        raw_analysis = execute_agent(
            agent=EPIC_SCOPE_ANALYSIS_AGENT,
            prompt_kwargs={
                "text": text_for_analysis,
                "language": language,
            },
            context={"user_data": user_data},
        )
        scope_analysis = _normalize_scope_analysis(raw_analysis)
        if not scope_analysis.get("target_users_roles"):
            return {
                "error": "Scope analysis did not return target users/roles for brainstorming.",
            }
        return {"scope_analysis": scope_analysis}
    except Exception as exc:
        logger.exception("Scope analysis node failed.")
        return {"error": f"Scope analysis failed: {exc}"}


def _kb_enrichment_node(state: EpicGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    use_knowledge_base = bool(state.get("use_knowledge_base"))
    text_for_analysis = state.get("text_for_analysis", "")

    if not use_knowledge_base or user_data is None:
        return {"kb_context": "", "used_knowledge_base": False}

    try:
        knowledge_base_id = get_knowledge_base_id_for_user(user_data.get_user_id())
    except Exception:
        knowledge_base_id = None

    if not knowledge_base_id:
        return {"kb_context": "", "used_knowledge_base": False}

    try:
        if inspect.iscoroutinefunction(AzureChatService.simple_kb_completion):
            return {"kb_context": "", "used_knowledge_base": False}

        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, knowledge_base_id)
        questions = KB_KEYWORDS_AGENT.bind_context({"user_data": user_data}).execute(
            text=text_for_analysis
        )
        questions = _to_text(questions)

        try:
            answers = azure_services.simple_kb_completion(questions)
        except Exception:
            return {"kb_context": "", "used_knowledge_base": False}

        if answers and not answers.is_error():
            context_payload = answers.get_data()
            return {
                "kb_context": _to_text(context_payload),
                "used_knowledge_base": True,
            }
    except Exception:
        logger.exception("Knowledge base enrichment failed; continuing without KB context.")

    return {"kb_context": "", "used_knowledge_base": False}


def _brainstorm_roles_node(state: EpicGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    project_name = state.get("project_name", "")
    project_description = state.get("project_description", "")
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    kb_context = state.get("kb_context", "")
    scope_analysis = state.get("scope_analysis") or {}

    roles = scope_analysis.get("target_users_roles") or []
    objectives = scope_analysis.get("objectives") or []
    constraints = scope_analysis.get("constraints") or {}
    non_goals = scope_analysis.get("non_goals") or []
    capabilities_implied = scope_analysis.get("capabilities_implied") or []
    risks_open_questions = scope_analysis.get("risks_open_questions") or []
    domain_terms = scope_analysis.get("domain_terms") or []

    role_brainstorms: List[Dict[str, Any]] = []

    for role_profile in roles:
        if not isinstance(role_profile, dict):
            continue
        role_name = str(role_profile.get("role_name") or "Role")
        role_agent = build_epic_role_brainstorm_agent(role_name=role_name)

        brainstorm_response = execute_agent(
            agent=role_agent,
            prompt_kwargs={
                "language": language,
                "project_name": project_name,
                "project_description": project_description,
                "role_profile": role_profile,
                "objectives": objectives,
                "constraints": constraints,
                "non_goals": non_goals,
                "capabilities_implied": capabilities_implied,
                "risks_open_questions": risks_open_questions,
                "domain_terms": domain_terms,
                "kb_context": kb_context,
            },
            context={"user_data": user_data},
        )

        if isinstance(brainstorm_response, dict):
            candidate_epics = brainstorm_response.get("candidate_epics")
            if isinstance(candidate_epics, list) and candidate_epics:
                role_brainstorms.append(
                    {
                        "role_name": brainstorm_response.get("role_name") or role_name,
                        "candidate_epics": candidate_epics,
                    }
                )

    if not role_brainstorms:
        return {"error": "Role brainstorming produced no candidate epics."}

    return {"role_brainstorms": role_brainstorms}


def _synthesis_node(state: EpicGraphState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    project_name = state.get("project_name", "")
    project_description = state.get("project_description", "")
    scope_analysis = state.get("scope_analysis") or {}
    role_brainstorms = state.get("role_brainstorms") or []
    kb_context = state.get("kb_context", "")

    try:
        final_response = execute_agent(
            agent=EPIC_SYNTHESIS_AGENT,
            prompt_kwargs={
                "language": language,
                "project_name": project_name,
                "project_description": project_description,
                "scope_analysis": scope_analysis,
                "role_brainstorms": role_brainstorms,
                "kb_context": kb_context,
            },
            expected_keys=FINAL_EXPECTED_KEYS,
            context={"user_data": user_data},
        )
        if not isinstance(final_response, dict):
            return {"error": "Synthesis agent did not return a valid JSON object."}
        return {"final_response": final_response}
    except Exception as exc:
        logger.exception("Synthesis node failed.")
        return {"error": f"Synthesis failed: {exc}"}


def _validate_output_node(state: EpicGraphState) -> Dict[str, Any]:
    final_response = state.get("final_response")
    if not isinstance(final_response, dict):
        return {"error": "Final response is missing."}
    if not has_expected_epic_structure(final_response, FINAL_EXPECTED_KEYS):
        return {"error": "Final response does not match expected epic structure."}
    return {}


def _route_on_error(state: EpicGraphState) -> str:
    return "error" if state.get("error") else "ok"


def _run_sequential_fallback(initial_state: EpicGraphState) -> EpicGraphState:
    state: EpicGraphState = dict(initial_state)
    for node in (
        _prepare_input_node,
        _scope_analysis_node,
        _kb_enrichment_node,
        _brainstorm_roles_node,
        _synthesis_node,
        _validate_output_node,
    ):
        updates = node(state)
        state.update(updates or {})
        if state.get("error"):
            break
    return state


def run_epic_generation_graph(
    user_data: Optional[UserData],
    project_name: str,
    project_description: str,
    language: Optional[str] = None,
    use_knowledge_base: bool = False,
) -> EpicGraphState:
    effective_language = normalize_language(language, default=get_default_llm_language())
    initial_state: EpicGraphState = {
        "user_data": user_data,
        "project_name": project_name or "",
        "project_description": project_description or "",
        "language": effective_language,
        "use_knowledge_base": bool(use_knowledge_base),
        "kb_context": "",
        "used_knowledge_base": False,
    }

    if StateGraph is None or START is None or END is None:
        return _run_sequential_fallback(initial_state)

    graph = StateGraph(EpicGraphState)
    graph.add_node("prepare_input", _prepare_input_node)
    graph.add_node("scope_analysis", _scope_analysis_node)
    graph.add_node("kb_enrichment", _kb_enrichment_node)
    graph.add_node("brainstorm_roles", _brainstorm_roles_node)
    graph.add_node("synthesis", _synthesis_node)
    graph.add_node("validate_output", _validate_output_node)

    graph.add_edge(START, "prepare_input")
    graph.add_conditional_edges(
        "prepare_input",
        _route_on_error,
        {"ok": "scope_analysis", "error": END},
    )
    graph.add_conditional_edges(
        "scope_analysis",
        _route_on_error,
        {"ok": "kb_enrichment", "error": END},
    )
    graph.add_conditional_edges(
        "kb_enrichment",
        _route_on_error,
        {"ok": "brainstorm_roles", "error": END},
    )
    graph.add_conditional_edges(
        "brainstorm_roles",
        _route_on_error,
        {"ok": "synthesis", "error": END},
    )
    graph.add_conditional_edges(
        "synthesis",
        _route_on_error,
        {"ok": "validate_output", "error": END},
    )
    graph.add_edge("validate_output", END)

    app = graph.compile()
    final_state = app.invoke(initial_state, config={"recursion_limit": 40})
    return final_state
