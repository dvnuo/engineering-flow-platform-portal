from types import SimpleNamespace

from app.services.capability_context_service import CapabilityContextService, CapabilityProfileValidationError
from app.services.runtime_capability_catalog import (
    RuntimeCapabilityCatalogLoader,
    RuntimeCapabilityCatalogProvider,
    build_runtime_capability_catalog_loader_from_settings,
    build_default_runtime_capability_catalog_provider,
    build_runtime_capability_catalog_provider_from_settings,
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


def test_loader_uses_default_seed_when_snapshot_missing():
    provider = RuntimeCapabilityCatalogLoader.from_snapshot_json(None).build_provider()
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_loader_builds_provider_from_runtime_snapshot_json():
    snapshot_json = (
        '[{"capability_id":"adapter:runtime:custom_action","capability_type":"adapter_action","action_alias":"custom_action"}]'
    )
    provider = RuntimeCapabilityCatalogLoader.from_snapshot_json(snapshot_json).build_provider()
    assert provider.resolve_action_to_capability_id("custom_action") == "adapter:runtime:custom_action"


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


def test_capability_context_service_runtime_snapshot_path_is_first_class():
    service = CapabilityContextService(
        runtime_catalog_snapshot_payload=[
            {
                "capability_id": "adapter:runtime:custom_action",
                "capability_type": "adapter_action",
                "action_alias": "custom_action",
            }
        ]
    )
    service.validate_profile_payload({"allowed_actions_json": '["custom_action"]'})


def test_provider_can_be_constructed_from_settings_snapshot_json():
    snapshot_json = (
        '[{"capability_id":"adapter:runtime:settings_action","capability_type":"adapter_action","action_alias":"settings_action"}]'
    )
    provider = build_runtime_capability_catalog_provider_from_settings(snapshot_json=snapshot_json)
    assert provider.resolve_action_to_capability_id("settings_action") == "adapter:runtime:settings_action"


def test_settings_based_provider_falls_back_to_seed_data_when_snapshot_invalid():
    provider = build_runtime_capability_catalog_provider_from_settings(snapshot_json="{bad json")
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_capability_context_service_default_construction_uses_settings_snapshot(monkeypatch):
    import app.config as config_module

    original_get_settings = config_module.get_settings
    try:
        config_module.get_settings = lambda: SimpleNamespace(
            runtime_capability_catalog_snapshot_json=(
                '[{"capability_id":"adapter:runtime:default_path_action","capability_type":"adapter_action","action_alias":"default_path_action"}]'
            )
        )
        service = CapabilityContextService()
        service.validate_profile_payload({"allowed_actions_json": '["default_path_action"]'})
    finally:
        config_module.get_settings = original_get_settings


def test_settings_loader_path_handles_invalid_snapshot_with_fallback():
    loader = build_runtime_capability_catalog_loader_from_settings(snapshot_json="not-json")
    provider = loader.build_provider()
    assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_settings_loader_path_handles_missing_snapshot_with_fallback():
    import app.config as config_module

    original_get_settings = config_module.get_settings
    try:
        config_module.get_settings = lambda: SimpleNamespace(runtime_capability_catalog_snapshot_json="")
        provider = build_runtime_capability_catalog_provider_from_settings()
        assert provider.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"
    finally:
        config_module.get_settings = original_get_settings


def _validation_error_detail(fn):
    try:
        fn()
    except CapabilityProfileValidationError as exc:
        return exc.detail
    raise AssertionError("Expected CapabilityProfileValidationError")
