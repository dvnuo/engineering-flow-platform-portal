import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.services.auth_service import hash_password


def _build_client(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="agent-1",
        owner_user_id=user.id,
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
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: SimpleNamespace(id=user.id, role="admin", username=user.username, nickname="Owner"))

    def _cleanup():
        db.close()

    return TestClient(app), db, agent, _cleanup


def test_settings_panel_reads_runtime_profile_not_runtime_api(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = RuntimeProfile(name="rp-settings", config_json=json.dumps({"llm": {"provider": "openai"}}), revision=2)
        db.add(rp)
        db.commit()
        db.refresh(rp)
        agent.runtime_profile_id = rp.id
        db.add(agent)
        db.commit()

        async def _should_not_call(**_kwargs):
            raise AssertionError("runtime proxy forward should not be called")

        monkeypatch.setattr("app.web._forward_runtime", _should_not_call)

        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "rp-settings" in resp.text
        assert "Revision: <strong>2</strong>" in resp.text
    finally:
        cleanup()


def test_settings_save_updates_profile_revision_and_triggers_sync(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = RuntimeProfile(name="rp-save", config_json=json.dumps({"llm": {"provider": "openai"}}), revision=1)
        db.add(rp)
        db.commit()
        db.refresh(rp)
        agent.runtime_profile_id = rp.id
        db.add(agent)
        db.commit()

        sync_calls = []

        async def _fake_sync(_db, profile):
            sync_calls.append(profile.id)
            return {"updated_running_count": 1, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        payload = {
            "original_config_json": rp.config_json,
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-4",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        assert rp.revision == 2
        assert sync_calls == [rp.id]
        assert "Runtime profile updated" in resp.text
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
