import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.epic_agent_prompt import (
    GENERATE_KEYWORDS_FOR_KBS_PROMPT,
    SUMMARIZE_PROJECT_DESCRIPTION_PROMPT,
)


_EPIC_AGENT_DEPLOYMENT = (
    os.getenv("EPIC_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


PROJECT_SUMMARY_AGENT = Agent(
    name="ProjectSummaryAgent",
    task=SUMMARIZE_PROJECT_DESCRIPTION_PROMPT,
    azure_deployment=_EPIC_AGENT_DEPLOYMENT,
    args=["current", "next", "language"],
)

KB_KEYWORDS_AGENT = Agent(
    name="KnowledgeBaseKeywordsAgent",
    task=GENERATE_KEYWORDS_FOR_KBS_PROMPT,
    azure_deployment=_EPIC_AGENT_DEPLOYMENT,
    args=["text"],
)
