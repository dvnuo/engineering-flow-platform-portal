import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentDelegation, AgentTask, User
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
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
    admin_user = User(username="admin", password_hash=hash_password("pw"), role="admin", is_active=True)
    leader_owner = User(username="owner", password_hash=hash_password("pw"), role="viewer", is_active=True)
    outsider_user = User(username="outsider", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, leader_owner, outsider_user])
    db.commit()
    db.refresh(admin_user)
    db.refresh(leader_owner)
    db.refresh(outsider_user)

    leader = Agent(
        name="Leader",
        description="leader",
        owner_user_id=leader_owner.id,
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
        owner_user_id=leader_owner.id,
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
    outsider_agent = Agent(
        name="Outsider Agent",
        description="outsider",
        owner_user_id=outsider_user.id,
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
    db.add_all([leader, assignee, outsider_agent])
    db.commit()
    db.refresh(leader)
    db.refresh(assignee)
    db.refresh(outsider_agent)

    group = AgentGroupRepository(db).create(
        name="Delegation Group",
        leader_agent_id=leader.id,
        ephemeral_agent_policy_json='{"allow_task_mode": true}',
        created_by_user_id=leader_owner.id,
    )
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=leader.id, role="leader")
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=assignee.id, role="member")

    state = {
        "user": SimpleNamespace(id=leader_owner.id, role=leader_owner.role, username=leader_owner.username, nickname="Owner"),
        "captured_bodies": [],
        "saw_running": [],
    }

    monkeypatch.setattr("app.services.proxy_service.ProxyService.build_agent_base_url", lambda _self, _agent: "http://runtime")

    class _SuccessResp:
        status_code = 200
        text = (
            '{"ok": true, "status": "success", "output_payload": '
            '{"delegation_result": {"status": "done", "summary": "Done", '
            '"artifacts": [{"artifact": "report"}], "blockers": [], '
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
                        "summary": "Done",
                        "artifacts": [{"artifact": "report"}],
                        "blockers": [],
                        "next_recommendation": "merge",
                        "audit_trace": {"steps": 2},
                    }
                },
            }

    async def _fake_post(_self, _url: str, body: dict):
        state["captured_bodies"].append(body)
        delegation_id = body.get("metadata", {}).get("portal_delegation_id")
        if delegation_id:
            delegation = db.get(AgentDelegation, delegation_id)
            state["saw_running"].append(bool(delegation and delegation.status == "running"))
        return _SuccessResp()

    monkeypatch.setattr("app.services.task_dispatcher.TaskDispatcherService._post_to_runtime", _fake_post)

    def _override_user():
        return state["user"]

    def _override_db():
        yield db

    app.dependency_overrides[delegations_api.get_current_user] = _override_user
    app.dependency_overrides[delegations_api.get_db] = _override_db

    def _set_user(user_obj):
        state["user"] = SimpleNamespace(id=user_obj.id, role=user_obj.role, username=user_obj.username, nickname=user_obj.username)

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, group, leader, assignee, outsider_agent, admin_user, leader_owner, outsider_user, state, _set_user, _cleanup


def _payload(group_id: str, leader_id: str, assignee_id: str, visibility: str = "leader_only") -> dict:
    return {
        "group_id": group_id,
        "leader_agent_id": leader_id,
        "assignee_agent_id": assignee_id,
        "objective": "Review PR #12",
        "visibility": visibility,
        "skill_name": "github-review",
        "skill_kwargs_json": '{"agent_mode":"task"}',
        "input_artifacts_json": '[{"type":"pull_request","id":12}]',
        "expected_output_schema_json": '{"type": "object"}',
        "retry_policy_json": '{"max_retries": 1}',
    }


def test_leader_can_create_delegation_and_creates_delegation_task(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, _owner, _outsider_user, state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
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

        assert state["captured_bodies"]
        metadata = state["captured_bodies"][0]["metadata"]
        assert metadata["portal_delegation_id"] == body["id"]
        assert metadata["portal_group_id"] == group.id
        assert metadata["portal_leader_agent_id"] == leader.id
        assert metadata["portal_assignee_agent_id"] == assignee.id
        assert state["saw_running"] and state["saw_running"][0] is True

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.result_summary == "Done"
        assert json.loads(delegation.result_artifacts_json)[0]["artifact"] == "report"
        assert json.loads(delegation.blockers_json) == []
        assert delegation.next_recommendation == "merge"
        assert json.loads(delegation.audit_trace_json)["steps"] == 2
    finally:
        cleanup()


def test_non_leader_cannot_create_delegation(monkeypatch):
    client, _db, group, _leader, assignee, outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post("/api/agent-delegations", json=_payload(group.id, outsider_agent.id, assignee.id))
        assert response.status_code == 403
        assert "leader_agent_id" in response.json()["detail"]
    finally:
        cleanup()


def test_assignee_not_in_group_is_rejected(monkeypatch):
    client, _db, group, leader, _assignee, outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, outsider_agent.id))
        assert response.status_code == 403
        assert "Assignee agent must be a member" in response.json()["detail"]
    finally:
        cleanup()


def test_invalid_visibility_is_rejected(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = _payload(group.id, leader.id, assignee.id)
        payload["visibility"] = "public"
        response = client.post("/api/agent-delegations", json=payload)
        assert response.status_code == 422
    finally:
        cleanup()


def test_invalid_json_payload_fields_are_rejected(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = _payload(group.id, leader.id, assignee.id)
        payload["input_artifacts_json"] = "not-json"
        response = client.post("/api/agent-delegations", json=payload)
        assert response.status_code == 400
        assert "input_artifacts_json" in response.json()["detail"]
    finally:
        cleanup()


def test_skill_name_missing_is_rejected(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = _payload(group.id, leader.id, assignee.id)
        payload.pop("skill_name")
        response = client.post("/api/agent-delegations", json=payload)
        assert response.status_code == 422
    finally:
        cleanup()


def test_json_type_validation_for_contract_fields(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cases = [
            ("skill_kwargs_json", '[1,2,3]'),
            ("expected_output_schema_json", '[1,2,3]'),
            ("retry_policy_json", '[1,2,3]'),
            ("input_artifacts_json", '{"x":1}'),
        ]
        for field, value in cases:
            payload = _payload(group.id, leader.id, assignee.id)
            payload[field] = value
            response = client.post("/api/agent-delegations", json=payload)
            assert response.status_code == 422
    finally:
        cleanup()


def test_malformed_runtime_delegation_result_marks_delegation_failed_but_keeps_task_result(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, _owner, _outsider_user, _state, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
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


def test_visibility_filters_hide_leader_only_from_non_owner_non_admin(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, _owner, outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        leader_only_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="leader_only"))
        assert leader_only_resp.status_code == 200
        group_visible_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="group_visible"))
        assert group_visible_resp.status_code == 200

        set_user(outsider_user)

        detail_hidden = client.get(f"/api/agent-delegations/{leader_only_resp.json()['id']}")
        assert detail_hidden.status_code == 404

        list_resp = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["visibility"] == "group_visible"

        board_resp = client.get(f"/api/agent-groups/{group.id}/task-board")
        assert board_resp.status_code == 200
        assert board_resp.json()["summary"]["total"] == 1
        assert all(item["visibility"] == "group_visible" for item in board_resp.json()["items"])
    finally:
        cleanup()


def test_admin_can_see_all_delegations(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, admin_user, _owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        first = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="leader_only"))
        second = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="group_visible"))
        assert first.status_code == 200
        assert second.status_code == 200

        set_user(admin_user)
        list_resp = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 2
    finally:
        cleanup()
