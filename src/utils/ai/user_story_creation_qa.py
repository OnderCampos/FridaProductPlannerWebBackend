import json
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.utils.core.validation_utils import get_code_block

logger = logging.getLogger(__name__)

USER_STORY_DRAFTS_COLLECTION = "user_story_drafts"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _random_suffix(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(1, length)))


def _safe_json_load(raw: str) -> Optional[Any]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            block = get_code_block(raw)
            if not block:
                return None
            return json.loads(block)
        except Exception:
            return None


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


def _build_context(
    *,
    project_description: str,
    epic_name: str,
    epic_description: str,
    goal: str,
    qa_history: List[Dict[str, str]],
    existing_stories: List[Dict[str, Any]],
) -> str:
    parts: List[str] = []
    if project_description:
        parts.append(f"Project Description:\n{project_description.strip()}")
    parts.append(f"Epic:\n{(epic_name or '').strip()}\n{(epic_description or '').strip()}".strip())
    if goal:
        parts.append(f"New User Story Goal:\n{goal.strip()}")
    if existing_stories:
        preview = []
        for item in existing_stories[:30]:
            story_id = str(item.get("user_story_id") or item.get("id") or "").strip()
            title = str(item.get("user_story") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not title and not desc:
                continue
            line = f"- {story_id}: {title}"
            if desc:
                line += f" — {desc[:180]}"
            preview.append(line)
        if preview:
            parts.append("Existing User Stories:\n" + "\n".join(preview))
    if qa_history:
        lines = ["Clarification Q&A:"]
        for idx, entry in enumerate(qa_history, start=1):
            question = str(entry.get("question") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            if not question and not answer:
                continue
            lines.append(f"{idx}. Q: {question}")
            lines.append(f"   A: {answer}")
        parts.append("\n".join(lines))
    return "\n\n".join([p for p in parts if p]).strip()


async def _generate_questions(
    user_data: UserData,
    context: str,
    *,
    phase: str,
    force_questions: bool,
    min_questions: int,
    max_questions: int = 5,
) -> Dict[str, Any]:
    phase = (phase or "").strip().lower()
    if phase not in {"exploratory", "detail"}:
        phase = "detail"

    if force_questions:
        completion_rule = "Do not mark complete. Ask questions."
    else:
        completion_rule = "If the context is sufficient, mark complete; otherwise ask questions."

    if phase == "exploratory":
        phase_instructions = """
Phase: Exploratory discovery.
Ask broad questions aligned with the User Story / Requirement document sections for ONE user story.
Focus on: description & scope, out of scope, preconditions, success flow, acceptance criteria, and test scenarios.
Avoid implementation details unless required to clarify the scope and testability.
""".strip()
        fallback_questions = [
            "Provide the Description and Scope for this user story (what changes, where it applies, and what is included).",
            "What is explicitly Out of Scope for this user story?",
            "Describe the Success Flow (happy path) step-by-step from entry to completion.",
            "List the Acceptance Criteria as bullet points (what must be true for this story to be done).",
            "List key Test Scenarios (happy path, edge cases, error cases).",
        ]
    else:
        phase_instructions = """
Phase: Detail specification.
Ask targeted follow-up questions based on the prior answers to make the user story testable.
Focus on: preconditions, entry points, output points, field descriptions/business rules, wireframes/mockups, and any remaining acceptance/test details.
Avoid repeating questions already answered.
""".strip()
        fallback_questions = [
            "List the Preconditions required before the user can use this functionality (roles, data, setup).",
            "What are the Entry Points (inputs/screens/actions) required to use this feature?",
            "What are the Output Points (screens, fields, messages, system effects) produced by this feature?",
            "List the key fields and business rules involved (Field Description), or say N/A.",
            "Add any missing Acceptance Criteria or Test Scenarios that are still unclear.",
        ]

    prompt = f"""
You are a senior product analyst helping write ONE new user story for the epic.
Ask clarification questions to remove ambiguity and make the story testable.
{completion_rule}

{phase_instructions}

Context:
{context}

Return ONLY valid JSON with the following structure:
{{
  "complete": boolean,
  "questions": ["Question 1", "Question 2"]
}}

Rules:
- Ask between {max(1, int(min_questions))} and {max(1, int(max_questions))} questions.
- Questions must be concrete, specific, and focused on missing/unclear details for ONE user story.
- If complete is true, questions must be an empty array.
""".strip()

    azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
    raw = await azure.simple_completion(prompt)
    parsed = _safe_json_load(raw)
    if not isinstance(parsed, dict):
        return {"complete": False, "questions": []}

    complete = bool(parsed.get("complete"))
    questions = parsed.get("questions", [])
    if not isinstance(questions, list):
        questions = []
    questions = [str(q).strip() for q in questions if str(q).strip()]

    # De-duplicate while preserving order
    deduped: List[str] = []
    seen = set()
    for q in questions:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
    questions = deduped[: max(1, int(max_questions))]

    if force_questions:
        complete = False

    if complete:
        questions = []
        return {"complete": True, "questions": []}

    min_questions = max(1, int(min_questions))
    if len(questions) < min_questions:
        for q in fallback_questions:
            if len(questions) >= min_questions:
                break
            if q.lower() in seen:
                continue
            seen.add(q.lower())
            questions.append(q)

    questions = questions[: max(1, int(max_questions))]
    if not questions:
        questions = [fallback_questions[0]]

    return {"complete": False, "questions": questions}


async def _generate_story_draft(user_data: UserData, context: str) -> Dict[str, Any]:
    prompt = f"""
You are a senior product manager. Based on the context, produce ONE atomic user story.
Also produce a document-aligned breakdown matching the User Story / Requirement template sections.

Context:
{context}

Return ONLY valid JSON for a single object with this shape:
{{
  "user_story": "As a ... I want ... so that ...",
  "description": "Short description (1-6 bullet points or a short paragraph).",
  "user_story_id": "Optional short ID like US-123 (omit if unsure).",
  "order": 0,
  "dependencies": ["Optional IDs or titles this depends on"],
  "effortHours": 0,
  "story_points": 0,
  "acceptanceCriteria": ["Acceptance criterion 1", "Acceptance criterion 2"],
  "outOfScope": ["Out of scope item 1", "Out of scope item 2"],
  "document": {{
    "description_and_scope": "Description and Scope (string)",
    "out_of_scope": "Out of Scope (string)",
    "preconditions": "Preconditions (string)",
    "entry_points": "Entry Points (string, can be N/A)",
    "output_points": "Output Points (string, can be N/A)",
    "success_flow": "Success Flow (string)",
    "wireframe_mockup": "Wireframe / Mockup (string, can be N/A)",
    "field_description": "Field Description (string, can be N/A)",
    "api_description": "API Description (string, can be N/A)",
    "acceptance_criteria": "Acceptance Criteria (string, preferably bullets)",
    "test_scenarios": "Test Scenarios (string, preferably bullets)",
    "benefits": "Benefits (string, can be N/A)",
    "estimation_dev": "Estimation Dev (string, can be N/A)"
  }}
}}

Rules:
- Keep the story small and testable.
- If order/effort/story_points are unknown, set them to 0.
- dependencies must be an array (can be empty).
- For any document section that cannot be determined, use an empty string or 'N/A'.
- acceptanceCriteria must contain at least 3 items.
- outOfScope must contain at least 1 item (use \"N/A\" if truly none).
""".strip()

    azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
    raw = await azure.simple_completion(prompt)
    parsed = _safe_json_load(raw)
    if not isinstance(parsed, dict):
        return {}

    draft: Dict[str, Any] = {
        "user_story": str(parsed.get("user_story") or "").strip(),
        "description": str(parsed.get("description") or "").strip(),
        "user_story_id": str(parsed.get("user_story_id") or "").strip(),
        "order": parsed.get("order", 0),
        "dependencies": parsed.get("dependencies", []),
        "effortHours": parsed.get("effortHours", 0),
        "story_points": parsed.get("story_points", 0),
    }

    raw_document = parsed.get("document")
    document: Dict[str, Any] = raw_document if isinstance(raw_document, dict) else {}
    draft["document"] = {
        "description_and_scope": str(document.get("description_and_scope") or "").strip(),
        "out_of_scope": str(document.get("out_of_scope") or "").strip(),
        "preconditions": str(document.get("preconditions") or "").strip(),
        "entry_points": str(document.get("entry_points") or "").strip(),
        "output_points": str(document.get("output_points") or "").strip(),
        "success_flow": str(document.get("success_flow") or "").strip(),
        "wireframe_mockup": str(document.get("wireframe_mockup") or "").strip(),
        "field_description": str(document.get("field_description") or "").strip(),
        "api_description": str(document.get("api_description") or "").strip(),
        "acceptance_criteria": str(document.get("acceptance_criteria") or "").strip(),
        "test_scenarios": str(document.get("test_scenarios") or "").strip(),
        "benefits": str(document.get("benefits") or "").strip(),
        "estimation_dev": str(document.get("estimation_dev") or "").strip(),
    }

    draft["acceptanceCriteria"] = _normalize_string_list(
        parsed.get("acceptanceCriteria") or draft["document"].get("acceptance_criteria") or document.get("acceptance_criteria")
    )
    if not draft["acceptanceCriteria"]:
        draft["acceptanceCriteria"] = ["Not provided."]

    draft["outOfScope"] = _normalize_string_list(
        parsed.get("outOfScope") or draft["document"].get("out_of_scope") or document.get("out_of_scope")
    )
    if not draft["outOfScope"]:
        draft["outOfScope"] = ["N/A"]

    if not isinstance(draft.get("dependencies"), list):
        draft["dependencies"] = []
    try:
        draft["order"] = int(draft.get("order") or 0)
    except Exception:
        draft["order"] = 0
    try:
        draft["effortHours"] = float(draft.get("effortHours") or 0)
    except Exception:
        draft["effortHours"] = 0
    try:
        draft["story_points"] = int(draft.get("story_points") or 0)
    except Exception:
        draft["story_points"] = 0

    return draft


def _load_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    doc = FIRESTORE_CLIENT.collection(USER_STORY_DRAFTS_COLLECTION).document(draft_id).get()
    return doc.to_dict() if doc.exists else None


def _save_draft(draft_id: str, payload: Dict[str, Any]) -> None:
    FIRESTORE_CLIENT.collection(USER_STORY_DRAFTS_COLLECTION).document(draft_id).set(payload, merge=True)


async def start_user_story_qa(
    *,
    user_data: UserData,
    epic_id: str,
    project_description: str,
    epic_name: str,
    epic_description: str,
    goal: str,
    existing_stories: List[Dict[str, Any]],
) -> ResponseModel:
    draft_id = f"usd_{_random_suffix(10)}"
    base_payload = {
        "draft_id": draft_id,
        "epic_id": epic_id,
        "project_description": project_description or "",
        "epic_name": epic_name or "",
        "epic_description": epic_description or "",
        "goal": goal or "",
        "qa_history": [],
        "loop_count": 0,
        "qa_round": 1,
        "status": "questions",
        "last_questions": [],
        "story_draft": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    _save_draft(draft_id, base_payload)

    context = _build_context(
        project_description=project_description,
        epic_name=epic_name,
        epic_description=epic_description,
        goal=goal,
        qa_history=[],
        existing_stories=existing_stories,
    )
    # Round 1 is always exploratory Q&A.
    question_result = await _generate_questions(
        user_data,
        context,
        phase="exploratory",
        force_questions=True,
        min_questions=3,
        max_questions=5,
    )
    questions = question_result.get("questions", []) or []

    _save_draft(
        draft_id,
        {
            "last_questions": questions,
            "updated_at": _now_iso(),
        },
    )

    return ResponseModel(
        success=True,
        message="Clarification started",
        data={
            "draft_id": draft_id,
            "status": "questions",
            "questions": questions,
            "loop_count": 0,
            "qa_round": 1,
            "story_draft": None,
        },
    )


async def submit_user_story_qa_answers(
    *,
    user_data: UserData,
    draft_id: str,
    answers: List[Dict[str, str]],
    existing_stories: List[Dict[str, Any]],
) -> ResponseModel:
    draft = _load_draft(draft_id)
    if not draft:
        return ResponseModel(success=False, message="Draft not found", data=None)

    qa_history = draft.get("qa_history", []) or []
    if not isinstance(qa_history, list):
        qa_history = []

    for item in answers or []:
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question:
            qa_history.append({"question": question, "answer": answer})

    loop_count = int(draft.get("loop_count", 0) or 0) + 1
    qa_round = int(draft.get("qa_round", 1) or 1)

    context = _build_context(
        project_description=str(draft.get("project_description") or ""),
        epic_name=str(draft.get("epic_name") or ""),
        epic_description=str(draft.get("epic_description") or ""),
        goal=str(draft.get("goal") or ""),
        qa_history=qa_history,
        existing_stories=existing_stories,
    )

    status = "questions"
    story_draft: Optional[Dict[str, Any]] = None

    # Force at least 2 rounds: exploratory (round 1) then detail (round 2).
    if qa_round < 2:
        question_result = await _generate_questions(
            user_data,
            context,
            phase="detail",
            force_questions=True,
            min_questions=2,
            max_questions=5,
        )
        questions = question_result.get("questions", []) or []
        qa_round = 2
    else:
        # Max 2 rounds total: after round-2 answers, generate the draft.
        status = "story_ready"
        story_draft = await _generate_story_draft(user_data, context)
        questions = []
        qa_round = 2

    _save_draft(
        draft_id,
        {
            "qa_history": qa_history,
            "loop_count": loop_count,
            "qa_round": qa_round,
            "status": status,
            "last_questions": questions,
            "story_draft": story_draft,
            "updated_at": _now_iso(),
        },
    )

    return ResponseModel(
        success=True,
        message="Answers submitted",
        data={
            "draft_id": draft_id,
            "status": status,
            "questions": questions,
            "loop_count": loop_count,
            "qa_round": qa_round,
            "story_draft": story_draft,
        },
    )


def get_user_story_draft(draft_id: str) -> ResponseModel:
    draft = _load_draft(draft_id)
    if not draft:
        return ResponseModel(success=False, message="Draft not found", data=None)
    return ResponseModel(
        success=True,
        message="Draft loaded",
        data={
            "draft_id": draft_id,
            "epic_id": draft.get("epic_id"),
            "epic_name": draft.get("epic_name"),
            "status": draft.get("status"),
            "questions": draft.get("last_questions") or [],
            "loop_count": int(draft.get("loop_count", 0) or 0),
            "qa_round": int(draft.get("qa_round", 1) or 1),
            "story_draft": draft.get("story_draft"),
        },
    )


def delete_user_story_draft(draft_id: str) -> None:
    try:
        FIRESTORE_CLIENT.collection(USER_STORY_DRAFTS_COLLECTION).document(draft_id).delete()
    except Exception as exc:
        logger.warning("Failed to delete user story draft %s: %s", draft_id, exc)
