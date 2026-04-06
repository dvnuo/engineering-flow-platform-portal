from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
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

    agent = Agent(
        name="Dispatch Agent",
        description="dispatch",
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
        deployment_name="dep-d",
        service_name="svc-d",
        pvc_name="pvc-d",
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

    app.dependency_overrides[tasks_api.get_current_user] = _override_user
    app.dependency_overrides[tasks_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, agent, _cleanup


def _create_task(db: Session, agent_id: str, input_payload_json: str = '{"a":1}') -> AgentTask:
    task = AgentTask(
        assignee_agent_id=agent_id,
        source="jira",
        task_type="jira_workflow_review_task",
        input_payload_json=input_payload_json,
        shared_context_ref="EFP-1",
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_dispatch_endpoint_marks_task_done_on_success(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = _create_task(db, agent.id)

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        class _Resp:
            status_code = 200
            text = '{"ok": true, "status": "done"}'

            @staticmethod
            def json():
                return {"ok": True, "status": "done"}

        async def _fake_post(_url: str, _body: dict):
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)

        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        body = response.json()
        assert body["dispatched"] is True
        assert body["task_status"] == "done"

        db.refresh(task)
        assert task.status == "done"
        assert task.result_payload_json is not None
    finally:
        cleanup()


def test_dispatch_endpoint_marks_task_failed_on_runtime_failure(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = _create_task(db, agent.id)

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        class _Resp:
            status_code = 200
            text = '{"ok": false, "status": "failed"}'

            @staticmethod
            def json():
                return {"ok": False, "status": "failed"}

        async def _fake_post(_url: str, _body: dict):
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)

        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        body = response.json()
        assert body["dispatched"] is True
        assert body["task_status"] == "failed"

        db.refresh(task)
        assert task.status == "failed"
    finally:
        cleanup()


def test_dispatch_endpoint_invalid_payload_marks_task_failed(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        task = _create_task(db, agent.id, input_payload_json='[1,2,3]')

        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 409

        db.refresh(task)
        assert task.status == "failed"
        assert "decode to a JSON object" in (task.result_payload_json or "")
    finally:
        cleanup()
