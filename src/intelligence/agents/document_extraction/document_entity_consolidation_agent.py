import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_entity_consolidation_agent_prompt import (
    DOCUMENT_ENTITY_CONSOLIDATION_TASK,
)


_DOCUMENT_ENTITY_CONSOLIDATION_DEPLOYMENT = (
    os.getenv("DOCUMENT_ENTITY_CONSOLIDATION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)


DOCUMENT_ENTITY_CONSOLIDATION_AGENT = Agent(
    name="DocumentEntityConsolidationAgent",
    task=DOCUMENT_ENTITY_CONSOLIDATION_TASK,
    azure_deployment=_DOCUMENT_ENTITY_CONSOLIDATION_DEPLOYMENT,
    args=["project_name", "project_description", "extractions", "language"],
)
