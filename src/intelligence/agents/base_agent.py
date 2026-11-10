from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.intelligence.runtime.strands_runtime import (
    _DEFAULT_MAX_TOOL_CALLS,
    _tier_for_deployment,
    format_agent_task,
    invoke_swarm_sync,
)
from src.services.setup.language_setup import (
    build_llm_language_system_prompt,
    get_default_llm_language,
    normalize_language,
)


@dataclass
class BoundAgent:
    _agent: "Agent"
    _context: Dict[str, Any]

    def bind_context(self, context: Optional[Dict[str, Any]] = None) -> "BoundAgent":
        merged = dict(self._context)
        if context:
            merged.update(context)
        return BoundAgent(self._agent, merged)

    def execute(self, **kwargs):
        return self._agent._execute_with_context(
            prompt_kwargs=kwargs,
            execution_context=self._context,
        )


class Agent:
    def __init__(
        self,
        name: str,
        system_message: Optional[str] = None,
        task: Optional[str] = None,
        args: Optional[List[str]] = None,
        azure_deployment: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_handlers: Optional[Dict[str, Callable[..., Any]]] = None,
        tool_choice: str = "auto",
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
    ):
        self._name = name
        self._system_message = system_message
        self._task = task
        self._args = list(args) if args else None
        self._azure_deployment = azure_deployment
        self._model_tier = _tier_for_deployment(azure_deployment)
        self._tools = list(tools) if tools else []
        self._tool_handlers = dict(tool_handlers) if tool_handlers else {}
        self._tool_choice = tool_choice
        self._max_tool_calls = max(0, int(max_tool_calls))

        if self._args and len(set(self._args)) != len(self._args):
            raise ValueError("Agent args list must not contain duplicates.")
        if self._tools:
            self._validate_tools_config()

    def _validate_tools_config(self) -> None:
        tool_names = []
        for tool in self._tools:
            if not isinstance(tool, dict):
                raise ValueError(
                    f"Invalid tool schema for {self._name}: expected dict."
                )
            function = tool.get("function", {})
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"Invalid tool schema for {self._name}: missing function.name."
                )
            tool_names.append(name)

        duplicated = sorted({name for name in tool_names if tool_names.count(name) > 1})
        if duplicated:
            raise ValueError(
                f"Tool schemas for {self._name} contain duplicated names: {', '.join(duplicated)}"
            )

        missing_handlers = [name for name in tool_names if name not in self._tool_handlers]
        if missing_handlers:
            raise ValueError(
                f"Missing tool handlers for {self._name}: {', '.join(sorted(missing_handlers))}"
            )

    def bind_context(self, context: Optional[Dict[str, Any]] = None) -> BoundAgent:
        return BoundAgent(self, dict(context or {}))

    def _validate_args(self, **kwargs) -> None:
        if not self._args:
            return

        expected = set(self._args)
        provided = set(kwargs.keys())
        missing = expected - provided
        extra = provided - expected
        if not missing and not extra:
            return

        details = []
        if missing:
            details.append(f"missing: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"extra: {', '.join(sorted(extra))}")
        raise ValueError(f"Invalid args for {self._name}. " + "; ".join(details))

    def _format_system_prompt(self, execution_context: Dict[str, Any], prompt_kwargs: Dict[str, Any]) -> str:
        language_override = None
        if isinstance(prompt_kwargs.get("language"), str) and prompt_kwargs["language"].strip():
            language_override = prompt_kwargs["language"]
        elif isinstance(execution_context.get("language"), str) and execution_context["language"].strip():
            language_override = execution_context["language"]
        elif isinstance(execution_context.get("llm_language"), str) and execution_context["llm_language"].strip():
            language_override = execution_context["llm_language"]

        effective_language = normalize_language(language_override, default=get_default_llm_language())
        language_system_prompt = build_llm_language_system_prompt(effective_language)

        parts = []
        if self._system_message:
            parts.append(self._system_message)
        if language_system_prompt:
            parts.append(language_system_prompt)
        return "\n\n".join(part for part in parts if part)

    def _build_task_prompt(self, prompt_kwargs: Dict[str, Any]) -> str:
        if not self._task:
            raise ValueError("No task provided. Set task on Agent or pass task explicitly.")
        return format_agent_task(
            task_template=self._task,
            prompt_kwargs=prompt_kwargs,
            args=self._args,
        )

    def _execute_with_context(
        self,
        prompt_kwargs: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ):
        prompt_kwargs = dict(prompt_kwargs or {})
        execution_context = dict(execution_context or {})
        self._validate_args(**prompt_kwargs)

        prompt_text = self._build_task_prompt(prompt_kwargs)
        system_prompt = self._format_system_prompt(execution_context, prompt_kwargs)
        result = invoke_swarm_sync(
            agent_name=self._name,
            prompt=prompt_text,
            system_prompt=system_prompt,
            model_tier=self._model_tier,
            execution_context={**execution_context, **prompt_kwargs},
        )
        return result.content

    def execute(self, **kwargs):
        return self._execute_with_context(
            prompt_kwargs=kwargs,
            execution_context={},
        )
