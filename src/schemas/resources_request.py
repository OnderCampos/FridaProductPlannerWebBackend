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


class CreateProjectFromFigmaRequest(BaseModel):
    """Request model for creating a project from a Figma link"""
    name: str
    project_key: str
    description: Optional[str] = None
    figma_url: str
    figma_notes: Optional[str] = None


class UpdateProjectRequest(BaseModel):
    """Request model for updating a project"""
    #project_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    project_key: Optional[str] = None
    tech_stack: Optional[List[str]] = None


class DeleteProjectRequest(BaseModel):
    """Request model for deleting a project"""
    project_id: str


class GenerateAnalysisRequest(BaseModel):
    """Request model for generating main functionalities analysis (Step 1)"""
    epic_id: str


class GenerateUserStoriesRequest(BaseModel):
    """Request model for generating user stories (Step 2)"""
    epic_id: str
    functionality: Optional[str] = None
    functionalities: Optional[list] = None


class ProjectClarificationAnswer(BaseModel):
    question: str
    answer: str


class ProjectClarificationRequest(BaseModel):
    answers: List[ProjectClarificationAnswer]


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


class EpicCreateRequest(BaseModel):
    """Request model for creating an epic"""
    name: str
    description: str
    labels: Optional[list] = None
    roles: Optional[List[str]] = None
    technologies: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    storyPoints: Optional[float] = None


class EpicUpdateRequest(BaseModel):
    """Request model for updating an epic"""
    name: Optional[str] = None
    description: Optional[str] = None
    labels: Optional[list] = None
    roles: Optional[List[str]] = None
    technologies: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    storyPoints: Optional[float] = None


class BacklogStatusUpdateRequest(BaseModel):
    """Request model for updating backlog item status"""
    item_type: str
    item_id: str
    status: str
