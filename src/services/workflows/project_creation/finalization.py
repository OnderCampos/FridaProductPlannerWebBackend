"""
Project finalization workflow.

This module is invoked when the user accepts the generated specification document
(`POST /project/{project_id}/spec/accept`). It turns the stored knowledge context
into persisted epics (and optionally user stories) and marks the project as finalized.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.project_finalization import FinalizeProjectCreationData
from src.schemas.user_data import UserData
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.services.workflows.project_creation.common import current_timestamp_iso
from src.utils.planning.epic_generation import generate_epics
from src.utils.planning.epics import create_epic, get_epics_for_project
from src.utils.planning.user_story_dependencies import generate_and_persist_user_story_dependencies
from src.utils.planning.user_stories import create_multiple_user_stories

logger = logging.getLogger(__name__)

PROJECTS_COLLECTION = "projects"
PROJECT_KNOWLEDGE_COLLECTION = "project_knowledge"
PROJECT_SPECS_COLLECTION = "project_specs"

SPEC_READY_STATUS = "spec_ready"
FINALIZED_STATUS = "finalized"


class ProjectFinalizationError(RuntimeError):
    """Base error for project finalization workflow failures."""


class ProjectNotFoundError(ProjectFinalizationError):
    """Raised when the project document does not exist."""


class ProjectNotReadyError(ProjectFinalizationError):
    """Raised when the project/spec state is not ready for finalization."""

    def __init__(self, *, status: str):
        super().__init__(f"Project is not ready to finalize (status={status}).")
        self.status = status


class EpicGenerationFailedError(ProjectFinalizationError):
    """Raised when the epic generation graph fails."""


# =====================
# String/normalization helpers
# =====================
def _slugify(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in (value or "").lower())
    return "_".join(part for part in cleaned.split("_") if part)


def _normalize_bullets(value: Any) -> List[str]:
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
        for prefix in ("- ", "* ", "• ", "â€¢ "):
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
                break
        if line and line[0].isdigit():
            if len(line) >= 3 and line[1] in {".", ")"} and line[2] == " ":
                line = line[3:].strip()
        if line:
            items.append(line)
    return items


def _should_create_user_stories(creation_source: str) -> bool:
    source = (creation_source or "").strip().lower()
    return source not in {"qa"}


def _normalize_extracted_epics(raw_epics: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_epics, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in raw_epics:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()

        roles = item.get("roles")
        if not isinstance(roles, list):
            roles = []
        roles = [str(role).strip() for role in roles if str(role).strip()]

        technologies = item.get("technologies")
        if not isinstance(technologies, list):
            technologies = []
        technologies = [str(tag).strip() for tag in technologies if str(tag).strip()]

        keywords = item.get("keywords")
        if not isinstance(keywords, list):
            keywords = []
        keywords = [str(tag).strip() for tag in keywords if str(tag).strip()]

        labels = item.get("labels", [])
        if not isinstance(labels, list):
            labels = []

        if not name and not description:
            continue
        if not name:
            name = "Untitled Epic"

        normalized.append(
            {
                "name": name,
                "description": description,
                "labels": labels,
                "roles": roles,
                "technologies": technologies,
                "keywords": keywords,
                "priority": item.get("priority"),
                "status": item.get("status"),
                "storyPoints": item.get("storyPoints"),
            }
        )

    return normalized


def _normalize_extracted_stories(
    raw_stories: Any,
    epic_names: List[str],
) -> List[Dict[str, Any]]:
    if not isinstance(raw_stories, list):
        return []

    normalized: List[Dict[str, Any]] = []
    epic_lookup = {name.lower(): name for name in epic_names if name}
    fallback_epic = epic_names[0] if epic_names else ""
    per_epic_order: Dict[str, int] = {}

    for item in raw_stories:
        if not isinstance(item, dict):
            continue

        epic_name = str(item.get("epic") or "").strip()
        epic_key = epic_name.lower()
        if epic_key not in epic_lookup:
            if not fallback_epic:
                continue
            epic_name = fallback_epic
        else:
            epic_name = epic_lookup[epic_key]

        user_story = str(item.get("user_story") or "").strip()
        if not user_story:
            continue

        description = str(item.get("description") or "").strip()
        story_id = str(item.get("user_story_id") or "").strip()
        if not story_id:
            # Create a stable, readable story ID when the extractor didn't provide one.
            story_id = _slugify(user_story)[:40] or "story"

        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list):
            dependencies = []
        dependencies = [str(dep).strip() for dep in dependencies if str(dep).strip()]

        acceptance_criteria = _normalize_bullets(
            item.get("acceptanceCriteria") or item.get("acceptance_criteria")
        )
        if not acceptance_criteria:
            acceptance_criteria = ["Not provided."]

        out_of_scope = _normalize_bullets(item.get("outOfScope") or item.get("out_of_scope"))
        if not out_of_scope:
            out_of_scope = ["N/A"]

        effort_hours = item.get("effortHours")
        try:
            effort_hours = float(effort_hours) if effort_hours is not None else 0
        except (TypeError, ValueError):
            effort_hours = 0

        per_epic_order[epic_name] = per_epic_order.get(epic_name, 0) + 1
        order = item.get("order")
        if not isinstance(order, int):
            order = per_epic_order[epic_name]

        normalized.append(
            {
                "epic": epic_name,
                "user_story": user_story,
                "description": description,
                "acceptanceCriteria": acceptance_criteria,
                "outOfScope": out_of_scope,
                "user_story_id": story_id,
                "order": order,
                "dependencies": dependencies,
                "effortHours": effort_hours,
            }
        )

    return normalized


def _build_enriched_description(
    *,
    base_description: str,
    qa_history: List[Dict[str, Any]],
    spec_text: str,
) -> str:
    enriched_parts: List[str] = [base_description or ""]

    if qa_history:
        enriched_parts.append("Clarification Q&A:")
        for entry in qa_history:
            if not isinstance(entry, dict):
                continue
            question = str(entry.get("question") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            if question or answer:
                enriched_parts.append(f"Q: {question}\nA: {answer}")

    if spec_text:
        enriched_parts.append("Specification Document:")
        enriched_parts.append(spec_text)

    return "\n\n".join([part for part in enriched_parts if part]).strip()


# =====================
# Firestore helpers
# =====================
def _fetch_project(project_id: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    # Read the project once and keep the document reference so we can update it during finalization.
    project_ref = FIRESTORE_CLIENT.collection(PROJECTS_COLLECTION).document(project_id)
    # Resolve the current project state (name/description/creation_status/creation_source, etc.).
    project_doc = project_ref.get()
    if not project_doc.exists:
        return None, None
    return project_ref, project_doc.to_dict() or {}


def _fetch_project_knowledge(project_id: str) -> Dict[str, Any]:
    # Load the knowledge doc produced by the creation flow (spec text, extracted epics, Q&A history, etc.).
    knowledge_doc = FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).get()
    return knowledge_doc.to_dict() if knowledge_doc.exists else {}


def _is_finalized(project: Dict[str, Any], knowledge: Dict[str, Any]) -> bool:
    project_status = str(project.get("creation_status") or "").strip().lower()
    knowledge_status = str(knowledge.get("status") or "").strip().lower()
    return project_status == FINALIZED_STATUS or knowledge_status == FINALIZED_STATUS


def _is_spec_ready(project: Dict[str, Any], knowledge: Dict[str, Any]) -> bool:
    project_status = str(project.get("creation_status") or "").strip().lower()
    knowledge_status = str(knowledge.get("status") or "").strip().lower()
    return project_status == SPEC_READY_STATUS or knowledge_status == SPEC_READY_STATUS


def _mark_project_finalized(*, project_ref: Any, now: str, update_data: Dict[str, Any]) -> None:
    # Persist the final project state so the frontend can treat it as ready (and prevent re-finalization).
    payload = {"updated_at": now, "creation_status": FINALIZED_STATUS, **(update_data or {})}
    # Write to Firestore.
    project_ref.update(payload)


def _mark_knowledge_finalized(project_id: str, now: str) -> None:
    # Mark the knowledge doc as finalized so subsequent calls can be handled idempotently.
    FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).set(
        {"status": FINALIZED_STATUS, "updated_at": now},
        merge=True,
    )


def _persist_spec_override(project_id: str, spec_text: str, now: str) -> None:
    cleaned_spec = str(spec_text or "").strip()
    FIRESTORE_CLIENT.collection(PROJECT_KNOWLEDGE_COLLECTION).document(project_id).set(
        {
            "spec_text": cleaned_spec,
            "updated_at": now,
        },
        merge=True,
    )
    FIRESTORE_CLIENT.collection(PROJECT_SPECS_COLLECTION).document(project_id).set(
        {
            "project_id": project_id,
            "spec_text": cleaned_spec,
            "updated_at": now,
        },
        merge=True,
    )


# =====================
# Creation helpers
# =====================
def _create_epics_from_payload(
    *,
    user_data: UserData,
    project_id: str,
    epics: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    created_epics: List[Dict[str, Any]] = []
    epic_id_by_name: Dict[str, str] = {}

    for epic in epics:
        # Persist each epic using the existing planning util (ensures consistent schema + timestamps).
        epic_response = create_epic(project_id, user_data.get_user_id(), epic)
        if not (epic_response.success and isinstance(epic_response.data, dict)):
            continue

        created_epics.append(epic_response.data)
        epic_name = str(epic_response.data.get("name") or "").strip()
        epic_id = str(epic_response.data.get("id") or "").strip()
        if epic_name and epic_id:
            epic_id_by_name[epic_name.lower()] = epic_id

    return created_epics, epic_id_by_name


async def _create_user_stories_for_epics(
    *,
    user_data: UserData,
    epic_id_by_name: Dict[str, str],
    user_stories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    created_stories: List[Dict[str, Any]] = []

    for epic_name, epic_id in epic_id_by_name.items():
        stories_for_epic = [
            story
            for story in user_stories
            if str(story.get("epic") or "").strip().lower() == epic_name
        ]
        if not stories_for_epic:
            continue

        # Bulk-create user stories for this epic via the existing util (ensures correct story schema).
        stories_response = create_multiple_user_stories(
            epic_id=epic_id,
            user_id=user_data.get_user_id(),
            user_stories_list=stories_for_epic,
        )
        if stories_response.success and isinstance(stories_response.data, list):
            saved_stories = stories_response.data
            dependencies_response = await generate_and_persist_user_story_dependencies(
                user_data=user_data,
                epic_id=epic_id,
                user_stories=saved_stories,
            )
            if (
                dependencies_response.success
                and isinstance(dependencies_response.data, dict)
                and isinstance(dependencies_response.data.get("user_stories"), list)
            ):
                created_stories.extend(dependencies_response.data.get("user_stories") or [])
            else:
                if not dependencies_response.success:
                    logger.warning(
                        "Finalization saved user stories for epic %s without refreshed dependencies: %s",
                        epic_id,
                        dependencies_response.message,
                    )
                created_stories.extend(saved_stories)

    return created_stories


async def _create_extracted_epics_and_stories(
    *,
    user_data: UserData,
    project_id: str,
    epics: List[Dict[str, Any]],
    user_stories: List[Dict[str, Any]],
    create_user_stories: bool,
) -> Dict[str, Any]:
    # Create epics first so we have epic IDs that user stories can attach to.
    created_epics, epic_id_by_name = _create_epics_from_payload(
        user_data=user_data,
        project_id=project_id,
        epics=epics,
    )

    if not create_user_stories:
        return {"epics": created_epics, "user_stories": []}

    # Create user stories only for flows that expect them to be persisted here.
    created_stories = await _create_user_stories_for_epics(
        user_data=user_data,
        epic_id_by_name=epic_id_by_name,
        user_stories=user_stories,
    )
    return {"epics": created_epics, "user_stories": created_stories}


# =====================
# Response helpers
# =====================
def _build_finalization_response(
    *,
    project_id: str,
    project: Dict[str, Any],
    project_description: str,
    technical_stack: List[Any],
    roles: List[Any],
    epics: List[Dict[str, Any]],
) -> FinalizeProjectCreationData:
    return FinalizeProjectCreationData(
        id=project_id,
        name=str(project.get("name") or ""),
        project_description=project_description,
        technical_stack=technical_stack,
        roles=roles,
        project_key=str(project.get("project_key") or ""),
        epics=epics,
    )


def _finalized_response(project_id: str, project: Dict[str, Any], user_id: str) -> FinalizeProjectCreationData:
    project_description = str(project.get("ai_project_description") or project.get("description") or "")
    technical_stack = project.get("technical_stack") or []
    roles = project.get("roles") or []
    # Load already-persisted epics so a repeated finalization call returns the current project state.
    epics = get_epics_for_project(project_id, user_id)
    return _build_finalization_response(
        project_id=project_id,
        project=project,
        project_description=project_description,
        technical_stack=technical_stack if isinstance(technical_stack, list) else [],
        roles=roles if isinstance(roles, list) else [],
        epics=epics if isinstance(epics, list) else [],
    )


# =====================
# Finalization flows
# =====================
async def _finalize_from_extracted_knowledge(
    *,
    user_data: UserData,
    project_id: str,
    project_ref: Any,
    project: Dict[str, Any],
    knowledge: Dict[str, Any],
) -> Optional[FinalizeProjectCreationData]:
    extracted_epics = knowledge.get("extracted_epics") or []
    extracted_user_stories = knowledge.get("extracted_user_stories") or []

    # If extraction already produced epics, prefer persisting them directly (no need to re-generate).
    normalized_epics = _normalize_extracted_epics(extracted_epics)
    if not normalized_epics:
        return None

    epic_names = [str(epic.get("name") or "") for epic in normalized_epics if epic.get("name")]
    # Normalize/align stories so they reference a real epic name (and fill in missing IDs/orders).
    normalized_stories = _normalize_extracted_stories(extracted_user_stories, epic_names)

    # Use one timestamp across all writes for consistency.
    now = current_timestamp_iso()
    creation_source = str(project.get("creation_source") or "").strip().lower()

    # Persist extracted epics and (only when appropriate) their user stories.
    created_payload = await _create_extracted_epics_and_stories(
        user_data=user_data,
        project_id=project_id,
        epics=normalized_epics,
        user_stories=normalized_stories,
        # Some creation sources (file/QA/document) don't expect user stories to be created here.
        create_user_stories=_should_create_user_stories(creation_source),
    )
    created_epics = created_payload.get("epics", [])

    base_description = str(project.get("description") or "")
    extracted_description = str(knowledge.get("extracted_project_description") or "").strip()
    extracted_roles = knowledge.get("extracted_roles") or []
    extracted_technical_stack = knowledge.get("extracted_technical_stack") or []

    update_data: Dict[str, Any] = {}
    if extracted_description:
        update_data["ai_project_description"] = extracted_description
        if not base_description:
            update_data["description"] = extracted_description
    if isinstance(extracted_roles, list) and extracted_roles:
        update_data["roles"] = extracted_roles
    if isinstance(extracted_technical_stack, list) and extracted_technical_stack:
        update_data["technical_stack"] = extracted_technical_stack

    # Mark the project + knowledge as finalized so future calls are idempotent.
    _mark_project_finalized(project_ref=project_ref, now=now, update_data=update_data)
    _mark_knowledge_finalized(project_id, now)

    return _build_finalization_response(
        project_id=project_id,
        project=project,
        project_description=extracted_description or base_description,
        technical_stack=extracted_technical_stack if isinstance(extracted_technical_stack, list) else [],
        roles=extracted_roles if isinstance(extracted_roles, list) else [],
        epics=created_epics if isinstance(created_epics, list) else [],
    )


async def _finalize_from_epic_generation(
    *,
    user_data: UserData,
    project_id: str,
    project_ref: Any,
    project: Dict[str, Any],
    enriched_description: str,
) -> FinalizeProjectCreationData:
    # Fall back to the epic generation graph when no extracted epics are available in knowledge.
    epics_result = await generate_epics(
        user_data=user_data,
        project_name=str(project.get("name") or ""),
        project_description=enriched_description,
    )

    # Stop early with a helpful error if the generation graph fails.
    if not epics_result.is_success():
        # Pull details from FunctionResult so the caller can surface them to the UI.
        error_details = epics_result.get_error()
        message = epics_result.message or "Error generating epics"
        if error_details:
            message = f"{message}: {error_details}"
        raise EpicGenerationFailedError(message)

    # Extract the graph output payload (project_description/roles/technical_stack/epics).
    epic_data = epics_result.get_data() or {}
    epics_list = epic_data.get("epics") or []
    if not isinstance(epics_list, list):
        epics_list = []

    # Persist generated epics into Firestore so the project can be managed in the planner UI.
    created_epics, _ = _create_epics_from_payload(
        user_data=user_data,
        project_id=project_id,
        epics=epics_list,
    )

    # Use one timestamp across all writes for consistency.
    now = current_timestamp_iso()
    project_description = str(epic_data.get("project_description") or "").strip()
    technical_stack = epic_data.get("technical_stack") or []
    roles = epic_data.get("roles") or []

    update_data = {
        "ai_project_description": project_description,
        "technical_stack": technical_stack if isinstance(technical_stack, list) else [],
        "roles": roles if isinstance(roles, list) else [],
    }
    # Mark the project + knowledge as finalized so future calls are idempotent.
    _mark_project_finalized(project_ref=project_ref, now=now, update_data=update_data)
    _mark_knowledge_finalized(project_id, now)

    base_description = str(project.get("description") or "")
    return _build_finalization_response(
        project_id=project_id,
        project=project,
        project_description=project_description or base_description,
        technical_stack=technical_stack if isinstance(technical_stack, list) else [],
        roles=roles if isinstance(roles, list) else [],
        epics=created_epics,
    )


# =====================
# Public API
# =====================
async def finalize_project_creation(
    *,
    user_data: UserData,
    project_id: str,
    spec_text_override: Optional[str] = None,
) -> FinalizeProjectCreationData:
    """
    Finalize a project's creation flow using the stored knowledge context.

    Requires the project to be in the `spec_ready` state (or already finalized).
    """
    # Load the project and keep the reference for later updates.
    project_ref, project = _fetch_project(project_id)
    if project_ref is None or project is None:
        raise ProjectNotFoundError("Project not found")

    # Load workflow knowledge (spec text, extracted epics/stories, Q&A history).
    knowledge = _fetch_project_knowledge(project_id)

    # If this project was already finalized, return persisted data instead of creating duplicates.
    if _is_finalized(project, knowledge):
        return _finalized_response(project_id, project, user_data.get_user_id())

    # Prevent finalization until the spec is ready (otherwise generation runs on incomplete context).
    if not _is_spec_ready(project, knowledge):
        status = str(project.get("creation_status") or "").strip() or "unknown"
        raise ProjectNotReadyError(status=status)

    cleaned_override = str(spec_text_override or "").strip()
    if cleaned_override:
        now = current_timestamp_iso()
        _persist_spec_override(project_id, cleaned_override, now)
        knowledge["spec_text"] = cleaned_override

    qa_history = knowledge.get("qa_history") or []
    if not isinstance(qa_history, list):
        qa_history = []
    spec_text = str(knowledge.get("spec_text") or "").strip()

    base_description = str(project.get("description") or "")
    # Build a single context string for epic generation fallback.
    enriched_description = _build_enriched_description(
        base_description=base_description,
        qa_history=qa_history,
        spec_text=spec_text,
    )

    # Prefer extracted epics (document/figma workflows) when available.
    extracted_response = await _finalize_from_extracted_knowledge(
        user_data=user_data,
        project_id=project_id,
        project_ref=project_ref,
        project=project,
        knowledge=knowledge,
    )
    if extracted_response is not None:
        return extracted_response

    # Otherwise, generate epics using the enriched description and persist them.
    return await _finalize_from_epic_generation(
        user_data=user_data,
        project_id=project_id,
        project_ref=project_ref,
        project=project,
        enriched_description=enriched_description,
    )


async def finalize_project_from_spec(
    user_data: UserData,
    project_id: str,
    spec_text_override: Optional[str] = None,
) -> FinalizeProjectCreationData:
    """
    Backwards-compatible name for the finalization step.

    Prefer `finalize_project_creation(...)` going forward.
    """
    return await finalize_project_creation(
        user_data=user_data,
        project_id=project_id,
        spec_text_override=spec_text_override,
    )
