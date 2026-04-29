import pytest
import json

import asyncio
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.log_context import bind_log_context, get_log_context, reset_log_context
from app.services.auth_service import hash_password
from app.services.task_dispatcher import TaskDispatcherService
import app.services.task_dispatcher as task_dispatcher_module


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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

    yield db, agent
    db.close()


def _create_task(db: Session, agent_id: str) -> AgentTask:
    task = AgentTask(
        assignee_agent_id=agent_id,
        owner_user_id=1,
        source="jira",
        task_type="jira_workflow_review_task",
        input_payload_json='{"a": 1}',
        shared_context_ref="EFP-1",
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_dispatch_task_async_submit_then_success(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()

    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 202
        text = '{"ok": true, "status": "accepted", "request_id": "req-1"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "accepted", "request_id": "req-1"}

    class StatusResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "done"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "done"}}

    async def fake_post(_url, _body):
        return SubmitResp()

    async def fake_get(_url, _meta):
        return StatusResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.dispatched is True
    assert result.task_status == "done"

    db.refresh(task)
    assert task.status == "done"
    assert task.runtime_request_id == "req-1"
    assert task.started_at is not None
    assert task.finished_at is not None
    assert task.summary == "done"


def test_dispatch_task_async_submit_then_error(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()

    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 202
        text = '{"ok": true, "status": "accepted", "request_id": "req-2"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "accepted", "request_id": "req-2"}

    class StatusResp:
        status_code = 200
        text = '{"ok": false, "status": "error", "error": {"message": "bad input"}}'

        @staticmethod
        def json():
            return {"ok": False, "status": "error", "error": {"message": "bad input"}}

    async def fake_post(_url, _body):
        return SubmitResp()

    async def fake_get(_url, _meta):
        return StatusResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "failed"
    db.refresh(task)
    assert task.status == "failed"
    assert task.error_message == "bad input"


def test_dispatch_task_sync_runtime_success_compatible(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "ok"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "ok"}}

    async def fake_post(_url, _body):
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "done"
    db.refresh(task)
    assert task.status == "done"


def test_dispatcher_derives_summary_from_github_review_summary():
    payload = {
        "ok": True,
        "status": "success",
        "output_payload": {
            "task_type": "github_review_task",
            "task_template_id": "github_pr_review",
            "review_summary": "Automated PR review summary",
            "automation_rule_id": "rule-1",
            "dedupe_key": "dedupe-1",
        },
    }
    assert TaskDispatcherService._derive_summary_from_runtime_payload(payload) == "Automated PR review summary"


def test_dispatch_task_poll_timeout_marks_failed(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 202
        text = '{"ok": true, "status": "accepted", "request_id": "req-3"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "accepted", "request_id": "req-3"}

    class PendingResp:
        status_code = 200
        text = '{"ok": true, "status": "running"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "running"}

    async def fake_post(_url, _body):
        return SubmitResp()

    async def fake_get(_url, _meta):
        return PendingResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)
    monkeypatch.setattr(service, "_poll_runtime_task_until_terminal", lambda **_kwargs: TaskDispatcherService._poll_runtime_task_until_terminal(service, timeout_seconds=0, interval_seconds=0, **_kwargs))

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "failed"
    db.refresh(task)
    payload = json.loads(task.result_payload_json or "{}")
    assert payload["error_code"] == "runtime_poll_timeout"
    assert payload["trace_id"]
    assert payload["portal_dispatch_id"]


def test_dispatch_task_continues_polling_on_http_200_running(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 202
        text = '{"ok": true, "status": "accepted", "request_id": "req-4"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "accepted", "request_id": "req-4"}

    class RunningResp:
        status_code = 200
        text = '{"ok": true, "status": "running"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "running"}

    class SuccessResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "done"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "done"}}

    async def fake_post(_url, _body):
        return SubmitResp()

    polls = {"count": 0}

    async def fake_get(_url, _meta):
        polls["count"] += 1
        if polls["count"] == 1:
            return RunningResp()
        return SuccessResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "done"
    assert polls["count"] >= 2


def test_dispatch_task_invalid_input_sets_error_message_and_finished_at(db_session):
    db, agent = db_session
    task = _create_task(db, agent.id)
    task.input_payload_json = "not-json"
    db.add(task)
    db.commit()
    service = TaskDispatcherService()

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "failed"
    db.refresh(task)
    assert task.error_message
    assert task.finished_at is not None


def test_dispatch_task_runtime_exception_sets_error_message_and_finished_at(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    async def fake_post(_url, _body):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "failed"
    db.refresh(task)
    assert task.error_message
    assert task.finished_at is not None


def test_dispatch_late_runtime_success_cannot_overwrite_stale(db_session, monkeypatch):
    db, agent = db_session
    task = AgentTask(
        assignee_agent_id=agent.id,
        source="github",
        task_type="github_review_task",
        input_payload_json='{"owner":"octo","repo":"portal","pull_number":1,"head_sha":"sha-1"}',
        shared_context_ref="github:review:octo/portal:1:acct:sha-1",
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    stale_payload = json.dumps(
        {
            "ok": False,
            "error_code": "superseded_by_new_head_sha",
            "message": "GitHub review task superseded by a newer PR head_sha",
            "superseded_by_task_id": "new-task-1",
            "superseded_by_head_sha": "sha-2",
        }
    )

    class Resp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"result": "ok"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

    async def fake_post(_url, _body):
        fresh = db.get(AgentTask, task.id)
        fresh.status = "stale"
        fresh.result_payload_json = stale_payload
        db.add(fresh)
        db.commit()
        return Resp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)

    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "stale"
    db.refresh(task)
    assert task.status == "stale"


def test_dispatch_task_inherits_parent_span_in_same_thread(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    captured = {}

    class SubmitResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "ok"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "ok"}}

    async def fake_post(_url, body):
        captured["url"] = _url
        captured["body"] = body
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)

    token = bind_log_context(trace_id="trace-parent", span_id="span-parent", path="/app/requirement-bundles")
    try:
        result = asyncio.run(service.dispatch_task(task.id, db))
    finally:
        reset_log_context(token)

    assert result.task_status == "done"
    metadata = captured["body"]["metadata"]
    assert metadata["trace_id"] == "trace-parent"
    assert metadata["parent_span_id"] == "span-parent"
    assert metadata["span_id"] != "span-parent"
    assert metadata["portal_dispatch_id"] != "-"


def test_dispatch_task_in_background_rebinds_parent_context(monkeypatch):
    service = TaskDispatcherService()
    seen = {}

    async def fake_dispatch_task(task_id, db_session):
        _ = task_id, db_session
        seen["context"] = get_log_context().copy()

    monkeypatch.setattr(service, "dispatch_task", fake_dispatch_task)

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr(task_dispatcher_module, "SessionLocal", lambda: DummySession())

    class FakeThread:
        def __init__(self, target, daemon):
            self._target = target
            self.daemon = daemon

        def start(self):
            token = bind_log_context(
                trace_id="-",
                span_id="-",
                parent_span_id="-",
                portal_dispatch_id="-",
                portal_task_id="-",
                agent_id="-",
                path="-",
            )
            try:
                self._target()
            finally:
                reset_log_context(token)

    monkeypatch.setattr(task_dispatcher_module.threading, "Thread", FakeThread)

    parent_token = bind_log_context(
        trace_id="trace-bg-1",
        span_id="span-bg-1",
        path="/app/requirement-bundles",
        agent_id="agent-9",
    )
    try:
        service.dispatch_task_in_background("task-bg-1")
    finally:
        reset_log_context(parent_token)

    assert seen["context"]["trace_id"] == "trace-bg-1"
    assert seen["context"]["span_id"] == "span-bg-1"
    assert seen["context"]["path"] == "/app/requirement-bundles"
    assert seen["context"]["agent_id"] == "agent-9"


def test_triggered_event_task_metadata_includes_binding_and_automation(monkeypatch, db_session):
    db, agent = db_session
    task = AgentTask(
        assignee_agent_id=agent.id,
        owner_user_id=1,
        source="jira",
        task_type="triggered_event_task",
        trigger="mention",
        input_payload_json=json.dumps(
            {
                "source_kind": "jira.mention",
                "binding_id": "binding-1",
                "automation_rule": "jira.mentions",
                "rule_id": "rule-1",
                "issue_key": "ENG-1",
                "project_key": "ENG",
                "body": "@agent ping",
            }
        ),
        shared_context_ref="ENG-1",
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    captured = {}

    class SubmitResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "ok"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "ok"}}

    async def fake_post(_url, body):
        captured["url"] = _url
        captured["body"] = body
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "done"

    metadata = captured["body"]["metadata"]
    assert "portal_subscription_id" not in metadata
    assert metadata["source_kind"] == "jira.mention"
    assert metadata["portal_binding_id"] == "binding-1"
    assert metadata["portal_automation_rule"] == "jira.mentions"
    assert metadata["portal_automation_rule_id"] == "rule-1"
    assert metadata["portal_task_trigger"] == "mention"


def test_github_review_dispatch_includes_execution_mode_metadata(monkeypatch, db_session):
    db, agent = db_session
    task = AgentTask(
        assignee_agent_id=agent.id,
        owner_user_id=1,
        source="automation_rule",
        task_type="github_review_task",
        trigger="github_pr_review_requested",
        input_payload_json=json.dumps(
            {
                "owner": "octo",
                "repo": "portal",
                "pull_number": 99,
                "head_sha": "sha-99",
                "execution_mode": "chat_tool_loop",
            }
        ),
        shared_context_ref="github:pr_review:octo/portal:99",
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    captured = {}

    class SubmitResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "ok"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "ok"}}

    async def fake_post(_url, body):
        captured["url"] = _url
        captured["body"] = body
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "done"

    runtime_body = captured["body"]
    assert captured["url"].endswith("/api/tasks/execute")
    assert "/api/chat" not in captured["url"]
    assert "/api/chat/stream" not in captured["url"]
    assert runtime_body["task_type"] == "github_review_task"
    assert runtime_body["input_payload"]["execution_mode"] == "chat_tool_loop"
    assert runtime_body["metadata"]["portal_execution_mode"] == "chat_tool_loop"


def test_github_review_automation_dispatch_does_not_use_chat_endpoint():
    source = Path("app/services/task_dispatcher.py").read_text()
    assert '"/api/tasks/execute"' in source
    automation_sources = "\n".join(
        Path(path).read_text()
        for path in [
            "app/services/automation_rule_service.py",
            "app/services/external_event_router.py",
            "app/services/task_dispatcher.py",
        ]
    )

    assert "github_review_task" in automation_sources
    assert "/api/chat" not in automation_sources
    assert "/api/chat/stream" not in automation_sources
