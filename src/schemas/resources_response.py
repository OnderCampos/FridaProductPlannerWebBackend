from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import datetime


class Project(BaseModel):
    """Project data model"""
    id: str
    name: str
    description: str
    project_key: str
    owner_id: str
    created_at: datetime
    updated_at: datetime


class BaseResponse(BaseModel):
    """Base response model"""
    success: bool
    message: str


class GetProjectsResponse(BaseResponse):
    """Response model for getting all projects"""
    data: Optional[List[Project]] = None


class GetProjectResponse(BaseResponse):
    """Response model for getting a single project"""
    data: Optional[Project] = None


class ProjectResponse(BaseResponse):
    """General response model for project operations"""
    data: Optional[Project] = None


class Template(BaseModel):
    """Template data model"""
    id: str
    project_id: str
    name: str
    language: str
    fields: list


class TemplateResponse(BaseResponse):
    """Response model for template operations"""
    data: Optional[Any] = None


class UserStory(BaseModel):
    """User story data model"""
    id: str
    epic_id: str
    user_id: str
    epic: str
    user_story: str
    description: str
    user_story_id: str
    tshirt_size: Optional[str] = None
    effortHours: float
    createdDate: str
    created_at: datetime
    updated_at: datetime


class UserStoriesResponse(BaseResponse):
    """Response model for user stories operations"""
    data: Optional[List[UserStory]] = None
