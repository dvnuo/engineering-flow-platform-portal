from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.schemas.agent_group import AgentGroupTaskCreateRequest
from app.services.agent_group_service import AgentGroupService
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.agent_groups as groups_api

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    parent_agent = Agent(
        name="Parent Agent",
        description="parent",
        owner_user_id=owner.id,
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
        owner_user_id=owner.id,
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

    group = AgentGroupRepository(db).create(
        name="Task Group",
        leader_agent_id=parent_agent.id,
        created_by_user_id=owner.id,
    )

    def _override_user():
        return SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[groups_api.get_current_user] = _override_user
    app.dependency_overrides[groups_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, group, parent_agent, assignee_agent, _cleanup


def test_get_group_tasks_returns_group_tasks_only():
    client, db, group, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="task-a",
            status="queued",
        )
        AgentTaskRepository(db).create(
            group_id="other-group",
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="task-b",
            status="queued",
        )

        response = client.get(f"/api/agent-groups/{group.id}/tasks")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["group_id"] == group.id
        assert items[0]["task_type"] == "task-a"
    finally:
        cleanup()


def test_get_group_task_summary_counts_statuses():
    client, db, group, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="task-queued",
            status="queued",
        )
        AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="task-running",
            status="running",
        )
        AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="task-done",
            status="done",
        )
        AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="task-failed",
            status="failed",
        )

        response = client.get(f"/api/agent-groups/{group.id}/task-summary")
        assert response.status_code == 200
        body = response.json()
        assert body["group_id"] == group.id
        assert body["total"] == 4
        assert body["queued"] == 1
        assert body["running"] == 1
        assert body["done"] == 1
        assert body["failed"] == 1
    finally:
        cleanup()


def test_post_group_scoped_task_sets_group_id_from_path():
    client, _db, group, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        response = client.post(
            f"/api/agent-groups/{group.id}/tasks",
            json={
                "parent_agent_id": parent_agent.id,
                "assignee_agent_id": assignee_agent.id,
                "source": "portal",
                "task_type": "group-task",
                "status": "queued",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["group_id"] == group.id
        assert body["assignee_agent_id"] == assignee_agent.id
    finally:
        cleanup()


def test_group_task_endpoints_return_404_when_group_missing():
    client, _db, _group, _parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        missing_group_id = "missing-group"

        list_resp = client.get(f"/api/agent-groups/{missing_group_id}/tasks")
        assert list_resp.status_code == 404

        summary_resp = client.get(f"/api/agent-groups/{missing_group_id}/task-summary")
        assert summary_resp.status_code == 404

        create_resp = client.post(
            f"/api/agent-groups/{missing_group_id}/tasks",
            json={
                "assignee_agent_id": assignee_agent.id,
                "source": "portal",
                "task_type": "group-task",
                "status": "queued",
            },
        )
        assert create_resp.status_code == 404
    finally:
        cleanup()


def test_group_tasks_endpoint_calls_service_list(monkeypatch):
    client, db, group, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        expected_task = AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="service-list-task",
            status="queued",
        )
        calls: list[str] = []

        def _fake_list_group_tasks(self, group_id: str):
            calls.append(group_id)
            return [expected_task]

        monkeypatch.setattr(AgentGroupService, "list_group_tasks", _fake_list_group_tasks)

        response = client.get(f"/api/agent-groups/{group.id}/tasks")
        assert response.status_code == 200
        assert calls == [group.id]
        assert response.json()[0]["id"] == expected_task.id
    finally:
        cleanup()


def test_group_task_summary_endpoint_calls_service_summary(monkeypatch):
    client, _db, group, _parent_agent, _assignee_agent, cleanup = _build_client_with_overrides()
    try:
        calls: list[str] = []

        def _fake_summary(self, group_id: str):
            calls.append(group_id)
            return {
                "group_id": group_id,
                "total": 7,
                "queued": 2,
                "running": 2,
                "done": 2,
                "failed": 1,
            }

        monkeypatch.setattr(AgentGroupService, "get_group_task_summary", _fake_summary)

        response = client.get(f"/api/agent-groups/{group.id}/task-summary")
        assert response.status_code == 200
        assert calls == [group.id]
        assert response.json()["total"] == 7
    finally:
        cleanup()


def test_group_task_create_endpoint_calls_service_create(monkeypatch):
    client, db, group, parent_agent, assignee_agent, cleanup = _build_client_with_overrides()
    try:
        calls: list[tuple[str, AgentGroupTaskCreateRequest]] = []
        expected_task = AgentTaskRepository(db).create(
            group_id=group.id,
            parent_agent_id=parent_agent.id,
            assignee_agent_id=assignee_agent.id,
            source="portal",
            task_type="service-create-task",
            status="queued",
        )

        def _fake_create(self, group_id: str, payload: AgentGroupTaskCreateRequest):
            calls.append((group_id, payload))
            return expected_task

        monkeypatch.setattr(AgentGroupService, "create_group_task", _fake_create)

        response = client.post(
            f"/api/agent-groups/{group.id}/tasks",
            json={
                "parent_agent_id": parent_agent.id,
                "assignee_agent_id": assignee_agent.id,
                "source": "portal",
                "task_type": "group-task",
                "status": "queued",
            },
        )
        assert response.status_code == 200
        assert len(calls) == 1
        assert calls[0][0] == group.id
        assert calls[0][1].assignee_agent_id == assignee_agent.id
        assert response.json()["id"] == expected_task.id
    finally:
        cleanup()
