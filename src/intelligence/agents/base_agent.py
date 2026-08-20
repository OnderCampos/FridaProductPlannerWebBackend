import json
import logging
import os
import re
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
# from langchain_openai import AzureChatOpenAI
from langchain_openai import ChatOpenAI

from firebase_admin import firestore
from src.services.setup.firebase_setup import FIRESTORE_CLIENT

from src.services.setup.variables_setup import LLMOPS_API_KEY, FRIDA_API_ENDPOINT, FRIDA_API_KEY, MODEL_PUCK_SWIFT
from src.services.setup.language_setup import build_llm_language_system_prompt, get_default_llm_language, normalize_language
from src.utils.ai.llm_graph import invoke_model_with_graph
from src.utils.ai.llmops_utils import log_to_llmops
from src.utils.core.logging import add_request_log
from src.utils.planning.team import get_team_name

logger = logging.getLogger(__name__)


class BoundAgent:
    def __init__(self, agent: "Agent", context: Optional[Dict[str, Any]] = None):
        self._agent = agent
        self._context = dict(context or {})

    def bind_context(self, context: Optional[Dict[str, Any]] = None) -> "BoundAgent":
        merged_context = dict(self._context)
        if context:
            merged_context.update(context)
        return BoundAgent(self._agent, merged_context)

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
        max_tool_calls: int = 3,
    ):
        self.__name = name
        self.__system_message_str = system_message
        self.__task = task
        self.__args = list(args) if args else None
        # self.__azure_deployment = (
        #     azure_deployment
        #     or os.getenv("AZURE_DEPLOYMENT")
        #     or os.getenv("AZURE_DEPLOYMENT_MINI")
        # )

        # self.__fallback_deployment = (
        #     azure_deployment
        #     or MODEL_PUCK_SWIFT
        #     or "PUCK-SWIFT"
        # )
        # self.__azure_deployment = None

        raw_fallback = azure_deployment or MODEL_PUCK_SWIFT or "PUCK-SWIFT"
        self.__fallback_deployment = str(raw_fallback).replace("_", "-")
        self.__azure_deployment = None

        self.__api_version = os.getenv("API_VERSION")
        self.__tools = list(tools) if tools else []
        self.__tool_handlers = dict(tool_handlers) if tool_handlers else {}
        self.__tool_choice = tool_choice
        self.__max_tool_calls = max(0, int(max_tool_calls))

        if self.__args and len(set(self.__args)) != len(self.__args):
            raise ValueError("Agent args list must not contain duplicates.")
        if self.__tools:
            self.__validate_tools_config()
        # self.__create_agent()

    def __get_active_model_from_firebase(self, fallback: str) -> str:
        try:
            doc_ref = FIRESTORE_CLIENT.collection("llm_settings").document("ai_settings")
            doc = doc_ref.get()
            
            if doc.exists:
                return doc.to_dict().get("active_model_id", fallback)
            return fallback
        except Exception as e:
            logger.warning(f"Error fetching model from Firebase, using fallback: {e}")
            return fallback

    def __validate_tools_config(self):
        tool_names = []
        for tool in self.__tools:
            if not isinstance(tool, dict):
                raise ValueError(
                    f"Invalid tool schema for {self.__name}: expected dict."
                )
            function = tool.get("function", {})
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"Invalid tool schema for {self.__name}: missing function.name."
                )
            tool_names.append(name)

        duplicated_names = sorted({name for name in tool_names if tool_names.count(name) > 1})
        if duplicated_names:
            raise ValueError(
                f"Tool schemas for {self.__name} contain duplicated names: {', '.join(duplicated_names)}"
            )

        missing_handlers = [name for name in tool_names if name not in self.__tool_handlers]
        if missing_handlers:
            raise ValueError(
                f"Missing tool handlers for {self.__name}: {', '.join(sorted(missing_handlers))}"
            )

    # def __create_agent(self):
    #     # self.agent = AzureChatOpenAI(
    #     #     azure_deployment=self.__azure_deployment,
    #     #     api_version=self.__api_version,
    #     # )
    #     self.agent = ChatOpenAI(
    #         model=self.__azure_deployment,
    #         base_url=FRIDA_API_ENDPOINT,
    #         api_key=FRIDA_API_KEY
    #     )
    #     self.agent_with_tools = (
    #         self.agent.bind_tools(self.__tools, tool_choice=self.__tool_choice)
    #         if self.__tools
    #         else self.agent
    #     )
    #     self.__system_message = (
    #         SystemMessage(content=self.__system_message_str)
    #         if self.__system_message_str
    #         else None
    #     )

    def __format_value(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if value is None:
            return ""
        return str(value)

    def build_prompt(self, **kwargs) -> str:
        lines = []
        if self.__args:
            for key in self.__args:
                value = self.__format_value(kwargs.get(key))
                lines.append(f"{key}: {value}")
            return "\n".join(lines)

        for key, value in kwargs.items():
            lines.append(f"{key}: {self.__format_value(value)}")
        return "\n".join(lines)

    def __validate_args(self, **kwargs):
        if not self.__args:
            return

        expected = set(self.__args)
        provided = set(kwargs.keys())
        missing = expected - provided
        extra = provided - expected
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing: {', '.join(sorted(missing))}")
            if extra:
                details.append(f"extra: {', '.join(sorted(extra))}")
            raise ValueError(f"Invalid args for {self.__name}. " + "; ".join(details))

    def __parse_tool_args(self, args: Any) -> Dict[str, Any]:
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def __normalize_tool_output(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload if payload is not None else {}, ensure_ascii=False)
        except Exception:
            return str(payload)

    def __execute_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        execution_kwargs: Dict[str, Any],
    ) -> Any:
        handler = self.__tool_handlers.get(tool_name)
        if handler is None:
            return {"error": f"No handler configured for tool '{tool_name}'."}
        try:
            return handler(tool_args, execution_kwargs)
        except TypeError:
            return handler(tool_args)

    # def __invoke(self, messages: List[Any], use_tools: bool = False, execution_kwargs: Optional[Dict[str, Any]] = None):
    #     client = self.agent_with_tools if use_tools else self.agent

    #     if execution_kwargs:
    #         user_id, _, email, _ = self.__extract_user_context(execution_kwargs)
    #         if user_id or email:
    #             client = client.bind(
    #                 extra_body={
    #                     "email": email or "unknown_email",
    #                     "user_id": user_id or "unknown_user"
    #                 }
    #             )

    #     return invoke_model_with_graph(client=client, messages=messages)

    def __invoke(self, messages: List[Any], use_tools: bool = False, execution_kwargs: Optional[Dict[str, Any]] = None):
        # Recuperamos el modelo activo
        active_model = execution_kwargs.get("active_model", self.__fallback_deployment) if execution_kwargs else self.__fallback_deployment

        print(F"[BASE_AGENT] MODELO USADO DE FIREBASE: {active_model}")

        # Preparamos el extra_body seguro
        extra_body_data = None
        if execution_kwargs:
            user_id, _, email, _ = self.__extract_user_context(execution_kwargs)
            print(F"[BASE_AGENT] USER EMAIL: {email} Y USER ID: {user_id}")
            if user_id or email:
                extra_body_data = {
                    "email": email or "unknown_email",
                    "user_id": user_id or "unknown_user"
                }

        # Creamos el cliente de IA inyectando el extra_body
        base_client = ChatOpenAI(
            model=active_model,
            base_url=FRIDA_API_ENDPOINT,
            api_key=FRIDA_API_KEY,
            extra_body=extra_body_data
        )

        # Atamos las herramientas si se requieren
        client = (
            base_client.bind_tools(self.__tools, tool_choice=self.__tool_choice)
            if use_tools and self.__tools
            else base_client
        )

        return invoke_model_with_graph(client=client, messages=messages)

    def __run_with_tools(
        self,
        messages: List[Any],
        execution_kwargs: Dict[str, Any],
    ):
        if not self.__tools or self.__max_tool_calls <= 0:
            return self.__invoke(messages=messages, use_tools=False, execution_kwargs=execution_kwargs)

        iterations = 0
        while iterations < self.__max_tool_calls:
            response = self.__invoke(messages=messages, use_tools=True, execution_kwargs=execution_kwargs)
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                return response

            for tool_call in tool_calls:
                tool_name = tool_call.get("name")
                tool_id = tool_call.get("id")
                parsed_args = self.__parse_tool_args(tool_call.get("args"))
                tool_output = self.__execute_tool(
                    tool_name=tool_name,
                    tool_args=parsed_args,
                    execution_kwargs=execution_kwargs,
                )
                messages.append(
                    ToolMessage(
                        content=self.__normalize_tool_output(tool_output),
                        tool_call_id=tool_id,
                        name=tool_name,
                    )
                )
            iterations += 1

        return self.__invoke(messages=messages, use_tools=True, execution_kwargs=execution_kwargs)

    def bind_context(self, context: Optional[Dict[str, Any]] = None) -> BoundAgent:
        return BoundAgent(self, context)

    def __extract_user_context(
        self, execution_context: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        user_data = execution_context.get("user_data")
        if user_data is not None:
            try:
                user_id = user_data.get_user_id()
                team_id = user_data.get_team_id()
                email = user_data.get_email()
                user_name = user_data.get_user_name()
                return user_id, team_id, email, user_name
            except Exception:
                pass

        return (
            execution_context.get("user_id"),
            execution_context.get("team_id"),
            execution_context.get("email"),
            execution_context.get("user_name"),
        )

    def __to_text(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload)

    def __log_request(
        self,
        messages: List[Any],
        response: Any,
        call_start_ns: int,
        end_time: datetime,
        elapsed_s: float,
        execution_context: Dict[str, Any],
    ):
        metadata = getattr(response, "response_metadata", {}) or {}
        token_usage = metadata.get("token_usage", {}) if isinstance(metadata, dict) else {}
        prompt_tokens = int(token_usage.get("prompt_tokens") or 0)
        completion_tokens = int(token_usage.get("completion_tokens") or 0)

        user_id, team_id, email, _user_name = self.__extract_user_context(execution_context)
        if user_id:
            try:
                add_request_log(
                    user_id=user_id,
                    team_id=team_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except Exception:
                logger.warning("Error saving request log for agent call.", exc_info=True)

        if not LLMOPS_API_KEY:
            return

        team_name = "Unknown Team"
        if team_id:
            try:
                team_name = get_team_name(team_id) or "Unknown Team"
            except Exception:
                team_name = "Unknown Team"

        prompt_text = "\n".join(self.__to_text(getattr(msg, "content", msg)) for msg in messages)
        response_text = self.__to_text(getattr(response, "content", response))

        # try:
        #     log_to_llmops(
        #         prompt=prompt_text,
        #         response=response_text,
        #         prompt_tokens=prompt_tokens,
        #         completion_tokens=completion_tokens,
        #         total_tokens=prompt_tokens + completion_tokens,
        #         call_start=call_start_ns,
        #         created=end_time.isoformat(),
        #         uid=user_id or "",
        #         api_key=LLMOPS_API_KEY,
        #         # model=self.__azure_deployment,
        #         model=execution_context.get("active_model", self.__fallback_deployment),
        #         additional_kwargs={
        #             "agent_name": self.__name,
        #             "team_name": team_name,
        #             "duration_ms": int(elapsed_s * 1000),
        #         },
        #         email=email or "unknown_email",
        #         status="success",
        #         team_id=team_id,
        #     )
        # except Exception:
        #     logger.warning("Error logging agent call to LLMOPS.", exc_info=True)

    def _execute_with_context(
        self,
        prompt_kwargs: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ):
        # self.__azure_deployment = self.__get_active_model_from_firebase(self.__fallback_deployment)

        # self.__create_agent()

        active_model = self.__get_active_model_from_firebase(self.__fallback_deployment)

        prompt_kwargs = dict(prompt_kwargs or {})
        execution_context = dict(execution_context or {})

        messages: List[Any] = []
        # if self.__system_message:
        #     messages.append(self.__system_message)

        system_message_obj = (
            SystemMessage(content=self.__system_message_str)
            if self.__system_message_str
            else None
        )
        if system_message_obj:
            messages.append(system_message_obj)

        language_override = None
        if isinstance(prompt_kwargs.get("language"), str) and prompt_kwargs["language"].strip():
            language_override = prompt_kwargs["language"]
        elif isinstance(execution_context.get("language"), str) and execution_context["language"].strip():
            language_override = execution_context["language"]
        elif isinstance(execution_context.get("llm_language"), str) and execution_context["llm_language"].strip():
            language_override = execution_context["llm_language"]

        effective_language = normalize_language(language_override, default=get_default_llm_language())
        language_system_prompt = build_llm_language_system_prompt(effective_language)
        if language_system_prompt:
            messages.append(SystemMessage(content=language_system_prompt))

        self.__validate_args(**prompt_kwargs)
        if not self.__task:
            raise ValueError("No task provided. Set task on Agent or pass task explicitly.")

        task_text = self.__task
        replaced_any = False
        format_keys = self.__args or list(prompt_kwargs.keys())
        for key in format_keys:
            placeholder = "{" + key + "}"
            if placeholder in task_text:
                task_text = task_text.replace(
                    placeholder,
                    self.__format_value(prompt_kwargs.get(key)),
                )
                replaced_any = True

        unresolved = set(re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*)}", task_text))
        if unresolved:
            raise ValueError(
                f"Unresolved task placeholders for {self.__name}: {', '.join(sorted(unresolved))}"
            )

        if not replaced_any:
            prompt_text = self.build_prompt(**prompt_kwargs) if prompt_kwargs else ""
            if not prompt_text:
                raise ValueError("No prompt content provided. Pass keyword args to execute().")
            task_text = f"{task_text}\n{prompt_text}"

        messages.append(HumanMessage(content=[{"type": "text", "text": task_text}]))

        combined_execution_kwargs = dict(prompt_kwargs)
        combined_execution_kwargs.update(execution_context)
        combined_execution_kwargs["active_model"] = active_model

        global_start = perf_counter_ns()
        start_time = datetime.now(timezone.utc)
        response = self.__run_with_tools(
            messages=messages,
            execution_kwargs=combined_execution_kwargs,
        )
        end_time = datetime.now(timezone.utc)
        elapsed_s = (end_time - start_time).total_seconds()
        print(f"[AGENT] {end_time.isoformat()} | {self.__name} responded in {elapsed_s:.2f}s")

        self.__log_request(
            messages=messages,
            response=response,
            call_start_ns=global_start,
            end_time=end_time,
            elapsed_s=elapsed_s,
            execution_context=combined_execution_kwargs,
        )
        return response.content

    def execute(self, **kwargs):
        return self._execute_with_context(
            prompt_kwargs=kwargs,
            execution_context={},
        )
