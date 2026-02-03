from pydantic import BaseModel, Field


class AuthResponse(BaseModel):
    """
    Schema for the response body of the authentication endpoint.

    Args:
        uid (str): The unique id of the user.
    """

    uid: str = Field(..., example="uid")
