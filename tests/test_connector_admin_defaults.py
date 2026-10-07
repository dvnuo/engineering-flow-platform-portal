"""Reset to defaults: the reset service and its web route.

An existing member's settings row never sees later edits to the admin's
Default connectors seed on its own; the button on every settings connector
panel except the model provider replaces the connector's settings with the
seed's after a confirmation (docs/CONNECTORS_CONTRACT.md section 7.1).
"""
from __future__ import annotations

import json

import pytest

from app.models import AuditLog
from app.models.runtime_profile import RuntimeProfile
from app.services.connector_defaults_service import (
    NoAdminDefaults,
    admin_defaults_for,
    admin_defaults_sections,
    has_admin_defaults,
    reset_to_admin_defaults,
)
from app.services.connector_registry import CONNECTOR_REGISTRY, SETTINGS_CONNECTORS, get_connector_spec
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from tests.test_web_connector_settings import _bind_profile, _build_env, _saved

JIRA = get_connector_spec("jira")
GITHUB = get_connector_spec("github")
BROWSERSTACK = get_connector_spec("browserstack")

SEED_JIRA = {
    "jira": {
        "enabled": False,
        "instances": [{"name": "Prod", "url": "https://corp.atlassian.net", "token": "shared", "project": "ABC"}],
    }
}


# ------------------------------------------------------------------- registry


def test_every_settings_connector_but_the_model_provider_offers_a_reset():
    offered = {spec.type for spec in SETTINGS_CONNECTORS if spec.admin_defaults}
    assert offered == {spec.type for spec in SETTINGS_CONNECTORS} - {"llm"}
    assert CONNECTOR_REGISTRY["local_bridge"].admin_defaults is False


# ---------------------------------------------------------------- the service


def test_no_seed_for_the_connector_means_nothing_to_reset_to():
    seed = {"github": {"base_url": "https://x"}}
    assert has_admin_defaults(JIRA, seed) is False
    assert admin_defaults_for(JIRA, seed) == {}
    with pytest.raises(NoAdminDefaults):
        reset_to_admin_defaults(JIRA, {"jira": {"enabled": True}}, seed)
    with pytest.raises(NoAdminDefaults):
        reset_to_admin_defaults(JIRA, {}, {})


def test_reset_replaces_the_connectors_section_switch_and_credentials_included():
    mine = {
        "jira": {
            "enabled": True,
            "instances": [{"name": "Mine", "url": "https://old", "token": "own"}, {"name": "Other", "url": "https://other"}],
        },
        "github": {"base_url": "https://mine", "api_token": "ghp_mine"},
    }
    config, sections = reset_to_admin_defaults(JIRA, mine, SEED_JIRA)
    assert sections == ["jira"]
    assert config["jira"] == SEED_JIRA["jira"]
    # Everything outside the connector stays.
    assert config["github"] == {"base_url": "https://mine", "api_token": "ghp_mine"}
    assert "own" not in json.dumps(config["jira"])


def test_the_commit_identity_is_never_reset():
    assert admin_defaults_sections(GITHUB) == ("github",)
    seed = {"github": {"base_url": "https://ghe"}, "git": {"user": {"name": "CI Bot", "email": "ci@corp"}}}
    mine = {"github": {"base_url": "https://mine", "api_token": "t"}, "git": {"user": {"name": "Me"}}}
    config, sections = reset_to_admin_defaults(GITHUB, mine, seed)
    assert sections == ["github"]
    assert config["github"] == {"base_url": "https://ghe"}
    assert config["git"] == {"user": {"name": "Me"}}


def test_reset_copies_the_sanitized_seed_so_rows_the_save_would_drop_never_appear():
    seed = {"jira": {"instances": [{"name": "No URL"}, {"name": "Ok", "url": "https://x/"}]}}
    config, _ = reset_to_admin_defaults(JIRA, {}, seed)
    assert config["jira"] == {"instances": [{"name": "Ok", "url": "https://x"}]}


def test_a_member_without_the_section_gets_exactly_what_a_new_member_gets():
    seed = {"mobile-auto": {"enabled": True, "browserstack": {"username": "team", "access_key": "shared-key"}}}
    config, _ = reset_to_admin_defaults(BROWSERSTACK, {"proxy": {"enabled": True, "url": "http://p"}}, seed)
    assert config["mobile-auto"] == {"enabled": True, "browserstack": {"username": "team", "access_key": "shared-key"}}
    assert config["proxy"] == {"enabled": True, "url": "http://p"}


def test_the_seed_is_copied_not_shared():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://x"}]}}
    config, _ = reset_to_admin_defaults(JIRA, {}, seed)
    config["jira"]["instances"][0]["name"] = "changed"
    assert seed["jira"]["instances"][0]["name"] == "Prod"


# ------------------------------------------------------------------ the route


def _seed(env, config):
    RuntimeProfileSeedService(env.db).save_seed(config)


def _reset(env, connector_type):
    return env.client.post(f"/app/connectors/{connector_type}/defaults/reset")


def test_reset_is_404_for_the_model_provider_unknown_and_local_connectors(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        _seed(env, {"llm": {"api_key": "shared"}, **SEED_JIRA})
        for connector_type in ("llm", "local_bridge", "no-such-connector", "mobile"):
            assert _reset(env, connector_type).status_code == 404, connector_type
    finally:
        env.cleanup()


def test_reset_replaces_the_row_bumps_the_revision_audits_and_refreshes_the_list(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(
            env.db,
            env.agent,
            {
                "jira": {"enabled": True, "instances": [{"name": "Mine", "url": "https://old", "token": "own"}]},
                "github": {"base_url": "https://mine"},
            },
        )
        _seed(env, SEED_JIRA)
        resp = _reset(env, "jira")
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "connectorsChanged"
        assert 'data-settings-status="success"' in resp.text
        # Jinja escapes the apostrophe in the status line.
        assert "Reset to your administrator&#39;s defaults." in resp.text
        assert 'data-connector-type="jira"' in resp.text
        # The re-rendered form shows the seed's row, not the member's old one.
        assert 'value="https://corp.atlassian.net"' in resp.text and 'value="https://old"' not in resp.text

        saved = _saved(env.db, rp)
        assert saved["jira"] == SEED_JIRA["jira"]
        assert saved["github"] == {"base_url": "https://mine"}
        assert rp.revision == 2
        assert env.calls["apply"] == 1

        actions = [row.action for row in env.db.query(AuditLog).all()]
        assert "update_runtime_profile" in actions and "reset_connector_defaults" in actions
        row = env.db.query(AuditLog).filter(AuditLog.action == "reset_connector_defaults").one()
        assert json.loads(row.details_json) == {"connector": "jira", "sections": ["jira"]}
        assert "shared" not in (row.details_json or "") and "own" not in (row.details_json or "")
    finally:
        env.cleanup()


def test_reset_with_no_defaults_for_the_connector_saves_nothing(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"jira": {"instances": [{"name": "Mine", "url": "https://old"}]}})
        _seed(env, {"github": {"base_url": "https://ghe"}})
        resp = _reset(env, "jira")
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text
        assert "has not set Default connectors for Jira" in resp.text
        assert resp.headers.get("HX-Trigger") is None
        assert _saved(env.db, rp)["jira"]["instances"][0]["url"] == "https://old"
        assert rp.revision == 1
        assert env.calls["apply"] == 0
        assert env.db.query(AuditLog).count() == 0
    finally:
        env.cleanup()


def test_reset_that_changes_nothing_restarts_nothing(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, SEED_JIRA)
        _seed(env, SEED_JIRA)
        resp = _reset(env, "jira")
        assert resp.status_code == 200
        assert "already matches" in resp.text and "Nothing changed" in resp.text
        assert resp.headers.get("HX-Trigger") is None
        assert rp.revision == 1
        assert env.calls["apply"] == 0
        assert env.db.query(AuditLog).count() == 0
    finally:
        env.cleanup()


def test_reset_touches_only_the_signed_in_members_row(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"jira": {"instances": [{"name": "Mine", "url": "https://old"}]}})
        _seed(env, SEED_JIRA)
        env.set_user(env.other)
        assert _reset(env, "jira").status_code == 200
        rows = {row.owner_user_id: json.loads(row.config_json) for row in env.db.query(RuntimeProfile).all()}
        assert [row["name"] for row in rows[env.owner.id]["jira"]["instances"]] == ["Mine"]
        assert [row["name"] for row in rows[env.other.id]["jira"]["instances"]] == ["Prod"]
    finally:
        env.cleanup()


def test_panels_offer_the_reset_only_when_the_seed_has_the_connector_and_never_for_the_model_provider(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        _seed(env, {**SEED_JIRA, "proxy": {"url": "http://proxy.corp:8080"}, "llm": {"api_key": "shared"}})
        for connector_type in ("jira", "proxy"):
            text = env.client.get(f"/app/connectors/{connector_type}/panel").text
            assert "data-reset-defaults-button" in text, connector_type
            assert "Reset to defaults" in text, connector_type
        assert 'data-connector-label="Jira"' in env.client.get("/app/connectors/jira/panel").text
        for connector_type in ("github", "llm"):
            text = env.client.get(f"/app/connectors/{connector_type}/panel").text
            assert "data-reset-defaults-button" not in text, connector_type
            assert "Reset to defaults" not in text, connector_type
    finally:
        env.cleanup()
