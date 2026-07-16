from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from src.utils.authz.auth import (
    authenticate_user_firebase,
    authenticate_external_user,
    build_login_user_payload,
    firebase_refresh_session,
)

from src.schemas.auth_request import AuthRequest
from src.schemas.auth_refresh_request import AuthRefreshRequest
from src.schemas.auth_response import AuthResponse

router = APIRouter()


def _build_auth_response_content(response, email: str | None = None) -> dict:
    raw_data = response.data
    id_token = raw_data
    refresh_token = None
    expires_in = None

    if isinstance(raw_data, dict) and any(
        key in raw_data for key in ("id_token", "idToken", "refresh_token", "refreshToken")
    ):
        id_token = raw_data.get("id_token")
        refresh_token = raw_data.get("refresh_token")
        expires_in = raw_data.get("expires_in")

    content = {
        "success": response.success,
        "message": response.message,
        "data": id_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
    }
    if response.success and email:
        content["user"] = build_login_user_payload(email)
    return content


@router.post(
    "/",
    response_description="Authenticates the user in the Firebase Authentication service.",
)
async def authenticate_user(req: AuthRequest) -> AuthResponse:
    try:
        response = authenticate_user_firebase(req.email, req.password, req.version)
        content = _build_auth_response_content(response, req.email)
        return JSONResponse(
            status_code=200,
            content=content,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post(
    "/external",
    response_description="Authenticates the user in an external service and Firebase.",
)   
async def authenticate_external(req: AuthRequest) -> AuthResponse:
    try:
        response = await authenticate_external_user(req.email, req.password, req.version)
        content = _build_auth_response_content(response, req.email)
        return JSONResponse(
            status_code=200,
            content=content,
        )
    except HTTPException as e:
        raise e  # Let FastAPI handle HTTPException (401, etc)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/refresh",
    response_description="Refreshes a Firebase session using a refresh token.",
)
async def refresh_authentication(req: AuthRefreshRequest) -> AuthResponse:
    try:
        response = firebase_refresh_session(req.refresh_token)
        return JSONResponse(
            status_code=200,
            content=_build_auth_response_content(response),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
