from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import re
import secrets
import string
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from src.schemas.project_creation import ProjectCreationInitializationData
from src.schemas.user_data import UserData
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.services.workflows.project_creation.common import (
    ProjectKeyConflictError,
    ProjectRecordCreationError,
    create_project_record,
)
from src.utils.planning.projects import get_project_by_id
from src.utils.planning.epics import create_epic
from src.utils.planning.user_stories import create_user_story
from src.utils.planning.user_story_generation import enrich_user_story_details


logger = logging.getLogger(__name__)

_PP_JIRA_DEBUG = str(os.getenv("PP_JIRA_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}

_JIRA_GROUPING_BATCH_SIZE = 25


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_jira_base_url(value: str) -> str:
    """
    Sanitize Jira base URL.

    Users often paste a deep Jira/Confluence URL (e.g. `/jira/software/...` or `/wiki/...`).
    For Jira Cloud, the REST API base is the site origin (e.g. `https://example.atlassian.net`).
    For Jira Server/DC, the REST API base may include a context path (e.g. `https://jira.company.com/jira`).
    """

    raw = str(value or "").strip()
    if not raw:
        return ""

    # If the user omitted the scheme, assume https.
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = f"https://{raw}"

    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw.rstrip("/")

    if not parsed.netloc:
        return raw.rstrip("/")

    host = parsed.netloc.lower()
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or ""

    # Jira Cloud: always use site origin (strip any `/jira/...` or `/wiki/...`).
    if host.endswith(".atlassian.net"):
        return origin.rstrip("/")

    # If the user pasted a full API URL, keep everything before `/rest/api/{2|3}`.
    rest_match = re.search(r"(?i)/rest/api/(?:2|3)(?:/|$)", path)
    if rest_match:
        prefix = path[: rest_match.start()].rstrip("/")
        return f"{origin}{prefix}".rstrip("/")

    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return origin.rstrip("/")

    # Atlassian Cloud custom domains commonly use these product prefixes.
    first = segments[0].lower()
    second = segments[1].lower() if len(segments) > 1 else ""
    if first == "wiki":
        return origin.rstrip("/")
    if first == "jira" and second in {"software", "servicedesk", "work-management", "core"}:
        return origin.rstrip("/")

    # Jira Server/DC context path: keep segments before common UI roots.
    ui_roots = {"secure", "browse", "projects", "issues", "plugins", "login"}
    try:
        ui_index = next(index for index, seg in enumerate(segments) if seg.lower() in ui_roots)
    except StopIteration:
        ui_index = None

    if ui_index is None:
        # If the user already pasted the context root (e.g. `/jira`), keep it,
        # but avoid keeping deep paths.
        context_segments = segments if len(segments) <= 2 else segments[:1]
    elif ui_index == 0:
        context_segments = []
    else:
        context_segments = segments[:ui_index]

    context_path = "/" + "/".join(context_segments) if context_segments else ""
    return f"{origin}{context_path}".rstrip("/")


def _sanitize_project_key(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _derive_project_key_base(*, jira_project_key: str, name: str, description: str) -> str:
    jira_key = _sanitize_project_key(jira_project_key)
    if jira_key:
        return (jira_key + "PRJ")[:3]

    candidates = re.findall(r"[A-Za-z0-9]+", (name or "")) or re.findall(r"[A-Za-z0-9]+", (description or ""))
    if not candidates:
        return "PRJ"
    acronym = "".join(word[0].upper() for word in candidates if word)
    return (acronym + "PRJ")[:3]


def _random_project_key_suffix(length: int = 3) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(1, length)))


class JiraImportError(RuntimeError):
    """Base error for Jira import failures."""


class JiraAuthenticationError(JiraImportError):
    """Raised when Jira credentials are invalid."""


class JiraApiError(JiraImportError):
    """Raised when Jira returns an unexpected error response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        url: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.detail = detail


@dataclass(frozen=True)
class JiraCredentials:
    base_url: str
    email: str
    api_token: str


class JiraClient:
    def __init__(self, credentials: JiraCredentials):
        self.credentials = credentials
        self._base_url = _sanitize_jira_base_url(credentials.base_url)
        self._api_versions = (3, 2)

    def _build_url(self, api_version: int, path: str) -> str:
        normalized_path = str(path or "").lstrip("/")
        return f"{self._base_url}/rest/api/{api_version}/{normalized_path}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self._base_url:
            raise JiraApiError("jira_base_url is required")

        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Accept", "application/json")

        timeout = kwargs.pop("timeout", httpx.Timeout(30.0))

        last_error: Optional[Exception] = None
        async with httpx.AsyncClient(
            auth=(self.credentials.email, self.credentials.api_token),
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            for api_version in self._api_versions:
                url = self._build_url(api_version, path)
                response = await client.request(method, url, headers=headers, **kwargs)
                if _PP_JIRA_DEBUG and method.upper() == "GET" and str(path or "").lstrip("/") == "project":
                    print(f"[JIRA DEBUG] GET {url} -> {response.status_code}")
                    print(response.text)

                if response.status_code == 404 and api_version != self._api_versions[-1]:
                    # Jira Server/DC may expose v2, while Jira Cloud exposes v3.
                    continue

                if response.status_code in (401, 403):
                    raise JiraAuthenticationError("Jira authentication failed. Check email/token and permissions.")

                if response.status_code >= 400:
                    detail = ""
                    try:
                        payload = response.json()
                        detail = payload.get("errorMessages") or payload.get("errors") or payload
                    except Exception:
                        detail = response.text
                    raise JiraApiError(
                        f"Jira API error ({response.status_code}): {detail}",
                        status_code=response.status_code,
                        url=url,
                        detail=detail,
                    )

                try:
                    return response.json()
                except Exception as exc:
                    last_error = exc
                    break

        raise JiraApiError(f"Failed to parse Jira response: {last_error}")

    async def get_project(self, jira_project_key: str) -> Dict[str, Any]:
        return await self._request("GET", f"project/{jira_project_key}")

    async def iter_search_issues(
        self,
        *,
        jql: str,
        fields: Optional[Iterable[str]] = None,
        max_results: int = 100,
    ):
        """
        Iterate Jira issues matching a JQL query in pages (does not load everything at once).

        Jira Cloud requires `/rest/api/3/search/jql` (enhanced search) which paginates via
        `nextPageToken`. Jira Server/Data Center may not support the enhanced endpoint, so
        we fall back to the legacy `/search` API when needed.
        """

        field_list = [str(item) for item in (fields or []) if str(item or "").strip()] or None

        # 1) Prefer enhanced JQL search (Cloud): POST /search/jql (no URL-encoding required).
        next_page_token: Optional[str] = None
        try:
            while True:
                body: Dict[str, Any] = {"jql": jql, "maxResults": max_results}
                if field_list:
                    body["fields"] = field_list
                if next_page_token:
                    body["nextPageToken"] = next_page_token

                payload = await self._request("POST", "search/jql", json=body)
                if not isinstance(payload, dict):
                    break

                issues = payload.get("issues") or []
                if isinstance(issues, list):
                    for issue in issues:
                        if isinstance(issue, dict):
                            yield issue

                is_last = payload.get("isLast")
                next_page_token_value = payload.get("nextPageToken")
                next_page_token = str(next_page_token_value).strip() if next_page_token_value else None

                if is_last is True:
                    break
                if not next_page_token:
                    break

            return
        except JiraApiError as exc:
            # Enhanced endpoint not supported (Server/DC) => fallback.
            if exc.status_code not in {404, 405}:
                raise

        # 2) Legacy search (Server/DC): GET /search (startAt-based).
        start_at = 0
        while True:
            params: Dict[str, Any] = {"jql": jql, "startAt": start_at, "maxResults": max_results}
            if field_list:
                params["fields"] = ",".join(field_list)

            payload = await self._request("GET", "search", params=params)
            if not isinstance(payload, dict):
                break

            issues = payload.get("issues") or []
            if isinstance(issues, list):
                for issue in issues:
                    if isinstance(issue, dict):
                        yield issue

            total = int(payload.get("total") or 0)
            page_size = int(payload.get("maxResults") or max_results)
            start_at += page_size
            if start_at >= total:
                break

    async def search_issues(
        self,
        *,
        jql: str,
        fields: Optional[Iterable[str]] = None,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        async for issue in self.iter_search_issues(jql=jql, fields=fields, max_results=max_results):
            collected.append(issue)
        return collected

    async def list_projects(self, *, max_results: int = 50) -> List[Dict[str, Any]]:
        """
        List Jira projects visible to the provided credentials.

        Prefer the paginated `project/search` endpoint (Jira Cloud), with a fallback to
        `project` for instances that do not support search.
        """

        async def fetch_project_search(extra_params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
            start_at = 0
            collected: List[Dict[str, Any]] = []
            extra_params = dict(extra_params or {})

            while True:
                params: Dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
                params.update(extra_params)

                payload = await self._request(
                    "GET",
                    "project/search",
                    params=params,
                )

                if isinstance(payload, list):
                    return [item for item in payload if isinstance(item, dict)]

                if not isinstance(payload, dict):
                    return collected

                values = payload.get("values")
                if not isinstance(values, list):
                    projects_value = payload.get("projects")
                    if isinstance(projects_value, list):
                        return [item for item in projects_value if isinstance(item, dict)]
                    return collected

                collected.extend([item for item in values if isinstance(item, dict)])

                is_last = payload.get("isLast")
                if is_last is True:
                    break

                if not values:
                    break

                start_at += int(payload.get("maxResults") or max_results)

            return collected

        # 1) Prefer project/search (Cloud). Try a couple variants for compatibility/perms.
        search_variants: List[Dict[str, Any]] = [
            {"action": "view"},
            {},
            {"action": "browse"},
        ]

        for variant in search_variants:
            try:
                collected = await fetch_project_search(variant)
            except JiraApiError:
                continue
            if collected:
                return collected

        # Some Jira instances return an empty list for `/project/search` but still support `/project`.
        try:
            fallback = await self._request("GET", "project")
        except JiraApiError:
            return []

        if isinstance(fallback, list):
            return [item for item in fallback if isinstance(item, dict)]
        if isinstance(fallback, dict):
            values = fallback.get("values")
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
            projects_value = fallback.get("projects")
            if isinstance(projects_value, list):
                return [item for item in projects_value if isinstance(item, dict)]
        # Last chance: recently viewed projects (some instances restrict listing).
        try:
            recent = await self._request("GET", "project/recent")
        except JiraApiError:
            return []

        if isinstance(recent, list):
            return [item for item in recent if isinstance(item, dict)]
        if isinstance(recent, dict):
            values = recent.get("values")
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
            projects_value = recent.get("projects")
            if isinstance(projects_value, list):
                return [item for item in projects_value if isinstance(item, dict)]
        return []


def _jira_description_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return str(value).strip()

    pieces: List[str] = []

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            pieces.append(node)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            pieces.append(str(node))
            return

        node_type = str(node.get("type") or "").strip().lower()
        if node_type == "text":
            text = node.get("text")
            if text:
                pieces.append(str(text))
            return

        content = node.get("content")
        if isinstance(content, list):
            for child in content:
                walk(child)

        if node_type in {"paragraph", "heading", "listitem"}:
            pieces.append("\n")
        if node_type in {"hardbreak"}:
            pieces.append("\n")

    walk(value)

    text = "".join(pieces)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _map_jira_status_to_epic_status(value: Any) -> str:
    status_name = str(value or "").strip().lower()
    if not status_name:
        return "To Do"
    if "done" in status_name:
        return "Done"
    if "progress" in status_name or "doing" in status_name or "in " in status_name:
        return "In Progress"
    return "To Do"


def _map_jira_priority(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not name:
        return "Medium"
    if "highest" in name or "critical" in name:
        return "Critical"
    if "high" in name:
        return "High"
    if "lowest" in name or "low" in name:
        return "Low"
    return "Medium"


def _extract_epic_key_for_issue(issue: Dict[str, Any], epic_keys: Iterable[str]) -> Optional[str]:
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    epic_key_set = {str(key) for key in epic_keys if str(key)}

    parent = fields.get("parent")
    if isinstance(parent, dict):
        parent_key = parent.get("key")
        if isinstance(parent_key, str) and parent_key in epic_key_set:
            return parent_key

    for field_key, value in fields.items():
        if not str(field_key).startswith("customfield_"):
            continue
        if isinstance(value, str) and value in epic_key_set:
            return value
        if isinstance(value, dict):
            candidate = value.get("key")
            if isinstance(candidate, str) and candidate in epic_key_set:
                return candidate

    return None


def _normalize_match_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_epic_name_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).casefold()


def _extract_jira_labels(fields: Dict[str, Any]) -> List[str]:
    raw = fields.get("labels")
    if not isinstance(raw, list):
        return []
    labels: List[str] = []
    for item in raw:
        label = str(item or "").strip()
        if label:
            labels.append(label)
    return labels


def _extract_jira_components(fields: Dict[str, Any]) -> List[str]:
    raw = fields.get("components")
    if not isinstance(raw, list):
        return []
    names: List[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _seconds_to_hours(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    if seconds <= 0:
        return 0.0
    return round(seconds / 3600, 2)


def _extract_jira_effort_hours(fields: Dict[str, Any]) -> float:
    direct_candidates = [
        fields.get("timeoriginalestimate"),
        fields.get("timeestimate"),
        fields.get("aggregatetimeoriginalestimate"),
        fields.get("aggregatetimeestimate"),
    ]
    for candidate in direct_candidates:
        hours = _seconds_to_hours(candidate)
        if hours > 0:
            return hours

    timetracking = fields.get("timetracking")
    if isinstance(timetracking, dict):
        nested_candidates = [
            timetracking.get("originalEstimateSeconds"),
            timetracking.get("remainingEstimateSeconds"),
        ]
        for candidate in nested_candidates:
            hours = _seconds_to_hours(candidate)
            if hours > 0:
                return hours

    return 0.0


def _derive_story_group(components: List[str], labels: List[str]) -> Tuple[str, str]:
    if components:
        return "component", components[0]
    if labels:
        return "label", labels[0]
    return "general", "General"


def _format_group_epic_name(group_type: str, group_value: str) -> str:
    value = str(group_value or "").strip()
    if not value:
        return "General"
    if group_type == "label":
        value = value.replace("-", " ").replace("_", " ").strip()
    return value[:120] if len(value) > 120 else value


def _validate_story_grouping_agent_response(
    response: Any,
    expected_story_keys: Iterable[str],
) -> Optional[List[Dict[str, Any]]]:
    expected_by_norm: Dict[str, str] = {}
    for raw_key in expected_story_keys:
        original = str(raw_key or "").strip()
        if not original:
            continue
        normalized = original.upper()
        if normalized not in expected_by_norm:
            expected_by_norm[normalized] = original
    expected_norm = set(expected_by_norm.keys())
    if not expected_norm:
        return None

    epics: Any
    if isinstance(response, dict):
        epics = response.get("epics")
    elif isinstance(response, list):
        epics = response
    else:
        return None
    if not isinstance(epics, list) or not epics:
        return None

    seen_norm: set[str] = set()
    sanitized: List[Dict[str, Any]] = []
    for item in epics:
        if not isinstance(item, dict):
            return None
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        description = str(item.get("description") or "").strip()
        story_keys = item.get("story_keys")
        if story_keys is None:
            story_keys = item.get("storyKeys")
        if not isinstance(story_keys, list) or not story_keys:
            return None
        normalized_keys: List[str] = []
        for raw_key in story_keys:
            key = str(raw_key or "").strip()
            if not key:
                return None
            key_norm = key.upper()
            if key_norm not in expected_by_norm:
                return None
            if key_norm in seen_norm:
                return None
            seen_norm.add(key_norm)
            normalized_keys.append(expected_by_norm[key_norm])
        sanitized.append(
            {
                "name": name[:120],
                "description": _truncate_text(description, 1200),
                "story_keys": normalized_keys,
            }
        )

    if seen_norm != expected_norm:
        return None
    return sanitized


def _try_group_stories_with_agent(
    *,
    user_data: UserData,
    existing_epics: List[Dict[str, Any]],
    stories: List[Dict[str, Any]],
    attempts: int = 3,
) -> Optional[List[Dict[str, Any]]]:
    expected_keys = [str(item.get("key") or "").strip() for item in stories if isinstance(item, dict)]
    expected_keys = [key for key in expected_keys if key]
    if not expected_keys:
        return None

    try:
        from src.intelligence.agents.jira_import.story_grouping_agent import (
            JIRA_STORY_GROUPING_AGENT,
        )
        from src.intelligence.agents.json_executor import execute_json_agent
    except Exception as exc:
        logger.warning("Jira grouping agent not available: %s", exc)
        return None

    trimmed_existing_epics: List[Dict[str, Any]] = []
    for epic in existing_epics[:50]:
        if not isinstance(epic, dict):
            continue
        name = str(epic.get("name") or "").strip()
        if not name:
            continue
        trimmed_existing_epics.append(
            {
                "name": name[:120],
                "description": _truncate_text(epic.get("description"), 400),
                "labels": list(epic.get("labels") or [])[:12],
            }
        )

    trimmed_stories: List[Dict[str, Any]] = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        key = str(story.get("key") or "").strip()
        if not key:
            continue
        trimmed_stories.append(
            {
                "key": key,
                "summary": _truncate_text(story.get("summary"), 180),
                "description": _truncate_text(story.get("description"), 500),
                "labels": list(story.get("labels") or [])[:12],
                "components": list(story.get("components") or [])[:12],
            }
        )

    try:
        raw = execute_json_agent(
            agent=JIRA_STORY_GROUPING_AGENT,
            prompt_kwargs={
                "existing_epics": trimmed_existing_epics,
                "stories": trimmed_stories,
            },
            attempts=max(1, int(attempts)),
            context={"user_data": user_data},
        )
    except Exception as exc:
        logger.warning("Jira story grouping agent failed: %s", exc)
        return None

    validated = _validate_story_grouping_agent_response(raw, expected_keys)
    if validated is None:
        logger.warning("Jira story grouping agent returned an invalid response; falling back to heuristics.")
    return validated


async def _persist_jira_import_metadata(
    *,
    project_id: str,
    jira_project_key: str,
    jira_base_url: str,
    status: str,
    stats: Optional[Dict[str, Any]] = None,
) -> None:
    update: Dict[str, Any] = {
        "creation_status": status,
        "updated_at": _current_timestamp_iso(),
        "jira_project_key": jira_project_key,
        "jira_base_url": jira_base_url,
    }
    if stats:
        update["jira_import_stats"] = stats

    try:
        FIRESTORE_CLIENT.collection("projects").document(project_id).set(update, merge=True)
    except Exception:
        logger.exception("Failed to update Jira import metadata for project %s", project_id)


def _derive_project_name_from_jira(project_payload: Dict[str, Any], jira_project_key: str) -> str:
    name = str(project_payload.get("name") or "").strip()
    if name:
        return name
    key = str(jira_project_key or "").strip().upper()
    return f"Imported Jira Project {key}".strip()


def _derive_project_description_from_jira(project_payload: Dict[str, Any]) -> str:
    raw = project_payload.get("description")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    return _jira_description_to_text(raw)


async def list_jira_projects(
    *,
    jira_base_url: str,
    jira_email: str,
    jira_api_token: str,
) -> List[Dict[str, str]]:
    """
    List Jira projects visible to the given credentials.

    Intended for the UI flow where the user enters Jira credentials first and then
    selects which Jira project to import.
    """

    credentials = JiraCredentials(
        base_url=_sanitize_jira_base_url(jira_base_url),
        email=str(jira_email or "").strip(),
        api_token=str(jira_api_token or "").strip(),
    )
    if not credentials.base_url:
        raise JiraImportError("jira_base_url is required")
    if not credentials.email:
        raise JiraImportError("jira_email is required")
    if not credentials.api_token:
        raise JiraImportError("jira_api_token is required")

    client = JiraClient(credentials)
    raw_projects = await client.list_projects()

    projects: List[Dict[str, str]] = []
    for item in raw_projects:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        name = str(item.get("name") or key).strip()
        projects.append({"key": key, "name": name})

    projects.sort(key=lambda p: (p.get("name", "").lower(), p.get("key", "").lower()))
    return projects


async def import_project_from_jira(
    *,
    user_data: UserData,
    jira_base_url: str,
    jira_email: str,
    jira_api_token: str,
    jira_project_key: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    project_key: Optional[str] = None,
    issue_types: Optional[List[str]] = None,
) -> ProjectCreationInitializationData:
    """
    Import a Jira project into Product Planner.

    Workflow:
    1) Read Jira project metadata to derive name/description when missing.
    2) Create the Product Planner project record in Firestore.
    3) Fetch epics + user stories from Jira and persist them as epics/user_stories documents.
    4) Update project status and store lightweight import metadata.
    """

    jira_project_key = str(jira_project_key or "").strip()
    if not jira_project_key:
        raise JiraImportError("jira_project_key is required")

    credentials = JiraCredentials(
        base_url=_sanitize_jira_base_url(jira_base_url),
        email=str(jira_email or "").strip(),
        api_token=str(jira_api_token or "").strip(),
    )
    if not credentials.base_url:
        raise JiraImportError("jira_base_url is required")
    if not credentials.email:
        raise JiraImportError("jira_email is required")
    if not credentials.api_token:
        raise JiraImportError("jira_api_token is required")

    client = JiraClient(credentials)

    project_payload = await client.get_project(jira_project_key)
    resolved_name = (name or "").strip() or _derive_project_name_from_jira(project_payload, jira_project_key)
    resolved_description = (description or "").strip() or _derive_project_description_from_jira(project_payload)

    requested_key = _sanitize_project_key(project_key or "")

    creation_status = "importing"
    creation_source = "jira"

    project_record = None
    if requested_key:
        project_record = create_project_record(
            user_data=user_data,
            name=resolved_name,
            description=resolved_description,
            project_key=requested_key,
            creation_status=creation_status,
            creation_source=creation_source,
        )
    else:
        base_key = _derive_project_key_base(
            jira_project_key=jira_project_key,
            name=resolved_name,
            description=resolved_description,
        )
        candidates = [_sanitize_project_key(base_key)]
        candidates.extend(f"{base_key[:2]}{_random_project_key_suffix(1)}" for _ in range(25))
        candidates.extend(_random_project_key_suffix(3) for _ in range(25))

        last_exc: Optional[Exception] = None
        for candidate in candidates:
            try:
                project_record = create_project_record(
                    user_data=user_data,
                    name=resolved_name,
                    description=resolved_description,
                    project_key=candidate,
                    creation_status=creation_status,
                    creation_source=creation_source,
                )
                last_exc = None
                break
            except ProjectKeyConflictError as exc:
                last_exc = exc

        if project_record is None:
            raise ProjectRecordCreationError(str(last_exc or "Failed to generate unique project key"))

    project_id = project_record.project_id
    project_context = get_project_by_id(
        project_id,
        user_data.get_user_id(),
        allow_member=True,
        user_email=user_data.get_email(),
    )
    project_data = project_context.data if project_context and project_context.success and isinstance(project_context.data, dict) else {}

    await _persist_jira_import_metadata(
        project_id=project_id,
        jira_project_key=jira_project_key,
        jira_base_url=credentials.base_url,
        status="importing",
    )

    epic_jql = f'project = "{jira_project_key}" AND issuetype = Epic ORDER BY created ASC'
    story_issue_types = issue_types or ["Story"]
    quoted_types = ", ".join(f'"{t}"' for t in story_issue_types if str(t or "").strip())
    if not quoted_types:
        quoted_types = '"Story"'

    story_jql = f'project = "{jira_project_key}" AND issuetype in ({quoted_types}) ORDER BY created ASC'

    epic_issues = await client.search_issues(
        jql=epic_jql,
        fields=["summary", "description", "labels", "status", "priority", "issuetype"],
    )

    epic_key_to_firestore: Dict[str, str] = {}
    epic_key_to_name: Dict[str, str] = {}
    imported_epics = 0
    epic_match_index: List[Dict[str, Any]] = []
    existing_epics_for_agent: List[Dict[str, Any]] = []
    epic_name_to_firestore: Dict[str, str] = {}
    epic_name_norm_to_firestore: Dict[str, str] = {}
    epic_name_norm_to_display: Dict[str, str] = {}

    for issue in epic_issues:
        jira_key = str(issue.get("key") or "").strip()
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        summary = str(fields.get("summary") or "").strip()
        raw_description = fields.get("description")
        description_text = _jira_description_to_text(raw_description)

        labels = _extract_jira_labels(fields)
        status_name = None
        status_obj = fields.get("status")
        if isinstance(status_obj, dict):
            status_name = status_obj.get("name")
        priority_name = None
        priority_obj = fields.get("priority")
        if isinstance(priority_obj, dict):
            priority_name = priority_obj.get("name")

        epic_payload = {
            "name": summary or jira_key or "Epic",
            "description": description_text,
            "labels": labels,
            "status": _map_jira_status_to_epic_status(status_name),
            "priority": _map_jira_priority(priority_name),
        }

        created = create_epic(project_id, user_data.get_user_id(), epic_payload)
        if not created.success or not created.data:
            logger.warning("Failed to create epic from Jira issue %s: %s", jira_key, created.message)
            continue

        epic_id = created.data.get("id")
        if jira_key and epic_id:
            epic_key_to_firestore[jira_key] = str(epic_id)
            epic_key_to_name[jira_key] = epic_payload["name"]
            epic_name_to_firestore[epic_payload["name"]] = str(epic_id)
            normalized_epic_name = _normalize_epic_name_key(epic_payload["name"])
            if normalized_epic_name and normalized_epic_name not in epic_name_norm_to_firestore:
                epic_name_norm_to_firestore[normalized_epic_name] = str(epic_id)
                epic_name_norm_to_display[normalized_epic_name] = epic_payload["name"]
            existing_epics_for_agent.append(
                {
                    "name": epic_payload["name"],
                    "description": description_text,
                    "labels": labels,
                }
            )
            epic_match_index.append(
                {
                    "id": str(epic_id),
                    "name": epic_payload["name"],
                    "match_text": _normalize_match_text(epic_payload["name"]),
                    "labels": {_normalize_match_text(label) for label in labels if _normalize_match_text(label)},
                }
            )
        imported_epics += 1

    group_epics: Dict[str, Tuple[str, str]] = {}

    def _ensure_group_epic(*, group_type: str, group_value: str) -> Tuple[str, str]:
        nonlocal imported_epics

        normalized_group = _normalize_match_text(group_value)
        group_id = "general" if group_type == "general" else f"{group_type}:{normalized_group}"
        existing = group_epics.get(group_id)
        if existing:
            return existing

        epic_name = _format_group_epic_name(group_type, group_value)
        description = (
            "Imported from Jira without a linked Epic."
            if group_type == "general"
            else f"Imported from Jira (auto-grouped by {group_type}: {group_value})."
        )

        labels = ["jira-import"]
        if group_type != "general" and normalized_group:
            labels.append(normalized_group.replace(" ", "-")[:50])

        created_epic = create_epic(
            project_id,
            user_data.get_user_id(),
            {
                "name": epic_name,
                "description": description,
                "labels": labels,
                "status": "To Do",
                "priority": "Medium",
            },
        )
        if created_epic.success and created_epic.data and created_epic.data.get("id"):
            epic_id = str(created_epic.data.get("id"))
            group_epics[group_id] = (epic_id, epic_name)
            epic_match_index.append(
                {
                    "id": epic_id,
                    "name": epic_name,
                    "match_text": _normalize_match_text(epic_name),
                    "labels": {_normalize_match_text(label) for label in labels if _normalize_match_text(label)},
                }
            )
            epic_name_to_firestore[epic_name] = epic_id
            normalized_epic_name = _normalize_epic_name_key(epic_name)
            if normalized_epic_name and normalized_epic_name not in epic_name_norm_to_firestore:
                epic_name_norm_to_firestore[normalized_epic_name] = epic_id
                epic_name_norm_to_display[normalized_epic_name] = epic_name
            existing_epics_for_agent.append(
                {
                    "name": epic_name,
                    "description": description,
                    "labels": labels,
                }
            )
            imported_epics += 1
            return epic_id, epic_name

        raise JiraImportError(f"Failed to create Epic for group '{group_value}'")

    def _find_matching_epic(*, components: List[str], labels: List[str], summary: str) -> Optional[Tuple[str, str]]:
        if not epic_match_index:
            return None

        for candidate in components + labels:
            normalized = _normalize_match_text(candidate)
            if not normalized:
                continue
            for epic in epic_match_index:
                if normalized in epic.get("labels", set()):
                    return epic["id"], epic["name"]
            for epic in epic_match_index:
                if normalized in str(epic.get("match_text") or ""):
                    return epic["id"], epic["name"]

        # Last resort: try to match by significant words in the summary.
        summary_tokens = [token for token in _normalize_match_text(summary).split(" ") if len(token) >= 4]
        for token in summary_tokens[:8]:
            needle = f" {token} "
            for epic in epic_match_index:
                haystack = f" {epic.get('match_text') or ''} "
                if needle in haystack:
                    return epic["id"], epic["name"]
        return None

    grouping_batch_size = max(5, min(100, int(_JIRA_GROUPING_BATCH_SIZE)))
    logger.info(
        "Jira import: story grouping batch_size=%s.",
        grouping_batch_size,
    )

    imported_stories = 0
    story_order = 0
    unlinked_batch: List[Dict[str, Any]] = []
    unlinked_by_key: Dict[str, Dict[str, Any]] = {}

    async def _create_user_story_in_epic(*, epic_id: str, epic_name: str, story: Dict[str, Any]) -> None:
        nonlocal imported_stories

        jira_key = str(story.get("key") or "").strip()
        summary = str(story.get("summary") or "").strip()
        description_text = str(story.get("description") or "").strip()
        order = int(story.get("order") or 0)
        effort_hours = _extract_jira_effort_hours(story.get("fields") if isinstance(story.get("fields"), dict) else {})

        story_payload: Dict[str, Any] = {
            "epic": epic_name,
            "user_story": summary or jira_key or "User story",
            "description": description_text,
            "user_story_id": jira_key,
            "order": order,
            "dependencies": [],
            "jira_base_url": credentials.base_url,
            "effortHours": effort_hours,
        }

        enriched_stories = await enrich_user_story_details(
            user_data=user_data,
            epic={"id": epic_id, "name": epic_name, "description": ""},
            project=project_data,
            stories=[story_payload],
        )
        if enriched_stories and isinstance(enriched_stories[0], dict):
            story_payload = enriched_stories[0]

        created_story = create_user_story(epic_id, user_data.get_user_id(), story_payload)
        if not created_story.success:
            logger.warning(
                "Failed to create user story from Jira issue %s: %s", jira_key, created_story.message
            )
            return
        imported_stories += 1

    def _get_or_create_agent_epic(*, epic_name: str, description: str) -> Optional[Tuple[str, str]]:
        nonlocal imported_epics

        resolved_name = str(epic_name or "").strip()
        if not resolved_name:
            return None
        resolved_name = resolved_name[:120]

        if resolved_name in epic_name_to_firestore:
            return epic_name_to_firestore[resolved_name], resolved_name

        normalized = _normalize_epic_name_key(resolved_name)
        if normalized and normalized in epic_name_norm_to_firestore:
            existing_id = epic_name_norm_to_firestore[normalized]
            display_name = epic_name_norm_to_display.get(normalized) or resolved_name
            return existing_id, display_name

        description_text = str(description or "").strip() or "Imported from Jira (AI-grouped)."
        labels = ["jira-import", "ai-grouped"]

        created_epic = create_epic(
            project_id,
            user_data.get_user_id(),
            {
                "name": resolved_name,
                "description": description_text,
                "labels": labels,
                "status": "To Do",
                "priority": "Medium",
            },
        )
        if not (created_epic.success and created_epic.data and created_epic.data.get("id")):
            logger.warning("Failed to create AI-grouped Epic '%s': %s", resolved_name, created_epic.message)
            return None

        epic_id = str(created_epic.data.get("id"))
        epic_name_to_firestore[resolved_name] = epic_id
        if normalized and normalized not in epic_name_norm_to_firestore:
            epic_name_norm_to_firestore[normalized] = epic_id
            epic_name_norm_to_display[normalized] = resolved_name

        epic_match_index.append(
            {
                "id": epic_id,
                "name": resolved_name,
                "match_text": _normalize_match_text(resolved_name),
                "labels": {_normalize_match_text(label) for label in labels if _normalize_match_text(label)},
            }
        )
        existing_epics_for_agent.append(
            {
                "name": resolved_name,
                "description": description_text,
                "labels": labels,
            }
        )
        imported_epics += 1
        return epic_id, resolved_name

    async def _flush_unlinked_batch() -> None:
        if not unlinked_batch:
            return

        nonlocal imported_epics

        logger.info(
            "Jira import: calling story grouping agent for %s unlinked stories (existing epics=%s).",
            len(unlinked_batch),
            len(existing_epics_for_agent),
        )

        batch_payload = [
            {
                "key": story.get("key"),
                "summary": story.get("summary"),
                "description": story.get("description"),
                "labels": story.get("labels"),
                "components": story.get("components"),
            }
            for story in unlinked_batch
        ]

        grouped = _try_group_stories_with_agent(
            user_data=user_data,
            existing_epics=existing_epics_for_agent,
            stories=batch_payload,
        )

        if grouped:
            logger.info("Jira import: grouping agent produced %s epic group(s).", len(grouped))
            for group in grouped:
                epic_name = str(group.get("name") or "").strip()
                story_keys = group.get("story_keys") or []
                description = str(group.get("description") or "").strip()
                resolved = _get_or_create_agent_epic(epic_name=epic_name, description=description)
                if not resolved:
                    for story_key in story_keys:
                        story = unlinked_by_key.get(str(story_key))
                        if not story:
                            continue
                        summary = str(story.get("summary") or "").strip()
                        story_labels = list(story.get("labels") or [])
                        story_components = list(story.get("components") or [])

                        matched = _find_matching_epic(
                            components=story_components,
                            labels=story_labels,
                            summary=summary,
                        )
                        if matched:
                            fallback_epic_id, fallback_epic_name = matched
                        else:
                            group_type, group_value = _derive_story_group(story_components, story_labels)
                            fallback_epic_id, fallback_epic_name = _ensure_group_epic(
                                group_type=group_type,
                                group_value=group_value,
                            )
                        await _create_user_story_in_epic(
                            epic_id=fallback_epic_id,
                            epic_name=fallback_epic_name,
                            story=story,
                        )
                    continue
                epic_id, resolved_epic_name = resolved
                for story_key in story_keys:
                    story = unlinked_by_key.get(str(story_key))
                    if not story:
                        continue
                    await _create_user_story_in_epic(epic_id=epic_id, epic_name=resolved_epic_name, story=story)
        else:
            logger.warning(
                "Jira import: grouping agent unavailable/invalid; using heuristic grouping for %s stories.",
                len(unlinked_batch),
            )
            for story in unlinked_batch:
                summary = str(story.get("summary") or "").strip()
                story_labels = list(story.get("labels") or [])
                story_components = list(story.get("components") or [])

                matched = _find_matching_epic(components=story_components, labels=story_labels, summary=summary)
                if matched:
                    epic_id, epic_name = matched
                else:
                    group_type, group_value = _derive_story_group(story_components, story_labels)
                    epic_id, epic_name = _ensure_group_epic(group_type=group_type, group_value=group_value)
                await _create_user_story_in_epic(epic_id=epic_id, epic_name=epic_name, story=story)

        unlinked_batch.clear()
        unlinked_by_key.clear()

    async for issue in client.iter_search_issues(
        jql=story_jql,
        fields=[
            "summary",
            "description",
            "labels",
            "components",
            "status",
            "priority",
            "issuetype",
            "parent",
            "*navigable",
        ],
    ):
        current_order = story_order
        story_order += 1

        jira_key = str(issue.get("key") or "").strip()
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        summary = str(fields.get("summary") or "").strip()
        raw_description = fields.get("description")
        description_text = _jira_description_to_text(raw_description)

        epic_key = _extract_epic_key_for_issue(issue, epic_key_to_firestore.keys())
        epic_id: str
        epic_name: str
        if epic_key and epic_key in epic_key_to_firestore:
            epic_id = epic_key_to_firestore[epic_key]
            epic_name = epic_key_to_name.get(epic_key) or "Epic"
        else:
            story_labels = _extract_jira_labels(fields)
            story_components = _extract_jira_components(fields)

            if jira_key:
                story_record = {
                    "key": jira_key,
                    "summary": summary,
                    "description": description_text,
                    "labels": story_labels,
                    "components": story_components,
                    "fields": fields,
                    "order": current_order,
                }
                unlinked_batch.append(story_record)
                unlinked_by_key[jira_key] = story_record
                if len(unlinked_batch) >= grouping_batch_size:
                    await _flush_unlinked_batch()
                continue

            matched = _find_matching_epic(components=story_components, labels=story_labels, summary=summary)
            if matched:
                epic_id, epic_name = matched
            else:
                group_type, group_value = _derive_story_group(story_components, story_labels)
                epic_id, epic_name = _ensure_group_epic(group_type=group_type, group_value=group_value)

        await _create_user_story_in_epic(
            epic_id=epic_id,
            epic_name=epic_name,
            story={
                "key": jira_key,
                "summary": summary,
                "description": description_text,
                "fields": fields,
                "order": current_order,
            },
        )

    await _flush_unlinked_batch()

    stats = {
        "imported_epics": imported_epics,
        "imported_user_stories": imported_stories,
        "imported_at": _current_timestamp_iso(),
        "jira_issue_types": story_issue_types,
    }

    await _persist_jira_import_metadata(
        project_id=project_id,
        jira_project_key=jira_project_key,
        jira_base_url=credentials.base_url,
        status="created",
        stats=stats,
    )

    return ProjectCreationInitializationData(project=project_record.project, clarification=None)
