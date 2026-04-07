from __future__ import annotations

import re
from typing import Dict, List, Sequence, Union

FSD_SECTION_TITLES: Sequence[str] = (
    "Overview / Introduction",
    "System Overview",
    "User Roles & Personas",
    "Functional Requirements",
    "Use Cases / User Flows",
    "UI / UX Requirements",
    "Data Requirements",
    "Business Rules",
    "Non-Functional Requirements",
    "Assumptions & Constraints",
    "Acceptance Criteria",
)


def _as_lines(value: Union[str, List[str]]) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def build_fsd_document(section_map: Dict[str, Union[str, List[str]]]) -> str:
    """
    Build a plain-text FSD document using the required section titles in order.
    Empty sections are omitted.
    """
    lines: List[str] = []
    for title in FSD_SECTION_TITLES:
        content_lines = _as_lines(section_map.get(title, ""))
        if not content_lines:
            continue
        if len(content_lines) == 1 and content_lines[0].lower() == "not provided.":
            continue

        lines.append(title)
        if len(content_lines) == 1:
            lines.append(content_lines[0])
            lines.append("")
            continue

        for item in content_lines:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines).strip()


def _canonicalize_persona(value: str) -> tuple[str, str]:
    """Return a (dedupe_key, display_label) pair for role/persona strings."""
    role_text = str(value or "").strip()
    if not role_text:
        return "", ""

    normalized = role_text.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9\\s]", " ", normalized)
    normalized = re.sub(r"\\s+", " ", normalized).strip()
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

    if normalized == "qa engineer":
        normalized = "qa"

    canonical_labels = {
        "developer": "Developer",
        "tech lead": "Tech Lead",
        "qa": "QA",
        "architect": "Architect",
        "modernization team": "Modernization Team",
    }

    if not normalized:
        return "", ""
    return normalized, canonical_labels.get(normalized) or role_text


def dedupe_user_roles_personas_section(spec_text: str) -> str:
    """
    De-duplicate the "User Roles & Personas" section in an FSD plain-text document.

    This is useful when roles are returned with small variations (e.g., Developers vs Developer,
    QA Engineer vs QA).
    """
    if not spec_text:
        return spec_text

    def _normalize_heading(line: str) -> str:
        cleaned = re.sub(r"^#{1,6}\\s+", "", str(line or "").strip())
        cleaned = cleaned.rstrip(":").strip()
        return cleaned.lower()

    title_set = {_normalize_heading(title) for title in FSD_SECTION_TITLES}
    roles_title = _normalize_heading("User Roles & Personas")

    out: List[str] = []
    in_roles_section = False
    seen_keys: set[str] = set()

    for raw_line in str(spec_text).splitlines():
        heading_key = _normalize_heading(raw_line)
        if heading_key and heading_key in title_set:
            in_roles_section = heading_key == roles_title
            seen_keys = set()
            out.append(raw_line)
            continue

        if in_roles_section:
            match = re.match(r"^\\s*[-*•]\\s+(.*)$", raw_line or "")
            if match:
                key, label = _canonicalize_persona(match.group(1))
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                out.append(f"- {label}")
                continue

        out.append(raw_line)

    return "\n".join(out).strip()
