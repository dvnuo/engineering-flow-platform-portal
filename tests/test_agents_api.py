"""Tests for agents API module."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from app.models.agent import Agent
from app.schemas.agent import AgentResponse
from app.db import Base
from app.models import User
from app.services.auth_service import hash_password
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_agent_model_fields():
    """Test Agent model has expected fields."""
    mapper = inspect(Agent)
    columns = [c.name for c in mapper.columns]
    
    # Check key fields exist
    assert "id" in columns
    assert "name" in columns
    assert "status" in columns
    assert "visibility" in columns
    assert "owner_user_id" in columns
    assert "agent_type" in columns
    assert "capability_profile_id" in columns
    assert "policy_profile_id" in columns


def test_agent_response_schema():
    """Test AgentResponse schema fields."""
    fields = AgentResponse.model_fields.keys()
    
    # Check key fields in response
    assert "id" in fields
    assert "name" in fields
    assert "status" in fields
    assert "visibility" in fields
    assert "agent_type" in fields
    assert "capability_profile_id" in fields
    assert "policy_profile_id" in fields


def test_agent_status_values():
    """Test valid Agent status values from state machine."""
    from app.utils.state_machine import VALID_STATUSES
    
    # Check that valid statuses are defined
    assert "running" in VALID_STATUSES
    assert "stopped" in VALID_STATUSES
    assert "creating" in VALID_STATUSES


def _build_agents_client_with_overrides():
    from app.main import app
    import app.api.agents as agents_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    state = {"user": SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)}

    def _override_user():
        return state["user"]

    def _override_db():
        yield db

    app.dependency_overrides[agents_api.get_current_user] = _override_user
    app.dependency_overrides[agents_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, _cleanup


def test_invalid_agent_type_on_create_returns_422(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        response = client.post(
            "/api/agents",
            json={"name": "bad-agent", "image": "example/image:latest", "agent_type": "invalid"},
        )
        assert response.status_code == 422
    finally:
        cleanup()


def test_invalid_agent_type_on_update_returns_422(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        create_resp = client.post(
            "/api/agents",
            json={"name": "good-agent", "image": "example/image:latest", "agent_type": "workspace"},
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]
        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"agent_type": "bad-type"})
        assert patch_resp.status_code == 422
    finally:
        cleanup()


def test_null_agent_type_on_update_returns_422_and_does_not_mutate_agent(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        create_resp = client.post(
            "/api/agents",
            json={"name": "workspace-agent", "image": "example/image:latest", "agent_type": "workspace"},
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"agent_type": None})
        assert patch_resp.status_code == 422
        assert patch_resp.json()["detail"] == "agent_type cannot be null"

        get_resp = client.get(f"/api/agents/{agent_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["agent_type"] == "workspace"
    finally:
        cleanup()
