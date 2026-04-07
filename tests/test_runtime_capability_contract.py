from app.contracts.runtime_capabilities import build_default_runtime_capability_contract
from app.services.capability_context_service import CapabilityContextService, CapabilityProfileValidationError


def test_runtime_capability_contract_resolves_friendly_name_to_capability_id():
    contract = build_default_runtime_capability_contract()
    assert contract.resolve_action_to_capability_id("review_pull_request") == "adapter:github:review_pull_request"


def test_runtime_capability_contract_accepts_exact_adapter_id_passthrough():
    contract = build_default_runtime_capability_contract()
    assert contract.resolve_action_to_capability_id("adapter:github:review_pull_request") == "adapter:github:review_pull_request"


def test_runtime_capability_contract_treats_ambiguous_alias_as_unresolved():
    contract = build_default_runtime_capability_contract()
    assert contract.resolve_action_to_capability_id("add_comment") is None


def test_capability_context_service_accepts_known_exact_adapter_id():
    service = CapabilityContextService()
    service.validate_profile_payload({"allowed_actions_json": '["adapter:github:review_pull_request"]'})


def test_capability_context_service_resolves_supported_friendly_name():
    service = CapabilityContextService()
    resolved = service._normalize_action_capability_id("review_pull_request")
    assert resolved == "adapter:github:review_pull_request"


def test_capability_context_service_rejects_ambiguous_name():
    service = CapabilityContextService()
    try:
        service.validate_profile_payload({"allowed_actions_json": '["add_comment"]'})
        raise AssertionError("Expected ambiguous action to fail validation")
    except CapabilityProfileValidationError as exc:
        assert "unknown or ambiguous action: add_comment" in exc.detail
