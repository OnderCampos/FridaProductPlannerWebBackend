import logging
from typing import Optional

from src.schemas.project_creation import (
    ProjectCreationClarificationData,
    ProjectCreationInitializationData,
)
from src.schemas.user_data import UserData
from src.services.workflows.project_creation.common import create_project_record


async def create_project_by_qa(
    *,
    user_data: UserData,
    name: str,
    description: str,
    project_key: str,
) -> ProjectCreationInitializationData:
    project_record = create_project_record(
        user_data=user_data,
        name=name,
        description=description,
        project_key=project_key,
        creation_status="clarifying",
        creation_source="qa",
    )

    from src.utils.ai.project_creation_qa.clarification import start_clarification

    clarification: Optional[ProjectCreationClarificationData] = None
    try:
        clarification_response = await start_clarification(
            user_data=user_data,
            project_id=project_record.project_id,
            description=description,
        )
        if clarification_response.success and isinstance(clarification_response.data, dict):
            clarification = ProjectCreationClarificationData(**clarification_response.data)
    except Exception as exc:
        logging.warning(f"Failed to start clarification for project {project_record.project_id}: {exc}")

    return ProjectCreationInitializationData(
        project=project_record.project,
        clarification=clarification,
    )
