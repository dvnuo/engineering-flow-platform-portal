from types import SimpleNamespace
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentDelegation, AgentTask, CapabilityProfile, PolicyProfile, RuntimeCapabilityCatalogSnapshot, User
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


def test_dispatch_late_runtime_success_cannot_overwrite_stale(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

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

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        stale_payload = json.dumps(
            {
                "ok": False,
                "error_code": "superseded_by_new_head_sha",
                "message": "GitHub review task superseded by a newer PR head_sha",
                "superseded_by_task_id": "new-task-1",
                "superseded_by_head_sha": "sha-2",
            }
        )

        class _Resp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result": "ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_url: str, _body: dict):
            fresh = db.get(AgentTask, task.id)
            fresh.status = "stale"
            fresh.result_payload_json = stale_payload
            db.add(fresh)
            db.commit()
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)

        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        body = response.json()
        assert body["task_status"] == "stale"
        assert "late_runtime_result_ignored_because_task_is_stale" in body["message"]

        db.refresh(task)
        assert task.status == "stale"
        assert task.result_payload_json == stale_payload
    finally:
        cleanup()


def test_dispatch_runtime_superseded_error_is_recorded_as_stale(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

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

        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")

        class _Resp:
            status_code = 200
            text = (
                '{"ok": false, "status": "error", '
                '"output_payload": {"error_code": "superseded_by_new_head_sha", "message": "superseded"}}'
            )

            @staticmethod
            def json():
                return {
                    "ok": False,
                    "status": "error",
                    "output_payload": {"error_code": "superseded_by_new_head_sha", "message": "superseded"},
                }

        async def _fake_post(_url: str, _body: dict):
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)
        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        body = response.json()
        assert body["task_status"] == "stale"

        db.refresh(task)
        assert task.status == "stale"
        parsed = json.loads(task.result_payload_json or "{}")
        assert parsed["output_payload"]["error_code"] == "superseded_by_new_head_sha"
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
        assert captured["metadata"]["portal_delegation_id"] == delegation.id
        assert captured["metadata"]["current_delegation_id"] == delegation.id
        assert captured["metadata"]["portal_group_id"] == "g-1"
        assert captured["metadata"]["group_id"] == "g-1"
        assert captured["metadata"]["current_coordination_run_id"] == "run-55"
    finally:
        cleanup()


def test_dispatch_includes_capability_and_policy_metadata(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        capability_profile = CapabilityProfile(
            name="cap-dispatch",
            tool_set_json='["shell"]',
            channel_set_json='["jira_get_issue"]',
            skill_set_json='["review"]',
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["pull_request_review_requested"]',
            allowed_actions_json='["review_pull_request","add_comment"]',
        )
        policy_profile = PolicyProfile(
            name="policy-dispatch",
            auto_run_rules_json='{"require_explicit_allow": true, "allow_auto_run": false}',
            permission_rules_json='{"denied_capability_ids":["tool:shell"],"denied_adapter_actions":["adapter:github:add_comment"]}',
            transition_rules_json='{"external_trigger_allowlist":["github"],"external_trigger_blocklist":["slack"]}',
        )
        db.add(capability_profile)
        db.add(policy_profile)
        db.commit()
        db.refresh(capability_profile)
        db.refresh(policy_profile)

        agent.capability_profile_id = capability_profile.id
        agent.policy_profile_id = policy_profile.id
        db.add(agent)
        db.commit()

        task = _create_task(db, agent.id)
        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result":"ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_url: str, body: dict):
            captured.update(body)
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)
        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        metadata = captured["metadata"]
        assert metadata["capability_profile_id"] == capability_profile.id
        assert metadata["policy_profile_id"] == policy_profile.id
        assert "tool:shell" in metadata["allowed_capability_ids"]
        assert "skill:review" in metadata["allowed_capability_ids"]
        assert "channel_action:jira_get_issue" in metadata["allowed_capability_ids"]
        assert "adapter:github:review_pull_request" in metadata["allowed_capability_ids"]
        assert "adapter:github:add_comment" not in metadata["allowed_capability_ids"]
        assert "adapter:jira:add_comment" not in metadata["allowed_capability_ids"]
        assert "tool" in metadata["allowed_capability_types"]
        assert "channel_action" in metadata["allowed_capability_types"]
        assert "adapter_action" in metadata["allowed_capability_types"]
        assert metadata["allowed_external_systems"] == ["github"]
        assert metadata["allowed_webhook_triggers"] == ["pull_request_review_requested"]
        assert metadata["allowed_actions"] == ["review_pull_request", "add_comment"]
        assert metadata["allowed_adapter_actions"] == ["adapter:github:review_pull_request"]
        assert metadata["unresolved_tools"] == []
        assert metadata["unresolved_skills"] == []
        assert metadata["unresolved_channels"] == []
        assert metadata["unresolved_actions"] == ["add_comment"]
        assert metadata["resolved_action_mappings"] == {
            "review_pull_request": "adapter:github:review_pull_request"
        }
        assert metadata["runtime_capability_catalog_version"] is not None
        assert metadata["runtime_capability_catalog_source"] in {"seed_fallback", "settings_snapshot", "runtime_api"}
        assert metadata["catalog_validation_mode"] in {"seed_fallback", "full_snapshot"}
        assert metadata["policy_context"]["policy_profile_id"] == policy_profile.id
        assert metadata["policy_context"]["auto_run_rules"]["require_explicit_allow"] is True
        assert metadata["governance_require_explicit_allow"] is True
        assert metadata["governance_allow_auto_run"] is False
        assert metadata["governance_external_allowlist"] == ["github"]
        assert metadata["governance_external_blocklist"] == ["slack"]
        assert metadata["denied_capability_ids"] == ["tool:shell"]
        assert metadata["denied_adapter_actions"] == ["adapter:github:add_comment"]
        assert metadata["portal_task_id"] == task.id
        assert metadata["portal_task_source"] == task.source
        assert metadata["current_task_id"] == task.id
        assert metadata["source_type"] == task.source
        assert metadata["source_ref"] == task.id
    finally:
        cleanup()


def test_dispatch_includes_capability_metadata_defaults_when_profile_is_missing(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        agent.capability_profile_id = None
        agent.policy_profile_id = None
        db.add(agent)
        db.commit()

        task = _create_task(db, agent.id)
        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result":"ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_url: str, body: dict):
            captured.update(body)
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)
        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        metadata = captured["metadata"]
        assert metadata["capability_profile_id"] is None
        assert metadata["policy_profile_id"] is None
        assert metadata["runtime_capability_catalog_version"] is not None
        assert metadata["catalog_validation_mode"] in {"seed_fallback", "full_snapshot"}
        assert metadata["policy_context"]["policy_profile_id"] is None
        assert metadata["portal_task_id"] == task.id
        assert metadata["portal_task_source"] == task.source
        assert metadata["current_task_id"] == task.id
        assert metadata["source_type"] == task.source
        assert metadata["source_ref"] == task.id
        assert metadata["allowed_capability_ids"] == []
        assert metadata["allowed_capability_types"] == []
        assert metadata["allowed_actions"] == []
        assert metadata["allowed_adapter_actions"] == []
        assert metadata["unresolved_actions"] == []
        assert metadata["resolved_action_mappings"] == {}
    finally:
        cleanup()


def test_dispatch_uses_assignee_agent_scoped_catalog_snapshot(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        db.add(
            RuntimeCapabilityCatalogSnapshot(
                source_agent_id=agent.id,
                catalog_version="dispatch-agent-v1",
                catalog_source="runtime_api",
                payload_json='{"catalog_version":"dispatch-agent-v1","capabilities":[{"capability_id":"adapter:github:review_pull_request","capability_type":"adapter_action","action_alias":"review_pull_request"}]}',
            )
        )
        db.commit()

        capability_profile = CapabilityProfile(name="cap-dispatch-snapshot", allowed_actions_json='["review_pull_request"]')
        db.add(capability_profile)
        db.commit()
        db.refresh(capability_profile)
        agent.capability_profile_id = capability_profile.id
        db.add(agent)
        db.commit()

        task = _create_task(db, agent.id)
        monkeypatch.setattr(tasks_api.task_dispatcher_service.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime")
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result":"ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_url: str, body: dict):
            captured.update(body)
            return _Resp()

        monkeypatch.setattr(tasks_api.task_dispatcher_service, "_post_to_runtime", _fake_post)
        response = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert response.status_code == 200
        assert captured["metadata"]["runtime_capability_catalog_version"] == "dispatch-agent-v1"
        assert captured["metadata"]["runtime_capability_catalog_source"] == "runtime_api"
    finally:
        cleanup()


def test_post_to_runtime_includes_internal_api_key_header(monkeypatch):
    service = TaskDispatcherService()
    captured = {}

    monkeypatch.setattr(
        service.proxy_service,
        "build_runtime_internal_headers",
        lambda: {"X-Internal-Api-Key": "runtime-s2s-key"},
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers or {}
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True}, text='{"ok": true}')

    monkeypatch.setattr("app.services.task_dispatcher.httpx.AsyncClient", _FakeClient)

    import asyncio

    result = asyncio.run(service._post_to_runtime("http://runtime/api/tasks/execute", {"task_id": "t-1"}))
    assert result.status_code == 200
    assert captured["headers"]["X-Internal-Api-Key"] == "runtime-s2s-key"
    assert captured["json"]["task_id"] == "t-1"
