from src.schemas.response import ResponseModel
from src.schemas.user_data import UserData
from src.services.workflows.project_creation.project_creation_by_qa.initialization import (
    create_project_by_qa as _workflow_create_project_by_qa,
)


async def create_project_by_qa(
    user_data: UserData,
    name: str,
    description: str,
    project_key: str,
) -> ResponseModel:
    return await _workflow_create_project_by_qa(
        user_data=user_data,
        name=name,
        description=description,
        project_key=project_key,
    )
