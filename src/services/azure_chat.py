from dataclasses import dataclass
from typing import Optional
import json


@dataclass
class UserData:
    """
    Class to store user authentication data returned from Firebase.
    
    Attributes:
        user_id (str): The Firebase UID of the user
        email (str): The user's email address
        team_id (Optional[str]): The ID of the team the user belongs to, if any
    """
    user_id: str
    email: str
    team_id: Optional[str] = None
    user_name: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert the UserData instance to a dictionary."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "team_id": self.team_id
        }
    
    def to_json(self) -> str:
        """Convert the UserData instance to a JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: dict) -> 'UserData':
        """Create a UserData instance from a dictionary."""
        return cls(
            user_id=data.get("user_id", ""),
            email=data.get("email", ""),
            team_id=data.get("team_id")
        )
    
    def has_team(self) -> bool:
        """Check if the user belongs to a team."""
        return self.team_id is not None
    
    def __str__(self) -> str:
        """String representation of the user data."""
        return f"UserData(user_id='{self.user_id}', email='{self.email}', team_id='{self.team_id}')"
    
    def get_user_id(self) -> str:
        """Get the user ID."""
        return self.user_id
    
    def get_email(self) -> str:
        """Get the user email."""
        return self.email
    
    def get_team_id(self) -> Optional[str]:
        """Get the team ID."""
        return self.team_id
    
    def get_user_name(self) -> Optional[str]:
        """Get the user name."""
        return self.user_name