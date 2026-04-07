from typing import Any, Dict, Optional, Tuple
import logging

from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.authz.project_memberships import (
    derive_membership_role,
    get_project_membership,
    get_memberships_for_user,
    upsert_project_membership,
)


LEAD_ROLES = {"pm", "lead", "leader", "principal"}
LEAD_SENIORITY = {"lead", "principal"}


def _is_lead_member(member: Optional[Dict[str, Any]]) -> bool:
    if not member:
        return False
    role = str(member.get("role") or "").strip().lower()
    seniority = str(member.get("seniority") or "").strip().lower()
    return role in LEAD_ROLES or seniority in LEAD_SENIORITY


def _get_member_record(project_id: str, user_id: str, email: Optional[str]) -> Optional[Dict[str, Any]]:
    members_ref = FIRESTORE_CLIENT.collection("team_members")
    try:
        member_docs = members_ref.where("project_id", "==", project_id).where("user_id", "==", user_id).get()
        if member_docs:
            member_data = member_docs[0].to_dict()
            member_data["id"] = member_data.get("id") or member_docs[0].id
            return member_data
    except Exception as e:
        logging.error(f"Error looking up member by user_id for project {project_id}: {e}")

    if email:
        try:
            member_docs = members_ref.where("project_id", "==", project_id).where("email", "==", email).get()
            if member_docs:
                member_data = member_docs[0].to_dict()
                member_data["id"] = member_data.get("id") or member_docs[0].id
                return member_data
        except Exception as e:
            logging.error(f"Error looking up member by email for project {project_id}: {e}")

    return None


def _get_membership_record(project_id: str, user_id: str, email: Optional[str]) -> Optional[Dict[str, Any]]:
    return get_project_membership(project_id, user_id, email)


def get_project_access(project_id: str, user_id: str, email: Optional[str]) -> ResponseModel:
    """
    Returns project access info for a user. Success when the user is owner or member.
    """
    try:
        project_ref = FIRESTORE_CLIENT.collection("projects").document(project_id)
        project_doc = project_ref.get()

        if not project_doc.exists:
            return ResponseModel(success=False, message="Project not found", data=None)

        project_data = project_doc.to_dict()
        project_data["id"] = project_doc.id

        is_owner = project_data.get("user_id") == user_id

        membership_record = None
        member_record = None

        if is_owner:
            membership_record = upsert_project_membership(
                project_id=project_id,
                user_id=user_id,
                email=email,
                role="leader",
                project_role="owner",
            )
        else:
            membership_record = _get_membership_record(project_id, user_id, email)
            if not membership_record:
                member_record = _get_member_record(project_id, user_id, email)
                if member_record:
                    membership_role = derive_membership_role(
                        member_record.get("role"),
                        member_record.get("seniority"),
                    )
                    membership_record = upsert_project_membership(
                        project_id=project_id,
                        user_id=user_id,
                        email=email or member_record.get("email"),
                        role=membership_role,
                        project_role=member_record.get("role"),
                        member_id=member_record.get("id") or member_record.get("member_id"),
                    )

        if not is_owner and not membership_record and not member_record:
            return ResponseModel(success=False, message="Unauthorized: You don't have access to this project", data=None)

        if not member_record and membership_record:
            member_record = membership_record

        is_lead = is_owner or _is_lead_member(member_record) or _is_lead_member(membership_record)
        if membership_record and not is_lead:
            if derive_membership_role(
                membership_record.get("project_role"),
                membership_record.get("project_seniority"),
            ) == "leader":
                is_lead = True

        return ResponseModel(
            success=True,
            message="Project access granted",
            data={
                "project": project_data,
                "member": member_record,
                "membership": membership_record,
                "is_owner": is_owner,
                "is_member": bool(is_owner or membership_record or member_record),
                "is_lead": is_lead,
            },
        )
    except Exception as e:
        logging.error(f"Error checking project access for {project_id}: {e}")
        return ResponseModel(success=False, message=f"Error checking project access: {str(e)}", data=None)


def get_global_user_role(user_data: UserData) -> Dict[str, Any]:
    """
    Determines the user's global role (leader/member) based on ownership or membership.
    """
    user_id = user_data.user_id
    email = user_data.email

    is_lead = False
    is_member = False
    member_id = None
    member_name = None

    try:
        owner_projects = FIRESTORE_CLIENT.collection("projects").where("user_id", "==", user_id).get()
        if owner_projects:
            is_lead = True
    except Exception as e:
        logging.error(f"Error checking owned projects for {user_id}: {e}")

    memberships = get_memberships_for_user(user_id, email)
    if memberships:
        is_member = True
        for membership in memberships:
            if member_id is None:
                member_id = membership.get("member_id")
            if _is_lead_member(membership) or derive_membership_role(
                membership.get("project_role"),
                membership.get("project_seniority"),
            ) == "leader":
                is_lead = True

    members_ref = FIRESTORE_CLIENT.collection("team_members")
    member_docs = []
    if not memberships:
        try:
            member_docs = members_ref.where("user_id", "==", user_id).get()
        except Exception as e:
            logging.error(f"Error checking team membership by user_id for {user_id}: {e}")

        if not member_docs and email:
            try:
                member_docs = members_ref.where("email", "==", email).get()
            except Exception as e:
                logging.error(f"Error checking team membership by email for {email}: {e}")
    elif member_name is None:
        # Attempt to retrieve a display name for login responses.
        try:
            member_docs = members_ref.where("user_id", "==", user_id).get()
        except Exception:
            member_docs = []
        if not member_docs and email:
            try:
                member_docs = members_ref.where("email", "==", email).get()
            except Exception:
                member_docs = []

    if member_docs:
        is_member = True
        for doc in member_docs:
            data = doc.to_dict()
            doc_id = data.get("id") or doc.id
            if member_id is None:
                member_id = doc_id
            if member_name is None:
                member_name = data.get("name")
            if _is_lead_member(data):
                is_lead = True

    role = "leader" if is_lead or not is_member else "member"
    return {
        "role": role,
        "is_team_lead": role == "leader",
        "member_id": member_id,
        "member_name": member_name,
    }


def get_project_id_for_epic(epic_id: str) -> Optional[str]:
    try:
        epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
        if not epic_doc.exists:
            return None
        return epic_doc.to_dict().get("project_id")
    except Exception as e:
        logging.error(f"Error retrieving project_id for epic {epic_id}: {e}")
        return None


def get_project_id_for_story(story_id: str) -> Optional[str]:
    try:
        story_doc = FIRESTORE_CLIENT.collection("user_stories").document(story_id).get()
        if not story_doc.exists:
            return None
        epic_id = story_doc.to_dict().get("epic_id")
        if not epic_id:
            return None
        return get_project_id_for_epic(epic_id)
    except Exception as e:
        logging.error(f"Error retrieving project_id for story {story_id}: {e}")
        return None


def get_project_id_for_subtask(subtask_id: str) -> Optional[str]:
    try:
        subtask_doc = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id).get()
        if not subtask_doc.exists:
            return None
        story_id = subtask_doc.to_dict().get("user_story_id")
        if not story_id:
            return None
        return get_project_id_for_story(story_id)
    except Exception as e:
        logging.error(f"Error retrieving project_id for subtask {subtask_id}: {e}")
        return None
