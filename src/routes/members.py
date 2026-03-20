"""
Routes for team member management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
import logging

from src.schemas.member_schemas import (
    TeamMemberResponse,
    TeamMemberDetailResponse,
    TeamMemberUpdate,
    MembersListResponse
)
from src.schemas.response import ResponseModel
from src.utils.planning.members import (
    get_project_members,
    get_member_by_id,
    get_member_details,
    update_team_member,
    remove_team_member
)


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{member_id}", response_model=ResponseModel)
async def get_member(
    member_id: str,
):
    """
    Get detailed information about a specific team member.
    """
    try:
        # Get member details
        member_response = get_member_details(member_id)
    
        return ResponseModel(
            success=True,
            message="",
            data=member_response
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting member details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
