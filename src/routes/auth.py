from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from src.utils.authz.auth import authenticate_user_firebase, authenticate_external_user, build_login_user_payload

from src.schemas.auth_request import AuthRequest
from src.schemas.auth_response import AuthResponse
from src.services.setup.firebase_setup import FIREBASE

router = APIRouter()


@router.post(
    "/",
    response_description="Authenticates the user in the Firebase Authentication service.",
)
async def authenticate_user(req: AuthRequest) -> AuthResponse:
    try:
        response = authenticate_user_firebase(req.email, req.password, req.version)
        content = response.dict()
        if response.success:
            content["user"] = build_login_user_payload(req.email)
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
        content = response.dict()
        if response.success:
            content["user"] = build_login_user_payload(req.email)
        return JSONResponse(
            status_code=200,
            content=content,
        )
    except HTTPException as e:
        raise e  # Let FastAPI handle HTTPException (401, etc)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
