from __future__ import annotations

from typing import Any, Dict, Optional

from src.schemas.project_creation import ProjectCreationInitializationData
from src.schemas.project_finalization import FinalizeProjectCreationData
from src.schemas.user_data import UserData
from src.services.workflows.project_creation.finalization import (
    finalize_project_creation,
)

class ProjectOrchestrationError(RuntimeError):
    """Base error for orchestration failures."""


class UnknownCreationModeError(ProjectOrchestrationError):
    """Raised when the orchestration mode is not supported."""


class MissingFileBytesError(ProjectOrchestrationError):
    """Raised when file initialization is requested without file bytes."""


class MissingFigmaPayloadError(ProjectOrchestrationError):
    """Raised when figma initialization is requested without payload."""


class ProjectCreationOrchestrator:
    """
    Orchestrates the project creation flows.

    This class intentionally contains *no* business logic. It routes the request to the
    corresponding workflow module.
    """

    def __init__(self, creation_mode: Optional[str] = None):
        self.creation_mode = (creation_mode or "").strip().lower()

    async def initialize_project(
        self,
        *,
        user_data: UserData,
        name: str,
        project_key: str,
        description: str = "",
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        figma_payload: Optional[Dict[str, Any]] = None,
    ) -> ProjectCreationInitializationData:
        if self.creation_mode == "file":
            return await self._init_from_file(
                user_data=user_data,
                name=name,
                description=description,
                project_key=project_key,
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
            )
        if self.creation_mode == "qa":
            return await self._init_from_qa(
                user_data=user_data,
                name=name,
                description=description,
                project_key=project_key,
            )
        if self.creation_mode == "figma":
            return await self._init_from_figma(
                user_data=user_data,
                name=name,
                description=description,
                project_key=project_key,
                figma_payload=figma_payload,
            )
        raise UnknownCreationModeError(f"Unknown creation mode: {self.creation_mode}")

    async def _init_from_file(
        self,
        *,
        user_data: UserData,
        name: str,
        project_key: str,
        description: str,
        file_bytes: Optional[bytes],
        filename: Optional[str],
        content_type: Optional[str],
    ) -> ProjectCreationInitializationData:
        if file_bytes is None:
            raise MissingFileBytesError("file is required")

        from src.services.workflows.project_creation.project_creation_by_document.initialization import (
            create_project_from_file_upload,
        )

        return await create_project_from_file_upload(
            user_data=user_data,
            name=name,
            description=description or "",
            project_key=project_key,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
        )

    async def _init_from_qa(
        self,
        *,
        user_data: UserData,
        name: str,
        project_key: str,
        description: str,
    ) -> ProjectCreationInitializationData:
        from src.services.workflows.project_creation.project_creation_by_qa.initialization import (
            create_project_by_qa,
        )

        return await create_project_by_qa(
            user_data=user_data,
            name=name,
            description=description or "",
            project_key=project_key,
        )

    async def _init_from_figma(
        self,
        *,
        user_data: UserData,
        name: str,
        project_key: str,
        description: str,
        figma_payload: Optional[Dict[str, Any]],
    ) -> ProjectCreationInitializationData:
        if not figma_payload:
            raise MissingFigmaPayloadError("figma_payload is required")

        from src.services.workflows.project_creation.project_creation_by_figma.initialization import (
            create_project_from_figma,
        )

        return await create_project_from_figma(
            user_data=user_data,
            name=name,
            project_key=project_key,
            description=description or "",
            figma_payload=figma_payload,
        )

    async def complete(
        self,
        *,
        user_data: UserData,
        project_id: str,
        spec_text_override: Optional[str] = None,
    ) -> FinalizeProjectCreationData:
        return await finalize_project_creation(
            user_data=user_data,
            project_id=project_id,
            spec_text_override=spec_text_override,
        )
