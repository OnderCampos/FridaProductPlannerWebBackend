from typing import Optional, Dict, Any
import logging
from datetime import datetime, timezone

from src.services.setup.firebase_setup import FIRESTORE_CLIENT


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_user_profile(
    user_id: Optional[str],
    email: Optional[str],
    name: Optional[str] = None,
    role: Optional[str] = None,
    member_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Creates or updates a user profile in the users collection.
    """
    if not user_id and not email:
        return None

    doc_id = user_id or email
    if not doc_id:
        return None

    now = _current_timestamp_iso()
    user_ref = FIRESTORE_CLIENT.collection("users").document(doc_id)
    normalized_email = email.lower() if isinstance(email, str) else email
    payload = {
        "id": doc_id,
        "user_id": user_id,
        "email": normalized_email,
        "name": name,
        "role": role,
        "member_id": member_id,
        "updated_at": now,
    }

    try:
        existing = user_ref.get()
        if existing.exists:
            existing_data = existing.to_dict() or {}
            # Avoid downgrading a leader to member on partial updates.
            if payload.get("role") == "member" and existing_data.get("role") == "leader":
                payload["role"] = None
            user_ref.update({k: v for k, v in payload.items() if v is not None})
        else:
            payload["created_at"] = now
            user_ref.set({k: v for k, v in payload.items() if v is not None})
        return payload
    except Exception as e:
        logging.error(f"Error upserting user profile: {e}")
        return None


def get_user_profile(user_id: Optional[str] = None, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not user_id and not email:
        return None

    try:
        if user_id:
            user_doc = FIRESTORE_CLIENT.collection("users").document(user_id).get()
            if user_doc.exists:
                data = user_doc.to_dict()
                data["id"] = data.get("id") or user_doc.id
                return data
    except Exception as e:
        logging.error(f"Error retrieving user profile by id: {e}")

    if email:
        try:
            query = FIRESTORE_CLIENT.collection("users").where("email", "==", email.lower()).get()
            if query:
                data = query[0].to_dict()
                data["id"] = data.get("id") or query[0].id
                return data
        except Exception as e:
            logging.error(f"Error retrieving user profile by email: {e}")

    return None
