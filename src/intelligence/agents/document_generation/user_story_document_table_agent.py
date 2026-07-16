import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.user_story_document_table_agent_prompt import (
    USER_STORY_DOCUMENT_TABLE_TASK,
)


_USER_STORY_DOCUMENT_TABLE_DEPLOYMENT = (
    os.getenv("USER_STORY_DOCUMENT_TABLE_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)


USER_STORY_DOCUMENT_TABLE_AGENT = Agent(
    name="UserStoryDocumentTableAgent",
    task=USER_STORY_DOCUMENT_TABLE_TASK,
    azure_deployment=_USER_STORY_DOCUMENT_TABLE_DEPLOYMENT,
    args=["project", "epic", "story", "current_document", "qa_history", "language"],
)
