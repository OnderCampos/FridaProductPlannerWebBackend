from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from src.utils.auth import authenticate_user_firebase, authenticate_external_user

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
        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/external",
    response_description="Authenticates the user in an external service and Firebase.",
)   
async def authenticate_external(req: AuthRequest) -> AuthResponse:
    try:
        print(f"External auth")
        response = await authenticate_external_user(req.email, req.password, req.version)
        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except HTTPException as e:
        raise e  # Let FastAPI handle HTTPException (401, etc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))