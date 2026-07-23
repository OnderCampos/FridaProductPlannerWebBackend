"""Swarm-style runtime for named Product Planner agents.

Feature modules must call ``run_agent`` instead of constructing an LLM client.
The Azure transport remains behind this boundary while the application migrates to
the OpenAI Agents SDK. This keeps existing Azure deployment routing, language
handling, token accounting, and LLMOps tracing stable during the rollout.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Sequence

from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import gpt_client, gpt_mini_client


class AgentName(str, Enum):
    PROJECT_TRIAGE = "project-triage"
    CLARIFICATION = "clarification"
    SPECIFICATION = "specification"
    DOCUMENT_EXTRACTION = "document-extraction"
    EPIC_PLANNING = "epic-planning"
    USER_STORY_GENERATION = "user-story-generation"
    USER_STORY_EXPANSION = "user-story-expansion"
    DEPENDENCY_ANALYSIS = "dependency-analysis"
    TASK_PLANNING = "task-planning"
    TASK_ESTIMATION = "task-estimation"
    IMPLEMENTATION_GUIDANCE = "implementation-guidance"
    USER_STORY_DOCUMENT = "user-story-document"
    CONTENT_CLEANUP = "content-cleanup"
    PROJECT_CHAT = "project-chat"


def _agent_instruction(agent: AgentName) -> str:
    return (
        "You are the Product Planner "
        f"{agent.value} agent. Follow the task instructions exactly, preserve the "
        "requested response format, and do not perform actions outside the supplied context."
    )


async def run_agent(
    agent: AgentName,
    prompt: str,
    user_data: UserData,
    *,
    model_tier: str = "mini",
    images: Optional[Sequence[str]] = None,
    knowledge_base_id: Optional[str] = None,
) -> str:
    """Run a named agent through the sole supported LLM execution boundary."""
    service = AzureChatService(api_key=None, user_data=user_data, knowledge_base_id=knowledge_base_id)
    composed_prompt = f"{_agent_instruction(agent)}\n\n{prompt}"
    if images:
        return await service.simple_completion_with_images(
            composed_prompt,
            list(images),
            model_tier=model_tier,
        )
    return await service.simple_completion(composed_prompt, model_tier=model_tier)


def bind_agent_tools(
    agent: AgentName,
    tools: Sequence[Any],
    *,
    model_tier: str = "mini",
) -> Any:
    """Bind tools for an agent-owned orchestration loop during the LangGraph transition."""
    client = gpt_client if str(model_tier).lower() == "gpt" else gpt_mini_client
    return client.bind_tools(list(tools))
