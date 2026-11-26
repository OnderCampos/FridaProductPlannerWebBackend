import logging
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from src.services.setup.firebase_setup import FIRESTORE_CLIENT

load_dotenv()

"""The API key for the LLMOPS API."""
LLMOPS_API_KEY = os.getenv("LLMOPS_API_KEY")
LOGGING_ENDPOINT = os.getenv("LOGGING_ENDPOINT")

FRIDA_API_ENDPOINT = os.getenv("FRIDA_API_ENDPOINT")
FRIDA_API_KEY = os.getenv("FRIDA_API_KEY")
FRIDA_MODEL = os.getenv("FRIDA_MODEL")
# MODEL_OLYMPUS_PEAK = os.getenv("MODEL_OLYMPUS_PEAK")
# MODEL_PUCK_SWIFT = os.getenv("MODEL_PUCK_SWIFT")

GPT_DEPLOYMENT = FRIDA_MODEL or "GAIA-LOOM"
MINI_DEPLOYMENT = FRIDA_MODEL or "GAIA-LOOM"

MODEL = GPT_DEPLOYMENT
MINI_MODEL = MINI_DEPLOYMENT

JIRA_API_KEY = os.getenv("JIRA_API_KEY")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")

FRONTEND_VERSION = os.getenv("FRONTEND_VERSION")
VITE_ENV_MODE = (os.getenv("VITE_ENV_MODE") or "development").strip().lower()
VITE_FRONTEND_BASE_URL_DEV = os.getenv("VITE_FRONTEND_BASE_URL_DEV")
VITE_FRONTEND_BASE_URL_PROD = os.getenv("VITE_FRONTEND_BASE_URL_PROD")


def _is_truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

FRONTEND_BASE_URL = (
    VITE_FRONTEND_BASE_URL_PROD
    if VITE_ENV_MODE == "production"
    else VITE_FRONTEND_BASE_URL_DEV
) or os.getenv("FRONTEND_BASE_URL")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

KNOWLEDGE_BASE_URL = os.getenv("KNOWLEDGE_BASE_URL")

CHATBOT_KB_BASE_URL = os.getenv("CHATBOT_KB_BASE_URL")

NOTIFICATION_API_URL = os.getenv(
    "NOTIFICATION_API_URL",
    "https://automationplatform.azurewebsites.net/api/mailnotification",
)
NOTIFICATION_SENDER_EMAIL = os.getenv(
    "NOTIFICATION_SENDER_EMAIL",
    "noreply@fridaplatform.online",
)
NOTIFICATION_SENDER_NAME = os.getenv(
    "NOTIFICATION_SENDER_NAME",
    "FridaPlatform",
)
DISABLE_EMAIL_NOTIFICATIONS = _is_truthy_env(
    os.getenv("DISABLE_EMAIL_NOTIFICATIONS")
)

logger = logging.getLogger(__name__)

def get_active_frida_model(fallback: str | None = None) -> str:
    resolved_fallback = str(fallback or MINI_DEPLOYMENT or "GAIA-LOOM").strip()
    try:

        doc = FIRESTORE_CLIENT.collection("llm_settings").document("ai_settings").get()
        if doc.exists:
            active_model = (doc.to_dict() or {}).get("active_model_id")
            if active_model:
                resolved_fallback = str(active_model).strip()
    except Exception as exc:
        logger.warning("Error fetching active model from Firebase, using fallback: %s", exc)

    return resolved_fallback.replace("_", "-")

def build_frida_chat_client(
    model_name: str | None = None,
    user_id: str | None = None,
    email: str | None = None,
) -> ChatOpenAI:
    resolved_model = get_active_frida_model(fallback=model_name or MINI_DEPLOYMENT)
    extra_body = None
    if user_id or email:
        extra_body = {
            "email": email or "unknown_email",
            "user_id": user_id or "unknown_user",
        }

    return ChatOpenAI(
        model=resolved_model,
        base_url=FRIDA_API_ENDPOINT,
        api_key=FRIDA_API_KEY,
        extra_body=extra_body,
    )

logger.info("Setting up Frida Chat OpenAI clients...")

gpt_client = ChatOpenAI(
    model=str(GPT_DEPLOYMENT).replace("_", "-"),
    base_url=FRIDA_API_ENDPOINT,
    api_key=FRIDA_API_KEY,
)

gpt_mini_client = ChatOpenAI(
    model=str(MINI_DEPLOYMENT).replace("_", "-"),
    base_url=FRIDA_API_ENDPOINT,
    api_key=FRIDA_API_KEY,
)

# Backward-compatible aliases for existing imports.
gpt40_client = gpt_client
gpt40_mini_client = gpt_mini_client