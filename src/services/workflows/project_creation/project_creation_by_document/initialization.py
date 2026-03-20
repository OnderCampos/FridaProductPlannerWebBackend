"""
Document-based project creation workflow.

This module:
1) Creates the base project record.
2) Extracts structured data from the uploaded document.
3) Builds a specification document and stores it in Firestore/Storage.
"""

from datetime import datetime, timezone
from io import BytesIO
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from src.schemas.project_creation import (
    ProjectCreationClarificationData,
    ProjectCreationInitializationData,
)
from src.schemas.user_data import UserData
from src.services.setup.firebase_setup import FIREBASE, FIRESTORE_CLIENT
from src.services.workflows.project_creation.common import create_project_record
from src.utils.ai.fsd_sections import build_fsd_document

PROJECT_KNOWLEDGE_COLLECTION = "project_knowledge"
PROJECT_SPECS_COLLECTION = "project_specs"



# =====================
# Time/Firestore helpers
# =====================
def _now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _save_knowledge(project_id: str, payload: Dict[str, Any]) -> None:
    """Persist project creation metadata to Firestore."""
    FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).set(
        payload,
        merge=True,
    )


# =====================
# Spec/PDF helpers
# =====================
def _render_pdf_bytes(title: str, body: str) -> bytes:
    """Render a simple PDF document and return its bytes."""
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("reportlab is required to generate PDF") from exc

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


# =====================
# Storage helpers
# =====================
def _upload_pdf(project_id: str, pdf_bytes: bytes) -> Optional[str]:
    """Upload spec PDF to Firebase Storage and return a public or signed URL."""
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


# =====================
# Spec text assembly
# =====================
def _build_spec_text(
    project_description: str,
    epics: List[Dict[str, Any]],
    user_stories: List[Dict[str, Any]],
    role_candidates: Optional[List[Any]] = None,
) -> str:
    """Build an FSD-style specification text from extracted epics/user stories."""
    overview = project_description.strip() if project_description else "Not provided."

    system_overview_lines = []
    for epic in epics:
        name = str(epic.get("name") or "").strip()
        description = str(epic.get("description") or "").strip()
        if not name and not description:
            continue
        system_overview_lines.append(f"{name}: {description}".strip(": "))

    role_values = list(role_candidates or [])
    for epic in epics:
        role_values.extend(epic.get("roles") or [])

    roles: List[str] = []
    seen_roles = set()
    for role in role_values:
        role_text = str(role).strip()
        if not role_text:
            continue

        # Canonicalize/merge common conceptual duplicates (e.g., Developers vs Developer, QA Engineer vs QA).
        normalized = (
            role_text.strip()
            .lower()
            .replace("&", " and ")
        )
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = normalized.replace("quality assurance", "qa")
        normalized = normalized.replace("modernisation", "modernization")
        normalized = normalized.replace("technical lead", "tech lead")

        tokens = normalized.split()
        if tokens:
            singular_last = {
                "developers": "developer",
                "leads": "lead",
                "teams": "team",
                "architects": "architect",
                "engineers": "engineer",
            }
            last = tokens[-1]
            if last in singular_last:
                tokens[-1] = singular_last[last]
        normalized = " ".join(tokens).strip()

        # Reduce common suffixes where they don't add meaning for the persona list.
        if normalized == "qa engineer":
            normalized = "qa"
        if not normalized:
            continue

        canonical_labels = {
            "developer": "Developer",
            "tech lead": "Tech Lead",
            "qa": "QA",
            "architect": "Architect",
            "modernization team": "Modernization Team",
        }
        role_key = normalized
        role_label = canonical_labels.get(role_key) or role_text.strip()
        if role_key in seen_roles:
            continue
        seen_roles.add(role_key)
        roles.append(role_label)

    functional_requirements = []
    if user_stories:
        for idx, story in enumerate(user_stories, start=1):
            title = str(story.get("user_story") or "").strip() or "Untitled Story"
            description = str(story.get("description") or "").strip()
            requirement = f"FR-{idx:02d}: {title}"
            if description:
                requirement = f"{requirement} - {description}"
            functional_requirements.append(requirement)
    elif epics:
        for idx, epic in enumerate(epics, start=1):
            name = str(epic.get("name") or "").strip() or "Untitled Epic"
            description = str(epic.get("description") or "").strip()
            requirement = f"FR-{idx:02d}: {name}"
            if description:
                requirement = f"{requirement} - {description}"
            functional_requirements.append(requirement)

    use_cases = []
    if user_stories:
        for story in user_stories:
            title = str(story.get("user_story") or "").strip()
            if not title:
                continue
            use_cases.append(f"Use Case: {title}")

    section_map = {
        "Overview / Introduction": overview,
        "System Overview": system_overview_lines or "Not provided.",
        "User Roles & Personas": roles or "Not provided.",
        "Functional Requirements": functional_requirements or "Not provided.",
        "Use Cases / User Flows": use_cases or "Not provided.",
        "UI / UX Requirements": "Not provided.",
        "Data Requirements": "Not provided.",
        "Business Rules": "Not provided.",
        "Non-Functional Requirements": "Not provided.",
        "Assumptions & Constraints": "Not provided.",
        "Acceptance Criteria": "Not provided.",
    }

    return build_fsd_document(section_map)


# =====================
# Extraction workflow
# =====================
async def start_file_extraction(
    user_data: UserData,
    project_id: str,
    project_name: str,
    description: str,
    document_text: str,
    source_payload: Dict[str, Any],
    language: str = "English",
) -> ProjectCreationClarificationData:
    """
    Run the document extraction graph and persist the generated spec.

    Falls back to direct LLM spec generation if the graph fails.
    """
    try:
        _save_knowledge(
            project_id,
            {
                "project_id": project_id,
                "base_description": description or "",
                "qa_history": [],
                "loop_count": 0,
                "status": "extracting",
                "last_questions": [],
                "source_type": "file",
                "source_payload": source_payload,
                "updated_at": _now_iso(),
                "created_at": _now_iso(),
            },
        )

        combined_text = document_text or ""
        if description:
            combined_text = f"User Description:\n{description.strip()}\n\nDocument Content:\n{combined_text}"

        from src.intelligence.graphs.document_extraction_graph import run_document_extraction_graph

        graph_state = run_document_extraction_graph(
            user_data=user_data,
            project_name=project_name,
            document_text=combined_text,
            language=language,
        )

        graph_error = graph_state.get("error")
        if graph_error:
            logging.warning(f"File extraction failed, falling back to LLM spec generation: {graph_error}")
            from src.utils.ai.project_creation_source_spec import generate_spec_from_source

            fallback = await generate_spec_from_source(
                user_data=user_data,
                project_id=project_id,
                description=description or "",
                source_type="document",
                source_payload={
                    "title": source_payload.get("filename"),
                    "text": combined_text,
                    "url": "",
                },
            )
            if fallback.success and isinstance(fallback.data, dict):
                return ProjectCreationClarificationData(**fallback.data)
            raise RuntimeError(fallback.message or "Failed to generate specification")

        project_description = graph_state.get("project_description", "")
        roles = graph_state.get("roles") or []
        technical_stack = graph_state.get("technical_stack") or []
        epics = graph_state.get("epics") or []
        user_stories = graph_state.get("user_stories") or []

        spec_text = _build_spec_text(
            project_description,
            epics,
            user_stories,
            role_candidates=roles,
        )
        pdf_bytes = _render_pdf_bytes("Product Specification", spec_text) if spec_text else None
        spec_url = _upload_pdf(project_id, pdf_bytes) if pdf_bytes else None

        spec_payload = {
            "spec_text": spec_text,
            "spec_url": spec_url,
            "spec_generated_at": _now_iso(),
        }

        FIRESTORE_CLIENT.collection(PROJECT_SPECS_COLLECTION).document(project_id).set(
            {"project_id": project_id, **spec_payload},
            merge=True,
        )

        _save_knowledge(
            project_id,
            {
                "status": "spec_ready",
                "last_questions": [],
                "extracted_project_description": project_description,
                "extracted_roles": roles,
                "extracted_technical_stack": technical_stack,
                "extracted_epics": epics,
                "extracted_user_stories": user_stories,
                "updated_at": _now_iso(),
                **spec_payload,
            },
        )

        FIRESTORE_CLIENT.collection("projects").document(project_id).update(
            {
                "creation_status": "spec_ready",
                "updated_at": _now_iso(),
            }
        )

        return ProjectCreationClarificationData(
            status="spec_ready",
            questions=[],
            loop_count=0,
            extracted_project_description=project_description,
            extracted_roles=roles,
            extracted_technical_stack=technical_stack,
            extracted_epics=epics,
            extracted_user_stories=user_stories,
            **spec_payload,
        )
    except Exception as exc:
        logging.error(f"Error extracting project data from file: {exc}")
        try:
            FIRESTORE_CLIENT.collection("projects").document(project_id).update(
                {
                    "creation_status": "spec_failed",
                    "updated_at": _now_iso(),
                }
            )
        except Exception:
            pass
        raise RuntimeError(f"Failed to extract project data: {str(exc)}") from exc

# =====================
# Project creation entry
# =====================
async def create_project_by_file(
    user_data: UserData,
    name: str,
    description: str,
    project_key: str,
    document_text: str,
    source_payload: Dict[str, Any],
) -> ProjectCreationInitializationData:
    """
    Create a project draft from a file upload and start the extraction flow.

    Flow:
    1) Create the base project record with `creation_source="file"`.
    2) Kick off file extraction using the full `document_text` and `source_payload`.
    3) Return the project payload and initial clarification/spec state.

    Returns:
        ProjectCreationInitializationData: Includes project info and clarification state (if available).
    """
    project_record = create_project_record(
        user_data=user_data,
        name=name,
        description=description,
        project_key=project_key,
        creation_status="extracting",
        creation_source="file",
    )

    clarification: Optional[ProjectCreationClarificationData] = None
    try:
        clarification = await start_file_extraction(
            user_data=user_data,
            project_id=project_record.project_id,
            project_name=name,
            description=description or "",
            document_text=document_text,
            source_payload=source_payload,
        )
    except Exception as exc:
        logging.warning(f"File extraction failed for project {project_record.project_id}: {exc}")

    return ProjectCreationInitializationData(
        project=project_record.project,
        clarification=clarification,
    )



# =====================
# Text extraction utils
# =====================
class DocumentTextError(RuntimeError):
    """Raised when document text extraction fails or is unsupported."""
    pass


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from a PDF payload."""
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise DocumentTextError("pypdf is required to parse PDF files.") from exc

    reader = PdfReader(BytesIO(data))
    pages_text = []
    for page in reader.pages:
        page_text = page.extract_text() if page else ""
        if page_text:
            pages_text.append(page_text)
    return "\n".join(pages_text).strip()


def _extract_docx_text(data: bytes) -> str:
    """Extract text from a DOCX payload."""
    try:
        import docx  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise DocumentTextError("python-docx is required to parse DOCX files.") from exc

    document = docx.Document(BytesIO(data))
    paragraphs = [para.text for para in document.paragraphs if para.text]
    return "\n".join(paragraphs).strip()


def _normalize_text(text: str) -> str:
    """Normalize whitespace and remove empty lines."""
    if not text:
        return ""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def extract_text_from_bytes(
    data: bytes,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> str:
    """Extract text from PDF/DOCX bytes based on extension/content type."""
    if not data:
        return ""

    extension = ""
    if filename:
        extension = Path(filename).suffix.lower()

    if extension in {".pdf"} or content_type == "application/pdf":
        return _normalize_text(_extract_pdf_text(data))

    if extension in {".docx"} or content_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }:
        return _normalize_text(_extract_docx_text(data))

    raise DocumentTextError("Unsupported file type. Please upload a PDF or DOCX file.")


async def create_project_from_file_upload(
    *,
    user_data: UserData,
    name: str,
    description: str,
    project_key: str,
    file_bytes: bytes,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> ProjectCreationInitializationData:
    """
    Convenience entrypoint for FastAPI routes that accept raw file bytes.

    This:
    1) extracts text from the uploaded file
    2) builds the `source_payload`
    3) delegates to `create_project_by_file(...)`
    """
    document_text = extract_text_from_bytes(
        file_bytes,
        filename=filename,
        content_type=content_type,
    )

    if not document_text:
        raise DocumentTextError("Uploaded file contains no readable text")

    source_payload = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(file_bytes),
        "text_excerpt": document_text[:2000],
    }

    return await create_project_by_file(
        user_data=user_data,
        name=name,
        description=description or "",
        project_key=project_key,
        document_text=document_text,
        source_payload=source_payload,
    )

