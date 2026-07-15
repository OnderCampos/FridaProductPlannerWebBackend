import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.firebase_setup import FIREBASE, FIRESTORE_CLIENT
from src.utils.ai.fsd_sections import FSD_SECTION_TITLES, dedupe_user_roles_personas_section

PROJECT_KNOWLEDGE_COLLECTION = "project_knowledge"
PROJECT_SPECS_COLLECTION = "project_specs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_knowledge(project_id: str, payload: Dict[str, Any]) -> None:
    FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).set(
        payload,
        merge=True,
    )


def _build_source_context(
    description: str,
    source_type: str,
    source_payload: Dict[str, Any],
) -> str:
    parts = []
    if description:
        parts.append(f"Base Description:\n{description.strip()}")

    source_type = (source_type or "").strip().lower()
    if source_type == "document":
        parts.append("Source: Document")
        title = str(source_payload.get("title") or "").strip()
        url = str(source_payload.get("url") or "").strip()
        text = str(source_payload.get("text") or "").strip()
        if title:
            parts.append(f"Document Title: {title}")
        if url:
            parts.append(f"Document URL: {url}")
        if text:
            parts.append(f"Document Content:\n{text}")
    elif source_type == "figma":
        parts.append("Source: Figma")
        url = str(source_payload.get("url") or "").strip()
        notes = str(source_payload.get("notes") or "").strip()
        if url:
            parts.append(f"Figma URL: {url}")
        if notes:
            parts.append(f"Figma Notes:\n{notes}")
    elif source_payload:
        parts.append("Source Details:")
        parts.append(json.dumps(source_payload, ensure_ascii=True, indent=2))

    return "\n\n".join([part for part in parts if part]).strip()


async def _generate_spec_text_from_context(user_data: UserData, context: str) -> str:
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
    c = canvas.Canvas(stream, pagesize=LETTER)
    width, height = LETTER

    x = 0.8 * inch
    y = height - 0.8 * inch
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, title)
    y -= 0.4 * inch

    c.setFont("Helvetica", 10)
    max_width = width - 1.6 * inch
    words = body.replace("\r\n", "\n").replace("\r", "\n").split()

    line = ""
    for word in words:
        test_line = f"{line} {word}".strip()
        if c.stringWidth(test_line, "Helvetica", 10) <= max_width:
            line = test_line
        else:
            c.drawString(x, y, line)
            y -= 0.22 * inch
            if y <= 0.8 * inch:
                c.showPage()
                y = height - 0.8 * inch
                c.setFont("Helvetica", 10)
            line = word

    if line:
        c.drawString(x, y, line)

    c.save()
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


async def generate_spec_from_source(
    user_data: UserData,
    project_id: str,
    description: str,
    source_type: str,
    source_payload: Dict[str, Any],
) -> ResponseModel:
    try:
        context = _build_source_context(description, source_type, source_payload)
        if not context:
            context = description.strip()

        spec_text = await _generate_spec_text_from_context(user_data, context)
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
                "qa_history": [],
                "loop_count": 0,
                "status": "spec_ready",
                "last_questions": [],
                "source_type": source_type,
                "source_payload": source_payload,
                "updated_at": _now_iso(),
                "created_at": _now_iso(),
                **spec_payload,
            },
        )

        try:
            FIRESTORE_CLIENT.collection("projects").document(project_id).update(
                {
                    "creation_status": "spec_ready",
                    "updated_at": _now_iso(),
                }
            )
        except Exception:
            pass

        return ResponseModel(
            success=True,
            message="Specification generated",
            data={
                "status": "spec_ready",
                "questions": [],
                "loop_count": 0,
                **spec_payload,
            },
        )
    except Exception as exc:
        logging.error(f"Error generating specification from source: {exc}")
        try:
            FIRESTORE_CLIENT.collection("projects").document(project_id).update(
                {
                    "creation_status": "spec_failed",
                    "updated_at": _now_iso(),
                }
            )
        except Exception:
            pass

        return ResponseModel(
            success=False,
            message=f"Failed to generate specification: {str(exc)}",
            data=None,
        )
