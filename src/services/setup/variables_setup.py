import logging
import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

"""The API key for the LLMOPS API."""
LLMOPS_API_KEY = os.getenv("LLMOPS_API_KEY")
LOGGING_ENDPOINT = os.getenv("LOGGING_ENDPOINT")

MODEL = os.getenv("AZURE_DEPLOYMENT")

JIRA_API_KEY = os.getenv("JIRA_API_KEY")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")

FRONTEND_VERSION = os.getenv("FRONTEND_VERSION")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

KNOWLEDGE_BASE_URL = os.getenv("KNOWLEDGE_BASE_URL")

CHATBOT_KB_BASE_URL = os.getenv("CHATBOT_KB_BASE_URL")

logging.getLogger(__name__).info("Setting up Azure Chat OpenAI client...")

gpt40_client = AzureChatOpenAI(
    azure_deployment=os.getenv("AZURE_DEPLOYMENT"),
    api_version=os.getenv("API_VERSION")
)

gpt40_mini_client = AzureChatOpenAI(
    azure_deployment=os.getenv("AZURE_DEPLOYMENT_MINI"),
    api_version=os.getenv("API_VERSION"),
)
