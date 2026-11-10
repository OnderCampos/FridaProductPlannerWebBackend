from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from src.integrations.jira_mcp.service import (
    JiraIntegrationError, begin_oauth, begin_rovo_oauth, disconnect, finish_oauth, finish_rovo_oauth, get_connection, get_oauth_flow, list_jira_projects, list_sites,
)
from src.schemas.jira_mcp import JiraProjectConfigurationRequest
from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.utils.authz.auth import get_current_user
from src.utils.authz.permissions import get_project_access
from src.integrations.jira_mcp.tools import search_project_jira_issues

router = APIRouter()
callback_router = APIRouter()


def _access(project_id: str, user: UserData, require_lead: bool = False) -> dict:
    response = get_project_access(project_id, user.get_user_id(), user.get_email())
    if not response.success:
        raise HTTPException(status_code=403, detail=response.message)
    data = response.data or {}
    if require_lead and not data.get("is_lead"):
        raise HTTPException(status_code=403, detail="Only project leads can configure Jira.")
    return data


@router.get("/{project_id}/jira/connection")
def jira_connection(project_id: str, user: UserData = Depends(get_current_user)):
    _access(project_id, user)
    project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
    config = (project_doc.to_dict() or {}).get("jira_config") if project_doc.exists else {}
    config = config or {}
    cloud_id = config.get("cloud_id")
    connection = get_connection(user.get_user_id(), cloud_id) if cloud_id else None
    connected = bool(connection)
    return ResponseModel(success=True, message="Jira connection loaded", data={
        "connected": connected, "rovo_connected": bool(connection and connection.get("rovo_access_token")), "cloud_id": cloud_id, "project_key": config.get("project_key"),
        "site_name": config.get("site_name"), "account_email": config.get("account_email"),
        "sites": list_sites(user.get_user_id()),
    })


@router.post("/{project_id}/jira/connection/start")
def start_jira_connection(project_id: str, user: UserData = Depends(get_current_user)):
    _access(project_id, user, require_lead=True)
    try:
        return ResponseModel(success=True, message="Jira authorization started", data={
            "authorization_url": begin_oauth(user.get_user_id(), user.get_email(), project_id)
        })
    except JiraIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{project_id}/jira/rovo/connection/start")
def start_rovo_connection(project_id: str, cloud_id: str = Query(...), user: UserData = Depends(get_current_user)):
    _access(project_id, user, require_lead=True)
    if not get_connection(user.get_user_id(), cloud_id):
        raise HTTPException(status_code=400, detail="Connect the selected Jira site before connecting Rovo MCP.")
    try:
        return ResponseModel(success=True, message="Rovo MCP authorization started", data={
            "authorization_url": begin_rovo_oauth(user.get_user_id(), user.get_email(), project_id, cloud_id)
        })
    except JiraIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@callback_router.get("/integrations/jira/callback", include_in_schema=False)
def jira_callback(code: Optional[str] = Query(None), state: Optional[str] = Query(None), error: Optional[str] = Query(None)):
    frontend = __import__("os").getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    if error or not code or not state:
        return RedirectResponse(f"{frontend}/?{urlencode({'jira': 'error', 'message': error or 'Authorization cancelled'})}")
    try:
        flow = get_oauth_flow(state)
        result = finish_rovo_oauth(code, state) if flow == "rovo_mcp" else finish_oauth(code, state)
        if flow == "project_import":
            return RedirectResponse(f"{frontend}/?{urlencode({'jira': 'connected', 'openJiraImport': '1'})}")
        return RedirectResponse(f"{frontend}/projects/{result['project_id']}?{urlencode({'openConfiguration': 'Jira'})}")
    except JiraIntegrationError as exc:
        return RedirectResponse(f"{frontend}/?{urlencode({'jira': 'error', 'message': str(exc)})}")


@router.get("/{project_id}/jira/projects")
def jira_projects(project_id: str, cloud_id: str = Query(...), user: UserData = Depends(get_current_user)):
    _access(project_id, user, require_lead=True)
    try:
        return ResponseModel(
            success=True,
            message="Jira projects loaded",
            data=list_jira_projects(user.get_user_id(), cloud_id),
        )
    except JiraIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_id}/jira/test")
def test_jira_access(project_id: str, user: UserData = Depends(get_current_user)):
    """Run one bounded, read-only Jira search without involving the chat model."""
    _access(project_id, user, require_lead=True)
    result = search_project_jira_issues(
        project_id=project_id,
        user_id=user.get_user_id(),
        query="ORDER BY updated DESC",
        limit=1,
    )
    if result.get("error"):
        return ResponseModel(success=False, message=result["error"], data=result)
    return ResponseModel(success=True, message="Jira MCP search succeeded", data=result)


@router.patch("/{project_id}/jira/config")
def save_jira_configuration(project_id: str, payload: JiraProjectConfigurationRequest, user: UserData = Depends(get_current_user)):
    _access(project_id, user, require_lead=True)
    connection = get_connection(user.get_user_id(), payload.cloud_id)
    if not connection:
        raise HTTPException(status_code=400, detail="Connect the selected Jira site before saving this project configuration.")
    config = {"cloud_id": payload.cloud_id, "project_key": payload.project_key.strip().upper(),
              "site_name": connection.get("site_name"), "account_email": connection.get("account_email")}
    FIRESTORE_CLIENT.collection("projects").document(project_id).update({"jira_config": config})
    return ResponseModel(success=True, message="Jira configuration saved", data=config)


@router.delete("/{project_id}/jira/connection")
def disconnect_jira(project_id: str, user: UserData = Depends(get_current_user)):
    _access(project_id, user, require_lead=True)
    project_ref = FIRESTORE_CLIENT.collection("projects").document(project_id)
    project = project_ref.get().to_dict() or {}
    config = project.get("jira_config") or {}
    if config.get("cloud_id"):
        disconnect(user.get_user_id(), config["cloud_id"])
    project_ref.update({"jira_config": {}})
    return ResponseModel(success=True, message="Jira disconnected", data=None)
