import pytest

from app.services.task_template_registry import (
    build_agent_task_create_payload_from_template,
    list_task_templates,
    require_task_template,
)


def test_registry_templates_and_require_unknown():
    templates = list_task_templates()
    assert len(templates) == 6
    with pytest.raises(ValueError):
        require_task_template("unknown")


def test_build_bundle_payload_defaults():
    payload = build_agent_task_create_payload_from_template(
        "collect_requirements_to_bundle",
        {
            "bundle_ref": {"repo": "a/b", "path": "x", "branch": "main"},
            "manifest_ref": {"repo": "a/b", "path": "x", "branch": "main"},
            "sources": {"jira": ["A-1"], "confluence": [], "github_docs": [], "figma": []},
        },
        "agent-1",
    )
    assert payload["task_type"] == "bundle_action_task"
    assert payload["input_payload_json"]["skill_name"] == "collect_requirements_to_bundle"


def test_build_github_review_payload_defaults():
    payload = build_agent_task_create_payload_from_template(
        "github_pr_review",
        {"owner": "acme", "repo": "portal", "pull_number": 1},
        "agent-1",
    )
    assert payload["task_type"] == "github_review_task"
