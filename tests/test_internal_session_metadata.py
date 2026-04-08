from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.services.auth_service import hash_password


def _build_client():
    from app.main import app
    from app.db import get_db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = Agent(
        name="Session Agent",
        description="session",
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
        deployment_name="dep-session",
        service_name="svc-session",
        pvc_name="pvc-session",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), agent, _cleanup


def test_internal_session_metadata_upsert_create_and_update():
    import app.deps as deps_module

    client, agent, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        create_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-1", "latest_event_type": "task_created", "metadata_json": '{"k":"v"}'},
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["session_id"] == "s-1"
        assert created["group_id"] == "g-1"

        update_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-2", "latest_event_state": "running"},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["id"] == created["id"]
        assert updated["group_id"] == "g-2"

        get_resp = client.get(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["id"] == created["id"]
        assert fetched["session_id"] == "s-1"
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()


def test_internal_session_metadata_requires_internal_api_key():
    import app.deps as deps_module

    client, agent, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        missing_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-unauth/metadata",
            json={"group_id": "g-1"},
        )
        wrong_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-unauth/metadata",
            headers={"X-Internal-Api-Key": "wrong"},
            json={"group_id": "g-1"},
        )
        assert missing_resp.status_code == 401
        assert wrong_resp.status_code == 401
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()
