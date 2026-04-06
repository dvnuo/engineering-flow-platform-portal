from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.agents as agents_api
    import app.api.external_event_subscriptions as subs_api
    import app.api.agent_tasks as tasks_api

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

    parent_agent = Agent(
        name="Parent Agent",
        description="parent",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-parent.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-parent",
        service_name="svc-parent",
        pvc_name="pvc-parent",
        endpoint_path="/",
        agent_type="workspace",
    )
    assignee_agent = Agent(
        name="Assignee Agent",
        description="assignee",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-assignee.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-assignee",
        service_name="svc-assignee",
        pvc_name="pvc-assignee",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(parent_agent)
    db.add(assignee_agent)
    db.commit()
    db.refresh(parent_agent)
    db.refresh(assignee_agent)

    def _override_user():
        return SimpleNamespace(id=user.id, role="admin", username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[subs_api.get_current_user] = _override_user
    app.dependency_overrides[subs_api.get_db] = _override_db
    app.dependency_overrides[tasks_api.get_current_user] = _override_user
    app.dependency_overrides[tasks_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_current_user] = _override_user
    app.dependency_overrides[agents_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), parent_agent, assignee_agent, _cleanup


def test_create_and_list_external_event_subscriptions():
    client, parent_agent, _assignee_agent, cleanup = _build_client_with_overrides()
    try:
        create_resp = client.post(
            "/api/external-event-subscriptions",
            json={
                "agent_id": parent_agent.id,
                "source_type": "GitHub",
                "event_type": "push",
                "target_ref": "repo:main",
                "enabled": True,
                "config_json": '{"branch":"main"}',
                "dedupe_key_template": "github:push:{sha}",
            },
        )
        assert create_resp.status_code == 200
        body = create_resp.json()
        assert body["agent_id"] == parent_agent.id
        assert body["source_type"] == "github"

        list_resp = client.get("/api/external-event-subscriptions")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) == 1
        assert items[0]["event_type"] == "push"
    finally:
        cleanup()


def test_list_external_event_subscriptions_by_agent():
    client, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        create_1 = client.post(
            "/api/external-event-subscriptions",
            json={"agent_id": parent_agent.id, "source_type": "github", "event_type": "push", "enabled": True},
        )
        create_2 = client.post(
            "/api/external-event-subscriptions",
            json={"agent_id": assignee_agent.id, "source_type": "jira", "event_type": "issue_updated", "enabled": True},
        )
        assert create_1.status_code == 200
        assert create_2.status_code == 200

        list_by_agent_resp = client.get(f"/api/agents/{parent_agent.id}/external-event-subscriptions")
        assert list_by_agent_resp.status_code == 200
        items = list_by_agent_resp.json()
        assert len(items) == 1
        assert items[0]["agent_id"] == parent_agent.id
    finally:
        cleanup()


def test_create_and_list_agent_tasks_and_status_persistence():
    client, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        create_resp = client.post(
            "/api/agent-tasks",
            json={
                "parent_agent_id": parent_agent.id,
                "assignee_agent_id": assignee_agent.id,
                "source": "external_event",
                "task_type": "triage",
                "input_payload_json": '{"event":"push"}',
                "status": "queued",
                "retry_count": 1,
            },
        )
        assert create_resp.status_code == 200
        task = create_resp.json()
        assert task["assignee_agent_id"] == assignee_agent.id
        assert task["status"] == "queued"

        list_resp = client.get("/api/agent-tasks")
        assert list_resp.status_code == 200
        tasks = list_resp.json()
        assert len(tasks) == 1
        assert tasks[0]["task_type"] == "triage"
        assert tasks[0]["retry_count"] == 1
    finally:
        cleanup()


def test_list_tasks_by_assignee_agent():
    client, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        create_resp = client.post(
            "/api/agent-tasks",
            json={
                "parent_agent_id": parent_agent.id,
                "assignee_agent_id": assignee_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "running",
            },
        )
        assert create_resp.status_code == 200

        list_by_agent_resp = client.get(f"/api/agents/{assignee_agent.id}/tasks")
        assert list_by_agent_resp.status_code == 200
        tasks = list_by_agent_resp.json()
        assert len(tasks) == 1
        assert tasks[0]["assignee_agent_id"] == assignee_agent.id
        assert tasks[0]["status"] == "running"
    finally:
        cleanup()
