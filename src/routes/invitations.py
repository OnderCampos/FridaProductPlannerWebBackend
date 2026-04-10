"""
Routes for member invitation management endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from typing import Optional
import logging

from src.schemas.member_schemas import MemberInvitationCreate
from src.schemas.response import ResponseModel
from src.utils.planning.invitations import (
    create_invitation,
    accept_invitation,
    reject_invitation,
    check_pending_invitation,
)
from src.utils.planning.members import check_member_exists
from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_invitation_payload(invitation: dict) -> dict:
    payload = dict(invitation or {})
    payload["projectId"] = payload.get("projectId") or payload.get("project_id")
    payload["memberType"] = payload.get("memberType") or payload.get("member_type") or "member"
    payload["invitedBy"] = payload.get("invitedBy") or payload.get("invited_by")
    payload["invitedDate"] = payload.get("invitedDate") or payload.get("invited_date")
    payload["expiresDate"] = payload.get("expiresDate") or payload.get("expires_date")
    payload["responseDate"] = payload.get("responseDate") or payload.get("response_date")
    return payload


@router.post("/invite", response_model=ResponseModel, status_code=201)
async def invite_member(
    invitation_data: MemberInvitationCreate,
    user_data: UserData = Depends(get_current_user),
):
    """
    Send a project invitation.
    """
    try:
        project_id = invitation_data.team_id
        invitee_email = invitation_data.email

        if check_member_exists(project_id, invitee_email):
            raise HTTPException(status_code=409, detail="Member already exists for this project")

        if check_pending_invitation(project_id, invitee_email):
            raise HTTPException(status_code=409, detail="A pending invitation already exists for this email")

        invitation = create_invitation(
            project_id=project_id,
            invited_by=user_data.get_user_id(),
            invited_by_name=user_data.get_user_name(),
            name=invitation_data.name,
            email=invitation_data.email,
            role=invitation_data.role,
            seniority=invitation_data.seniority,
        )
        invitation = _normalize_invitation_payload(invitation)

        return JSONResponse(
            status_code=201,
            content=ResponseModel(
                success=True,
                message="Invitation sent successfully",
                data=invitation,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating invitation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{invitation_token}/accept",
    response_model=ResponseModel,
    response_description="Accept an invitation token and join project",
)
async def accept_invitation_route(
    invitation_token: str = Path(..., description="Plain invitation token"),
    user_data: UserData = Depends(get_current_user),
):
    try:
        accepted = accept_invitation(invitation_token, user_data.get_user_id())
        if not accepted:
            return JSONResponse(
                status_code=404,
                content=ResponseModel(
                    success=False,
                    message="Invitation not found, expired, or already processed",
                    data=None,
                ).dict(),
            )

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="Invitation accepted successfully",
                data=accepted,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error accepting invitation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{invitation_token}/reject",
    response_model=ResponseModel,
    response_description="Reject an invitation token",
)
async def reject_invitation_route(
    invitation_token: str = Path(..., description="Plain invitation token"),
):
    try:
        rejected = reject_invitation(invitation_token)
        if not rejected:
            return JSONResponse(
                status_code=404,
                content=ResponseModel(
                    success=False,
                    message="Invitation not found, expired, or already processed",
                    data=None,
                ).dict(),
            )

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="Invitation rejected successfully",
                data=None,
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting invitation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

