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


def test_collect_requirements_requires_bundle_template_and_non_empty_sources():
    with pytest.raises(ValueError, match="bundle_template_id is required"):
        build_agent_task_create_payload_from_template(
            "collect_requirements_to_bundle",
            {
                "bundle_ref": {"repo": "a/b", "path": "x", "branch": "main"},
                "manifest_ref": {"repo": "a/b", "path": "x", "branch": "main"},
                "sources": {"jira": ["ABC-1"]},
            },
            "agent-1",
        )

    with pytest.raises(ValueError, match="sources requires at least one non-empty source"):
        build_agent_task_create_payload_from_template(
            "collect_requirements_to_bundle",
            {
                "bundle_template_id": "requirement.v1",
                "bundle_ref": {"repo": "a/b", "path": "x", "branch": "main"},
                "manifest_ref": {"repo": "a/b", "path": "x", "branch": "main"},
                "sources": {"jira": [], "confluence": ["  "], "github_docs": [], "figma": []},
            },
            "agent-1",
        )


def test_collect_requirements_bundle_template_compatibility():
    payload = build_agent_task_create_payload_from_template(
        "collect_requirements_to_bundle",
        {
            "bundle_template_id": "requirement.v1",
            "bundle_ref": {"repo": "a/b", "path": "x", "branch": "main"},
            "manifest_ref": {"repo": "a/b", "path": "x", "branch": "main"},
            "sources": {"jira": ["ABC-1"]},
        },
        "agent-1",
    )
    assert payload["task_type"] == "bundle_action_task"
    assert payload["input_payload_json"]["task_template_id"] == "collect_requirements_to_bundle"
    assert payload["input_payload_json"]["task_type"] == "bundle_action_task"

    with pytest.raises(ValueError, match="not compatible"):
        build_agent_task_create_payload_from_template(
            "collect_requirements_to_bundle",
            {
                "bundle_template_id": "research.v1",
                "bundle_ref": {"repo": "a/b", "path": "x", "branch": "main"},
                "manifest_ref": {"repo": "a/b", "path": "x", "branch": "main"},
                "sources": {"jira": ["ABC-1"]},
            },
            "agent-1",
        )


def test_github_review_required_inputs():
    with pytest.raises(ValueError, match="Missing required template input fields"):
        build_agent_task_create_payload_from_template(
            "github_pr_review",
            {"owner": "acme", "repo": "portal"},
            "agent-1",
        )


def test_github_review_payload_contains_runtime_template_fields():
    payload = build_agent_task_create_payload_from_template(
        "github_pr_review",
        {"owner": "acme", "repo": "portal", "pull_number": 42},
        "agent-1",
    )
    runtime_payload = payload["input_payload_json"]
    assert runtime_payload["task_template_id"] == "github_pr_review"
    assert runtime_payload["task_type"] == "github_review_task"
    assert runtime_payload["trigger"] == "github_pr_review_requested"


def test_github_review_template_defaults_and_optional_inputs():
    template = require_task_template("github_pr_review")
    assert template.task_type == "github_review_task"
    assert template.default_skill_name == "review-pull-request"
    assert "writeback_mode" in template.optional_inputs
    assert "review_event" in template.optional_inputs
    assert "head_sha" in template.optional_inputs
    assert "review_target" in template.optional_inputs
    assert "skill_execution_mode" in template.optional_inputs
