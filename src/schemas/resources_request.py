from pydantic import BaseModel
from typing import Optional, List


class GetProjectsRequest(BaseModel):
    """Request model for getting all projects - no body needed since auth is in header"""
    pass


class GetProjectRequest(BaseModel):
    """Request model for getting a single project - no body needed since auth is in header"""
    pass


class CreateProjectRequest(BaseModel):
    """Request model for creating a project"""
    name: str
    description: str
    project_key: str


class UpdateProjectRequest(BaseModel):
    """Request model for updating a project"""
    project_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    project_key: Optional[str] = None


class DeleteProjectRequest(BaseModel):
    """Request model for deleting a project"""
    project_id: str


class GenerateAnalysisRequest(BaseModel):
    """Request model for generating main functionalities analysis (Step 1)"""
    epic_id: str
    project_description: Optional[str] = None 
    epic_description: Optional[str] = None

class GenerateUserStoriesRequest(BaseModel):
    """Request model for generating user stories (Step 2)"""
    epic_id: str
    project_description: Optional[str] = None 
    epic_description: Optional[str] = None
    functionality: Optional[str] = None
    functionalities: Optional[list] = None


class CreateTemplateRequest(BaseModel):
    """Request model for creating a template"""
    name: str
    language: str
    fields: list


class UpdateTemplateRequest(BaseModel):
    """Request model for updating a template"""
    name: Optional[str] = None
    language: Optional[str] = None
    fields: Optional[list] = None


class GetUserStoriesByEpicRequest(BaseModel):
    """Request model for getting user stories by epic - no body needed since epic_id is in path"""
    pass


class UpdateUserStoryAssigneeRequest(BaseModel):
    """Request model for assigning or reassigning a user story"""
    assigneeId: Optional[str] = None
    assignee: Optional[str] = None


class UserStoryDependencyItem(BaseModel):
    """User story item for dependency generation"""
    id: Optional[str] = None
    user_story_id: Optional[str] = None
    user_story: Optional[str] = None
    description: Optional[str] = None


class GenerateUserStoryDependenciesRequest(BaseModel):
    """Request model for generating dependencies between user stories"""
    epic_id: str
    user_stories: List[UserStoryDependencyItem]
