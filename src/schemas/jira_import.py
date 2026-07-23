from pydantic import BaseModel
from typing import List, Optional


class ImportProjectFromJiraRequest(BaseModel):
    """
    Request model for importing a Jira project.

    Notes:
    - `project_key` refers to the 3-character Product Planner project key.
    - `jira_project_key` refers to the Jira project key (often longer than 3 chars).
    """

    cloud_id: str
    jira_project_key: str

    name: Optional[str] = None
    description: Optional[str] = None
    project_key: Optional[str] = None

    # Optional: Jira issue types treated as "user stories" (defaults to ["Story"]).
    issue_types: Optional[List[str]] = None


class ListJiraProjectsRequest(BaseModel):
    """Request model for listing Jira projects available through the user's OAuth connection."""

    cloud_id: str
