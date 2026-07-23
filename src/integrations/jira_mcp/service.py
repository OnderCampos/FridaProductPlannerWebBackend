"""Per-user OAuth storage and read-only Atlassian Rovo MCP access."""

import base64
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from cryptography.fernet import Fernet, InvalidToken

from src.services.setup.firebase_setup import FIRESTORE_CLIENT


OAUTH_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
OAUTH_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
MCP_URL = os.getenv("ATLASSIAN_ROVO_MCP_URL", "https://mcp.atlassian.com/v1/mcp/authv2")
MCP_RESOURCE_METADATA_URL = os.getenv(
    "ATLASSIAN_ROVO_MCP_RESOURCE_METADATA_URL",
    "https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/mcp/authv2",
)
CONNECTIONS = "jira_connections"
OAUTH_STATES = "jira_oauth_states"
logger = logging.getLogger(__name__)


class JiraIntegrationError(RuntimeError):
    pass


def _log_mcp_response(step: str, response: httpx.Response) -> None:
    """Emit protocol diagnostics only when explicitly enabled for troubleshooting."""
    if os.getenv("JIRA_MCP_DEBUG", "").strip().lower() not in {"1", "true", "yes"}:
        return
    safe_headers = {
        key: value for key, value in response.headers.items()
        if key.lower() not in {"authorization", "cookie", "set-cookie"}
    }
    logger.warning(
        "Jira MCP %s response | status=%s | headers=%s | body=%s",
        step,
        response.status_code,
        json.dumps(safe_headers, sort_keys=True),
        response.text,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _fernet() -> Fernet:
    secret = os.getenv("JIRA_TOKEN_ENCRYPTION_KEY") or os.getenv("ENCRYPTION_KEY")
    if not secret:
        raise JiraIntegrationError("Jira integration is not configured: set JIRA_TOKEN_ENCRYPTION_KEY")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise JiraIntegrationError("The saved Jira connection cannot be decrypted. Reconnect Jira.") from exc


def _connection_id(user_id: str, cloud_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{cloud_id}".encode("utf-8")).hexdigest()


def _oauth_settings() -> Dict[str, str]:
    settings = {
        "client_id": os.getenv("ATLASSIAN_OAUTH_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("ATLASSIAN_OAUTH_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("ATLASSIAN_OAUTH_REDIRECT_URI", "").strip(),
    }
    if not all(settings.values()):
        raise JiraIntegrationError(
            "Jira OAuth is not configured. Set ATLASSIAN_OAUTH_CLIENT_ID, "
            "ATLASSIAN_OAUTH_CLIENT_SECRET, and ATLASSIAN_OAUTH_REDIRECT_URI."
        )
    _fernet()
    return settings


def begin_oauth(user_id: str, email: str, project_id: str, flow: str = "project_connection") -> str:
    settings = _oauth_settings()
    state = secrets.token_urlsafe(32)
    FIRESTORE_CLIENT.collection(OAUTH_STATES).document(state).set({
        "user_id": user_id,
        "email": email,
        "project_id": project_id,
        "flow": flow,
        "expires_at": _iso(_now() + timedelta(minutes=10)),
        "created_at": _iso(_now()),
    })
    scopes = os.getenv("ATLASSIAN_OAUTH_SCOPES", "read:jira-work offline_access")
    return str(httpx.URL(OAUTH_AUTHORIZE_URL).copy_merge_params({
        "audience": "api.atlassian.com",
        "client_id": settings["client_id"],
        "scope": scopes,
        "redirect_uri": settings["redirect_uri"],
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }))


def begin_import_oauth(user_id: str, email: str) -> str:
    """Start Jira OAuth for a new-project import before a project exists."""
    return begin_oauth(user_id, email, "", flow="project_import")


def _rovo_oauth_metadata() -> Dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resource_response = client.get(MCP_RESOURCE_METADATA_URL)
        if resource_response.is_error:
            raise JiraIntegrationError("Could not discover Atlassian Rovo MCP OAuth metadata.")
        resource_metadata = resource_response.json()
        authorization_servers = resource_metadata.get("authorization_servers") or []
        if not authorization_servers:
            raise JiraIntegrationError("Atlassian Rovo MCP did not provide an OAuth authorization server.")
        authorization_server = str(authorization_servers[0]).rstrip("/")
        metadata_url = f"{authorization_server}/.well-known/oauth-authorization-server"
        metadata_response = client.get(metadata_url)
        if metadata_response.is_error:
            raise JiraIntegrationError("Could not load Atlassian Rovo MCP OAuth server metadata.")
        metadata = metadata_response.json()
    required = ("authorization_endpoint", "token_endpoint", "registration_endpoint")
    if not all(metadata.get(key) for key in required):
        raise JiraIntegrationError("Atlassian Rovo MCP OAuth metadata is missing a required endpoint.")
    return {"resource": resource_metadata.get("resource") or MCP_URL, "metadata": metadata}


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def begin_rovo_oauth(user_id: str, email: str, project_id: str, cloud_id: str) -> str:
    _fernet()
    redirect_uri = os.getenv("ATLASSIAN_ROVO_MCP_REDIRECT_URI", "").strip()
    if not redirect_uri:
        raise JiraIntegrationError("Set ATLASSIAN_ROVO_MCP_REDIRECT_URI to the Rovo MCP callback URL.")
    oauth = _rovo_oauth_metadata()
    metadata = oauth["metadata"]
    verifier, challenge = _pkce_pair()
    with httpx.Client(timeout=30.0) as client:
        registration = client.post(metadata["registration_endpoint"], json={
            "client_name": "Product Planner Web",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
    if registration.is_error:
        raise JiraIntegrationError("Atlassian Rovo MCP could not register the Product Planner OAuth client.")
    client_id = str((registration.json() or {}).get("client_id") or "")
    if not client_id:
        raise JiraIntegrationError("Atlassian Rovo MCP registration did not return a client ID.")
    state = secrets.token_urlsafe(32)
    scopes = "read:me read:account read:jira-work offline_access"
    FIRESTORE_CLIENT.collection(OAUTH_STATES).document(state).set({
        "flow": "rovo_mcp", "user_id": user_id, "email": email, "project_id": project_id, "cloud_id": cloud_id,
        "expires_at": _iso(_now() + timedelta(minutes=10)), "created_at": _iso(_now()),
        "client_id": client_id, "code_verifier": verifier, "token_endpoint": metadata["token_endpoint"],
        "redirect_uri": redirect_uri, "resource": oauth["resource"],
    })
    return str(httpx.URL(metadata["authorization_endpoint"]).copy_merge_params({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": scopes, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": oauth["resource"],
    }))


def get_oauth_flow(state: str) -> Optional[str]:
    document = FIRESTORE_CLIENT.collection(OAUTH_STATES).document(state).get()
    return (document.to_dict() or {}).get("flow") if document.exists else None


def finish_rovo_oauth(code: str, state: str) -> Dict[str, Any]:
    state_ref = FIRESTORE_CLIENT.collection(OAUTH_STATES).document(state)
    state_doc = state_ref.get()
    if not state_doc.exists:
        raise JiraIntegrationError("Rovo MCP OAuth state is invalid or has expired. Start the connection again.")
    data = state_doc.to_dict() or {}
    state_ref.delete()
    if data.get("flow") != "rovo_mcp" or datetime.fromisoformat(str(data.get("expires_at")).replace("Z", "+00:00")) <= _now():
        raise JiraIntegrationError("Rovo MCP OAuth state has expired. Start the connection again.")
    with httpx.Client(timeout=30.0) as client:
        token_response = client.post(data["token_endpoint"], data={
            "grant_type": "authorization_code", "client_id": data["client_id"], "code": code,
            "redirect_uri": data["redirect_uri"], "code_verifier": data["code_verifier"],
            "resource": data["resource"],
        })
    if token_response.is_error:
        raise JiraIntegrationError("Atlassian Rovo MCP rejected the OAuth authorization code.")
    token = token_response.json()
    cloud_id = str(data.get("cloud_id") or "")
    user_id = str(data["user_id"])
    FIRESTORE_CLIENT.collection(CONNECTIONS).document(_connection_id(user_id, cloud_id)).set({
        "user_id": user_id, "cloud_id": cloud_id, "rovo_access_token": _encrypt(token.get("access_token")),
        "rovo_refresh_token": _encrypt(token.get("refresh_token")),
        "rovo_expires_at": _iso(_now() + timedelta(seconds=int(token.get("expires_in") or 3600))),
        "rovo_client_id": data["client_id"], "rovo_token_endpoint": data["token_endpoint"],
        "rovo_resource": data["resource"], "updated_at": _iso(_now()), "created_at": _iso(_now()),
    }, merge=True)
    return {"project_id": data["project_id"], "user_id": user_id}


def finish_oauth(code: str, state: str) -> Dict[str, Any]:
    state_ref = FIRESTORE_CLIENT.collection(OAUTH_STATES).document(state)
    state_doc = state_ref.get()
    if not state_doc.exists:
        raise JiraIntegrationError("Jira OAuth state is invalid or has expired. Start the connection again.")
    state_data = state_doc.to_dict() or {}
    state_ref.delete()
    expires_at = str(state_data.get("expires_at") or "")
    if not expires_at or datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= _now():
        raise JiraIntegrationError("Jira OAuth state has expired. Start the connection again.")

    settings = _oauth_settings()
    with httpx.Client(timeout=30.0) as client:
        response = client.post(OAUTH_TOKEN_URL, json={
            "grant_type": "authorization_code",
            "client_id": settings["client_id"],
            "client_secret": settings["client_secret"],
            "code": code,
            "redirect_uri": settings["redirect_uri"],
        })
        if response.is_error:
            raise JiraIntegrationError("Atlassian rejected the OAuth authorization code.")
        token_data = response.json()
        resources_response = client.get(
            ACCESSIBLE_RESOURCES_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        if resources_response.is_error:
            raise JiraIntegrationError("Could not load the Jira sites available to this account.")
        sites = resources_response.json()

    user_id = str(state_data["user_id"])
    account_id = str(token_data.get("account_id") or "")
    expires = _now() + timedelta(seconds=int(token_data.get("expires_in") or 3600))
    for site in sites if isinstance(sites, list) else []:
        cloud_id = str(site.get("id") or "")
        if not cloud_id:
            continue
        FIRESTORE_CLIENT.collection(CONNECTIONS).document(_connection_id(user_id, cloud_id)).set({
            "user_id": user_id,
            "cloud_id": cloud_id,
            "site_name": site.get("name") or site.get("url") or cloud_id,
            "site_url": site.get("url"),
            "account_id": account_id,
            "account_email": state_data.get("email"),
            "access_token": _encrypt(token_data.get("access_token")),
            "refresh_token": _encrypt(token_data.get("refresh_token")),
            "expires_at": _iso(expires),
            "updated_at": _iso(_now()),
            "created_at": _iso(_now()),
        }, merge=True)
    return {
        "project_id": state_data.get("project_id"),
        "flow": state_data.get("flow") or "project_connection",
        "sites": sites if isinstance(sites, list) else [],
    }


def list_sites(user_id: str) -> List[Dict[str, Any]]:
    docs = FIRESTORE_CLIENT.collection(CONNECTIONS).where("user_id", "==", user_id).get()
    return [{"cloud_id": item.get("cloud_id"), "name": item.get("site_name"), "url": item.get("site_url")}
            for doc in docs for item in [doc.to_dict() or {}]]


def list_jira_projects(user_id: str, cloud_id: str) -> List[Dict[str, str]]:
    """List Jira projects visible to the connected user for configuration only."""
    connection = get_connection(user_id, cloud_id)
    if not connection:
        raise JiraIntegrationError("Connect the selected Jira site before loading its projects.")
    endpoint = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project/search"
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            endpoint,
            headers={"Authorization": f"Bearer {_access_token(connection)}"},
            params={"maxResults": 100, "orderBy": "name"},
        )
    if response.is_error:
        raise JiraIntegrationError("Jira could not load projects for this site. Verify that the connected account can browse Jira projects.")
    payload = response.json()
    projects = payload.get("values") if isinstance(payload, dict) else []
    return [
        {"key": str(project.get("key") or ""), "name": str(project.get("name") or "")}
        for project in projects or []
        if isinstance(project, dict) and project.get("key")
    ]


def search_jira_rest(connection: Dict[str, Any], cloud_id: str, jql: str, max_results: int) -> Any:
    """Fallback for agents when a Rovo MCP tool is unavailable."""
    endpoint = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql"
    with httpx.Client(timeout=45.0) as client:
        response = client.get(
            endpoint,
            headers={"Authorization": f"Bearer {_access_token(connection)}"},
            params={"jql": jql, "maxResults": max_results, "fields": "summary,status,issuetype,priority"},
        )
    if response.is_error:
        raise JiraIntegrationError("Jira REST API could not complete the fallback search.")
    return response.json()


def get_connection(user_id: str, cloud_id: str) -> Optional[Dict[str, Any]]:
    doc = FIRESTORE_CLIENT.collection(CONNECTIONS).document(_connection_id(user_id, cloud_id)).get()
    return doc.to_dict() if doc.exists else None


def get_jira_rest_access_token(user_id: str, cloud_id: str) -> str:
    """Return a refreshed Jira REST OAuth token without exposing encrypted storage details."""
    connection = get_connection(user_id, cloud_id)
    if not connection:
        raise JiraIntegrationError("Connect the selected Jira site before importing it.")
    return _access_token(connection)


def disconnect(user_id: str, cloud_id: str) -> None:
    FIRESTORE_CLIENT.collection(CONNECTIONS).document(_connection_id(user_id, cloud_id)).delete()


def _access_token(connection: Dict[str, Any]) -> str:
    expires_at = str(connection.get("expires_at") or "")
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires <= _now() + timedelta(seconds=60):
                _refresh_access_token(connection)
        except ValueError:
            raise JiraIntegrationError("The saved Jira connection has an invalid expiration time. Reconnect Jira.")
    token = _decrypt(connection.get("access_token"))
    if not token:
        raise JiraIntegrationError("The Jira connection has no usable access token. Reconnect Jira.")
    return token


def _rovo_access_token(connection: Dict[str, Any]) -> str:
    token = _decrypt(connection.get("rovo_access_token"))
    if not token:
        raise JiraIntegrationError("Rovo MCP is not connected for this Jira site. Connect Rovo MCP in Configuration.")
    return token


def _refresh_access_token(connection: Dict[str, Any]) -> None:
    refresh_token = _decrypt(connection.get("refresh_token"))
    if not refresh_token:
        raise JiraIntegrationError("The Jira connection has expired and cannot be refreshed. Reconnect Jira.")
    settings = _oauth_settings()
    with httpx.Client(timeout=30.0) as client:
        response = client.post(OAUTH_TOKEN_URL, json={
            "grant_type": "refresh_token",
            "client_id": settings["client_id"],
            "client_secret": settings["client_secret"],
            "refresh_token": refresh_token,
        })
    if response.is_error:
        raise JiraIntegrationError("The Jira connection has expired. Reconnect Jira.")
    token_data = response.json()
    connection["access_token"] = _encrypt(token_data.get("access_token"))
    connection["refresh_token"] = _encrypt(token_data.get("refresh_token") or refresh_token)
    connection["expires_at"] = _iso(_now() + timedelta(seconds=int(token_data.get("expires_in") or 3600)))
    FIRESTORE_CLIENT.collection(CONNECTIONS).document(
        _connection_id(str(connection["user_id"]), str(connection["cloud_id"]))
    ).update({
        "access_token": connection["access_token"], "refresh_token": connection["refresh_token"],
        "expires_at": connection["expires_at"], "updated_at": _iso(_now()),
    })


def call_mcp_tool(connection: Dict[str, Any], tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Call an MCP tool over Streamable HTTP. The endpoint may return JSON or SSE."""
    headers = {"Authorization": f"Bearer {_rovo_access_token(connection)}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    with httpx.Client(timeout=45.0) as client:
        initialize = client.post(MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "id": secrets.token_hex(8), "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "product-planner", "version": "1.0"}},
        })
        _log_mcp_response("initialize", initialize)
        if initialize.is_error:
            raise JiraIntegrationError("Could not initialize the Atlassian Rovo MCP session.")
        session_id = initialize.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        initialized = client.post(MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
        })
        _log_mcp_response("initialized_notification", initialized)
        request = {"jsonrpc": "2.0", "id": secrets.token_hex(8), "method": "tools/call",
                   "params": {"name": tool_name, "arguments": arguments}}
        response = client.post(MCP_URL, headers=headers, json=request)
        _log_mcp_response("tools_call", response)
    if response.is_error:
        raise JiraIntegrationError("Atlassian Rovo MCP rejected the request. Reconnect Jira or verify your Rovo MCP access.")
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if "error" in payload:
                    raise JiraIntegrationError(str(payload["error"]))
                result = payload.get("result")
                _raise_mcp_tool_error(result)
                return result
        raise JiraIntegrationError("Atlassian Rovo MCP returned no tool result.")
    payload = response.json()
    if "error" in payload:
        raise JiraIntegrationError(str(payload["error"]))
    result = payload.get("result")
    _raise_mcp_tool_error(result)
    return result


def _raise_mcp_tool_error(result: Any) -> None:
    """Turn MCP's in-band `isError` responses into actionable application errors."""
    if not isinstance(result, dict) or not result.get("isError"):
        return
    messages = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            messages.append(str(item["text"]))
    detail = " ".join(messages).strip()
    raise JiraIntegrationError(detail or "Atlassian Rovo MCP could not complete the Jira search.")
