from src.schemas.workflow_status import coerce_workflow_status, normalize_workflow_status
from src.utils.planning.user_stories import _normalize_story_payload


def test_workflow_status_normalizes_all_legacy_values():
    assert normalize_workflow_status("Testing") == "In Review"
    assert normalize_workflow_status("Blocked") == "Stopped"
    assert normalize_workflow_status("Rework") == "In Progress"
    assert normalize_workflow_status("Completed") == "Done"


def test_workflow_status_uses_to_do_for_unknown_or_missing_values():
    assert coerce_workflow_status(None) == "To Do"
    assert coerce_workflow_status("Unknown") == "To Do"


def test_user_story_payload_promotes_and_normalizes_legacy_status_fields():
    story = _normalize_story_payload({"fields": [{"key": "status", "value": "Testing"}]})

    assert story["status"] == "In Review"
