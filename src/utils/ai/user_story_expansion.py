import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.utils.core.validation_utils import get_code_block

logger = logging.getLogger(__name__)


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


def _build_context(
    *,
    project_description: str,
    epic_name: str,
    epic_description: str,
    existing_stories: List[Dict[str, Any]],
    instruction: str,
) -> str:
    parts: List[str] = []
    if project_description:
        parts.append(f"Project Description:\n{project_description.strip()}")
    parts.append(f"Epic:\n{(epic_name or '').strip()}\n{(epic_description or '').strip()}".strip())
    if instruction:
        parts.append(f"Instruction:\n{instruction.strip()}")

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
) -> ResponseModel:
    max_new = max(1, min(int(max_new_stories or 5), 12))
    context = _build_context(
        project_description=project_description,
        epic_name=epic_name,
        epic_description=epic_description,
        existing_stories=existing_stories,
        instruction=instruction,
    )

    prompt = f"""
You are a senior product manager. Your task is to EXPAND the existing user stories for this epic.
Create up to {max_new} NEW atomic user stories that:
- cover missing flows/edge cases,
- split oversized stories into smaller ones (as new stories),
- add variations for different roles,
- avoid duplicates of existing stories.

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
""".strip()

    azure = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=None)
    raw = await azure.simple_completion(prompt)
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

        next_item = {
            "epic": epic_name or "",
            "user_story": title,
            "description": str(item.get("description") or "").strip(),
            "user_story_id": story_id,
            "order": item.get("order", 0),
            "dependencies": dependencies,
            "effortHours": item.get("effortHours", 0),
            "story_points": item.get("story_points", 0),
            "acceptanceCriteria": acceptance_criteria,
            "outOfScope": out_of_scope,
        }
        expanded.append(next_item)

    return ResponseModel(
        success=True,
        message=f"Generated {len(expanded)} additional user stories",
        data={"user_stories": expanded, "generated_count": len(expanded)},
    )
