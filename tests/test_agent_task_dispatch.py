import pytest
import json

import asyncio
from pathlib import Path
from types import SimpleNamespace
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, RuntimeProfile, User
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
        task_type="agent_async_task",
        input_payload_json='{"a": 1}',
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _attach_github_runtime_profile(db: Session, agent: Agent) -> RuntimeProfile:
    profile = RuntimeProfile(
        owner_user_id=agent.owner_user_id,
        name=f"github-rp-{agent.id}",
        config_json=json.dumps({"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "secret"}}),
        revision=1,
        is_default=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    agent.runtime_profile_id = profile.id
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return profile


def _assert_no_runtime_profile_authorization_metadata(metadata: dict) -> None:
    assert "authorization_source" not in metadata
    assert "allowed_external_systems" not in metadata
    assert "allowed_actions" not in metadata
    assert "allowed_adapter_actions" not in metadata
    assert "allowed_capability_ids" not in metadata
    assert "allowed_capability_types" not in metadata
    assert "resolved_action_mappings" not in metadata
    assert "unresolved_tools" not in metadata
    assert "unresolved_skills" not in metadata
    assert "unresolved_channels" not in metadata
    assert "unresolved_actions" not in metadata
    assert "skill_details" not in metadata


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


def test_dispatch_task_done_delegation_triggers_immediate_reply_processing(db_session, monkeypatch):
    db, agent = db_session
    task = AgentTask(
        assignee_agent_id=agent.id,
        owner_user_id=agent.owner_user_id,
        source="delegation",
        task_type="agent_async_task",
        task_family="agent_task",
        input_payload_json=json.dumps({"schema": "agent_async_task.v1", "delegation_rule_id": "rule-1"}),
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    service = TaskDispatcherService()

    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "done"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "done"}}

    async def fake_post(_url, _body):
        return SubmitResp()

    calls = []

    async def fake_process_reply(db_arg, task_arg):
        calls.append({"same_db": db_arg is db, "task_id": task_arg.id, "status": task_arg.status})

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_process_delegation_reply_after_done", fake_process_reply)

    result = asyncio.run(service.dispatch_task(task.id, db))

    assert result.task_status == "done"
    assert calls == [{"same_db": True, "task_id": task.id, "status": "done"}]


def test_dispatch_task_async_status_poll_timeout_retry_then_success(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()

    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 202
        text = '{"ok": true, "status": "accepted", "request_id": "req-poll-timeout"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "accepted", "request_id": "req-poll-timeout"}

    class SuccessResp:
        status_code = 200
        text = '{"ok": true, "status": "success", "output_payload": {"summary": "done after retry"}}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "output_payload": {"summary": "done after retry"}}

    async def fake_post(_url, _body):
        return SubmitResp()

    polls = {"count": 0}

    async def fake_get(_url, _meta):
        polls["count"] += 1
        if polls["count"] == 1:
            raise httpx.ReadTimeout("status read timeout")
        return SuccessResp()

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)
    monkeypatch.setattr(task_dispatcher_module.asyncio, "sleep", fake_sleep)

    result = asyncio.run(service.dispatch_task(task.id, db))

    assert result.dispatched is True
    assert result.task_status == "done"
    assert polls["count"] == 2
    db.refresh(task)
    assert task.status == "done"
    assert task.summary == "done after retry"


def test_agent_async_task_dispatch_uses_configured_poll_timeout_and_interval(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")
    monkeypatch.setattr(
        task_dispatcher_module,
        "get_settings",
        lambda: SimpleNamespace(
            agent_task_runtime_poll_timeout_seconds=3660,
            agent_task_runtime_poll_interval_seconds=7,
        ),
    )

    class SubmitResp:
        status_code = 202
        text = '{"ok": true, "status": "accepted", "request_id": "req-timeout"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "accepted", "request_id": "req-timeout"}

    async def fake_post(_url, _body):
        return SubmitResp()

    captured = {}

    async def fake_poll(**kwargs):
        captured.update(kwargs)
        return task_dispatcher_module.NormalizedRuntimeOutcome(
            terminal_status="done",
            result_payload_json=json.dumps({"ok": True, "status": "success", "output_payload": {"summary": "done"}}),
            message="Task dispatched successfully",
            runtime_status_code=200,
        )

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    monkeypatch.setattr(service, "_poll_runtime_task_until_terminal", fake_poll)

    result = asyncio.run(service.dispatch_task(task.id, db))

    assert result.task_status == "done"
    assert captured["timeout_seconds"] == 3660
    assert captured["interval_seconds"] == 7
    assert captured["runtime_status_url"] == f"http://runtime/api/tasks/{task.id}"


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


def test_agent_async_task_dispatch_sends_session_and_metadata(db_session, monkeypatch):
    db, agent = db_session
    task = AgentTask(
        id="task-async-1",
        assignee_agent_id=agent.id,
        owner_user_id=1,
        source="portal",
        task_type="agent_async_task",
        task_family="agent_task",
        title="Review branch",
        skill_name="review",
        root_task_id="task-async-1",
        task_session_id="agent-task:task-async-1",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "user_task": "Review the branch.",
                "skill_name": "review",
                "task_session_id": "agent-task:task-async-1",
                "root_task_id": "task-async-1",
                "parent_task_id": None,
                "autonomous_instruction": "Run as a background long-running task. Finish independently.",
            }
        ),
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
        text = '{"ok": true, "status": "success", "final_response": "done"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "final_response": "done"}

    async def fake_post(url, body):
        captured["url"] = url
        captured["body"] = body
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "done"
    assert captured["body"]["task_type"] == "agent_async_task"
    assert captured["body"]["session_id"] == "agent-task:task-async-1"
    metadata = captured["body"]["metadata"]
    assert metadata["portal_task_mode"] == "agent_async_task"
    assert metadata["portal_skill_name"] == "review"
    assert metadata["portal_root_task_id"] == "task-async-1"
    assert metadata["portal_task_session_id"] == "agent-task:task-async-1"
    assert metadata["system_prompt"] == "Run as a background long-running task. Finish independently."


def test_agent_async_task_dispatch_does_not_infer_github_authorization_from_credentials(db_session, monkeypatch):
    db, agent = db_session
    _attach_github_runtime_profile(db, agent)
    task = AgentTask(
        id="task-async-github-auth",
        assignee_agent_id=agent.id,
        owner_user_id=agent.owner_user_id,
        source="portal",
        task_type="agent_async_task",
        task_family="agent_task",
        title="Review pull request",
        skill_name="review-pull-request",
        root_task_id="task-async-github-auth",
        task_session_id="agent-task:task-async-github-auth",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "user_task": "Review the pull request.",
                "skill_name": "review-pull-request",
                "task_session_id": "agent-task:task-async-github-auth",
                "root_task_id": "task-async-github-auth",
                "parent_task_id": None,
                "delegation_rule": {"id": "rule-1", "name": "Review PR"},
                "delegation": {
                    "delegation_rule_id": "rule-1",
                    "source": "github",
                    "provider": "github",
                },
            }
        ),
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
        text = '{"ok": true, "status": "success", "final_response": "done"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "final_response": "done"}

    async def fake_post(url, body):
        captured["url"] = url
        captured["body"] = body
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    result = asyncio.run(service.dispatch_task(task.id, db))

    assert result.task_status == "done"
    assert captured["url"].endswith("/api/tasks/execute")
    assert captured["body"]["task_type"] == "agent_async_task"
    metadata = captured["body"]["metadata"]
    assert metadata["runtime_profile_id"] == agent.runtime_profile_id
    assert metadata["runtime_profile"]["runtime_profile_id"] == agent.runtime_profile_id
    assert metadata["runtime_profile"]["source"] == "portal.runtime_profile"
    assert metadata["runtime_profile"]["config"]["github"]["api_token"] == "secret"
    assert metadata["portal_task_mode"] == "agent_async_task"
    assert metadata["portal_skill_name"] == "review-pull-request"
    assert metadata["portal_root_task_id"] == "task-async-github-auth"
    assert metadata["portal_task_session_id"] == "agent-task:task-async-github-auth"
    assert metadata["portal_task_id"] == "task-async-github-auth"
    assert metadata["portal_task_source"] == "portal"
    assert metadata["portal_task_family"] == "agent_task"
    assert metadata["portal_delegation_rule_id"] == "rule-1"
    assert metadata["portal_delegation_source"] == "github"
    assert metadata["portal_delegation_provider"] == "github"
    _assert_no_runtime_profile_authorization_metadata(metadata)


def test_agent_async_task_dispatch_uses_single_runtime_profile_model(db_session, monkeypatch):
    db, agent = db_session
    runtime_profile = RuntimeProfile(
        owner_user_id=agent.owner_user_id,
        name="Copilot",
        config_json=json.dumps(
            {
                "llm": {
                    "provider": "github_copilot",
                    "model": "gpt-5.4-mini",
                }
            }
        ),
        revision=5,
        is_default=True,
    )
    db.add(runtime_profile)
    db.commit()
    db.refresh(runtime_profile)

    agent.runtime_type = "native"
    agent.runtime_profile_id = runtime_profile.id
    db.add(agent)
    db.commit()
    db.refresh(agent)

    task = AgentTask(
        id="task-single-runtime-profile",
        assignee_agent_id=agent.id,
        owner_user_id=agent.owner_user_id,
        source="portal",
        task_type="agent_async_task",
        task_family="agent_task",
        title="Review branch",
        skill_name="review",
        root_task_id="task-single-runtime-profile",
        task_session_id="agent-task:task-single-runtime-profile",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "user_task": "Review the branch.",
                "skill_name": "review",
                "task_session_id": "agent-task:task-single-runtime-profile",
                "root_task_id": "task-single-runtime-profile",
                "parent_task_id": None,
            }
        ),
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
        text = '{"ok": true, "status": "success", "final_response": "done"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "final_response": "done"}

    async def fake_post(url, body):
        captured["url"] = url
        captured["body"] = body
        return SubmitResp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)

    result = asyncio.run(service.dispatch_task(task.id, db))

    assert result.task_status == "done"
    assert captured["url"].endswith("/api/tasks/execute")
    metadata = captured["body"]["metadata"]
    assert metadata["provider"] == "github_copilot"
    assert metadata["model"] == "gpt-5.4-mini"
    assert metadata["runtime_profile"]["provider"] == "github_copilot"
    assert metadata["runtime_profile"]["model"] == "gpt-5.4-mini"
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "gpt-5.4-mini"


def test_dispatcher_derives_summary_from_review_summary():
    payload = {
        "ok": True,
        "status": "success",
        "output_payload": {
            "task_type": "agent_async_task",
            "review_summary": "Automated PR review summary",
            "delegation_rule_id": "rule-1",
            "dedupe_key": "dedupe-1",
        },
    }
    assert TaskDispatcherService._derive_summary_from_runtime_payload(payload) == "Automated PR review summary"


def test_poll_runtime_task_retries_transient_status_timeout_then_success(monkeypatch):
    service = TaskDispatcherService()

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

    polls = {"count": 0}

    async def fake_get(_url, _meta):
        polls["count"] += 1
        if polls["count"] == 1:
            raise httpx.ReadTimeout("status read timeout")
        if polls["count"] == 2:
            return RunningResp()
        return SuccessResp()

    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)

    outcome = asyncio.run(
        service._poll_runtime_task_until_terminal(
            runtime_status_url="http://runtime/api/tasks/task-1",
            metadata={"trace_id": "trace-1"},
            trace_context={"trace_id": "trace-1", "portal_dispatch_id": "dispatch-1"},
            timeout_seconds=1,
            interval_seconds=0,
        )
    )

    assert outcome.terminal_status == "done"
    assert polls["count"] == 3


def test_poll_runtime_task_transient_status_failures_until_timeout(monkeypatch):
    service = TaskDispatcherService()
    polls = {"count": 0}

    async def fake_get(_url, _meta):
        polls["count"] += 1
        raise httpx.ReadTimeout("status read timeout")

    monkeypatch.setattr(service, "_get_runtime_task_status", fake_get)

    outcome = asyncio.run(
        service._poll_runtime_task_until_terminal(
            runtime_status_url="http://runtime/api/tasks/task-timeout",
            metadata={"trace_id": "trace-timeout"},
            trace_context={"trace_id": "trace-timeout", "portal_dispatch_id": "dispatch-timeout"},
            timeout_seconds=0.05,
            interval_seconds=0.001,
        )
    )

    payload = json.loads(outcome.result_payload_json)
    assert outcome.terminal_status == "failed"
    assert payload["error_code"] == "runtime_poll_timeout"
    assert polls["count"] >= 1


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
    async def fast_timeout_poll(**kwargs):
        kwargs.pop("timeout_seconds", None)
        kwargs.pop("interval_seconds", None)
        return await TaskDispatcherService._poll_runtime_task_until_terminal(
            service,
            timeout_seconds=0,
            interval_seconds=0,
            **kwargs,
        )

    monkeypatch.setattr(service, "_poll_runtime_task_until_terminal", fast_timeout_poll)

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
        id="task-async-cancelled",
        assignee_agent_id=agent.id,
        owner_user_id=agent.owner_user_id,
        source="delegation",
        task_type="agent_async_task",
        task_family="agent_task",
        title="Delegation PR review",
        skill_name="review",
        root_task_id="task-async-cancelled",
        task_session_id="agent-task:task-async-cancelled",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "user_task": "Review https://github.com/octo/portal/pull/1.",
                "skill_name": "review",
                "task_session_id": "agent-task:task-async-cancelled",
                "root_task_id": "task-async-cancelled",
                "parent_task_id": None,
                "delegation_rule_id": "rule-1",
                "delegation": {
                    "delegation_rule_id": "rule-1",
                    "source": "github_pr_review",
                    "provider": "github",
                    "source_url": "https://github.com/octo/portal/pull/1",
                },
            }
        ),
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
            "message": "Delegation task superseded by a newer source version",
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


def test_dispatch_late_runtime_success_cannot_overwrite_cancelled(db_session, monkeypatch):
    db, agent = db_session
    task = AgentTask(
        assignee_agent_id=agent.id,
        source="portal",
        task_type="agent_async_task",
        task_family="agent_task",
        title="Review branch",
        skill_name="review",
        root_task_id="task-async-cancelled",
        task_session_id="agent-task:task-async-cancelled",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "user_task": "Review the branch.",
                "skill_name": "review",
                "task_session_id": "agent-task:task-async-cancelled",
                "root_task_id": "task-async-cancelled",
                "parent_task_id": None,
            }
        ),
        status="queued",
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class Resp:
        status_code = 200
        text = '{"ok": true, "status": "success", "final_response": "ok"}'

        @staticmethod
        def json():
            return {"ok": True, "status": "success", "final_response": "ok"}

    async def fake_post(_url, _body):
        fresh = db.get(AgentTask, task.id)
        fresh.status = "cancelled"
        fresh.summary = "Cancelled locally."
        db.add(fresh)
        db.commit()
        return Resp()

    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "cancelled"
    assert result.message == "late_runtime_result_ignored_because_task_is_cancelled"
    db.refresh(task)
    assert task.status == "cancelled"
    assert task.summary == "Cancelled locally."


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


def test_agent_async_delegation_task_metadata_includes_source(monkeypatch, db_session):
    db, agent = db_session
    task = AgentTask(
        id="delegation-agent-task-1",
        assignee_agent_id=agent.id,
        owner_user_id=1,
        source="delegation",
        task_type="agent_async_task",
        task_family="agent_task",
        provider="jira",
        trigger="jira_mention",
        skill_name="reply-skill",
        root_task_id="delegation-agent-task-1",
        task_session_id="agent-task:delegation-agent-task-1",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "skill_name": "reply-skill",
                "task_session_id": "agent-task:delegation-agent-task-1",
                "root_task_id": "delegation-agent-task-1",
                "parent_task_id": None,
                "delegation_rule_id": "rule-1",
                "user_task": "You are responding as Bot User.\nJira issue:\nhttps://jira.local/browse/ENG-1\n\nComment:\nBot User ping",
                "delegation": {
                    "delegation_rule_id": "rule-1",
                    "source": "jira_mention",
                    "provider": "jira",
                    "source_url": "https://jira.local/browse/ENG-1",
                    "represented_identity": "Bot User",
                    "reply_target": {"provider": "jira", "kind": "issue_comment", "issue_key": "ENG-1"},
                },
            }
        ),
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
    assert captured["body"]["session_id"] == "agent-task:delegation-agent-task-1"
    assert "portal_subscription_id" not in metadata
    assert "portal_automation_rule_id" not in metadata
    assert "portal_automation_source" not in metadata
    assert "portal_automation_provider" not in metadata
    assert metadata["portal_delegation_rule_id"] == "rule-1"
    assert metadata["portal_delegation_source"] == "jira_mention"
    assert metadata["portal_delegation_provider"] == "jira"
    assert metadata["portal_task_trigger"] == "jira_mention"
    assert metadata["portal_task_mode"] == "agent_async_task"
    assert metadata["portal_skill_name"] == "reply-skill"
    assert metadata["portal_task_session_id"] == "agent-task:delegation-agent-task-1"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"ok": True, "status": "done"}, "done"),
        ({"ok": True, "status": "completed"}, "done"),
        ({"ok": True, "status": "blocked", "blockers": ["needs access"]}, "blocked"),
        ({"ok": True, "status": "stale"}, "stale"),
        ({"ok": True, "status": "cancelled"}, "cancelled"),
        ({"ok": True, "status": "canceled"}, "cancelled"),
        ({"ok": True, "status": "pending_restart"}, "pending_restart"),
        ({"ok": True, "status": "cancel_failed", "message": "stop failed"}, "cancel_failed"),
        ({"ok": False, "status": "done"}, "failed"),
    ],
)
def test_normalize_runtime_response_extended_statuses(payload, expected):
    class Resp:
        status_code = 200
        text = json.dumps(payload)

        @staticmethod
        def json():
            return payload

    outcome = TaskDispatcherService._normalize_runtime_response(Resp())
    assert outcome.terminal_status == expected


def test_normalize_runtime_response_unsupported_status_malformed():
    payload = {"ok": True, "status": "mystery"}
    class Resp:
        status_code = 200
        text = json.dumps(payload)

        @staticmethod
        def json():
            return payload

    outcome = TaskDispatcherService._normalize_runtime_response(Resp())
    assert outcome.is_malformed is True


def test_dispatch_task_sets_pending_restart_summary(db_session, monkeypatch):
    db, agent = db_session
    task = _create_task(db, agent.id)
    service = TaskDispatcherService()
    monkeypatch.setattr(service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

    class SubmitResp:
        status_code = 200
        text = '{"ok": true, "status": "pending_restart"}'
        @staticmethod
        def json():
            return {"ok": True, "status": "pending_restart"}

    async def fake_post(_url, _body):
        return SubmitResp()
    monkeypatch.setattr(service, "_post_to_runtime", fake_post)
    result = asyncio.run(service.dispatch_task(task.id, db))
    assert result.task_status == "pending_restart"
    db.refresh(task)
    assert task.summary


def test_delegation_dispatch_uses_agent_async_and_tasks_execute_endpoint():
    source = Path("app/services/task_dispatcher.py").read_text()
    assert '"/api/tasks/execute"' in source
    assert "_grant_github_pr_review_runtime_metadata" not in source
    assert "github_review_task" not in source
    assert "jira_workflow_review_task" not in source
    delegation_service = Path("app/services/delegation_rule_service.py").read_text()
    assert '"agent_async_task"' in delegation_service
    assert "github_review_task" not in delegation_service
    assert "triggered_event_task" not in delegation_service
    assert "/api/chat" not in delegation_service
    assert "/api/chat/stream" not in delegation_service
