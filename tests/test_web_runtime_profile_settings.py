import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.agent_task import AgentTask
from app.models.runtime_profile import RuntimeProfile


def _build_client(monkeypatch, *, current_user_role="admin", current_user_id=None, agent_owner_id=None):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    admin = User(username="admin", password_hash="test", role="admin", is_active=True)
    db.add_all([owner, admin])
    db.commit()
    db.refresh(owner)
    db.refresh(admin)

    if current_user_id is None:
        current_user_id = admin.id if current_user_role == "admin" else owner.id
    if agent_owner_id is None:
        agent_owner_id = owner.id

    current_user = admin if current_user_id == admin.id else owner

    agent = Agent(
        name="agent-1",
        owner_user_id=agent_owner_id,
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
        lambda _request: SimpleNamespace(
            id=current_user.id,
            role=current_user_role,
            username=current_user.username,
            nickname=current_user.username,
        ),
    )

    def _cleanup():
        db.close()

    return TestClient(app), db, agent, _cleanup


def _bind_profile(db, agent, name="rp-save", config=None, revision=1):
    rp = RuntimeProfile(
        owner_user_id=agent.owner_user_id,
        name=name,
        config_json=json.dumps(config or {"llm": {"provider": "openai"}}),
        revision=revision,
        is_default=True,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    agent.runtime_profile_id = rp.id
    db.add(agent)
    db.commit()
    return rp


def test_settings_panel_reads_runtime_profile_not_runtime_api(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, name="rp-settings", config={"llm": {"provider": "openai"}}, revision=2)

        async def _should_not_call(**_kwargs):
            raise AssertionError("runtime proxy forward should not be called")

        monkeypatch.setattr("app.web._forward_runtime", _should_not_call)

        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "rp-settings" in resp.text
        assert "Revision: <strong>2</strong>" in resp.text
        assert 'name="original_config_json"' not in resp.text
    finally:
        cleanup()


def test_settings_save_uses_db_profile_as_merge_base_and_sanitizes(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent)

        async def _fake_sync(_db, profile):
            return {"updated_running_count": 1, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        payload = {
            "original_config_json": json.dumps({"ssh": {"hacked": True}}),
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-4",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert rp.revision == 2
        assert saved["llm"]["provider"] == "anthropic"
        assert "ssh" not in saved
    finally:
        cleanup()


def test_settings_save_sync_exception_does_not_break_response(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"llm": {"provider": "openai"}}, revision=1)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("fanout failed")

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _boom)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_provider": "anthropic", "llm_model": "claude"},
        )
        assert resp.status_code == 200
        assert "Runtime profile was saved, but sync fan-out failed" in resp.text
        db.refresh(rp)
        assert rp.revision == 2
        saved = json.loads(rp.config_json)
        assert saved["llm"]["provider"] == "anthropic"
    finally:
        cleanup()


def test_settings_save_preserves_proxy_password_when_field_absent(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"proxy": {"enabled": True, "password": "keep-secret"}}, revision=5)

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"proxy_enabled": "on", "proxy_url": "http://proxy.local", "proxy_username": "u"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["proxy"]["password"] == "keep-secret"
    finally:
        cleanup()


def test_settings_save_does_not_infer_llm_api_base(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"llm": {}}, revision=1)

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_provider": "anthropic", "llm_model": "claude"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert "api_base" not in saved.get("llm", {})
    finally:
        cleanup()


def test_settings_save_drops_jira_instance_when_name_and_url_blank(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(
            db,
            agent,
            config={
                "jira": {"instances": [{"name": "J", "url": "https://jira", "password": "p", "token": "t"}]},
            },
            revision=1,
        )

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={
                "jira_instance_count": "1",
                "jira_instances_0_name": "",
                "jira_instances_0_url": "",
                "jira_instances_0_username": "",
                "jira_instances_0_password": "",
                "jira_instances_0_token": "",
                "jira_instances_0_project": "",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["jira"]["instances"] == []
    finally:
        cleanup()


def test_settings_save_allowed_for_admin_non_owner(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_role="admin")
    try:
        rp = _bind_profile(db, agent, config={"debug": {"enabled": False}}, revision=2)

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(f"/app/agents/{agent.id}/settings/save", data={"debug_enabled": "on"})
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["debug"]["enabled"] is True
    finally:
        cleanup()


def test_settings_panel_without_runtime_profile_shows_message(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "This agent has no runtime profile" in resp.text
    finally:
        cleanup()


def test_settings_panel_includes_triggered_work_sections(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, name="rp-triggered", config={"llm": {"provider": "openai"}}, revision=1)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "External Identity Bindings" in resp.text
        assert "External Event Subscriptions" in resp.text
    finally:
        cleanup()


def test_task_detail_panel_shows_bundle_and_dedupe_metadata(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        task = AgentTask(
            assignee_agent_id=agent.id,
            source="github",
            task_type="mention",
            status="queued",
            owner_user_id=agent.owner_user_id,
            task_family="triggered_work",
            provider="github",
            trigger="mention",
            bundle_id="github:issue:octo/portal:7",
            version_key=None,
            dedupe_key="github:mention:octo/portal:7",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        resp = client.get(f"/app/tasks/{task.id}/panel")
        assert resp.status_code == 200
        assert "Bundle ID" in resp.text
        assert "Dedupe Key" in resp.text
        assert "github:issue:octo/portal:7" in resp.text
    finally:
        cleanup()
