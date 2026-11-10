import asyncio
import traceback
from typing import Any, Dict, List, Optional
import json
import logging
import re
from datetime import datetime, timezone

from src.services.notifications import NotificationService
from src.prompts.subtask_generation import GENERATE_SUBTASKS_PROMPT
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.intelligence.runtime import AgentName, run_agent
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.utils.authz.permissions import get_project_access
from src.utils.core.general import get_code_block
from src.utils.integrations.github import get_github_file_content, get_installation_token, list_github_repository_files
from src.utils.planning.epics import get_epic_by_id, get_epics_for_project
from src.utils.planning.user_stories import get_user_stories_by_epic, get_user_story_by_id
from src.utils.firebase.identifier import get_next_TK_identifier
from src.schemas.workflow_status import (
    WORKFLOW_STATUS_VALUES,
    coerce_workflow_status,
    normalize_workflow_status,
)
from src.utils.authz.users import get_user_profile

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

REPO_CONTEXT_MAX_FILES = 6
REPO_CONTEXT_MAX_FILE_CHARS = 1800
REPO_CONTEXT_MAX_TOTAL_CHARS = 9000
REPO_CONTEXT_MAX_LISTED_PATHS = 30
REPO_CONTEXT_PRIORITY_FILENAMES = {
    "readme.md": 100,
    "package.json": 60,
    "pyproject.toml": 55,
    "requirements.txt": 55,
    "dockerfile": 50,
    "compose.yaml": 45,
    "docker-compose.yml": 45,
    "docker-compose.yaml": 45,
    "tsconfig.json": 35,
    "vite.config.ts": 35,
    "vite.config.js": 35,
}
REPO_CONTEXT_TEXT_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".kt", ".cs", ".go", ".rb", ".php", ".rs",
    ".swift", ".m", ".mm", ".scala", ".sh", ".ps1", ".sql", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".env", ".md", ".txt", ".html", ".css", ".scss", ".sass", ".less", ".xml", ".gradle",
}
REPO_CONTEXT_SKIP_PATH_PARTS = {
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".next",
    ".nuxt",
    ".git",
    "vendor",
    "bin",
    "obj",
}
REPO_CONTEXT_STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "when", "where", "what", "show",
    "task", "tasks", "plan", "make", "does", "must", "most", "need", "create", "creating", "repo",
    "repository", "github", "agent", "development", "fix", "bug", "bugs", "refactor", "research",
    "title", "description", "your", "their", "have", "has", "had", "will", "would", "should",
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
You are a senior software engineer creating a practical development plan for a single software task.

Project tech stack: {tech_stack}
Task type: {task_type}
Title: {title}
Description: {description}
Repository context:
{repository_context}

Write Markdown only.

Required structure:
## Problem Summary
## Relevant Repository Files
## Development Plan
## Validation Steps
## Risks and Edge Cases

Rules:
- If repository context includes files, explicitly mention the most relevant repository-relative paths in `Relevant Repository Files`.
- Mention only files that appear in the repository context.
- `Development Plan` must be a short numbered list with concrete investigation and implementation steps.
- `Validation Steps` must describe how to verify the task after changes.
- If repository context is unavailable, say that clearly in `Relevant Repository Files` and fall back to a high-level plan.
- Do not invent files, functions, endpoints, or components that are not present in the repository context.
- Do not provide code fences.
- Use bullets where appropriate.
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


def _get_project_data(project_id: str) -> Dict[str, Any]:
    project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
    if not project_doc.exists:
        return {}
    return project_doc.to_dict() or {}


def _normalize_task_type(value: Any, default: str = "Implementation") -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return default
    if normalized in TASK_TYPE_VALUES:
        return normalized
    return LEGACY_TASK_TYPE_MAP.get(normalized.lower(), default)

def _maybe_send_subtask_updated_notification(
    *,
    previous_subtask: Dict[str, Any],
    updated_subtask: Dict[str, Any],
    user_id: str,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        fields_to_watch = {
            "status": "Status"
        }

        changes = {}
        for db_key, display_name in fields_to_watch.items():
            old_val = str(previous_subtask.get(db_key) or "N/A").strip()
            new_val = str(updated_subtask.get(db_key) or "N/A").strip()

            if old_val != new_val and old_val.lower() != new_val.lower():
                changes[display_name] = {"old": old_val, "new": new_val}

        if not changes:
            return NotificationService()._notification_result(False, "skipped", "no_changes", "No relevant fields were changed")

        story_id = str(updated_subtask.get("user_story_id") or previous_subtask.get("user_story_id") or "").strip()
        if not story_id:
            return NotificationService()._notification_result(False, "skipped", "story_missing", "User Story ID is missing in subtask.")

        story_doc = FIRESTORE_CLIENT.collection("user_stories").document(story_id).get()
        if not story_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "story_not_found", "Parent Story not found.")
        
        story_data = story_doc.to_dict() or {}
        parent_story_title = str(story_data.get("user_story") or story_data.get("title") or "").strip()
        epic_id = str(story_data.get("epic_id") or "").strip()

        if not epic_id:
            return NotificationService()._notification_result(False, "skipped", "epic_missing", "Epic ID is missing in parent story.")

        epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
        epic_name = ""
        project_id = ""
        if epic_doc.exists:
            epic_data = epic_doc.to_dict() or {}
            epic_name = str(epic_data.get("epic") or epic_data.get("name") or "").strip()
            project_id = str(epic_data.get("project_id") or "").strip()

        if not project_id:
            return NotificationService()._notification_result(False, "skipped", "project_missing", "Project ID missing in epic.")

        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
        if not project_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "project_not_found", "Project not found")

        project_data = project_doc.to_dict() or {}
        project_name = str(project_data.get("name") or "").strip()
        leader_email = project_data.get("projectLead", "")

        leader_id = project_data.get("user_id", "")
        leader_doc = FIRESTORE_CLIENT.collection("users").document(leader_id).get()
        if not leader_doc.exists:
            return NotificationService()._notification_result(False, "skipped", "admin_not_found", "Admin Project not found")

        # If the leader has made the change , we do not send the email
        # if user_email and user_email.lower() == leader_email.lower():
        #     return NotificationService()._notification_result(False, "skipped", "user_is_admin", "User is the admin, no email needed")

        leader_data = leader_doc.to_dict() or {}
        leader_name = leader_data.get("name", "")

        actor_name = str(user_name or user_email or "A User").strip()
        if not actor_name or actor_name == "None":
            actor_profile = get_user_profile(user_id=user_id, email=user_email)
            actor_name = str((actor_profile or {}).get("name") or user_email or "A User").strip()

        subtask_title = str(updated_subtask.get("title") or "").strip()
        display_title = f"[Subtask] {subtask_title} (De: {parent_story_title})"

        sent = NotificationService().try_send_subtask_updated(
            leader_email=leader_email, 
            leader_name=leader_name,               
            changer_name=actor_name,
            project_name=project_name,
            epic_name=epic_name,
            parent_story_title=parent_story_title,
            subtask_title=display_title,
            changes=changes
        )

        if not sent:
            return NotificationService()._notification_result(
                False,
                "failed",
                "notification_provider_failed",
                "Subtask notification failed."
            )

        return NotificationService()._notification_result(True, "sent", "sent", f"Subtask update email sent to {leader_email}.")
    except Exception as e:
        print(f"ERROR EN NOTIFICACIÓN DE SUBTAREA: {e}")
        traceback.print_exc()
        return NotificationService()._notification_result(False, "failed", "error", "Subtask notification failed.")

def _maybe_send_subtask_assignment_notification(
    *,
    previous_subtask: Dict[str, Any],
    updated_subtask: Dict[str, Any],
    user_id: str,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        # We check if the assignee has changed
        previous_email = str(previous_subtask.get("assigneeEmail") or "").strip().lower()
        updated_email = str(updated_subtask.get("assignee_email") or "").strip().lower()

        if not updated_email:
            return NotificationService()._notification_result(False, "skipped", "no_assignee", "No assignee email provided, skipping assignment notification.")

        if previous_email == updated_email:
            return NotificationService()._notification_result(False, "skipped", "assignee_unchanged", "Assignee email did not change, skipping notification.")

        # Initialize context variables with defaults in case of missing data
        project_id = str(updated_subtask.get("project_id") or "").strip()
        story_id = str(updated_subtask.get("user_story_id") or "").strip()
        epic_name = "N/A"
        parent_story_title = "Independent Task"
        project_name = "Unknown Project"

        # If the subtask is linked to a story, It is a normal subtask
        if story_id:
            story_doc = FIRESTORE_CLIENT.collection("user_stories").document(story_id).get()
            if story_doc.exists:
                story_data = story_doc.to_dict() or {}
                parent_story_title = str(story_data.get("user_story") or "").strip()
                epic_id = str(story_data.get("epic_id") or "").strip()

                if epic_id: 
                    epic_doc = FIRESTORE_CLIENT.collection("epics").document(epic_id).get()
                    if epic_doc.exists:
                        epic_data = epic_doc.to_dict() or {}
                        epic_name = str(epic_data.get("name") or "").strip()
                        if not project_id:
                            project_id = str(epic_data.get("project_id") or "").strip()

        if project_id:
            project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
            if project_doc.exists:
                project_name = str(project_doc.to_dict().get("name") or "").strip()
        else:
            return NotificationService()._notification_result(False, "skipped", "project_missing", "Project ID is missing, cannot send assignment notification.")

        # Get names
        assignee_name = str(updated_subtask.get("assignee") or updated_email).strip()
        subtask_title = str(updated_subtask.get("title") or updated_subtask.get("description") or "").strip()

        actor_name = str(user_name or "").strip()
        if not actor_name:
            actor_profile = get_user_profile(user_id=user_id, email=user_email)
            actor_name = str((actor_profile or {}).get("name") or user_email or "A User").strip()

        # Send email
        NotificationService().try_send_subtask_assignment(
            assignee_name=assignee_name,
            assignee_email=updated_email,
            project_name=project_name,
            epic_name=epic_name,
            parent_story_title=parent_story_title,
            subtask_title=subtask_title,
            assigned_by_name=actor_name,
        )

        return NotificationService()._notification_result(True, "sent", "sent", f"Assignment email sent to {updated_email}.")

    except Exception as e:
        import traceback
        print(f"ERROR EN NOTIFICACIÓN DE ASIGNACIÓN DE SUBTAREA: {e}")
        traceback.print_exc()
        return NotificationService()._notification_result(False, "failed", "error", "Subtask assignment notification failed.")

def _get_project_tech_stack(project_id: str) -> List[str]:
    try:
        project_data = _get_project_data(project_id)
        tech_stack = project_data.get("technical_stack") or project_data.get("techStack") or []
        if not isinstance(tech_stack, list):
            return []

        return [str(item).strip() for item in tech_stack if str(item).strip()]
    except Exception as exc:
        logging.warning(f"Failed to load project tech stack for task tips in project {project_id}: {exc}")
        return []


def _extract_task_keywords(*values: Any) -> List[str]:
    keywords = []
    seen = set()
    combined = " ".join(str(value or "") for value in values)
    for token in re.findall(r"[A-Za-z0-9_\-/]{3,}", combined.lower()):
        normalized = token.strip("_-/")
        if not normalized or normalized in REPO_CONTEXT_STOP_WORDS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(normalized)
    return keywords[:20]


def _is_text_repository_file(path: str) -> bool:
    normalized_path = str(path or "").strip().lower()
    if not normalized_path:
        return False

    path_parts = [part for part in normalized_path.split("/") if part]
    if any(part in REPO_CONTEXT_SKIP_PATH_PARTS for part in path_parts):
        return False

    file_name = path_parts[-1] if path_parts else normalized_path
    if file_name in REPO_CONTEXT_PRIORITY_FILENAMES:
        return True

    extension_match = re.search(r"(\.[a-z0-9]+)$", file_name)
    extension = extension_match.group(1) if extension_match else ""
    return extension in REPO_CONTEXT_TEXT_EXTENSIONS


def _score_repository_file(path: str, keywords: List[str], task_type: str) -> int:
    normalized_path = str(path or "").strip().lower()
    if not normalized_path:
        return 0

    path_parts = [part for part in normalized_path.split("/") if part]
    file_name = path_parts[-1] if path_parts else normalized_path
    score = REPO_CONTEXT_PRIORITY_FILENAMES.get(file_name, 0)

    if normalized_path.startswith("src/") or "/src/" in normalized_path:
        score += 10
    if any(segment in normalized_path for segment in ("/api/", "/routes/", "/services/", "/components/", "/pages/", "/features/", "/utils/")):
        score += 8
    if any(segment in normalized_path for segment in ("/test", "/tests/", ".spec.", ".test.")):
        score += 6
    if task_type == "Bug" and any(segment in normalized_path for segment in ("/test", "/tests/", ".spec.", ".test.", "/logs/")):
        score += 8
    if task_type == "Research" and file_name in {"readme.md", "package.json", "requirements.txt", "pyproject.toml"}:
        score += 10

    for keyword in keywords:
        keyword_parts = [part for part in re.split(r"[_\-/]", keyword) if part]
        if normalized_path.find(keyword) >= 0:
            score += 18
        for keyword_part in keyword_parts:
            if len(keyword_part) < 3:
                continue
            if keyword_part == file_name.replace(".", ""):
                score += 10
            elif f"/{keyword_part}" in normalized_path or f"{keyword_part}/" in normalized_path:
                score += 7
            elif keyword_part in normalized_path:
                score += 4

    return score


def _extract_content_excerpt(content: str, keywords: List[str], max_chars: int = REPO_CONTEXT_MAX_FILE_CHARS) -> str:
    normalized_content = str(content or "").replace("\r\n", "\n").strip()
    if not normalized_content:
        return ""

    lowered = normalized_content.lower()
    match_index = -1
    for keyword in keywords:
        candidate_index = lowered.find(keyword.lower())
        if candidate_index >= 0:
            match_index = candidate_index
            break

    if match_index < 0:
        return normalized_content[:max_chars].strip()

    start = max(0, match_index - max_chars // 3)
    end = min(len(normalized_content), start + max_chars)
    excerpt = normalized_content[start:end].strip()
    if start > 0:
        excerpt = "...\n" + excerpt
    if end < len(normalized_content):
        excerpt = excerpt + "\n..."
    return excerpt


def _build_repository_context_markdown(repository_context: Dict[str, Any]) -> str:
    if not repository_context.get("available"):
        return str(repository_context.get("message") or "No repository context available.")

    lines = [
        f"Repository: {repository_context.get('repository') or 'Unknown repository'}",
        f"Branch: {repository_context.get('branch') or 'Unknown branch'}",
        "Top repository paths:",
    ]

    for path in repository_context.get("top_paths") or []:
        lines.append(f"- {path}")

    selected_files = repository_context.get("selected_files") or []
    if selected_files:
        lines.append("Loaded file excerpts:")
        for item in selected_files:
            lines.append(f"File: {item.get('path')}")
            lines.append(item.get("excerpt") or "(No readable excerpt available)")

    return "\n".join(lines).strip()


def _build_task_tips_fallback(
    title: str,
    description: str,
    task_type: str,
    tech_stack_text: str,
    repository_context: Dict[str, Any],
) -> str:
    fallback_title = title.strip() or "Untitled task"
    fallback_description = description.strip() or "No additional description provided."
    selected_files = repository_context.get("selected_files") or []
    top_paths = repository_context.get("top_paths") or []

    if selected_files:
        relevant_files_lines = [
            f"- `{item.get('path')}`" for item in selected_files if str(item.get("path") or "").strip()
        ]
        development_plan_lines = [
            "- Review the listed files first and confirm which one owns the behavior described in the task.",
            "- Trace the smallest change needed in those files and any directly related callers or consumers.",
            f"- Implement the {task_type.lower()} change with the project tech stack in mind: {tech_stack_text}.",
        ]
        validation_lines = [
            "- Verify the affected flow end-to-end in the product surface tied to those files.",
            "- Confirm adjacent states such as empty, loading, invalid input, and failure handling.",
            "- Run the most relevant automated checks available for the touched area.",
        ]
    elif top_paths:
        relevant_files_lines = [f"- `{path}`" for path in top_paths[: min(6, len(top_paths))]]
        development_plan_lines = [
            "- Start with the listed repository paths and confirm which one owns the behavior described in the task.",
            "- Load the matching implementation files and any adjacent contracts or UI/API consumers before editing.",
            f"- Implement the {task_type.lower()} change with the project tech stack in mind: {tech_stack_text}.",
        ]
        validation_lines = [
            "- Verify the affected flow against the files identified above.",
            "- Check any connected entry points, shared contracts, or rendering states near those files.",
            "- Run the most relevant automated checks available for the touched area.",
        ]
    else:
        relevant_files_lines = [
            f"- {repository_context.get('message') or 'Repository files were not available for this task plan.'}",
        ]
        development_plan_lines = [
            "- Clarify the expected outcome, who uses it, and what success looks like.",
            "- Identify the product area most affected by the task before changing code.",
            f"- Use the task type ({task_type}) and tech stack ({tech_stack_text}) to narrow the first implementation target.",
        ]
        validation_lines = [
            "- Validate the primary happy path for the task.",
            "- Check failure handling, invalid input, and regression risk in nearby flows.",
        ]

    return (
        "## Problem Summary\n"
        f"- {fallback_title}\n"
        f"- {fallback_description}\n\n"
        "## Relevant Repository Files\n"
        + "\n".join(relevant_files_lines)
        + "\n\n## Development Plan\n"
        + "\n".join(f"{index}. {line[2:]}" for index, line in enumerate(development_plan_lines, start=1))
        + "\n\n## Validation Steps\n"
        + "\n".join(validation_lines)
        + "\n\n## Risks and Edge Cases\n"
        "- Hidden dependencies may exist outside the first matching files.\n"
        "- Requirements may affect shared contracts, permissions, or data shape.\n"
        "- Regression risk is higher when the task touches common entry points or shared UI.\n"
    )


def _load_repository_context_for_task(
    project_id: str,
    title: str,
    description: str,
    task_type: str,
) -> Dict[str, Any]:
    try:
        project_data = _get_project_data(project_id)
        github_config = project_data.get("github_config") or {}
        repository_url = str(github_config.get("repository_url") or "").strip()
        api_token = github_config.get("api_token")
        branch = github_config.get("branch")
        installation_id = github_config.get("installation_id")

        active_token = None
        if installation_id:
            try:
                active_token = get_installation_token(str(installation_id))
            except Exception as e:
                logging.error(f"Failed to generate GitHub App token: {e}")

        active_token = active_token or str(api_token or "").strip()

        if not repository_url or not str(active_token or "").strip():
            return {
                "available": False,
                "message": "GitHub repository context is not configured for this project.",
            }

        listing = list_github_repository_files(repository_url, active_token, branch)
        all_files = listing.get("files") or []
        if not isinstance(all_files, list) or not all_files:
            return {
                "available": False,
                "message": "The configured GitHub repository did not return any files.",
            }

        keywords = _extract_task_keywords(title, description, task_type)
        ranked_files = []
        for item in all_files:
            path = str((item or {}).get("path") or "").strip()
            if not _is_text_repository_file(path):
                continue
            score = _score_repository_file(path, keywords, task_type)
            ranked_files.append(
                {
                    "path": path,
                    "score": score,
                }
            )

        ranked_files.sort(key=lambda item: (-int(item.get("score") or 0), item.get("path") or ""))
        top_paths = [
            item.get("path")
            for item in ranked_files[:REPO_CONTEXT_MAX_LISTED_PATHS]
            if str(item.get("path") or "").strip()
        ]

        selected_files = []
        total_chars = 0
        for item in ranked_files:
            if len(selected_files) >= REPO_CONTEXT_MAX_FILES or total_chars >= REPO_CONTEXT_MAX_TOTAL_CHARS:
                break

            path = str(item.get("path") or "").strip()
            if not path:
                continue

            try:
                file_payload = get_github_file_content(repository_url, active_token, path, branch)
                excerpt = _extract_content_excerpt(file_payload.get("content") or "", keywords)
                if not excerpt:
                    continue
                total_chars += len(excerpt)
                selected_files.append(
                    {
                        "path": path,
                        "excerpt": excerpt,
                    }
                )
            except Exception as exc:
                logging.warning(f"Skipping GitHub file '{path}' while building task context: {exc}")

        if not selected_files and top_paths:
            return {
                "available": True,
                "repository": listing.get("repository"),
                "branch": listing.get("branch"),
                "top_paths": top_paths,
                "selected_files": [],
            }

        return {
            "available": True,
            "repository": listing.get("repository"),
            "branch": listing.get("branch"),
            "top_paths": top_paths,
            "selected_files": selected_files,
        }
    except Exception as exc:
        logging.warning(f"Failed to load GitHub repository context for task planning in project {project_id}: {exc}")
        return {
            "available": False,
            "message": "GitHub repository context could not be loaded for this task.",
        }


def _serialize_subtask(subtask_data: Dict[str, Any], subtask_id: Optional[str] = None) -> Dict[str, Any]:
    serialized = dict(subtask_data or {})
    if subtask_id:
        serialized["id"] = subtask_id

    user_story_id = str(serialized.get("user_story_id") or "").strip()
    project_id = str(serialized.get("project_id") or "").strip()
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
    serialized["estimated_hours"] = (
        serialized.get("estimated_hours")
        if serialized.get("estimated_hours") is not None
        else serialized.get("estimatedHours")
    )
    if serialized["estimated_hours"] is None:
        serialized["estimated_hours"] = (
            serialized.get("effortHours")
            if serialized.get("effortHours") is not None
            else serialized.get("effort_hours", 0)
        )
    serialized["complexity"] = serialized.get("complexity") or "Medium"
    tips_markdown = str(serialized.get("tips_markdown") or serialized.get("tipsMarkdown") or "").strip()
    if not tips_markdown:
        tech_stack_items = _get_project_tech_stack(project_id) if project_id else []
        tech_stack_text = ", ".join(tech_stack_items) if tech_stack_items else "the existing project stack"
        tips_markdown = _build_task_tips_fallback(
            str(serialized.get("title") or "").strip(),
            str(serialized.get("description") or "").strip(),
            task_type,
            tech_stack_text,
            {"available": False, "message": "Saved task tips were not available for this item."},
        )
    serialized["tips_markdown"] = tips_markdown
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
        raw = await run_agent(
            AgentName.TASK_ESTIMATION,
            TASK_ESTIMATION_PROMPT.format(
                task_type=task_type,
                title=title.strip() or "Untitled task",
                description=description.strip() or "No additional description provided.",
            ),
            user_data,
            model_tier="mini",
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
    project_id: str,
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
    repository_context = _load_repository_context_for_task(
        project_id=project_id,
        title=fallback_title,
        description=fallback_description,
        task_type=task_type,
    )
    repository_context_text = _build_repository_context_markdown(repository_context)
    fallback = _build_task_tips_fallback(
        title=fallback_title,
        description=fallback_description,
        task_type=task_type,
        tech_stack_text=tech_stack_text,
        repository_context=repository_context,
    )

    try:
        raw = await run_agent(
            AgentName.IMPLEMENTATION_GUIDANCE,
            TASK_TIPS_PROMPT.format(
                tech_stack=tech_stack_text,
                task_type=task_type,
                title=fallback_title,
                description=fallback_description,
                repository_context=repository_context_text,
            ),
            user_data,
            model_tier="mini",
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
        raw = await run_agent(
            AgentName.TASK_PLANNING,
            PROJECT_TASK_BATCH_PROMPT.format(source_text=cleaned_text),
            user_data,
            model_tier="mini",
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
        project_id=project_id,
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
        "assignee": str(story_data.get("assignee") or "").strip(),
        "assigneeId": story_data.get("assigneeId") or story_data.get("assigned_to"),
        "assigneeEmail": story_data.get("assigneeEmail") or story_data.get("assignee_email"),
        "assignee_email": story_data.get("assignee_email") or story_data.get("assigneeEmail"),
        "assigned_to": story_data.get("assigned_to") or story_data.get("assigneeId"),
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


async def save_subtasks_to_firestore(
    user_story_id: str,
    user_data: UserData,
    subtasks: List[Dict[str, Any]],
) -> ResponseModel:
    try:
        saved_subtasks: List[Dict[str, Any]] = []
        now = _current_timestamp_iso()
        user_id = user_data.get_user_id()
        story_context = _build_story_context(user_story_id, user_id)
        project_id = str(story_context.get("project_id") or "").strip()
        if not project_id:
            return ResponseModel(
                success=False,
                message="Project not found for user story",
                data=None,
            )
        tech_stack = _get_project_tech_stack(project_id)

        existing_subtasks = FIRESTORE_CLIENT.collection("subtasks").where("user_story_id", "==", user_story_id).get()
        for doc in existing_subtasks:
            doc.reference.delete()

        task_payloads: List[Dict[str, Any]] = []
        for subtask_data in subtasks:
            task_type = _normalize_task_type(
                subtask_data.get("task_type") or subtask_data.get("type"),
                default="Implementation",
            )
            task_payloads.append(
                {
                    "task_identifier": get_next_TK_identifier(),
                    "order": subtask_data.get("order", 0),
                    "title": str(subtask_data.get("title") or "").strip(),
                    "description": str(subtask_data.get("description") or "").strip(),
                    "estimated_hours": _normalize_positive_hours(
                        subtask_data.get("estimated_hours"),
                        default=0.0,
                    ),
                    "complexity": subtask_data.get("complexity", "Medium"),
                    "dependencies": subtask_data.get("dependencies", []),
                    "task_type": task_type,
                    "type": task_type,
                }
            )

        tips_values = await asyncio.gather(
            *[
                _generate_project_task_tips(
                    project_id=project_id,
                    user_data=user_data,
                    title=task_payload["title"],
                    description=task_payload["description"],
                    task_type=task_payload["task_type"],
                    tech_stack=tech_stack,
                )
                for task_payload in task_payloads
            ]
        ) if task_payloads else []

        for index, task_payload in enumerate(task_payloads):
            subtask_document = {
                "user_story_id": user_story_id,
                "user_id": user_id,
                "project_id": project_id,
                "source": "story",
                "task_id": task_payload["task_identifier"],
                "order": task_payload["order"],
                "title": task_payload["title"],
                "description": task_payload["description"],
                "estimated_hours": task_payload["estimated_hours"],
                "complexity": task_payload["complexity"],
                "tips_markdown": tips_values[index] if index < len(tips_values) else "",
                "dependencies": task_payload["dependencies"],
                "task_type": task_payload["task_type"],
                "type": task_payload["type"],
                "status": "To Do",
                "completed_date": None,
                "created_at": now,
                "updated_at": now,
                "assignee": story_context.get("assignee", ""),
                "assigneeId": story_context.get("assigneeId"),
                "assigneeEmail": story_context.get("assigneeEmail"),
                "assignee_email": story_context.get("assignee_email"),
                "assigned_to": story_context.get("assigned_to"),
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

        total_estimated_hours = round(
            sum(float(task.get("estimated_hours") or 0) for task in task_payloads),
            1,
        )
        try:
            FIRESTORE_CLIENT.collection("user_stories").document(user_story_id).update(
                {
                    "effortHours": total_estimated_hours,
                    "updated_at": now,
                }
            )
        except Exception as exc:
            logging.warning(
                "Saved subtasks for user story %s, but could not update its total effort hours: %s",
                user_story_id,
                exc,
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


def _sync_parent_story_status_from_subtasks(user_story_id: str) -> Optional[Dict[str, Any]]:
    story_id = str(user_story_id or "").strip()
    if not story_id:
        return None

    subtask_docs = FIRESTORE_CLIENT.collection("subtasks").where(
        "user_story_id", "==", story_id
    ).get()
    if not subtask_docs:
        return None

    subtask_statuses = [
        coerce_workflow_status((doc.to_dict() or {}).get("status"), default="To Do")
        for doc in subtask_docs
    ]
    all_subtasks_done = all(status == "Done" for status in subtask_statuses)
    any_subtask_started = any(status != "To Do" for status in subtask_statuses)
    story_ref = FIRESTORE_CLIENT.collection("user_stories").document(story_id)
    story_doc = story_ref.get()
    if not story_doc.exists:
        return None

    story_data = story_doc.to_dict() or {}
    current_status = coerce_workflow_status(
        story_data.get("status"),
        default="To Do",
    )
    current_start_date = story_data.get("startDate")

    if all_subtasks_done:
        next_status = "Done"
    elif any_subtask_started:
        next_status = "In Progress"
    else:
        next_status = "To Do"

    update_data: Dict[str, Any] = {}
    if next_status != current_status:
        update_data["status"] = next_status
    if next_status == "In Progress" and not current_start_date:
        update_data["startDate"] = _current_timestamp_iso()
    if update_data:
        update_data["updated_at"] = _current_timestamp_iso()
        story_ref.update(update_data)

    return {
        "status": next_status,
        "startDate": update_data.get("startDate") or current_start_date,
    }


def update_subtask_status(subtask_id: str, user_id: str, status: str, completed_date: str = None, user_name: Optional[str] = None, user_email: Optional[str] = None) -> ResponseModel:
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
        current_data = subtask_doc.to_dict() or {}
        if current_data.get("user_id") != user_id:
            project_id = str(current_data.get("project_id") or "").strip()
            access = get_project_access(project_id, user_id, user_email) if project_id else None
            if not access or not access.success or not (access.data or {}).get("is_lead"):
                return ResponseModel(success=False, message="Unauthorized: You don't own this subtask", data=None)

        now = _current_timestamp_iso()
        update_data = {
            "status": canonical_status,
            "updated_at": now,
            "completed_date": completed_date if canonical_status == "Done" and completed_date else (now if canonical_status == "Done" else None),
        }
        # A task's actual start must reflect when work began, rather than a
        # previously planned future date. Sprint plannedStartDate stays on the
        # sprint assignment and is intentionally not changed here.
        if canonical_status == "In Progress":
            update_data["startDate"] = now

        subtask_ref.update(update_data)
        updated_doc = subtask_ref.get()
        updated_data = _serialize_subtask(updated_doc.to_dict() or {}, updated_doc.id)
        parent_story_sync = _sync_parent_story_status_from_subtasks(
            str(current_data.get("user_story_id") or "")
        )
        if parent_story_sync:
            updated_data["parent_story_status"] = parent_story_sync.get("status")
            updated_data["parent_story_startDate"] = parent_story_sync.get("startDate")

        try:
            updated_data_for_notif = updated_data.copy()
            updated_data_for_notif["id"] = subtask_id

            _maybe_send_subtask_updated_notification(
                previous_subtask=current_data,
                updated_subtask=updated_data_for_notif,
                user_id=user_id,
                user_name=user_name,
                user_email=user_email,
            )
        except Exception as e:
            logging.error(f"Error disparando notificación de actualización de status de subtarea: {e}")

        return ResponseModel(success=True, message="Subtask status updated successfully", data=updated_data)
    except Exception as e:
        logging.error(f"Error updating subtask status: {e}")
        return ResponseModel(success=False, message=f"Error updating subtask status: {str(e)}", data=None)


def update_subtask_fields(subtask_id: str, user_id: str, update_data: Dict, user_name: Optional[str] = None, user_email: Optional[str] = None) -> ResponseModel:
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
        # final_data = _serialize_subtask(updated_doc.to_dict() or {}, subtask_id)
        updated_data = updated_doc.to_dict() or {}
        final_data = _serialize_subtask(updated_data, subtask_id)

        try:
            # Le pasamos el ID por si la notificación lo necesita
            updated_data_for_notif = updated_data.copy()
            updated_data_for_notif["id"] = subtask_id

            _maybe_send_subtask_assignment_notification(
                previous_subtask=current_data,
                updated_subtask=updated_data_for_notif,
                user_id=user_id,
                user_name=user_name,
                user_email=user_email,
            )
        except Exception as e:
            logging.error(f"Error disparando notificación de asignación de subtarea: {e}")

        return ResponseModel(success=True, message="Subtask updated successfully", data=final_data)
    except Exception as e:
        logging.error(f"Error updating subtask fields: {e}")
        return ResponseModel(success=False, message=f"Error updating subtask fields: {str(e)}", data=None)


async def generate_subtasks_for_user_story(
    user_data: UserData,
    story_id: str,
    allow_member: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    try:
        story_response = get_user_story_by_id(
            story_id,
            user_data.get_user_id(),
            allow_member=allow_member,
            user_email=user_email,
        )
        if not story_response.success:
            return ResponseModel(
                success=False,
                message=f"User story not found: {story_response.message}",
                data=None,
            )

        story_data = story_response.data
        story_context = _build_story_context(
            story_id,
            user_data.get_user_id(),
            allow_member=allow_member,
            user_email=user_email,
        )
        project_id = str(story_context.get("project_id") or "").strip()
        technical_stack = _get_project_tech_stack(project_id)
        repository_context = _load_repository_context_for_task(
            project_id=project_id,
            title=str(story_data.get("user_story") or ""),
            description=str(story_data.get("description") or ""),
            task_type="Implementation",
        )
        repository_context_text = _build_repository_context_markdown(repository_context)
        additional_fields_text = ""
        if story_data.get("fields"):
            for field in story_data["fields"]:
                additional_fields_text += f"- {field['name']}: {field['value']}\n"

        prompt = GENERATE_SUBTASKS_PROMPT.format(
            user_story=story_data.get("user_story", ""),
            description=story_data.get("description", ""),
            epic=story_data.get("epic", ""),
            user_story_id=story_data.get("user_story_id", ""),
            technical_stack=", ".join(technical_stack) if technical_stack else "Not specified",
            additional_fields=additional_fields_text if additional_fields_text else "No additional fields",
            repository_context=repository_context_text,
        )

        response = await run_agent(AgentName.TASK_PLANNING, prompt, user_data, model_tier="gpt")
        subtasks_json = get_code_block(response)

        if subtasks_json:
            try:
                subtasks_data = json.loads(subtasks_json)
                subtasks = subtasks_data.get("subtasks", [])
                if subtasks:
                    save_result = await save_subtasks_to_firestore(story_id, user_data, subtasks)
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
                save_result = await save_subtasks_to_firestore(story_id, user_data, subtasks)
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
            "assignee": story_context.get("assignee", ""),
            "assigneeId": story_context.get("assigneeId"),
            "assigneeEmail": story_context.get("assigneeEmail"),
            "assignee_email": story_context.get("assignee_email"),
            "assigned_to": story_context.get("assigned_to"),
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
            project_id=project_id,
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
            "assignee": story_context.get("assignee", ""),
            "assigneeId": story_context.get("assigneeId"),
            "assigneeEmail": story_context.get("assigneeEmail"),
            "assignee_email": story_context.get("assignee_email"),
            "assigned_to": story_context.get("assigned_to"),
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
