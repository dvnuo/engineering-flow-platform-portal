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
    client, agent, _agent_b, cleanup = _build_client()
    try:
        create_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
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
        )
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["id"] == created["id"]
        assert fetched["session_id"] == "s-1"
        assert fetched["current_task_id"] == "t-1"
        assert fetched["source_type"] == "jira"
        assert fetched["source_ref"] == "task-1"
    finally:
        cleanup()


def test_internal_session_metadata_allows_requests_without_additional_headers():
    client, agent, _agent_b, cleanup = _build_client()
    try:
        missing_resp = client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-unauth/metadata",
            json={"group_id": "g-1"},
        )
        assert missing_resp.status_code == 200
    finally:
        cleanup()


def test_same_session_id_across_two_agents_does_not_conflict():
    client, agent_a, agent_b, cleanup = _build_client()
    try:
        resp_a = client.put(
            f"/api/internal/agents/{agent_a.id}/sessions/s-1/metadata",
            json={"group_id": "g-a", "latest_event_state": "running"},
        )
        resp_b = client.put(
            f"/api/internal/agents/{agent_b.id}/sessions/s-1/metadata",
            json={"group_id": "g-b", "latest_event_state": "queued"},
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["id"] != resp_b.json()["id"]

        get_a = client.get(
            f"/api/internal/agents/{agent_a.id}/sessions/s-1/metadata",
        )
        get_b = client.get(
            f"/api/internal/agents/{agent_b.id}/sessions/s-1/metadata",
        )
        assert get_a.status_code == 200
        assert get_b.status_code == 200
        assert get_a.json()["group_id"] == "g-a"
        assert get_b.json()["group_id"] == "g-b"
    finally:
        cleanup()


def test_list_session_metadata_with_filters_and_sorting():
    import app.deps as deps_module
    import time

    client, agent, _agent_b, cleanup = _build_client()
    try:
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            json={"group_id": "g-1", "latest_event_state": "running", "current_task_id": "t-1"},
        )
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-2/metadata",
            json={"group_id": "g-1", "latest_event_state": "done", "current_task_id": "t-2"},
        )
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-3/metadata",
            json={"group_id": "g-2", "latest_event_state": "running", "current_task_id": "t-3"},
        )
        # ensure s-1 becomes most recently updated
        time.sleep(1.1)
        client.put(
            f"/api/internal/agents/{agent.id}/sessions/s-1/metadata",
            json={"group_id": "g-1", "latest_event_state": "running", "current_task_id": "t-1"},
        )

        base_list = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata",
        )
        assert base_list.status_code == 200
        items = base_list.json()
        assert len(items) == 3
        assert items[0]["session_id"] == "s-1"

        by_group = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata?group_id=g-1",
        )
        assert by_group.status_code == 200
        assert {item["session_id"] for item in by_group.json()} == {"s-1", "s-2"}

        by_state = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata?latest_event_state=running",
        )
        assert by_state.status_code == 200
        assert {item["session_id"] for item in by_state.json()} == {"s-1", "s-3"}

        by_task = client.get(
            f"/api/internal/agents/{agent.id}/sessions/metadata?current_task_id=t-2",
        )
        assert by_task.status_code == 200
        assert [item["session_id"] for item in by_task.json()] == ["s-2"]
    finally:
        cleanup()


def test_agent_session_metadata_upsert_recovers_from_insert_race(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.models.agent_session_metadata import AgentSessionMetadata
    from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository

    class _FakeDB:
        def __init__(self):
            self.rollback_calls = 0
            self.commit_calls = 0

        def add(self, _obj):
            return None

        def commit(self):
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise IntegrityError("insert", {}, Exception("duplicate"))

        def rollback(self):
            self.rollback_calls += 1

        def refresh(self, _obj):
            return None

    db = _FakeDB()
    repo = AgentSessionMetadataRepository(db)  # type: ignore[arg-type]
    existing = AgentSessionMetadata(agent_id="agent-1", session_id="s-1", group_id="old-group", latest_event_state="queued")
    existing.id = "existing-row"

    calls = {"count": 0}

    def _fake_get(*, agent_id: str, session_id: str):
        _ = (agent_id, session_id)
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return existing

    monkeypatch.setattr(repo, "get_by_agent_and_session", _fake_get)

    result = repo.upsert(
        agent_id="agent-1",
        session_id="s-1",
        group_id="new-group",
        latest_event_state="running",
        metadata_json='{"k":"v"}',
    )

    assert result is existing
    assert result.group_id == "new-group"
    assert result.latest_event_state == "running"
    assert result.metadata_json == '{"k":"v"}'
    assert db.rollback_calls == 1
