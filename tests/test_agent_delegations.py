import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentDelegation, AgentTask, GroupSharedContextSnapshot, User
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
    direct_member_user = User(username="direct-member", password_hash=hash_password("pw"), role="viewer", is_active=True)
    member_agent_owner = User(username="member-agent-owner", password_hash=hash_password("pw"), role="viewer", is_active=True)
    outsider_user = User(username="outsider", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, leader_owner, direct_member_user, member_agent_owner, outsider_user])
    db.commit()
    for u in [admin_user, leader_owner, direct_member_user, member_agent_owner, outsider_user]:
        db.refresh(u)

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
    member_owned_agent = Agent(
        name="Member Owned Agent",
        description="member-owned",
        owner_user_id=member_agent_owner.id,
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
        deployment_name="dep-member-owned",
        service_name="svc-member-owned",
        pvc_name="pvc-member-owned",
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
        repo_url="https://example.com/repo4.git",
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
    db.add_all([leader, assignee, member_owned_agent, outsider_agent])
    db.commit()
    for a in [leader, assignee, member_owned_agent, outsider_agent]:
        db.refresh(a)

    group = AgentGroupRepository(db).create(
        name="Delegation Group",
        leader_agent_id=leader.id,
        ephemeral_agent_policy_json='{"allow_task_mode": true}',
        created_by_user_id=leader_owner.id,
    )
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=leader.id, role="leader")
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=assignee.id, role="member")
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="user", user_id=direct_member_user.id, agent_id=None, role="member")
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=member_owned_agent.id, role="member")

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

    return (
        TestClient(app),
        db,
        group,
        leader,
        assignee,
        outsider_agent,
        admin_user,
        leader_owner,
        direct_member_user,
        member_agent_owner,
        outsider_user,
        state,
        _set_user,
        _cleanup,
    )


def _payload(
    group_id: str,
    leader_id: str,
    assignee_id: str,
    visibility: str = "leader_only",
    scoped_context_ref: str | None = None,
    scoped_context_payload_json: str | None = None,
) -> dict:
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
        "scoped_context_ref": scoped_context_ref,
        "scoped_context_payload_json": scoped_context_payload_json,
    }


def test_create_authorization_admin_and_leader_owner_allowed_unrelated_forbidden(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, admin_user, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(admin_user)
        admin_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert admin_resp.status_code == 200

        set_user(leader_owner)
        owner_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert owner_resp.status_code == 200

        set_user(outsider_user)
        forbidden_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert forbidden_resp.status_code == 403
        assert "leader owner" in forbidden_resp.json()["detail"]
    finally:
        cleanup()


def test_leader_can_create_delegation_and_creates_delegation_task(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
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

        metadata = state["captured_bodies"][0]["metadata"]
        assert metadata["portal_delegation_id"] == body["id"]
        assert metadata["portal_group_id"] == group.id
        assert state["saw_running"] and state["saw_running"][0] is True

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.result_summary == "Done"
        assert json.loads(delegation.result_artifacts_json)[0]["artifact"] == "report"
    finally:
        cleanup()


def test_non_leader_agent_id_cannot_be_used(monkeypatch):
    client, _db, group, _leader, assignee, outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, outsider_agent.id, assignee.id))
        assert response.status_code == 403
        assert "leader_agent_id" in response.json()["detail"]
    finally:
        cleanup()


def test_assignee_not_in_group_is_rejected(monkeypatch):
    client, _db, group, leader, _assignee, outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, outsider_agent.id))
        assert response.status_code == 403
    finally:
        cleanup()


def test_json_type_validation_for_contract_fields(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        for field, value in [
            ("skill_kwargs_json", '[1,2,3]'),
            ("expected_output_schema_json", '[1,2,3]'),
            ("retry_policy_json", '[1,2,3]'),
            ("input_artifacts_json", '{"x":1}'),
        ]:
            payload = _payload(group.id, leader.id, assignee.id)
            payload[field] = value
            response = client.post("/api/agent-delegations", json=payload)
            assert response.status_code == 422
    finally:
        cleanup()


def test_visibility_scope_group_visible_is_group_scoped_not_global(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, admin_user, leader_owner, direct_member_user, member_agent_owner, outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        leader_only_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="leader_only"))
        group_visible_resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="group_visible"))
        assert leader_only_resp.status_code == 200
        assert group_visible_resp.status_code == 200

        set_user(outsider_user)
        outsider_list = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert outsider_list.status_code == 200
        assert outsider_list.json() == []

        set_user(direct_member_user)
        direct_list = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert len(direct_list.json()) == 1
        assert direct_list.json()[0]["visibility"] == "group_visible"

        set_user(member_agent_owner)
        owner_list = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert len(owner_list.json()) == 1
        assert owner_list.json()[0]["visibility"] == "group_visible"

        set_user(leader_owner)
        leader_list = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert len(leader_list.json()) == 2

        set_user(admin_user)
        admin_list = client.get(f"/api/agent-groups/{group.id}/delegations")
        assert len(admin_list.json()) == 2
    finally:
        cleanup()


def test_leader_only_hidden_from_group_participant_non_leader(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="leader_only"))
        delegation_id = resp.json()["id"]

        set_user(direct_member_user)
        detail = client.get(f"/api/agent-delegations/{delegation_id}")
        assert detail.status_code == 404

        board = client.get(f"/api/agent-groups/{group.id}/task-board")
        assert board.status_code == 200
        assert board.json()["summary"]["total"] == 0
    finally:
        cleanup()


def test_shared_visibility_rule_used_by_panel_and_task_board(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        resp = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="group_visible"))
        assert resp.status_code == 200

        set_user(outsider_user)
        board = client.get(f"/api/agent-groups/{group.id}/task-board")
        assert board.status_code == 200
        assert board.json()["summary"]["total"] == 0

        import app.web as web_module

        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: SimpleNamespace(id=outsider_user.id, role=outsider_user.role, username=outsider_user.username, nickname=outsider_user.username))
        monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
        panel = client.get(f"/app/agent-groups/{group.id}/task-board/panel")
        assert panel.status_code == 200
        assert "No delegations yet." in panel.text
    finally:
        cleanup()



def test_delegation_creation_persists_shared_context_snapshot_with_explicit_ref(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post(
            "/api/agent-delegations",
            json=_payload(
                group.id,
                leader.id,
                assignee.id,
                scoped_context_ref="ctx-pr-12",
                scoped_context_payload_json='{"pr": 12, "repo": "portal"}',
            ),
        )
        assert response.status_code == 200
        body = response.json()

        delegation = db.get(AgentDelegation, body["id"])
        task = db.get(AgentTask, body["agent_task_id"])
        assert delegation.scoped_context_ref == "ctx-pr-12"
        assert task.shared_context_ref == "ctx-pr-12"

        snapshot = db.query(GroupSharedContextSnapshot).filter(
            GroupSharedContextSnapshot.group_id == group.id,
            GroupSharedContextSnapshot.context_ref == "ctx-pr-12",
        ).first()
        assert snapshot is not None
        assert snapshot.scope_kind == "delegation"
        assert json.loads(snapshot.payload_json)["pr"] == 12

        assert state["captured_bodies"][0]["shared_context_ref"] == "ctx-pr-12"
        assert state["captured_bodies"][0]["context_ref"]["repo"] == "portal"
    finally:
        cleanup()


def test_delegation_creation_auto_generates_context_ref_when_payload_provided(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post(
            "/api/agent-delegations",
            json=_payload(
                group.id,
                leader.id,
                assignee.id,
                scoped_context_ref=None,
                scoped_context_payload_json='{"doc":"brief"}',
            ),
        )
        assert response.status_code == 200
        body = response.json()

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.scoped_context_ref is not None
        assert delegation.scoped_context_ref.startswith("ctx-")

        snapshot = db.query(GroupSharedContextSnapshot).filter(
            GroupSharedContextSnapshot.group_id == group.id,
            GroupSharedContextSnapshot.context_ref == delegation.scoped_context_ref,
        ).first()
        assert snapshot is not None
    finally:
        cleanup()


def test_dispatch_fails_when_shared_context_ref_missing_snapshot(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post(
            "/api/agent-delegations",
            json=_payload(group.id, leader.id, assignee.id, scoped_context_ref="ctx-missing", scoped_context_payload_json=None),
        )
        assert response.status_code == 409
        assert "Shared context snapshot not found" in response.json()["detail"]
    finally:
        cleanup()


def test_malformed_runtime_delegation_result_marks_failed_but_keeps_task_result(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)

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
        body = response.json()
        delegation = db.get(AgentDelegation, body["id"])
        task = db.get(AgentTask, body["agent_task_id"])

        assert delegation.status == "failed"
        assert task.status == "done"
        assert json.loads(task.result_payload_json)["status"] == "success"
    finally:
        cleanup()
