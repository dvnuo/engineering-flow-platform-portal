"""The administration pages a group administrator sees, and a group's Default connectors form.

web.py app_default_connections_*, app_groups_*, app_users_panel; the group
form posts ``group_id`` and one ``group_choice_<type>`` per connector
(default_connections_panel.html group_scope_open).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.models import Group
from app.services.connector_defaults_service import parse_group_modes
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from tests.test_web_connector_settings import _build_env

PLATFORM = {
    "jira": {"enabled": True, "instances": [{"name": "Corp", "url": "https://corp.atlassian.net", "token": "platform-secret"}]},
    "proxy": {"enabled": True, "proxies": [{"name": "corp", "url": "http://proxy.corp:3128"}], "default": "corp"},
}


def _as(monkeypatch, env, user):
    """Sign the pages in as ``user``, group included."""
    import app.web as web_module

    env.set_user(user)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(
            id=user.id, role=user.role, username=user.username, nickname=user.username, group_id=user.group_id
        ),
    )


def _group(db, name):
    group = Group(name=name, is_default=False, config_json="{}", connector_modes_json="{}")
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def test_a_group_administrator_edits_only_their_groups_default_connectors(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        RuntimeProfileSeedService(env.db).save_seed(PLATFORM)
        dev = _group(env.db, "Dev")
        other = _group(env.db, "Ops")
        env.other.role = "group_admin"
        env.other.group_id = dev.id
        env.db.commit()
        _as(monkeypatch, env, env.other)

        panel = env.client.get(f"/app/admin/default-connections/panel?group_id={other.id}")
        assert panel.status_code == 200
        # Always their own group, whatever was asked for.
        assert 'name="group_id" value="%d"' % dev.id in panel.text
        assert 'name="group_choice_jira"' in panel.text
        # The platform's credentials never reach the page.
        assert "platform-secret" not in panel.text
        assert "Platform (every group follows it)" not in panel.text

        saved = env.client.post(
            "/app/admin/default-connections/save",
            data={
                "group_id": str(dev.id),
                "group_choice_jira": "custom",
                "group_choice_confluence": "none",
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_name": "Dev",
                "jira_instances_0_url": "https://dev.atlassian.net",
            },
        )
        assert saved.status_code == 200
        assert 'data-settings-status="error"' not in saved.text
        env.db.refresh(dev)
        assert parse_group_modes(dev.connector_modes_json) == {"jira": "custom", "confluence": "custom"}
        stored = json.loads(dev.config_json)
        assert stored["jira"]["instances"][0]["url"] == "https://dev.atlassian.net"
        assert "confluence" not in stored and "proxy" not in stored
        # The platform's seed is untouched.
        assert RuntimeProfileSeedService(env.db).get_seed()["jira"]["instances"][0]["url"] == "https://corp.atlassian.net"

        assert env.client.get("/app/admin/groups/panel").status_code == 403
        assert env.client.get("/app/admin/errors/panel").status_code == 403
        users = env.client.get("/app/users/panel")
        assert users.status_code == 200
        assert "Members of Dev" in users.text
        assert "@owner" not in users.text
    finally:
        env.cleanup()


def test_a_system_administrator_picks_the_platform_or_a_group(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        RuntimeProfileSeedService(env.db).save_seed(PLATFORM)
        dev = _group(env.db, "Dev")
        _as(monkeypatch, env, env.owner)

        platform = env.client.get("/app/admin/default-connections/panel")
        assert "Platform (every group follows it)" in platform.text
        assert 'name="group_choice_jira"' not in platform.text

        group = env.client.get(f"/app/admin/default-connections/panel?group_id={dev.id}")
        assert 'name="group_choice_jira"' in group.text
        # The proxy is the platform's: shown, never a group's choice.
        assert 'name="group_choice_proxy"' not in group.text

        groups = env.client.get("/app/admin/groups/panel")
        assert groups.status_code == 200 and "Dev" in groups.text
        created = env.client.post("/app/admin/groups/create", data={"name": "QA"})
        assert "Group QA created" in created.text
    finally:
        env.cleanup()
