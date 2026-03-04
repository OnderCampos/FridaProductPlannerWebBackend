from typing import Any, Dict, Optional

from src.utils.members import get_project_members


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


def _first_value(payload: Dict[str, Any], keys: tuple) -> Optional[Any]:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def normalize_assignee_fields(
    payload: Dict[str, Any],
    member_lookup: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    assignee = _first_value(payload, ("assignee", "assignee_name", "assigneeName"))
    raw_id = _first_value(payload, ("assignee_id", "assigneeId", "assigned_to", "assignedTo"))
    assignee_email = _first_value(payload, ("assignee_email", "assigneeEmail"))

    assignee_id = None
    if raw_id is not None:
        raw_id_str = str(raw_id)
        if "@" in raw_id_str:
            assignee_email = assignee_email or raw_id_str
        else:
            assignee_id = raw_id_str

    member = None
    if member_lookup:
        if assignee_id and assignee_id in member_lookup["by_id"]:
            member = member_lookup["by_id"][assignee_id]
        elif assignee:
            assignee_key = str(assignee).lower().strip()
            member = member_lookup["by_email"].get(assignee_key) or member_lookup["by_name"].get(assignee_key)

    if member:
        if not assignee_id:
            assignee_id = member.get("id") or assignee_id
        member_name = member.get("name")
        member_email = member.get("email")
        if assignee not in (member_name, member_email):
            assignee = member_name or member_email or assignee
        assignee_email = assignee_email or member_email
    else:
        if not assignee and assignee_email:
            assignee = assignee_email
        if assignee_email is None and assignee and "@" in str(assignee):
            assignee_email = str(assignee)

    payload["assignee"] = assignee if assignee is not None else ""
    payload["assignee_id"] = assignee_id

    if assignee_id:
        payload.setdefault("assigneeId", assignee_id)
    if assignee_email:
        payload["assignee_email"] = assignee_email
        payload.setdefault("assigneeEmail", assignee_email)

    return payload


def assignee_matches(
    payload: Dict[str, Any],
    assignee_id: Optional[str],
    member_lookup: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> bool:
    if not assignee_id:
        return True

    normalized = normalize_assignee_fields(dict(payload), member_lookup)
    if normalized.get("assignee_id") == assignee_id:
        return True

    if "@" in str(assignee_id):
        assignee_email = str(assignee_id).lower()
        if str(normalized.get("assignee_email") or "").lower() == assignee_email:
            return True
        if str(normalized.get("assignee") or "").lower() == assignee_email:
            return True

    if member_lookup and assignee_id in member_lookup.get("by_id", {}):
        member = member_lookup["by_id"][assignee_id]
        member_name = member.get("name")
        member_email = member.get("email")
        if normalized.get("assignee") in (member_name, member_email):
            return True
        if normalized.get("assignee_email") and normalized.get("assignee_email") == member_email:
            return True

    return False
