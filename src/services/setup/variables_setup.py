import logging
import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

"""The API key for the LLMOPS API."""
LLMOPS_API_KEY = os.getenv("LLMOPS_API_KEY")
LOGGING_ENDPOINT = os.getenv("LOGGING_ENDPOINT")

GPT_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT")
MINI_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT_MINI") or GPT_DEPLOYMENT

MODEL = GPT_DEPLOYMENT
MINI_MODEL = MINI_DEPLOYMENT

JIRA_API_KEY = os.getenv("JIRA_API_KEY")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")

FRONTEND_VERSION = os.getenv("FRONTEND_VERSION")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL")

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

logging.getLogger(__name__).info("Setting up Azure Chat OpenAI clients...")

gpt_client = AzureChatOpenAI(
    azure_deployment=GPT_DEPLOYMENT or MINI_DEPLOYMENT,
    api_version=os.getenv("API_VERSION"),
)

gpt_mini_client = AzureChatOpenAI(
    azure_deployment=MINI_DEPLOYMENT or GPT_DEPLOYMENT,
    api_version=os.getenv("API_VERSION"),
)

# Backward-compatible aliases for existing imports.
gpt40_client = gpt_client
gpt40_mini_client = gpt_mini_client
