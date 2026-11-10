import base64
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx
import jwt
import requests
from fastapi import HTTPException, logger


GITHUB_API_BASE = "https://api.github.com"
MAX_GITHUB_FILE_SIZE_BYTES = 200_000

BASE_DIR = Path(__file__).resolve().parent


def get_github_app_jwt() -> str:
    app_id = os.environ.get("GITHUB_APP_ID")
    if not app_id:
        raise Exception("GITHUB_APP_ID environment variable is not set.")

    relative_key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
    if not relative_key_path:
        raise Exception("GITHUB_APP_PRIVATE_KEY_PATH environment variable is not set.")

    key_path = BASE_DIR / relative_key_path if not os.path.isabs(relative_key_path) else Path(relative_key_path)

    if not key_path.exists():
        raise FileNotFoundError(f"GitHub App private key file not found at: {key_path}")

    # Read the private key from the specified file
    with open(key_path, "rb") as pem_file:
        private_key = pem_file.read()

    # Generate a JWT using the private key and app ID
    issued_at = int(time.time())
    payload = {
        # GitHub recommends allowing for server/GitHub clock drift. Keep the
        # expiry below its 10-minute maximum rather than exactly on the limit.
        "iat": issued_at - 60,
        "exp": issued_at + (9 * 60),
        "iss": app_id
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(installation_id: str) -> str:
    encoded_jwt = get_github_app_jwt()

    # We ask the Installation Token
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"

    with httpx.Client(timeout=10.0) as client:
        response = client.post(url, headers=headers)
        if response.status_code != 201:
            logger.error(f"Failed to get installation token: {response.status_code} - {response.text}")
            raise Exception(f"Failed to get installation token: {response.status_code} - {response.text}")

    return response.json()["token"]


def uninstall_github_app_installation(installation_id: str) -> None:
    """Uninstall this GitHub App from an account or organization installation."""
    encoded_jwt = get_github_app_jwt()
    response = requests.delete(
        f"{GITHUB_API_BASE}/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {encoded_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "frida-product-planner",
        },
        timeout=30,
    )
    # A 404 means the installation was already removed in GitHub. Treat it as
    # a successful idempotent uninstall so Product Planner can still clear its
    # now-stale project configuration.
    if response.status_code in {202, 404}:
        return
    if response.status_code != 202:
        _raise_for_github_error(response, "Failed to uninstall the GitHub App")

def parse_github_repository_reference(repository_url: str) -> Tuple[str, str]:
    raw_value = str(repository_url or "").strip()
    if not raw_value:
        raise HTTPException(status_code=400, detail="GitHub repository URL is required")

    if raw_value.startswith("http://") or raw_value.startswith("https://"):
        parsed = urlparse(raw_value)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise HTTPException(status_code=400, detail="Only github.com repositories are supported")
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    else:
        path_parts = [part for part in raw_value.strip("/").split("/") if part]

    if len(path_parts) < 2:
        raise HTTPException(status_code=400, detail="Repository must be in the form owner/repository")

    owner = path_parts[0].strip()
    repo = path_parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not owner or not repo:
        raise HTTPException(status_code=400, detail="Repository must be in the form owner/repository")

    return owner, repo


def _build_github_headers(api_token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "frida-product-planner",
    }
    clean_token = str(api_token or "").strip()
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"
    return headers


def _raise_for_github_error(response: requests.Response, default_detail: str) -> None:
    if response.ok:
        return

    detail = default_detail
    try:
        payload = response.json()
        detail = payload.get("message") or detail
    except Exception:
        if response.text:
            detail = response.text

    status_code = 400 if response.status_code < 500 else 502
    if response.status_code == 401:
        status_code = 401
    elif response.status_code == 403:
        status_code = 403
    elif response.status_code == 404:
        status_code = 404

    raise HTTPException(status_code=status_code, detail=detail)


def get_github_repository_metadata(
    repository_url: str,
    api_token: Optional[str],
) -> Dict[str, Any]:
    owner, repo = parse_github_repository_reference(repository_url)
    response = requests.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
        headers=_build_github_headers(api_token),
        timeout=30,
    )
    _raise_for_github_error(response, "Failed to load GitHub repository metadata")
    payload = response.json()
    return {
        "owner": owner,
        "repo": repo,
        "default_branch": payload.get("default_branch") or "main",
        "full_name": payload.get("full_name") or f"{owner}/{repo}",
        "private": bool(payload.get("private")),
    }


def list_github_repository_files(
    repository_url: str,
    api_token: Optional[str],
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = get_github_repository_metadata(repository_url, api_token)
    owner = metadata["owner"]
    repo = metadata["repo"]
    resolved_branch = str(branch or "").strip() or metadata["default_branch"]

    response = requests.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{quote(resolved_branch, safe='')}",
        headers=_build_github_headers(api_token),
        params={"recursive": "1"},
        timeout=30,
    )
    _raise_for_github_error(response, "Failed to load GitHub repository files")
    payload = response.json()

    files: List[Dict[str, Any]] = []
    for item in payload.get("tree") or []:
        if item.get("type") != "blob":
            continue
        files.append(
            {
                "path": item.get("path"),
                "sha": item.get("sha"),
                "size": int(item.get("size") or 0),
            }
        )

    files.sort(key=lambda item: item.get("path") or "")
    return {
        "repository": metadata["full_name"],
        "branch": resolved_branch,
        "files": files,
    }


def get_github_file_content(
    repository_url: str,
    api_token: Optional[str],
    path: str,
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = get_github_repository_metadata(repository_url, api_token)
    owner = metadata["owner"]
    repo = metadata["repo"]
    resolved_branch = str(branch or "").strip() or metadata["default_branch"]
    clean_path = str(path or "").strip().lstrip("/")

    if not clean_path:
        raise HTTPException(status_code=400, detail="GitHub file path is required")

    response = requests.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{quote(clean_path, safe='/')}",
        headers=_build_github_headers(api_token),
        params={"ref": resolved_branch},
        timeout=30,
    )
    _raise_for_github_error(response, "Failed to load GitHub file")
    payload = response.json()

    if payload.get("type") != "file":
        raise HTTPException(status_code=400, detail="The requested GitHub path is not a file")

    file_size = int(payload.get("size") or 0)
    if file_size > MAX_GITHUB_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"GitHub file is too large to preview (max {MAX_GITHUB_FILE_SIZE_BYTES} bytes)",
        )

    if payload.get("encoding") != "base64":
        raise HTTPException(status_code=400, detail="Unsupported GitHub file encoding")

    try:
        decoded_bytes = base64.b64decode((payload.get("content") or "").encode("utf-8"))
        decoded_text = decoded_bytes.decode("utf-8")
    except Exception as error:
        raise HTTPException(status_code=400, detail="GitHub file is not valid UTF-8 text") from error

    return {
        "repository": metadata["full_name"],
        "branch": resolved_branch,
        "path": clean_path,
        "sha": payload.get("sha"),
        "size": file_size,
        "download_url": payload.get("download_url"),
        "content": decoded_text,
        "encoding": "utf-8",
    }

def list_github_app_repositories(installation_id: str) -> List[Dict[str, Any]]:
    try:
        # Generate token
        token = get_installation_token(installation_id)

        # Make a request to the GitHub Api
        url = f"{GITHUB_API_BASE}/installation/repositories"
        headers = _build_github_headers(token)

        response = requests.get(url, headers=headers, timeout=30)
        _raise_for_github_error(response, "Failed to load repositories for GitHub App installation")

        payload = response.json()
        repositories = payload.get("repositories", [])

        # Map only the data that the fronted needs
        return [
            {
                "id": repo.get("id"),
                "name": repo.get("name"),
                "full_name": repo.get("full_name"),
                "url": repo.get("html_url"),
                "default_branch": repo.get("default_branch")
            }
            for repo in repositories
        ]
    except Exception as e:
        logger.error(f"Error listing app repositories: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def list_github_repository_branches(
    repository_url: str,
    installation_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    try:
        # Get the owner and repo
        owner, repo = parse_github_repository_reference(repository_url)

        # Determine what token use
        active_token = get_installation_token(installation_id)

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches"
        headers = _build_github_headers(active_token)

        params = {"per_page": 100}

        response = requests.get(url, headers=headers, params=params, timeout=30)
        _raise_for_github_error(response, f"Failed to load branches for repository {owner}/{repo}")

        branches = response.json()

        return [
            {
                "name": branch.get("name"),
                "protected": branch.get("protected", False)
            }
            for branch in branches
        ]
    except Exception as e:
        logger.error(f"Error listing repository branches: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
