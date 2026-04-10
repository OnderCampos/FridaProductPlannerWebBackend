from typing import List, Optional

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    complexity: Optional[str] = "Medium"
    task_type: Optional[str] = "Implementation"
    dependencies: Optional[List[int]] = None
    assignee: Optional[str] = None


class UpdateTaskStatusRequest(BaseModel):
    status: str
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
