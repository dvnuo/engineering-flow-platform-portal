from app.services.capability_context_service import CapabilityContextService, CapabilityProfileValidationError
from app.services.runtime_capability_catalog import RuntimeCapabilityCatalogProvider, build_default_runtime_capability_catalog_provider


def test_provider_parses_full_runtime_catalog_payload_and_exposes_metadata():
    provider = RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
        {
            "catalog_version": "v2026.04",
            "supports_snapshot_contract": True,
            "capabilities": [
                {"capability_id": "tool:shell", "capability_type": "tool", "logical_name": "shell"},
                {"capability_id": "skill:review", "capability_type": "skill", "logical_name": "review"},
                {"capability_id": "channel_action:jira_get_issue", "capability_type": "channel_action", "logical_name": "jira_get_issue"},
                {
                    "capability_id": "adapter:github:review_pull_request",
                    "capability_type": "adapter_action",
                    "logical_name": "review_pull_request",
                    "action_alias": "review_pull_request",
                    "adapter_system": "github",
                },
            ],
        }
    )
    assert provider.get_catalog_version() == "v2026.04"
    assert provider.get_catalog_source() == "runtime_api"
    assert provider.resolve_tool_name_to_capability_id("shell") == "tool:shell"
    assert provider.resolve_skill_name_to_capability_id("review") == "skill:review"
    assert provider.resolve_channel_name_to_capability_id("jira_get_issue") == "channel_action:jira_get_issue"
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_action_resolution_only_accepts_adapter_action_aliases():
    provider = RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
        {
            "capabilities": [
                {"capability_id": "tool:shell", "capability_type": "tool", "logical_name": "shell"},
                {"capability_id": "adapter:github:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"},
            ]
        }
    )
    assert provider.resolve_action_to_capability_id("shell") is None
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_seed_fallback_remains_compatible():
    provider = build_default_runtime_capability_catalog_provider()
    assert provider.get_catalog_source() == "seed_fallback"
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_capability_context_validates_full_sets_with_full_snapshot():
    service = CapabilityContextService(
        runtime_catalog_snapshot_payload={
            "catalog_version": "v-full",
            "supports_snapshot_contract": True,
            "capabilities": [
                {"capability_id": "tool:shell", "capability_type": "tool", "logical_name": "shell"},
                {"capability_id": "skill:review", "capability_type": "skill", "logical_name": "review"},
                {"capability_id": "channel_action:jira_get_issue", "capability_type": "channel_action", "logical_name": "jira_get_issue"},
                {"capability_id": "adapter:github:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"},
            ],
        }
    )
    service.validate_profile_payload(
        {
            "tool_set_json": '["shell"]',
            "skill_set_json": '["review"]',
            "channel_set_json": '["jira_get_issue"]',
            "allowed_actions_json": '["review_pull_request"]',
        }
    )


def test_capability_context_rejects_unknown_and_wrong_type_actions():
    service = CapabilityContextService(
        runtime_catalog_snapshot_payload={
            "catalog_version": "v-full",
            "supports_snapshot_contract": True,
            "capabilities": [
                {"capability_id": "tool:shell", "capability_type": "tool", "logical_name": "shell"},
                {"capability_id": "adapter:github:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"},
            ],
        }
    )
    assert _validation_error(lambda: service.validate_profile_payload({"allowed_actions_json": '["shell"]'})).endswith(
        "unknown or ambiguous action: shell"
    )
    assert _validation_error(lambda: service.validate_profile_payload({"allowed_actions_json": '["unknown"]'})).endswith(
        "unknown or ambiguous action: unknown"
    )


def test_seed_fallback_mode_does_not_hard_fail_tool_validation():
    service = CapabilityContextService(runtime_catalog_snapshot_payload=None)
    service.validate_profile_payload({"tool_set_json": '["anything"]', "allowed_actions_json": '["review_pull_request"]'})


def _validation_error(fn):
    try:
        fn()
    except CapabilityProfileValidationError as exc:
        return exc.detail
    raise AssertionError("expected validation error")
