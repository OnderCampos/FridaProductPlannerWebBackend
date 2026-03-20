import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_epic_extraction_agent_prompt import (
    DOCUMENT_EPIC_EXTRACTION_TASK,
)


_DOCUMENT_EPIC_EXTRACTION_DEPLOYMENT = (
    os.getenv("DOCUMENT_EPIC_EXTRACTION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


DOCUMENT_EPIC_EXTRACTION_AGENT = Agent(
    name="DocumentEpicExtractionAgent",
    task=DOCUMENT_EPIC_EXTRACTION_TASK,
    azure_deployment=_DOCUMENT_EPIC_EXTRACTION_DEPLOYMENT,
    args=["text", "project_description", "language"],
)
