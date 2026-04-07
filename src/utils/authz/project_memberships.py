from typing import Optional, Dict, Any, List
import logging
from datetime import datetime, timezone

from src.services.setup.firebase_setup import FIRESTORE_CLIENT


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_membership_role(role: Optional[str], seniority: Optional[str]) -> str:
    role_value = (role or "").strip().lower()
    seniority_value = (seniority or "").strip().lower()
    if role_value in {"pm", "lead", "leader", "principal"}:
        return "leader"
    if seniority_value in {"lead", "principal"}:
        return "leader"
    return "member"


def _membership_doc_id(project_id: str, user_id: Optional[str], email: Optional[str]) -> Optional[str]:
    if user_id:
        return f"{project_id}:{user_id}"
    if email:
        return f"{project_id}:{email.lower()}"
    return None


def upsert_project_membership(
    project_id: str,
    user_id: Optional[str],
    email: Optional[str],
    role: str,
    project_role: Optional[str] = None,
    member_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if user_id and email and user_id.lower() == email.lower():
        user_id = None

    doc_id = _membership_doc_id(project_id, user_id, email)
    if not doc_id:
        return None

    now = _current_timestamp_iso()
    payload = {
        "id": doc_id,
        "project_id": project_id,
        "user_id": user_id,
        "email": email.lower() if isinstance(email, str) else email,
        "role": role,
        "project_role": project_role,
        "member_id": member_id,
        "updated_at": now,
    }

    try:
        collection = FIRESTORE_CLIENT.collection("project_memberships")
        ref = collection.document(doc_id)
        existing = ref.get()

        if not existing.exists and user_id and email:
            fallback_id = _membership_doc_id(project_id, None, email)
            if fallback_id and fallback_id != doc_id:
                fallback_ref = collection.document(fallback_id)
                fallback_doc = fallback_ref.get()
                if fallback_doc.exists:
                    ref = fallback_ref
                    existing = fallback_doc
                    payload["id"] = fallback_id
        if existing.exists:
            ref.update({k: v for k, v in payload.items() if v is not None})
        else:
            payload["created_at"] = now
            ref.set({k: v for k, v in payload.items() if v is not None})
        return payload
    except Exception as e:
        logging.error(f"Error upserting project membership: {e}")
        return None


def get_project_membership(
    project_id: str,
    user_id: Optional[str],
    email: Optional[str],
) -> Optional[Dict[str, Any]]:
    try:
        if user_id:
            doc_id = _membership_doc_id(project_id, user_id, None)
            doc = FIRESTORE_CLIENT.collection("project_memberships").document(doc_id).get()
            if doc.exists:
                data = doc.to_dict()
                data["id"] = data.get("id") or doc.id
                return data
    except Exception as e:
        logging.error(f"Error retrieving membership by user_id: {e}")

    if email:
        normalized_email = email.lower()
        try:
            doc_id = _membership_doc_id(project_id, None, normalized_email)
            doc = FIRESTORE_CLIENT.collection("project_memberships").document(doc_id).get()
            if doc.exists:
                data = doc.to_dict()
                data["id"] = data.get("id") or doc.id
                return data
        except Exception as e:
            logging.error(f"Error retrieving membership by email: {e}")

        try:
            query = FIRESTORE_CLIENT.collection("project_memberships") \
                .where("project_id", "==", project_id) \
                .where("email", "==", normalized_email).get()
            if query:
                data = query[0].to_dict()
                data["id"] = data.get("id") or query[0].id
                return data
        except Exception as e:
            logging.error(f"Error retrieving membership by email query: {e}")

    return None


def get_memberships_for_user(user_id: Optional[str], email: Optional[str]) -> List[Dict[str, Any]]:
    memberships: List[Dict[str, Any]] = []
    if not user_id and not email:
        return memberships

    if user_id:
        try:
            docs = FIRESTORE_CLIENT.collection("project_memberships").where("user_id", "==", user_id).get()
            for doc in docs or []:
                data = doc.to_dict()
                data["id"] = data.get("id") or doc.id
                memberships.append(data)
        except Exception as e:
            logging.error(f"Error retrieving memberships by user_id: {e}")

    if not memberships and email:
        try:
            docs = FIRESTORE_CLIENT.collection("project_memberships").where("email", "==", email.lower()).get()
            for doc in docs or []:
                data = doc.to_dict()
                data["id"] = data.get("id") or doc.id
                memberships.append(data)
        except Exception as e:
            logging.error(f"Error retrieving memberships by email: {e}")

    return memberships


def delete_project_membership(
    project_id: str,
    user_id: Optional[str],
    email: Optional[str],
) -> bool:
    doc_ids = []
    if user_id:
        doc_ids.append(_membership_doc_id(project_id, user_id, None))
    if email:
        doc_ids.append(_membership_doc_id(project_id, None, email))

    doc_ids = [doc_id for doc_id in doc_ids if doc_id]
    if not doc_ids:
        return False

    deleted = False
    for doc_id in dict.fromkeys(doc_ids):
        try:
            ref = FIRESTORE_CLIENT.collection("project_memberships").document(doc_id)
            doc = ref.get()
            if not doc.exists:
                continue
            ref.delete()
            deleted = True
        except Exception as e:
            logging.error(f"Error deleting project membership {doc_id}: {e}")
    return deleted


def delete_memberships_for_project(project_id: str) -> int:
    deleted = 0
    try:
        docs = FIRESTORE_CLIENT.collection("project_memberships").where("project_id", "==", project_id).get()
        for doc in docs or []:
            doc.reference.delete()
            deleted += 1
    except Exception as e:
        logging.error(f"Error deleting memberships for project {project_id}: {e}")
    return deleted
