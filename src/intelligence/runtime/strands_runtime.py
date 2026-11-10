from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from time import perf_counter_ns
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from openai import AsyncAzureOpenAI

from src.services.setup.language_setup import (
    build_llm_language_system_prompt,
    get_default_llm_language,
    normalize_language,
)
from src.services.setup.variables_setup import (
    GPT_DEPLOYMENT,
    LLMOPS_API_KEY,
    MINI_DEPLOYMENT,
)
from src.utils.ai.llm_graph import invoke_model_with_graph
from src.utils.ai.llmops_utils import log_to_llmops
from src.utils.core.logging import add_request_log
from src.utils.planning.team import get_team_name

try:
    from strands import Agent as StrandsAgent
    from strands.models.openai import OpenAIModel
except Exception:  # pragma: no cover - optional dependency fallback
    StrandsAgent = None
    OpenAIModel = None

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_CALLS = 3


@dataclass(frozen=True)
class StrandsInvocationResult:
    content: str
    raw_response: Any
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _normalize_model_tier(model_tier: str) -> str:
    return "gpt" if str(model_tier or "").strip().lower() == "gpt" else "mini"


def _deployment_for_tier(model_tier: str) -> str:
    normalized = _normalize_model_tier(model_tier)
    if normalized == "gpt":
        return GPT_DEPLOYMENT or MINI_DEPLOYMENT or ""
    return MINI_DEPLOYMENT or GPT_DEPLOYMENT or ""


def _tier_for_deployment(azure_deployment: Optional[str]) -> str:
    deployment = str(azure_deployment or "").strip()
    if deployment and deployment == (GPT_DEPLOYMENT or ""):
        return "gpt"
    return "mini"


def _extract_content(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response

    for attr in ("content", "text", "output_text"):
        value = getattr(response, attr, None)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text_value = item.get("text")
                    if isinstance(text_value, str):
                        parts.append(text_value)
            if parts:
                return "\n".join(part for part in parts if part)

    if isinstance(response, dict):
        for key in ("content", "text", "output_text"):
            value = response.get(key)
            if isinstance(value, str):
                return value

    return str(response)


def _extract_token_usage(response: Any) -> Tuple[int, int]:
    usage_candidates: List[Any] = []
    for attr in ("usage", "token_usage", "response_metadata", "metadata"):
        usage_candidates.append(getattr(response, attr, None))
    if isinstance(response, dict):
        usage_candidates.extend(
            [
                response.get("usage"),
                response.get("token_usage"),
                response.get("response_metadata"),
                response.get("metadata"),
            ]
        )

    for candidate in usage_candidates:
        if not isinstance(candidate, dict):
            continue
        token_usage = candidate.get("token_usage") if isinstance(candidate.get("token_usage"), dict) else candidate
        if not isinstance(token_usage, dict):
            continue
        prompt_tokens = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
        completion_tokens = int(
            token_usage.get("completion_tokens")
            or token_usage.get("output_tokens")
            or token_usage.get("generated_tokens")
            or 0
        )
        if prompt_tokens or completion_tokens:
            return prompt_tokens, completion_tokens

    return 0, 0


def _extract_user_context(execution_context: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    user_data = execution_context.get("user_data")
    if user_data is not None:
        try:
            return (
                user_data.get_user_id(),
                user_data.get_team_id(),
                user_data.get_email(),
                user_data.get_user_name(),
            )
        except Exception:
            pass

    return (
        execution_context.get("user_id"),
        execution_context.get("team_id"),
        execution_context.get("email"),
        execution_context.get("user_name"),
    )


def _to_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)


def _log_completion(
    *,
    prompt_text: str,
    response: Any,
    execution_context: Dict[str, Any],
    agent_name: str,
    model_name: str,
    call_start_ns: int,
    end_time: datetime,
    elapsed_s: float,
) -> None:
    prompt_tokens, completion_tokens = _extract_token_usage(response)
    user_id, team_id, email, _user_name = _extract_user_context(execution_context)

    if user_id:
        try:
            add_request_log(
                user_id=user_id,
                team_id=team_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception:
            logger.warning("Error saving request log for Strands agent call.", exc_info=True)

    if not LLMOPS_API_KEY:
        return

    team_name = "Unknown Team"
    if team_id:
        try:
            team_name = get_team_name(team_id) or "Unknown Team"
        except Exception:
            team_name = "Unknown Team"

    try:
        log_to_llmops(
            prompt=prompt_text,
            response=_extract_content(response),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            call_start=call_start_ns,
            created=end_time.isoformat(),
            uid=user_id or "",
            api_key=LLMOPS_API_KEY,
            model=model_name,
            additional_kwargs={
                "agent_name": agent_name,
                "team_name": team_name,
                "duration_ms": int(elapsed_s * 1000),
            },
            email=email or "unknown_email",
            status="success",
            team_id=team_id,
        )
    except Exception:
        logger.warning("Error logging Strands agent call to LLMOPS.", exc_info=True)


def _decode_image_payload(image_payload: str) -> Optional[Dict[str, Any]]:
    raw = str(image_payload or "").strip()
    if not raw:
        return None

    image_format = "jpeg"
    encoded = raw
    if raw.startswith("data:image"):
        prefix, _, encoded = raw.partition(",")
        match = re.match(r"data:image/([a-zA-Z0-9.+-]+);base64", prefix)
        if match:
            image_format = match.group(1).lower()

    try:
        image_bytes = base64.b64decode(encoded, validate=False)
    except Exception:
        return None

    return {
        "image": {
            "format": image_format or "jpeg",
            "source": {"bytes": image_bytes},
        }
    }


def _build_multimodal_prompt(prompt: str, images: Sequence[str]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [{"text": prompt}]
    for image_payload in images or []:
        block = _decode_image_payload(str(image_payload or ""))
        if block is not None:
            blocks.append(block)
    return blocks


def _build_openai_client(model_tier: str):
    api_key = (
        os.getenv("AZURE_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("AZURE_API_KEY")
    )
    if not api_key:
        return None

    api_version = os.getenv("API_VERSION")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_ENDPOINT")
    if azure_endpoint:
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "azure_endpoint": azure_endpoint,
        }
        if api_version:
            kwargs["api_version"] = api_version
        return AsyncAzureOpenAI(**kwargs)

    return None


@lru_cache(maxsize=4)
def _cached_langchain_client(model_tier: str) -> AzureChatOpenAI:
    deployment = _deployment_for_tier(model_tier)
    return AzureChatOpenAI(
        azure_deployment=deployment,
        api_version=os.getenv("API_VERSION"),
    )


def _build_strands_model(model_tier: str):
    if OpenAIModel is None:
        raise RuntimeError("Strands OpenAIModel is not available.")

    deployment = _deployment_for_tier(model_tier)
    client = _build_openai_client(model_tier)
    if client is not None:
        return OpenAIModel(client=client, model_id=deployment)

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY") or ""
    client_args: Dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("AZURE_OPENAI_BASE_URL")
    if base_url:
        client_args["base_url"] = base_url
    return OpenAIModel(client_args=client_args, model_id=deployment)


def _build_strands_agent(*, agent_name: str, system_prompt: str, model_tier: str):
    if StrandsAgent is None:
        raise RuntimeError("Strands Agent is not available.")
    return StrandsAgent(
        model=_build_strands_model(model_tier),
        system_prompt=system_prompt,
    )


def _invoke_with_langchain_fallback(
    *,
    system_prompt: str,
    prompt: str,
    model_tier: str,
    images: Optional[Sequence[str]] = None,
) -> Any:
    messages: List[Any] = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))

    if images:
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_payload in images:
            decoded = _decode_image_payload(str(image_payload or ""))
            if decoded is None:
                continue
            image = decoded.get("image", {})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/{image.get('format', 'jpeg')};base64,{base64.b64encode(image['source']['bytes']).decode('ascii')}"
                    },
                }
            )
        messages.append(HumanMessage(content=content))
    else:
        messages.append(HumanMessage(content=prompt))

    client = _cached_langchain_client(model_tier)
    return invoke_model_with_graph(client=client, messages=messages)


def _reviewer_system_prompt(agent_name: str) -> str:
    return (
        f"You are the Product Planner {agent_name} reviewer agent. "
        "You are the second agent in a fixed two-agent swarm. "
        "Review the first agent's candidate response against the original instructions. "
        "Return the final corrected response only. "
        "Preserve the requested output format exactly, especially JSON-only constraints. "
        "Do not add commentary, explanations, markdown fences, or prefixes unless explicitly required."
    )


def _reviewer_prompt(original_prompt: str, candidate_response: str) -> str:
    return (
        "Review the candidate response and produce the final response.\n\n"
        "Original instructions:\n"
        f"{original_prompt}\n\n"
        "Candidate response:\n"
        f"{candidate_response}\n\n"
        "Return only the final answer. If the candidate is already correct, return it unchanged."
    )


def invoke_swarm_sync(
    *,
    agent_name: str,
    prompt: str,
    system_prompt: str,
    model_tier: str = "mini",
    execution_context: Optional[Dict[str, Any]] = None,
    images: Optional[Sequence[str]] = None,
) -> StrandsInvocationResult:
    execution_context = dict(execution_context or {})
    normalized_tier = _normalize_model_tier(model_tier)
    reviewer_system_prompt = _reviewer_system_prompt(agent_name)
    call_start_ns = perf_counter_ns()
    start_time = datetime.now(timezone.utc)

    try:
        if images:
            primary_prompt: Any = _build_multimodal_prompt(prompt, images)
            candidate_response = _build_strands_agent(
                agent_name=agent_name,
                system_prompt=system_prompt,
                model_tier=normalized_tier,
            )(primary_prompt)
            candidate_text = _extract_content(candidate_response)
            reviewer_response = _build_strands_agent(
                agent_name=f"{agent_name}Reviewer",
                system_prompt=reviewer_system_prompt,
                model_tier=normalized_tier,
            )(_reviewer_prompt(prompt, candidate_text))
            final_response = reviewer_response
        else:
            primary_response = _build_strands_agent(
                agent_name=agent_name,
                system_prompt=system_prompt,
                model_tier=normalized_tier,
            )(prompt)
            candidate_text = _extract_content(primary_response)
            reviewer_agent = _build_strands_agent(
                agent_name=f"{agent_name}Reviewer",
                system_prompt=reviewer_system_prompt,
                model_tier=normalized_tier,
            )
            final_response = reviewer_agent(_reviewer_prompt(prompt, candidate_text))
    except Exception:
        logger.warning("Strands sync invocation failed for %s; falling back to LangChain client.", agent_name, exc_info=True)
        final_response = _invoke_with_langchain_fallback(
            system_prompt=system_prompt,
            prompt=prompt,
            model_tier=normalized_tier,
            images=images,
        )

    end_time = datetime.now(timezone.utc)
    elapsed_s = (end_time - start_time).total_seconds()
    _log_completion(
        prompt_text=prompt,
        response=final_response,
        execution_context=execution_context,
        agent_name=agent_name,
        model_name=_deployment_for_tier(normalized_tier),
        call_start_ns=call_start_ns,
        end_time=end_time,
        elapsed_s=elapsed_s,
    )
    prompt_tokens, completion_tokens = _extract_token_usage(final_response)
    return StrandsInvocationResult(
        content=_extract_content(final_response),
        raw_response=final_response,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def invoke_swarm_async(
    *,
    agent_name: str,
    prompt: str,
    system_prompt: str,
    model_tier: str = "mini",
    execution_context: Optional[Dict[str, Any]] = None,
    images: Optional[Sequence[str]] = None,
) -> StrandsInvocationResult:
    execution_context = dict(execution_context or {})
    normalized_tier = _normalize_model_tier(model_tier)
    reviewer_system_prompt = _reviewer_system_prompt(agent_name)
    call_start_ns = perf_counter_ns()
    start_time = datetime.now(timezone.utc)

    try:
        primary_agent = _build_strands_agent(
            agent_name=agent_name,
            system_prompt=system_prompt,
            model_tier=normalized_tier,
        )
        primary_prompt: Any = _build_multimodal_prompt(prompt, images) if images else prompt
        primary_response = await primary_agent.invoke_async(primary_prompt)
        candidate_text = _extract_content(primary_response)

        reviewer_agent = _build_strands_agent(
            agent_name=f"{agent_name}Reviewer",
            system_prompt=reviewer_system_prompt,
            model_tier=normalized_tier,
        )
        reviewer_response = await reviewer_agent.invoke_async(
            _reviewer_prompt(prompt, candidate_text)
        )
        final_response = reviewer_response
    except Exception:
        logger.warning("Strands async invocation failed for %s; falling back to LangChain client.", agent_name, exc_info=True)
        final_response = _invoke_with_langchain_fallback(
            system_prompt=system_prompt,
            prompt=prompt,
            model_tier=normalized_tier,
            images=images,
        )

    end_time = datetime.now(timezone.utc)
    elapsed_s = (end_time - start_time).total_seconds()
    _log_completion(
        prompt_text=prompt,
        response=final_response,
        execution_context=execution_context,
        agent_name=agent_name,
        model_name=_deployment_for_tier(normalized_tier),
        call_start_ns=call_start_ns,
        end_time=end_time,
        elapsed_s=elapsed_s,
    )
    prompt_tokens, completion_tokens = _extract_token_usage(final_response)
    return StrandsInvocationResult(
        content=_extract_content(final_response),
        raw_response=final_response,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def format_agent_task(*, task_template: str, prompt_kwargs: Dict[str, Any], args: Optional[List[str]]) -> str:
    task_text = task_template
    replaced_any = False

    format_keys = args or list(prompt_kwargs.keys())
    for key in format_keys:
        placeholder = "{" + key + "}"
        if placeholder not in task_text:
            continue
        value = prompt_kwargs.get(key)
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False)
        elif value is None:
            value_text = ""
        else:
            value_text = str(value)
        task_text = task_text.replace(placeholder, value_text)
        replaced_any = True

    unresolved = set(re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*)}", task_text))
    if unresolved:
        raise ValueError(
            "Unresolved task placeholders: " + ", ".join(sorted(unresolved))
        )

    if not replaced_any and prompt_kwargs:
        prompt_lines = []
        for key, value in prompt_kwargs.items():
            if isinstance(value, (dict, list)):
                value_text = json.dumps(value, ensure_ascii=False)
            elif value is None:
                value_text = ""
            else:
                value_text = str(value)
            prompt_lines.append(f"{key}: {value_text}")
        prompt_text = "\n".join(prompt_lines)
        if prompt_text:
            task_text = f"{task_text}\n{prompt_text}"

    return task_text
