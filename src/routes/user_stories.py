
from fastapi import APIRouter, HTTPException, Header, Path, Request
from fastapi.responses import JSONResponse
from typing import Optional

from src.schemas.resources_request import (
    GenerateAnalysisRequest,
    GenerateUserStoriesRequest,
    GetUserStoriesByEpicRequest,
    UpdateUserStoryAssigneeRequest,
    GenerateUserStoryDependenciesRequest
)
from src.schemas.response import ResponseModel
from src.utils.auth import validate_user_and_get_data
from src.utils.user_story_generation import (
    generate_analysis,
    generate_user_stories
)
from src.utils.user_story_dependencies import generate_user_story_dependencies
from src.utils.user_stories import get_user_story_by_id, update_user_story, update_user_story_fields
from src.utils.epics import get_epic_by_id
from src.utils.projects import get_project_by_id
from src.utils.members import get_member_by_id
from src.utils.subtask_generation import (
    generate_subtasks_for_user_story,
    create_subtask_for_user_story,
    get_subtasks_by_user_story,
    update_subtask_status,
    update_subtask_fields,
    delete_subtasks_by_user_story
)

router = APIRouter()

@router.post(
    "/user-story-generation-step-1/",
    response_description="Step 1: Analyzes epic and generates main functionalities and user identification.",
)
async def generate_analysis_route(
    req: GenerateAnalysisRequest,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Step 1 of user story generation: Analyzes the epic and project description to identify:
    - Main functionalities
    - User roles and personas
    - Key workflows
    This prepares the foundation for detailed user story generation.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        print("[DEBUG] Step 1: Generating analysis for user story creation")
        response = await generate_analysis(
            user_data=user_data,
            epic_id=req.epic_id,
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/user-story-generation-step-2/",
    response_description="Step 2: Generates detailed user stories based on analysis from Step 1.",
)
async def generate_user_stories_route(
    req: GenerateUserStoriesRequest,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Step 2 of user story generation: Creates detailed user stories based on:
    - Results from Step 1 analysis
    - Identified users and functionalities
    - Project context and requirements
    This generates the final user stories ready for development.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detaiccl="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        print("[DEBUG] Step 2: Generating detailed user stories")
        response = await generate_user_stories(
            user_data=user_data,
            epic_id=req.epic_id,
            functionality=req.functionality,
            functionalities=req.functionalities,
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/user-story-dependencies/",
    response_description="Generate dependencies between user stories.",
)
async def generate_user_story_dependencies_route(
    req: GenerateUserStoryDependenciesRequest,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Generates dependencies between user stories for an epic.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    if not req.user_stories:
        raise HTTPException(status_code=400, detail="user_stories is required")

    try:
        epic_response = get_epic_by_id(req.epic_id)
        if not epic_response.success:
            raise HTTPException(status_code=404, detail="Epic not found")

        project_response = get_project_by_id(epic_response.data.get("project_id"), user_data.get_user_id())
        if not project_response.success:
            raise HTTPException(status_code=403, detail="Unauthorized: You don't own this project/epic")

        response = await generate_user_story_dependencies(
            user_data=user_data,
            epic_id=req.epic_id,
            user_stories=[story.dict() for story in req.user_stories],
        )
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{story_id}/",
    response_description="Get a user story by ID",
)
async def get_user_story_route(
    story_id: str = Path(..., description="The user story ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Retrieves a single user story by its ID.
    Requires authentication and verifies that the user owns the user story.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        print(f"[DEBUG] Retrieving user story with ID: {story_id}")
        response = get_user_story_by_id(story_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 403,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{story_id}/",
    response_description="Assign or reassign a user story",
)
async def update_user_story_assignee_route(
    story_id: str = Path(..., description="The user story ID"),
    req: UpdateUserStoryAssigneeRequest = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Assigns or reassigns a user story to a team member.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    if not req or (req.assigneeId is None and req.assignee is None):
        raise HTTPException(status_code=400, detail="assigneeId or assignee is required")

    try:
        story_response = get_user_story_by_id(story_id, user_data.get_user_id())
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        update_data = {}
        if req.assigneeId:
            epic_id = story_response.data.get("epic_id")
            epic_response = get_epic_by_id(epic_id) if epic_id else None
            if not epic_response or not epic_response.success:
                raise HTTPException(status_code=404, detail="Epic not found")

            project_id = epic_response.data.get("project_id")
            member = get_member_by_id(project_id, req.assigneeId) if project_id else None
            if not member:
                raise HTTPException(status_code=404, detail="Member not found")

            update_data["assigneeId"] = req.assigneeId
            update_data["assignee"] = member.get("name")
            update_data["assigned_to"] = req.assigneeId
        elif req.assignee:
            update_data["assignee"] = req.assignee

        response = update_user_story(story_id, user_data.get_user_id(), update_data)

        status_code = 200
        if not response.success:
            if "not found" in response.message.lower():
                status_code = 404
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 400

        return JSONResponse(
            status_code=status_code,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{story_id}/assignee/",
    response_description="Assign or reassign a user story",
)
async def update_user_story_assignee_name_route(
    story_id: str = Path(..., description="The user story ID"),
    req: UpdateUserStoryAssigneeRequest = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Assigns or reassigns a user story to a team member via assignee name or ID.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    if not req or (req.assigneeId is None and req.assignee is None):
        raise HTTPException(status_code=400, detail="assigneeId or assignee is required")

    try:
        story_response = get_user_story_by_id(story_id, user_data.get_user_id())
        if not story_response.success:
            status_code = 404 if "not found" in story_response.message.lower() else 403
            return JSONResponse(
                status_code=status_code,
                content=story_response.dict(),
            )

        update_data = {}
        if req.assigneeId:
            epic_id = story_response.data.get("epic_id")
            epic_response = get_epic_by_id(epic_id) if epic_id else None
            if not epic_response or not epic_response.success:
                raise HTTPException(status_code=404, detail="Epic not found")

            project_id = epic_response.data.get("project_id")
            member = get_member_by_id(project_id, req.assigneeId) if project_id else None
            if not member:
                raise HTTPException(status_code=404, detail="Member not found")

            update_data["assigneeId"] = req.assigneeId
            update_data["assignee"] = member.get("name")
            update_data["assigned_to"] = req.assigneeId
        elif req.assignee:
            update_data["assignee"] = req.assignee

        response = update_user_story(story_id, user_data.get_user_id(), update_data)
        if not response.success:
            status_code = 200
            if "not found" in response.message.lower():
                status_code = 404
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 400
            return JSONResponse(
                status_code=status_code,
                content=response.dict(),
            )

        assignee_payload = {
            "id": story_id,
            "assignee": update_data.get("assignee", response.data.get("assignee"))
        }
        if "assigneeId" in update_data or response.data.get("assigneeId"):
            assignee_payload["assigneeId"] = update_data.get("assigneeId", response.data.get("assigneeId"))

        return JSONResponse(
            status_code=200,
            content=ResponseModel(
                success=True,
                message="",
                data=assignee_payload
            ).dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch(
    "/{story_id}/fields/",
    response_description="Update fields of a user story",
)
async def update_user_story_fields_route(
    story_id: str = Path(..., description="The user story ID"),
    request: Request = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Updates fields of a specific user story.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        update_data = await request.json()
        response = update_user_story_fields(story_id, user_data.get_user_id(), update_data)

        if not response.success:
            status_code = 200
            if "not found" in response.message.lower():
                status_code = 404
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 400
            return JSONResponse(
                status_code=status_code,
                content=response.dict(),
            )

        return JSONResponse(
            status_code=200,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{story_id}/subtasks/",
    response_description="Generate subtasks for a user story",
)
async def generate_subtasks_route(
    story_id: str = Path(..., description="The user story ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Generates subtasks for a user story using AI analysis.
    Each subtask includes:
    - order: Sequential number for execution order
    - title: Short, clear title (3-8 words)
    - description: Clear, actionable task description
    - estimated_hours: Time estimate to complete the subtask
    - complexity: Low, Medium, or High
    - dependencies: Array of order numbers of prerequisite subtasks
    
    Requires authentication and verifies that the user owns the user story.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        print(f"[DEBUG] Generating subtasks for user story ID: {story_id}")
        response = await generate_subtasks_for_user_story(user_data, story_id)
        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post(
    "/{story_id}/subtasks-manually/",
    response_description="Create a subtask manually for a user story",
)
async def create_subtask_route(
    story_id: str = Path(..., description="The user story ID"),
    request: Request = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        subtask_data = await request.json()
        response = create_subtask_for_user_story(story_id, user_data.get_user_id(), subtask_data)

        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get(
    "/{story_id}/subtasks/",
    response_description="Get subtasks for a user story",
)
async def get_subtasks_route(
    story_id: str = Path(..., description="The user story ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Retrieves all subtasks for a user story.
    Returns previously generated subtasks ordered by execution sequence with:
    - order: Sequential execution order
    - title: Short task title
    - description: Task description
    - estimated_hours: Time estimate
    - complexity: Low, Medium, or High
    - dependencies: Array of prerequisite subtask order numbers
    
    Requires authentication and verifies that the user owns the user story.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        print(f"[DEBUG] Retrieving subtasks for user story ID: {story_id}")
        response = get_subtasks_by_user_story(story_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{story_id}/subtasks/{subtask_id}/status/",
    response_description="Update the status of a subtask",
)
async def update_subtask_status_route(
    story_id: str = Path(..., description="The user story ID"),
    subtask_id: str = Path(..., description="The subtask ID"),
    request: Request = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Updates the status of a specific subtask within a user story.
    Automatically sets the completion date when status is changed to "Done".
    
    Valid status values:
    - "To Do"
    - "In Progress"
    - "Testing"
    - "Done"
    - "Rework"
    - "Blocked"
    
    Request body:
    {
        "status": "In Progress",
        "completed_date": null  // Optional, auto-set when status is "Done"
    }
    
    Requires authentication and verifies that the user owns the subtask.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        # Parse request body
        body = await request.json()
        status = body.get("status")
        completed_date = body.get("completed_date")
        
        if not status:
            raise HTTPException(status_code=400, detail="Status field is required")
        
        print(f"[DEBUG] Updating subtask {subtask_id} status to: {status}")
        response = update_subtask_status(subtask_id, user_data.get_user_id(), status, completed_date)
        
        status_code = 200
        if not response.success:
            if "not found" in response.message.lower():
                status_code = 404
            elif "invalid status" in response.message.lower():
                status_code = 400
            elif "unauthorized" in response.message.lower():
                status_code = 403
            else:
                status_code = 500
        
        return JSONResponse(
            status_code=status_code,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch(
    "/{story_id}/subtasks/{subtask_id}/fields/",
    response_description="Update fields of a subtask",
)
async def update_subtask_fields_route(
    story_id: str = Path(..., description="The user story ID"),
    subtask_id: str = Path(..., description="The subtask ID"),
    request: Request = None,
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Updates fields of a specific subtask within a user story.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")

    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")

    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        update_data = await request.json()

        response = update_subtask_fields(subtask_id, user_data.get_user_id(), update_data)

        if not response.success:
            raise HTTPException(status_code=400, detail=response.message)

        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete(
    "/{story_id}/subtasks/{subtask_id}/",
    response_description="Delete a subtask",
)
async def delete_subtask_route(
    story_id: str = Path(..., description="The user story ID"),
    subtask_id: str = Path(..., description="The subtask ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> ResponseModel:
    """
    Deletes a specific subtask within a user story.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    print(f"[DEBUG] User data: {user_data}")

    try:
        print(f"[DEBUG] Deleting subtask {subtask_id}")
        response = delete_subtasks_by_user_story(subtask_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 404 if "not found" in response.message.lower() else 400,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))