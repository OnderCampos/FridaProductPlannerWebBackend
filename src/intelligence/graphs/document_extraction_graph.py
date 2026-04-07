import json
import logging
from typing import Any, Dict, Iterable, List, Optional, TypedDict

from src.intelligence.agents.document_extraction.document_chunk_extraction_agent import (
    DOCUMENT_CHUNK_EXTRACTION_AGENT,
)
from src.intelligence.agents.document_extraction.document_description_agent import (
    DOCUMENT_DESCRIPTION_AGENT,
)
from src.intelligence.agents.document_extraction.document_entity_consolidation_agent import (
    DOCUMENT_ENTITY_CONSOLIDATION_AGENT,
)
from src.intelligence.agents.document_extraction.document_epic_extraction_agent import (
    DOCUMENT_EPIC_EXTRACTION_AGENT,
)
from src.intelligence.agents.document_extraction.document_story_grouping_agent import (
    DOCUMENT_STORY_GROUPING_AGENT,
)
from src.intelligence.agents.document_extraction.document_user_story_extraction_agent import (
    DOCUMENT_USER_STORY_EXTRACTION_AGENT,
)
from src.intelligence.agents.epic_generation.epic_agent import PROJECT_SUMMARY_AGENT
from src.intelligence.agents.json_executor import execute_json_agent, parse_json_response
from src.schemas.user_data import UserData
from src.services.setup.language_setup import get_default_llm_language, normalize_language

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover
    END = None
    START = None
    StateGraph = None

logger = logging.getLogger(__name__)


MAX_LENGTH = 7000
EVIDENCE_CHUNK_SIZE = 3500
EVIDENCE_CHUNK_OVERLAP = 400
CONSOLIDATION_BATCH_SIZE = 4


class DocumentExtractionState(TypedDict, total=False):
    user_data: Optional[UserData]
    project_name: str
    document_text: str
    language: str

    prepared_text: str
    evidence_chunks: List[Dict[str, str]]
    chunk_extractions: List[Dict[str, Any]]
    consolidated_extraction: Dict[str, Any]
    grouped_epics: List[Dict[str, Any]]
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


def _build_overlapping_chunks(text: str, size: int, overlap: int) -> List[Dict[str, str]]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []

    chunk_size = max(500, int(size))
    chunk_overlap = max(0, min(int(overlap), chunk_size // 2))
    step = max(1, chunk_size - chunk_overlap)

    chunks: List[Dict[str, str]] = []
    cursor = 0
    index = 1
    while cursor < len(cleaned):
        chunk_text = cleaned[cursor:cursor + chunk_size].strip()
        if chunk_text:
            chunks.append({"chunk_id": f"chunk_{index:03d}", "text": chunk_text})
            index += 1
        cursor += step
    return chunks


def _slugify(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in (value or "").lower())
    return "_".join(part for part in cleaned.split("_") if part)


def _prepare_text_node(state: DocumentExtractionState) -> Dict[str, Any]:
    project_name = state.get("project_name", "")
    document_text = state.get("document_text", "")
    language = normalize_language(state.get("language"), default=get_default_llm_language())
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

    return {
        "prepared_text": text_for_analysis,
        "evidence_chunks": _build_overlapping_chunks(
            combined_text,
            size=EVIDENCE_CHUNK_SIZE,
            overlap=EVIDENCE_CHUNK_OVERLAP,
        ),
    }


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


def _normalize_bullets(value: Any) -> List[str]:
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
        for prefix in ("- ", "* ", "• ", "â€¢ "):
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
                break
        if line and line[0].isdigit():
            if len(line) >= 3 and line[1] in {".", ")"} and line[2] == " ":
                line = line[3:].strip()
        if line:
            items.append(line)
    return items


def _normalize_number(value: Any, default: float = 0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _description_node(state: DocumentExtractionState) -> Dict[str, Any]:
    user_data = state.get("user_data")
    prepared_text = state.get("prepared_text", "")
    language = normalize_language(state.get("language"), default=get_default_llm_language())

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
    language = normalize_language(state.get("language"), default=get_default_llm_language())
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
            story_id = _slugify(user_story)[:40] or "story"

        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list):
            dependencies = []
        dependencies = [_slugify(str(dep).strip())[:40] for dep in dependencies if str(dep).strip()]

        acceptance_criteria = _normalize_bullets(
            item.get("acceptanceCriteria") or item.get("acceptance_criteria")
        )
        if not acceptance_criteria:
            acceptance_criteria = ["Not provided."]

        out_of_scope = _normalize_bullets(item.get("outOfScope") or item.get("out_of_scope"))
        if not out_of_scope:
            out_of_scope = ["N/A"]

        effort_hours = _normalize_number(item.get("effortHours"), default=0)

        per_epic_order[epic_name] = per_epic_order.get(epic_name, 0) + 1
        order = item.get("order")
        if not isinstance(order, int):
            order = per_epic_order[epic_name]

        normalized.append(
            {
                "epic": epic_name,
                "user_story": user_story,
                "description": description,
                "acceptanceCriteria": acceptance_criteria,
                "outOfScope": out_of_scope,
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
    language = normalize_language(state.get("language"), default=get_default_llm_language())
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


def _normalize_relations(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        relation = str(item.get("relation") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not relation or not target:
            continue
        normalized.append(
            {
                "source_type": str(item.get("source_type") or "").strip(),
                "source": source,
                "relation": relation,
                "target_type": str(item.get("target_type") or "").strip(),
                "target": target,
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return normalized


def _normalize_tasks(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name and not description:
            continue
        normalized.append(
            {
                "name": name or "Untitled Task",
                "description": description,
                "related_user_story": str(item.get("related_user_story") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return normalized


def _normalize_story_candidates(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        user_story = str(item.get("user_story") or "").strip()
        if not user_story:
            continue

        story_key = _slugify(str(item.get("story_key") or "").strip())[:40]
        if not story_key:
            story_key = _slugify(user_story)[:40] or f"story_{index}"
        if story_key in seen:
            continue
        seen.add(story_key)

        roles = _normalize_list(item.get("roles"))
        role = str(item.get("role") or "").strip()
        if role and role.lower() not in {value.lower() for value in roles}:
            roles = [role, *roles]

        normalized.append(
            {
                "story_key": story_key,
                "user_story": user_story,
                "description": str(item.get("description") or "").strip(),
                "acceptanceCriteria": _normalize_bullets(
                    item.get("acceptanceCriteria") or item.get("acceptance_criteria")
                ),
                "outOfScope": _normalize_bullets(item.get("outOfScope") or item.get("out_of_scope")),
                "dependencies": [_slugify(dep)[:40] for dep in _normalize_list(item.get("dependencies"))],
                "effortHours": _normalize_number(item.get("effortHours"), default=0),
                "roles": roles,
                "epic_hint": str(item.get("epic_hint") or "").strip(),
                "technologies": _normalize_list(item.get("technologies")),
                "keywords": _normalize_list(item.get("keywords")),
                "task_hints": _normalize_list(item.get("task_hints")),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return normalized


def _normalize_epic_candidates(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name and not description:
            continue
        key = (name or description).lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "name": name or "Untitled Epic",
                "description": description,
                "roles": _normalize_list(item.get("roles")),
                "technologies": _normalize_list(item.get("technologies")),
                "keywords": _normalize_list(item.get("keywords")),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return normalized


def _normalize_chunk_extraction(raw_payload: Any, chunk_id: str) -> Dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    return {
        "chunk_id": chunk_id,
        "roles": _normalize_list(payload.get("roles")),
        "technical_stack": _normalize_list(payload.get("technical_stack")),
        "epic_candidates": _normalize_epic_candidates(payload.get("epic_candidates")),
        "user_stories": _normalize_story_candidates(payload.get("user_stories")),
        "tasks": _normalize_tasks(payload.get("tasks")),
        "relations": _normalize_relations(payload.get("relations")),
    }


def _has_signal(payload: Dict[str, Any]) -> bool:
    return any(payload.get(key) for key in ("roles", "technical_stack", "epic_candidates", "user_stories"))


def _batched(items: List[Any], size: int) -> Iterable[List[Any]]:
    batch_size = max(1, int(size))
    for index in range(0, len(items), batch_size):
        yield items[index:index + batch_size]


def _merge_story_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in records:
        story_key = str(item.get("story_key") or "").strip()
        if not story_key:
            continue
        if story_key not in merged:
            merged[story_key] = dict(item)
            continue
        existing = merged[story_key]
        if not existing.get("description") and item.get("description"):
            existing["description"] = item.get("description")
        if not existing.get("epic_hint") and item.get("epic_hint"):
            existing["epic_hint"] = item.get("epic_hint")
        if not existing.get("evidence") and item.get("evidence"):
            existing["evidence"] = item.get("evidence")
        existing["roles"] = _normalize_list((existing.get("roles") or []) + (item.get("roles") or []))
        existing["technologies"] = _normalize_list(
            (existing.get("technologies") or []) + (item.get("technologies") or [])
        )
        existing["keywords"] = _normalize_list((existing.get("keywords") or []) + (item.get("keywords") or []))
        existing["task_hints"] = _normalize_list(
            (existing.get("task_hints") or []) + (item.get("task_hints") or [])
        )
        existing["dependencies"] = _normalize_list(
            (existing.get("dependencies") or []) + (item.get("dependencies") or [])
        )
        existing["acceptanceCriteria"] = _normalize_bullets(
            (existing.get("acceptanceCriteria") or []) + (item.get("acceptanceCriteria") or [])
        )
        existing["outOfScope"] = _normalize_bullets(
            (existing.get("outOfScope") or []) + (item.get("outOfScope") or [])
        )
        existing["effortHours"] = max(
            _normalize_number(existing.get("effortHours"), default=0),
            _normalize_number(item.get("effortHours"), default=0),
        )
    return list(merged.values())


def _merge_epic_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in records:
        name = str(item.get("name") or "").strip() or "Untitled Epic"
        key = name.lower()
        if key not in merged:
            merged[key] = dict(item)
            merged[key]["name"] = name
            continue
        existing = merged[key]
        if not existing.get("description") and item.get("description"):
            existing["description"] = item.get("description")
        existing["roles"] = _normalize_list((existing.get("roles") or []) + (item.get("roles") or []))
        existing["technologies"] = _normalize_list(
            (existing.get("technologies") or []) + (item.get("technologies") or [])
        )
        existing["keywords"] = _normalize_list((existing.get("keywords") or []) + (item.get("keywords") or []))
    return list(merged.values())


def _fallback_merge_extractions(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    roles: List[str] = []
    technical_stack: List[str] = []
    epic_candidates: List[Dict[str, Any]] = []
    user_stories: List[Dict[str, Any]] = []
    for payload in payloads:
        roles.extend(payload.get("roles") or [])
        technical_stack.extend(payload.get("technical_stack") or [])
        epic_candidates.extend(payload.get("epic_candidates") or [])
        user_stories.extend(payload.get("user_stories") or [])
    return {
        "roles": _normalize_list(roles),
        "technical_stack": _normalize_list(technical_stack),
        "epic_candidates": _merge_epic_records(epic_candidates),
        "user_stories": _merge_story_records(user_stories),
    }


def _normalize_consolidated_extraction(raw_payload: Any) -> Dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    return {
        "roles": _normalize_list(payload.get("roles")),
        "technical_stack": _normalize_list(payload.get("technical_stack")),
        "epic_candidates": _merge_epic_records(_normalize_epic_candidates(payload.get("epic_candidates"))),
        "user_stories": _merge_story_records(_normalize_story_candidates(payload.get("user_stories"))),
    }


def _normalize_grouped_epics(raw_payload: Any, story_keys: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(raw_payload, dict):
        return []
    raw_epics = raw_payload.get("epics")
    if not isinstance(raw_epics, list):
        return []
    valid_story_keys = set(story_keys)
    normalized: List[Dict[str, Any]] = []
    for item in raw_epics:
        if not isinstance(item, dict):
            continue
        story_refs = [
            _slugify(str(story_key).strip())[:40]
            for story_key in (item.get("story_keys") or [])
            if _slugify(str(story_key).strip())[:40] in valid_story_keys
        ]
        if not story_refs:
            continue
        normalized.append(
            {
                "name": str(item.get("name") or "").strip() or "Untitled Epic",
                "description": str(item.get("description") or "").strip(),
                "roles": _normalize_list(item.get("roles")),
                "technologies": _normalize_list(item.get("technologies")),
                "keywords": _normalize_list(item.get("keywords")),
                "story_keys": story_refs,
            }
        )
    return normalized


def _chunk_extraction_node(state: DocumentExtractionState) -> Dict[str, Any]:
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    chunk_extractions: List[Dict[str, Any]] = []

    for chunk in state.get("evidence_chunks") or []:
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        chunk_text = str(chunk.get("text") or "").strip()
        if not chunk_id or not chunk_text:
            continue

        raw = execute_json_agent(
            agent=DOCUMENT_CHUNK_EXTRACTION_AGENT,
            prompt_kwargs={
                "project_name": state.get("project_name", ""),
                "project_description": state.get("project_description", ""),
                "chunk_id": chunk_id,
                "text": chunk_text,
                "language": language,
            },
            context={"user_data": state.get("user_data")},
        )
        normalized = _normalize_chunk_extraction(raw, chunk_id)
        if _has_signal(normalized):
            chunk_extractions.append(normalized)

    if not chunk_extractions:
        return {"error": "Chunk extraction returned no grounded entities."}
    return {"chunk_extractions": chunk_extractions}


def _consolidation_node(state: DocumentExtractionState) -> Dict[str, Any]:
    current_payloads = list(state.get("chunk_extractions") or [])
    if not current_payloads:
        return {"error": "Cannot consolidate without chunk extractions."}

    language = normalize_language(state.get("language"), default=get_default_llm_language())
    while len(current_payloads) > 1:
        next_round: List[Dict[str, Any]] = []
        for batch in _batched(current_payloads, CONSOLIDATION_BATCH_SIZE):
            raw = execute_json_agent(
                agent=DOCUMENT_ENTITY_CONSOLIDATION_AGENT,
                prompt_kwargs={
                    "project_name": state.get("project_name", ""),
                    "project_description": state.get("project_description", ""),
                    "extractions": batch,
                    "language": language,
                },
                context={"user_data": state.get("user_data")},
            )
            normalized = _normalize_consolidated_extraction(raw)
            next_round.append(normalized if _has_signal(normalized) else _fallback_merge_extractions(batch))
        current_payloads = next_round

    consolidated = _normalize_consolidated_extraction(current_payloads[0])
    if not _has_signal(consolidated):
        consolidated = _fallback_merge_extractions(state.get("chunk_extractions") or [])
    if not consolidated.get("user_stories") and not consolidated.get("epic_candidates"):
        return {"error": "Document consolidation returned no usable entities."}

    return {
        "roles": _normalize_list((state.get("roles") or []) + (consolidated.get("roles") or [])),
        "technical_stack": _normalize_list(
            (state.get("technical_stack") or []) + (consolidated.get("technical_stack") or [])
        ),
        "consolidated_extraction": consolidated,
    }


def _grouping_node(state: DocumentExtractionState) -> Dict[str, Any]:
    consolidated = state.get("consolidated_extraction") or {}
    user_stories = consolidated.get("user_stories") or []
    if not user_stories:
        return {"error": "Cannot group stories because no consolidated user stories were found."}

    raw = execute_json_agent(
        agent=DOCUMENT_STORY_GROUPING_AGENT,
        prompt_kwargs={
            "project_name": state.get("project_name", ""),
            "project_description": state.get("project_description", ""),
            "roles": state.get("roles") or [],
            "technical_stack": state.get("technical_stack") or [],
            "epic_candidates": consolidated.get("epic_candidates") or [],
            "user_stories": user_stories,
            "language": normalize_language(state.get("language"), default=get_default_llm_language()),
        },
        context={"user_data": state.get("user_data")},
    )

    grouped_epics = _normalize_grouped_epics(
        raw,
        story_keys=[str(story.get("story_key") or "") for story in user_stories],
    )
    if not grouped_epics:
        return {"error": "Story grouping returned no epics."}
    return {"grouped_epics": grouped_epics}


def _materialize_grouped_outputs(state: DocumentExtractionState) -> Dict[str, Any]:
    consolidated = state.get("consolidated_extraction") or {}
    grouped_epics = state.get("grouped_epics") or []
    story_map = {
        str(story.get("story_key") or "").strip(): story
        for story in (consolidated.get("user_stories") or [])
        if str(story.get("story_key") or "").strip()
    }
    if not story_map or not grouped_epics:
        return {"epics": [], "user_stories": []}

    final_epics_raw: List[Dict[str, Any]] = []
    final_stories_raw: List[Dict[str, Any]] = []
    assigned_story_keys = set()

    for grouped_epic in grouped_epics:
        story_keys = [
            str(story_key).strip()
            for story_key in (grouped_epic.get("story_keys") or [])
            if str(story_key).strip() in story_map
        ]
        if not story_keys:
            continue

        epic_name = str(grouped_epic.get("name") or "").strip() or "Untitled Epic"
        epic_roles = list(grouped_epic.get("roles") or [])
        epic_technologies = list(grouped_epic.get("technologies") or [])
        epic_keywords = list(grouped_epic.get("keywords") or [])

        for story_key in story_keys:
            story = story_map[story_key]
            epic_roles.extend(story.get("roles") or [])
            epic_technologies.extend(story.get("technologies") or [])
            epic_keywords.extend(story.get("keywords") or [])

        final_epics_raw.append(
            {
                "name": epic_name,
                "description": str(grouped_epic.get("description") or "").strip(),
                "roles": _normalize_list(epic_roles),
                "technologies": _normalize_list(epic_technologies),
                "keywords": _normalize_list(epic_keywords),
            }
        )

        for order, story_key in enumerate(story_keys, start=1):
            assigned_story_keys.add(story_key)
            story = story_map[story_key]
            final_stories_raw.append(
                {
                    "epic": epic_name,
                    "user_story": story.get("user_story"),
                    "description": story.get("description"),
                    "acceptanceCriteria": story.get("acceptanceCriteria") or [],
                    "outOfScope": story.get("outOfScope") or [],
                    "user_story_id": story_key,
                    "order": order,
                    "dependencies": story.get("dependencies") or [],
                    "effortHours": story.get("effortHours"),
                }
            )

    remaining_story_keys = [story_key for story_key in story_map if story_key not in assigned_story_keys]
    if remaining_story_keys and final_epics_raw:
        fallback_epic_name = str(final_epics_raw[0].get("name") or "").strip()
        current_order = sum(1 for story in final_stories_raw if story.get("epic") == fallback_epic_name)
        for story_key in remaining_story_keys:
            current_order += 1
            story = story_map[story_key]
            final_stories_raw.append(
                {
                    "epic": fallback_epic_name,
                    "user_story": story.get("user_story"),
                    "description": story.get("description"),
                    "acceptanceCriteria": story.get("acceptanceCriteria") or [],
                    "outOfScope": story.get("outOfScope") or [],
                    "user_story_id": story_key,
                    "order": current_order,
                    "dependencies": story.get("dependencies") or [],
                    "effortHours": story.get("effortHours"),
                }
            )

    epics = _normalize_epics(final_epics_raw)
    epic_names = [str(epic.get("name") or "").strip() for epic in epics if epic.get("name")]
    user_stories = _normalize_user_stories(final_stories_raw, epic_names)
    return {"epics": epics, "user_stories": user_stories}


def _materialize_outputs_node(state: DocumentExtractionState) -> Dict[str, Any]:
    materialized = _materialize_grouped_outputs(state)
    if materialized.get("epics") and materialized.get("user_stories"):
        return materialized

    legacy_epics = _epics_node(state)
    if legacy_epics.get("error"):
        return legacy_epics

    fallback_state = dict(state)
    fallback_state.update(legacy_epics)
    legacy_stories = _user_stories_node(fallback_state)
    if legacy_stories.get("error"):
        return legacy_stories

    return {
        "epics": legacy_epics.get("epics") or [],
        "user_stories": legacy_stories.get("user_stories") or [],
    }


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
        _chunk_extraction_node,
        _consolidation_node,
        _grouping_node,
        _materialize_outputs_node,
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
    language: Optional[str] = None,
) -> DocumentExtractionState:
    effective_language = normalize_language(language, default=get_default_llm_language())
    initial_state: DocumentExtractionState = {
        "user_data": user_data,
        "project_name": project_name or "",
        "document_text": document_text or "",
        "language": effective_language,
    }

    if StateGraph is None or START is None or END is None:
        return _run_sequential_fallback(initial_state)

    graph = StateGraph(DocumentExtractionState)
    graph.add_node("prepare_text", _prepare_text_node)
    graph.add_node("description", _description_node)
    graph.add_node("chunk_extraction", _chunk_extraction_node)
    graph.add_node("consolidation", _consolidation_node)
    graph.add_node("grouping", _grouping_node)
    graph.add_node("materialize_outputs", _materialize_outputs_node)
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
        {"ok": "chunk_extraction", "error": END},
    )
    graph.add_conditional_edges(
        "chunk_extraction",
        _route_on_error,
        {"ok": "consolidation", "error": END},
    )
    graph.add_conditional_edges(
        "consolidation",
        _route_on_error,
        {"ok": "grouping", "error": END},
    )
    graph.add_conditional_edges(
        "grouping",
        _route_on_error,
        {"ok": "materialize_outputs", "error": END},
    )
    graph.add_conditional_edges(
        "materialize_outputs",
        _route_on_error,
        {"ok": "validate", "error": END},
    )
    graph.add_edge("validate", END)

    app = graph.compile()
    final_state = app.invoke(initial_state, config={"recursion_limit": 40})
    return final_state
