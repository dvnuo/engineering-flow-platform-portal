import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User

def _build_client(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    other = User(username="other", password_hash="test", role="user", is_active=True)
    db.add_all([owner, other]); db.commit(); db.refresh(owner); db.refresh(other)

    rp = RuntimeProfile(owner_user_id=owner.id, name="Default", description="d", config_json=json.dumps({"llm": {"provider": "openai"}}), revision=1, is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    running = Agent(
        name="runner",
        owner_user_id=owner.id,
        runtime_profile_id=rp.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp",
        deployment_name="dep",
        service_name="svc",
        pvc_name="pvc",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(running); db.commit(); db.refresh(running)

    state = {"user": owner}
    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=state["user"].id, role="user", username=state["user"].username, nickname=state["user"].username),
    )

    async def _fake_sync(*_args, **_kwargs):
        return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

    monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

    def _set_user(u):
        state["user"] = u

    def _cleanup():
        db.close()

    return TestClient(app), db, owner, other, rp, running, _set_user, _cleanup


def test_runtime_profile_panel_owner_only(monkeypatch):
    client, _db, owner, other, rp, running, set_user, cleanup = _build_client(monkeypatch)
    try:
        ok = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert ok.status_code == 200
        assert "Runtime Profile Metadata" in ok.text
        assert 'data-copilot-auth-base="/api/copilot/auth"' in ok.text
        assert 'data-copilot-agent-id=' not in ok.text
        assert 'Copilot auth proxy' not in ok.text
        assert f'data-test-base=\"/app/runtime-profiles/{rp.id}/test\"' in ok.text
        assert "data-current-value=" in ok.text

        set_user(other)
        deny = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert deny.status_code == 404
    finally:
        cleanup()


def test_runtime_profile_save_updates_and_triggers(monkeypatch):
    client, db, owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "1",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "1",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": "Renamed",
                "description": "new-desc",
                "is_default": "on",
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4",
                "llm_tools_mode": "all",
                "llm_tools_count": "0",
                "proxy_enabled": "",
                "proxy_url": "",
                "proxy_username": "",
                "proxy_password": "",
                "github_enabled": "",
                "github_base_url": "",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "runtimeProfilesChanged"

        db.refresh(rp)
        assert rp.name == "Renamed"
        assert rp.description == "new-desc"
        assert rp.revision == 2
        saved = json.loads(rp.config_json)
        assert saved["llm"]["provider"] == "anthropic"
        assert saved["llm"]["tools"] == ["*"]
        assert "max_tokens" not in saved["llm"]
        assert "max_retries" not in saved["llm"]
        assert "system-prompt" not in saved["llm"]
        assert "api_key" not in saved["llm"]
        assert "base_url" not in saved.get("github", {})
        assert "password" not in saved.get("proxy", {})
    finally:
        cleanup()


def test_runtime_profile_save_full_form_only_touched_debug_persists_only_debug(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "0",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "1",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "github_copilot",
                "llm_model": "gpt-5-mini",
                "llm_tools_mode": "all",
                "proxy_enabled": "",
                "jira_enabled": "",
                "confluence_enabled": "",
                "github_enabled": "",
                "debug_enabled": "on",
                "debug_log_level": "ERROR",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json) == {
            "llm": {"provider": "openai"},
            "debug": {"enabled": True, "log_level": "ERROR"},
        }
    finally:
        cleanup()


def test_runtime_profile_name_only_save_keeps_sparse_config_unchanged(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()
        db.refresh(rp)

        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "0",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": "Metadata Only",
                "description": "still sparse",
                "is_default": "on",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        assert rp.name == "Metadata Only"
        assert rp.description == "still sparse"
        assert json.loads(rp.config_json) == {}
    finally:
        cleanup()


def test_runtime_profile_panel_renders_view_defaults_for_sparse_profiles(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="jira_instance_count" value="0"' in resp.text
        assert 'name="confluence_instance_count" value="0"' in resp.text
        assert 'name="llm_provider"' in resp.text
    finally:
        cleanup()


def test_runtime_profile_panel_get_renders_llm_tools_custom_patterns(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"tools": ["git_clone", "jira_*"]}})
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode" value="custom" checked' in resp.text
        assert "git_clone" in resp.text
        assert "jira_*" in resp.text
        assert 'data-action="add-llm-tool-pattern"' in resp.text
    finally:
        cleanup()


def test_runtime_profile_panel_get_renders_llm_tools_none_mode(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"tools": []}})
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode"' in resp.text
        assert 'name="llm_tools_mode" value="none" checked' in resp.text
        assert 'data-llm-tools-editor class="space-y-2 hidden"' in resp.text
    finally:
        cleanup()
