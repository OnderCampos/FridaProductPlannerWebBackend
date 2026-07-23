import json
import logging
from typing import Any, Dict, List

from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.intelligence.runtime import AgentName, run_agent
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.prompts.user_story_dependencies import GENERATE_USER_STORY_DEPENDENCIES_PROMPT
from src.utils.core.validation_utils import get_code_block


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


def _canonical_story_identifier(story: Dict[str, Any]) -> str:
    return str(story.get("user_story_id") or story.get("id") or "").strip()


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
        canonical_story_id = _canonical_story_identifier(valid_ids.get(story_id_str, {})) or story_id_str
        filtered_depends_on = [
            _canonical_story_identifier(valid_ids.get(str(item), {})) or str(item)
            for item in depends_on
            if str(item) in valid_ids and str(item) != story_id_str
        ]
        filtered_depends_on = list(dict.fromkeys([
            item for item in filtered_depends_on if item and item != canonical_story_id
        ]))

        normalized.append(
            {
                "story_id": canonical_story_id,
                "depends_on": filtered_depends_on,
            }
        )
    return normalized


def attach_story_dependencies(
    user_stories: List[Dict[str, Any]],
    dependencies: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not isinstance(user_stories, list) or not isinstance(dependencies, list):
        return user_stories

    dep_map: Dict[str, List[str]] = {}
    for item in dependencies:
        story_id = str(item.get("story_id") or "").strip()
        if story_id:
            dep_map[story_id] = item.get("depends_on", []) or []

    merged: List[Dict[str, Any]] = []
    for story in user_stories:
        if not isinstance(story, dict):
            merged.append(story)
            continue

        story_id = str(story.get("id") or "").strip()
        user_story_id = str(story.get("user_story_id") or "").strip()
        depends_on = dep_map.get(user_story_id) or dep_map.get(story_id) or story.get("dependencies") or []
        next_story = dict(story)
        next_story["dependencies"] = depends_on
        merged.append(next_story)

    return merged


def _persist_dependencies_to_firestore(
    user_stories: List[Dict[str, Any]],
    dependencies: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    updated_stories = attach_story_dependencies(user_stories, dependencies)
    story_ref_by_identifier: Dict[str, Any] = {}

    for story in updated_stories:
        story_doc_id = str(story.get("id") or "").strip()
        if not story_doc_id:
            continue

        user_story_id = str(story.get("user_story_id") or "").strip()
        story_ref = FIRESTORE_CLIENT.collection("user_stories").document(story_doc_id)
        story_ref_by_identifier[story_doc_id] = story_ref
        if user_story_id:
            story_ref_by_identifier[user_story_id] = story_ref

    for item in dependencies:
        story_identifier = str(item.get("story_id") or "").strip()
        if not story_identifier:
            continue

        story_ref = story_ref_by_identifier.get(story_identifier)
        if not story_ref:
            continue

        story_ref.update({"dependencies": item.get("depends_on", []) or []})

    return updated_stories


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

        raw_response = await run_agent(
            AgentName.DEPENDENCY_ANALYSIS,
            prompt,
            user_data,
            model_tier="mini",
        )
        parsed_response = json.loads(get_code_block(raw_response) or raw_response)
        dependencies_response = (
            parsed_response.get("dependencies")
            if isinstance(parsed_response, dict)
            else parsed_response
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


async def generate_and_persist_user_story_dependencies(
    user_data: UserData,
    epic_id: str,
    user_stories: List[Dict[str, Any]],
) -> ResponseModel:
    try:
        stories = [story for story in (user_stories or []) if isinstance(story, dict)]
        if len(stories) < 2:
            return ResponseModel(
                success=True,
                message="Not enough user stories to generate dependencies",
                data={"user_stories": stories, "dependencies": []},
            )

        dependencies_response = await generate_user_story_dependencies(
            user_data=user_data,
            epic_id=epic_id,
            user_stories=stories,
        )
        if not dependencies_response.success:
            return dependencies_response

        dependencies = (
            dependencies_response.data.get("dependencies", [])
            if isinstance(dependencies_response.data, dict)
            else []
        )
        updated_stories = _persist_dependencies_to_firestore(stories, dependencies)

        return ResponseModel(
            success=True,
            message="Dependencies generated and persisted",
            data={"user_stories": updated_stories, "dependencies": dependencies},
        )
    except Exception as e:
        logging.error(f"Error persisting dependencies for epic {epic_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error persisting dependencies: {str(e)}",
            data=None,
        )
