"""
Schemas for Team Members and Member Invitations
"""
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Literal
from datetime import datetime


# Enums
RoleType = Literal["Frontend", "Backend", "FullStack", "DevOps", "QA", "UX/UI", "PM", "Other"]
SeniorityType = Literal["Junior", "Mid", "Senior", "Lead", "Principal"]
MemberStatusType = Literal["Active", "Inactive"]
InvitationStatusType = Literal["Pending", "Accepted", "Rejected", "Expired"]


class TeamMemberBase(BaseModel):
    """Base model for team member"""
    name: str = Field(..., min_length=2, max_length=100, description="Full name of the team member")
    email: EmailStr = Field(..., description="Email address of the team member")
    role: RoleType = Field(..., description="Role of the team member")
    seniority: SeniorityType = Field(..., description="Seniority level of the team member")


class TeamMemberCreateRequest(TeamMemberBase):
    """Schema for creating a team member via project members endpoints"""
    avatar: Optional[str] = Field(None, description="Optional avatar initials or URL")


class ProjectInvitationRequest(TeamMemberBase):
    """Schema for inviting a member via project invitation endpoints"""
    pass


class TeamMemberCreate(TeamMemberBase):
    """Schema for creating a team member"""
    project_id: str = Field(..., description="Project ID the member belongs to")
    user_id: str = Field(..., description="User ID of the member")
    avatar: Optional[str] = Field(None, description="URL to avatar image")


class TeamMemberUpdate(BaseModel):
    """Schema for updating a team member"""
    role: Optional[RoleType] = Field(None, description="Role of the team member")
    seniority: Optional[SeniorityType] = Field(None, description="Seniority level of the team member")


class TeamMemberResponse(TeamMemberBase):
    """Schema for team member response"""
    id: str = Field(..., description="Unique identifier for the member")
    status: MemberStatusType = Field(..., description="Status of the member")
    joined_date: str = Field(..., description="Date the member joined the project")
    avatar: Optional[str] = Field(None, description="URL to avatar image")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "member-uuid-1",
                "name": "John Doe",
                "email": "john.doe@company.com",
                "role": "Frontend",
                "seniority": "Senior",
                "status": "Active",
                "joined_date": "2024-01-15T10:30:00Z",
                "avatar": "https://example.com/avatars/john.jpg"
            }
        }


class TeamMemberDetailResponse(TeamMemberResponse):
    """Schema for detailed team member response"""
    projects: list = Field(default_factory=list, description="Projects the member is part of")
    assigned_epics: int = Field(0, description="Number of epics assigned to the member")
    assigned_user_stories: int = Field(0, description="Number of user stories assigned to the member")
    completed_user_stories: int = Field(0, description="Number of completed user stories")


class MemberInvitationCreate(TeamMemberBase):
    """Schema for creating a member invitation"""
    user_id: str = Field(..., description="User ID of the inviter")
    team_id: str = Field(..., description="Team ID the invitation is for")


class MemberInvitationResponse(BaseModel):
    """Schema for member invitation response"""
    id: str = Field(..., description="Unique identifier for the invitation")
    project_id: str = Field(..., description="Project ID for the invitation")
    email: EmailStr = Field(..., description="Email address of the invitee")
    name: str = Field(..., description="Name of the invitee")
    role: RoleType = Field(..., description="Role for the invitee")
    seniority: SeniorityType = Field(..., description="Seniority level for the invitee")
    status: InvitationStatusType = Field(..., description="Status of the invitation")
    invited_by: str = Field(..., description="UUID of the user who sent the invitation")
    invited_by_name: Optional[str] = Field(None, description="Name of the user who sent the invitation")
    invited_date: str = Field(..., description="Date the invitation was sent")
    expires_date: str = Field(..., description="Date the invitation expires")
    response_date: Optional[str] = Field(None, description="Date the invitation was responded to")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "invitation-uuid-1",
                "project_id": "project-uuid-1",
                "email": "alice.johnson@company.com",
                "name": "Alice Johnson",
                "role": "FullStack",
                "seniority": "Mid",
                "status": "Pending",
                "invited_by": "current-user-uuid",
                "invited_by_name": "John Doe",
                "invited_date": "2024-11-27T15:30:00Z",
                "expires_date": "2024-12-04T15:30:00Z",
                "response_date": None
            }
        }


class MembersListResponse(BaseModel):
    """Schema for members list response"""
    success: bool = True
    data: list[TeamMemberResponse]
    count: int

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "data": [
                    {
                        "id": "member-uuid-1",
                        "name": "John Doe",
                        "email": "john.doe@company.com",
                        "role": "Frontend",
                        "seniority": "Senior",
                        "status": "Active",
                        "joined_date": "2024-01-15T10:30:00Z",
                        "avatar": "https://example.com/avatars/john.jpg"
                    }
                ],
                "count": 1
            }
        }


class InvitationsListResponse(BaseModel):
    """Schema for invitations list response"""
    success: bool = True
    data: list[MemberInvitationResponse]
    count: int


class InvitationAcceptResponse(BaseModel):
    """Schema for invitation acceptance response"""
    project_id: str = Field(..., description="Project ID")
    project_name: str = Field(..., description="Project name")
    member_id: str = Field(..., description="New member ID")
    role: RoleType = Field(..., description="Member role")
    seniority: SeniorityType = Field(..., description="Member seniority")
