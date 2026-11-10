"""Read-only Jira MCP tools available to project agents."""

import os
import logging
from typing import Any, Dict

from src.integrations.jira_mcp.service import JiraIntegrationError, call_mcp_tool, get_connection, search_jira_rest
from src.services.setup.firebase_setup import FIRESTORE_CLIENT

logger = logging.getLogger(__name__)


def get_jira_connection_status(project_id: str, user_id: str) -> Dict[str, Any]:
    project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
    project = project_doc.to_dict() if project_doc.exists else {}
    config = (project or {}).get("jira_config") or {}
    cloud_id = config.get("cloud_id")
    connection = get_connection(user_id, cloud_id) if cloud_id else None
    return {
        "connected": bool(connection and cloud_id and config.get("project_key")),
        "cloud_id": cloud_id,
        "project_key": config.get("project_key"),
        "site_name": config.get("site_name"),
        "account_email": config.get("account_email"),
    }


def search_project_jira_issues(project_id: str, user_id: str, query: str, limit: int = 8) -> Dict[str, Any]:
    status = get_jira_connection_status(project_id, user_id)
    if not status["connected"]:
        return {"error": "Jira is not connected for this project. Connect Jira in Configuration first."}
    connection = get_connection(user_id, status["cloud_id"])
    if not connection:
        return {"error": "The connected Jira account is no longer available. Reconnect Jira."}
    tool_name = os.getenv("ATLASSIAN_MCP_JIRA_SEARCH_TOOL", "searchJiraIssuesUsingJql")
    safe_limit = max(1, min(int(limit), 20))
    clause = (query or "").strip()
    project_filter = f'project = "{status["project_key"]}"'
    jql = (
        f"{project_filter} {clause}"
        if clause.upper().startswith("ORDER BY")
        else f"{project_filter} AND ({clause or 'status is not EMPTY'}) ORDER BY updated DESC"
    )
    try:
        result = call_mcp_tool(connection, tool_name, {
            "cloudId": status["cloud_id"], "jql": jql, "maxResults": safe_limit,
        })
        return {"project_key": status["project_key"], "query": query, "source": "rovo_mcp", "result": result}
    except JiraIntegrationError as exc:
        logger.warning(
            "Jira MCP search failed | project_id=%s | cloud_id=%s | project_key=%s | error=%s",
            project_id,
            status["cloud_id"],
            status["project_key"],
            exc,
        )
        try:
            result = search_jira_rest(connection, status["cloud_id"], jql, safe_limit)
            return {
                "project_key": status["project_key"], "query": query, "source": "jira_rest_api",
                "mcp_error": str(exc), "result": result,
            }
        except JiraIntegrationError as rest_error:
            return {"error": f"Rovo MCP: {exc}. Jira REST fallback: {rest_error}"}
