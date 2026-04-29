from typing import Any, Literal, Optional


WorkflowStatus = Literal["To Do", "In Progress", "In Review", "Stopped", "Done"]

WORKFLOW_STATUS_VALUES = [
    "To Do",
    "In Progress",
    "In Review",
    "Stopped",
    "Done",
]

_WORKFLOW_STATUS_MAP = {
    "todo": "To Do",
    "to do": "To Do",
    "backlog": "To Do",
    "in progress": "In Progress",
    "inprogress": "In Progress",
    "in review": "In Review",
    "inreview": "In Review",
    "testing": "In Review",
    "stopped": "Stopped",
    "blocked": "Stopped",
    "done": "Done",
    "rework": "In Progress",
}


def normalize_workflow_status(status_value: Any) -> Optional[str]:
    if status_value is None:
        return None

    normalized = str(status_value).strip().lower().replace("_", " ")
    return _WORKFLOW_STATUS_MAP.get(normalized)


def coerce_workflow_status(status_value: Any, default: str = "To Do") -> str:
    return normalize_workflow_status(status_value) or default
