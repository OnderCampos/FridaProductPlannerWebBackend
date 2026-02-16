from pydantic import BaseModel
from typing import List, Optional


class CreateSprintRequest(BaseModel):
    name: str
    lengthDays: int
    startDate: Optional[str] = None
    endDate: Optional[str] = None


class UpdateSprintRequest(BaseModel):
    name: Optional[str] = None
    lengthDays: Optional[int] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None


class SprintItemAssignmentRequest(BaseModel):
    type: str
    id: str
    include_subtasks: Optional[bool] = False


class SprintItemsBulkRequest(BaseModel):
    assign: Optional[List[SprintItemAssignmentRequest]] = None
    unassign: Optional[List[SprintItemAssignmentRequest]] = None


class SprintOrderRequest(BaseModel):
    order: List[str]


class SprintItemsOrderRequest(BaseModel):
    order: List[str]
