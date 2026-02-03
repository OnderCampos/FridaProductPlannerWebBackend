"""
Routes for member invitation management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Header
from fastapi.responses import JSONResponse
from typing import Optional
import logging

from src.schemas.member_schemas import (
    MemberInvitationCreate,
    MemberInvitationResponse,
    InvitationsListResponse,
    InvitationAcceptResponse
)
from src.schemas.response import ResponseModel
from src.utils.invitations import (
    create_invitation,
    get_project_invitations,
    get_invitation_by_id,
    cancel_invitation,
    resend_invitation,
    accept_invitation,
    reject_invitation,
    check_pending_invitation,
    check_and_expire_invitations
)
from src.utils.members import check_member_exists
from src.utils.auth import validate_user_and_get_data

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/invite", response_model=ResponseModel, status_code=201)
async def invite_member(
    invitation_data: MemberInvitationCreate,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
):
    """
    Send an invitation to a new member to join a team.
    
    Business Rules:
    - Cannot invite a user who is already a member
    - Cannot invite a user who has a pending invitation
    - Invitation expires after 7 days
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
    print(f"[DEBUG] User data: {user_data}")
    
    try:

        # Create invitation
        invitation = create_invitation(
            project_id=invitation_data.team_id,
            member_id=invitation_data.user_id,
        )
        
        
        # Convert to response format
        invitation_response = MemberInvitationResponse(**invitation)
        
        return JSONResponse(
            status_code=201,
            content={
                "success": True,
                "message": "Invitation sent successfully",
                "data": invitation_response.dict()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating invitation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))






