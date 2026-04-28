import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
    validate_runtime_profile_config_json,
)
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
    assert cfg["llm"]["model"] == "gpt-5.4-mini"
    assert cfg["llm"]["max_tokens"] == 64000
    assert "temperature" not in cfg["llm"]
    assert cfg["llm"]["max_retries"] == 3
    assert cfg["llm"]["retry_delay"] == 1
    assert cfg["llm"]["tools"] == ["*"]
    assert cfg["llm"]["response_flow"]["plan_policy"] == "explicit_or_complex"
    assert cfg["llm"]["response_flow"]["staging_policy"] == "explicit_or_complex"
    assert cfg["llm"]["response_flow"]["default_skill_execution_style"] == "direct"
    assert cfg["llm"]["response_flow"]["ask_user_policy"] == "blocked_only"
    assert cfg["llm"]["response_flow"]["active_skill_conflict_policy"] == "auto_switch_direct"
    assert cfg["llm"]["response_flow"]["complexity_prompt_budget_ratio"] == 0.85
    assert cfg["llm"]["response_flow"]["complexity_min_request_tokens"] == 24000
    assert cfg["llm"]["system-prompt"]["daily_notes"]["enabled"] is True
    assert cfg["debug"]["log_level"] == "INFO"

    assert "api_key" not in cfg["llm"]
    assert "api_base" not in cfg["llm"]
    assert "api_token" not in cfg["github"]
    assert "base_url" not in cfg["github"]
    assert "automation" not in cfg["github"]
    assert "password" not in cfg["proxy"]
    assert "url" not in cfg["proxy"]
    assert cfg["jira"]["instances"] == []
    assert cfg["confluence"]["instances"] == []
    assert "automation" not in cfg["jira"]
    assert "automation" not in cfg["confluence"]




def test_managed_provider_models_prefer_gpt_5_4_mini():
    github_models = RuntimeProfileService.managed_model_values_for_provider("github_copilot")
    openai_models = RuntimeProfileService.managed_model_values_for_provider("openai")
    assert github_models[0] == "gpt-5.4-mini"
    assert openai_models[0] == "gpt-5.4-mini"
    assert "gpt-5-mini" in github_models
    assert "gpt-5-mini" in openai_models


def test_parse_runtime_profile_config_json_keeps_temperature_for_exact_gpt4():
    parsed = parse_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":0.2}}')
    assert parsed == {"llm": {"model": "gpt-4", "temperature": 0.2}}


def test_parse_runtime_profile_config_json_strips_temperature_without_gpt4_model():
    parsed = parse_runtime_profile_config_json('{"llm":{"temperature":0.2}}')
    assert parsed == {"llm": {}}


def test_parse_runtime_profile_config_json_strips_temperature_for_gpt4o():
    parsed = parse_runtime_profile_config_json('{"llm":{"model":"gpt-4o","temperature":0.2}}')
    assert parsed == {"llm": {"model": "gpt-4o"}}


def test_parse_runtime_profile_config_json_normalizes_string_temperature_for_exact_gpt4():
    parsed = parse_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":"0.2"}}')
    assert parsed == {"llm": {"model": "gpt-4", "temperature": 0.2}}


def test_validate_runtime_profile_config_json_rejects_temperature_above_two():
    with pytest.raises(ValueError, match="temperature.*0 and 2"):
        validate_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":2.1}}')


def test_validate_runtime_profile_config_json_rejects_boolean_temperature():
    with pytest.raises(ValueError, match="temperature"):
        validate_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":true}}')


def test_validate_runtime_profile_config_json_rejects_nan_temperature_string():
    with pytest.raises(ValueError, match="temperature|0 and 2"):
        validate_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":"NaN"}}')


def test_validate_runtime_profile_config_json_rejects_lowercase_nan_temperature_string():
    with pytest.raises(ValueError, match="temperature|0 and 2"):
        validate_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":"nan"}}')


def test_validate_runtime_profile_config_json_rejects_infinity_temperature_string():
    with pytest.raises(ValueError, match="temperature|0 and 2"):
        validate_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":"Infinity"}}')


def test_parse_runtime_profile_config_json_nan_temperature_returns_empty_with_fallback():
    parsed = parse_runtime_profile_config_json('{"llm":{"model":"gpt-4","temperature":"NaN"}}', fallback_to_empty=True)
    assert parsed == {}


def test_dump_runtime_profile_config_json_keeps_zero_temperature_for_exact_gpt4():
    dumped = dump_runtime_profile_config_json({"llm": {"model": "gpt-4", "temperature": 0}})
    parsed = json.loads(dumped)
    assert parsed["llm"]["temperature"] == 0


def test_dump_runtime_profile_config_json_strips_zero_temperature_for_non_gpt4():
    dumped = dump_runtime_profile_config_json({"llm": {"model": "gpt-4.1", "temperature": 0}})
    parsed = json.loads(dumped)
    assert parsed["llm"] == {"model": "gpt-4.1"}


def test_dump_runtime_profile_config_json_rejects_nan_temperature():
    with pytest.raises(ValueError, match="temperature|0 and 2"):
        dump_runtime_profile_config_json({"llm": {"model": "gpt-4", "temperature": "NaN"}})


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


def test_normalize_persisted_config_json_strips_legacy_provider_automation_fields():
    raw = json.dumps(
        {
            "github": {"enabled": True, "automation": {"review_requests": {"enabled": True, "repos": ["a/b"]}}},
            "jira": {"enabled": True, "automation": {"assignments": {"enabled": True, "projects": ["ENG"]}}},
            "confluence": {"enabled": True, "automation": {"mentions": {"enabled": True, "spaces": ["DOCS"]}}},
        }
    )
    normalized = RuntimeProfileService.normalize_persisted_config_json(raw)
    saved = json.loads(normalized)
    assert saved == {"github": {"enabled": True}, "jira": {"enabled": True}, "confluence": {"enabled": True}}


def test_sanitize_all_persisted_runtime_profiles_removes_legacy_provider_automation_fields():
    db = _session()
    user = User(username="u-clean", password_hash="test", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    profile = RuntimeProfile(
        owner_user_id=user.id,
        name="legacy",
        config_json=json.dumps(
            {
                "github": {"enabled": True, "api_token": "tok", "base_url": "https://api.github.com", "automation": {"mentions": {"enabled": True}}},
                "jira": {"enabled": True, "instances": [{"name": "jira", "url": "https://jira.local"}], "automation": {"assignments": {"enabled": True}}},
                "confluence": {"enabled": True, "instances": [{"name": "conf", "url": "https://conf.local"}], "automation": {"mentions": {"enabled": True}}},
            }
        ),
        revision=1,
        is_default=True,
    )
    db.add(profile); db.commit(); db.refresh(profile)

    svc = RuntimeProfileService(db)
    changed = svc.sanitize_all_persisted_runtime_profiles()
    assert changed == 1

    db.refresh(profile)
    saved = json.loads(profile.config_json)
    assert "automation" not in saved["github"]
    assert "automation" not in saved["jira"]
    assert "automation" not in saved["confluence"]
    assert saved["github"]["enabled"] is True
    assert saved["github"]["api_token"] == "tok"
    assert saved["github"]["base_url"] == "https://api.github.com"
    assert saved["jira"]["enabled"] is True
    assert saved["jira"]["instances"] == [{"name": "jira", "url": "https://jira.local"}]
    assert saved["confluence"]["enabled"] is True
    assert saved["confluence"]["instances"] == [{"name": "conf", "url": "https://conf.local"}]


def test_update_for_user_sanitizes_runtime_profile_config():
    db = _session()
    user = User(username="u-upd", password_hash="test", role="user", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    svc = RuntimeProfileService(db)
    profile = svc.create_for_user(user, name="p1", description=None, config_json=json.dumps({"github": {"enabled": True}}), is_default=True)

    updated, _changed = svc.update_for_user(
        user,
        profile.id,
        config_json=json.dumps({"github": {"enabled": True, "automation": {"mentions": {"enabled": True}}}}),
    )
    saved = json.loads(updated.config_json)
    assert saved == {"github": {"enabled": True}}


def test_create_for_user_sanitizes_runtime_profile_config():
    db = _session()
    user = User(username="u-create", password_hash="test", role="user", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    svc = RuntimeProfileService(db)

    profile = svc.create_for_user(
        user,
        name="with-legacy-automation",
        description=None,
        config_json=json.dumps(
            {
                "github": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                "jira": {"enabled": True, "automation": {"assignments": {"enabled": True}}},
                "confluence": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
            }
        ),
        is_default=True,
    )
    saved = json.loads(profile.config_json)
    assert saved["github"] == {"enabled": True}
    assert saved["jira"] == {"enabled": True}
    assert saved["confluence"] == {"enabled": True}
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
    assert "response_flow" not in saved["llm"]


def test_runtime_profile_json_sanitizer_preserves_valid_llm_response_flow():
    raw = json.dumps(
        {
            "llm": {
                "provider": "openai",
                "response_flow": {
                    "plan_policy": "explicit_or_complex",
                    "staging_policy": "always",
                    "default_skill_execution_style": "direct",
                    "ask_user_policy": "blocked_only",
                    "active_skill_conflict_policy": "always_ask",
                    "complexity_prompt_budget_ratio": 0.85,
                    "complexity_min_request_tokens": 24000,
                },
            }
        }
    )
    parsed = parse_runtime_profile_config_json(raw)
    dumped = json.loads(dump_runtime_profile_config_json(parsed))

    assert parsed["llm"]["response_flow"]["plan_policy"] == "explicit_or_complex"
    assert parsed["llm"]["response_flow"]["staging_policy"] == "always"
    assert parsed["llm"]["response_flow"]["default_skill_execution_style"] == "direct"
    assert parsed["llm"]["response_flow"]["ask_user_policy"] == "blocked_only"
    assert parsed["llm"]["response_flow"]["active_skill_conflict_policy"] == "always_ask"
    assert parsed["llm"]["response_flow"]["complexity_prompt_budget_ratio"] == 0.85
    assert parsed["llm"]["response_flow"]["complexity_min_request_tokens"] == 24000
    assert dumped["llm"]["response_flow"]["plan_policy"] == "explicit_or_complex"
    assert dumped["llm"]["response_flow"]["staging_policy"] == "always"
    assert dumped["llm"]["response_flow"]["active_skill_conflict_policy"] == "always_ask"


def test_runtime_profile_json_sanitizer_omits_response_flow_when_absent():
    raw = json.dumps({"llm": {"provider": "openai"}})
    parsed = parse_runtime_profile_config_json(raw)
    dumped = json.loads(dump_runtime_profile_config_json(parsed))

    assert "response_flow" not in parsed["llm"]
    assert "response_flow" not in dumped["llm"]
