from __future__ import annotations

from typing import List, Optional

from src.schemas.project_creation import ProjectCreationInitializationData
from src.schemas.user_data import UserData


class ProjectImportOrchestrationError(RuntimeError):
    """Base error for project import orchestration failures."""


class UnknownImportModeError(ProjectImportOrchestrationError):
    """Raised when the requested import mode is not supported."""


class ProjectImportOrchestrator:
    """
    Orchestrates project import flows.

    This class intentionally contains no business logic. It routes the request to the
    corresponding workflow module.
    """

    def __init__(self, import_mode: Optional[str] = None):
        self.import_mode = (import_mode or "").strip().lower()

    async def import_project(
        self,
        *,
        user_data: UserData,
        name: Optional[str] = None,
        description: Optional[str] = None,
        project_key: Optional[str] = None,
        jira_base_url: str,
        jira_email: str,
        jira_api_token: str,
        jira_project_key: str,
        issue_types: Optional[List[str]] = None,
    ) -> ProjectCreationInitializationData:
        if self.import_mode == "jira":
            from src.services.workflows.project_import.project_import_from_jira.initialization import (
                import_project_from_jira,
            )

            return await import_project_from_jira(
                user_data=user_data,
                name=name,
                description=description,
                project_key=project_key,
                jira_base_url=jira_base_url,
                jira_email=jira_email,
                jira_api_token=jira_api_token,
                jira_project_key=jira_project_key,
                issue_types=issue_types,
            )
        raise UnknownImportModeError(f"Unknown import mode: {self.import_mode}")

