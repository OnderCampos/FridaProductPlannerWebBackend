import os
from pathlib import Path
from dotenv import load_dotenv

from src.services.auth.firebase import FirebaseObject

load_dotenv()

AUTH_PATH = os.path.join(Path(__file__).parent.parent, "auth", "firebase.json")
"""The path to the Firebase authentication file."""

_storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET")
_firebase_options = {"storageBucket": _storage_bucket} if _storage_bucket else None

FIREBASE = FirebaseObject.from_json(AUTH_PATH, options=_firebase_options)
"""The Firebase object used for accessing the Firebase API."""

FIRESTORE_CLIENT = FIREBASE.fs_client
"""The Firestore client used for accessing the Firebase API."""

FIREBASE_KEY = os.getenv("FIREBASE_KEY")
"""The Firebase API key."""
