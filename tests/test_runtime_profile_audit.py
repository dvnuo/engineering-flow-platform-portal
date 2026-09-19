"""Audit rows for runtime profile changes.

Creating, updating and deleting a profile used to leave no trace, while the
seed and every agent action did. A profile carries credentials, so the row
says *what* changed -- the sections, and the dotted paths of the secret fields
whose value differs -- and never what it changed to.
"""
import json

import pytest

from tests.test_runtime_profiles_api import _build_client as _build_api_client
from tests.test_web_runtime_profile_settings import _bind_profile, _build_client as _build_web_client

from app.models import AuditLog
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


def test_api_create_update_and_delete_write_audit_rows(monkeypatch):
    client, db, u1, _u2, _set_user, cleanup = _build_api_client(monkeypatch)
    try:
        created = client.post(
            "/api/runtime-profiles",
            json={
                "name": "Audited",
                "config_json": json.dumps({"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://n", "password": "nexus-pass"}]}}),
            },
        )
        assert created.status_code == 200
        profile_id = created.json()["id"]

        rows = _audit_rows(db, "create_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_type == "runtime_profile"
        assert rows[0].target_id == profile_id
        assert rows[0].user_id == u1.id
        assert json.loads(rows[0].details_json) == {"sections": ["nexus"], "secret_fields_changed": ["nexus.instances[0].password"]}
        assert "nexus-pass" not in rows[0].details_json

        # A second profile so the first can be deleted later.
        other = client.post("/api/runtime-profiles", json={"name": "Other", "config_json": "{}"})
        assert other.status_code == 200

        updated = client.patch(
            f"/api/runtime-profiles/{profile_id}",
            json={
                "config_json": json.dumps(
                    {
                        "nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://n", "password": "nexus-pass"}]},
                        "pgsql": {"enabled": True, "instances": [{"name": "db", "host": "h", "database": "o", "username": "u", "password": "pg-pass"}]},
                    }
                )
            },
        )
        assert updated.status_code == 200
        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_id == profile_id and rows[0].user_id == u1.id
        assert json.loads(rows[0].details_json) == {"sections": ["pgsql"], "secret_fields_changed": ["pgsql.instances[0].password"]}
        assert "pg-pass" not in rows[0].details_json and "nexus-pass" not in rows[0].details_json

        # A metadata-only update is still an update, with nothing to list.
        renamed = client.patch(f"/api/runtime-profiles/{profile_id}", json={"description": "renamed"})
        assert renamed.status_code == 200
        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 2
        assert json.loads(rows[1].details_json) == {"sections": [], "secret_fields_changed": []}

        deleted = client.delete(f"/api/runtime-profiles/{profile_id}")
        assert deleted.status_code == 200
        rows = _audit_rows(db, "delete_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_id == profile_id and rows[0].user_id == u1.id
        details = json.loads(rows[0].details_json)
        assert details["sections"] == ["nexus", "pgsql"]
        assert details["secret_fields_changed"] == ["nexus.instances[0].password", "pgsql.instances[0].password"]
        assert "pass" not in rows[0].details_json.replace("password", "")
    finally:
        cleanup()


def test_api_refusals_leave_no_audit_row(monkeypatch):
    client, db, _u1, u2, set_user, cleanup = _build_api_client(monkeypatch)
    try:
        theirs = RuntimeProfile(owner_user_id=u2.id, name="Theirs", config_json=json.dumps({"github": {"api_token": "ghp_theirs"}}), is_default=True)
        db.add(theirs)
        db.commit()
        db.refresh(theirs)

        assert client.patch(f"/api/runtime-profiles/{theirs.id}", json={"description": "x"}).status_code == 404
        assert client.delete(f"/api/runtime-profiles/{theirs.id}").status_code == 404
        # The owner's last profile cannot go either.
        set_user(u2)
        assert client.delete(f"/api/runtime-profiles/{theirs.id}").status_code == 409

        assert db.query(AuditLog).count() == 0
    finally:
        cleanup()


# ------------------------------------------------------------------- web


def test_profile_panel_save_writes_an_update_row_naming_the_touched_sections(monkeypatch):
    client, db, agent, cleanup = _build_web_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://n", "password": "kept"}]}})
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "name": "rp",
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
        assert rows[0].user_id == agent.owner_user_id
        assert json.loads(rows[0].details_json) == {"sections": ["splunk"], "secret_fields_changed": ["splunk.instances[0].token"]}
        assert "splunk-token" not in rows[0].details_json and "kept" not in rows[0].details_json
    finally:
        cleanup()


def test_assistant_settings_save_writes_a_row_only_when_the_config_changed(monkeypatch):
    client, db, agent, cleanup = _build_web_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"aws": {"enabled": True, "domain": "HBEU", "username": "u", "password": "old"}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_aws": "1", "aws_enabled": "on", "aws_domain": "HBEU", "aws_username": "u", "aws_password": "new"},
        )
        assert resp.status_code == 200
        rows = _audit_rows(db, "update_runtime_profile")
        assert len(rows) == 1
        assert rows[0].target_id == rp.id
        assert json.loads(rows[0].details_json) == {"sections": ["aws"], "secret_fields_changed": ["aws.password"]}
        assert "new" not in json.loads(rows[0].details_json)["secret_fields_changed"]

        # Saving the same thing again changes nothing and is not an update.
        again = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_aws": "1", "aws_enabled": "on", "aws_domain": "HBEU", "aws_username": "u", "aws_password": "new"},
        )
        assert again.status_code == 200
        assert len(_audit_rows(db, "update_runtime_profile")) == 1
    finally:
        cleanup()


def test_a_rejected_form_leaves_no_audit_row(monkeypatch):
    client, db, agent, cleanup = _build_web_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", "__touch_nexus": "1", "nexus_enabled": "on", "nexus_default_instance": "ghost", "nexus_instance_count": "0"},
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
