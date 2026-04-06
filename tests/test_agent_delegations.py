import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentDelegation, AgentTask, User
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.services.auth_service import hash_password


def _build_client_with_overrides(monkeypatch):
    from app.main import app
    import app.api.agent_delegations as delegations_api

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

    leader = Agent(
        name="Leader",
        description="leader",
        owner_user_id=owner.id,
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
        deployment_name="dep-leader",
        service_name="svc-leader",
        pvc_name="pvc-leader",
        endpoint_path="/",
        agent_type="workspace",
    )
    assignee = Agent(
        name="Assignee",
        description="assignee",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo2.git",
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
    outsider = Agent(
        name="Outsider",
        description="outsider",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo3.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-outsider",
        service_name="svc-outsider",
        pvc_name="pvc-outsider",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(leader)
    db.add(assignee)
    db.add(outsider)
    db.commit()
    db.refresh(leader)
    db.refresh(assignee)
    db.refresh(outsider)

    group = AgentGroupRepository(db).create(
        name="Delegation Group",
        leader_agent_id=leader.id,
        ephemeral_agent_policy_json='{"allow_task_mode": true}',
        created_by_user_id=owner.id,
    )
    AgentGroupMemberRepository(db).create(
        group_id=group.id,
        member_type="agent",
        user_id=None,
        agent_id=leader.id,
        role="leader",
    )
    AgentGroupMemberRepository(db).create(
        group_id=group.id,
        member_type="agent",
        user_id=None,
        agent_id=assignee.id,
        role="member",
    )

    captured_bodies = []

    monkeypatch.setattr(
        "app.services.proxy_service.ProxyService.build_agent_base_url",
        lambda _self, _agent: "http://runtime",
    )

    class _SuccessResp:
        status_code = 200
        text = (
            '{"ok": true, "status": "success", "output_payload": '
            '{"delegation_result": {"status": "done", "result_summary": "Done", '
            '"result_artifacts": [{"artifact": "report"}], "blockers": [], '
            '"next_recommendation": "merge", "audit_trace": {"steps": 2}}}}'
        )

        @staticmethod
        def json():
            return {
                "ok": True,
                "status": "success",
                "output_payload": {
                    "delegation_result": {
                        "status": "done",
                        "result_summary": "Done",
                        "result_artifacts": [{"artifact": "report"}],
                        "blockers": [],
                        "next_recommendation": "merge",
                        "audit_trace": {"steps": 2},
                    }
                },
            }

    async def _fake_post(_self, _url: str, body: dict):
        captured_bodies.append(body)
        return _SuccessResp()

    monkeypatch.setattr("app.services.task_dispatcher.TaskDispatcherService._post_to_runtime", _fake_post)

    def _override_user():
        return SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[delegations_api.get_current_user] = _override_user
    app.dependency_overrides[delegations_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, group, leader, assignee, outsider, captured_bodies, _cleanup


def _payload(group_id: str, leader_id: str, assignee_id: str) -> dict:
    return {
        "group_id": group_id,
        "leader_agent_id": leader_id,
        "assignee_agent_id": assignee_id,
        "objective": "Review PR #12",
        "visibility": "leader_only",
        "skill_name": "github-review",
        "skill_kwargs_json": '{"agent_mode":"task"}',
        "input_artifacts_json": '{"pull_request": 12}',
        "expected_output_schema_json": '{"type": "object"}',
        "retry_policy_json": '{"max_retries": 1}',
    }


def test_leader_can_create_delegation_and_creates_delegation_task(monkeypatch):
    client, db, group, leader, assignee, _outsider, captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert response.status_code == 200
        body = response.json()
        assert body["group_id"] == group.id
        assert body["status"] == "done"
        assert body["agent_task_id"] is not None

        task = AgentTaskRepository(db).get_by_id(body["agent_task_id"])
        assert task is not None
        assert task.task_type == "delegation_task"
        assert task.source == "agent"

        task_payload = json.loads(task.input_payload_json)
        assert task_payload["delegation_id"] == body["id"]
        assert task_payload["skill_name"] == "github-review"

        assert captured_bodies, "dispatch payload should be captured"
        metadata = captured_bodies[0]["metadata"]
        assert metadata["portal_delegation_id"] == body["id"]
        assert metadata["portal_group_id"] == group.id
        assert metadata["portal_leader_agent_id"] == leader.id
        assert metadata["portal_assignee_agent_id"] == assignee.id

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.result_summary == "Done"
        assert json.loads(delegation.result_artifacts_json)[0]["artifact"] == "report"
        assert json.loads(delegation.blockers_json) == []
        assert delegation.next_recommendation == "merge"
        assert delegation.audit_trace_json is not None
        assert json.loads(delegation.audit_trace_json)["steps"] == 2
    finally:
        cleanup()


def test_non_leader_cannot_create_delegation(monkeypatch):
    client, _db, group, _leader, assignee, outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post("/api/agent-delegations", json=_payload(group.id, outsider.id, assignee.id))
        assert response.status_code == 403
        assert "leader_agent_id" in response.json()["detail"]
    finally:
        cleanup()


def test_assignee_not_in_group_is_rejected(monkeypatch):
    client, _db, group, leader, _assignee, outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, outsider.id))
        assert response.status_code == 403
        assert "Assignee agent must be a member" in response.json()["detail"]
    finally:
        cleanup()


def test_invalid_visibility_is_rejected(monkeypatch):
    client, _db, group, leader, assignee, _outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = _payload(group.id, leader.id, assignee.id)
        payload["visibility"] = "public"
        response = client.post("/api/agent-delegations", json=payload)
        assert response.status_code == 422
    finally:
        cleanup()


def test_invalid_json_payload_fields_are_rejected(monkeypatch):
    client, _db, group, leader, assignee, _outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = _payload(group.id, leader.id, assignee.id)
        payload["input_artifacts_json"] = "not-json"
        response = client.post("/api/agent-delegations", json=payload)
        assert response.status_code == 400
        assert "input_artifacts_json" in response.json()["detail"]
    finally:
        cleanup()


def test_skill_name_missing_is_rejected(monkeypatch):
    client, _db, group, leader, assignee, _outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = _payload(group.id, leader.id, assignee.id)
        payload.pop("skill_name")
        response = client.post("/api/agent-delegations", json=payload)
        assert response.status_code == 422
    finally:
        cleanup()


def test_malformed_runtime_delegation_result_marks_delegation_failed_but_keeps_task_result(monkeypatch):
    client, db, group, leader, assignee, _outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        class _MalformedResp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result": "ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_self, _url: str, _body: dict):
            return _MalformedResp()

        monkeypatch.setattr("app.services.task_dispatcher.TaskDispatcherService._post_to_runtime", _fake_post)

        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert response.status_code == 200
        body = response.json()

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.status == "failed"
        assert "delegation_result" in (delegation.result_summary or "")

        task = db.get(AgentTask, body["agent_task_id"])
        assert task.status == "done"
        assert json.loads(task.result_payload_json)["status"] == "success"
    finally:
        cleanup()


def test_group_task_board_endpoint_returns_summary_and_items(monkeypatch):
    client, _db, group, leader, assignee, _outsider, _captured_bodies, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        first = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert first.status_code == 200

        response = client.get(f"/api/agent-groups/{group.id}/task-board")
        assert response.status_code == 200
        body = response.json()
        assert body["group_id"] == group.id
        assert body["leader_agent_id"] == leader.id
        assert body["summary"]["total"] >= 1
        assert body["summary"]["done"] >= 1
        assert len(body["items"]) >= 1
        assert body["items"][0]["assignee_agent_id"] == assignee.id
    finally:
        cleanup()
