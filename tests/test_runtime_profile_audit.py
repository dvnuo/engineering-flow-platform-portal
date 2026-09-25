"""Audit rows for runtime profile changes.

Saving a member's connector settings used to leave no trace, while the
seed and every agent action did. A profile carries credentials, so the row
says *what* changed -- the sections, and the dotted paths of the secret fields
whose value differs -- and never what it changed to.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests.test_runtime_profile_api import _build_client as _build_api_env

from app.db import Base
from app.models import AuditLog, User
from app.models.runtime_profile import RuntimeProfile
from app.services.runtime_profile_audit import runtime_profile_config_changes

BEFORE = {
    "aws": {"enabled": True, "domain": "HBEU", "username": "u", "password": "old-adfs"},
    "splunk": {
        "enabled": True,
        "instances": [
            {"name": "prod", "url": "https://s:8089", "token": "old-splunk"},
            {"name": "dr", "url": "https://dr:8089", "token": "dr-token"},
        ],
    },
    "github": {"enabled": True, "api_token": "ghp_same"},
    "git": {"user": {"name": "Ada"}},
}


# ------------------------------------------------------------------- diff


def test_changes_name_the_sections_and_secret_paths_but_never_values():
    after = json.loads(json.dumps(BEFORE))
    after["aws"]["password"] = "new-adfs"
    after["splunk"]["instances"][0]["token"] = "new-splunk"
    after["git"]["user"]["name"] = "Grace"

    changes = runtime_profile_config_changes(BEFORE, after)

    assert changes == {
        "sections": ["aws", "git", "splunk"],
        "secret_fields_changed": ["aws.password", "splunk.instances[0].token"],
    }
    dumped = json.dumps(changes)
    for value in ("old-adfs", "new-adfs", "old-splunk", "new-splunk", "ghp_same", "dr-token"):
        assert value not in dumped


def test_unchanged_configs_diff_to_nothing():
    assert runtime_profile_config_changes(BEFORE, json.loads(json.dumps(BEFORE))) == {"sections": [], "secret_fields_changed": []}
    assert runtime_profile_config_changes(None, {}) == {"sections": [], "secret_fields_changed": []}


def test_added_and_removed_sections_and_secrets_are_both_changes():
    changes = runtime_profile_config_changes(BEFORE, {"aws": BEFORE["aws"]})
    assert changes["sections"] == ["git", "github", "splunk"]
    assert changes["secret_fields_changed"] == [
        "github.api_token",
        "splunk.instances[0].token",
        "splunk.instances[1].token",
    ]

    added = runtime_profile_config_changes({}, {"pgsql": {"enabled": True, "instances": [{"name": "db", "host": "h", "password": "pw"}]}})
    assert added == {"sections": ["pgsql"], "secret_fields_changed": ["pgsql.instances[0].password"]}


def test_a_secret_set_to_blank_counts_as_removed_and_blank_ones_are_ignored():
    changes = runtime_profile_config_changes(
        {"proxy": {"enabled": True, "password": "p"}, "llm": {"api_key": ""}},
        {"proxy": {"enabled": True, "password": ""}, "llm": {"api_key": "   "}},
    )
    assert changes["secret_fields_changed"] == ["proxy.password"]


# ------------------------------------------------------------------- API


def _audit_rows(db, action):
    return db.query(AuditLog).filter_by(action=action).order_by(AuditLog.id.asc()).all()


def _build_api_client(monkeypatch):
    env = _build_api_env(monkeypatch)
    return env.client, env.db, env.u1, env.cleanup


def test_api_update_writes_audit_rows_only_when_the_config_changes(monkeypatch):
    client, db, u1, cleanup = _build_api_client(monkeypatch)
    try:
        profile_id = client.get("/api/runtime-profile").json()["id"]
        # Creating the member's row on first read is not an audited change.
        assert db.query(AuditLog).count() == 0

        nexus = {"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://n", "password": "nexus-pass"}]}}
        first = client.patch("/api/runtime-profile", json={"config_json": json.dumps(nexus)})
        assert first.status_code == 200

        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_type == "runtime_profile"
        assert rows[0].target_id == profile_id
        assert rows[0].user_id == u1.id
        assert json.loads(rows[0].details_json) == {"sections": ["nexus"], "secret_fields_changed": ["nexus.instances[0].password"]}
        assert "nexus-pass" not in rows[0].details_json

        both = {
            **nexus,
            "pgsql": {"enabled": True, "instances": [{"name": "db", "host": "h", "database": "o", "username": "u", "password": "pg-pass"}]},
        }
        updated = client.patch("/api/runtime-profile", json={"config_json": json.dumps(both)})
        assert updated.status_code == 200
        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 2
        assert rows[1].target_id == profile_id and rows[1].user_id == u1.id
        assert json.loads(rows[1].details_json) == {"sections": ["pgsql"], "secret_fields_changed": ["pgsql.instances[0].password"]}
        assert "pg-pass" not in rows[1].details_json and "nexus-pass" not in rows[1].details_json

        # Saving the same settings again is not an update.
        again = client.patch("/api/runtime-profile", json={"config_json": json.dumps(both)})
        assert again.status_code == 200
        assert len(_audit_rows(db, "update_runtime_profile")) == 2
    finally:
        cleanup()


# ------------------------------------------- the audit must not touch the save


def test_the_diff_never_mutates_the_configs_it_inspects():
    """The audit reads the live config dicts, so it must not write to them.

    _secret_values_by_path walks the same objects the caller is about to
    persist or has just persisted. If it ever assigned into them -- blanking a
    password to keep it out of the row, say -- the damage would land in the
    saved config, which is the one thing an audit module must never do.
    """
    before = json.loads(json.dumps(BEFORE))
    after = json.loads(json.dumps(BEFORE))
    after["aws"]["password"] = "new-adfs"
    before_pristine = json.loads(json.dumps(before))
    after_pristine = json.loads(json.dumps(after))

    runtime_profile_config_changes(before, after)

    assert before == before_pristine
    assert after == after_pristine
    assert before["aws"]["password"] == "old-adfs"
    assert after["aws"]["password"] == "new-adfs"
    assert after["splunk"]["instances"][0]["token"] == "old-splunk"


def test_a_failing_audit_leaves_the_saved_profile_and_its_password_intact(monkeypatch):
    """An audit that blows up must cost nothing but the row.

    The row is written after the settings are committed, and the handler rolls
    back only its own failed INSERT. This drives a real save through the API
    with AuditRepository.create raising, then reads the profile back out of
    the database to prove the config -- password included -- is exactly what
    was posted.
    """
    from app.repositories import audit_repo as audit_repo_module

    client, db, _u1, cleanup = _build_api_client(monkeypatch)
    try:
        config = {
            "pgsql": {
                "enabled": True,
                "instances": [{"name": "db", "host": "h", "database": "o", "username": "u", "password": "pg-secret"}],
            }
        }

        def explode(self, *args, **kwargs):
            raise RuntimeError("audit table is gone")

        monkeypatch.setattr(audit_repo_module.AuditRepository, "create", explode)

        saved = client.patch("/api/runtime-profile", json={"config_json": json.dumps(config)})
        assert saved.status_code == 200, saved.text
        profile_id = saved.json()["id"]

        stored = json.loads(db.get(RuntimeProfile, profile_id).config_json)
        assert stored["pgsql"]["instances"][0]["password"] == "pg-secret"
        assert stored == config
        assert _audit_rows(db, "update_runtime_profile") == []

        # The same on the next save: the new password lands, the row does not.
        config["pgsql"]["instances"][0]["password"] = "pg-rotated"
        updated = client.patch("/api/runtime-profile", json={"config_json": json.dumps(config)})
        assert updated.status_code == 200, updated.text

        db.expire_all()
        stored = json.loads(db.get(RuntimeProfile, profile_id).config_json)
        assert stored["pgsql"]["instances"][0]["password"] == "pg-rotated"
        assert _audit_rows(db, "update_runtime_profile") == []
    finally:
        cleanup()


def test_the_audit_does_not_change_what_gets_persisted(monkeypatch):
    """The saved config is the same whether the audit works or explodes.

    This is the property that matters: the audit observes the save, it is not
    part of it. The same three saves run twice -- once
    normally, once with AuditRepository.create raising -- and the stored
    config_json is compared byte for byte. A module that blanked a password,
    reordered a section or dropped a key on its way through would show up
    here as a difference between the two runs.

    The blanked-password step is deliberate: the portal drops a blank secret
    rather than storing it, so an empty password box cannot wipe a stored
    credential (the same rule master applies to jenkins and jira). That is the
    sanitizer's decision, and this pins that the audit does not alter it.
    """
    from app.repositories import audit_repo as audit_repo_module

    filled = {"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://n", "password": "keep-me"}]}}
    rotated = json.loads(json.dumps(filled))
    rotated["nexus"]["instances"][0]["password"] = "rotated"
    blanked = json.loads(json.dumps(filled))
    blanked["nexus"]["instances"][0]["password"] = ""

    def run(break_audit: bool) -> list[str]:
        client, db, _u1, cleanup = _build_api_client(monkeypatch)
        try:
            if break_audit:
                def explode(self, *args, **kwargs):
                    raise RuntimeError("audit table is gone")

                monkeypatch.setattr(audit_repo_module.AuditRepository, "create", explode)

            stored = []
            for payload in (filled, rotated, blanked):
                res = client.patch("/api/runtime-profile", json={"config_json": json.dumps(payload)})
                assert res.status_code == 200, res.text
                db.expire_all()
                stored.append(db.get(RuntimeProfile, res.json()["id"]).config_json)

            if break_audit:
                assert _audit_rows(db, "update_runtime_profile") == []
            else:
                assert len(_audit_rows(db, "update_runtime_profile")) == 3
            return stored
        finally:
            cleanup()

    with_audit = run(break_audit=False)
    without_audit = run(break_audit=True)

    assert with_audit == without_audit
    # And the saves themselves did what was asked, in both runs.
    assert json.loads(with_audit[0])["nexus"]["instances"][0]["password"] == "keep-me"
    assert json.loads(with_audit[1])["nexus"]["instances"][0]["password"] == "rotated"
    assert "password" not in json.loads(with_audit[2])["nexus"]["instances"][0]


def test_api_refusals_leave_no_audit_row(monkeypatch):
    client, db, _u1, cleanup = _build_api_client(monkeypatch)
    try:
        assert client.patch("/api/runtime-profile", json={"config_json": "not-json"}).status_code == 422
        assert client.patch("/api/runtime-profile", json={"config_json": "[]"}).status_code == 422
        # A body without a config is not a save either.
        assert client.patch("/api/runtime-profile", json={}).status_code == 200

        assert db.query(AuditLog).count() == 0
    finally:
        cleanup()


# ------------------------------------------------------------------- web


def _build_web_client(monkeypatch, config=None):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    rp = RuntimeProfile(owner_user_id=owner.id, name="Default", config_json=json.dumps(config or {}), revision=1, is_default=True)
    db.add(rp)
    db.commit()
    db.refresh(rp)

    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname=owner.username),
    )
    monkeypatch.setattr(
        "app.web.runtime_profile_secret_service.apply_profile_save",
        lambda _db, _profile: {"restarted_agent_ids": [], "pending_agent_ids": [], "failed_agent_ids": []},
    )

    return TestClient(app), db, owner, rp, db.close


def test_connector_save_writes_an_update_row_naming_the_touched_sections(monkeypatch):
    client, db, owner, rp, cleanup = _build_web_client(
        monkeypatch, {"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://n", "password": "kept"}]}}
    )
    try:
        resp = client.post(
            "/app/connectors/splunk/save",
            data={
                "__touch_splunk": "1",
                "splunk_enabled": "on",
                "splunk_instance_count": "1",
                "splunk_instances_0_enabled": "1",
                "splunk_instances_0_name": "prod",
                "splunk_instances_0_url": "https://s:8089",
                "splunk_instances_0_token": "splunk-token",
            },
        )
        assert resp.status_code == 200

        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_type == "runtime_profile"
        assert rows[0].target_id == rp.id
        assert rows[0].user_id == owner.id
        assert json.loads(rows[0].details_json) == {"sections": ["splunk"], "secret_fields_changed": ["splunk.instances[0].token"]}
        assert "splunk-token" not in rows[0].details_json and "kept" not in rows[0].details_json
    finally:
        cleanup()


def test_connector_save_writes_a_row_only_when_the_config_changed(monkeypatch):
    client, db, _owner, rp, cleanup = _build_web_client(
        monkeypatch, {"aws": {"enabled": True, "domain": "HBEU", "username": "u", "password": "old"}}
    )
    try:
        form = {"__touch_aws": "1", "aws_enabled": "on", "aws_domain": "HBEU", "aws_username": "u", "aws_password": "new"}
        resp = client.post("/app/connectors/aws/save", data=form)
        assert resp.status_code == 200
        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_id == rp.id
        assert json.loads(rows[0].details_json) == {"sections": ["aws"], "secret_fields_changed": ["aws.password"]}
        assert "new" not in json.loads(rows[0].details_json)["secret_fields_changed"]

        # Saving the same thing again changes nothing and is not an update.
        again = client.post("/app/connectors/aws/save", data=form)
        assert again.status_code == 200
        assert len(_audit_rows(db, "update_runtime_profile")) == 1
    finally:
        cleanup()


def test_a_rejected_form_leaves_no_audit_row(monkeypatch):
    client, db, _owner, _rp, cleanup = _build_web_client(monkeypatch, {})
    try:
        resp = client.post(
            "/app/connectors/nexus/save",
            data={"__touch_nexus": "1", "nexus_enabled": "on", "nexus_default_instance": "ghost", "nexus_instance_count": "0"},
        )
        assert resp.status_code == 200
        assert "Default Nexus instance ghost" in resp.text
        assert db.query(AuditLog).count() == 0
    finally:
        cleanup()


@pytest.mark.parametrize("action", ["create_runtime_profile", "update_runtime_profile", "delete_runtime_profile"])
def test_action_names_follow_the_verb_noun_convention(action):
    from app.services import runtime_profile_audit

    assert action in {
        runtime_profile_audit.CREATE_RUNTIME_PROFILE,
        runtime_profile_audit.UPDATE_RUNTIME_PROFILE,
        runtime_profile_audit.DELETE_RUNTIME_PROFILE,
    }
