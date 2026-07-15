import os
import re

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.epic_role_brainstorm_agent_prompt import (
    EPIC_ROLE_BRAINSTORM_TASK,
)


_EPIC_ROLE_BRAINSTORM_DEPLOYMENT = (
    os.getenv("EPIC_ROLE_BRAINSTORM_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)

def _sanitize_role_name(value: str) -> str:
    value = (value or "role").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value or "role"


def build_epic_role_brainstorm_agent(role_name: str) -> Agent:
    safe_role = _sanitize_role_name(role_name)[:40]
    return Agent(
        name=f"EpicRoleBrainstormAgent_{safe_role}",
        task=EPIC_ROLE_BRAINSTORM_TASK,
        azure_deployment=_EPIC_ROLE_BRAINSTORM_DEPLOYMENT,
        args=[
            "language",
            "project_name",
            "project_description",
            "role_profile",
            "objectives",
            "constraints",
            "non_goals",
            "capabilities_implied",
            "risks_open_questions",
            "domain_terms",
            "kb_context",
        ],
    )
