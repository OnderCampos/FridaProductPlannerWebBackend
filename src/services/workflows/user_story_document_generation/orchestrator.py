import logging
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.setup.language_setup import get_default_llm_language, normalize_language
from src.utils.documents.user_story_document import (
    MAX_DOCUMENT_QUESTIONS,
    WIREFRAME_IMAGES_KEY,
    _get_question_text,
    _load_draft,
    _missing_document_keys,
    _normalize_document_payload,
    _now_iso,
    _random_suffix,
    _save_draft,
)
from src.utils.planning.epics import get_epic_by_id
from src.utils.planning.projects import get_project_by_id
from src.utils.planning.user_stories import get_user_story_by_id

logger = logging.getLogger(__name__)


DOCUMENT_AGENT_SECTION_KEYS = [
    "description_and_scope",
    "out_of_scope",
    "preconditions",
    "entry_points",
    "output_points",
    "success_flow",
    "wireframe_mockup",
    "field_description",
    "api_description",
    "acceptance_criteria",
    "test_scenarios",
    "dependencies",
    "benefits",
    "estimation_dev",
]


DOCUMENT_TEMPLATE_SPEC = """
Document title area:
- Metadata labels are rendered separately by the DOCX builder: Jira ID, User Story Name / Requirement, Project Associated, Responsible, Date.
- The user-story narrative table is also rendered separately using the story statement plus fallback values.

Section order in the DOCX:
1. Description and Scope
2. Out of Scope
3. Preconditions
4. Entry Points
5. Output Points*
6. Success Flow
7. Wireframe / Mockup*
8. Field Description
9. API Description*
10. Acceptance Criteria
11. Test Scenarios
12. Dependencies
13. Benefits
14. Estimation Dev.

Formatting expectations:
- Description and Scope: short paragraph or bullets.
- Out of Scope: bullet list when multiple items exist.
- Preconditions: bullet list when multiple items exist.
- Entry Points / Output Points: bullets or short paragraphs.
- Success Flow: numbered steps when possible.
- Wireframe / Mockup: brief note summarizing attached or referenced wireframes.
- Field Description: this is rendered into an 8-column table. Output one row per line using EXACTLY 8 pipe-delimited columns in this order: Element Name | Data Name on the System | Data - Source System | Behavior | Format | Data Type | Example | Visibility when empty.
- API Description: this is rendered into a 6-column table. Output one row per line using EXACTLY 6 pipe-delimited columns in this order: Source System | Target System | Connection Type | Data Format | Technical Viability | Comments.
- Acceptance Criteria: bullet list of testable conditions.
- Test Scenarios: bullet list covering happy path, edge cases, and error cases when known.
- Dependencies: bullet list when multiple dependencies exist.
- Benefits: short paragraph or bullets.
- Estimation Dev.: concise value for a single table cell, for example "8 hours", "2 days", or "N/A".

Critical constraints:
- Use only information supported by the story, current draft, or clarification answers.
- Do not invent missing requirements.
- Use an empty string when a field is still unknown and should trigger follow-up questions.
- Use "N/A" only when the provided information explicitly indicates the field does not apply.
""".strip()


def _get_language_label() -> str:
    return normalize_language(get_default_llm_language(), default="English")


def _load_story_context(
    *,
    user_data: UserData,
    story_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    story_response = get_user_story_by_id(
        story_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not story_response.success:
        return None, None, None, story_response.message

    story = story_response.data or {}
    epic_id = str(story.get("epic_id") or "").strip()
    if not epic_id:
        return None, None, None, "User story is missing epic_id"

    epic_response = get_epic_by_id(epic_id)
    if not epic_response.success:
        return None, None, None, "Epic not found"

    project_id = str(epic_response.data.get("project_id") or "").strip()
    project_response = get_project_by_id(
        project_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    project = project_response.data if project_response and project_response.success else {}
    return story, epic_response.data or {}, project or {}, None


def _trim_story_for_agent(story: Dict[str, Any]) -> Dict[str, Any]:
    def _coerce_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    return {
        "id": str(story.get("id") or "").strip(),
        "epic_id": str(story.get("epic_id") or "").strip(),
        "user_story_id": str(story.get("user_story_id") or "").strip(),
        "user_story": str(story.get("user_story") or story.get("title") or "").strip(),
        "description": str(story.get("description") or "").strip(),
        "assignee": str(story.get("assignee") or "").strip(),
        "acceptanceCriteria": _coerce_list(story.get("acceptanceCriteria") or story.get("acceptance_criteria")),
        "outOfScope": _coerce_list(story.get("outOfScope") or story.get("out_of_scope")),
        "dependencies": _coerce_list(story.get("dependencies")),
        "tshirt_size": str(story.get("tshirt_size") or story.get("tshirtSize") or "").strip(),
        "story_points": story.get("story_points") if story.get("story_points") is not None else story.get("storyPoints"),
        "effortHours": story.get("effortHours") if story.get("effortHours") is not None else story.get("effort_hours"),
    }


def _trim_project_for_agent(project: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(project.get("id") or "").strip(),
        "name": str(project.get("name") or project.get("project_name") or "").strip(),
        "description": str(project.get("description") or "").strip(),
        "project_key": str(project.get("project_key") or "").strip(),
    }


def _trim_epic_for_agent(epic: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(epic.get("id") or "").strip(),
        "name": str(epic.get("name") or epic.get("epic") or "").strip(),
        "description": str(epic.get("description") or "").strip(),
        "labels": list(epic.get("labels") or []),
    }


def _normalize_agent_value(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join([f"- {item}" for item in items])
    if value is None:
        return ""
    return str(value).strip()


def _sanitize_agent_document(candidate: Any, current_document: Dict[str, Any]) -> Dict[str, Any]:
    raw_document = candidate.get("document") if isinstance(candidate, dict) else None
    if not isinstance(raw_document, dict):
        return dict(current_document)

    merged = dict(current_document)
    for key in DOCUMENT_AGENT_SECTION_KEYS:
        if key not in raw_document:
            continue
        next_value = _normalize_agent_value(raw_document.get(key))
        if next_value:
            merged[key] = next_value
            continue
        if not str(merged.get(key) or "").strip():
            merged[key] = next_value

    if not isinstance(merged.get(WIREFRAME_IMAGES_KEY), list):
        merged[WIREFRAME_IMAGES_KEY] = list(current_document.get(WIREFRAME_IMAGES_KEY) or [])
    return merged


def _merge_table_sections(document: Dict[str, Any], table_payload: Any) -> Dict[str, Any]:
    if not isinstance(table_payload, dict):
        return document

    merged = dict(document)
    for key in ("field_description", "api_description"):
        value = _normalize_agent_value(table_payload.get(key))
        if value:
            merged[key] = value
    return merged


def _build_questions(document: Dict[str, Any]) -> List[Dict[str, str]]:
    missing = _missing_document_keys(document)
    ordered_keys: List[str] = []

    # Always offer the wireframe/image attachment step during document generation.
    ordered_keys.append("wireframe_mockup")
    ordered_keys.extend(missing)

    deduped_keys: List[str] = []
    seen = set()
    for key in ordered_keys:
        normalized = str(key or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped_keys.append(normalized)

    return [
        {"key": key, "question": _get_question_text(key)}
        for key in deduped_keys[:MAX_DOCUMENT_QUESTIONS]
    ]


def _normalize_qa_history(raw_history: Any) -> List[Dict[str, str]]:
    history: List[Dict[str, str]] = []
    if not isinstance(raw_history, list):
        return history

    for item in raw_history:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question and not answer:
            continue
        history.append({"key": key, "question": question, "answer": answer})
    return history


async def _compose_document_with_agent(
    *,
    user_data: UserData,
    story: Dict[str, Any],
    epic: Dict[str, Any],
    project: Dict[str, Any],
    current_document: Dict[str, Any],
    qa_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    try:
        from src.intelligence.agents.json_executor import execute_agent
        from src.intelligence.agents.document_generation.user_story_document_generation_agent import (
            USER_STORY_DOCUMENT_GENERATION_AGENT,
        )
    except Exception as exc:
        logger.warning("User story document generation agent not available: %s", exc)
        return current_document

    try:
        raw = execute_agent(
            agent=USER_STORY_DOCUMENT_GENERATION_AGENT,
            prompt_kwargs={
                "template_spec": DOCUMENT_TEMPLATE_SPEC,
                "project": _trim_project_for_agent(project),
                "epic": _trim_epic_for_agent(epic),
                "story": _trim_story_for_agent(story),
                "current_document": current_document,
                "qa_history": qa_history,
                "language": _get_language_label(),
            },
            attempts=2,
            context={"user_data": user_data},
        )
        if raw is None:
            return current_document
        return _sanitize_agent_document(raw, current_document)
    except Exception as exc:
        logger.warning("User story document generation agent failed: %s", exc)
        return current_document


async def _compose_document_tables_with_agent(
    *,
    user_data: UserData,
    story: Dict[str, Any],
    epic: Dict[str, Any],
    project: Dict[str, Any],
    current_document: Dict[str, Any],
    qa_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    try:
        from src.intelligence.agents.json_executor import execute_agent
        from src.intelligence.agents.document_generation.user_story_document_table_agent import (
            USER_STORY_DOCUMENT_TABLE_AGENT,
        )
    except Exception as exc:
        logger.warning("User story document table agent not available: %s", exc)
        return current_document

    try:
        raw = execute_agent(
            agent=USER_STORY_DOCUMENT_TABLE_AGENT,
            prompt_kwargs={
                "project": _trim_project_for_agent(project),
                "epic": _trim_epic_for_agent(epic),
                "story": _trim_story_for_agent(story),
                "current_document": current_document,
                "qa_history": qa_history,
                "language": _get_language_label(),
            },
            attempts=2,
            context={"user_data": user_data},
        )
        return _merge_table_sections(current_document, raw)
    except Exception as exc:
        logger.warning("User story document table agent failed: %s", exc)
        return current_document


async def start_user_story_document_draft(
    *,
    user_data: UserData,
    story_id: str,
) -> ResponseModel:
    story, epic, project, error_message = _load_story_context(user_data=user_data, story_id=story_id)
    if error_message:
        return ResponseModel(success=False, message=error_message, data=None)

    document = _normalize_document_payload(
        (story or {}).get("document"),
        story=story or {},
        epic=epic or {},
        project=project or {},
    )
    document = await _compose_document_with_agent(
        user_data=user_data,
        story=story or {},
        epic=epic or {},
        project=project or {},
        current_document=document,
        qa_history=[],
    )
    document = await _compose_document_tables_with_agent(
        user_data=user_data,
        story=story or {},
        epic=epic or {},
        project=project or {},
        current_document=document,
        qa_history=[],
    )

    questions = _build_questions(document)
    draft_id = f"usdoc_{_random_suffix(10)}"
    status = "questions" if questions else "ready"
    _save_draft(
        draft_id,
        {
            "draft_id": draft_id,
            "story_id": story_id,
            "epic_id": str((story or {}).get("epic_id") or "").strip(),
            "project_id": str((epic or {}).get("project_id") or "").strip(),
            "status": status,
            "questions": questions,
            "missing_keys": [item["key"] for item in questions],
            "qa_history": [],
            "document": document,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    )

    return ResponseModel(
        success=True,
        message="Document draft started",
        data={"draft_id": draft_id, "status": status, "questions": questions},
    )


async def submit_user_story_document_answers(
    *,
    user_data: UserData,
    draft_id: str,
    answers: List[Dict[str, str]],
) -> ResponseModel:
    draft = _load_draft(draft_id)
    if not draft:
        return ResponseModel(success=False, message="Draft not found", data=None)

    story_id = str(draft.get("story_id") or "").strip()
    if not story_id:
        return ResponseModel(success=False, message="Draft is missing story_id", data=None)

    story, epic, project, error_message = _load_story_context(user_data=user_data, story_id=story_id)
    if error_message:
        return ResponseModel(success=False, message=error_message, data=None)

    document = _normalize_document_payload(
        draft.get("document") or {},
        story=story or {},
        epic=epic or {},
        project=project or {},
    )
    qa_history = _normalize_qa_history(draft.get("qa_history"))

    for item in answers or []:
        key = str(item.get("key") or "").strip()
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not key:
            continue
        if answer:
            document[key] = answer
            qa_history.append({"key": key, "question": question, "answer": answer})

    document = await _compose_document_with_agent(
        user_data=user_data,
        story=story or {},
        epic=epic or {},
        project=project or {},
        current_document=document,
        qa_history=qa_history,
    )
    document = await _compose_document_tables_with_agent(
        user_data=user_data,
        story=story or {},
        epic=epic or {},
        project=project or {},
        current_document=document,
        qa_history=qa_history,
    )

    missing = _missing_document_keys(document)
    for key in missing:
        document[key] = document.get(key) or "N/A"

    _save_draft(
        draft_id,
        {
            "status": "ready",
            "questions": [],
            "missing_keys": [],
            "qa_history": qa_history,
            "document": document,
            "updated_at": _now_iso(),
        },
    )

    return ResponseModel(
        success=True,
        message="Document ready",
        data={"draft_id": draft_id, "status": "ready", "questions": []},
    )
