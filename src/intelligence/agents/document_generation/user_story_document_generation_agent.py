import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.user_story_document_generation_agent_prompt import (
    USER_STORY_DOCUMENT_GENERATION_TASK,
)


_USER_STORY_DOCUMENT_GENERATION_DEPLOYMENT = (
    os.getenv("USER_STORY_DOCUMENT_GENERATION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)


USER_STORY_DOCUMENT_GENERATION_AGENT = Agent(
    name="UserStoryDocumentGenerationAgent",
    task=USER_STORY_DOCUMENT_GENERATION_TASK,
    azure_deployment=_USER_STORY_DOCUMENT_GENERATION_DEPLOYMENT,
    args=["template_spec", "project", "epic", "story", "current_document", "qa_history", "language"],
)
