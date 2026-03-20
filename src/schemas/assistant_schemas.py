from typing import Any, Dict, List, Literal
from pydantic import BaseModel, Field


class AssistantHistoryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1, description="Message content")


class AssistantChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Latest user message")
    project_id: str = Field(
        ...,
        min_length=1,
        description="Required project scope. Assistant chat is locked to this single project.",
    )
    history: List[AssistantHistoryMessage] = Field(
        default_factory=list,
        description="Prior chat history (oldest to newest)",
    )


class AssistantPendingAction(BaseModel):
    action_id: str = Field(..., description="Client-visible action identifier")
    action_type: str = Field(..., description="Action type key for backend execution")
    title: str = Field(..., description="Short action title shown in chat")
    summary: str = Field(..., description="Human-readable action summary")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Action payload")
    requires_confirmation: bool = Field(
        default=True,
        description="Whether user confirmation is required before execution",
    )


class AssistantExecuteActionRequest(BaseModel):
    action: AssistantPendingAction
