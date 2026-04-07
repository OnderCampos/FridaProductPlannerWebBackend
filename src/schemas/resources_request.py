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


class CreateProjectFromDescriptionRequest(BaseModel):
    """Request model for creating a project from a description only."""
    description: str
    name: Optional[str] = None
    project_key: Optional[str] = None


class StartProjectClarificationRequest(BaseModel):
    """Request model for starting a clarification flow on an existing project."""
    description: Optional[str] = None


class GenerateProjectSpecFromFigmaRequest(BaseModel):
    """Request model for generating a spec from a Figma payload for an existing project."""
    figma_url: str
    figma_notes: Optional[str] = None
    description: Optional[str] = None


class AcceptProjectSpecificationRequest(BaseModel):
    """Request model for accepting a generated project specification."""
    spec_text: Optional[str] = None


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


class CreateUserStoryManualRequest(BaseModel):
    """Request model for creating a single user story manually."""
    epic_id: str
    user_story: str
    description: str
    user_story_id: Optional[str] = None
    order: Optional[int] = None
    dependencies: Optional[List[str]] = None
    effortHours: Optional[float] = None
    story_points: Optional[int] = None


class StartUserStoryQaRequest(BaseModel):
    """Request model for starting a Q&A flow to create a single user story."""
    epic_id: str
    goal: Optional[str] = None


class UserStoryQaAnswersRequest(BaseModel):
    """Submit answers for a user story Q&A draft."""
    draft_id: str
    answers: List[ProjectClarificationAnswer]


class AcceptUserStoryQaRequest(BaseModel):
    """Accept a user story draft and persist it."""
    draft_id: str


class StartUserStoryDocumentRequest(BaseModel):
    """Start a document generation flow for a user story."""
    story_id: str


class DocumentClarificationAnswer(BaseModel):
    """Answer payload for a specific document section/question."""
    key: str
    question: str
    answer: str


class UserStoryDocumentAnswersRequest(BaseModel):
    """Submit answers for a user story document draft."""
    draft_id: str
    answers: List[DocumentClarificationAnswer]


class ExpandUserStoriesRequest(BaseModel):
    """Request model for expanding user stories for an epic (agentic AI)."""
    epic_id: str
    instruction: Optional[str] = None
    max_new_stories: Optional[int] = None


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
