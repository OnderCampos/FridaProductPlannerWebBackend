import json
import logging
from fastapi import HTTPException
import requests
import random
import string
from firebase_admin import auth
from datetime import datetime, timedelta
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.utils.validation_utils import get_code_block
from src.schemas.response import ResponseModel
from src.prompts.admin_prompts import TRANSFORM_USER_DATA_PROMPT, TRANSFORM_TEXT_TO_USER_JSON_PROMPT
from src.utils.users import upsert_user_profile

# Define registration status constants
REGISTRATION_STATUS = {
    "PENDING": "pending",
    "APPROVED": "approved",
    "REJECTED": "rejected",
}
# Make it immutable
REGISTRATION_STATUS = dict(REGISTRATION_STATUS)  # Python equivalent of Object.freeze



async def update_firebase_user(user_data: dict) -> ResponseModel:
    """
    Updates a Firebase user with the provided data and updates/creates user_information in Firestore.
    """
    try:

        # Update or create user_information document in Firestore
        user_info_ref = FIRESTORE_CLIENT.collection("user_information").document(
            user_data.get("uid")
        )
        user_info_data = {
            "name": user_data.get("name"),
            "role": user_data.get("role"),
            "seniority": user_data.get("seniority"),
        }
        # Remove None values
        user_info_data = {k: v for k, v in user_info_data.items() if v is not None}
        if user_info_data:
            user_info_ref.set(user_info_data, merge=True)

        upsert_user_profile(
            user_id=user_data.get("uid"),
            email=user_data.get("email"),
            name=user_data.get("name"),
            role=user_data.get("role"),
        )

        return ResponseModel(
            success=True, message="User updated successfully", data=user_data.get("uid")
        )
    except Exception as e:
        logging.error(f"Failed to update user {user_data.uid}. Error: {str(e)}")
        return ResponseModel(
            success=False, message="Failed to update user", data=str(e)
        )


async def create_firebase_user(user_data) -> ResponseModel:
    """
    Creates a single Firebase user and related Firestore documents.

    Args:
        user_data: An object or dict containing 'email', 'password', 'team_id', 'name', 'role', 'seniority'.

    Returns:
        ResponseModel: Result of the user creation attempt.
    """
    try:
        email = user_data.email
        password = user_data.password
        team_id = user_data.team_id
        print("Hello", email)
        user = auth.create_user(email=email, password=password)

        # Add a document to the 'user_team' collection
        user_team_ref = FIRESTORE_CLIENT.collection("user_team").document()
        user_team_ref.set({"user_id": user.uid, "team_id": team_id})

        # Add a document to the 'configuration' collection
        configuration_ref = FIRESTORE_CLIENT.collection("configuration").document()
        configuration_ref.set(
            {
                "acceptance_criteria": True,
                "gherkin": True,
                "jira_domain": "",
                "jira_domains": "",
                "language": "English",
                "out_of_scope": True,
                "test_cases": True,
                "user_id": user.uid,
                "selected_template_id": "0",
                "use_team_members": False,
            }
        )

        # Add a document to the 'user_information' collection
        user_info_ref = FIRESTORE_CLIENT.collection("user_information").document(
            user.uid
        )
        user_info_ref.set(
            {
                "name": user_data["name"] if "name" in user_data else "",
                "role": user_data["role"] if "role" in user_data else "",
                "seniority": user_data["seniority"] if "seniority" in user_data else "",
            }
        )

        upsert_user_profile(
            user_id=user.uid,
            email=email,
            name=user_data["name"] if "name" in user_data else "",
            role=user_data["role"] if "role" in user_data else None,
        )

        print(f"Created new user: {email}")
        return ResponseModel(
            success=True,
            message="User created successfully",
            data={"email": email, "status": "success"},
        )
    except Exception as e:
        print(
            f'Failed to create new user: {getattr(user_data, "email", None)} - {str(e)}'
        )
        return ResponseModel(
            success=False,
            message="Failed to create user",
            data={
                "email": getattr(user_data, "email", None),
                "error": str(e),
                "status": "failure",
            },
        )


async def create_firebase_users(users: list) -> list:
    """
    Creates multiple Firebase users.

    Args:
        users (list): A list of objects or dicts, each containing 'email', 'password', 'team_id', 'name', 'role', 'seniority'.

    Returns:
        list: A list of results for each user creation attempt.
    """
    results = []
    print("Creating Firebase users...",)
    for user_data in users:
        result = await create_firebase_user(user_data)
        # If any user creation fails, return immediately with failure and partial results
        if not result.success:
            results.append(result.data)
            return ResponseModel(
                success=False, message="Failed to create users", data=results
            )
        results.append(result.data)

    return ResponseModel(
        success=True, message="Users created successfully", data=results
    )


def get_usage_by_all_teams():
    # Get the current date and the date three months ago

    try:
        now = datetime.utcnow()
        three_months_ago = now - timedelta(days=90)

        # Query all documents in the 'request_log' collection
        request_log_ref = FIRESTORE_CLIENT.collection("request_log")
        request_logs = request_log_ref.where(
            "created", ">=", three_months_ago.isoformat()
        ).get()

        # Dictionary to store usage statistics by month and team
        monthly_usage = {}

        if request_logs:
            for request_log in request_logs:
                request_data = request_log.to_dict()
                team_id = request_data["team_id"]
                created = request_data["created"]
                # Parse the created date
                created_date = datetime.fromisoformat(created.replace("Z", "+00:00"))

                # Determine the month and year
                month_year = created_date.strftime("%Y-%m")

                if month_year not in monthly_usage:
                    monthly_usage[month_year] = {}

                if team_id not in monthly_usage[month_year]:
                    # Query the team name from the 'teams' collection
                    team_ref = FIRESTORE_CLIENT.collection("teams").document(team_id)
                    team_doc = team_ref.get()
                    team_name = team_doc.to_dict().get("name", "Unknown Team")

                    monthly_usage[month_year][team_id] = {
                        "team_name": team_name,
                        "total_prompt_tokens": 0,
                        "total_completion_tokens": 0,
                    }

                monthly_usage[month_year][team_id][
                    "total_prompt_tokens"
                ] += request_data.get("prompt_tokens", 0)
                monthly_usage[month_year][team_id][
                    "total_completion_tokens"
                ] += request_data.get("completion_tokens", 0)

        return ResponseModel(
            success=True,
            message="Usage statistics retrieved successfully",
            data=monthly_usage,
        )
    except Exception as e:
        return ResponseModel(
            success=False, message="Failed to retrieve usage statistics", data=str(e)
        )


async def reject_preregistration(registration_id: str) -> ResponseModel:
    """
    Rejects a pre-registration request.
    
    Args:
        registration_id (str): The ID of the pre-registration document.
        rejection_reason (str, optional): The reason for rejection.
        
    Returns:
        ResponseModel: A response model containing success status, message, and data.
    """
    try:
        # Get the pre-registration document
        preregistration_ref = FIRESTORE_CLIENT.collection("preregistration").document(registration_id)
        preregistration_doc = preregistration_ref.get()
        
        if not preregistration_doc.exists:
            return ResponseModel(
                success=False,
                message=f"Pre-registration with ID {registration_id} not found",
                data={"status": "not_found"}
            )
        
        preregistration_data = preregistration_doc.to_dict()
        
        # Check if the registration is already approved or rejected
        current_status = preregistration_data.get("status")
        if current_status == "approved":
            return ResponseModel(
                success=False,
                message="Cannot reject a pre-registration that has already been approved",
                data={"status": "already_approved"}
            )
        elif current_status == "rejected":
            return ResponseModel(
                success=False,
                message="This pre-registration has already been rejected",
                data={"status": "already_rejected"}
            )
        
        # Update the pre-registration status to rejected
        update_data = {
            "status": "rejected",
            "updated_at": datetime.now()
        }
        
            
        preregistration_ref.update(update_data)
        
        return ResponseModel(
            success=True,
            message="Pre-registration rejected successfully",
            data={
                "registration_id": registration_id,
                "status": "rejected",
                "email": preregistration_data.get("email")
            }
        )
    except Exception as e:
        logging.error(f"Error rejecting pre-registration {registration_id}: {str(e)}")
        return ResponseModel(
            success=False,
            message="Failed to reject pre-registration",
            data={"error": str(e)}
        )

async def get_all_preregistrations(status: str = None, limit: int = 100) -> ResponseModel:
    """
    Retrieves pre-registration entries from Firestore, optionally filtered by status.
    
    Args:
        status (str, optional): Filter registrations by status (pending, approved, rejected).
                               If not provided, returns all registrations.
        limit (int, optional): Maximum number of results to return. Default is 100.
    
    Returns:
        ResponseModel: A response model containing the list of pre-registrations.
    """
    try:
        # Reference to preregistration collection
        query = FIRESTORE_CLIENT.collection("preregistration")
        
        # Validate status if provided
        if status:
            if status not in REGISTRATION_STATUS.values():
                return ResponseModel(
                    success=False,
                    message=f"Invalid status. Must be one of: {', '.join(REGISTRATION_STATUS.values())}"
                )
            query = query.where("status", "==", status)
            
        # Apply limit to query
        query = query.limit(limit)
        
        # Execute query
        docs = query.get()
        
        # Format the results
        pre_registrations = []
        for doc in docs:
            # Get the document data
            data = doc.to_dict()
            
            # Format the timestamps to ISO format strings
            created_at = data.get("created_at")
            if created_at and isinstance(created_at, datetime):
                data["created_at"] = created_at.isoformat()
            
            updated_at = data.get("updated_at")
            if updated_at and isinstance(updated_at, datetime):
                data["updated_at"] = updated_at.isoformat()
            
            # Add the document ID
            data["id"] = doc.id
            
            pre_registrations.append(data)
        
        return ResponseModel(
            success=True,
            message=f"Retrieved {len(pre_registrations)} pre-registration entries",
            data={"pre_registrations": pre_registrations}
        )
    except Exception as e:
        logging.error(f"Failed to retrieve pre-registrations. Error: {str(e)}")
        return ResponseModel(
            success=False, 
            message="Failed to retrieve pre-registrations", 
            data=str(e)
        )
    
async def transform_user_data(user_data: dict) -> dict:
    """
    Transforms a user dictionary by renaming 'uid' to 'user_id' and removing sensitive fields.

    Args:
        user (dict): The original user dictionary.
    Returns:
        dict: The transformed user dictionary.
    """
    try:
        # Initialize Azure Chat Service
        azure_chat_service = AzureChatService(LLMOPS_API_KEY, user_data, None)
        
        # Format the prompt with user data
        formatted_prompt = TRANSFORM_USER_DATA_PROMPT.format(
            user_data=json.dumps(user_data)
        )
        
        # Call the Azure Chat Service with the prompt
        response = await azure_chat_service.simple_completion(
            prompt=formatted_prompt
        )
        transformed_data = json.loads(response)
    except Exception as e:
        logging.error(f"Failed to initialize AzureChatService. Error: {str(e)}")
        return {}
        
async def transform_user_data_with_llm(user_data: dict, team_id: str, text_data: str) -> ResponseModel:
    """
    Transforms raw text data containing user information into structured JSON format
    using Azure OpenAI services.
    
    Args:
        team_id (str): The team ID to associate with the transformed users.
        text_data (str): Raw text containing user information.
        
    Returns:
        ResponseModel: A response model containing the structured user data.
    """
    try:
        print("Transforming user data with LLM...")
        # Initialize ChatSession
        chat_session = AzureChatService(
            LLMOPS_API_KEY,
            user_data,
            None
        )
        print("Chat session initialized, team_id:", team_id, "and text_data:", text_data)
        # Format the prompt with team_id and text_data
        formatted_prompt = TRANSFORM_TEXT_TO_USER_JSON_PROMPT.format(
            team_id=team_id,
            text_data=text_data
        )
        print("Formatted Prompt:", formatted_prompt)
        
        # Get completion from Azure OpenAI
        result = await chat_session.simple_completion(
            prompt=formatted_prompt
        )

        print("LLM Result:", result)
        
        # Extract JSON from response
        try:
            # Try to parse the result directly
            json_data = json.loads(result)
        except json.JSONDecodeError:
            # If direct parsing fails, try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                json_data = json.loads(json_match.group(1))
            else:
                return ResponseModel(
                    success=False,
                    message="Failed to parse JSON from LLM response",
                    data=result
                )
        
        # Validate the response structure
        if not json_data.get("users"):
            return ResponseModel(
                success=False,
                message="Invalid response format: 'users' array not found",
                data=json_data
            )
            
        return ResponseModel(
            success=True,
            message=f"Successfully transformed user data for team {team_id}",
            data=json_data
        )
        
    except Exception as e:
        logging.error(f"Error transforming user data: {str(e)}")
        return ResponseModel(
            success=False,
            message=f"Failed to transform user data: {str(e)}",
            data=None
        )

