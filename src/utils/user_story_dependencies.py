import json
import logging
from typing import Any, Dict, List

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.azure_services import AzureChatService
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.prompts.user_story_dependencies import GENERATE_USER_STORY_DEPENDENCIES_PROMPT


def _build_valid_story_ids(user_stories: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    valid_ids: Dict[str, Dict[str, Any]] = {}
    for story in user_stories:
        story_id = story.get("id")
        user_story_id = story.get("user_story_id")
        if story_id:
            valid_ids[str(story_id)] = story
        if user_story_id:
            valid_ids[str(user_story_id)] = story
    return valid_ids


def _normalize_dependencies(
    dependencies: List[Dict[str, Any]],
    valid_ids: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for dep in dependencies:
        story_id = dep.get("story_id")
        if not story_id or str(story_id) not in valid_ids:
            continue
        depends_on = dep.get("depends_on", [])
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        if not isinstance(depends_on, list):
            depends_on = []

        story_id_str = str(story_id)
        filtered_depends_on = [
            str(item) for item in depends_on
            if str(item) in valid_ids and str(item) != story_id_str
        ]

        normalized.append(
            {
                "story_id": story_id_str,
                "depends_on": filtered_depends_on,
            }
        )
    return normalized


async def generate_user_story_dependencies(
    user_data: UserData,
    epic_id: str,
    user_stories: List[Dict[str, Any]],
) -> ResponseModel:
    try:
        valid_ids = _build_valid_story_ids(user_stories)
        if not valid_ids:
            return ResponseModel(
                success=False,
                message="No valid user story identifiers provided",
                data=None,
            )

        prompt = GENERATE_USER_STORY_DEPENDENCIES_PROMPT.format(
            epic_id=epic_id,
            user_stories_json=json.dumps(user_stories),
        )

        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        dependencies_response = await azure_services.completion_without_knowledge_base(
            prompt,
            key="dependencies",
            expected_keys=["story_id", "depends_on"],
        )

        if dependencies_response is None:
            return ResponseModel(
                success=False,
                message="Failed to generate dependencies",
                data=None,
            )

        normalized = _normalize_dependencies(dependencies_response, valid_ids)

        return ResponseModel(
            success=True,
            message="Dependencies generated",
            data={"dependencies": normalized},
        )
    except Exception as e:
        logging.error(f"Error generating user story dependencies: {e}")
        return ResponseModel(
            success=False,
            message=f"Error generating dependencies: {str(e)}",
            data=None,
        )
