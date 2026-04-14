"""Tests for agents API module."""
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from app.models.agent import Agent
from app.schemas.agent import AgentResponse
from app.db import Base
from app.models import User
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
    assert "runtime_profile_id" in columns


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


def test_agent_response_normalizes_legacy_repo_url():
    obj = SimpleNamespace(
        id="agent-1",
        name="Agent One",
        status="running",
        visibility="private",
        image="example/image:latest",
        repo_url="git@github.com:Acme/Portal.git",
        branch="main",
        owner_user_id=1,
        cpu="250m",
        memory="512Mi",
        agent_type="workspace",
        capability_profile_id=None,
        policy_profile_id=None,
        disk_size_gi=20,
        description=None,
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    response = AgentResponse.model_validate(obj)
    assert response.repo_url == "https://github.com/Acme/Portal.git"


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
    user = User(username="owner", password_hash="test", role="admin", is_active=True)
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


def test_agent_response_schema_includes_runtime_profile_id():
    fields = AgentResponse.model_fields.keys()
    assert "runtime_profile_id" in fields


def test_create_and_update_agent_runtime_profile_validation_and_response(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        create_missing = client.post(
            "/api/agents",
            json={"name": "bad", "image": "example/image:latest", "runtime_profile_id": "missing-rp"},
        )
        assert create_missing.status_code == 404

        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp", config_json="{}", revision=1, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        create_ok = client.post(
            "/api/agents",
            json={"name": "ok", "image": "example/image:latest", "runtime_profile_id": rp.id},
        )
        assert create_ok.status_code == 200
        agent = create_ok.json()
        assert agent["runtime_profile_id"] == rp.id

        patch_missing = client.patch(f"/api/agents/{agent['id']}", json={"runtime_profile_id": "missing-rp"})
        assert patch_missing.status_code == 404
    finally:
        cleanup()


def test_create_agent_without_runtime_profile_id_uses_user_default_profile(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile

        default_profile = RuntimeProfile(owner_user_id=1, name="Default", config_json="{}", revision=1, is_default=True)
        db.add(default_profile)
        db.commit()
        db.refresh(default_profile)

        create_ok = client.post("/api/agents", json={"name": "auto-default", "image": "example/image:latest"})
        assert create_ok.status_code == 200
        assert create_ok.json()["runtime_profile_id"] == default_profile.id
    finally:
        cleanup()


def test_create_agent_with_other_users_runtime_profile_is_404(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile
        from app.models import User

        foreign_user = User(username="foreign", password_hash="test", role="user", is_active=True)
        db.add(foreign_user)
        db.commit()
        db.refresh(foreign_user)
        foreign_profile = RuntimeProfile(owner_user_id=foreign_user.id, name="Foreign", config_json="{}", revision=1, is_default=True)
        db.add(foreign_profile)
        db.commit()
        db.refresh(foreign_profile)

        resp = client.post(
            "/api/agents",
            json={"name": "bad-foreign", "image": "example/image:latest", "runtime_profile_id": foreign_profile.id},
        )
        assert resp.status_code == 404
    finally:
        cleanup()


def test_update_running_agent_runtime_profile_pushes_apply_and_clear(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp-sync", config_json='{"llm": {"provider": "openai"}}', revision=3, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        create_ok = client.post(
            "/api/agents",
            json={"name": "sync-agent", "image": "example/image:latest"},
        )
        assert create_ok.status_code == 200
        agent_id = create_ok.json()["id"]

        pushed = []

        async def _fake_push(agent, payload):
            pushed.append(payload)
            return True

        monkeypatch.setattr("app.api.agents.runtime_profile_sync_service.push_payload_to_agent", _fake_push)

        apply_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": rp.id})
        assert apply_resp.status_code == 200
        assert pushed[-1]["runtime_profile_id"] == rp.id
        assert pushed[-1]["revision"] == 3

        clear_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": None})
        assert clear_resp.status_code == 422
        assert "runtime_profile_id cannot be null" in clear_resp.json()["detail"]
        assert pushed[-1]["runtime_profile_id"] == rp.id
    finally:
        cleanup()


def test_update_running_agent_runtime_profile_push_failure_still_returns_200(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp-sync-fail", config_json='{"llm": {"provider": "openai"}}', revision=3, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        create_ok = client.post(
            "/api/agents",
            json={"name": "sync-fail-agent", "image": "example/image:latest"},
        )
        assert create_ok.status_code == 200
        agent_id = create_ok.json()["id"]

        pushed = []

        async def _fake_push(agent, payload):
            pushed.append((agent.id, payload))
            return False

        monkeypatch.setattr("app.api.agents.runtime_profile_sync_service.push_payload_to_agent", _fake_push)

        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": rp.id})
        assert patch_resp.status_code == 200
        assert pushed
    finally:
        cleanup()
