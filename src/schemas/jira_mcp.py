from typing import Optional

from pydantic import BaseModel, Field


class JiraProjectConfigurationRequest(BaseModel):
    cloud_id: str = Field(min_length=1, max_length=200)
    project_key: str = Field(min_length=1, max_length=100)
