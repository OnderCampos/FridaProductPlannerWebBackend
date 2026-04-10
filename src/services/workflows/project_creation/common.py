from datetime import datetime, timezone
from typing import Optional

from src.schemas.project_creation import ProjectCreationProjectData, ProjectRecordCreationData
from src.schemas.user_data import UserData
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.utils.authz.project_memberships import upsert_project_membership
from src.utils.authz.users import upsert_user_profile
from src.utils.planning.members import create_team_member


def current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


PROJECT_KEY_LENGTH = 3


def normalize_project_key(value: str) -> str:
    return "".join(ch for ch in (value or "").strip().upper() if ch.isalnum())


class ProjectRecordCreationError(RuntimeError):
    """Base error for project record creation failures."""


class ProjectKeyRequiredError(ProjectRecordCreationError):
    """Raised when `project_key` is missing/blank."""


class ProjectKeyInvalidError(ProjectRecordCreationError):
    """Raised when `project_key` is not valid."""


class ProjectKeyConflictError(ProjectRecordCreationError):
    """Raised when a project already exists for the given user + key."""


def create_project_record(
    user_data: UserData,
    name: str,
    description: str,
    project_key: str,
    creation_status: str,
    creation_source: Optional[str] = None,
) -> ProjectRecordCreationData:
    """
    Create the initial project record in Firestore with ownership metadata.

    Behavior:
    1) Validates `project_key` and uniqueness per user.
    2) Inserts a project document with creation status/source.
    3) Ensures the creator has a user profile and project membership.

    Returns:
        ProjectRecordCreationData: Includes `project_id` and a minimal project payload.
    """
    if not project_key:
        raise ProjectKeyRequiredError("project_key is required.")

    normalized_key = normalize_project_key(project_key)
    if len(normalized_key) != PROJECT_KEY_LENGTH:
        raise ProjectKeyInvalidError(
            f"project_key must be exactly {PROJECT_KEY_LENGTH} alphanumeric characters."
        )

    project_key = normalized_key

    existing_projects = FIRESTORE_CLIENT.collection("projects").where(
        "user_id", "==", user_data.get_user_id()
    ).where("project_key", "==", project_key).get()

    if existing_projects:
        raise ProjectKeyConflictError("Project with this key already exists for this user.")

    now = current_timestamp_iso()
    owner_email = user_data.get_email()
    project_data = {
        "user_id": user_data.get_user_id(),
        "name": name,
        "description": description,
        "project_key": project_key,
        "projectLead": owner_email,
        "created_at": now,
        "updated_at": now,
        "creation_status": creation_status,
    }
    if creation_source:
        project_data["creation_source"] = creation_source

    doc_ref = FIRESTORE_CLIENT.collection("projects").add(project_data)
    project_id = doc_ref[1].id
    owner_name = user_data.get_user_name() or owner_email
    owner_avatar = "".join(part[0].upper() for part in owner_name.split()[:2] if part) or owner_email[:2].upper()

    upsert_user_profile(
        user_id=user_data.get_user_id(),
        email=owner_email,
        name=owner_name,
        role="leader",
    )
    create_team_member(
        project_id=project_id,
        user_id=user_data.get_user_id(),
        name=owner_name,
        email=owner_email,
        role="PM",
        seniority="Lead",
        avatar=owner_avatar,
        member_type="leader",
    )
    upsert_project_membership(
        project_id=project_id,
        user_id=user_data.get_user_id(),
        email=owner_email,
        role="leader",
        project_role="owner",
    )

    return ProjectRecordCreationData(
        project_id=project_id,
        project=ProjectCreationProjectData(
            id=project_id,
            name=name,
            description=description,
            project_key=project_key,
        ),
    )
