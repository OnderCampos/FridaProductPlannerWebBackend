from typing import Any, Dict, List

from pydantic import BaseModel


class EpicUserStoryGenerationStatus(BaseModel):
    epic_id: str
    epic_name: str
    success: bool = True
    skipped: bool = False
    generated_count: int = 0
    existing_count: int = 0
    message: str = ""


class FinalizeProjectCreationData(BaseModel):
    """
    Payload returned by the project creation finalization workflow.

    This is intentionally separate from `ResponseModel`: workflows return domain data,
    while API endpoints wrap it in `ResponseModel`.
    """

    id: str
    name: str
    project_description: str
    technical_stack: List[Any] = []
    roles: List[Any] = []
    project_key: str
    epics: List[Dict[str, Any]] = []
    generated_user_stories_count: int = 0
    user_story_generation: List[EpicUserStoryGenerationStatus] = []

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)
