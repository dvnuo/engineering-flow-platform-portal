import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.services.auth_service import hash_password


def _client():
    from app.main import app
    import app.api.agent_tasks as api_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = Agent(
        name="Async Agent",
        description="async",
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
        deployment_name="dep-async",
        service_name="svc-async",
        pvc_name="pvc-async",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    def _override_user():
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)

    def _override_db():
        yield db

    app.dependency_overrides[api_module.get_current_user] = _override_user
    app.dependency_overrides[api_module.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, agent, _cleanup


def test_create_agent_async_task_normalizes_skill_and_schedules_dispatch(monkeypatch):
    client, db, agent, cleanup = _client()
    scheduled = []
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: scheduled.append(task_id))
    try:
        response = client.post(
            "/api/agent-tasks/async",
            json={
                "assignee_agent_id": agent.id,
                "skill_name": " /Code_Review ",
                "task_content": "Review the current branch\nFocus on regressions.",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["task_type"] == "agent_async_task"
        assert body["task_family"] == "agent_task"
        assert body["source"] == "portal"
        assert body["trigger"] == "manual"
        assert body["template_id"] is None
        assert body["provider"] is None
        assert body["skill_name"] == "Code_Review"
        assert body["root_task_id"] == body["id"]
        assert body["parent_task_id"] is None
        assert body["task_session_id"] == f"agent-task:{body['id']}"
        assert body["title"] == "Review the current branch"
        assert scheduled == [body["id"]]

        task = db.get(AgentTask, body["id"])
        assert task is not None
        payload = json.loads(task.input_payload_json)
        assert payload["schema"] == "agent_async_task.v1"
        assert payload["user_task"] == "Review the current branch\nFocus on regressions."
        assert payload["skill_name"] == "Code_Review"
        assert payload["root_task_id"] == task.id
        assert payload["parent_task_id"] is None
        assert payload["task_session_id"] == task.task_session_id
        assert payload["autonomous"] is True
        assert "background long-running task" in payload["autonomous_instruction"]
    finally:
        cleanup()


def test_create_agent_async_task_rejects_blank_skill_and_content():
    client, _db, agent, cleanup = _client()
    try:
        blank_skill = client.post(
            "/api/agent-tasks/async",
            json={"assignee_agent_id": agent.id, "skill_name": " / ", "task_content": "Do work"},
        )
        assert blank_skill.status_code == 400

        blank_content = client.post(
            "/api/agent-tasks/async",
            json={"assignee_agent_id": agent.id, "skill_name": "build", "task_content": "   "},
        )
        assert blank_content.status_code == 400
    finally:
        cleanup()


def test_followup_creates_child_reusing_root_and_session(monkeypatch):
    client, db, agent, cleanup = _client()
    scheduled = []
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: scheduled.append(task_id))
    try:
        created = client.post(
            "/api/agent-tasks/async",
            json={"assignee_agent_id": agent.id, "skill_name": "/implement", "task_content": "Build the feature."},
        ).json()
        task = db.get(AgentTask, created["id"])
        task.status = "done"
        task.result_payload_json = json.dumps({"status": "success", "final_response": "Built."})
        db.add(task)
        db.commit()

        response = client.post(f"/api/agent-tasks/{created['id']}/followups", json={"task_content": "Add tests."})
        assert response.status_code == 200
        child = response.json()
        assert child["id"] != created["id"]
        assert child["parent_task_id"] == created["id"]
        assert child["root_task_id"] == created["id"]
        assert child["task_session_id"] == created["task_session_id"]
        assert child["skill_name"] == "implement"
        assert scheduled == [created["id"], child["id"]]

        payload = json.loads(child["input_payload_json"])
        assert payload["followup_task"] == "Add tests."
        assert payload["previous_task_id"] == created["id"]
        assert payload["root_task_id"] == created["id"]
        assert payload["task_session_id"] == created["task_session_id"]
    finally:
        cleanup()


def test_cancel_queued_agent_async_task_marks_cancelled(monkeypatch):
    client, _db, agent, cleanup = _client()
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda _task_id: None)
    try:
        created = client.post(
            "/api/agent-tasks/async",
            json={"assignee_agent_id": agent.id, "skill_name": "plan", "task_content": "Plan the work."},
        ).json()
        response = client.post(f"/api/agent-tasks/{created['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
    finally:
        cleanup()


def test_cancel_running_agent_async_task_delegates_to_dispatcher(monkeypatch):
    client, db, agent, cleanup = _client()
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda _task_id: None)
    cancelled = []

    async def fake_cancel_task(task_id, db_session, user=None):
        _ = user
        task = db_session.get(AgentTask, task_id)
        task.status = "cancelled"
        task.summary = "Task cancellation was requested."
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)
        cancelled.append(task_id)
        return task

    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.cancel_task", fake_cancel_task)
    try:
        created = client.post(
            "/api/agent-tasks/async",
            json={"assignee_agent_id": agent.id, "skill_name": "plan", "task_content": "Plan the work."},
        ).json()
        task = db.get(AgentTask, created["id"])
        task.status = "running"
        db.add(task)
        db.commit()

        response = client.post(f"/api/agent-tasks/{created['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert cancelled == [created["id"]]
    finally:
        cleanup()
