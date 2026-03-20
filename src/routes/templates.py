from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from typing import Optional
import logging

from src.schemas.resources_request import (
    CreateTemplateRequest,
    UpdateTemplateRequest
)
from src.schemas.resources_response import TemplateResponse

from src.schemas.user_data import UserData
from src.utils.authz.auth import get_current_user

from src.utils.planning.templates import (
    get_all_templates_by_project,
    get_selected_template_by_project,
    create_template,
    update_template,
    delete_template,
    set_selected_template
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/{project_id}/templates",
    response_description="Get all templates for the project.",
)
async def get_all_templates_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
) -> TemplateResponse:
    """
    Retrieves all templates for the authenticated user and project.
    
    Args:
        project_id (str): The project ID
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: List of all templates for the project
    """
    try:
        response = get_all_templates_by_project(project_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get templates")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{project_id}/templates/selected",
    response_description="Get the selected template for the project.",
)
async def get_selected_template_route(
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
) -> TemplateResponse:
    """
    Retrieves the selected template for the authenticated user and project.
    
    Args:
        project_id (str): The project ID
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: The selected template data
    """
    try:
        response = get_selected_template_by_project(project_id, user_data.user_id)
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get selected template")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/templates",
    response_description="Create a new template.",
)
async def create_template_route(
    req: CreateTemplateRequest,
    project_id: str = Path(..., description="The project ID"),
    user_data: UserData = Depends(get_current_user),
) -> TemplateResponse:
    """
    Creates a new template for the authenticated user and project.
    
    Args:
        req (CreateTemplateRequest): Template creation request with language and fields
        project_id (str): The project ID
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: The created template data
    """
    try:
        response = create_template(
            project_id=project_id,
            user_id=user_data.get_user_id(),
            name=req.name,
            language=req.language,
            fields=req.fields
        )
        return JSONResponse(
            status_code=201 if response.success else 400,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to create template")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/{project_id}/templates/{template_id}",
    response_description="Update an existing template.",
)
async def update_template_route(
    req: UpdateTemplateRequest,
    project_id: str = Path(..., description="The project ID"),
    template_id: str = Path(..., description="The template ID"),
    user_data: UserData = Depends(get_current_user),
) -> TemplateResponse:
    """
    Updates an existing template. User must have access to the project.
    
    Args:
        req (UpdateTemplateRequest): Template update request with optional language and fields
        project_id (str): The project ID
        template_id (str): The template ID to update
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: The updated template data
    """
    try:
        response = update_template(
            template_id=template_id,
            project_id=project_id,
            user_id=user_data.user_id,
            name=req.name,
            language=req.language,
            fields=req.fields
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update template")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{project_id}/templates/{template_id}",
    response_description="Delete a template.",
)
async def delete_template_route(
    project_id: str = Path(..., description="The project ID"),
    template_id: str = Path(..., description="The template ID"),
    user_data: UserData = Depends(get_current_user),
) -> TemplateResponse:
    """
    Deletes a template. User must have access to the project.
    
    Args:
        project_id (str): The project ID
        template_id (str): The template ID to delete
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: Confirmation of deletion
    """
    try:
        response = delete_template(
            template_id=template_id,
            project_id=project_id,
            user_id=user_data.user_id
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete template")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{project_id}/templates/{template_id}/select",
    response_description="Set a template as selected for the project.",
)
async def set_selected_template_route(
    project_id: str = Path(..., description="The project ID"),
    template_id: str = Path(..., description="The template ID"),
    user_data: UserData = Depends(get_current_user),
) -> TemplateResponse:
    """
    Sets a specific template as the selected template for the project.
    
    Args:
        project_id (str): The project ID
        template_id (str): The template ID to select
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: Confirmation of selection
    """
    try:
        response = set_selected_template(
            project_id=project_id,
            user_id=user_data.get_user_id(),
            template_id=template_id
        )
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to set selected template")
        raise HTTPException(status_code=500, detail="Internal server error")

