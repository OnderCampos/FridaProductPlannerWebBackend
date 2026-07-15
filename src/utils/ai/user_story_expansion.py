import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import UploadFile

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.utils.core.validation_utils import get_code_block

logger = logging.getLogger(__name__)

MAX_EXPANSION_IMAGES = 5
MAX_EXPANSION_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB each


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


def _normalize_title(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip().lower())
    return cleaned


def _existing_fingerprints(existing_stories: List[Dict[str, Any]]) -> Tuple[set, set]:
    titles = set()
    ids = set()
    for item in existing_stories or []:
        title = str(item.get("user_story") or "").strip()
        story_id = str(item.get("user_story_id") or item.get("id") or "").strip()
        if title:
            titles.add(_normalize_title(title))
        if story_id:
            ids.add(story_id.lower())
    return titles, ids


def _normalize_number(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_float(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_document_text(value: Any, fallback: str = "N/A") -> str:
    text = str(value or "").strip()
    return text or fallback


def _stringify_bullet_list(items: List[str], fallback: str = "N/A") -> str:
    normalized = [str(item).strip() for item in items if str(item).strip()]
    if not normalized:
        return fallback
    return "\n".join(f"- {item}" for item in normalized)


def _normalize_document_payload(
    raw_document: Any,
    *,
    description: str,
    acceptance_criteria: List[str],
    out_of_scope: List[str],
    has_image_context: bool,
) -> Dict[str, str]:
    document = raw_document if isinstance(raw_document, dict) else {}
    wireframe_summary = _normalize_document_text(
        document.get("wireframe_mockup") or document.get("wireframeMockup"),
        fallback="Image-based UI reference provided." if has_image_context else "N/A",
    )

    return {
        "description_and_scope": _normalize_document_text(
            document.get("description_and_scope") or document.get("descriptionAndScope") or description,
            fallback=description or "N/A",
        ),
        "out_of_scope": _normalize_document_text(
            document.get("out_of_scope") or document.get("outOfScope") or _stringify_bullet_list(out_of_scope),
            fallback=_stringify_bullet_list(out_of_scope),
        ),
        "entry_points": _normalize_document_text(
            document.get("entry_points") or document.get("entryPoints"),
        ),
        "output_points": _normalize_document_text(
            document.get("output_points") or document.get("outputPoints"),
        ),
        "success_flow": _normalize_document_text(
            document.get("success_flow") or document.get("successFlow"),
        ),
        "wireframe_mockup": wireframe_summary,
        "field_description": _normalize_document_text(
            document.get("field_description") or document.get("fieldDescription"),
        ),
        "acceptance_criteria": _normalize_document_text(
            document.get("acceptance_criteria") or document.get("acceptanceCriteria") or _stringify_bullet_list(acceptance_criteria, fallback="Not provided."),
            fallback=_stringify_bullet_list(acceptance_criteria, fallback="Not provided."),
        ),
    }


async def _prepare_image_payloads(image_files: List[UploadFile]) -> List[str]:
    pending_files = [file for file in (image_files or []) if file is not None]
    if not pending_files:
        return []
    if len(pending_files) > MAX_EXPANSION_IMAGES:
        raise ValueError(f"Too many images. Max allowed is {MAX_EXPANSION_IMAGES}.")

    prepared: List[str] = []
    for file in pending_files:
        content_type = str(file.content_type or "").strip().lower()
        if not content_type.startswith("image/"):
            raise ValueError(
                f"Invalid file type for {file.filename or 'file'}. Only images are allowed."
            )

        data = await file.read()
        if not data:
            continue
        if len(data) > MAX_EXPANSION_IMAGE_BYTES:
            raise ValueError(
                f"File {file.filename or 'file'} exceeds the {MAX_EXPANSION_IMAGE_BYTES // (1024 * 1024)}MB limit."
            )

        encoded = base64.b64encode(data).decode("ascii")
        prepared.append(f"data:{content_type};base64,{encoded}")
    return prepared


def _build_context(
    *,
    project_description: str,
    epic_name: str,
    epic_description: str,
    existing_stories: List[Dict[str, Any]],
    instruction: str,
    has_reference_images: bool,
) -> str:
    parts: List[str] = []
    if project_description:
        parts.append(f"Project Description:\n{project_description.strip()}")
    parts.append(f"Epic:\n{(epic_name or '').strip()}\n{(epic_description or '').strip()}".strip())
    if instruction:
        parts.append(f"Instruction:\n{instruction.strip()}")
    if has_reference_images:
        parts.append(
            "Reference Images:\nOne or more UI screenshots, wireframes, or mockups are attached. "
            "Use them to identify visible screens, components, fields, actions, and states."
        )

    if existing_stories:
        lines: List[str] = []
        for item in existing_stories[:60]:
            story_id = str(item.get("user_story_id") or item.get("id") or "").strip()
            title = str(item.get("user_story") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not title and not desc:
                continue
            line = f"- {story_id}: {title}"
            if desc:
                line += f" — {desc[:200]}"
            lines.append(line)
        if lines:
            parts.append("Existing User Stories:\n" + "\n".join(lines))
    return "\n\n".join([p for p in parts if p]).strip()


async def expand_user_stories(
    *,
    user_data: UserData,
    project_description: str,
    epic_name: str,
    epic_description: str,
    existing_stories: List[Dict[str, Any]],
    instruction: str = "",
    max_new_stories: int = 5,
    image_files: Optional[List[UploadFile]] = None,
) -> ResponseModel:
    max_new = max(1, min(int(max_new_stories or 5), 12))
    try:
        image_payloads = await _prepare_image_payloads(image_files or [])
    except ValueError as exc:
        return ResponseModel(success=False, message=str(exc), data=None)

    context = _build_context(
        project_description=project_description,
        epic_name=epic_name,
        epic_description=epic_description,
        existing_stories=existing_stories,
        instruction=instruction,
        has_reference_images=bool(image_payloads),
    )

    prompt = f"""
You are a senior product manager. Your task is to EXPAND the existing user stories for this epic.
Create up to {max_new} NEW atomic user stories that:
- cover missing flows/edge cases,
- split oversized stories into smaller ones (as new stories),
- add variations for different roles,
- avoid duplicates of existing stories,
- when images are attached, extract the UI that is visible and use it to complement the goal/instruction and epic context.

Context:
{context}

Return ONLY valid JSON with this structure:
{{
  "user_stories": [
    {{
      "user_story": "As a ... I want ... so that ...",
      "description": "Short description",
      "acceptanceCriteria": ["Acceptance criterion 1", "Acceptance criterion 2"],
      "outOfScope": ["Out of scope item 1", "Out of scope item 2"],
      "document": {{
        "description_and_scope": "Short scope summary",
        "entry_points": "Where the user starts this flow",
        "output_points": "What the UI shows or updates after completion",
        "success_flow": "Short happy-path summary",
        "wireframe_mockup": "Summary of the UI seen in the attached images",
        "field_description": "Visible inputs, controls, and validation cues"
      }},
      "user_story_id": "Optional short ID (omit if unsure)",
      "order": 0,
      "dependencies": [],
      "effortHours": 0,
      "story_points": 0
    }}
  ]
}}

Rules:
- Output MUST be JSON only (no markdown).
- Avoid duplicates with the existing list.
- dependencies must be an array (can be empty).
- acceptanceCriteria must be a non-empty array.
- outOfScope must be a non-empty array (use \"N/A\" if none).
- If images are attached, inspect them first and extract visible UI components, forms, buttons, navigation, empty/loading/error states, and important fields.
- Use image-derived UI evidence to complement the written goal/instruction, especially in description, acceptanceCriteria, and document.* fields.
- Do not invent unsupported details from the images. Be explicit but conservative.
- document.wireframe_mockup should summarize the relevant UI evidence used for the story, or \"N/A\" if no image context exists.
- document.field_description should summarize visible inputs/controls when relevant, or \"N/A\".
- document.entry_points, document.output_points, and document.success_flow must be short strings, not arrays.
""".strip()

    azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
    raw = (
        await azure.simple_completion_with_images(prompt, image_payloads, model_tier="gpt")
        if image_payloads
        else await azure.simple_completion(prompt, model_tier="gpt")
    )
    parsed = _safe_json_load(raw)
    if not isinstance(parsed, dict):
        return ResponseModel(success=False, message="Failed to parse expansion output", data=None)

    items = parsed.get("user_stories")
    if not isinstance(items, list):
        items = []

    existing_titles, existing_ids = _existing_fingerprints(existing_stories)
    expanded: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("user_story") or "").strip()
        if not title:
            continue
        if _normalize_title(title) in existing_titles:
            continue

        story_id = str(item.get("user_story_id") or "").strip()
        if story_id and story_id.lower() in existing_ids:
            continue

        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list):
            dependencies = []

        acceptance_criteria = _normalize_string_list(item.get("acceptanceCriteria") or item.get("acceptance_criteria"))
        if not acceptance_criteria:
            acceptance_criteria = ["Not provided."]
        out_of_scope = _normalize_string_list(item.get("outOfScope") or item.get("out_of_scope"))
        if not out_of_scope:
            out_of_scope = ["N/A"]

        description = str(item.get("description") or "").strip()
        document = _normalize_document_payload(
            item.get("document"),
            description=description,
            acceptance_criteria=acceptance_criteria,
            out_of_scope=out_of_scope,
            has_image_context=bool(image_payloads),
        )
        if not description:
            description = next(
                (
                    candidate
                    for candidate in [
                        str(document.get("description_and_scope") or "").strip(),
                        str(document.get("wireframe_mockup") or "").strip(),
                        title,
                    ]
                    if candidate and candidate != "N/A"
                ),
                title,
            )

        next_item = {
            "epic": epic_name or "",
            "user_story": title,
            "description": description,
            "user_story_id": story_id,
            "order": _normalize_number(item.get("order"), 0),
            "dependencies": dependencies,
            "effortHours": _normalize_float(item.get("effortHours"), 0),
            "story_points": _normalize_number(item.get("story_points"), 0),
            "acceptanceCriteria": acceptance_criteria,
            "outOfScope": out_of_scope,
            "document": document,
        }
        expanded.append(next_item)
        existing_titles.add(_normalize_title(title))
        if story_id:
            existing_ids.add(story_id.lower())

    return ResponseModel(
        success=True,
        message=f"Generated {len(expanded)} additional user stories",
        data={"user_stories": expanded, "generated_count": len(expanded)},
    )
