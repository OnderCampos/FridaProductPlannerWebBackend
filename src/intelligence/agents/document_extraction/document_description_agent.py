import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_description_agent_prompt import (
    DOCUMENT_DESCRIPTION_TASK,
)


_DOCUMENT_DESCRIPTION_DEPLOYMENT = (
    os.getenv("DOCUMENT_DESCRIPTION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


DOCUMENT_DESCRIPTION_AGENT = Agent(
    name="DocumentDescriptionAgent",
    task=DOCUMENT_DESCRIPTION_TASK,
    azure_deployment=_DOCUMENT_DESCRIPTION_DEPLOYMENT,
    args=["text", "language"],
)
