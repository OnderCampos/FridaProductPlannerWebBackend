from typing import Any, Dict, Optional

from src.utils.planning.members import get_project_members


def build_member_lookup_from_members(
    members: Optional[list],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_email: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}

    for member in members or []:
        member_id = member.get("id") or member.get("member_id")
        if member_id:
            by_id[member_id] = member

        email = member.get("email")
        if email:
            by_email[email.lower()] = member

        name = member.get("name")
        if name:
            by_name[name.lower()] = member

    return {"by_id": by_id, "by_email": by_email, "by_name": by_name}


def build_member_lookup(project_id: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    members = get_project_members(project_id)
    return build_member_lookup_from_members(members)


def _get_story_field_value(payload: Dict[str, Any], field_key: str) -> Optional[str]:
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return None

    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or field.get("name") or "").strip()
        if key != field_key:
            continue
        value = str(field.get("value") or "").strip()
        if value:
            return value
    return None


def get_assignee_email(
    payload: Dict[str, Any],
    member_lookup: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Optional[str]:
    assignee_email = str(payload.get("assignee_email") or "").strip()
    if not assignee_email:
        assignee_email = str(payload.get("assigneeEmail") or "").strip()
    if not assignee_email:
        assignee_email = str(_get_story_field_value(payload, "assignee_email") or "").strip()

    if assignee_email:
        return assignee_email

    if not member_lookup:
        return None

    assignee_id = str(
        payload.get("assigneeId")
        or payload.get("assignee_id")
        or payload.get("assigned_to")
        or payload.get("assignedTo")
        or ""
    ).strip()
    if not assignee_id:
        return None

    member = member_lookup.get("by_id", {}).get(assignee_id) or {}
    member_email = str(member.get("email") or "").strip()
    return member_email or None


def ensure_assignee_email(
    payload: Dict[str, Any],
    member_lookup: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    assignee_email = get_assignee_email(payload, member_lookup)
    payload["assignee_email"] = assignee_email
    if assignee_email:
        payload.setdefault("assigneeEmail", assignee_email)
    return payload


def assignee_matches(
    payload: Dict[str, Any],
    assignee_email: Optional[str],
    member_lookup: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> bool:
    if not assignee_email:
        return True

    current_assignee_email = str(get_assignee_email(payload, member_lookup) or "").strip().lower()
    expected_assignee_email = str(assignee_email or "").strip().lower()
    return bool(current_assignee_email) and current_assignee_email == expected_assignee_email
