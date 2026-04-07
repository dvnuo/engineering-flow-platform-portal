from types import SimpleNamespace
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentDelegation, AgentTask, User
from app.services.task_dispatcher import TaskDispatcherService
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
            text = (
                '{"ok": true, "task_id": "t1", "execution_type": "task", "request_id": "req-1",'
                ' "status": "success", "output_payload": {"result": "ok"}}'
            )

            @staticmethod
            def json():
                return {
                    "ok": True,
                    "task_id": "t1",
                    "execution_type": "task",
                    "request_id": "req-1",
                    "status": "success",
                    "output_payload": {"result": "ok"},
                }

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
        assert json.loads(task.result_payload_json)["status"] == "success"
    finally:
        cleanup()


def test_dispatch_endpoint_marks_task_failed_on_runtime_error(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = _create_task(db, agent.id)

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        class _Resp:
            status_code = 200
            text = (
                '{"ok": false, "task_id": "t1", "execution_type": "task", "request_id": "req-2",'
                ' "status": "error", "error": {"message": "bad input"}}'
            )

            @staticmethod
            def json():
                return {
                    "ok": False,
                    "task_id": "t1",
                    "execution_type": "task",
                    "request_id": "req-2",
                    "status": "error",
                    "error": {"message": "bad input"},
                }

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
        assert json.loads(task.result_payload_json)["status"] == "error"
    finally:
        cleanup()


def test_dispatch_endpoint_marks_task_failed_on_runtime_blocked(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = _create_task(db, agent.id)

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        class _Resp:
            status_code = 200
            text = (
                '{"ok": false, "task_id": "t1", "execution_type": "task", "request_id": "req-3",'
                ' "status": "blocked", "error": {"message": "policy denied"}}'
            )

            @staticmethod
            def json():
                return {
                    "ok": False,
                    "task_id": "t1",
                    "execution_type": "task",
                    "request_id": "req-3",
                    "status": "blocked",
                    "error": {"message": "policy denied"},
                }

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
        assert json.loads(task.result_payload_json)["status"] == "blocked"
    finally:
        cleanup()


def test_dispatch_endpoint_marks_task_failed_on_malformed_2xx_without_status(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = _create_task(db, agent.id)

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        class _Resp:
            status_code = 200
            text = '{"ok": true, "task_id": "t1", "execution_type": "task"}'

            @staticmethod
            def json():
                return {"ok": True, "task_id": "t1", "execution_type": "task"}

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
        assert json.loads(task.result_payload_json)["ok"] is True
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


def test_cleanup_telemetry_deleted_task_agent_ids_is_deduped():
    service = TaskDispatcherService()
    delegation = SimpleNamespace(audit_trace_json=json.dumps({"cleanup": {"deleted_task_agent_ids": ["a-1"]}}))

    service._append_deleted_task_agent_id_to_delegation(delegation, "a-1")
    service._append_deleted_task_agent_id_to_delegation(delegation, "a-1")
    service._append_deleted_task_agent_id_to_delegation(delegation, "a-2")

    parsed = json.loads(delegation.audit_trace_json)
    assert parsed["cleanup"]["deleted_task_agent_ids"] == ["a-1", "a-2"]


def test_dispatch_prefers_delegation_origin_session_over_task_payload(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = AgentTask(
            assignee_agent_id=agent.id,
            source="agent",
            task_type="delegation_task",
            parent_agent_id=agent.id,
            input_payload_json='{"leader_session_id":"payload-session","strict_delegation_result":true}',
            status="queued",
            retry_count=0,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        delegation = AgentDelegation(
            group_id="g-1",
            parent_agent_id=agent.id,
            leader_agent_id=agent.id,
            assignee_agent_id=agent.id,
            agent_task_id=task.id,
            objective="test",
            leader_session_id="leader-session",
            origin_session_id="origin-session",
            reply_target_type="leader",
            coordination_run_id="run-55",
            round_index=4,
            visibility="leader_only",
            status="queued",
        )
        db.add(delegation)
        db.commit()

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"delegation_result":{"status":"done"}}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"delegation_result": {"status": "done"}}}

        async def _fake_post(_url: str, body: dict):
            captured.update(body)
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)
        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        assert captured["session_id"] == "origin-session"
        assert captured["metadata"]["portal_leader_session_id"] == "origin-session"
        assert captured["metadata"]["portal_delegation_reply_target"] == "leader"
        assert captured["metadata"]["portal_coordination_run_id"] == "run-55"
        assert captured["metadata"]["portal_coordination_round_index"] == 4
    finally:
        cleanup()
