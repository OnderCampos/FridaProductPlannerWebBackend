import logging
from typing import Any, Dict, Optional

from src.schemas.project_creation import (
    ProjectCreationClarificationData,
    ProjectCreationInitializationData,
)
from src.schemas.user_data import UserData
from src.services.workflows.project_creation.common import create_project_record


async def create_project_from_figma(
    *,
    user_data: UserData,
    name: str,
    project_key: str,
    description: str,
    figma_payload: Dict[str, Any],
) -> ProjectCreationInitializationData:
    """
    Creates a project and generates a spec from a Figma payload.
    """
    project_record = create_project_record(
        user_data=user_data,
        name=name,
        description=description,
        project_key=project_key,
        creation_status="spec_generating",
        creation_source="figma",
    )

    from src.utils.ai.project_creation_source_spec import generate_spec_from_source

    clarification: Optional[ProjectCreationClarificationData] = None
    try:
        spec_response = await generate_spec_from_source(
            user_data=user_data,
            project_id=project_record.project_id,
            description=description,
            source_type="figma",
            source_payload=figma_payload,
        )
        if spec_response.success and isinstance(spec_response.data, dict):
            clarification = ProjectCreationClarificationData(**spec_response.data)
    except Exception as exc:
        logging.warning(f"Failed to generate spec from figma for project {project_record.project_id}: {exc}")

    return ProjectCreationInitializationData(
        project=project_record.project,
        clarification=clarification,
    )
