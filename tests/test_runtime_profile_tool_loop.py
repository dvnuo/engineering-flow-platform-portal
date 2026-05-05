import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, CapabilityProfile, PolicyProfile, RuntimeCapabilityCatalogSnapshot, RuntimeProfile, User
from app.services.auth_service import hash_password
from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService
from app.services.runtime_profile_service import RuntimeProfileService


def test_runtime_profile_tool_loop_is_preserved():
    raw = {
        "llm": {
            "tool_loop": {
                "one_tool_per_turn": True,
                "parallel_tool_calls": False,
                "max_repeated_tool_signature": 2,
            }
        }
    }

    parsed = sanitize_runtime_profile_config_dict(raw)

    assert parsed["llm"]["tool_loop"] == {
        "one_tool_per_turn": True,
        "parallel_tool_calls": False,
        "max_repeated_tool_signature": 2,
    }


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"one_tool_per_turn": "true"}, "one_tool_per_turn must be a boolean"),
        ({"parallel_tool_calls": "false"}, "parallel_tool_calls must be a boolean"),
        ({"max_repeated_tool_signature": 0}, "must be between 1 and 10"),
        ({"max_repeated_tool_signature": 11}, "must be between 1 and 10"),
    ],
)
def test_runtime_profile_tool_loop_invalid_values_raise(payload, match):
    with pytest.raises(ValueError, match=match):
        sanitize_runtime_profile_config_dict({"llm": {"tool_loop": payload}})


@pytest.mark.parametrize("bad_value", [True, False])
def test_runtime_profile_tool_loop_rejects_bool_max_repeated_tool_signature(bad_value):
    with pytest.raises(ValueError, match="must be an integer"):
        sanitize_runtime_profile_config_dict(
            {
                "llm": {
                    "tool_loop": {
                        "max_repeated_tool_signature": bad_value,
                    }
                }
            }
        )


def test_default_runtime_profile_contains_tool_loop_defaults():
    cfg = RuntimeProfileService.default_profile_config()

    assert cfg["llm"]["tool_loop"]["one_tool_per_turn"] is True
    assert cfg["llm"]["tool_loop"]["parallel_tool_calls"] is False
    assert cfg["llm"]["tool_loop"]["max_repeated_tool_signature"] == 2


def test_runtime_metadata_includes_runtime_profile_and_tool_loop(monkeypatch):
    service = RuntimeExecutionContextService()

    monkeypatch.setattr(
        service,
        "build_for_agent",
        lambda _db, agent: {
            "capability_profile_id": "cap-1",
            "policy_profile_id": "pol-1",
            "runtime_profile_id": agent.runtime_profile_id,
            "runtime_profile_context": {
                "one_tool_per_turn": True,
                "parallel_tool_calls": False,
                "max_repeated_tool_signature": 2,
            },
            "capability_context": {
                "allowed_capability_ids": ["cap.a"],
                "allowed_capability_types": ["tool"],
                "allowed_external_systems": [],
                "allowed_webhook_triggers": [],
                "allowed_actions": ["tool.run"],
                "allowed_adapter_actions": ["adapter.run"],
                "unresolved_tools": [],
                "unresolved_skills": [],
                "unresolved_channels": [],
                "unresolved_actions": [],
                "resolved_action_mappings": {},
                "runtime_capability_catalog_version": "v1",
                "runtime_capability_catalog_source": "test",
                "catalog_validation_mode": "strict",
            },
            "policy_context": {
                "policy_profile_id": "pol-1",
                "derived_runtime_rules": {"governance_allow_auto_run": True},
            },
        },
    )

    agent = SimpleNamespace(runtime_profile_id="rp-1")
    metadata = service.build_runtime_metadata(db=object(), agent=agent)

    assert metadata["runtime_profile_id"] == "rp-1"
    assert metadata["llm_tool_loop"]["one_tool_per_turn"] is True
    assert metadata["llm_tool_loop"]["parallel_tool_calls"] is False
    assert metadata["allowed_capability_ids"] == ["cap.a"]
    assert metadata["allowed_adapter_actions"] == ["adapter.run"]
    assert metadata["policy_context"]["policy_profile_id"] == "pol-1"


def test_runtime_metadata_materializes_default_tool_loop_for_sparse_profile(monkeypatch):
    service = RuntimeExecutionContextService()
    profile = SimpleNamespace(id="rp-1", config_json='{"llm": {"provider": "github_copilot"}}')

    import app.services.runtime_execution_context_service as module

    monkeypatch.setattr(
        module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )

    runtime_profile_id, runtime_context = service._build_runtime_profile_context(
        db=object(),
        agent=SimpleNamespace(
            id="agent-1",
            runtime_profile_id="rp-1",
            capability_profile_id=None,
            policy_profile_id=None,
        ),
    )

    assert runtime_profile_id == "rp-1"
    assert runtime_context == {
        "one_tool_per_turn": True,
        "parallel_tool_calls": False,
        "max_repeated_tool_signature": 2,
    }


def test_runtime_metadata_uses_explicit_tool_loop_override(monkeypatch):
    service = RuntimeExecutionContextService()
    profile = SimpleNamespace(
        id="rp-1",
        config_json='{"llm": {"tool_loop": {"one_tool_per_turn": false, "parallel_tool_calls": true, "max_repeated_tool_signature": 3}}}',
    )

    import app.services.runtime_execution_context_service as module

    monkeypatch.setattr(
        module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )

    runtime_profile_id, runtime_context = service._build_runtime_profile_context(
        db=object(),
        agent=SimpleNamespace(
            id="agent-1",
            runtime_profile_id="rp-1",
            capability_profile_id=None,
            policy_profile_id=None,
        ),
    )

    assert runtime_profile_id == "rp-1"
    assert runtime_context == {
        "one_tool_per_turn": False,
        "parallel_tool_calls": True,
        "max_repeated_tool_signature": 3,
    }


def test_runtime_metadata_includes_tool_permission_governance(monkeypatch):
    service = RuntimeExecutionContextService()
    monkeypatch.setattr(
        service,
        "build_for_agent",
        lambda _db, _agent: {
            "capability_profile_id": None,
            "policy_profile_id": "pol-1",
            "runtime_profile_id": None,
            "runtime_profile_context": {},
            "capability_context": {
                "allowed_capability_ids": [],
                "allowed_capability_types": [],
                "allowed_external_systems": [],
                "allowed_webhook_triggers": [],
                "allowed_actions": [],
                "allowed_adapter_actions": [],
                "unresolved_tools": [],
                "unresolved_skills": [],
                "unresolved_channels": [],
                "unresolved_actions": [],
                "resolved_action_mappings": {},
                "runtime_capability_catalog_version": None,
                "runtime_capability_catalog_source": None,
                "catalog_validation_mode": "strict",
            },
            "policy_context": {
                "policy_profile_id": "pol-1",
                "derived_runtime_rules": {
                    "external_tools_strict_mode": True,
                    "tool_permission_defaults": {"write": "ask", "mutation": "deny"},
                    "allowed_write_tools": ["git.commit"],
                    "write_tool_policy": {"mode": "allowlist"},
                },
            },
        },
    )
    metadata = service.build_runtime_metadata(db=object(), agent=SimpleNamespace(id="a1"))
    assert metadata["external_tools_strict_mode"] is True
    assert metadata["tool_permission_defaults"] == {"write": "ask", "mutation": "deny"}
    assert metadata["allowed_write_tools"] == ["git.commit"]
    assert metadata["write_tool_policy"] == {"mode": "allowlist"}

def test_runtime_metadata_skill_details_and_policy_defaults_source_markers():
    from pathlib import Path
    src = Path('app/services/runtime_execution_context_service.py').read_text(encoding='utf-8')
    assert 'skill_details' in src
    assert 'external_tools_strict_mode' in src
    assert 'tool_permission_defaults' in src


@pytest.fixture()
def runtime_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    user = User(username="rp-owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.close()


def test_runtime_metadata_policy_profile_rules_from_db(runtime_db):
    db, user = runtime_db
    policy = PolicyProfile(
        name="policy",
        permission_rules_json=json.dumps(
            {
                "external_tools_strict_mode": True,
                "write_tool_default": "ASK",
                "mutation_tool_default": "deny",
                "allowed_write_tools": ["github_add_comment"],
                "ask_write_tools": ["jira_transition_issue"],
                "denied_write_tools": ["git_push"],
                "allowed_mutation_tools": ["safe_mutation"],
                "write_tool_policy": {"mode": "allowlist"},
                "mutation_tool_policy": {"mode": "ask"},
            }
        ),
    )
    db.add(policy)
    db.commit()
    agent = Agent(name="a", owner_user_id=user.id, policy_profile_id=policy.id, image="img", status="running", deployment_name="dep-a", service_name="svc-a", pvc_name="pvc-a")
    db.add(agent)
    db.commit()
    db.refresh(agent)
    metadata = RuntimeExecutionContextService().build_runtime_metadata(db, agent)
    assert metadata["external_tools_strict_mode"] is True
    assert metadata["tool_permission_defaults"]["write"] == "ask"
    assert metadata["tool_permission_defaults"]["mutation"] == "deny"
    assert metadata["allowed_write_tools"] == ["github_add_comment"]
    assert metadata["ask_write_tools"] == ["jira_transition_issue"]
    assert metadata["denied_write_tools"] == ["git_push"]
    assert metadata["allowed_mutation_tools"] == ["safe_mutation"]
    assert metadata["write_tool_policy"] == {"mode": "allowlist"}
    assert metadata["mutation_tool_policy"] == {"mode": "ask"}


def test_runtime_metadata_policy_profile_invalid_defaults_ignored(runtime_db):
    db, user = runtime_db
    policy = PolicyProfile(name="bad", permission_rules_json=json.dumps({"write_tool_default": "invalid", "mutation_tool_default": "ALSO_BAD"}))
    db.add(policy)
    db.commit()
    agent = Agent(name="b", owner_user_id=user.id, policy_profile_id=policy.id, image="img", status="running", deployment_name="dep-b", service_name="svc-b", pvc_name="pvc-b")
    db.add(agent)
    db.commit()
    metadata = RuntimeExecutionContextService().build_runtime_metadata(db, agent)
    assert metadata.get("tool_permission_defaults") in (None, {})


def test_runtime_metadata_and_apply_payload_include_skill_details(runtime_db):
    db, user = runtime_db
    cap = CapabilityProfile(name="cap", skill_set_json='["review_pull_request"]')
    rp = RuntimeProfile(name="rp", owner_user_id=user.id, config_json="{}")
    db.add_all([cap, rp])
    db.commit()
    agent = Agent(
        name="skill-agent",
        owner_user_id=user.id,
        capability_profile_id=cap.id,
        runtime_profile_id=rp.id,
        status="running",
        image="img",
        namespace="efp-agents",
        deployment_name="dep-skill",
        service_name="svc",
        pvc_name="pvc-skill",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    snapshot = RuntimeCapabilityCatalogSnapshot(
        source_agent_id=agent.id,
        catalog_version="v1",
        catalog_source="runtime_api",
        payload_json=json.dumps({"catalog_version": "v1", "capabilities": [{"capability_id": "skill:review-pull-request", "capability_type": "skill", "logical_name": "review-pull-request", "permission_state": "allowed", "runtime_compatibility": "prompt_only"}]}),
    )
    db.add(snapshot)
    db.commit()
    metadata = RuntimeExecutionContextService().build_runtime_metadata(db, agent)
    assert metadata["skill_details"]
    payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, agent, rp)
    assert payload["config"]["skill_details"]
