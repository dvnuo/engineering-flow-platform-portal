import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile


def _build_client(monkeypatch):
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

    agent = Agent(
        name="agent-1",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep",
        service_name="svc",
        pvc_name="pvc",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname=owner.username),
    )

    async def _fake_sync(*_args, **_kwargs):
        return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

    monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

    def _cleanup():
        db.close()

    return TestClient(app), db, agent, _cleanup


def _bind_profile(db, agent, config=None):
    rp = RuntimeProfile(
        owner_user_id=agent.owner_user_id,
        name="rp",
        config_json=json.dumps(config or {}),
        revision=1,
        is_default=True,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    agent.runtime_profile_id = rp.id
    db.add(agent)
    db.commit()
    return rp


def test_settings_panel_removes_subscriptions_ui(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "External Event Subscriptions" not in resp.text
        assert "settings-subscriptions-panel-container" not in resp.text
        assert "External Identities" in resp.text
    finally:
        cleanup()


def test_settings_panel_get_llm_tools_all_mode_by_default(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode"' in resp.text
        assert 'name="llm_tools_mode" value="all" checked' in resp.text
    finally:
        cleanup()


def test_settings_panel_get_llm_tools_custom_mode_renders_patterns(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {"llm": {"tools": ["git_clone", "jira_*"]}})
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode" value="custom" checked' in resp.text
        assert 'name="llm_tools_count"' in resp.text
        assert "git_clone" in resp.text
        assert "jira_*" in resp.text
    finally:
        cleanup()


def test_settings_save_and_echoes_automation(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent)
        payload = {
            "__touch_github": "1",
            "__touch_jira": "1",
            "__touch_confluence": "1",
            "github_enabled": "on",
            "github_review_requests_enabled": "on",
            "github_review_requests_repos": "org/repo-a\norg/repo-b",
            "github_mentions_enabled": "on",
            "github_mentions_repos": "org/repo-a",
            "github_mentions_include_review_comments": "on",
            "jira_enabled": "on",
            "jira_assignments_enabled": "on",
            "jira_assignments_projects": "ENG",
            "jira_mentions_enabled": "on",
            "jira_mentions_projects": "ENG",
            "confluence_enabled": "on",
            "confluence_mentions_enabled": "on",
            "confluence_mentions_spaces": "DEV",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["github"]["automation"]["mentions"]["include_review_comments"] is True
        assert cfg["jira"]["automation"]["assignments"]["projects"] == ["ENG"]
        assert cfg["confluence"]["automation"]["mentions"]["spaces"] == ["DEV"]
        assert 'name="github_review_requests_repos"' in resp.text
        assert 'name="jira_assignments_projects"' in resp.text
        assert 'name="confluence_mentions_spaces"' in resp.text
    finally:
        cleanup()


def test_settings_save_persists_llm_tools_custom_patterns(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "4",
            "llm_tools_0_pattern": " git_clone ",
            "llm_tools_1_pattern": "jira_*",
            "llm_tools_2_pattern": "",
            "llm_tools_3_pattern": "GIT_CLONE",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["tools"] == ["git_clone", "jira_*"]
        assert 'name="llm_tools_count"' in resp.text
        assert 'data-action="add-llm-tool-pattern"' in resp.text
    finally:
        cleanup()


def test_settings_save_persists_llm_tools_none_mode(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"tools": ["*"]}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_tools_mode": "none", "llm_tools_count": "0"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["tools"] == []
    finally:
        cleanup()


def test_settings_save_merges_into_raw_profile_without_injecting_hidden_defaults(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_model": "gpt-5",
            "llm_tools_mode": "none",
            "llm_tools_count": "0",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["provider"] == "openai"
        assert cfg["llm"]["model"] == "gpt-5"
        assert cfg["llm"]["tools"] == []
        assert "max_retries" not in cfg["llm"]
        assert "system-prompt" not in cfg["llm"]
        assert "proxy" not in cfg
        assert "jira" not in cfg
        assert "confluence" not in cfg
    finally:
        cleanup()


def test_settings_save_full_form_only_touched_debug_persists_debug_only(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        payload = {
            "__touch_llm": "0",
            "__touch_proxy": "0",
            "__touch_jira": "0",
            "__touch_confluence": "0",
            "__touch_github": "0",
            "__touch_git": "0",
            "__touch_debug": "1",
            "llm_provider": "github_copilot",
            "llm_model": "gpt-5-mini",
            "llm_tools_mode": "all",
            "proxy_enabled": "",
            "proxy_url": "",
            "jira_enabled": "",
            "confluence_enabled": "",
            "github_enabled": "",
            "git_user_name": "",
            "debug_enabled": "on",
            "debug_log_level": "DEBUG",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg == {"debug": {"enabled": True, "log_level": "DEBUG"}}
    finally:
        cleanup()


def test_bindings_can_be_configured_without_runtime_profile(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/agents/{agent.id}/external-identities/create",
            data={"system_type": "github", "external_account_id": "acct-1", "enabled": "on"},
        )
        assert resp.status_code == 200
        assert "External identity added" in resp.text
    finally:
        cleanup()


def test_settings_panel_runtime_profile_missing_message_mentions_external_identities(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "This agent has no runtime profile." in resp.text
        assert "External identities can still be configured below." in resp.text
        assert "External Identities" in resp.text
    finally:
        cleanup()


def test_bindings_panel_available_without_runtime_profile(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/external-identities/panel")
        assert resp.status_code == 200
        assert "Add External Identity" in resp.text
        assert "Assign one from Edit Assistant first." not in resp.text
    finally:
        cleanup()
