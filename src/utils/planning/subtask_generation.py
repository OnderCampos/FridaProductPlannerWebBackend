from typing import Any, Dict, List, Optional
import json
import logging
import re
from datetime import datetime, timezone

from src.prompts.subtask_generation import GENERATE_SUBTASKS_PROMPT
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.utils.authz.permissions import get_project_access
from src.utils.core.general import get_code_block
from src.utils.planning.epics import get_epic_by_id, get_epics_for_project
from src.utils.planning.user_stories import get_user_stories_by_epic, get_user_story_by_id
from src.utils.firebase.identifier import get_next_TK_identifier
from src.schemas.workflow_status import (
    WORKFLOW_STATUS_VALUES,
    coerce_workflow_status,
    normalize_workflow_status,
)

TASK_TYPE_VALUES = {"Implementation", "Bug", "Refactor", "Research"}
LEGACY_TASK_TYPE_MAP = {
    "development": "Implementation",
    "testing": "Implementation",
    "documentation": "Implementation",
    "review": "Implementation",
    "bug fix": "Bug",
    "implementation": "Implementation",
    "bug": "Bug",
    "refactor": "Refactor",
    "research": "Research",
}

TASK_ESTIMATION_PROMPT = """
You are estimating implementation effort for a single software task.

Task type: {task_type}
Title: {title}
Description: {description}

Return only valid JSON with this shape:
{{
  "estimated_hours": number,
  "complexity": "Low" | "Medium" | "High" | "Critical"
}}

Rules:
- Estimate realistic engineering hours for one developer task.
- `estimated_hours` must be greater than 0.
- Prefer practical ranges:
  - 1-3 for small contained work
  - 4-8 for medium work
  - 9-16 for larger work
  - 17+ only when clearly substantial
- Keep the answer concise and return JSON only.
""".strip()

TASK_TIPS_PROMPT = """
You are a senior software engineer creating conceptual guidance for a single software task.

You do NOT have access to the project's source code, repository, runtime, logs, or database.
Your guidance must stay high-level and discovery-oriented.

Project tech stack: {tech_stack}
Task type: {task_type}
Title: {title}
Description: {description}

Write Markdown only.

Required structure:
## Problem Summary
## Where to Start
## What to Research
## Conceptual Approaches
## Risks and Edge Cases

Rules:
- Be practical, but stay conceptual.
- Focus on how an engineer should frame the problem before coding.
- Suggest first steps, useful questions, and topics to investigate.
- If mentioning the tech stack, keep it at the level of areas to inspect or documentation to review.
- Do not claim knowledge of specific files, functions, components, endpoints, schemas, or current code behavior unless explicitly stated in the task title or description.
- Do not provide code, pseudocode, exact implementation steps, or instructions that assume direct codebase access.
- Use bullets where appropriate.
- Do not wrap the response in code fences.
""".strip()

PROJECT_TASK_BATCH_PROMPT = """
You are a project planning agent.

Your job is to read freeform planning text and identify the independent project tasks that should be created from it.

Input text:
{source_text}

Return only valid JSON with this shape:
{{
  "tasks": [
    {{
      "title": "Short action-oriented title",
      "description": "Clear scope for the task",
      "task_type": "Implementation" | "Bug" | "Research" | "Refactor"
    }}
  ]
}}

Rules:
- Create 1 to 10 tasks.
- Each task must be independent enough to track on a task board.
- Use concise, concrete titles.
- Keep descriptions practical and implementation-oriented, but not overly detailed.
- Infer the most appropriate `task_type` for each task.
- Do not create duplicate tasks.
- Do not include IDs, estimates, assignees, status, or dependencies.
- Return JSON only.
""".strip()


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_task_type(value: Any, default: str = "Implementation") -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return default
    if normalized in TASK_TYPE_VALUES:
        return normalized
    return LEGACY_TASK_TYPE_MAP.get(normalized.lower(), default)


def _get_project_tech_stack(project_id: str) -> List[str]:
    try:
        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
        if not project_doc.exists:
            return []

        project_data = project_doc.to_dict() or {}
        tech_stack = project_data.get("technical_stack") or project_data.get("techStack") or []
        if not isinstance(tech_stack, list):
            return []

        return [str(item).strip() for item in tech_stack if str(item).strip()]
    except Exception as exc:
        logging.warning(f"Failed to load project tech stack for task tips in project {project_id}: {exc}")
        return []


def _serialize_subtask(subtask_data: Dict[str, Any], subtask_id: Optional[str] = None) -> Dict[str, Any]:
    serialized = dict(subtask_data or {})
    if subtask_id:
        serialized["id"] = subtask_id

    user_story_id = str(serialized.get("user_story_id") or "").strip()
    task_type = _normalize_task_type(
        serialized.get("task_type") or serialized.get("type"),
        default="Implementation",
    )

    serialized["source"] = "story" if user_story_id else str(serialized.get("source") or "project").strip() or "project"
    if serialized["source"] not in {"story", "project"}:
        serialized["source"] = "story" if user_story_id else "project"

    serialized["task_type"] = task_type
    serialized["task_id"] = str(serialized.get("task_id") or "").strip()
    serialized["type"] = task_type
    serialized["status"] = coerce_workflow_status(serialized.get("status"), default="To Do")
    serialized["dependencies"] = serialized.get("dependencies") or []
    serialized["estimated_hours"] = serialized.get("estimated_hours", 0)
    serialized["complexity"] = serialized.get("complexity") or "Medium"
    serialized["tips_markdown"] = str(serialized.get("tips_markdown") or serialized.get("tipsMarkdown") or "").strip()
    serialized["story_title"] = str(serialized.get("story_title") or "").strip()
    serialized["epic_name"] = str(serialized.get("epic_name") or "").strip()
    serialized["sprint_id"] = serialized.get("sprint_id")
    serialized["assignee_email"] = serialized.get("assignee_email") or serialized.get("assigneeEmail")
    return serialized


def _normalize_positive_hours(value: Any, default: float = 2.0) -> float:
    try:
        numeric = float(value)
        if numeric > 0:
            return round(numeric, 1)
    except (TypeError, ValueError):
        pass
    return default


def _normalize_complexity(value: Any, default: str = "Medium") -> str:
    normalized = str(value or "").strip().title()
    if normalized in {"Low", "Medium", "High", "Critical"}:
        return normalized
    return default


async def _estimate_project_task_fields(
    user_data: UserData,
    title: str,
    description: str,
    task_type: str,
) -> Dict[str, Any]:
    fallback = {
        "estimated_hours": 2.0,
        "complexity": "Medium",
    }

    try:
        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        raw = await azure_services.simple_completion(
            TASK_ESTIMATION_PROMPT.format(
                task_type=task_type,
                title=title.strip() or "Untitled task",
                description=description.strip() or "No additional description provided.",
            )
        )

        payload_text = get_code_block(raw) or raw
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", payload_text, re.DOTALL)
            parsed = json.loads(match.group(0)) if match else {}

        return {
            "estimated_hours": _normalize_positive_hours(parsed.get("estimated_hours"), fallback["estimated_hours"]),
            "complexity": _normalize_complexity(parsed.get("complexity"), fallback["complexity"]),
        }
    except Exception as exc:
        logging.warning(f"Falling back to default task estimation for '{title}': {exc}")
        return fallback


async def _generate_project_task_tips(
    user_data: UserData,
    title: str,
    description: str,
    task_type: str,
    tech_stack: Optional[List[str]] = None,
) -> str:
    fallback_title = title.strip() or "Untitled task"
    fallback_description = description.strip() or "No additional description provided."
    tech_stack_values = [str(item).strip() for item in (tech_stack or []) if str(item).strip()]
    tech_stack_text = ", ".join(tech_stack_values) if tech_stack_values else "Not specified"
    fallback = (
        "## Problem Summary\n"
        f"- {fallback_title}\n\n"
        "## Where to Start\n"
        "- Clarify the expected outcome, who uses it, and what success looks like.\n"
        "- Identify the product area, workflow, or user journey most affected by this task.\n"
        f"- Use the task type ({task_type}) and the project tech stack ({tech_stack_text}) to decide which area to inspect first.\n\n"
        "## What to Research\n"
        "- Existing product behavior and current limitations related to this task.\n"
        "- Relevant framework, library, or platform documentation tied to the affected area.\n"
        "- Similar patterns already used elsewhere in the product, if any.\n"
        "- Constraints around validation, permissions, loading states, failures, and empty states.\n\n"
        "## Conceptual Approaches\n"
        "- Break the work into a small discovery phase and a small delivery phase.\n"
        "- Prefer the smallest change that resolves the user problem without expanding scope.\n"
        f"- Use this description as context while validating assumptions: {fallback_description}\n\n"
        "## Risks and Edge Cases\n"
        "- Ambiguous scope can lead to rework.\n"
        "- Hidden dependencies may exist in adjacent flows or shared data.\n"
        "- Permissions, invalid inputs, empty states, and failure handling should be verified early.\n"
    )

    try:
        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        raw = await azure_services.simple_completion(
            TASK_TIPS_PROMPT.format(
                tech_stack=tech_stack_text,
                task_type=task_type,
                title=fallback_title,
                description=fallback_description,
            )
        )
        normalized = str(raw or "").strip()
        return normalized or fallback
    except Exception as exc:
        logging.warning(f"Falling back to default task tips for '{title}': {exc}")
        return fallback


async def _generate_project_tasks_from_text(
    user_data: UserData,
    source_text: str,
) -> List[Dict[str, str]]:
    cleaned_text = str(source_text or "").strip()
    if not cleaned_text:
        return []

    fallback_task = {
        "title": "Review and break down request",
        "description": cleaned_text[:500],
        "task_type": "Implementation",
    }

    try:
        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        raw = await azure_services.simple_completion(
            PROJECT_TASK_BATCH_PROMPT.format(source_text=cleaned_text)
        )
        payload_text = get_code_block(raw) or raw
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", payload_text, re.DOTALL)
            parsed = json.loads(match.group(0)) if match else {}

        tasks = parsed.get("tasks") if isinstance(parsed, dict) else None
        if not isinstance(tasks, list):
            return [fallback_task]

        normalized_tasks: List[Dict[str, str]] = []
        seen_titles = set()
        for task in tasks[:10]:
            if not isinstance(task, dict):
                continue
            title = str(task.get("title") or "").strip()
            description = str(task.get("description") or "").strip()
            task_type = _normalize_task_type(task.get("task_type"))
            if not title:
                continue
            dedupe_key = title.lower()
            if dedupe_key in seen_titles:
                continue
            seen_titles.add(dedupe_key)
            normalized_tasks.append(
                {
                    "title": title,
                    "description": description,
                    "task_type": task_type,
                }
            )

        return normalized_tasks or [fallback_task]
    except Exception as exc:
        logging.warning(f"Falling back to single batch task for source text: {exc}")
        return [fallback_task]


async def _build_project_task_document(
    project_id: str,
    user_data: UserData,
    task_data: Dict[str, Any],
) -> Dict[str, Any]:
    now = _current_timestamp_iso()
    task_identifier = get_next_TK_identifier()
    order = int(task_identifier.split("-")[1])

    normalized_task_type = _normalize_task_type(task_data.get("task_type") or task_data.get("type"))
    title = str(task_data.get("title") or "").strip()
    description = str(task_data.get("description") or "").strip()
    tech_stack = _get_project_tech_stack(project_id)
    estimated_fields = await _estimate_project_task_fields(
        user_data=user_data,
        title=title,
        description=description,
        task_type=normalized_task_type,
    )
    tips_markdown = await _generate_project_task_tips(
        user_data=user_data,
        title=title,
        description=description,
        task_type=normalized_task_type,
        tech_stack=tech_stack,
    )
    return {
        "project_id": project_id,
        "user_id": user_data.get_user_id(),
        "source": "project",
        "task_id": task_identifier,
        "title": title,
        "description": description,
        "order": order,
        "estimated_hours": estimated_fields["estimated_hours"],
        "complexity": estimated_fields["complexity"],
        "tips_markdown": tips_markdown,
        "dependencies": task_data.get("dependencies", []),
        "task_type": normalized_task_type,
        "type": normalized_task_type,
        "status": coerce_workflow_status(task_data.get("status"), default="To Do"),
        "completed_date": None,
        "created_at": now,
        "updated_at": now,
        "assignee": task_data.get("assignee", ""),
        "assigneeId": task_data.get("assigneeId"),
        "assigneeEmail": task_data.get("assigneeEmail"),
        "assignee_email": task_data.get("assignee_email") or task_data.get("assigneeEmail"),
        "assigned_to": task_data.get("assigned_to"),
    }


def _attach_sprint_ids(tasks: List[Dict[str, Any]]) -> None:
    task_ids = [str(task.get("id") or "").strip() for task in tasks if str(task.get("id") or "").strip()]
    if not task_ids:
        return

    sprint_map: Dict[str, str] = {}
    chunk_size = 30
    for index in range(0, len(task_ids), chunk_size):
        chunk = task_ids[index:index + chunk_size]
        assignments = (
            FIRESTORE_CLIENT.collection("sprint_items")
            .where("item_type", "==", "subtask")
            .where("item_id", "in", chunk)
            .get()
        )
        for assignment in assignments:
            assignment_data = assignment.to_dict() or {}
            item_id = str(assignment_data.get("item_id") or "").strip()
            if item_id:
                sprint_map[item_id] = assignment_data.get("sprint_id")

    for task in tasks:
        task["sprint_id"] = sprint_map.get(str(task.get("id") or "").strip())


def _build_story_context(
    story_id: str,
    user_id: str,
    allow_member: bool = False,
    user_email: Optional[str] = None,
) -> Dict[str, Any]:
    story_response = get_user_story_by_id(
        story_id,
        user_id,
        allow_member=allow_member,
        user_email=user_email,
    )
    if not story_response.success or not isinstance(story_response.data, dict):
        return {}

    story_data = story_response.data
    epic_id = str(story_data.get("epic_id") or "").strip()
    project_id = ""
    epic_name = ""
    if epic_id:
        epic_response = get_epic_by_id(epic_id)
        if epic_response.success and isinstance(epic_response.data, dict):
            project_id = str(epic_response.data.get("project_id") or "").strip()
            epic_name = str(epic_response.data.get("name") or "").strip()

    return {
        "story_id": story_id,
        "story_title": str(story_data.get("user_story") or story_data.get("user_story_id") or "").strip(),
        "epic_id": epic_id,
        "epic_name": epic_name,
        "project_id": project_id,
    }


def _list_tasks_for_project_legacy(
    project_id: str,
    user_id: str,
    user_email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tasks_by_id: Dict[str, Dict[str, Any]] = {}
    epics = get_epics_for_project(project_id, user_id)
    story_map: Dict[str, Dict[str, Any]] = {}
    epic_name_map = {str(epic.get("id") or ""): str(epic.get("name") or "") for epic in epics}

    for epic in epics:
        epic_id = str(epic.get("id") or "").strip()
        if not epic_id:
            continue
        stories_response = get_user_stories_by_epic(epic_id, user_id, allow_member=True)
        if not stories_response.success:
            continue
        for story in stories_response.data or []:
            story_id = str(story.get("id") or "").strip()
            if not story_id:
                continue
            story_map[story_id] = story
            subtasks_response = get_subtasks_by_user_story(
                story_id,
                user_id,
                allow_member=True,
                user_email=user_email,
            )
            if not subtasks_response.success:
                continue
            for task in subtasks_response.data or []:
                tasks_by_id[str(task.get("id") or "").strip()] = task

    standalone_docs = FIRESTORE_CLIENT.collection("subtasks").where("project_id", "==", project_id).get()
    for doc in standalone_docs:
        if doc.id in tasks_by_id:
            continue
        task_data = doc.to_dict() or {}
        story_id = str(task_data.get("user_story_id") or "").strip()
        if story_id:
            story = story_map.get(story_id)
            task_data["story_title"] = task_data.get("story_title") or str(
                (story or {}).get("user_story") or (story or {}).get("user_story_id") or ""
            ).strip()
            epic_id = str((story or {}).get("epic_id") or task_data.get("epic_id") or "").strip()
            task_data["epic_id"] = epic_id
            task_data["epic_name"] = task_data.get("epic_name") or epic_name_map.get(epic_id, "")
        tasks_by_id[doc.id] = _serialize_subtask(task_data, doc.id)

    return list(tasks_by_id.values())


def save_subtasks_to_firestore(user_story_id: str, user_id: str, subtasks: List[Dict[str, Any]]) -> ResponseModel:
    try:
        saved_subtasks: List[Dict[str, Any]] = []
        now = _current_timestamp_iso()
        story_context = _build_story_context(user_story_id, user_id)
        project_id = str(story_context.get("project_id") or "").strip()
        if not project_id:
            return ResponseModel(
                success=False,
                message="Project not found for user story",
                data=None,
            )

        existing_subtasks = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", user_story_id).get()
        for doc in existing_subtasks:
            doc.reference.delete()

        for subtask_data in subtasks:
            task_identifier = get_next_TK_identifier()
            subtask_document = {
                "user_story_id": user_story_id,
                "user_id": user_id,
                "project_id": project_id,
                "source": "story",
                "task_id": task_identifier,
                "order": subtask_data.get("order", 0),
                "title": subtask_data.get("title", ""),
                "description": subtask_data.get("description", ""),
                "estimated_hours": subtask_data.get("estimated_hours", 0),
                "complexity": subtask_data.get("complexity", "Medium"),
                "dependencies": subtask_data.get("dependencies", []),
                "task_type": "Implementation",
                "type": "Implementation",
                "status": "To Do",
                "completed_date": None,
                "created_at": now,
                "updated_at": now,
            }

            doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(subtask_document)
            saved_subtasks.append(
                _serialize_subtask(
                    {
                        **subtask_document,
                        "story_title": story_context.get("story_title", ""),
                        "epic_id": story_context.get("epic_id", ""),
                        "epic_name": story_context.get("epic_name", ""),
                    },
                    doc_ref[1].id,
                )
            )

        return ResponseModel(
            success=True,
            message=f"Successfully saved {len(saved_subtasks)} subtasks",
            data=saved_subtasks,
        )
    except Exception as e:
        logging.error(f"Error saving subtasks: {e}")
        return ResponseModel(
            success=False,
            message=f"Error saving subtasks: {str(e)}",
            data=None,
        )


def get_subtasks_by_user_story(
    user_story_id: str,
    user_id: str,
    allow_member: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    try:
        story_context = _build_story_context(
            user_story_id,
            user_id,
            allow_member=allow_member,
            user_email=user_email,
        )
        if not story_context:
            return ResponseModel(success=False, message="Unauthorized: You don't have access to this story", data=None)

        query = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", user_story_id)
        if not allow_member:
            query = query.where("user_id", "==", user_id)

        subtasks_docs = query.get()
        subtasks: List[Dict[str, Any]] = []
        for doc in subtasks_docs:
            subtask_data = doc.to_dict()
            subtasks.append(
                _serialize_subtask(
                    {
                        **subtask_data,
                        "story_title": story_context.get("story_title", ""),
                        "epic_id": story_context.get("epic_id", ""),
                        "epic_name": story_context.get("epic_name", ""),
                        "project_id": subtask_data.get("project_id") or story_context.get("project_id"),
                    },
                    doc.id,
                )
            )

        if not subtasks:
            return ResponseModel(success=True, message="No subtasks found", data=[])

        _attach_sprint_ids(subtasks)
        subtasks.sort(key=lambda item: item.get("order", 0))

        return ResponseModel(
            success=True,
            message=f"Retrieved {len(subtasks)} subtasks",
            data=subtasks,
        )
    except Exception as e:
        logging.error(f"Error retrieving subtasks: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving subtasks: {str(e)}",
            data=None,
        )


def get_subtask_by_id(
    subtask_id: str,
    user_id: str,
    allow_member: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    try:
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()
        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        subtask_data = subtask_doc.to_dict() or {}
        if subtask_data.get("user_id") != user_id:
            if not allow_member:
                return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

            project_id = str(subtask_data.get("project_id") or "").strip()
            if project_id:
                access = get_project_access(project_id, user_id, user_email)
                if not access.success:
                    return ResponseModel(success=False, message="Unauthorized: You don't have access to this subtask", data=None)
            else:
                story_id = str(subtask_data.get("user_story_id") or "").strip()
                story_context = _build_story_context(
                    story_id,
                    user_id,
                    allow_member=True,
                    user_email=user_email,
                ) if story_id else {}
                if not story_context:
                    return ResponseModel(success=False, message="Unauthorized: You don't have access to this subtask", data=None)
                subtask_data["project_id"] = story_context.get("project_id")
                subtask_data["story_title"] = story_context.get("story_title")
                subtask_data["epic_id"] = story_context.get("epic_id")
                subtask_data["epic_name"] = story_context.get("epic_name")
        else:
            story_id = str(subtask_data.get("user_story_id") or "").strip()
            if story_id:
                story_context = _build_story_context(story_id, user_id)
                if story_context:
                    subtask_data["project_id"] = subtask_data.get("project_id") or story_context.get("project_id")
                    subtask_data["story_title"] = subtask_data.get("story_title") or story_context.get("story_title")
                    subtask_data["epic_id"] = subtask_data.get("epic_id") or story_context.get("epic_id")
                    subtask_data["epic_name"] = subtask_data.get("epic_name") or story_context.get("epic_name")

        task = _serialize_subtask(subtask_data, subtask_doc.id)
        _attach_sprint_ids([task])
        return ResponseModel(success=True, message="Subtask retrieved successfully", data=task)
    except Exception as e:
        logging.error(f"Error retrieving subtask {subtask_id}: {e}")
        return ResponseModel(success=False, message=f"Error retrieving subtask: {str(e)}", data=None)


def list_tasks_for_project(
    project_id: str,
    user_id: str,
    allow_member: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    try:
        access = get_project_access(project_id, user_id, user_email)
        if not access.success:
            return ResponseModel(success=False, message=access.message, data=None)
        if not allow_member and not access.data.get("is_owner"):
            return ResponseModel(success=False, message="Unauthorized: You don't own this project", data=None)

        subtasks_docs = FIRESTORE_CLIENT.collection("subtasks").where("project_id", "==", project_id).get()
        tasks: List[Dict[str, Any]] = []
        story_context_cache: Dict[str, Dict[str, Any]] = {}

        for doc in subtasks_docs:
            task_data = doc.to_dict() or {}
            story_id = str(task_data.get("user_story_id") or "").strip()
            if story_id and (
                not str(task_data.get("story_title") or "").strip()
                or not str(task_data.get("epic_name") or "").strip()
                or not str(task_data.get("epic_id") or "").strip()
            ):
                if story_id not in story_context_cache:
                    story_context_cache[story_id] = _build_story_context(
                        story_id,
                        user_id,
                        allow_member=True,
                        user_email=user_email,
                    )
                story_context = story_context_cache.get(story_id) or {}
                task_data["story_title"] = task_data.get("story_title") or story_context.get("story_title", "")
                task_data["epic_id"] = task_data.get("epic_id") or story_context.get("epic_id", "")
                task_data["epic_name"] = task_data.get("epic_name") or story_context.get("epic_name", "")
            tasks.append(_serialize_subtask(task_data, doc.id))

        if not tasks:
            logging.info(
                "No subtasks found by project_id for project %s. Falling back to legacy task listing.",
                project_id,
            )
            tasks = _list_tasks_for_project_legacy(project_id, user_id, user_email=user_email)

        _attach_sprint_ids(tasks)
        tasks.sort(
            key=lambda item: (
                0 if item.get("source") == "story" else 1,
                str(item.get("epic_name") or "").lower(),
                str(item.get("story_title") or "").lower(),
                int(item.get("order") or 0),
                str(item.get("title") or "").lower(),
            )
        )

        return ResponseModel(
            success=True,
            message=f"Retrieved {len(tasks)} tasks",
            data=tasks,
        )
    except Exception as e:
        logging.error(f"Error retrieving tasks for project {project_id}: {e}")
        return ResponseModel(success=False, message=f"Error retrieving tasks: {str(e)}", data=None)


async def create_project_task(project_id: str, user_data: UserData, task_data: Dict[str, Any]) -> ResponseModel:
    try:
        if not str(task_data.get("title") or "").strip():
            return ResponseModel(success=False, message="title is required", data=None)

        new_task = await _build_project_task_document(project_id, user_data, task_data)
        doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(new_task)
        task = _serialize_subtask(new_task, doc_ref[1].id)
        return ResponseModel(success=True, message="Task created successfully", data=task)
    except Exception as e:
        logging.error(f"Error creating project task: {e}")
        return ResponseModel(success=False, message=f"Error creating project task: {str(e)}", data=None)


async def batch_create_project_tasks_from_text(
    project_id: str,
    user_data: UserData,
    source_text: str,
) -> ResponseModel:
    try:
        generated_tasks = await _generate_project_tasks_from_text(user_data, source_text)
        created_tasks: List[Dict[str, Any]] = []

        for generated_task in generated_tasks:
            if not str(generated_task.get("title") or "").strip():
                continue
            new_task = await _build_project_task_document(project_id, user_data, generated_task)
            doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(new_task)
            created_tasks.append(_serialize_subtask(new_task, doc_ref[1].id))

        if not created_tasks:
            return ResponseModel(success=False, message="No tasks could be created from the provided text", data=None)

        return ResponseModel(
            success=True,
            message=f"Created {len(created_tasks)} tasks from text",
            data=created_tasks,
        )
    except Exception as e:
        logging.error(f"Error batch creating project tasks: {e}")
        return ResponseModel(success=False, message=f"Error batch creating project tasks: {str(e)}", data=None)


def update_subtask_status(subtask_id: str, user_id: str, status: str, completed_date: str = None) -> ResponseModel:
    try:
        canonical_status = normalize_workflow_status(status)
        if canonical_status is None:
            return ResponseModel(
                success=False,
                message="Invalid status value",
                data={"valid_statuses": WORKFLOW_STATUS_VALUES},
            )

        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()
        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        subtask_data = subtask_doc.to_dict() or {}
        if subtask_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        now = _current_timestamp_iso()
        update_data = {
            "status": canonical_status,
            "updated_at": now,
            "completed_date": completed_date if canonical_status == "Done" and completed_date else (now if canonical_status == "Done" else None),
        }

        subtask_ref.update(update_data)
        updated_doc = subtask_ref.get()
        updated_data = _serialize_subtask(updated_doc.to_dict() or {}, updated_doc.id)
        return ResponseModel(success=True, message="Subtask status updated successfully", data=updated_data)
    except Exception as e:
        logging.error(f"Error updating subtask status: {e}")
        return ResponseModel(success=False, message=f"Error updating subtask status: {str(e)}", data=None)


def update_subtask_fields(subtask_id: str, user_id: str, update_data: Dict) -> ResponseModel:
    try:
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()
        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        current_data = subtask_doc.to_dict() or {}
        if current_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        allowed_fields = {
            "title",
            "description",
            "estimated_hours",
            "complexity",
            "tips_markdown",
            "dependencies",
            "task_type",
            "type",
            "assignee",
            "assigneeId",
            "assigneeEmail",
            "assignee_email",
            "assigned_to",
        }
        filtered_update = {key: value for key, value in update_data.items() if key in allowed_fields}
        if "assignee_email" in filtered_update and "assigneeEmail" not in filtered_update:
            filtered_update["assigneeEmail"] = filtered_update.get("assignee_email")
        if "assigneeEmail" in filtered_update:
            filtered_update["assignee_email"] = filtered_update.get("assigneeEmail")
        if not filtered_update:
            return ResponseModel(success=False, message="No valid fields to update", data=None)

        if "task_type" in filtered_update or "type" in filtered_update:
            normalized_task_type = _normalize_task_type(
                filtered_update.get("task_type") or filtered_update.get("type")
            )
            filtered_update["task_type"] = normalized_task_type
            filtered_update["type"] = normalized_task_type

        filtered_update["updated_at"] = _current_timestamp_iso()
        subtask_ref.update(filtered_update)

        updated_doc = subtask_ref.get()
        final_data = _serialize_subtask(updated_doc.to_dict() or {}, subtask_id)
        return ResponseModel(success=True, message="Subtask updated successfully", data=final_data)
    except Exception as e:
        logging.error(f"Error updating subtask fields: {e}")
        return ResponseModel(success=False, message=f"Error updating subtask fields: {str(e)}", data=None)


async def generate_subtasks_for_user_story(user_data: UserData, story_id: str) -> ResponseModel:
    try:
        story_response = get_user_story_by_id(story_id, user_data.get_user_id())
        if not story_response.success:
            return ResponseModel(
                success=False,
                message=f"User story not found: {story_response.message}",
                data=None,
            )

        story_data = story_response.data
        additional_fields_text = ""
        if story_data.get("fields"):
            for field in story_data["fields"]:
                additional_fields_text += f"- {field['name']}: {field['value']}\n"

        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        prompt = GENERATE_SUBTASKS_PROMPT.format(
            user_story=story_data.get("user_story", ""),
            description=story_data.get("description", ""),
            epic=story_data.get("epic", ""),
            user_story_id=story_data.get("user_story_id", ""),
            additional_fields=additional_fields_text if additional_fields_text else "No additional fields",
        )

        response = await azure_services.simple_completion(prompt)
        subtasks_json = get_code_block(response)

        if subtasks_json:
            try:
                subtasks_data = json.loads(subtasks_json)
                subtasks = subtasks_data.get("subtasks", [])
                if subtasks:
                    save_result = save_subtasks_to_firestore(story_id, user_data.get_user_id(), subtasks)
                    if save_result.success:
                        return ResponseModel(
                            success=True,
                            message=f"Successfully generated and saved {len(subtasks)} subtasks",
                            data={
                                "user_story_id": story_id,
                                "user_story": story_data.get("user_story", ""),
                                "subtasks": save_result.data,
                            },
                        )
                    return ResponseModel(
                        success=True,
                        message=f"Successfully generated {len(subtasks)} subtasks, but failed to save: {save_result.message}",
                        data={
                            "user_story_id": story_id,
                            "user_story": story_data.get("user_story", ""),
                            "subtasks": subtasks,
                        },
                    )
                return ResponseModel(success=False, message="No subtasks were generated", data=None)
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse subtasks JSON: {e}")
                return ResponseModel(
                    success=False,
                    message=f"Failed to parse subtasks response: {str(e)}",
                    data={"raw_response": response},
                )

        try:
            subtasks_data = json.loads(response)
            subtasks = subtasks_data.get("subtasks", [])
            if subtasks:
                save_result = save_subtasks_to_firestore(story_id, user_data.get_user_id(), subtasks)
                if save_result.success:
                    return ResponseModel(
                        success=True,
                        message=f"Successfully generated and saved {len(subtasks)} subtasks",
                        data={
                            "user_story_id": story_id,
                            "user_story": story_data.get("user_story", ""),
                            "subtasks": save_result.data,
                        },
                    )
                return ResponseModel(
                    success=True,
                    message=f"Successfully generated {len(subtasks)} subtasks, but failed to save: {save_result.message}",
                    data={
                        "user_story_id": story_id,
                        "user_story": story_data.get("user_story", ""),
                        "subtasks": subtasks,
                    },
                )
        except json.JSONDecodeError:
            pass

        return ResponseModel(
            success=False,
            message="Could not parse subtasks from LLM response",
            data={"raw_response": response},
        )
    except Exception as e:
        logging.error(f"Error in generate_subtasks_for_user_story: {e}")
        return ResponseModel(success=False, message=f"Error generating subtasks: {str(e)}", data=None)


def create_subtask_for_user_story(story_id: str, user_id: str, subtask_data: Dict[str, Any]) -> ResponseModel:
    try:
        story_context = _build_story_context(story_id, user_id)
        project_id = str(story_context.get("project_id") or "").strip()
        if not project_id:
            return ResponseModel(success=False, message="Project not found for user story", data=None)
        existing_subtasks_query = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", story_id).get()
        current_orders = [doc.to_dict().get("order", 0) for doc in existing_subtasks_query]
        next_order = max(current_orders, default=0) + 1

        now = _current_timestamp_iso()
        task_identifier = get_next_TK_identifier()
        task_type = _normalize_task_type(subtask_data.get("task_type") or subtask_data.get("type"))
        new_subtask = {
            "user_story_id": story_id,
            "user_id": user_id,
            "project_id": project_id,
            "source": "story",
            "task_id": task_identifier,
            "title": subtask_data.get("title", ""),
            "order": next_order,
            "description": subtask_data.get("description", ""),
            "estimated_hours": subtask_data.get("estimated_hours", 0),
            "complexity": subtask_data.get("complexity", "Medium"),
            "dependencies": subtask_data.get("dependencies", []),
            "task_type": task_type,
            "type": task_type,
            "status": coerce_workflow_status(subtask_data.get("status"), default="To Do"),
            "completed_date": None,
            "created_at": now,
            "updated_at": now,
            "assignee": subtask_data.get("assignee", ""),
            "assigneeId": subtask_data.get("assigneeId"),
            "assigneeEmail": subtask_data.get("assigneeEmail"),
            "assignee_email": subtask_data.get("assignee_email") or subtask_data.get("assigneeEmail"),
            "assigned_to": subtask_data.get("assigned_to"),
        }

        doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(new_subtask)
        serialized = _serialize_subtask(
            {
                **new_subtask,
                "story_title": story_context.get("story_title", ""),
                "epic_id": story_context.get("epic_id", ""),
                "epic_name": story_context.get("epic_name", ""),
            },
            doc_ref[1].id,
        )

        return ResponseModel(success=True, message="Subtask created successfully", data=serialized)
    except Exception as e:
        logging.error(f"Error creating subtask: {e}")
        return ResponseModel(success=False, message=f"Error creating subtask: {str(e)}", data=None)


async def create_subtask_for_user_story_with_agent(
    story_id: str,
    user_data: UserData,
    subtask_data: Dict[str, Any],
) -> ResponseModel:
    try:
        story_context = _build_story_context(story_id, user_data.get_user_id())
        project_id = str(story_context.get("project_id") or "").strip()
        if not project_id:
            return ResponseModel(success=False, message="Project not found for user story", data=None)

        existing_subtasks_query = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", story_id).get()
        current_orders = [doc.to_dict().get("order", 0) for doc in existing_subtasks_query]
        next_order = max(current_orders, default=0) + 1

        now = _current_timestamp_iso()
        task_identifier = get_next_TK_identifier()
        task_type = _normalize_task_type(subtask_data.get("task_type") or subtask_data.get("type"))
        title = str(subtask_data.get("title") or "").strip()
        description = str(subtask_data.get("description") or "").strip()

        if not title:
            return ResponseModel(success=False, message="title is required", data=None)

        tech_stack = _get_project_tech_stack(project_id)
        estimated_fields = await _estimate_project_task_fields(
            user_data=user_data,
            title=title,
            description=description,
            task_type=task_type,
        )
        tips_markdown = await _generate_project_task_tips(
            user_data=user_data,
            title=title,
            description=description,
            task_type=task_type,
            tech_stack=tech_stack,
        )

        new_subtask = {
            "user_story_id": story_id,
            "user_id": user_data.get_user_id(),
            "project_id": project_id,
            "source": "story",
            "task_id": task_identifier,
            "title": title,
            "order": next_order,
            "description": description,
            "estimated_hours": estimated_fields["estimated_hours"],
            "complexity": estimated_fields["complexity"],
            "tips_markdown": tips_markdown,
            "dependencies": subtask_data.get("dependencies", []),
            "task_type": task_type,
            "type": task_type,
            "status": coerce_workflow_status(subtask_data.get("status"), default="To Do"),
            "completed_date": None,
            "created_at": now,
            "updated_at": now,
            "assignee": subtask_data.get("assignee", ""),
            "assigneeId": subtask_data.get("assigneeId"),
            "assigneeEmail": subtask_data.get("assigneeEmail"),
            "assignee_email": subtask_data.get("assignee_email") or subtask_data.get("assigneeEmail"),
            "assigned_to": subtask_data.get("assigned_to"),
        }

        doc_ref = FIRESTORE_CLIENT.collection("subtasks").add(new_subtask)
        serialized = _serialize_subtask(
            {
                **new_subtask,
                "story_title": story_context.get("story_title", ""),
                "epic_id": story_context.get("epic_id", ""),
                "epic_name": story_context.get("epic_name", ""),
            },
            doc_ref[1].id,
        )

        return ResponseModel(success=True, message="Subtask created successfully", data=serialized)
    except Exception as e:
        logging.error(f"Error creating agent-backed subtask: {e}")
        return ResponseModel(success=False, message=f"Error creating subtask: {str(e)}", data=None)


def delete_subtasks_by_user_story(subtask_id: str, user_id: str) -> ResponseModel:
    try:
        subtask_ref = FIRESTORE_CLIENT.collection("subtasks").document(subtask_id)
        subtask_doc = subtask_ref.get()
        if not subtask_doc.exists:
            return ResponseModel(success=False, message="Subtask not found", data=None)

        subtask_data = subtask_doc.to_dict() or {}
        if subtask_data.get("user_id") != user_id:
            return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        subtask_ref.delete()
        return ResponseModel(success=True, message="Subtask deleted successfully", data=None)
    except Exception as e:
        logging.error(f"Error deleting subtask: {e}")
        return ResponseModel(success=False, message=f"Error deleting subtask: {str(e)}", data=None)
