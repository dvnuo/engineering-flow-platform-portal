from app.services.runtime_capability_catalog import (
    build_default_runtime_capability_catalog_provider,
    build_runtime_capability_catalog_provider,
)

RUNTIME_CORE_TOOL_IDS = (
    "apply_patch",
    "bash",
    "edit",
    "glob",
    "grep",
    "invalid",
    "read",
    "skill",
    "task",
    "todowrite",
    "webfetch",
    "write",
)


def test_seed_fallback_resolves_github_create_pull_request_alias():
    provider = build_default_runtime_capability_catalog_provider()
    assert provider.get_catalog_source() == "seed_fallback"
    assert provider.resolve_action_to_capability_id("create_pull_request") == "adapter:github:create_pull_request"


def test_seed_fallback_keeps_existing_github_aliases():
    provider = build_default_runtime_capability_catalog_provider()
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"
    assert provider.resolve_action_to_capability_id("reply_review_comment") == "adapter:github:reply_review_comment"
    assert provider.resolve_action_to_capability_id("add_commit_comment") == "adapter:github:add_commit_comment"
    assert provider.resolve_action_to_capability_id("add_discussion_comment") == "adapter:github:add_discussion_comment"


def test_runtime_snapshot_remains_source_of_truth_over_seed_fallback():
    provider = build_runtime_capability_catalog_provider(
        runtime_catalog_snapshot_payload={
            "catalog_version": "runtime-v1",
            "supports_snapshot_contract": True,
            "capabilities": [
                {
                    "capability_id": "adapter:github:create_pull_request_runtime",
                    "capability_type": "adapter_action",
                    "action_alias": "create_pull_request",
                }
            ],
        }
    )
    assert provider.get_catalog_source() == "runtime_snapshot_payload"
    assert provider.resolve_action_to_capability_id("create_pull_request") == "adapter:github:create_pull_request_runtime"


def test_runtime_core_tool_snapshot_entries_are_accepted_and_resolved():
    provider = build_runtime_capability_catalog_provider(
        runtime_catalog_snapshot_payload={
            "catalog_version": "runtime-single",
            "supports_snapshot_contract": True,
            "capabilities": [
                {
                    "capability_id": tool_id,
                    "capability_type": "tool",
                    "logical_name": tool_id,
                }
                for tool_id in RUNTIME_CORE_TOOL_IDS
            ],
        }
    )

    assert provider.has_full_catalog() is True
    for tool_id in RUNTIME_CORE_TOOL_IDS:
        assert provider.resolve_tool_name_to_capability_id(tool_id) == tool_id
