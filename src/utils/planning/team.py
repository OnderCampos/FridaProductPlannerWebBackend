from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from datetime import datetime, timezone

from src.schemas.response import ResponseModel

def get_team_name(team_id: str) -> str:
    try:
        team_doc = FIRESTORE_CLIENT.collection("teams").document(team_id).get()
        if team_doc.exists:
            team_data = team_doc.to_dict()
            return team_data.get("name", "")
        else:
            return ""
    except Exception as e:
        print(f"Error fetching team name: {e}")
        return ""
