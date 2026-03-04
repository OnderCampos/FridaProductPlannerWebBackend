import json
import logging
from typing import Optional
from fastapi import HTTPException
import requests

from firebase_admin import auth

from src.services.setup.firebase_setup import FIREBASE, FIREBASE_KEY, FIRESTORE_CLIENT
from src.services.setup.variables_setup import FRONTEND_VERSION, LLMOPS_API_KEY
from src.utils.admin_utils import create_firebase_user

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.permissions import get_global_user_role
from src.utils.users import upsert_user_profile


def build_login_user_payload(email: str) -> dict:
    uid = None
    display_name = None
    try:
        user = auth.get_user_by_email(email)
        uid = user.uid
        display_name = user.display_name
    except Exception as e:
        logging.error(f"Error retrieving Firebase user for {email}: {e}")

    user_data = UserData(user_id=uid or "", email=email, team_id=None)
    role_info = get_global_user_role(user_data)

    name = display_name or role_info.get("member_name") or email

    upsert_user_profile(
        user_id=uid,
        email=email,
        name=name,
        role=role_info.get("role"),
        member_id=role_info.get("member_id"),
    )

    return {
        "id": uid,
        "email": email,
        "name": name,
        "role": role_info.get("role"),
        "is_team_lead": role_info.get("is_team_lead", False),
        "member_id": role_info.get("member_id"),
    }


def firebase_authenticate(email: str, password: str) -> ResponseModel:
    """
    Ensures a user exists in Firebase and authenticates them.
    If the user does not exist, creates the user first.

    Args:
        email (str): User email.
        password (str): User password.

    Returns:
        ResponseModel: Success and idToken if authenticated, error otherwise.
    """
    try:
        # Authenticate with Firebase
        url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
        payload = {"email": email, "password": password, "returnSecureToken": True}
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            url, params={"key": FIREBASE_KEY}, data=json.dumps(payload), headers=headers
        )
        response_data = response.json()
        print(f"[DEBUG] Firebase auth response status: {response.status_code}, body: {response_data}")

        if response.status_code == 200 and "idToken" in response_data:
            logging.info(f"User {email} authenticated successfully.")
            return ResponseModel(success=True, message="User authenticated successfully", data=response_data.get("idToken"))
        else:
            logging.error(f"Failed to authenticate user {email}. Error: {response_data.get('error')}")
            return ResponseModel(success=False, message="Failed to authenticate user", data=response_data.get("error"))
    except Exception as e:
        logging.error(f"Exception during Firebase authentication for {email}: {e}")
        return ResponseModel(success=False, message="Exception during Firebase authentication", data=str(e))

async def authenticate_external_user(email: str, password: str, version: str) -> ResponseModel:
    """
    Authenticates a user:
    - Checks external endpoint for user existence.
    - If user exists externally but not in Firebase, creates the user in Firebase.
    - Authenticates with Firebase and returns the idToken.
    """
    print(f"Version: {version}")

    if version != FRONTEND_VERSION:
        print(f"Invalid version: {version}")
        return ResponseModel(success=False, message="invalid_version", data=None)

    try:
        # Step 1: Check if the user exists in the external endpoint
        print(f"[DEBUG] Checking external endpoint for account existence for {email}")
        external_url = "https://frida-extension-backend.azurewebsites.net/external/authenticate"
        external_payload = {"email": email, "password": password}
        external_headers = {"Content-Type": "application/json", "API-Key": LLMOPS_API_KEY}
        ext_response = requests.post(external_url, data=json.dumps(external_payload), headers=external_headers)
        print(f"[DEBUG] External endpoint response status: {ext_response.status_code}, body: {ext_response.text}")

        external_user_exists = False
        if ext_response.status_code == 200:
            external_user_exists = True

        if ext_response.status_code == 401:
            print(f"[DEBUG] Unauthorized user for external authentication: {email}")
            raise HTTPException(status_code=401, detail="Unauthorized user")
        # Step 2: Check if the user exists in Firebase
        firebase_user_exists = False
        try:
            print(f"[DEBUG] Checking Firebase for user {email}")
            user_record = auth.get_user_by_email(email)
            firebase_user_exists = True
        except auth.UserNotFoundError:
            firebase_user_exists = False
            print(f"[DEBUG] User {email} does not exist in Firebase.")

        # Step 3: If user exists in external but not in Firebase, create in Firebase
        if external_user_exists and not firebase_user_exists:
            print(f"[DEBUG] External user exists but not in Firebase. Creating Firebase user.")
            try:
                await create_firebase_user({"email": email, "password": password, "team_id": "WOGkU8UvSFcUjE0GxCBt", "name": None, "role": None, "seniority": None})
                firebase_user_exists = True
                print(f"[DEBUG] Successfully created Firebase user for {email}")
            except Exception as create_error:
                logging.error(f"Failed to create Firebase user for {email}. Error: {create_error}")
                print(f"[DEBUG] Failed to create Firebase user for {email}. Error: {create_error}")
                return ResponseModel(success=False, message="Failed to create Firebase user", data=str(create_error))
        elif not external_user_exists and not firebase_user_exists:
            print(f"[DEBUG] User does not exist in either external or Firebase.")
            return ResponseModel(success=False, message="User does not exist", data=None)
        # else: (external_user_exists and firebase_user_exists) or (not external_user_exists and firebase_user_exists)
        # Both cases: just authenticate with Firebase

        # Step 4: Authenticate with Firebase if user exists in Firebase
        if firebase_user_exists:
            return firebase_authenticate(email, password)

    except Exception as error:
        logging.error(f"Failed to authenticate user {email}. Error: {error}")
        print(f"[DEBUG] Exception in authenticate_user for {email}: {error}")
        return ResponseModel(success=False, message="Failed to authenticate user", data=str(error))

def authenticate_user_firebase(email: str, password: str, version: str) -> str:
    print(f"Version: {version}")
    print(f"Frontend Version: {FRONTEND_VERSION}")
    if version != FRONTEND_VERSION:
        print(f"Invalid version: {version}")
        raise HTTPException(status_code=400, detail="invalid_version")

    try:
        return firebase_authenticate(email, password)

    except Exception as error:
        logging.error(f"Failed to authenticate user {email}. Error: {error}")
        print(f"[DEBUG] Exception in authenticate_user_firebase for {email}: {error}")
        return ResponseModel(success=False, message="Failed to authenticate user", data=str(error))
    
def validate_user(id_token: str) -> dict:
    """
    Verifies the Firebase idToken and retrieves the user's info.

    Args:
        id_token (str): The Firebase ID token.

    Returns:
        dict: {"success": True, "data": {...}} if valid, {"success": False, "message": "..."} if not.
    """
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token.get("uid")
        print(f"[DEBUG] Token valid. UID: {uid}")

        user = auth.get_user(uid)
        if user:
            user_details = {"user_id": user.uid, "email": user.email}
            return True
        else:
            print(f"[DEBUG] No user found for UID: {uid}")
            raise HTTPException(status_code=401, detail="Unauthorized user")
    except Exception as e:
        logging.error(f"Error verifying token or retrieving user: {e}")
        raise HTTPException(status_code=401, detail="Unauthorized user")

def validate_user_and_get_data(id_token: str) -> UserData:
    """
    Validates the Firebase idToken and retrieves the user's team information.

    Args:
        id_token (str): The Firebase ID token.

    Returns:
        UserData: A UserData instance containing user details and team information if authenticated, or raises an HTTPException if not.
    """
    try:
        # Verify the idToken
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token.get("uid")
        print(f"[DEBUG] Token valid. UID: {uid}")

        user = auth.get_user(uid)
        if user:
            # Query the 'user_team' collection where 'user_id' matches the given uid
            team_ref = FIRESTORE_CLIENT.collection("user_team").where("user_id", "==", user.uid)
            teams = team_ref.get()
            print(f"[DEBUG] Retrieved teams: {teams}")
            
            team_id = None
            if teams:
                team = teams[0].to_dict()
                team_id = team.get("team_id")
            
            user_data = UserData(
                user_id=user.uid,
                email=user.email,
                team_id=team_id
            )
            print(f"[DEBUG] Created user data: {user_data}")

            return user_data
        else:
            print(f"[DEBUG] No user found for UID: {uid}")
            raise HTTPException(status_code=401, detail="Unauthorized user")
    except Exception as e:
        logging.error(f"Error verifying token or retrieving user details/team: {e}")
        print(f"[DEBUG] Exception in validate_user_and_get_data for {id_token}: {e}")
        raise HTTPException(status_code=401, detail="Unauthorized user")
