from typing import Any, Optional

from pydantic import BaseModel


class ResponseModel(BaseModel):
    """Generic response model for API responses."""

    success: bool
    message: str
    data: Optional[Any] = None

    def dict(self, *args, **kwargs):
        """Compatibility wrapper for Pydantic v1/v2 callers."""
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        return super().dict(*args, **kwargs)

    model_config = {"arbitrary_types_allowed": True}
