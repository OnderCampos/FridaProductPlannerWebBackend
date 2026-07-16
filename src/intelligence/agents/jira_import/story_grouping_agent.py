import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.jira_story_grouping_agent_prompt import (
    JIRA_STORY_GROUPING_PROMPT,
)


_JIRA_GROUPING_AGENT_DEPLOYMENT = (
    os.getenv("JIRA_GROUPING_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)


JIRA_STORY_GROUPING_AGENT = Agent(
    name="JiraStoryGroupingAgent",
    task=JIRA_STORY_GROUPING_PROMPT,
    azure_deployment=_JIRA_GROUPING_AGENT_DEPLOYMENT,
    args=["existing_epics", "stories"],
)

