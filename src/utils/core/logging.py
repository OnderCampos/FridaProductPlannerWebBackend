from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from datetime import datetime, timezone

from src.schemas.response import ResponseModel

def add_request_log(
    user_id: str, team_id: str, prompt_tokens: int, completion_tokens: int
):
    try:
        total_tokens = prompt_tokens + completion_tokens
        created = datetime.now(timezone.utc).isoformat()

        request_log_ref = FIRESTORE_CLIENT.collection("request_log")
        request_log = request_log_ref.document()

        request_log.set(
            {
                "user_id": user_id,
                "team_id": team_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "created": created,
            }
        )
    except Exception as e:
        print("Error:", e)


def get_all_teams():
    try:
        # Query all documents in the 'teams' collection
        teams_ref = FIRESTORE_CLIENT.collection("teams")
        teams = teams_ref.get()

        # Process and return the teams with their IDs
        all_teams = []
        for team in teams:
            team_data = team.to_dict()
            team_data["id"] = team.id
            all_teams.append(team_data)

        return ResponseModel(
            success=True,
            message="Teams retrieved successfully.",
            data=all_teams,
        )
    except Exception as e:
        return ResponseModel(success=False, message="Error getting teams.", data={})


def get_team_members(user_id: str):
    try:
        # Query the 'user_team' collection to get the team_id for the given user_id
        user_team_ref = FIRESTORE_CLIENT.collection("user_team").where(
            "user_id", "==", user_id
        )
        user_team_docs = user_team_ref.get()

        if not user_team_docs:
            return ResponseModel(success=False, message="User not found.", data={})

        # Assuming each user belongs to only one team
        team_id = user_team_docs[0].to_dict().get("team_id")

        # Query the 'user_team' collection where 'team_id' matches the given team_id
        team_members_ref = FIRESTORE_CLIENT.collection("user_team").where(
            "team_id", "==", team_id
        )
        team_members = team_members_ref.get()

        # Process and return the team members with detailed information
        members = []
        for member in team_members:
            member_data = member.to_dict()
            member_user_id = member_data.get("user_id")

            # Query the 'user_information' collection to get detailed information for each team member
            user_info_ref = FIRESTORE_CLIENT.collection("user_information").document(
                member_user_id
            )
            user_info_doc = user_info_ref.get()

            if user_info_doc.exists:
                user_info_data = user_info_doc.to_dict()
                member_data.update(user_info_data)

            members.append(member_data)

        return ResponseModel(
            success=True, message="Team members retrieved successfully.", data=members
        )
    except Exception as e:
        return ResponseModel(
            success=False, message="Error getting team members.", data={}
        )


def update_user_information(user_id: str, user_info: dict):
    try:
        # Create a reference to the 'user_information' collection
        user_info_ref = FIRESTORE_CLIENT.collection("user_information")

        # Query to check if user information with the same 'user_id' already exists
        existing_user_info = user_info_ref.document(user_id).get()

        if existing_user_info.exists:
            # If the user information exists, update the existing user information
            user_info_ref.document(user_id).update(user_info)
        else:
            # If the user information does not exist, create a new user information with the given user_id
            user_info_ref.document(user_id).set(user_info)

        return ResponseModel(
            success=True,
            message="User information updated successfully.",
            data=user_info,
        )
    except Exception as e:
        return ResponseModel(
            success=False, message="Error updating user information.", data={}
        )


def get_user_information(user_id):
    try:
        user_info_ref = FIRESTORE_CLIENT.collection("user_information").document(
            user_id
        )
        user_info = user_info_ref.get()

        if user_info.exists:
            return ResponseModel(
                success=True,
                message="User information retrieved successfully.",
                data=user_info.to_dict(),
            )
        else:
            return ResponseModel(
                success=False, message="User information not found.", data={}
            )
    except Exception as e:
        return ResponseModel(
            success=False, message="Error getting user information.", data={}
        )

def get_knowledge_base_id_for_user(user_id: str) -> str:
    """
    Retrieves the knowledge_base_id for a user from their configuration.
    Returns the knowledge_base_id or None if not found.
    """
    config_response = get_user_configuration(user_id)
    knowledge_base_id = None
    if config_response and getattr(config_response, 'success', False):
        data = getattr(config_response, 'data', {})
        if isinstance(data, dict):
            knowledge_base_id = data.get('knowledge_base_id')
    return knowledge_base_id


def get_chatbot_id_for_user(user_id: str) -> str:
    """
    Retrieves the chatbot_id for a user from their configuration.
    Returns the chatbot_id or None if not found.
    """
    config_response = get_user_configuration(user_id)
    chatbot_id = None
    if config_response and getattr(config_response, 'success', False):
        data = getattr(config_response, 'data', {})
        if isinstance(data, dict):
            chatbot_id = data.get('chatbot_id')
    return chatbot_id


def update_chatbot_id_for_user(user_id: str, chatbot_id: str) -> bool:
    """
    Updates the chatbot_id for a user in their configuration.
    Returns True if successful, False otherwise.
    """
    try:
        # Get current configuration
        config_response = get_user_configuration(user_id)
        if not config_response or not getattr(config_response, 'success', False):
            # Create a new configuration if it doesn't exist
            config_data = {'chatbot_id': chatbot_id}
            update_response = update_user_configuration(user_id, config_data)
            return getattr(update_response, 'success', False)
        
        # Update existing configuration
        data = getattr(config_response, 'data', {})
        if isinstance(data, dict):
            # Keep existing data and just update chatbot_id
            data['chatbot_id'] = chatbot_id
            update_response = update_user_configuration(user_id, data)
            return getattr(update_response, 'success', False)
        
        return False
    except Exception as e:
        print(f"Error updating chatbot_id: {e}")
        return False
