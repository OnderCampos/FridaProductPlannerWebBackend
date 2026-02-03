import os
from pathlib import Path
from dotenv import load_dotenv

from src.services.auth.firebase import FirebaseObject

load_dotenv()

AUTH_PATH = os.path.join(Path(__file__).parent.parent, "auth", "firebase.json")
"""The path to the Firebase authentication file."""

FIREBASE = FirebaseObject.from_json(AUTH_PATH)
"""The Firebase object used for accessing the Firebase API."""

FIRESTORE_CLIENT = FIREBASE.fs_client
"""The Firestore client used for accessing the Firebase API."""

FIREBASE_KEY = os.getenv("FIREBASE_KEY")
"""The Firebase API key."""