import pytest

from app.services.task_template_registry import (
    build_agent_task_create_payload_from_template,
    list_task_templates,
    require_task_template,
)


def test_registry_templates_and_require_unknown():
    templates = list_task_templates()
    assert "github_comment_mention" in {t.template_id for t in templates}
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
    assert runtime_payload["execution_mode"] == "chat_tool_loop"


def test_github_comment_mention_payload_contains_runtime_template_fields():
    payload = build_agent_task_create_payload_from_template(
        "github_comment_mention",
        {"owner": "acme", "repo": "portal", "comment_id": 1, "comment_kind": "issue_comment", "body": "@efp-agent hi", "mentioned_account": "efp-agent"},
        "agent-1",
    )
    runtime_payload = payload["input_payload_json"]
    assert payload["task_type"] == "triggered_event_task"
    assert payload["trigger"] == "github_comment_mention"
    assert runtime_payload["source_kind"] == "github.mention"
    assert runtime_payload["skill_name"] == "handle-triggered-event"
    assert runtime_payload["execution_mode"] == "chat_tool_loop"
    assert runtime_payload["reply_mode"] == "same_surface"


def test_github_comment_mention_template_supports_commit_fields():
    template = require_task_template("github_comment_mention")
    assert "commit_id" in template.optional_inputs
    assert "commit_sha" in template.optional_inputs
    assert "position" in template.optional_inputs
