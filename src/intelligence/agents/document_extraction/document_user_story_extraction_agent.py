import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_user_story_extraction_agent_prompt import (
    DOCUMENT_USER_STORY_EXTRACTION_TASK,
)


_DOCUMENT_USER_STORY_EXTRACTION_DEPLOYMENT = (
    os.getenv("DOCUMENT_USER_STORY_EXTRACTION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


DOCUMENT_USER_STORY_EXTRACTION_AGENT = Agent(
    name="DocumentUserStoryExtractionAgent",
    task=DOCUMENT_USER_STORY_EXTRACTION_TASK,
    azure_deployment=_DOCUMENT_USER_STORY_EXTRACTION_DEPLOYMENT,
    args=["text", "epics", "language"],
)
