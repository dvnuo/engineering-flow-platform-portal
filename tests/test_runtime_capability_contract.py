import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, CapabilityProfile, User
from app.models.runtime_capability_catalog_snapshot import RuntimeCapabilityCatalogSnapshot
from app.services.auth_service import hash_password
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


def test_runtime_catalog_parser_accepts_legacy_adapter_field_aliases():
    provider = RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
        {
            "capabilities": [
                {
                    "capability_id": "adapter:github:review_pull_request",
                    "capability_type": "adapter_action",
                    "external_system": "github",
                    "action": "review_pull_request",
                }
            ]
        }
    )
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


def test_capability_context_prefers_agent_scoped_snapshot_over_other_agents():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        db.add(
            RuntimeCapabilityCatalogSnapshot(
                source_agent_id="agent-a",
                catalog_version="v-a",
                catalog_source="runtime_api",
                payload_json=json.dumps(
                    {"catalog_version": "v-a", "capabilities": [{"capability_id": "adapter:a:act", "capability_type": "adapter_action", "action_alias": "act"}]}
                ),
            )
        )
        db.add(
            RuntimeCapabilityCatalogSnapshot(
                source_agent_id="agent-b",
                catalog_version="v-b",
                catalog_source="runtime_api",
                payload_json=json.dumps(
                    {"catalog_version": "v-b", "capabilities": [{"capability_id": "adapter:b:act", "capability_type": "adapter_action", "action_alias": "act"}]}
                ),
            )
        )
        db.commit()

        service = CapabilityContextService()
        context = service.build_runtime_capability_context(
            capability_profile_id="cap-1",
            resolved=service.resolve_profile(None).model_copy(update={"allowed_actions": ["act"]}),
            db=db,
            agent_id="agent-b",
        )
        assert context["allowed_adapter_actions"] == ["adapter:b:act"]
        assert context["runtime_capability_catalog_version"] == "v-b"
    finally:
        db.close()


def test_capability_context_falls_back_when_no_agent_scoped_snapshot_exists():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        db.add(
            RuntimeCapabilityCatalogSnapshot(
                source_agent_id="another-agent",
                catalog_version="v-other",
                catalog_source="runtime_api",
                payload_json=json.dumps(
                    {"catalog_version": "v-other", "capabilities": [{"capability_id": "adapter:other:act", "capability_type": "adapter_action", "action_alias": "act"}]}
                ),
            )
        )
        db.commit()

        service = CapabilityContextService(runtime_catalog_snapshot_payload=None)
        context = service.build_runtime_capability_context(
            capability_profile_id="cap-1",
            resolved=service.resolve_profile(None).model_copy(update={"allowed_actions": ["review_pull_request"]}),
            db=db,
            agent_id="missing-agent",
        )
        assert context["allowed_adapter_actions"] == ["adapter:github:review_pull_request"]
    finally:
        db.close()


def _validation_error(fn):
    try:
        fn()
    except CapabilityProfileValidationError as exc:
        return exc.detail
    raise AssertionError("expected validation error")

def test_runtime_capability_context_skill_details_source_marker():
    from pathlib import Path
    src = Path('app/services/capability_context_service.py').read_text(encoding='utf-8')
    assert 'skill_details' in src


def test_skill_alias_resolves_capability_id_and_detail():
    provider = RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
        {
            "catalog_version": "v1",
            "supports_snapshot_contract": True,
            "capabilities": [
                {
                    "capability_id": "skill:review-pull-request",
                    "capability_type": "skill",
                    "logical_name": "review-pull-request",
                    "permission_state": "allowed",
                    "runtime_compatibility": "prompt_only",
                    "tool_mappings": {"github_get_pr": "efp_github_get_pr"},
                    "metadata": {"description": "Review PR"},
                }
            ],
        }
    )
    assert provider.resolve_skill_name_to_capability_id("review_pull_request") == "skill:review-pull-request"
    detail = provider.get_skill_detail("review_pull_request")
    assert detail["permission_state"] == "allowed"
    assert detail["runtime_compatibility"] == "prompt_only"
    assert detail["tool_mappings"]["github_get_pr"] == "efp_github_get_pr"


def test_capability_context_resolves_skill_alias_and_populates_skill_details():
    payload = {
        "catalog_version": "v1",
        "supports_snapshot_contract": True,
        "capabilities": [
            {
                "capability_id": "skill:review-pull-request",
                "capability_type": "skill",
                "logical_name": "review-pull-request",
                "permission_state": "ask",
                "runtime_compatibility": "prompt_only",
                "tool_mappings": {"github_get_pr": "efp_github_get_pr"},
                "metadata": {"description": "Review PR"},
            }
        ],
    }
    service = CapabilityContextService(runtime_catalog_snapshot_payload=payload)
    resolved = service.resolve_profile(None).model_copy(update={"skill_set": ["review_pull_request"]})
    ctx = service.build_runtime_capability_context(capability_profile_id="cap-1", resolved=resolved, db=None, agent_id=None)
    assert "skill:review-pull-request" in ctx["allowed_capability_ids"]
    assert ctx["unresolved_skills"] == []
    assert ctx["skill_details"][0]["capability_id"] == "skill:review-pull-request"


def test_skill_allowance_detail_accepts_hyphen_underscore_aliases():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        user = User(username="alias-owner", password_hash=hash_password("pw"), role="admin", is_active=True)
        profile = CapabilityProfile(name="cp1", skill_set_json='["review_pull_request"]')
        db.add_all([user, profile])
        db.commit()
        db.refresh(user)
        db.refresh(profile)
        agent = Agent(
            name="agent-alias",
            owner_user_id=user.id,
            capability_profile_id=profile.id,
            status="running",
            image="img",
            deployment_name="dep",
            service_name="svc",
            pvc_name="pvc",
        )
        db.add(agent)
        db.commit()
        service = CapabilityContextService()
        detail = service.get_skill_allowance_detail(db, agent, "review-pull-request")
        assert detail.allowed is True
        assert detail.reason == "allowed"

        profile.skill_set_json = '["review-pull-request"]'
        db.commit()
        detail_reverse = service.get_skill_allowance_detail(db, agent, "review_pull_request")
        assert detail_reverse.allowed is True
        assert detail_reverse.reason == "allowed"

        profile.skill_set_json = '["review_pull_request"]'
        db.commit()
        detail_miss = service.get_skill_allowance_detail(db, agent, "other_skill")
        assert detail_miss.allowed is False
    finally:
        db.close()


def test_runtime_capability_catalog_promotes_skill_metadata_fields():
    provider = RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
        {
            "catalog_version": "v1",
            "supports_snapshot_contract": True,
            "capabilities": [
                {
                    "capability_id": "skill:review-pull-request",
                    "capability_type": "skill",
                    "logical_name": "review-pull-request",
                    "metadata": {
                        "permission_state": "allowed",
                        "runtime_compatibility": "unsupported",
                        "tool_mappings": {"github_get_pr": "efp_github_get_pr"},
                        "description": "Review PR",
                    },
                }
            ],
        }
    )
    detail = provider.get_skill_detail("review_pull_request")
    assert detail["permission_state"] == "allowed"
    assert detail["runtime_compatibility"] == "unsupported"
    assert detail["tool_mappings"]["github_get_pr"] == "efp_github_get_pr"
    assert detail["metadata"]["description"] == "Review PR"


def test_capability_context_skill_details_promote_metadata_only_fields():
    payload = {
        "catalog_version": "v1",
        "supports_snapshot_contract": True,
        "capabilities": [
            {
                "capability_id": "skill:review-pull-request",
                "capability_type": "skill",
                "logical_name": "review-pull-request",
                "metadata": {
                    "permission_state": "allowed",
                    "runtime_compatibility": "unsupported",
                    "tool_mappings": {"github_get_pr": "efp_github_get_pr"},
                },
            }
        ],
    }
    service = CapabilityContextService(runtime_catalog_snapshot_payload=payload)
    resolved = service.resolve_profile(None).model_copy(update={"skill_set": ["review_pull_request"]})
    ctx = service.build_runtime_capability_context(capability_profile_id="cap-1", resolved=resolved, db=None, agent_id=None)
    assert ctx["skill_details"][0]["runtime_compatibility"] == "unsupported"
    assert ctx["skill_details"][0]["tool_mappings"]["github_get_pr"] == "efp_github_get_pr"
