import os
import logging
import math
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from src.intelligence.agents.document_extraction.document_chunk_extraction_agent import (
    DOCUMENT_CHUNK_EXTRACTION_AGENT,
)
from src.intelligence.agents.document_extraction.document_description_agent import (
    DOCUMENT_DESCRIPTION_AGENT,
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
from src.intelligence.agents.json_executor import execute_agent, parse_json_response
from src.schemas.user_data import UserData
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.services.setup.language_setup import get_default_llm_language, normalize_language
from src.utils.knowledge_bases.embeddings import SofttekOpenAIEmbeddings

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover
    END = None
    START = None
    StateGraph = None

logger = logging.getLogger(__name__)


EVIDENCE_CHUNK_SIZE = 5000
EVIDENCE_CHUNK_OVERLAP = 200
STORY_DEDUP_SIMILARITY = 0.93
STORY_RELATION_SIMILARITY = 0.82


class DocumentExtractionState(TypedDict, total=False):
    user_data: Optional[UserData]
    project_name: str
    document_text: str
    language: str

    evidence_chunks: List[Dict[str, str]]
    chunk_extractions: List[Dict[str, Any]]
    consolidated_extraction: Dict[str, Any]
    story_relationships: List[Dict[str, Any]]
    grouped_epics: List[Dict[str, Any]]
    project_description: str
    roles: List[str]
    technical_stack: List[str]
    epics: List[Dict[str, Any]]
    user_stories: List[Dict[str, Any]]

    error: str


def _is_document_extraction_debug_enabled() -> bool:
    value = str(os.getenv("DOCUMENT_EXTRACTION_DEBUG") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _trace_node_call(node_name: str) -> None:
    if not _is_document_extraction_debug_enabled():
        return
    message = f"Document extraction node called: {node_name}"
    logger.warning(message)
    print(message)
    _append_document_extraction_trace(message)


def _get_document_extraction_logs_dir() -> Path:
    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / "logs"


def _append_document_extraction_trace(message: str) -> None:
    if not _is_document_extraction_debug_enabled():
        return
    try:
        logs_dir = _get_document_extraction_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        trace_path = logs_dir / "document_extraction_trace.log"
        timestamp = datetime.now(timezone.utc).isoformat()
        with trace_path.open("a", encoding="utf-8") as trace_file:
            trace_file.write(f"[{timestamp}] {message}\n")
    except Exception as exc:
        logger.warning("Failed to write document extraction trace log: %s", exc)


def _write_consolidated_story_debug_dump(
    state: DocumentExtractionState,
    stories: List[Dict[str, Any]],
) -> None:
    if not _is_document_extraction_debug_enabled():
        return
    logs_dir = _get_document_extraction_logs_dir()
    latest_path = logs_dir / "document_extraction_consolidated_stories_latest.json"
    history_path = logs_dir / "document_extraction_consolidated_stories.jsonl"

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "project_name": str(state.get("project_name") or "").strip(),
        "story_count": len(stories),
        "stories": stories,
    }

    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with history_path.open("a", encoding="utf-8") as history_file:
            history_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        logger.warning("Wrote consolidated user story debug dump to %s", latest_path)
        print(f"Wrote consolidated user story debug dump to {latest_path}")
        _append_document_extraction_trace(
            f"Wrote consolidated user story debug dump to {latest_path} with {len(stories)} stories."
        )
    except Exception as exc:
        logger.warning("Failed to write consolidated user story debug dump: %s", exc)
        _append_document_extraction_trace(f"Failed to write consolidated user story debug dump: {exc}")


def _require_non_empty_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _require_non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _ensure_node_requirements(
    state: DocumentExtractionState,
    node_name: str,
    checks: Dict[str, bool],
) -> Optional[Dict[str, Any]]:
    missing = [label for label, valid in checks.items() if not valid]
    if not missing:
        return None
    return {"error": f"{node_name} requires: {', '.join(missing)}."}


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


def _build_source_text(state: DocumentExtractionState) -> str:
    project_name = state.get("project_name", "")
    document_text = state.get("document_text", "")
    return f"Project Name: {project_name}\n\n{document_text}".strip()


def _prepare_text_node(state: DocumentExtractionState) -> Dict[str, Any]:
    _trace_node_call("prepare_text")
    combined_text = _build_source_text(state)
    requirements = _ensure_node_requirements(
        state,
        "prepare_text",
        {"document_text or project_name": _require_non_empty_text(combined_text)},
    )
    if requirements:
        return requirements

    return {
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


_IMPLEMENTATION_DETAIL_PATTERNS = [
    r"\bapi\b",
    r"\bendpoints?\b",
    r"\bhttp\b",
    r"\bjson\b",
    r"\bsql\b",
    r"\bdatabase\b",
    r"\bschema\b",
    r"\btable\b",
    r"\bquery\b",
    r"\bbackend\b",
    r"\bfrontend\b",
    r"\breact\b",
    r"\bangular\b",
    r"\bvue\b",
    r"\bpython\b",
    r"\bfastapi\b",
    r"\bflask\b",
    r"\bdjango\b",
    r"\bnode\.?js\b",
    r"\btypescript\b",
    r"\bjavascript\b",
    r"\bclass(?:es)?\b",
    r"\bmethod(?:s)?\b",
    r"\bfunction(?:s)?\b",
    r"\bimplement(?:ation|ed|ing)?\b",
    r"\bcode\b",
    r"\blibrary\b",
    r"\bframework\b",
]


def _looks_like_implementation_detail(text: Any) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.lower()
    for pattern in _IMPLEMENTATION_DETAIL_PATTERNS:
        if re.search(pattern, lowered):
            return True
    return False


def _sanitize_functional_story_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if _looks_like_implementation_detail(value):
        return ""
    return value


def _sanitize_functional_bullets(value: Any) -> List[str]:
    bullets = _normalize_bullets(value)
    sanitized = [bullet for bullet in bullets if not _looks_like_implementation_detail(bullet)]
    return sanitized


def _extract_role_from_user_story(user_story: str) -> str:
    text = str(user_story or "").strip()
    if not text:
        return ""

    match = re.match(r"(?i)^as a[n]?\s+([^,]+),", text)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _build_story_embedding_text(story: Dict[str, Any]) -> str:
    parts = [
        str(story.get("user_story") or "").strip(),
        str(story.get("description") or "").strip(),
        " | ".join(_normalize_bullets(story.get("acceptanceCriteria"))),
        " | ".join(_normalize_list(story.get("dependencies"))),
        " | ".join(_normalize_list(story.get("roles"))),
        " | ".join(_normalize_list(story.get("technologies"))),
        " | ".join(_normalize_list(story.get("keywords"))),
        " | ".join(_normalize_list(story.get("task_hints"))),
        str(story.get("epic_hint") or "").strip(),
        str(story.get("evidence") or "").strip(),
    ]
    return "\n".join(part for part in parts if part)


def _embed_story_records(
    records: List[Dict[str, Any]],
) -> Optional[List[List[float]]]:
    if not records:
        return []

    embeddings_model = _build_embedding_model()
    if embeddings_model is None:
        return None

    story_texts = [_build_story_embedding_text(record) for record in records]
    try:
        return [embeddings_model.embed(text) for text in story_texts]
    except Exception as exc:
        logger.warning("Embedding generation failed for document extraction: %s", exc)
        return None


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _build_embedding_model() -> Optional[SofttekOpenAIEmbeddings]:
    if not LLMOPS_API_KEY:
        return None
    try:
        return SofttekOpenAIEmbeddings(
            model_name="OpenAIEmbeddings",
            api_key=LLMOPS_API_KEY,
        )
    except Exception as exc:
        logger.warning("Could not initialize embeddings model for document extraction: %s", exc)
        return None


def _union_find(items: int) -> Dict[int, int]:
    return {index: index for index in range(items)}


def _find_root(parent: Dict[int, int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _union(parent: Dict[int, int], left: int, right: int) -> None:
    left_root = _find_root(parent, left)
    right_root = _find_root(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def _merge_story_cluster(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {}

    base = dict(records[0])
    base["story_key"] = min(
        (str(record.get("story_key") or "").strip() for record in records if str(record.get("story_key") or "").strip()),
        key=lambda value: (len(value), value),
        default=str(base.get("story_key") or "").strip(),
    )
    if not base["story_key"]:
        base["story_key"] = _slugify(str(base.get("user_story") or "").strip())[:40] or "story"

    for item in records[1:]:
        if not base.get("user_story") and item.get("user_story"):
            base["user_story"] = item.get("user_story")
        if not base.get("description") and item.get("description"):
            base["description"] = item.get("description")
        if not base.get("epic_hint") and item.get("epic_hint"):
            base["epic_hint"] = item.get("epic_hint")
        if not base.get("evidence") and item.get("evidence"):
            base["evidence"] = item.get("evidence")
        base["roles"] = _normalize_list((base.get("roles") or []) + (item.get("roles") or []))
        base["technologies"] = _normalize_list((base.get("technologies") or []) + (item.get("technologies") or []))
        base["keywords"] = _normalize_list((base.get("keywords") or []) + (item.get("keywords") or []))
        base["task_hints"] = _normalize_list((base.get("task_hints") or []) + (item.get("task_hints") or []))
        base["dependencies"] = _normalize_list((base.get("dependencies") or []) + (item.get("dependencies") or []))
        base["acceptanceCriteria"] = _normalize_bullets(
            (base.get("acceptanceCriteria") or []) + (item.get("acceptanceCriteria") or [])
        )
        base["outOfScope"] = _normalize_bullets((base.get("outOfScope") or []) + (item.get("outOfScope") or []))
        base["effortHours"] = max(
            _normalize_number(base.get("effortHours"), default=0),
            _normalize_number(item.get("effortHours"), default=0),
        )
    return base


def _average_embeddings(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    if len(vectors) == 1:
        return list(vectors[0])

    dimensions = len(vectors[0])
    averaged = [0.0] * dimensions
    for vector in vectors:
        if len(vector) != dimensions:
            return []
        for index, value in enumerate(vector):
            averaged[index] += value
    return [value / len(vectors) for value in averaged]


def _dedupe_stories_with_embeddings(
    records: List[Dict[str, Any]],
    embeddings: Optional[List[List[float]]] = None,
) -> tuple[List[Dict[str, Any]], Optional[List[List[float]]]]:
    if not records:
        return [], []

    if embeddings is None:
        embeddings = _embed_story_records(records)

    if embeddings is None:
        logger.warning("Embeddings model unavailable; falling back to story-key deduplication.")
        return _merge_story_records(records), None

    parent = _union_find(len(records))
    for left in range(len(records)):
        for right in range(left + 1, len(records)):
            if records[left].get("story_key") == records[right].get("story_key"):
                _union(parent, left, right)
                continue
            similarity = _cosine_similarity(embeddings[left], embeddings[right])
            if similarity >= STORY_DEDUP_SIMILARITY:
                _union(parent, left, right)

    clusters: Dict[int, List[Dict[str, Any]]] = {}
    cluster_embeddings: Dict[int, List[List[float]]] = {}
    for index, record in enumerate(records):
        root = _find_root(parent, index)
        clusters.setdefault(root, []).append(record)
        cluster_embeddings.setdefault(root, []).append(embeddings[index])

    deduped_stories: List[Dict[str, Any]] = []
    deduped_embeddings: List[List[float]] = []
    for root, cluster in clusters.items():
        deduped_stories.append(_merge_story_cluster(cluster))
        deduped_embeddings.append(_average_embeddings(cluster_embeddings.get(root) or []))

    return deduped_stories, deduped_embeddings


def _build_story_relationships(
    stories: List[Dict[str, Any]],
    embeddings: Optional[List[List[float]]] = None,
) -> List[Dict[str, Any]]:
    if len(stories) < 2:
        return []

    if embeddings is None:
        embeddings = _embed_story_records(stories)

    if embeddings is None:
        return []

    relationships: List[Dict[str, Any]] = []
    for left in range(len(stories)):
        for right in range(left + 1, len(stories)):
            similarity = _cosine_similarity(embeddings[left], embeddings[right])
            if similarity < STORY_RELATION_SIMILARITY:
                continue
            left_key = str(stories[left].get("story_key") or "").strip()
            right_key = str(stories[right].get("story_key") or "").strip()
            if not left_key or not right_key:
                continue
            relationships.append(
                {
                    "source_story_key": left_key,
                    "target_story_key": right_key,
                    "relationship": "similar_to",
                    "similarity": round(similarity, 4),
                }
            )
    return relationships


def _collect_story_records(payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stories: List[Dict[str, Any]] = []
    for payload in payloads:
        stories.extend(payload.get("user_stories") or [])
    return stories


def _derive_roles_from_epics(epics: List[Dict[str, Any]]) -> List[str]:
    roles: List[str] = []
    for epic in epics:
        roles.extend(epic.get("roles") or [])
    return _normalize_list(roles)


def _derive_technical_stack_from_epics(epics: List[Dict[str, Any]]) -> List[str]:
    technologies: List[str] = []
    for epic in epics:
        technologies.extend(epic.get("technologies") or [])
    return _normalize_list(technologies)


def _description_node(state: DocumentExtractionState) -> Dict[str, Any]:
    _trace_node_call("description")
    requirements = _ensure_node_requirements(
        state,
        "description",
        {"epics": _require_non_empty_list(state.get("epics"))},
    )
    if requirements:
        return requirements
    user_data = state.get("user_data")
    epics = state.get("epics") or []
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    epic_inputs = [
        {
            "name": str(epic.get("name") or "").strip(),
            "description": str(epic.get("description") or "").strip(),
        }
        for epic in epics
        if str(epic.get("name") or "").strip() or str(epic.get("description") or "").strip()
    ]
    if not epic_inputs:
        return {"error": "Cannot generate project description without epics."}

    raw = execute_agent(
        agent=DOCUMENT_DESCRIPTION_AGENT,
        prompt_kwargs={"epics": epic_inputs, "language": language},
        context={"user_data": user_data},
    )
    parsed = raw if isinstance(raw, dict) else parse_json_response(raw)
    if not isinstance(parsed, dict):
        return {"error": "Project description synthesis did not return valid JSON."}

    description = str(parsed.get("project_description") or "").strip()
    if not description:
        return {"error": "Epic analysis did not yield a project description."}

    return {"project_description": description}


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
    _trace_node_call("epics")
    source_text = _build_source_text(state)
    requirements = _ensure_node_requirements(
        state,
        "epics",
        {"document_text or project_name": _require_non_empty_text(source_text)},
    )
    if requirements:
        return requirements
    user_data = state.get("user_data")
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    project_description = state.get("project_description", "")

    raw_epics = execute_agent(
        agent=DOCUMENT_EPIC_EXTRACTION_AGENT,
        prompt_kwargs={
            "text": source_text,
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

        user_story = _sanitize_functional_story_text(item.get("user_story"))
        description = _sanitize_functional_story_text(item.get("description"))
        if not user_story:
            continue

        story_id = str(item.get("user_story_id") or "").strip()
        if not story_id:
            story_id = _slugify(user_story)[:40] or "story"

        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list):
            dependencies = []
        dependencies = [_slugify(str(dep).strip())[:40] for dep in dependencies if str(dep).strip()]

        acceptance_criteria = _sanitize_functional_bullets(
            item.get("acceptanceCriteria") or item.get("acceptance_criteria")
        )
        if not acceptance_criteria:
            acceptance_criteria = ["Not provided."]

        out_of_scope = _sanitize_functional_bullets(item.get("outOfScope") or item.get("out_of_scope"))
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
    _trace_node_call("user_stories")
    source_text = _build_source_text(state)
    requirements = _ensure_node_requirements(
        state,
        "user_stories",
        {
            "epics": _require_non_empty_list(state.get("epics")),
            "document_text or project_name": _require_non_empty_text(source_text),
        },
    )
    if requirements:
        return requirements
    user_data = state.get("user_data")
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    epics = state.get("epics") or []
    epic_names = [str(epic.get("name") or "").strip() for epic in epics if epic.get("name")]

    if not epic_names:
        return {"error": "Cannot extract user stories without epic names."}

    raw_stories = execute_agent(
        agent=DOCUMENT_USER_STORY_EXTRACTION_AGENT,
        prompt_kwargs={
            "text": source_text,
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


def _normalize_story_candidates(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        user_story = _sanitize_functional_story_text(item.get("user_story"))
        if not user_story:
            continue

        story_key = _slugify(str(item.get("story_key") or "").strip())[:40]
        if not story_key:
            story_key = _slugify(user_story)[:40] or f"story_{index}"
        if story_key in seen:
            continue

        roles = _normalize_list(item.get("roles"))
        role = str(item.get("role") or "").strip()
        if not role:
            role = _extract_role_from_user_story(user_story)
        if not role:
            continue
        if role and role.lower() not in {value.lower() for value in roles}:
            roles = [role, *roles]
        seen.add(story_key)

        normalized.append(
            {
                "story_key": story_key,
                "user_story": user_story,
                "description": _sanitize_functional_story_text(item.get("description")),
                "acceptanceCriteria": _sanitize_functional_bullets(
                    item.get("acceptanceCriteria") or item.get("acceptance_criteria")
                ),
                "outOfScope": _sanitize_functional_bullets(item.get("outOfScope") or item.get("out_of_scope")),
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


def _normalize_chunk_extraction(raw_payload: Any, chunk_id: str) -> Dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    return {
        "chunk_id": chunk_id,
        "user_stories": _normalize_story_candidates(payload.get("user_stories")),
    }


def _has_signal(payload: Dict[str, Any]) -> bool:
    return bool(payload.get("user_stories"))


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
    _trace_node_call("chunk_extraction")
    requirements = _ensure_node_requirements(
        state,
        "chunk_extraction",
        {"evidence_chunks": _require_non_empty_list(state.get("evidence_chunks"))},
    )
    if requirements:
        return requirements
    language = normalize_language(state.get("language"), default=get_default_llm_language())
    chunk_extractions: List[Dict[str, Any]] = []

    for chunk in state.get("evidence_chunks") or []:
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        chunk_text = str(chunk.get("text") or "").strip()
        if not chunk_id or not chunk_text:
            continue

        raw = execute_agent(
            agent=DOCUMENT_CHUNK_EXTRACTION_AGENT,
            prompt_kwargs={
                "project_name": state.get("project_name", ""),
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
    _trace_node_call("consolidation")
    requirements = _ensure_node_requirements(
        state,
        "consolidation",
        {"chunk_extractions": _require_non_empty_list(state.get("chunk_extractions"))},
    )
    if requirements:
        return requirements
    current_payloads = list(state.get("chunk_extractions") or [])
    user_stories = _collect_story_records(current_payloads)
    if not user_stories:
        return {"error": "Document consolidation requires extracted user stories."}
    if _is_document_extraction_debug_enabled():
        logger.warning("Document extraction collected %s user stories before consolidation.", len(user_stories))
        print(f"Document extraction collected {len(user_stories)} user stories before consolidation.")

    embeddings = _embed_story_records(user_stories)
    consolidated_stories, consolidated_embeddings = _dedupe_stories_with_embeddings(
        user_stories,
        embeddings=embeddings,
    )
    _write_consolidated_story_debug_dump(state, consolidated_stories)
    story_relationships = _build_story_relationships(consolidated_stories, embeddings=consolidated_embeddings)
    consolidated = {
        "roles": [],
        "technical_stack": [],
        "epic_candidates": [],
        "user_stories": consolidated_stories,
    }
    if not consolidated_stories:
        return {"error": "Document consolidation returned no usable entities."}

    return {
        "roles": [],
        "technical_stack": [],
        "consolidated_extraction": consolidated,
        "story_relationships": story_relationships,
    }


def _grouping_node(state: DocumentExtractionState) -> Dict[str, Any]:
    _trace_node_call("grouping")
    consolidated = state.get("consolidated_extraction") or {}
    requirements = _ensure_node_requirements(
        state,
        "grouping",
        {"consolidated user_stories": _require_non_empty_list(consolidated.get("user_stories"))},
    )
    if requirements:
        return requirements
    user_stories = consolidated.get("user_stories") or []

    raw = execute_agent(
        agent=DOCUMENT_STORY_GROUPING_AGENT,
        prompt_kwargs={
            "project_name": state.get("project_name", ""),
            "user_stories": user_stories,
            "story_relationships": state.get("story_relationships") or [],
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
    _trace_node_call("materialize_outputs")
    source_text = _build_source_text(state)
    requirements = _ensure_node_requirements(
        state,
        "materialize_outputs",
        {"document_text or project_name": _require_non_empty_text(source_text)},
    )
    if requirements:
        return requirements
    materialized = _materialize_grouped_outputs(state)
    if materialized.get("epics") and materialized.get("user_stories"):
        epics = materialized.get("epics") or []
        return {
            **materialized,
            "roles": _derive_roles_from_epics(epics),
            "technical_stack": _derive_technical_stack_from_epics(epics),
        }

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
        "roles": _derive_roles_from_epics(legacy_epics.get("epics") or []),
        "technical_stack": _derive_technical_stack_from_epics(legacy_epics.get("epics") or []),
    }


def _validate_node(state: DocumentExtractionState) -> Dict[str, Any]:
    _trace_node_call("validate")
    requirements = _ensure_node_requirements(
        state,
        "validate",
        {
            "project_description": _require_non_empty_text(state.get("project_description")),
            "epics": _require_non_empty_list(state.get("epics")),
            "user_stories": _require_non_empty_list(state.get("user_stories")),
        },
    )
    if requirements:
        return requirements
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
        _chunk_extraction_node,
        _consolidation_node,
        _grouping_node,
        _materialize_outputs_node,
        _description_node,
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
    graph.add_node("chunk_extraction", _chunk_extraction_node)
    graph.add_node("consolidation", _consolidation_node)
    graph.add_node("grouping", _grouping_node)
    graph.add_node("materialize_outputs", _materialize_outputs_node)
    graph.add_node("description", _description_node)
    graph.add_node("validate", _validate_node)

    graph.add_edge(START, "prepare_text")
    graph.add_conditional_edges(
        "prepare_text",
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
        {"ok": "description", "error": END},
    )
    graph.add_conditional_edges(
        "description",
        _route_on_error,
        {"ok": "validate", "error": END},
    )
    graph.add_edge("validate", END)

    app = graph.compile()
    final_state = app.invoke(initial_state, config={"recursion_limit": 40})
    return final_state
