"""Connector modes: follow the administrator's Default connectors, or keep your own.

Every settings connector of a member is either ``system`` (its values are the
current Default connectors and change with them) or ``custom`` (the member's
own values). docs/CONNECTORS_CONTRACT.md section 7.1.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user, require_admin
from app.models import AuditLog, User
from app.models.runtime_profile import RuntimeProfile
from app.services.connector_defaults_service import (
    MODE_CUSTOM,
    MODE_SYSTEM,
    backfill_connector_modes,
    changed_connector_types,
    connector_mode,
    dump_connector_modes,
    effective_config,
    following_types,
    managed_specs,
    parse_connector_modes,
    sanitized_seed,
    stored_connector_modes,
)
from app.services.connector_defaults_sync import SYNC_AUDIT_ACTION, sync_followers
from app.services.connector_registry import CONNECTOR_REGISTRY, SETTINGS_CONNECTORS, get_connector_spec
from app.services.runtime_profile_references import seed_config_error
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from app.services.runtime_profile_service import RuntimeProfileService
from tests.test_web_connector_settings import _bind_profile, _build_env, _saved

JIRA = get_connector_spec("jira")
GITHUB = get_connector_spec("github")
LLM = get_connector_spec("llm")
PROXY = get_connector_spec("proxy")

SEED = {
    "jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://corp.atlassian.net", "token": "shared", "project": "ABC"}]},
    "github": {"enabled": True, "base_url": "https://ghe.corp/api/v3"},
    "llm": {"api_key": "shared-key"},
    "git": {"user": {"name": "CI Bot"}},
}
MINE_JIRA = {"enabled": True, "instances": [{"name": "Mine", "url": "https://old", "token": "own"}]}


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _user(db, name="alice"):
    user = User(username=name, password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ------------------------------------------------------------------- registry


def test_every_settings_connector_but_the_model_provider_has_managed_sections():
    managed = {spec.type for spec in managed_specs()}
    assert managed == {spec.type for spec in SETTINGS_CONNECTORS} - {"llm"}
    assert LLM.managed_sections == ()
    # The commit identity is the member's name, never a shared default.
    assert GITHUB.managed_sections == ("github",)
    assert get_connector_spec("browserstack").managed_sections == ("mobile-auto",)
    assert CONNECTOR_REGISTRY["local_bridge"].managed_sections == ()


# ---------------------------------------------------------------- the service


def test_modes_round_trip_and_default_to_system():
    assert parse_connector_modes(None) == {}
    # A row not classified yet keeps its own values for every connector.
    assert stored_connector_modes(None) == {spec.type: MODE_CUSTOM for spec in managed_specs()}
    assert stored_connector_modes("{}") == {}
    assert parse_connector_modes("not json") == {}
    assert parse_connector_modes('{"jira": "custom", "llm": "custom", "nope": "custom", "github": "weird"}') == {"jira": "custom"}
    assert json.loads(dump_connector_modes({"jira": MODE_CUSTOM, "github": MODE_SYSTEM})) == {"jira": "custom"}
    assert connector_mode(JIRA, {}) == MODE_SYSTEM
    assert connector_mode(JIRA, {"jira": MODE_CUSTOM}) == MODE_CUSTOM
    assert connector_mode(LLM, {"llm": MODE_SYSTEM}) == MODE_CUSTOM
    assert following_types({"jira": MODE_CUSTOM}, ["jira", "github"]) == ["github"]


def test_effective_config_reads_system_connectors_from_the_seed_and_keeps_the_rest():
    mine = {"jira": MINE_JIRA, "github": {"base_url": "https://mine"}, "git": {"user": {"name": "Me"}}, "llm": {"api_key": "my-key"}}
    config = effective_config(mine, {"github": MODE_CUSTOM}, SEED)
    assert config["jira"] == sanitized_seed(SEED)["jira"]
    assert config["github"] == {"base_url": "https://mine"}
    # llm and git are never managed.
    assert config["git"] == {"user": {"name": "Me"}}
    assert config["llm"] == {"api_key": "my-key"}
    # A system-mode connector the seed has nothing for is absent, as for a new member.
    assert "proxy" not in effective_config({"proxy": {"url": "http://p"}}, {}, SEED)
    assert effective_config({"proxy": {"url": "http://p"}}, {"proxy": MODE_CUSTOM}, SEED)["proxy"] == {"url": "http://p"}


def test_a_seed_the_sanitizer_refuses_reads_as_empty_instead_of_raising():
    bad = {"jira": SEED["jira"], "aws": {"eks_clusters": [{"account": "a", "cluster": "c", "private_endpoint": "https://[corp].eks"}]}}
    assert sanitized_seed(bad) == {}
    assert effective_config({"jira": MINE_JIRA}, {}, bad).get("jira") is None
    assert seed_config_error(bad) is not None and "cannot be stored" in seed_config_error(bad)


def test_backfill_marks_custom_only_where_the_row_differs_from_the_seed():
    # A trailing slash is normalized away; the values are the seed's.
    same = {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://corp.atlassian.net/", "token": "shared", "project": "ABC"}]}}
    assert backfill_connector_modes(same, SEED) == {}
    assert backfill_connector_modes({"jira": MINE_JIRA}, SEED) == {"jira": MODE_CUSTOM}
    # Values for a connector the seed does not have are the member's own.
    assert backfill_connector_modes({"proxy": {"url": "http://p"}}, SEED) == {"proxy": MODE_CUSTOM}
    # A member without the section simply follows.
    assert backfill_connector_modes({"llm": {"api_key": "k"}}, SEED) == {}


def test_changed_connector_types_names_only_the_connectors_whose_defaults_moved():
    new = json.loads(json.dumps(SEED))
    new["jira"]["instances"][0]["project"] = "XYZ"
    new["llm"]["api_key"] = "rotated"
    assert changed_connector_types(SEED, new) == ["jira"]
    assert changed_connector_types(SEED, SEED) == []
    assert changed_connector_types({}, SEED) == ["jira", "github"]


def test_the_seed_is_refused_when_its_references_point_at_nothing():
    assert seed_config_error({"nexus": {"default_instance": "Prod", "instances": [{"name": "Production", "url": "https://n"}]}}).startswith("Default Nexus instance Prod")
    assert "Default AWS account nope" in seed_config_error({"aws": {"default_account": "nope", "accounts": [{"name": "dev", "account_id": "123456789012"}]}})
    assert "EKS cluster 1: ghost" in seed_config_error({"aws": {"accounts": [{"name": "dev", "account_id": "123456789012"}], "eks_clusters": [{"account": "ghost", "cluster": "c", "private_endpoint": "https://vpce.x"}]}})
    assert seed_config_error(SEED) is None


# -------------------------------------------------------- RuntimeProfileService


def test_a_new_member_follows_everything_and_only_the_model_provider_is_copied():
    db = _session()
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(_user(db))
    assert json.loads(profile.config_json) == {"llm": {"api_key": "shared-key"}, "git": {"user": {"name": "CI Bot"}}}
    assert profile.connector_modes_json == "{}"
    effective = service.effective_config_for(profile)
    assert effective["jira"]["instances"][0]["token"] == "shared"
    assert effective["github"] == {"enabled": True, "base_url": "https://ghe.corp/api/v3"}


def test_customize_starts_from_the_system_default_without_a_restart_and_follow_restores_it():
    db = _session()
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(_user(db))

    profile, restored = service.customize_connector(profile, JIRA)
    assert restored is False
    assert json.loads(profile.config_json)["jira"] == sanitized_seed(SEED)["jira"]
    assert service.connector_modes(profile) == {"jira": MODE_CUSTOM}
    assert profile.revision == 1  # nothing the assistants see changed
    profile, again = service.customize_connector(profile, JIRA)
    assert again is False

    # The member edits, then follows the system default again: their values
    # stay parked in the row and the effective settings move back to the seed.
    profile, _ = service.save_config(profile, {"jira": MINE_JIRA, "llm": {"api_key": "shared-key"}})
    assert profile.revision == 2
    profile, changed = service.follow_system_defaults(profile, JIRA)
    assert changed is True
    assert profile.revision == 3
    assert service.connector_modes(profile) == {}
    assert json.loads(profile.config_json)["jira"] == MINE_JIRA
    assert service.effective_config_for(profile)["jira"] == sanitized_seed(SEED)["jira"]

    # Customizing again brings the parked values back.
    profile, restored = service.customize_connector(profile, JIRA)
    assert restored is True
    assert service.effective_config_for(profile)["jira"] == MINE_JIRA
    assert profile.revision == 3  # the row already held them: no bump


def test_following_a_default_equal_to_the_members_values_changes_nothing_and_parks_nothing():
    db = _session()
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(_user(db))
    profile, _ = service.customize_connector(profile, JIRA)
    profile, changed = service.follow_system_defaults(profile, JIRA)
    assert changed is False and profile.revision == 1
    # The copy of the defaults is not kept: customizing later starts from the
    # defaults as they are then, not from this stale copy.
    assert "jira" not in json.loads(profile.config_json)
    new_seed = json.loads(json.dumps(SEED))
    new_seed["jira"]["instances"][0]["project"] = "XYZ"
    RuntimeProfileSeedService(db).save_seed(new_seed)
    profile, restored = service.customize_connector(profile, JIRA)
    assert restored is False
    assert json.loads(profile.config_json)["jira"]["instances"][0]["project"] == "XYZ"


def test_backfill_classifies_rows_from_before_modes_and_drops_the_stale_copies():
    db = _session()
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    unchanged = _user(db, "unchanged")
    changed = _user(db, "changed")
    rows = []
    for user, jira in ((unchanged, sanitized_seed(SEED)["jira"]), (changed, MINE_JIRA)):
        row = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"jira": jira, "github": SEED["github"], "llm": {"api_key": "k"}}), revision=4, is_default=True, connector_modes_json=None)
        db.add(row)
        rows.append(row)
    db.commit()

    assert service.backfill_connector_modes() == 2
    assert service.backfill_connector_modes() == 0

    db.refresh(rows[0])
    db.refresh(rows[1])
    assert service.connector_modes(rows[0]) == {}
    assert json.loads(rows[0].config_json) == {"llm": {"api_key": "k"}}
    assert service.connector_modes(rows[1]) == {"jira": MODE_CUSTOM}
    assert json.loads(rows[1].config_json) == {"jira": MINE_JIRA, "llm": {"api_key": "k"}}
    assert rows[0].revision == 4 and rows[1].revision == 4
    assert service.effective_config_for(rows[1])["jira"] == MINE_JIRA
    assert service.effective_config_for(rows[0])["jira"] == sanitized_seed(SEED)["jira"]


def test_the_secret_is_rendered_from_the_effective_settings():
    class FakeK8s:
        enabled = True

        def __init__(self):
            self.secrets = {}

        def upsert_secret(self, name, data):
            self.secrets[name] = data

    db = _session()
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(_user(db))
    k8s = FakeK8s()
    RuntimeProfileSecretService(k8s_service=k8s).sync_profile_secret(profile, db)
    (payload,) = [json.loads(data["config.json"]) for data in k8s.secrets.values()]
    assert payload["config"]["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"


# --------------------------------------------------- the background rollout


class _CountingSecrets:
    def __init__(self):
        self.applied = []

    def apply_profile_save(self, _db, profile):
        self.applied.append(profile.id)
        if profile.name == "boom":
            raise RuntimeError("k8s down")
        return {"restarted_agent_ids": ["a"], "pending_agent_ids": ["b"], "failed_agent_ids": []}


def test_sync_followers_updates_only_members_following_a_changed_connector():
    db = _session()
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, class_=Session)
    RuntimeProfileSeedService(db).save_seed(SEED)
    service = RuntimeProfileService(db)
    follower = service.get_or_create_for_user(_user(db, "follower"))
    custom = service.get_or_create_for_user(_user(db, "custom"))
    custom, _ = service.customize_connector(custom, JIRA)
    other = service.get_or_create_for_user(_user(db, "other"))
    other, _ = service.customize_connector(other, GITHUB)
    failing = service.get_or_create_for_user(_user(db, "failing"))
    failing.name = "boom"
    db.commit()
    secrets = _CountingSecrets()

    summary = sync_followers(["jira"], secret_service=secrets, triggered_by_user_id=1, session_factory=factory)

    assert sorted(secrets.applied) == sorted([follower.id, other.id, failing.id])
    assert summary["members"] == 3 and summary["restarted"] == 2 and summary["pending"] == 2 and summary["failed_members"] == 1
    for row in (follower, other):
        db.refresh(row)
        assert row.revision == 2
    db.refresh(custom)
    assert custom.revision == 1
    audit = db.query(AuditLog).filter(AuditLog.action == SYNC_AUDIT_ACTION).one()
    assert json.loads(audit.details_json)["connectors"] == ["jira"]
    assert sync_followers([], secret_service=secrets, session_factory=factory)["members"] == 0


# ------------------------------------------------------------------ the panel


def _seed(env, config):
    RuntimeProfileSeedService(env.db).save_seed(config)


def test_a_new_members_panel_follows_the_system_default_read_only(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _seed(env, SEED)
        resp = env.client.get("/app/connectors/jira/panel")
        assert resp.status_code == 200
        text = resp.text
        assert 'data-connector-mode="system"' in text
        assert "Follows your administrator" in text
        assert 'data-connector-mode-button="custom"' in text and "Customize" in text
        assert 'hx-post="/app/connectors/jira/mode"' in text
        assert 'value="https://corp.atlassian.net"' in text
        assert '<button type="submit" class="portal-btn is-primary">Save</button>' not in text
        assert "choose Customize above" in text
        # The row itself holds nothing for Jira.
        rp = env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).one()
        assert "jira" not in json.loads(rp.config_json)
    finally:
        env.cleanup()


def test_a_connector_without_a_system_default_says_so_and_the_model_provider_has_no_switch(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _seed(env, SEED)
        proxy = env.client.get("/app/connectors/proxy/panel").text
        assert "has not set up Proxy yet" in proxy and "Customize" in proxy
        llm = env.client.get("/app/connectors/llm/panel").text
        assert "data-connector-mode-section" not in llm
        assert '<button type="submit" class="portal-btn is-primary">Save</button>' in llm
    finally:
        env.cleanup()


def test_customize_copies_the_default_into_the_row_without_a_restart(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _seed(env, SEED)
        resp = env.client.post("/app/connectors/jira/mode", data={"mode": "custom"})
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "connectorsChanged"
        assert 'data-connector-mode="custom"' in resp.text
        assert "starting from the system default" in resp.text
        assert 'data-connector-mode-button="system"' in resp.text and "Use system default" in resp.text
        assert '<button type="submit" class="portal-btn is-primary">Save</button>' in resp.text
        rp = env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).one()
        assert json.loads(rp.config_json)["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"
        assert json.loads(rp.connector_modes_json) == {"jira": "custom"}
        assert rp.revision == 1
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()


def test_use_system_default_replaces_the_members_values_audits_and_restarts(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"jira": MINE_JIRA})
        rp.connector_modes_json = json.dumps({"jira": "custom"})
        env.db.commit()
        _seed(env, SEED)
        resp = env.client.post("/app/connectors/jira/mode", data={"mode": "system"})
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "connectorsChanged"
        assert 'data-settings-status="success"' in resp.text
        assert "Jira now follows your administrator&#39;s Default connectors." in resp.text
        assert 'value="https://corp.atlassian.net"' in resp.text and 'value="https://old"' not in resp.text
        env.db.refresh(rp)
        assert rp.revision == 2
        assert json.loads(rp.connector_modes_json) == {}
        # The member's own values stay parked in the row.
        assert json.loads(rp.config_json)["jira"] == MINE_JIRA
        assert env.calls["apply"] == 1
        audit = env.db.query(AuditLog).filter(AuditLog.action == "follow_connector_defaults").one()
        assert json.loads(audit.details_json)["sections"] == ["jira"]
        assert "shared" not in (audit.details_json or "") and "own" not in (audit.details_json or "")

        again = env.client.post("/app/connectors/jira/mode", data={"mode": "system"})
        assert "Nothing changed" in again.text
        assert env.calls["apply"] == 1
    finally:
        env.cleanup()


def test_a_failed_rollout_after_following_says_what_to_do(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"jira": MINE_JIRA})
        rp.connector_modes_json = json.dumps({"jira": "custom"})
        env.db.commit()
        _seed(env, SEED)

        def _boom(_db, _profile):
            raise RuntimeError("k8s down")

        monkeypatch.setattr("app.web.runtime_profile_secret_service.apply_profile_save", _boom)
        resp = env.client.post("/app/connectors/jira/mode", data={"mode": "system"})
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text
        assert "Jira now follows your administrator&#39;s Default connectors, but handing the change to your assistants failed. Use Restart on the assistants listed above." in resp.text
        env.db.refresh(rp)
        assert rp.revision == 2 and json.loads(rp.connector_modes_json) == {}
    finally:
        env.cleanup()


def test_save_is_refused_while_a_connector_follows_the_system_default(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _seed(env, SEED)
        env.client.get("/app/connectors/jira/panel")
        resp = env.client.post(
            "/app/connectors/jira/save",
            data={"__touch_jira": "1", "jira_enabled": "on", "jira_instance_count": "1", "jira_instances_0_name": "X", "jira_instances_0_url": "https://x"},
        )
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text
        assert "Choose Customize to edit it" in resp.text
        rp = env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).one()
        assert "jira" not in json.loads(rp.config_json)
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()


def test_a_custom_save_keeps_the_other_connectors_following(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _seed(env, SEED)
        env.client.post("/app/connectors/jira/mode", data={"mode": "custom"})
        resp = env.client.post(
            "/app/connectors/jira/save",
            data={"__touch_jira": "1", "jira_enabled": "on", "jira_instance_count": "1", "jira_instances_0_name": "X", "jira_instances_0_url": "https://x"},
        )
        assert 'data-settings-status="success"' in resp.text
        rp = env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).one()
        stored = json.loads(rp.config_json)
        assert stored["jira"]["instances"][0]["url"] == "https://x"
        # GitHub still follows: no copy in the row, the seed's value on the panel.
        assert "github" not in stored
        assert 'value="https://ghe.corp/api/v3"' in env.client.get("/app/connectors/github/panel").text
    finally:
        env.cleanup()


def test_a_connection_test_in_system_mode_runs_on_the_default(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _seed(env, SEED)
        seen = {}

        async def _fake_run_test(target, config, runtime_type="native"):
            seen["config"] = config
            return True, "ok"

        monkeypatch.setattr("app.web.runtime_profile_test_service.run_test", _fake_run_test)
        resp = env.client.post("/app/connectors/jira/test/jira", data={})
        assert resp.status_code == 200 and resp.json()["ok"] is True
        assert seen["config"]["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"
    finally:
        env.cleanup()


def test_mode_route_rejects_unknown_connectors_and_modes(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        for connector_type in ("llm", "local_bridge", "no-such-connector", "mobile"):
            assert env.client.post(f"/app/connectors/{connector_type}/mode", data={"mode": "system"}).status_code == 404, connector_type
        assert env.client.post("/app/connectors/jira/mode", data={"mode": "theirs"}).status_code == 400
    finally:
        env.cleanup()


def test_mode_switch_touches_only_the_signed_in_members_row(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"jira": MINE_JIRA})
        _seed(env, SEED)
        env.set_user(env.other)
        assert env.client.post("/app/connectors/jira/mode", data={"mode": "custom"}).status_code == 200
        rows = {row.owner_user_id: row for row in env.db.query(RuntimeProfile).all()}
        assert json.loads(rows[env.owner.id].config_json)["jira"] == MINE_JIRA
        assert json.loads(rows[env.other.id].config_json)["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"
    finally:
        env.cleanup()


# ---------------------------------------------------------------- admin save


def test_admin_save_refuses_a_seed_members_could_not_save_themselves(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        resp = env.client.post(
            "/app/admin/default-connections/save",
            data={
                "nexus_enabled": "on",
                "nexus_default_instance": "Prod",
                "nexus_instance_count": "1",
                "nexus_instances_0_name": "Production",
                "nexus_instances_0_url": "https://nexus.corp",
            },
        )
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text or "Default Nexus instance Prod" in resp.text
        assert RuntimeProfileSeedService(env.db).get_seed() == {}
    finally:
        env.cleanup()


def test_admin_save_updates_the_followers_in_the_background(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        service = RuntimeProfileService(env.db)
        follower = _bind_profile(env.db, env.agent, {})
        follower.connector_modes_json = "{}"
        env.db.commit()
        custom = service.get_or_create_for_user(env.other)
        custom, _ = service.customize_connector(custom, JIRA)
        resp = env.client.post(
            "/app/admin/default-connections/save",
            data={
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_enabled": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://corp.atlassian.net",
                "jira_instances_0_project": "ABC",
            },
        )
        assert resp.status_code == 200
        assert "Members who follow Jira are being updated in the background" in resp.text
        env.db.refresh(follower)
        env.db.refresh(custom)
        assert follower.revision == 2
        assert custom.revision == 1
        assert env.calls["apply"] == 1
        actions = [row.action for row in env.db.query(AuditLog).all()]
        assert "update_runtime_profile_seed" in actions and SYNC_AUDIT_ACTION in actions

        # Saving the same values again changes nothing for anyone.
        again = env.client.post(
            "/app/admin/default-connections/save",
            data={
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_enabled": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://corp.atlassian.net",
                "jira_instances_0_project": "ABC",
            },
        )
        assert "Nothing changed for the members who follow them" in again.text
        assert env.calls["apply"] == 1
    finally:
        env.cleanup()


# ------------------------------------------------------------------ the APIs


@pytest.fixture
def api_env(monkeypatch):
    from app.main import app
    import app.api.admin as admin_api
    import app.api.runtime_profiles as runtime_profiles_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    db = factory()
    alice = _user(db, "alice")
    admin = User(username="root", password_hash="test", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    secrets = _CountingSecrets()
    monkeypatch.setattr(runtime_profiles_api, "runtime_profile_secret_service", secrets)
    monkeypatch.setattr(admin_api, "runtime_profile_secret_service", secrets)
    monkeypatch.setattr(admin_api, "SessionLocal", factory)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: alice
    app.dependency_overrides[require_admin] = lambda: admin
    try:
        yield SimpleNamespace(client=TestClient(app), db=db, alice=alice, admin=admin, secrets=secrets)
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_runtime_profile_api_reports_effective_settings_and_modes(api_env):
    RuntimeProfileSeedService(api_env.db).save_seed(SEED)
    got = api_env.client.get("/api/runtime-profile").json()
    assert got["connector_modes"]["jira"] == "system" and got["connector_modes"]["github"] == "system"
    assert "llm" not in got["connector_modes"]
    assert json.loads(got["config_json"])["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"

    patched = api_env.client.patch(
        "/api/runtime-profile",
        json={"config_json": json.dumps({"jira": MINE_JIRA, "github": SEED["github"]})},
    ).json()
    assert patched["connector_modes"] == {**got["connector_modes"], "jira": "custom"}
    assert json.loads(patched["config_json"])["jira"]["instances"][0]["url"] == "https://old"
    assert api_env.secrets.applied  # the settings the assistants get changed

    back = api_env.client.patch("/api/runtime-profile", json={"connector_modes": {"jira": "system"}}).json()
    assert back["connector_modes"]["jira"] == "system"
    assert json.loads(back["config_json"])["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"
    assert back["revision"] == patched["revision"] + 1


def test_admin_seed_api_validates_and_rolls_out(api_env):
    RuntimeProfileService(api_env.db).get_or_create_for_user(api_env.alice)
    refused = api_env.client.put(
        "/api/admin/runtime-profile-seed",
        json={"seed": {"aws": {"default_account": "nope", "accounts": [{"name": "dev", "account_id": "123456789012"}]}}},
    )
    assert refused.status_code == 422 and "Default AWS account nope" in refused.json()["detail"]

    saved = api_env.client.put("/api/admin/runtime-profile-seed", json={"seed": SEED})
    assert saved.status_code == 200
    assert saved.json()["changed_connectors"] == ["jira", "github"]
    assert api_env.secrets.applied  # alice follows both; updated after the response
