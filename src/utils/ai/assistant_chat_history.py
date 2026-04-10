from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

from src.services.setup.firebase_setup import FIRESTORE_CLIENT


ASSISTANT_CHAT_HISTORY_COLLECTION = "assistant_chat_history"
ASSISTANT_CHAT_HISTORY_LIMIT = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_doc_id(user_id: str, project_id: str) -> str:
    return f"{user_id}__{project_id}"


def _normalize_history_messages(messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        created_at = item.get("created_at") or item.get("timestamp") or _now_iso()

        if role not in {"user", "assistant", "system"}:
            continue
        if not content:
            continue

        normalized.append(
            {
                "role": role,
                "content": content,
                "created_at": created_at,
            }
        )
    return normalized


def get_assistant_chat_history(
    user_id: str,
    project_id: str,
    limit: int = ASSISTANT_CHAT_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    if not user_id or not project_id:
        return []

    max_items = max(1, min(int(limit or ASSISTANT_CHAT_HISTORY_LIMIT), ASSISTANT_CHAT_HISTORY_LIMIT))
    doc_id = _history_doc_id(user_id, project_id)

    try:
        doc_ref = FIRESTORE_CLIENT.collection(ASSISTANT_CHAT_HISTORY_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            return []

        data = doc.to_dict() or {}
        messages = _normalize_history_messages(data.get("messages") or [])
        return messages[-max_items:]
    except Exception as history_error:
        logging.error("Failed to load assistant chat history (%s): %s", doc_id, history_error)
        return []


def save_assistant_chat_history(
    user_id: str,
    project_id: str,
    messages: List[Dict[str, Any]],
    limit: int = ASSISTANT_CHAT_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    if not user_id or not project_id:
        return []

    max_items = max(1, min(int(limit or ASSISTANT_CHAT_HISTORY_LIMIT), ASSISTANT_CHAT_HISTORY_LIMIT))
    normalized_messages = _normalize_history_messages(messages)[-max_items:]
    doc_id = _history_doc_id(user_id, project_id)
    doc_ref = FIRESTORE_CLIENT.collection(ASSISTANT_CHAT_HISTORY_COLLECTION).document(doc_id)

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "messages": normalized_messages,
        "updated_at": _now_iso(),
    }

    try:
        existing_doc = doc_ref.get()
        if not existing_doc.exists:
            payload["created_at"] = payload["updated_at"]
        doc_ref.set(payload, merge=True)
    except Exception as history_error:
        logging.error("Failed to save assistant chat history (%s): %s", doc_id, history_error)

    return normalized_messages


def append_assistant_chat_messages(
    user_id: str,
    project_id: str,
    new_messages: List[Dict[str, Any]],
    limit: int = ASSISTANT_CHAT_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    existing_messages = get_assistant_chat_history(
        user_id=user_id,
        project_id=project_id,
        limit=ASSISTANT_CHAT_HISTORY_LIMIT,
    )
    combined_messages = existing_messages + _normalize_history_messages(new_messages)
    return save_assistant_chat_history(
        user_id=user_id,
        project_id=project_id,
        messages=combined_messages,
        limit=limit,
    )


def clear_assistant_chat_history(user_id: str, project_id: str) -> bool:
    if not user_id or not project_id:
        return False

    doc_id = _history_doc_id(user_id, project_id)

    try:
        doc_ref = FIRESTORE_CLIENT.collection(ASSISTANT_CHAT_HISTORY_COLLECTION).document(doc_id)
        doc_ref.delete()
        return True
    except Exception as history_error:
        logging.error("Failed to clear assistant chat history (%s): %s", doc_id, history_error)
        return False
