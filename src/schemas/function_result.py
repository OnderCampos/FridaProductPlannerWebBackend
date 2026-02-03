"""
Function response schema for consistent API responses across all utility functions.
"""
from dataclasses import dataclass
from typing import Any, Optional, Dict, List
import json


@dataclass
class FunctionResult:
    """
    Standardized response class for all utility functions.
    
    Provides consistent schema with success/error handling, data payload,
    and optional metadata for debugging and monitoring.
    """
    success: bool
    message: str
    data: Optional[Any] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {
            "success": self.success,
            "message": self.message,
        }
        
        if self.data is not None:
            result["data"] = self.data
            
        if self.error is not None:
            result["error"] = self.error
            
        if self.metadata is not None:
            result["metadata"] = self.metadata
            
        return result
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def success_result(cls, message: str, data: Any = None, metadata: Optional[Dict[str, Any]] = None) -> "FunctionResult":
        """Create a successful result"""
        return cls(success=True, message=message, data=data, metadata=metadata)
    
    @classmethod
    def error_result(cls, message: str, error: str, metadata: Optional[Dict[str, Any]] = None) -> "FunctionResult":
        """Create an error result"""
        return cls(success=False, message=message, error=error, metadata=metadata)
    
    def is_success(self) -> bool:
        """Check if the result is successful"""
        return self.success
    
    def is_error(self) -> bool:
        """Check if the result is an error"""
        return not self.success
    
    def get_data(self) -> Any:
        """Get the data payload"""
        return self.data
    
    def get_error(self) -> Optional[str]:
        """Get the error message"""
        return self.error