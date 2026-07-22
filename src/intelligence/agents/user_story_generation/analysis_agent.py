import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.user_story_analysis_agent_prompt import (
    IDENTIFY_EPIC_USERS_AND_FUNCTIONALITY_PROMPT,
)


_USER_STORY_ANALYSIS_DEPLOYMENT = (
    os.getenv("USER_STORY_ANALYSIS_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


USER_STORY_ANALYSIS_AGENT = Agent(
    name="UserStoryAnalysisAgent",
    task=IDENTIFY_EPIC_USERS_AND_FUNCTIONALITY_PROMPT,
    azure_deployment=_USER_STORY_ANALYSIS_DEPLOYMENT,
    args=["epic", "project_description"],
)
