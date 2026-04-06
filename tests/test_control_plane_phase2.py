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

    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    monkeypatch.setattr(agents_api.k8s_service, "update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

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
            json={"system_type": "JIRA", "external_account_id": "jira-user-7"},
        )
        assert resolve_resp.status_code == 200
        body = resolve_resp.json()
        assert body["matched_agent_id"] == agent.id
        assert body["matched_agent_type"] == "workspace"
        assert body["capability_profile_id"] is None
        assert body["policy_profile_id"] is None
        assert body["execution_mode"] == "sync"
        assert body["reason"] == "matched_enabled_binding"
        assert body["runtime_target"]["agent_id"] == agent.id
        assert body["runtime_target"]["namespace"] == "efp-agents"
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
            assert decision["runtime_target"] is None
        finally:
            db.close()
    finally:
        cleanup()


def test_create_agent_rejects_nonexistent_capability_profile(monkeypatch):
    client, _agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/agents",
            json={
                "name": "new-agent",
                "image": "example/image:latest",
                "capability_profile_id": "missing-capability",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "CapabilityProfile not found"
    finally:
        cleanup()


def test_create_agent_rejects_nonexistent_policy_profile(monkeypatch):
    client, _agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/agents",
            json={
                "name": "new-agent",
                "image": "example/image:latest",
                "policy_profile_id": "missing-policy",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "PolicyProfile not found"
    finally:
        cleanup()


def test_update_agent_rejects_invalid_profile_references(monkeypatch):
    client, agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cap_resp = client.patch(f"/api/agents/{agent.id}", json={"capability_profile_id": "missing-capability"})
        assert cap_resp.status_code == 404
        assert cap_resp.json()["detail"] == "CapabilityProfile not found"

        policy_resp = client.patch(f"/api/agents/{agent.id}", json={"policy_profile_id": "missing-policy"})
        assert policy_resp.status_code == 404
        assert policy_resp.json()["detail"] == "PolicyProfile not found"
    finally:
        cleanup()


def test_update_agent_allows_clearing_profile_references(monkeypatch):
    client, agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cap_create = client.post("/api/capability-profiles", json={"name": "cap-for-update"})
        policy_create = client.post("/api/policy-profiles", json={"name": "policy-for-update"})
        assert cap_create.status_code == 200
        assert policy_create.status_code == 200

        set_resp = client.patch(
            f"/api/agents/{agent.id}",
            json={
                "capability_profile_id": cap_create.json()["id"],
                "policy_profile_id": policy_create.json()["id"],
            },
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["capability_profile_id"] == cap_create.json()["id"]
        assert set_resp.json()["policy_profile_id"] == policy_create.json()["id"]

        clear_resp = client.patch(
            f"/api/agents/{agent.id}",
            json={"capability_profile_id": None, "policy_profile_id": None},
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["capability_profile_id"] is None
        assert clear_resp.json()["policy_profile_id"] is None
    finally:
        cleanup()


def test_identity_bindings_duplicate_enabled_conflict(monkeypatch):
    client, agent, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        first_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "GitHub", "external_account_id": "acct-123", "enabled": True},
        )
        assert first_resp.status_code == 200
        assert first_resp.json()["system_type"] == "github"

        duplicate_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "github", "external_account_id": "acct-123", "enabled": True},
        )
        assert duplicate_resp.status_code == 409
        assert "already exists" in duplicate_resp.json()["detail"]
    finally:
        cleanup()
