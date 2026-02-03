from fastapi import APIRouter, HTTPException, Header, Path
from fastapi.responses import JSONResponse
from typing import Optional

from src.schemas.response import ResponseModel
from src.utils.auth import validate_user_and_get_data
from src.utils.user_stories import get_user_stories_by_epic_with_auth

router = APIRouter()

@router.get(
    "/{epic_id}/user-stories/",
    response_description="Get all user stories for a specific epic.",
)
async def get_user_stories_by_epic_route(
    epic_id: str = Path(..., description="The epic ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Retrieves all user stories for a specific epic.
    Requires authentication and user must own the project/epic.
    
    Args:
        epic_id (str): The epic ID to get user stories for
        authorization (str): Authorization header with Bearer token
    
    Returns:
        ResponseModel: List of user stories for the epic in structured format with fields array
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] Getting user stories for epic {epic_id}, user: {user_data.get_user_id()}")

    try:
        response = get_user_stories_by_epic_with_auth(epic_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))