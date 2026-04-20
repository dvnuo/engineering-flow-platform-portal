import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.schemas.runtime_profile import dump_runtime_profile_config_json, parse_runtime_profile_config_json
from app.services.runtime_profile_service import RuntimeProfileService

def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _mk_agent(user_id, profile_id):
    return Agent(
        name=f"a-{user_id}", owner_user_id=user_id, visibility="private", status="running", image="example/image:latest",
        runtime_profile_id=profile_id, disk_size_gi=20, mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace"
    )


def test_ensure_user_has_default_profile_creates_default():
    db = _session()
    user = User(username="u1", password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit(); db.refresh(user)

    profile = RuntimeProfileService(db).ensure_user_has_default_profile(user)
    assert profile.name == "Default"
    assert profile.is_default is True
    saved = json.loads(profile.config_json)
    assert saved == {}


def test_switch_default_keeps_exactly_one_default():
    db = _session()
    user = User(username="u1", password_hash="test", role="user", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    svc = RuntimeProfileService(db)
    p1 = svc.create_for_user(user, name="Default", description=None, config_json="{}", is_default=True)
    p2 = svc.create_for_user(user, name="P2", description=None, config_json="{}", is_default=True)
    rows = svc.list_for_user(user)
    assert len([r for r in rows if r.is_default]) == 1
    assert any(r.id == p2.id and r.is_default for r in rows)


def test_delete_default_promotes_other_and_last_delete_conflict():
    db = _session()
    user = User(username="u1", password_hash="test", role="user", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    svc = RuntimeProfileService(db)
    p1 = svc.create_for_user(user, name="Default", description=None, config_json="{}", is_default=True)
    p2 = svc.create_for_user(user, name="P2", description=None, config_json="{}", is_default=False)
    svc.delete_for_user(user, p1.id)
    rows = svc.list_for_user(user)
    assert len(rows) == 1
    assert rows[0].is_default is True

    with pytest.raises(Exception):
        svc.delete_for_user(user, rows[0].id)


def test_repair_legacy_shared_profiles_clones_and_rebinds():
    db = _session()
    u1 = User(username="u1", password_hash="test", role="admin", is_active=True)
    u2 = User(username="u2", password_hash="test", role="user", is_active=True)
    db.add_all([u1, u2]); db.commit(); db.refresh(u1); db.refresh(u2)

    rp = RuntimeProfile(owner_user_id=u1.id, name="Global", config_json=json.dumps({"llm": {"provider": "openai"}}), revision=1, is_default=False)
    db.add(rp); db.commit(); db.refresh(rp)

    db.add_all([_mk_agent(u1.id, rp.id), _mk_agent(u2.id, rp.id)])
    db.commit()

    svc = RuntimeProfileService(db)
    svc.repair_legacy_runtime_profiles(db)
    svc.ensure_defaults_for_all_users(db)

    a1 = db.query(Agent).filter(Agent.owner_user_id == u1.id).one()
    a2 = db.query(Agent).filter(Agent.owner_user_id == u2.id).one()
    assert a1.runtime_profile_id != a2.runtime_profile_id

    u1_profiles = svc.list_for_user(u1)
    u2_profiles = svc.list_for_user(u2)
    assert len([p for p in u1_profiles if p.is_default]) == 1
    assert len([p for p in u2_profiles if p.is_default]) == 1


def test_default_profile_config_has_safe_managed_defaults():
    cfg = RuntimeProfileService.default_profile_config()
    assert cfg["llm"]["max_tokens"] == 64000
    assert cfg["llm"]["temperature"] == 0.7
    assert cfg["llm"]["max_retries"] == 3
    assert cfg["llm"]["retry_delay"] == 1
    assert cfg["llm"]["tools"] == ["*"]
    assert cfg["llm"]["system-prompt"]["daily_notes"]["enabled"] is True
    assert cfg["debug"]["log_level"] == "INFO"

    assert "api_key" not in cfg["llm"]
    assert "api_base" not in cfg["llm"]
    assert "api_token" not in cfg["github"]
    assert "base_url" not in cfg["github"]
    assert "password" not in cfg["proxy"]
    assert "url" not in cfg["proxy"]
    assert cfg["jira"]["instances"] == []
    assert cfg["confluence"]["instances"] == []


def test_create_for_user_with_empty_config_stays_sparse():
    db = _session()
    user = User(username="u1", password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    profile = RuntimeProfileService(db).create_for_user(
        user,
        name="Seeded",
        description="raw on create",
        config_json="{}",
        is_default=False,
    )
    saved = json.loads(profile.config_json)
    assert saved == {}


def test_materialize_create_config_json_normalizes_raw_without_default_expansion():
    materialized = RuntimeProfileService.materialize_create_config_json(
        json.dumps({"llm": {"provider": "openai"}, "ssh": {"hack": True}})
    )
    saved = json.loads(materialized)
    assert saved == {"llm": {"provider": "openai"}}


def test_merge_with_managed_defaults_does_not_apply_creation_seed_to_legacy_sparse_profile():
    cfg = RuntimeProfileService.merge_with_managed_defaults({})
    assert cfg["proxy"]["enabled"] is False
    assert "url" not in cfg["proxy"]
    assert cfg["jira"]["enabled"] is False
    assert cfg["jira"]["instances"] == []
    assert cfg["confluence"]["enabled"] is False
    assert cfg["confluence"]["instances"] == []
    assert cfg["llm"]["provider"] == "github_copilot"


def test_create_for_user_persists_raw_snapshot_without_hidden_default_injection():
    db = _session()
    user = User(username="u2", password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    profile = RuntimeProfileService(db).create_for_user(
        user,
        name="Raw",
        description=None,
        config_json=json.dumps({"llm": {"provider": "openai"}}),
        is_default=False,
    )
    db.refresh(profile)
    saved = json.loads(profile.config_json)

    assert saved == {"llm": {"provider": "openai"}}
    assert "max_retries" not in saved["llm"]
    assert "system-prompt" not in saved["llm"]
    assert "proxy" not in saved
    assert "jira" not in saved


def test_normalize_persisted_config_json_prunes_unmanaged_nested_fields():
    raw = json.dumps(
        {
            "llm": {
                "provider": "openai",
                "api_base": "https://example.invalid",
                "system-prompt": {"tools": {"enabled": True}},
            },
            "ssh": {"enabled": True},
        }
    )
    normalized = RuntimeProfileService.normalize_persisted_config_json(raw)
    saved = json.loads(normalized)
    assert saved == {"llm": {"provider": "openai"}}


def test_runtime_profile_json_sanitizer_preserves_llm_context_budget_and_projection():
    raw = '{"llm":{"provider":"openai","context_budget":{"tool_loop":{"max_prompt_tokens":32000}},"context_projection":{"enabled":true}}}'
    parsed = parse_runtime_profile_config_json(raw)
    dumped = json.loads(dump_runtime_profile_config_json(parsed))

    assert parsed["llm"]["context_budget"]["tool_loop"]["max_prompt_tokens"] == 32000
    assert parsed["llm"]["context_projection"]["enabled"] is True
    assert dumped["llm"]["context_budget"]["tool_loop"]["max_prompt_tokens"] == 32000
    assert dumped["llm"]["context_projection"]["enabled"] is True


def test_normal_settings_form_like_save_remains_sparse_without_context_fields():
    raw = json.dumps({"llm": {"provider": "openai", "model": "gpt-5-mini", "tools": ["*"]}})
    normalized = RuntimeProfileService.normalize_persisted_config_json(raw)
    saved = json.loads(normalized)

    assert saved == {"llm": {"provider": "openai", "model": "gpt-5-mini", "tools": ["*"]}}
    assert "context_budget" not in saved["llm"]
    assert "context_projection" not in saved["llm"]
