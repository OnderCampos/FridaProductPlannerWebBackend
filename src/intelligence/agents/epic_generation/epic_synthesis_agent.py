import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.epic_synthesis_agent_prompt import (
    EPIC_SYNTHESIS_TASK,
)


_EPIC_SYNTHESIS_DEPLOYMENT = (
    os.getenv("EPIC_SYNTHESIS_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


EPIC_SYNTHESIS_AGENT = Agent(
    name="EpicSynthesisAgent",
    task=EPIC_SYNTHESIS_TASK,
    azure_deployment=_EPIC_SYNTHESIS_DEPLOYMENT,
    args=[
        "language",
        "project_name",
        "project_description",
        "scope_analysis",
        "role_brainstorms",
        "kb_context",
    ],
)
