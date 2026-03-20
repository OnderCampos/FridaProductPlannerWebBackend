from typing import Any, Dict, List, Optional

from pydantic import BaseModel

try:
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover
    ConfigDict = None  # type: ignore[assignment]


class ProjectCreationProjectData(BaseModel):
    """Minimal project payload returned during creation flows."""

    id: str
    name: str
    description: str
    project_key: str

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)


class ProjectCreationClarificationData(BaseModel):
    """
    Clarification/spec state returned during project creation flows.

    The underlying creation sources (QA, file extraction, Figma, etc.) may populate different
    optional fields. Extra keys are allowed to avoid breaking callers when new fields are added.
    """

    status: str
    questions: List[str] = []
    loop_count: int = 0

    spec_text: Optional[str] = None
    spec_url: Optional[str] = None
    spec_generated_at: Optional[str] = None

    extracted_project_description: Optional[str] = None
    extracted_roles: Optional[List[Any]] = None
    extracted_technical_stack: Optional[List[Any]] = None
    extracted_epics: Optional[List[Dict[str, Any]]] = None
    extracted_user_stories: Optional[List[Dict[str, Any]]] = None

    # Allow extra keys for forward compatibility across creation sources.
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    else:
        class Config:
            extra = "allow"

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)


class ProjectCreationInitializationData(BaseModel):
    """Return type for project creation initialization workflows."""

    project: ProjectCreationProjectData
    clarification: Optional[ProjectCreationClarificationData] = None

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)


class ProjectRecordCreationData(BaseModel):
    """Internal payload returned after inserting the base project document."""

    project_id: str
    project: ProjectCreationProjectData

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)
