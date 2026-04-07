import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.epic_scope_analysis_agent_prompt import (
    EPIC_SCOPE_ANALYSIS_TASK,
)


_EPIC_SCOPE_ANALYSIS_DEPLOYMENT = (
    os.getenv("EPIC_SCOPE_ANALYSIS_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


EPIC_SCOPE_ANALYSIS_AGENT = Agent(
    name="EpicScopeAnalysisAgent",
    task=EPIC_SCOPE_ANALYSIS_TASK,
    azure_deployment=_EPIC_SCOPE_ANALYSIS_DEPLOYMENT,
    args=["text", "language"],
)
