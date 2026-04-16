import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.services.runtime_profile_service import RuntimeProfileService

EXPECTED_PROXY_URL = "https://proxy.com:80"
EXPECTED_JIRA_INSTANCES = [
    {"name": "Jira 1", "url": "https://yourcompany.atlassian.net"},
    {"name": "Jira 2", "url": "https://yourcompany2.atlassian.net"},
]
EXPECTED_CONFLUENCE_INSTANCES = [
    {"name": "Confluence 1", "url": "https://yourcompany.atlassian.net/wiki"},
    {"name": "Confluence 2", "url": "https://yourcompany2.atlassian.net/wiki"},
]


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
    assert saved["proxy"]["url"] == "https://proxy.com:80"
    assert len(saved["jira"]["instances"]) == 2
    assert len(saved["confluence"]["instances"]) == 2
    assert saved["llm"]["provider"] == "openai"


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
    assert cfg["llm"]["max_tokens"] == 1000
    assert cfg["llm"]["temperature"] == 0.7
    assert cfg["llm"]["max_retries"] == 3
    assert cfg["llm"]["retry_delay"] == 1
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


def test_creation_profile_config_has_business_seed_defaults():
    cfg = RuntimeProfileService.creation_profile_config()
    assert cfg["proxy"]["enabled"] is False
    assert cfg["proxy"]["url"] == EXPECTED_PROXY_URL
    assert cfg["jira"]["enabled"] is False
    assert cfg["jira"]["instances"] == EXPECTED_JIRA_INSTANCES
    assert cfg["confluence"]["enabled"] is False
    assert cfg["confluence"]["instances"] == EXPECTED_CONFLUENCE_INSTANCES

    for instance in cfg["jira"]["instances"] + cfg["confluence"]["instances"]:
        assert "password" not in instance
        assert "token" not in instance


def test_create_for_user_materializes_creation_defaults_when_config_is_empty():
    db = _session()
    user = User(username="u1", password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    profile = RuntimeProfileService(db).create_for_user(
        user,
        name="Seeded",
        description="materialized on create",
        config_json="{}",
        is_default=False,
    )
    saved = json.loads(profile.config_json)
    assert saved["proxy"]["url"] == EXPECTED_PROXY_URL
    assert saved["jira"]["instances"] == EXPECTED_JIRA_INSTANCES
    assert saved["confluence"]["instances"] == EXPECTED_CONFLUENCE_INSTANCES
    assert saved["llm"]["provider"] == "openai"


def test_materialize_create_config_json_preserves_explicit_overlay_and_fills_missing_seed():
    materialized = RuntimeProfileService.materialize_create_config_json(
        json.dumps(
            {
                "proxy": {"enabled": True, "url": "https://custom-proxy.example:8443"},
                "jira": {
                    "enabled": True,
                    "instances": [
                        {"name": "Custom Jira", "url": "https://jira.custom.example"},
                    ],
                },
            }
        )
    )
    saved = json.loads(materialized)
    assert saved["proxy"]["enabled"] is True
    assert saved["proxy"]["url"] == "https://custom-proxy.example:8443"
    assert saved["jira"]["enabled"] is True
    assert saved["jira"]["instances"] == [{"name": "Custom Jira", "url": "https://jira.custom.example"}]
    assert len(saved["confluence"]["instances"]) == 2

    assert "llm" in saved
    assert "debug" in saved
    assert "git" in saved
    assert "github" in saved

    for instance in saved["jira"]["instances"] + saved["confluence"]["instances"]:
        assert "password" not in instance
        assert "token" not in instance


def test_merge_with_managed_defaults_does_not_apply_creation_seed_to_legacy_sparse_profile():
    cfg = RuntimeProfileService.merge_with_managed_defaults({})
    assert cfg["proxy"]["enabled"] is False
    assert "url" not in cfg["proxy"]
    assert cfg["jira"]["enabled"] is False
    assert cfg["jira"]["instances"] == []
    assert cfg["confluence"]["enabled"] is False
    assert cfg["confluence"]["instances"] == []
    assert cfg["llm"]["provider"] == "openai"
