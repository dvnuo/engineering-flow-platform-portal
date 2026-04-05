from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.services.auth_service import hash_password
from app.services.runtime_router import RuntimeRouterService


def _build_client_with_overrides(monkeypatch):
    from app.main import app
    import app.api.agents as agents_api
    import app.api.agent_identity_bindings as bindings_api
    import app.api.capability_profiles as capability_api
    import app.api.policy_profiles as policy_api
    import app.api.runtime_router as runtime_router_api

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="Agent One",
        description="desc",
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
        deployment_name="dep-1",
        service_name="svc-1",
        pvc_name="pvc-1",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    def _override_user():
        return SimpleNamespace(id=user.id, role="admin", username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[capability_api.get_current_user] = _override_user
    app.dependency_overrides[capability_api.get_db] = _override_db
    app.dependency_overrides[policy_api.get_current_user] = _override_user
    app.dependency_overrides[policy_api.get_db] = _override_db
    app.dependency_overrides[bindings_api.get_current_user] = _override_user
    app.dependency_overrides[bindings_api.get_db] = _override_db
    app.dependency_overrides[runtime_router_api.get_current_user] = _override_user
    app.dependency_overrides[runtime_router_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_current_user] = _override_user
    app.dependency_overrides[agents_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), agent, _cleanup


def test_capability_profiles_create_and_list(monkeypatch):
    client, _agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            "/api/capability-profiles",
            json={"name": "cap-basic", "description": "Basic profile", "tool_set_json": '["shell"]'},
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["name"] == "cap-basic"

        list_resp = client.get("/api/capability-profiles")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["tool_set_json"] == '["shell"]'
    finally:
        cleanup()


def test_policy_profiles_create_and_list(monkeypatch):
    client, _agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            "/api/policy-profiles",
            json={"name": "pol-basic", "description": "Policy", "max_parallel_tasks": 3},
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["max_parallel_tasks"] == 3

        list_resp = client.get("/api/policy-profiles")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["name"] == "pol-basic"
    finally:
        cleanup()


def test_identity_bindings_create_and_list_for_agent(monkeypatch):
    client, agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={
                "system_type": "github",
                "external_account_id": "acct-123",
                "username": "octocat",
                "scope_json": '{"repos": ["engineering-flow-platform-portal"]}',
                "enabled": True,
            },
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["system_type"] == "github"

        list_resp = client.get(f"/api/agents/{agent.id}/identity-bindings")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["external_account_id"] == "acct-123"
    finally:
        cleanup()


def test_agent_response_includes_additive_control_plane_fields(monkeypatch):
    client, agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.get(f"/api/agents/{agent.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["agent_type"] == "workspace"
        assert "capability_profile_id" in body
        assert "policy_profile_id" in body
    finally:
        cleanup()


def test_runtime_router_resolves_agent_by_binding(monkeypatch):
    client, agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_binding_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "jira", "external_account_id": "jira-user-7", "enabled": True},
        )
        assert create_binding_resp.status_code == 200

        resolve_resp = client.post(
            "/api/runtime-router/resolve-binding",
            json={"system_type": "jira", "external_account_id": "jira-user-7"},
        )
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["matched_agent_id"] == agent.id
        assert resolve_resp.json()["reason"] == "matched_enabled_binding"
    finally:
        cleanup()


def test_runtime_router_returns_none_when_no_binding_exists(monkeypatch):
    _client, _agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        service = RuntimeRouterService()
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        try:
            found = service.find_agent_for_identity_binding("slack", "missing", db)
            assert found is None

            decision = service.resolve_binding_decision("slack", "missing", db)
            assert decision["matched_agent_id"] is None
            assert decision["reason"] == "no_enabled_binding"
        finally:
            db.close()
    finally:
        cleanup()
