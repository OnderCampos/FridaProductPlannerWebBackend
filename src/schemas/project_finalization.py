from typing import Any, Dict, List

from pydantic import BaseModel


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

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)
