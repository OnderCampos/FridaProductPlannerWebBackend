from pydantic import BaseModel, Field
from typing import Any, Optional, Dict


class AuthResponse(BaseModel):
    """
    Schema for authentication endpoint responses.
    """

    success: bool = Field(..., example=True)
    message: str = Field(..., example="User authenticated successfully")
    data: Optional[Any] = Field(None, example="firebase_id_token")
    user: Optional[Dict[str, Any]] = Field(None, example={"email": "user@domain.com"})
    refresh_token: Optional[str] = Field(None, example="firebase_refresh_token")
    expires_in: Optional[str] = Field(None, example="3600")
