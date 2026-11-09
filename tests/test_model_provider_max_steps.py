"""The Model provider's step limit: Max steps per message.

One chat message may take many model calls; the native runtime stops at a cap
(``DEFAULT_MAX_STEPS`` by default) and the Portal offers Continue. The cap is a Model provider
connector setting: the administrator's Default connectors hold the default,
every member may set their own, and a member who sets none follows the
administrator's, now and when it changes. It is stored as ``llm.max_steps``
and reaches the runtimes as the loop cap (``max_iterations``).
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session, sessionmaker

from app.contracts.llm_catalog import DEFAULT_MAX_STEPS, MAX_STEPS_LIMIT
from app.models import AuditLog
from app.services.connector_defaults_service import (
    changed_connector_types,
    default_step_limit,
    effective_config,
    follows_changed_defaults,
    with_default_step_limit,
)
from app.services.connector_defaults_sync import SYNC_AUDIT_ACTION, sync_followers
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_secret_service import render_profile_secret_data
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from app.services.runtime_profile_service import RuntimeProfileService
from tests.test_connector_modes import _CountingSecrets, _session, _user
from tests.test_web_connector_settings import _bind_profile, _build_env, _saved

SEED = {"llm": {"provider": "github_copilot", "model": "gpt-6-sol", "max_steps": 60}}
OUT_OF_RANGE = f"Max steps per message must be a whole number from 1 to {MAX_STEPS_LIMIT}."


# ------------------------------------------------------------- the service


def test_a_row_without_its_own_limit_follows_the_seed_and_one_with_keeps_it():
    assert effective_config({"llm": {"api_key": "k"}}, {}, SEED)["llm"] == {"api_key": "k", "max_steps": 60}
    assert effective_config({}, {}, SEED)["llm"] == {"max_steps": 60}
    assert effective_config({"llm": {"max_steps": 12}}, {}, SEED)["llm"] == {"max_steps": 12}
    # A seed without one, or with a value the sanitizer refuses, adds nothing.
    assert "max_steps" not in effective_config({"llm": {"api_key": "k"}}, {}, {"llm": {"api_key": "s"}})["llm"]
    assert "llm" not in effective_config({}, {}, {"llm": {"max_steps": 0}})
    assert with_default_step_limit({"llm": {"api_key": "k"}}, None) == {"llm": {"api_key": "k"}}
    assert default_step_limit(SEED) == 60
    assert default_step_limit({}) is None


def test_a_changed_default_limit_is_a_change_for_the_members_who_follow_it():
    assert changed_connector_types({"llm": {"max_steps": 30}}, SEED) == ["llm"]
    assert changed_connector_types(SEED, SEED) == []
    # The rest of the model provider is every member's own: a rotated shared
    # key is not a change for anyone.
    assert changed_connector_types({"llm": {"max_steps": 60, "api_key": "a"}}, {"llm": {"max_steps": 60, "api_key": "b"}}) == []

    assert follows_changed_defaults({"llm": {"api_key": "k"}}, {}, ["llm"]) is True
    assert follows_changed_defaults({"llm": {"max_steps": 12}}, {}, ["llm"]) is False
    # Managed connectors follow as before: by mode.
    assert follows_changed_defaults({"llm": {"max_steps": 12}}, {}, ["jira"]) is True
    assert follows_changed_defaults({}, {"jira": "custom"}, ["jira"]) is False


def test_a_new_member_follows_the_default_limit_instead_of_copying_it():
    db = _session()
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(_user(db))

    # Provider and model are copied as before; the limit is followed.
    assert json.loads(profile.config_json)["llm"] == {"provider": "github_copilot", "model": "gpt-6-sol"}
    assert service.effective_config_for(profile)["llm"]["max_steps"] == 60

    # The administrator moves it: the member moves with it.
    RuntimeProfileSeedService(db).save_seed({"llm": {**SEED["llm"], "max_steps": 90}})
    assert service.effective_config_for(profile)["llm"]["max_steps"] == 90


def test_the_rollout_reaches_only_the_members_without_a_limit_of_their_own():
    db = _session()
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, class_=Session)
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    follower = service.get_or_create_for_user(_user(db, "follower"))
    own = service.get_or_create_for_user(_user(db, "own"))
    own_config = json.loads(own.config_json)
    own, changed = service.save_config(own, {**own_config, "llm": {**own_config["llm"], "max_steps": 12}})
    assert changed
    secrets = _CountingSecrets()

    summary = sync_followers(["llm"], secret_service=secrets, triggered_by_user_id=1, session_factory=factory)

    assert secrets.applied == [follower.id]
    assert summary["members"] == 1 and summary["connectors"] == ["llm"]
    db.refresh(follower)
    assert follower.revision == 2
    audit = db.query(AuditLog).filter(AuditLog.action == SYNC_AUDIT_ACTION).one()
    assert json.loads(audit.details_json)["connectors"] == ["llm"]


# ------------------------------------------------------ what the runtimes get


def test_the_execution_context_and_the_secret_carry_the_limit_the_member_gets(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        RuntimeProfileSeedService(env.db).save_seed(SEED)
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot", "model": "gpt-6-sol"}})

        # Per request, as the runtime profile config the native gateway reads.
        context = RuntimeExecutionContextService().build_for_agent(env.db, env.agent)
        config = context["runtime_profile_context"]["config"]
        assert config["max_iterations"] == 60
        assert "max_steps" not in config["llm"]
        # In the pod Secret, canonical: each runtime projects it at boot.
        effective = RuntimeProfileService(env.db).effective_config_for(rp)
        secret = json.loads(render_profile_secret_data(rp, effective)["config.json"])
        assert secret["config"]["llm"]["max_steps"] == 60

        # Their own value wins over the administrator's.
        rp.config_json = json.dumps({"llm": {"provider": "github_copilot", "model": "gpt-6-sol", "max_steps": 12}})
        env.db.add(rp)
        env.db.commit()
        context = RuntimeExecutionContextService().build_for_agent(env.db, env.agent)
        assert context["runtime_profile_context"]["config"]["max_iterations"] == 12
    finally:
        env.cleanup()


# ------------------------------------------------------------------ the forms


def test_the_members_panel_shows_their_own_value_and_the_default_as_the_placeholder(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot"}})
        # No administrator default yet: the runtime's own.
        resp = env.client.get("/app/connectors/llm/panel")
        assert resp.status_code == 200
        assert re.search(rf'name="llm_max_steps"[^>]*value=""[^>]*placeholder="Default {DEFAULT_MAX_STEPS}"', resp.text)

        RuntimeProfileSeedService(env.db).save_seed(SEED)
        resp = env.client.get("/app/connectors/llm/panel")
        assert re.search(r'name="llm_max_steps"[^>]*value=""[^>]*placeholder="Default 60"', resp.text)

        resp = env.client.post(
            "/app/connectors/llm/save",
            data={"__touch_llm": "1", "llm_provider": "github_copilot", "llm_max_steps": "12"},
        )
        assert resp.status_code == 200
        assert _saved(env.db, rp)["llm"]["max_steps"] == 12
        assert re.search(r'name="llm_max_steps"[^>]*value="12"', resp.text)

        # Blank goes back to following the administrator.
        resp = env.client.post(
            "/app/connectors/llm/save",
            data={"__touch_llm": "1", "llm_provider": "github_copilot", "llm_max_steps": ""},
        )
        assert "max_steps" not in _saved(env.db, rp)["llm"]
        assert re.search(r'name="llm_max_steps"[^>]*value=""[^>]*placeholder="Default 60"', resp.text)

        resp = env.client.post(
            "/app/connectors/llm/save",
            data={"__touch_llm": "1", "llm_provider": "github_copilot", "llm_max_steps": "0"},
        )
        assert OUT_OF_RANGE in resp.text
        assert 'data-settings-status="error"' in resp.text
        assert "max_steps" not in _saved(env.db, rp)["llm"]
    finally:
        env.cleanup()


def test_the_administrator_saving_a_new_default_limit_rolls_it_out(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        service = RuntimeProfileService(env.db)
        follower = _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot"}})
        follower.connector_modes_json = "{}"
        env.db.commit()
        own = service.get_or_create_for_user(env.other)
        own, _ = service.save_config(own, {"llm": {"provider": "github_copilot", "max_steps": 12}})

        resp = env.client.post("/app/admin/default-connections/save", data={"llm_max_steps": "60"})
        assert resp.status_code == 200
        assert "Members who follow Model provider are being updated in the background" in resp.text
        assert RuntimeProfileSeedService(env.db).get_seed()["llm"]["max_steps"] == 60
        env.db.refresh(follower)
        env.db.refresh(own)
        assert follower.revision == 2
        assert own.revision == 2  # bumped by their own save above, not by the rollout
        assert env.calls["apply"] == 1

        # Saving the same limit again changes nothing for anyone.
        again = env.client.post("/app/admin/default-connections/save", data={"llm_max_steps": "60"})
        assert "Nothing changed for the members who follow them" in again.text
        assert env.calls["apply"] == 1
    finally:
        env.cleanup()
