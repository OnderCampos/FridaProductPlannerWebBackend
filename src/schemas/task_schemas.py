from typing import List, Optional

from pydantic import BaseModel, Field
from src.schemas.workflow_status import WorkflowStatus


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    complexity: Optional[str] = "Medium"
    task_type: Optional[str] = "Implementation"
    dependencies: Optional[List[int]] = None
    assignee: Optional[str] = None


class BatchCreateTasksRequest(BaseModel):
    source_text: str = Field(..., min_length=1)


class UpdateTaskStatusRequest(BaseModel):
    status: WorkflowStatus
    completed_date: Optional[str] = None


class UpdateTaskFieldsRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    estimated_hours: Optional[float] = None
    complexity: Optional[str] = None
    task_type: Optional[str] = None
    dependencies: Optional[List[int]] = None
    assignee: Optional[str] = None
    assigneeId: Optional[str] = None
    assignee_email: Optional[str] = None
