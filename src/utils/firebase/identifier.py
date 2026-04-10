from src.services.setup.firebase_setup import FIRESTORE_CLIENT
IDENTIFIER_COLLECTION = "identifier_index"

def get_next_US_identifier() -> str:
    doc_ref = FIRESTORE_CLIENT.collection(IDENTIFIER_COLLECTION).document("user_stories_index")
    doc = doc_ref.get()
    if not doc.exists:
        next_value = 1
    else:
        next_value = int((doc.to_dict() or {}).get("value") or 0) + 1
    doc_ref.set({"value": next_value})
    return f"US-{next_value}"

def get_next_TK_identifier() -> str:
    doc_ref = FIRESTORE_CLIENT.collection(IDENTIFIER_COLLECTION).document("tasks_index")
    doc = doc_ref.get()
    if not doc.exists:
        next_value = 1
    else:
        next_value = int((doc.to_dict() or {}).get("value") or 0) + 1
    doc_ref.set({"value": next_value})
    return f"TK-{next_value}"

def get_next_EPIC_identifier() -> str:
    doc_ref = FIRESTORE_CLIENT.collection(IDENTIFIER_COLLECTION).document("epics_index")
    doc = doc_ref.get()
    if not doc.exists:
        next_value = 1
    else:
        next_value = int((doc.to_dict() or {}).get("value") or 0) + 1
    doc_ref.set({"value": next_value})
    return f"EP-{next_value}"
