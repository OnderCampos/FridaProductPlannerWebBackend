import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_story_grouping_agent_prompt import (
    DOCUMENT_STORY_GROUPING_TASK,
)


_DOCUMENT_STORY_GROUPING_DEPLOYMENT = (
    os.getenv("DOCUMENT_STORY_GROUPING_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)


DOCUMENT_STORY_GROUPING_AGENT = Agent(
    name="DocumentStoryGroupingAgent",
    task=DOCUMENT_STORY_GROUPING_TASK,
    azure_deployment=_DOCUMENT_STORY_GROUPING_DEPLOYMENT,
    args=[
        "project_name",
        "user_stories",
        "story_relationships",
        "language",
    ],
)
