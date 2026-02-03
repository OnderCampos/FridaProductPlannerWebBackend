from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    """
    Schema for the request body of the authentication endpoint.

    Args:
        email (str): The email of the user to authenticate.
    """

    email: str = Field(..., example="testing_purposes_email@domain.com")
    password: str = Field(..., example="testing_purposes_password")
    version: str = Field(..., example="1.0.0")
