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
    agent_b = Agent(
        name="Session Agent B",
        description="session-b",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-b.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-session-b",
        service_name="svc-session-b",
        pvc_name="pvc-session-b",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.add(agent_b)
    db.commit()
    db.refresh(agent)
    db.refresh(agent_b)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), agent, agent_b, _cleanup


def test_internal_session_metadata_upsert_create_and_update():
    import app.deps as deps_module

    client, agent, _agent_b, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        create_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={
                "group_id": "g-1",
                "current_task_id": "t-1",
                "source_type": "jira",
                "source_ref": "task-1",
                "latest_event_type": "task_created",
                "metadata_json": '{"k":"v"}',
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["session_id"] == "s-1"
        assert created["group_id"] == "g-1"
        assert created["current_task_id"] == "t-1"
        assert created["source_type"] == "jira"
        assert created["source_ref"] == "task-1"

        update_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={
                "group_id": "g-2",
                "current_task_id": "t-1",
                "source_type": "jira",
                "source_ref": "task-1",
                "latest_event_state": "running",
            },
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
        assert fetched["current_task_id"] == "t-1"
        assert fetched["source_type"] == "jira"
        assert fetched["source_ref"] == "task-1"
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()


def test_internal_session_metadata_requires_internal_api_key():
    import app.deps as deps_module

    client, agent, _agent_b, cleanup = _build_client()
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


def test_same_session_id_across_two_agents_does_not_conflict():
    import app.deps as deps_module

    client, agent_a, agent_b, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        resp_a = client.put(
            f"/api/internal/agents/{agent_a.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-a", "latest_event_state": "running"},
        )
        resp_b = client.put(
            f"/api/internal/agents/{agent_b.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-b", "latest_event_state": "queued"},
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["id"] != resp_b.json()["id"]

        get_a = client.get(
            f"/api/internal/agents/{agent_a.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        get_b = client.get(
            f"/api/internal/agents/{agent_b.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert get_a.status_code == 200
        assert get_b.status_code == 200
        assert get_a.json()["group_id"] == "g-a"
        assert get_b.json()["group_id"] == "g-b"
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()


def test_list_session_metadata_with_filters_and_sorting():
    import app.deps as deps_module
    import time

    client, agent, _agent_b, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-1", "latest_event_state": "running", "current_task_id": "t-1"},
        )
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-2/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-1", "latest_event_state": "done", "current_task_id": "t-2"},
        )
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-3/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-2", "latest_event_state": "running", "current_task_id": "t-3"},
        )
        # ensure s-1 becomes most recently updated
        time.sleep(1.1)
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
            json={"group_id": "g-1", "latest_event_state": "running", "current_task_id": "t-1"},
        )

        base_list = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert base_list.status_code == 200
        items = base_list.json()
        assert len(items) == 3
        assert items[0]["session_id"] == "s-1"

        by_group = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata?group_id=g-1",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert by_group.status_code == 200
        assert {item["session_id"] for item in by_group.json()} == {"s-1", "s-2"}

        by_state = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata?latest_event_state=running",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert by_state.status_code == 200
        assert {item["session_id"] for item in by_state.json()} == {"s-1", "s-3"}

        by_task = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata?current_task_id=t-2",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert by_task.status_code == 200
        assert [item["session_id"] for item in by_task.json()] == ["s-2"]
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()
