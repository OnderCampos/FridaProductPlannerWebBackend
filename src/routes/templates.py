from fastapi import APIRouter, HTTPException, Header, Path
from fastapi.responses import JSONResponse
from typing import Optional

from src.schemas.resources_request import (
    CreateTemplateRequest,
    UpdateTemplateRequest
)
from src.schemas.resources_response import TemplateResponse

from src.utils.auth import validate_user_and_get_data

from src.utils.templates import (
    get_all_templates_by_project,
    get_selected_template_by_project,
    create_template,
    update_template,
    delete_template,
    set_selected_template
)

router = APIRouter()


@router.get(
    "/{project_id}/templates",
    response_description="Get all templates for the project.",
)
async def get_all_templates_route(
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> TemplateResponse:
    """
    Retrieves all templates for the authenticated user and project.
    
    Args:
        project_id (str): The project ID
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: List of all templates for the project
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    
    try:
        response = get_all_templates_by_project(project_id, user_data.get_user_id())
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{project_id}/templates/selected",
    response_description="Get the selected template for the project.",
)
async def get_selected_template_route(
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> TemplateResponse:
    """
    Retrieves the selected template for the authenticated user and project.
    
    Args:
        project_id (str): The project ID
        authorization (str): Authorization header with Bearer token
    
    Returns:
        TemplateResponse: The selected template data
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    
    try:
        response = get_selected_template_by_project(project_id, user_data.user_id)
        return JSONResponse(
            status_code=200 if response.success else 404,
            content=response.dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{project_id}/templates",
    response_description="Create a new template.",
)
async def create_template_route(
    req: CreateTemplateRequest,
    project_id: str = Path(..., description="The project ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
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
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put(
    "/{project_id}/templates/{template_id}",
    response_description="Update an existing template.",
)
async def update_template_route(
    req: UpdateTemplateRequest,
    project_id: str = Path(..., description="The project ID"),
    template_id: str = Path(..., description="The template ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
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
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{project_id}/templates/{template_id}",
    response_description="Delete a template.",
)
async def delete_template_route(
    project_id: str = Path(..., description="The project ID"),
    template_id: str = Path(..., description="The template ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
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
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{project_id}/templates/{template_id}/select",
    response_description="Set a template as selected for the project.",
)
async def set_selected_template_route(
    project_id: str = Path(..., description="The project ID"),
    template_id: str = Path(..., description="The template ID"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
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
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    try:
        token_type, token = authorization.split(" ", 1)
        if token_type.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    user_data = validate_user_and_get_data(token)
    
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
