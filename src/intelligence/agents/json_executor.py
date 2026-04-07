import json
from typing import Any, Dict, List, Optional

from src.intelligence.agents.base_agent import Agent
from src.utils.core.validation_utils import get_code_block, has_expected_epic_structure


def parse_json_response(response_content: Any) -> Optional[Any]:
    if isinstance(response_content, (dict, list)):
        return response_content

    text = response_content if isinstance(response_content, str) else str(response_content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        code_block = get_code_block(text)
        if not code_block:
            return None
        try:
            return json.loads(code_block)
        except json.JSONDecodeError:
            return None


def execute_json_agent(
    agent: Agent,
    prompt_kwargs: Dict[str, Any],
    key: Optional[str] = None,
    attempts: int = 3,
    expected_keys: Optional[List[str]] = None,
    return_full_response: bool = False,
    context: Optional[Dict[str, Any]] = None,
):
    expected_keys = expected_keys or []
    context = context or {}
    max_attempts = max(1, int(attempts))

    bound_agent = agent.bind_context(context)
    for _ in range(max_attempts):
        raw_response = bound_agent.execute(**prompt_kwargs)
        parsed_response = parse_json_response(raw_response)
        if parsed_response is None:
            continue

        if key is None:
            if expected_keys:
                if has_expected_epic_structure(parsed_response, expected_keys):
                    return parsed_response
                continue
            return parsed_response

        if not isinstance(parsed_response, dict):
            continue
        objects = parsed_response.get(key)
        if objects is None:
            continue

        if not isinstance(objects, list):
            continue

        if expected_keys:
            good_responses = [
                obj for obj in objects
                if has_expected_epic_structure(obj, expected_keys)
            ]
        else:
            good_responses = objects

        if not good_responses:
            continue

        if return_full_response:
            parsed_response[key] = good_responses
            return parsed_response
        return good_responses

    return None
