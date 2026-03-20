import json
import logging
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import requests

from firebase_admin import auth

from src.services.setup.firebase_setup import FIREBASE, FIREBASE_KEY, FIRESTORE_CLIENT
from src.services.setup.variables_setup import FRONTEND_VERSION, LLMOPS_API_KEY
from src.utils.authz.admin_utils import create_firebase_user

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.utils.authz.permissions import get_global_user_role
from src.utils.authz.users import upsert_user_profile


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
        logging.getLogger(__name__).debug(
            "Firebase auth response status: %s, body: %s",
            response.status_code,
            response_data,
        )

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
    logging.getLogger(__name__).debug("Version: %s", version)

    if version != FRONTEND_VERSION:
        logging.getLogger(__name__).debug("Invalid version: %s", version)
        return ResponseModel(success=False, message="invalid_version", data=None)

    try:
        # Step 1: Check if the user exists in the external endpoint
        logging.getLogger(__name__).debug(
            "Checking external endpoint for account existence for %s", email
        )
        external_url = "https://frida-extension-backend.azurewebsites.net/external/authenticate"
        external_payload = {"email": email, "password": password}
        external_headers = {"Content-Type": "application/json", "API-Key": LLMOPS_API_KEY}
        ext_response = requests.post(external_url, data=json.dumps(external_payload), headers=external_headers)
        logging.getLogger(__name__).debug(
            "External endpoint response status: %s, body: %s",
            ext_response.status_code,
            ext_response.text,
        )

        external_user_exists = False
        if ext_response.status_code == 200:
            external_user_exists = True

        if ext_response.status_code == 401:
            logging.getLogger(__name__).debug(
                "Unauthorized user for external authentication: %s", email
            )
            raise HTTPException(status_code=401, detail="Unauthorized user")
        # Step 2: Check if the user exists in Firebase
        firebase_user_exists = False
        try:
            logging.getLogger(__name__).debug("Checking Firebase for user %s", email)
            user_record = auth.get_user_by_email(email)
            firebase_user_exists = True
        except auth.UserNotFoundError:
            firebase_user_exists = False
            logging.getLogger(__name__).debug(
                "User %s does not exist in Firebase.", email
            )

        # Step 3: If user exists in external but not in Firebase, create in Firebase
        if external_user_exists and not firebase_user_exists:
            logging.getLogger(__name__).debug(
                "External user exists but not in Firebase. Creating Firebase user."
            )
            try:
                await create_firebase_user({"email": email, "password": password, "team_id": "WOGkU8UvSFcUjE0GxCBt", "name": None, "role": None, "seniority": None})
                firebase_user_exists = True
                logging.getLogger(__name__).debug(
                    "Successfully created Firebase user for %s", email
                )
            except Exception as create_error:
                logging.error(f"Failed to create Firebase user for {email}. Error: {create_error}")
                logging.getLogger(__name__).debug(
                    "Failed to create Firebase user for %s. Error: %s",
                    email,
                    create_error,
                )
                return ResponseModel(success=False, message="Failed to create Firebase user", data=str(create_error))
        elif not external_user_exists and not firebase_user_exists:
            logging.getLogger(__name__).debug(
                "User does not exist in either external or Firebase."
            )
            return ResponseModel(success=False, message="User does not exist", data=None)
        # else: (external_user_exists and firebase_user_exists) or (not external_user_exists and firebase_user_exists)
        # Both cases: just authenticate with Firebase

        # Step 4: Authenticate with Firebase if user exists in Firebase
        if firebase_user_exists:
            return firebase_authenticate(email, password)

    except Exception as error:
        logging.error(f"Failed to authenticate user {email}. Error: {error}")
        logging.getLogger(__name__).debug(
            "Exception in authenticate_user for %s: %s", email, error
        )
        return ResponseModel(success=False, message="Failed to authenticate user", data=str(error))

def authenticate_user_firebase(email: str, password: str, version: str) -> ResponseModel:
    logging.getLogger(__name__).debug("Version: %s", version)
    logging.getLogger(__name__).debug("Frontend Version: %s", FRONTEND_VERSION)
    if version != FRONTEND_VERSION:
        logging.getLogger(__name__).debug("Invalid version: %s", version)
        raise HTTPException(status_code=400, detail="invalid_version")

    try:
        return firebase_authenticate(email, password)

    except Exception as error:
        logging.error(f"Failed to authenticate user {email}. Error: {error}")
        logging.getLogger(__name__).debug(
            "Exception in authenticate_user_firebase for %s: %s", email, error
        )
        return ResponseModel(success=False, message="Failed to authenticate user", data=str(error))
    
def validate_user(id_token: str) -> bool:
    """
    Verifies the Firebase idToken and retrieves the user's info.

    Args:
        id_token (str): The Firebase ID token.

    Returns:
        bool: True if valid, otherwise raises HTTPException.
    """
    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token.get("uid")
        logging.getLogger(__name__).debug("Token valid. UID: %s", uid)

        user = auth.get_user(uid)
        if user:
            user_details = {"user_id": user.uid, "email": user.email}
            return True
        else:
            logging.getLogger(__name__).debug("No user found for UID: %s", uid)
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
        logging.getLogger(__name__).debug("Token valid. UID: %s", uid)

        user = auth.get_user(uid)
        if user:
            # Query the 'user_team' collection where 'user_id' matches the given uid
            team_ref = FIRESTORE_CLIENT.collection("user_team").where("user_id", "==", user.uid)
            teams = team_ref.get()
            logging.getLogger(__name__).debug("Retrieved teams: %s", teams)
            
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
            logging.getLogger(__name__).debug("No user found for UID: %s", uid)
            raise HTTPException(status_code=401, detail="Unauthorized user")
    except Exception as e:
        logging.error(f"Error verifying token or retrieving user details/team: {e}")
        logging.getLogger(__name__).debug(
            "Exception in validate_user_and_get_data for %s: %s", id_token, e
        )
        raise HTTPException(status_code=401, detail="Unauthorized user")


_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> UserData:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Use 'Bearer <token>'",
        )
    return validate_user_and_get_data(credentials.credentials)


def get_current_user_data(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> UserData:
    """Backward-compatible alias for get_current_user."""
    return get_current_user(credentials)
