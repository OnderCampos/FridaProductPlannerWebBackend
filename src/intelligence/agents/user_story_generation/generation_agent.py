import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.user_story_generation_agent_prompt import (
    GENERATE_EPIC_PROMPT,
)


_USER_STORY_GENERATION_DEPLOYMENT = (
    os.getenv("USER_STORY_GENERATION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
    or os.getenv("AZURE_DEPLOYMENT")
)


USER_STORY_GENERATION_AGENT = Agent(
    name="UserStoryGenerationAgent",
    task=GENERATE_EPIC_PROMPT,
    azure_deployment=_USER_STORY_GENERATION_DEPLOYMENT,
    args=[
        "functionality",
        "users",
        "epic",
        "functionalities",
        "template_field_keys",
        "template_fields_json",
        "fields_description",
    ],
)
