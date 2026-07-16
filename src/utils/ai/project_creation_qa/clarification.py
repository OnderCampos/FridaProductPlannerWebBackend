import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.firebase_setup import FIREBASE, FIRESTORE_CLIENT
from src.utils.core.validation_utils import get_code_block
from src.utils.ai.fsd_sections import FSD_SECTION_TITLES, dedupe_user_roles_personas_section

PROJECT_KNOWLEDGE_COLLECTION = "project_knowledge"
PROJECT_SPECS_COLLECTION = "project_specs"
PROJECTS_COLLECTION = "projects"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_knowledge(project_id: str) -> Optional[Dict[str, Any]]:
    doc = FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).get()
    return doc.to_dict() if doc.exists else None


def _save_knowledge(project_id: str, payload: Dict[str, Any]) -> None:
    FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).set(
        payload,
        merge=True,
    )


def _update_project_creation_status(project_id: str, status: str) -> None:
    try:
        FIRESTORE_CLIENT.collection(PROJECTS_COLLECTION).document(project_id).update(
            {
                "creation_status": status,
                "updated_at": _now_iso(),
            }
        )
    except Exception:
        logging.exception("Failed to update project creation status for clarification flow")


def _build_context(description: str, qa_history: List[Dict[str, str]]) -> str:
    if not qa_history:
        return description.strip()

    lines = [description.strip(), "", "Clarification Q&A:"]
    for idx, entry in enumerate(qa_history, start=1):
        question = entry.get("question", "").strip()
        answer = entry.get("answer", "").strip()
        if not question and not answer:
            continue
        lines.append(f"{idx}. Q: {question}")
        lines.append(f"   A: {answer}")

    return "\n".join(lines).strip()


async def _generate_questions(
    user_data: UserData,
    description: str,
    qa_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    context = _build_context(description, qa_history)
    prompt = f"""
You are a senior product analyst. Your job is to identify missing or unclear information
needed to produce a complete product specification. If the context is sufficient, mark complete.

Context:
{context}

Return ONLY valid JSON with the following structure:
{{
  "complete": boolean,
  "questions": ["Question 1", "Question 2"]
}}

Rules:
- Ask up to 5 questions.
- Questions must be concrete, specific, and focused on missing/unclear info.
- If complete is true, questions must be an empty array.
""".strip()

    azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
    raw = await azure.simple_completion(prompt, model_tier="mini")

    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        try:
            block = get_code_block(raw)
            if block:
                parsed = json.loads(block)
        except json.JSONDecodeError:
            parsed = None

    if not isinstance(parsed, dict):
        return {"complete": False, "questions": []}

    complete = bool(parsed.get("complete"))
    questions = parsed.get("questions", [])
    if not isinstance(questions, list):
        questions = []

    normalized_questions = [str(question).strip() for question in questions if str(question).strip()]
    if complete:
        normalized_questions = []
    if not complete and not normalized_questions:
        normalized_questions = [
            "Please provide any additional details or requirements that are important for this project."
        ]

    return {"complete": complete, "questions": normalized_questions}


async def _generate_spec_text(
    user_data: UserData,
    description: str,
    qa_history: List[Dict[str, str]],
) -> str:
    context = _build_context(description, qa_history)
    sections = "\n".join([f"- {title}" for title in FSD_SECTION_TITLES])
    prompt = f"""
You are a senior product manager. Generate a concise but complete Functional Specification
Document (FSD) based on the context below.

Rules:
- Use the EXACT section headings listed below, in the same order for included sections.
- Provide bullet points where appropriate.
- If information is missing for a section, OMIT that section entirely.
- Return plain text (no JSON).

Required sections:
{sections}

Context:
{context}
""".strip()

    azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
    return await azure.simple_completion(prompt, model_tier="gpt")


def _render_pdf_bytes(title: str, body: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("reportlab is required to generate PDF") from exc

    from io import BytesIO

    stream = BytesIO()
    canvas_obj = canvas.Canvas(stream, pagesize=LETTER)
    width, height = LETTER

    x = 0.8 * inch
    y = height - 0.8 * inch
    canvas_obj.setFont("Helvetica-Bold", 16)
    canvas_obj.drawString(x, y, title)
    y -= 0.4 * inch

    canvas_obj.setFont("Helvetica", 10)
    max_width = width - 1.6 * inch
    words = body.replace("\r\n", "\n").replace("\r", "\n").split()

    line = ""
    for word in words:
        test_line = f"{line} {word}".strip()
        if canvas_obj.stringWidth(test_line, "Helvetica", 10) <= max_width:
            line = test_line
        else:
            canvas_obj.drawString(x, y, line)
            y -= 0.22 * inch
            if y <= 0.8 * inch:
                canvas_obj.showPage()
                y = height - 0.8 * inch
                canvas_obj.setFont("Helvetica", 10)
            line = word

    if line:
        canvas_obj.drawString(x, y, line)

    canvas_obj.save()
    stream.seek(0)
    return stream.read()


def _upload_pdf(project_id: str, pdf_bytes: bytes) -> Optional[str]:
    bucket = FIREBASE.storage_client
    if not bucket:
        return None

    path = f"project-specs/{project_id}/specification.pdf"
    blob = bucket.blob(path)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")

    try:
        blob.make_public()
        return blob.public_url
    except Exception:
        try:
            return blob.generate_signed_url(version="v4", expiration=3600)
        except Exception:
            return None


async def start_clarification(
    user_data: UserData,
    project_id: str,
    description: str,
) -> ResponseModel:
    try:
        existing = _load_knowledge(project_id) or {}
        qa_history = []
        loop_count = 0
        created_at = existing.get("created_at") or _now_iso()

        question_result = await _generate_questions(user_data, description, qa_history)
        complete = question_result.get("complete", False)
        questions = question_result.get("questions", [])

        status = "questions"
        spec_payload: Dict[str, Any] = {}
        if complete or loop_count >= 3:
            status = "spec_ready"
            spec_text = await _generate_spec_text(user_data, description, qa_history)
            spec_text = dedupe_user_roles_personas_section(spec_text)
            pdf_bytes = _render_pdf_bytes("Product Specification", spec_text)
            spec_url = _upload_pdf(project_id, pdf_bytes)
            spec_payload = {
                "spec_text": spec_text,
                "spec_url": spec_url,
                "spec_generated_at": _now_iso(),
            }
            FIRESTORE_CLIENT.collection(PROJECT_SPECS_COLLECTION).document(project_id).set(
                {
                    "project_id": project_id,
                    **spec_payload,
                },
                merge=True,
            )

        _save_knowledge(
            project_id,
            {
                "project_id": project_id,
                "base_description": description,
                "qa_history": qa_history,
                "loop_count": loop_count,
                "status": status,
                "last_questions": questions,
                "created_at": created_at,
                "updated_at": _now_iso(),
                **spec_payload,
            },
        )
        _update_project_creation_status(project_id, status)

        return ResponseModel(
            success=True,
            message="Clarification initialized",
            data={
                "status": status,
                "questions": questions,
                "loop_count": loop_count,
                **spec_payload,
            },
        )
    except Exception as exc:
        logging.error(f"Error starting clarification: {exc}")
        return ResponseModel(
            success=False,
            message=f"Failed to start clarification: {str(exc)}",
            data=None,
        )


async def submit_answers(
    user_data: UserData,
    project_id: str,
    answers: List[Dict[str, str]],
) -> ResponseModel:
    try:
        knowledge = _load_knowledge(project_id) or {}
        description = knowledge.get("base_description", "")
        qa_history = knowledge.get("qa_history", []) or []
        loop_count = int(knowledge.get("loop_count", 0) or 0) + 1

        normalized_answers = []
        for entry in answers:
            question = str(entry.get("question", "")).strip()
            answer = str(entry.get("answer", "")).strip()
            if not question and not answer:
                continue
            normalized_answers.append({"question": question, "answer": answer})

        qa_history.extend(normalized_answers)

        question_result = await _generate_questions(user_data, description, qa_history)
        complete = question_result.get("complete", False)
        questions = question_result.get("questions", [])

        status = "questions"
        spec_payload: Dict[str, Any] = {}
        if complete or loop_count >= 3:
            status = "spec_ready"
            spec_text = await _generate_spec_text(user_data, description, qa_history)
            pdf_bytes = _render_pdf_bytes("Product Specification", spec_text)
            spec_url = _upload_pdf(project_id, pdf_bytes)
            spec_payload = {
                "spec_text": spec_text,
                "spec_url": spec_url,
                "spec_generated_at": _now_iso(),
            }
            FIRESTORE_CLIENT.collection(PROJECT_SPECS_COLLECTION).document(project_id).set(
                {
                    "project_id": project_id,
                    **spec_payload,
                },
                merge=True,
            )

        _save_knowledge(
            project_id,
            {
                "base_description": description,
                "qa_history": qa_history,
                "loop_count": loop_count,
                "status": status,
                "last_questions": questions,
                "updated_at": _now_iso(),
                **spec_payload,
            },
        )
        _update_project_creation_status(project_id, status)

        return ResponseModel(
            success=True,
            message="Clarification updated",
            data={
                "status": status,
                "questions": questions,
                "loop_count": loop_count,
                **spec_payload,
            },
        )
    except Exception as exc:
        logging.error(f"Error submitting clarification answers: {exc}")
        return ResponseModel(
            success=False,
            message=f"Failed to submit answers: {str(exc)}",
            data=None,
        )
