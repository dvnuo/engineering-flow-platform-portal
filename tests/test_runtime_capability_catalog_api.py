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

    admin_user = User(username="admin", password_hash=hash_password("pw"), role="admin", is_active=True)
    owner_user = User(username="owner", password_hash=hash_password("pw"), role="viewer", is_active=True)
    other_user = User(username="other", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, owner_user, other_user])
    db.commit()
    for item in [admin_user, owner_user, other_user]:
        db.refresh(item)

    agent = Agent(
        name="Runtime Agent",
        description="runtime",
        owner_user_id=owner_user.id,
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
    other_agent = Agent(
        name="Other Runtime Agent",
        description="runtime-other",
        owner_user_id=other_user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-other.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-runtime-other",
        service_name="svc-runtime-other",
        pvc_name="pvc-runtime-other",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(other_agent)
    db.commit()
    db.refresh(agent)
    db.refresh(other_agent)

    monkeypatch.setattr(catalog_api.service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime.test")
    original_runtime_key = deps_module.settings.runtime_internal_api_key
    deps_module.settings.runtime_internal_api_key = "runtime-internal-test-key"

    state = {"user": admin_user}

    def _override_user():
        user = state["user"]
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[catalog_api.get_current_user] = _override_user
    app.dependency_overrides[catalog_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        deps_module.settings.runtime_internal_api_key = original_runtime_key
        db.close()

    def _set_user(user_obj):
        state["user"] = user_obj

    return TestClient(app), agent, other_agent, admin_user, owner_user, other_user, _set_user, _cleanup


def test_sync_api_persists_snapshot_and_latest_reads_it(monkeypatch):
    client, agent, _other_agent, admin_user, owner_user, _other_user, set_user, cleanup = _build_client(monkeypatch)

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
        set_user(owner_user)
        sync_resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert sync_resp.status_code == 200
        assert sync_resp.json()["catalog_version"] == "v-sync-1"
        assert sync_resp.json()["source_agent_id"] == agent.id
        assert captured["headers"]["X-Internal-Api-Key"] == "runtime-internal-test-key"

        set_user(admin_user)
        latest = client.get("/api/runtime-capability-catalog/latest")
        assert latest.status_code == 200
        assert latest.json()["catalog_version"] == "v-sync-1"
    finally:
        cleanup()


def test_latest_api_can_filter_by_agent_id(monkeypatch):
    client, agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client(monkeypatch)

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
        set_user(owner_user)
        sync_resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert sync_resp.status_code == 200
        latest_resp = client.get(f"/api/runtime-capability-catalog/latest?agent_id={agent.id}")
        assert latest_resp.status_code == 200
        assert latest_resp.json()["source_agent_id"] == agent.id
    finally:
        cleanup()


def test_sync_api_returns_clear_error_when_runtime_unreachable(monkeypatch):
    client, agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("boom")))
    try:
        set_user(owner_user)
        resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert resp.status_code == 502
        assert "unreachable" in resp.json()["detail"].lower()
    finally:
        cleanup()


def test_sync_api_returns_clear_error_when_runtime_internal_key_missing(monkeypatch):
    client, agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client(monkeypatch)
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
        set_user(owner_user)
        resp = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "RUNTIME_INTERNAL_API_KEY is not configured"
    finally:
        deps_module.settings.runtime_internal_api_key = original_runtime_key
        cleanup()


def test_runtime_capability_catalog_authorization(monkeypatch):
    client, agent, other_agent, admin_user, owner_user, other_user, set_user, cleanup = _build_client(monkeypatch)

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"catalog_version": "v-auth", "capabilities": []}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _FakeResponse())
    try:
        set_user(other_user)
        forbidden_sync = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert forbidden_sync.status_code == 403

        set_user(owner_user)
        own_sync = client.post("/api/runtime-capability-catalog/sync", json={"agent_id": agent.id})
        assert own_sync.status_code == 200

        set_user(other_user)
        forbidden_global = client.get("/api/runtime-capability-catalog/latest")
        assert forbidden_global.status_code == 403

        forbidden_other_agent = client.get(f"/api/runtime-capability-catalog/latest?agent_id={agent.id}")
        assert forbidden_other_agent.status_code == 403

        own_agent_latest = client.get(f"/api/runtime-capability-catalog/latest?agent_id={other_agent.id}")
        assert own_agent_latest.status_code == 404

        set_user(admin_user)
        admin_global = client.get("/api/runtime-capability-catalog/latest")
        assert admin_global.status_code == 200
    finally:
        cleanup()
