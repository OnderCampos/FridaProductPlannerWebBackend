import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_story_grouping_agent_prompt import (
    DOCUMENT_STORY_GROUPING_TASK,
)


_DOCUMENT_STORY_GROUPING_DEPLOYMENT = (
    os.getenv("DOCUMENT_STORY_GROUPING_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


DOCUMENT_STORY_GROUPING_AGENT = Agent(
    name="DocumentStoryGroupingAgent",
    task=DOCUMENT_STORY_GROUPING_TASK,
    azure_deployment=_DOCUMENT_STORY_GROUPING_DEPLOYMENT,
    args=[
        "project_name",
        "project_description",
        "roles",
        "technical_stack",
        "epic_candidates",
        "user_stories",
        "language",
    ],
)
