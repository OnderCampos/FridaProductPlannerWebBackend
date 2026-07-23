import json
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from src.schemas.response import ResponseModel
from src.schemas.task_schemas import (
    BatchCreateTasksRequest,
    CreateTaskRequest,
    UpdateTaskFieldsRequest,
    UpdateTaskStatusRequest,
)
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user
from src.utils.authz.permissions import get_project_access
from src.utils.planning.assignees import (
    build_frida_assignee_update,
    build_member_lookup,
    is_frida_assignee_id,
    is_frida_assignee_name,
)
from src.utils.planning.members import get_member_by_id
from src.utils.planning.subtask_generation import (
    batch_create_project_tasks_from_text,
    create_project_task,
    delete_subtasks_by_user_story,
    get_subtask_by_id,
    list_tasks_for_project,
    update_subtask_fields,
    update_subtask_status,
)
from src.integrations.jira_mcp.tools import search_project_jira_issues
from src.intelligence.runtime import AgentName, run_agent
from src.services.setup.firebase_setup import FIRESTORE_CLIENT


router = APIRouter()

JIRA_SEARCH_MAX_ATTEMPTS = 4
JIRA_SEARCH_STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "been", "being", "but", "can", "could",
    "error", "for", "from", "have", "into", "issue", "new", "not", "only", "our", "that",
    "the", "their", "this", "to", "was", "with", "when", "where", "will", "your",
}


def _jira_issue_summaries(search_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize Jira REST and Rovo-MCP wrapped issue responses for an LLM prompt."""
    raw_result = (search_response or {}).get("result")
    payloads: List[Any] = [raw_result]
    issues: List[Dict[str, Any]] = []
    seen_keys = set()

    while payloads:
        payload = payloads.pop()
        if isinstance(payload, str):
            try:
                payloads.append(json.loads(payload))
            except (TypeError, ValueError):
                continue
            continue
        if isinstance(payload, list):
            payloads.extend(payload)
            continue
        if not isinstance(payload, dict):
            continue

        if isinstance(payload.get("issues"), list):
            for issue in payload["issues"]:
                if not isinstance(issue, dict):
                    continue
                fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else issue
                key = str(issue.get("key") or issue.get("id") or "").strip()
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                status = fields.get("status")
                issues.append({
                    "key": key,
                    "summary": fields.get("summary") or "",
                    "description": fields.get("description") or "",
                    "status": status.get("name") if isinstance(status, dict) else status,
                    "issue_type": (fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else fields.get("issuetype"),
                    "updated": fields.get("updated") or "",
                })

        # Rovo returns the Jira JSON as a string in a text content block.
        content = payload.get("content")
        if isinstance(content, list):
            payloads.extend(item.get("text") for item in content if isinstance(item, dict) and item.get("text"))

    return issues


def _task_jira_search_queries(title: str, description: str) -> List[Dict[str, str]]:
    clean_title = re.sub(r'\s+', ' ', (title or '').replace('"', ' ')).strip()[:120]
    query_specs: List[Dict[str, str]] = []
    if clean_title:
        query_specs.append({"purpose": "exact task title", "jql": f'text ~ "\\"{clean_title}\\""'})

    terms: List[str] = []
    max_keywords = JIRA_SEARCH_MAX_ATTEMPTS - (2 if clean_title else 1)
    for token in re.findall(r"[A-Za-z0-9_-]{3,}", f"{title or ''} {description or ''}".lower()):
        if token in JIRA_SEARCH_STOP_WORDS or token in terms:
            continue
        terms.append(token)
        if len(terms) == max_keywords:
            break

    for term in terms:
        query_specs.append({"purpose": f"keyword: {term}", "jql": f'text ~ "\\"{term}\\""'})

    # The final fallback is useful when the wording differs completely, but it
    # is deliberately marked as recent context rather than a direct match.
    query_specs.append({"purpose": "recent project issues fallback", "jql": "ORDER BY updated DESC"})
    return query_specs[:JIRA_SEARCH_MAX_ATTEMPTS]


def _load_task_jira_context(project_id: str, user_id: str, title: str, description: str) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    matched_issues: List[Dict[str, Any]] = []
    seen_keys = set()
    queries = _task_jira_search_queries(title, description)

    for search in queries:
        # Recent issues are only a fallback after all related searches found no match.
        if search["purpose"] == "recent project issues fallback" and matched_issues:
            break
        response = search_project_jira_issues(project_id, user_id, search["jql"], limit=5)
        issues = _jira_issue_summaries(response)
        attempts.append({
            "purpose": search["purpose"],
            "query": search["jql"],
            "source": response.get("source"),
            "issue_count": len(issues),
            "error": response.get("error"),
        })
        for issue in issues:
            if issue["key"] not in seen_keys:
                seen_keys.add(issue["key"])
                matched_issues.append(issue)

    return {
        "attempts": attempts,
        "matching_issues": matched_issues,
        "used_recent_fallback": bool(attempts and attempts[-1]["purpose"] == "recent project issues fallback"),
    }


def _status_from_response(response: ResponseModel, success_code: int = 200) -> int:
    if response.success:
        return success_code
    message = (response.message or "").lower()
    if "not found" in message:
        return 404
    if "unauthorized" in message:
        return 403
    return 400


def _require_project_lead(project_id: str, user_data: UserData) -> None:
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        status_code = 404 if "not found" in access.message.lower() else 403
        raise HTTPException(status_code=status_code, detail=access.message)
    if not access.data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Forbidden: Team members cannot perform this action")


def _build_task_assignee_update(project_id: str, req: UpdateTaskFieldsRequest) -> dict:
    if (
        is_frida_assignee_id(req.assigneeId)
        or is_frida_assignee_name(req.assignee)
        or is_frida_assignee_name(req.assignee_email)
    ):
        return build_frida_assignee_update()

    if req.assigneeId:
        member = get_member_by_id(project_id, req.assigneeId) if project_id else None
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")
        update_data = {
            "assigneeId": req.assigneeId,
            "assignee": member.get("name") or "",
            "assigned_to": req.assigneeId,
            "assigneeEmail": member.get("email"),
            "assignee_email": member.get("email"),
        }
        return update_data

    assignee_email = str(req.assignee_email or "").strip()
    assignee_value = str(req.assignee or "").strip()
    resolved_assignee = assignee_email or assignee_value
    if not resolved_assignee or resolved_assignee.lower() == "unassigned":
        return {
            "assignee": "",
            "assigneeId": None,
            "assigned_to": None,
            "assigneeEmail": None,
            "assignee_email": None,
        }

    update_data = {
        "assignee": assignee_value or assignee_email,
    }
    if assignee_email:
        update_data["assigneeEmail"] = assignee_email
        update_data["assignee_email"] = assignee_email
        member_lookup = build_member_lookup(project_id)
        member = member_lookup.get("by_email", {}).get(assignee_email.lower())
        if member:
            update_data["assignee"] = member.get("name") or assignee_email
            update_data["assigneeId"] = member.get("id")
            update_data["assigned_to"] = member.get("id")
    return update_data


@router.get(
    "/{project_id}/tasks",
    response_description="List all tasks for a project",
)
async def list_tasks_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    response = list_tasks_for_project(
        project_id=project_id,
        user_id=user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )


@router.post(
    "/{project_id}/tasks",
    response_description="Create an independent project task",
)
async def create_task_route(
    req: CreateTaskRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    response = await create_project_task(
        project_id=project_id,
        user_data=user_data,
        task_data=req.model_dump(),
    )
    return JSONResponse(
        status_code=_status_from_response(response, success_code=201),
        content=response.dict(),
    )


@router.get("/{project_id}/tasks/{task_id}/related-context")
async def task_related_context_route(
    project_id: str,
    task_id: str,
    user_data: UserData = Depends(get_current_user),
):
    access = get_project_access(project_id, user_data.get_user_id(), user_data.get_email())
    if not access.success:
        raise HTTPException(status_code=403, detail=access.message)
    task_response = get_subtask_by_id(task_id, user_data.get_user_id(), allow_member=True, user_email=user_data.get_email())
    if not task_response.success or str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")
    task = task_response.data or {}
    project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
    project = project_doc.to_dict() if project_doc.exists else {}
    github = (project or {}).get("github_config") or {}
    task_title = str(task.get("title") or "").strip()
    jira_context = _load_task_jira_context(
        project_id,
        user_data.get_user_id(),
        task_title,
        str(task.get("description") or ""),
    )
    github_context = {
        "connected": bool(github.get("repository_url") and (github.get("api_token") or github.get("installation_id"))),
        "repository_url": github.get("repository_url"), "branch": github.get("branch"),
    }
    prompt = (
        "Write a concise Markdown implementation recommendation for this task. Start with 'Based on the available Jira items'. "
        "Use only matching_issues as direct Jira evidence. If used_recent_fallback is true, make clear those issues are only potentially related. "
        "State clearly when Jira/GitHub context is unavailable. Do not claim code was implemented. "
        f"Task: {task.get('title')}\nDescription: {task.get('description')}\n"
        f"Jira context: {jira_context}\nGitHub context: {github_context}"
    )
    try:
        recommendation = await run_agent(AgentName.TASK_PLANNING, prompt, user_data, model_tier="mini")
        recommendation = str(recommendation).strip()
    except Exception:
        recommendation = "Based on the available Jira items and GitHub connection status, review the related context before implementing this task."
    # Jira MCP/REST output can contain complete issue payloads. It is deliberately
    # kept server-side and used only as task-planning agent context.
    return ResponseModel(success=True, message="Task related context generated", data={
        "recommendation": recommendation,
    })


@router.post(
    "/{project_id}/tasks/batch",
    response_description="Create multiple independent project tasks from freeform text",
)
async def batch_create_tasks_route(
    req: BatchCreateTasksRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    response = await batch_create_project_tasks_from_text(
        project_id=project_id,
        user_data=user_data,
        source_text=req.source_text,
    )
    return JSONResponse(
        status_code=_status_from_response(response, success_code=201),
        content=response.dict(),
    )


@router.patch(
    "/{project_id}/tasks/{task_id}/status",
    response_description="Update task status",
)
async def update_task_status_route(
    req: UpdateTaskStatusRequest,
    project_id: str = Path(..., description="The project ID"),
    task_id: str = Path(..., description="The task ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    task_response = get_subtask_by_id(
        task_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not task_response.success:
        return JSONResponse(
            status_code=_status_from_response(task_response),
            content=task_response.dict(),
        )
    if str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")

    response = update_subtask_status(
        subtask_id=task_id,
        user_id=user_data.get_user_id(),
        status=req.status,
        completed_date=req.completed_date,
        user_name=user_data.get_user_name(),
        user_email=user_data.get_email()
    )
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )


@router.patch(
    "/{project_id}/tasks/{task_id}",
    response_description="Update task fields",
)
async def update_task_fields_route(
    req: UpdateTaskFieldsRequest,
    project_id: str = Path(..., description="The project ID"),
    task_id: str = Path(..., description="The task ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    task_response = get_subtask_by_id(
        task_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not task_response.success:
        return JSONResponse(
            status_code=_status_from_response(task_response),
            content=task_response.dict(),
        )
    if str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")

    payload = req.model_dump(exclude_none=True)
    if req.assignee is not None or req.assigneeId is not None or req.assignee_email is not None:
        assignee_update = _build_task_assignee_update(project_id, req)
        payload.update(assignee_update)

    response = update_subtask_fields(
        subtask_id=task_id,
        user_id=user_data.get_user_id(),
        update_data=payload,
        user_name=user_data.get_user_name(),
        user_email=user_data.get_email(),
    )
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )


@router.delete(
    "/{project_id}/tasks/{task_id}",
    response_description="Delete a task",
)
async def delete_task_route(
    project_id: str = Path(..., description="The project ID"),
    task_id: str = Path(..., description="The task ID"),
    user_data: UserData = Depends(get_current_user),
):
    _require_project_lead(project_id, user_data)
    task_response = get_subtask_by_id(
        task_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    if not task_response.success:
        return JSONResponse(
            status_code=_status_from_response(task_response),
            content=task_response.dict(),
        )
    if str((task_response.data or {}).get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="Task not found for this project")

    response = delete_subtasks_by_user_story(task_id, user_data.get_user_id())
    return JSONResponse(
        status_code=_status_from_response(response),
        content=response.dict(),
    )
