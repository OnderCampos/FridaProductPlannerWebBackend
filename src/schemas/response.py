from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ResponseModel:
    """Generic response model for API responses"""
    success: bool
    message: str
    data: Optional[Any] = None

    def dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data
        }