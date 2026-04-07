from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.services.auth_service import hash_password


def _build_client(monkeypatch):
    from app.main import app
    import app.api.runtime_capability_catalog as catalog_api
    import app.deps as deps_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="Runtime Agent",
        description="runtime",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-runtime",
        service_name="svc-runtime",
        pvc_name="pvc-runtime",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    monkeypatch.setattr(catalog_api.service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime.test")
    original_runtime_key = deps_module.settings.runtime_internal_api_key
    deps_module.settings.runtime_internal_api_key = "runtime-internal-test-key"

    def _override_user():
        return SimpleNamespace(id=user.id, role="admin", username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[catalog_api.get_current_user] = _override_user
    app.dependency_overrides[catalog_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        deps_module.settings.runtime_internal_api_key = original_runtime_key
        db.close()

    return TestClient(app), agent, _cleanup


def test_sync_api_persists_snapshot_and_latest_reads_it(monkeypatch):
    client, agent, cleanup = _build_client(monkeypatch)

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "catalog_version": "v-sync-1",
                "capabilities": [
                    {"capability_id": "adapter:github:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"}
                ],
            }

    captured = {}

    def _fake_get(*args, **kwargs):
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", _fake_get)
    try:
        sync_resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert sync_resp.status_code == 200
        assert sync_resp.json()["catalog_version"] == "v-sync-1"
        assert sync_resp.json()["source_agent_id"] == agent.id
        assert captured["headers"]["X-Internal-Api-Key"] == "runtime-internal-test-key"

        latest = client.get("/api/runtime-capability-catalog/latest")
        assert latest.status_code == 200
        assert latest.json()["catalog_version"] == "v-sync-1"
    finally:
        cleanup()


def test_latest_api_can_filter_by_agent_id(monkeypatch):
    client, agent, cleanup = _build_client(monkeypatch)

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "catalog_version": "v-agent",
                "capabilities": [
                    {"capability_id": "adapter:github:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"}
                ],
            }

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _FakeResponse())
    try:
        sync_resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert sync_resp.status_code == 200
        latest_resp = client.get(f"/api/runtime-capability-catalog/latest?agent_id={agent.id}")
        assert latest_resp.status_code == 200
        assert latest_resp.json()["source_agent_id"] == agent.id
    finally:
        cleanup()


def test_sync_api_returns_clear_error_when_runtime_unreachable(monkeypatch):
    client, agent, cleanup = _build_client(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("boom")))
    try:
        resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert resp.status_code == 502
        assert "unreachable" in resp.json()["detail"].lower()
    finally:
        cleanup()


def test_sync_api_returns_clear_error_when_runtime_internal_key_missing(monkeypatch):
    client, agent, cleanup = _build_client(monkeypatch)
    import app.deps as deps_module

    original_runtime_key = deps_module.settings.runtime_internal_api_key
    deps_module.settings.runtime_internal_api_key = ""

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"catalog_version": "v-sync-2", "capabilities": []}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _FakeResponse())
    try:
        resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "RUNTIME_INTERNAL_API_KEY is not configured"
    finally:
        deps_module.settings.runtime_internal_api_key = original_runtime_key
        cleanup()
