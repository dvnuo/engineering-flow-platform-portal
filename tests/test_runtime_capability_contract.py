from app.services.capability_context_service import CapabilityContextService, CapabilityProfileValidationError
from app.services.runtime_capability_catalog import (
    RuntimeCapabilityCatalogProvider,
    build_default_runtime_capability_catalog_provider,
)


def test_default_provider_resolves_friendly_name_to_capability_id():
    provider = build_default_runtime_capability_catalog_provider()
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_default_provider_accepts_exact_adapter_id_passthrough():
    provider = build_default_runtime_capability_catalog_provider()
    assert provider.resolve_action_to_capability_id("adapter:github:review_pull_request") == "adapter:github:review_pull_request"


def test_default_provider_rejects_ambiguous_alias():
    provider = build_default_runtime_capability_catalog_provider()
    assert provider.resolve_action_to_capability_id("add_comment") is None


def test_provider_can_be_built_from_runtime_catalog_payload():
    provider = RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
        [
            {
                "capability_id": "adapter:github:review_pull_request",
                "capability_type": "adapter_action",
                "action_alias": "review_pull_request",
            }
        ]
    )
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_capability_context_service_allowed_actions_validation_rules():
    service = CapabilityContextService()

    service.validate_profile_payload({"allowed_actions_json": '["review_pull_request"]'})

    assert _validation_error_detail(
        lambda: service.validate_profile_payload({"allowed_actions_json": '["unknown_action"]'})
    ).endswith("unknown or ambiguous action: unknown_action")
    assert _validation_error_detail(
        lambda: service.validate_profile_payload({"allowed_actions_json": '["add_comment"]'})
    ).endswith("unknown or ambiguous action: add_comment")
    assert "duplicate logical action" in _validation_error_detail(
        lambda: service.validate_profile_payload(
            {"allowed_actions_json": '["review_pull_request","adapter:github:review_pull_request"]'}
        )
    )


def _validation_error_detail(fn):
    try:
        fn()
    except CapabilityProfileValidationError as exc:
        return exc.detail
    raise AssertionError("Expected CapabilityProfileValidationError")
