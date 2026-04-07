import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentCoordinationRun, AgentDelegation, AgentTask, AuditLog, CapabilityProfile, GroupSharedContextSnapshot, PolicyProfile, User
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.services.auth_service import hash_password


def _build_client_with_overrides(monkeypatch):
    from app.main import app
    import app.api.agent_delegations as delegations_api
    import app.deps as deps_module

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
        agent_type="specialist",
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
        "original_internal_api_key": deps_module.settings.portal_internal_api_key,
    }
    deps_module.settings.portal_internal_api_key = "internal-test-key"

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
        deps_module.settings.portal_internal_api_key = state["original_internal_api_key"]
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
        deps_module,
        _cleanup,
    )


def _payload(
    group_id: str,
    leader_id: str,
    assignee_id: str,
    visibility: str = "leader_only",
    scoped_context_ref: str | None = None,
    scoped_context_payload_json: str | None = None,
    leader_session_id: str | None = None,
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
        "leader_session_id": leader_session_id,
        "scoped_context_ref": scoped_context_ref,
        "scoped_context_payload_json": scoped_context_payload_json,
    }


def test_create_authorization_admin_and_leader_owner_allowed_unrelated_forbidden(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, admin_user, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert response.status_code == 200
        body = response.json()
        assert body["group_id"] == group.id
        assert body["status"] == "done"
        assert body["agent_task_id"] is not None
        assert body["reply_target_type"] == "leader"

        task = AgentTaskRepository(db).get_by_id(body["agent_task_id"])
        assert task is not None
        assert task.task_type == "delegation_task"
        assert task.source == "agent"

        task_payload = json.loads(task.input_payload_json)
        assert task_payload["delegation_id"] == body["id"]

        metadata = state["captured_bodies"][0]["metadata"]
        assert metadata["portal_delegation_id"] == body["id"]
        assert metadata["portal_group_id"] == group.id
        assert metadata["portal_delegation_reply_target"] == "leader"
        assert state["saw_running"] and state["saw_running"][0] is True

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.result_summary == "Done"
        assert json.loads(delegation.result_artifacts_json)[0]["artifact"] == "report"
    finally:
        cleanup()


def test_delegation_rejects_skill_not_allowed_by_assignee_capability_profile(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        profile = CapabilityProfile(name="cap-no-github-review", skill_set_json='["other-skill"]')
        db.add(profile)
        db.commit()
        db.refresh(profile)
        assignee.capability_profile_id = profile.id
        db.add(assignee)
        db.commit()

        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert response.status_code == 422
        assert "does not allow skill 'github-review'" in response.json()["detail"]
    finally:
        cleanup()


def test_delegation_leader_session_id_persists_and_dispatches(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post(
            "/api/agent-delegations",
            json=_payload(group.id, leader.id, assignee.id, leader_session_id="leader-session-123"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["leader_session_id"] == "leader-session-123"
        assert body["origin_session_id"] == "leader-session-123"
        assert body["reply_target_type"] == "leader"

        delegation = db.get(AgentDelegation, body["id"])
        assert delegation.leader_session_id == "leader-session-123"
        assert delegation.origin_session_id == "leader-session-123"
        assert delegation.reply_target_type == "leader"

        runtime_body = state["captured_bodies"][0]
        assert runtime_body["session_id"] == "leader-session-123"
        assert runtime_body["metadata"]["portal_leader_session_id"] == "leader-session-123"
        assert runtime_body["metadata"]["portal_delegation_reply_target"] == "leader"
        assert runtime_body["metadata"]["strict_delegation_result"] is True
        assert runtime_body["metadata"]["agent_mode"] == "specialist"
        assert runtime_body["input_payload"]["leader_session_id"] == "leader-session-123"
        assert runtime_body["input_payload"]["strict_delegation_result"] is True
        assert runtime_body["input_payload"]["agent_mode"] == "specialist"
    finally:
        cleanup()


def test_non_leader_agent_id_cannot_be_used(monkeypatch):
    client, _db, group, _leader, assignee, outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, outsider_agent.id, assignee.id))
        assert response.status_code == 403
        assert "leader_agent_id" in response.json()["detail"]
    finally:
        cleanup()


def test_assignee_not_in_group_is_rejected(monkeypatch):
    client, _db, group, leader, _assignee, outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, outsider_agent.id))
        assert response.status_code == 403
    finally:
        cleanup()


def test_self_delegation_is_rejected_for_leader_and_parent(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        leader_self = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, leader.id))
        assert leader_self.status_code == 409
        assert leader_self.json()["detail"] == "Leader agent cannot delegate to itself"

        parent_self_payload = _payload(group.id, leader.id, assignee.id)
        parent_self_payload["parent_agent_id"] = assignee.id
        parent_self = client.post("/api/agent-delegations", json=parent_self_payload)
        assert parent_self.status_code == 409
        assert parent_self.json()["detail"] == "Parent agent cannot delegate to itself"
    finally:
        cleanup()


def test_delegation_rejects_workspace_assignee(monkeypatch):
    client, db, group, leader, _assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        workspace_member_assignee = next(
            member.agent_id
            for member in AgentGroupMemberRepository(db).list_by_group(group.id)
            if member.agent_id and db.get(Agent, member.agent_id).owner_user_id == member_agent_owner.id
        )
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, workspace_member_assignee))
        assert response.status_code == 422
        assert response.json()["detail"] == "Assignee agent must be a specialist or task agent"
    finally:
        cleanup()


def test_delegation_rejects_assignee_not_in_specialist_pool(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        group.specialist_agent_pool_json = "[]"
        db.add(group)
        db.commit()
        set_user(leader_owner)
        response = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id))
        assert response.status_code == 422
        assert response.json()["detail"] == "Assignee agent must belong to the specialist agent pool"
    finally:
        cleanup()


def test_json_type_validation_for_contract_fields(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
    client, _db, group, leader, assignee, _outsider_agent, admin_user, leader_owner, direct_member_user, member_agent_owner, outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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


def test_shared_context_list_panel_auth_and_render(monkeypatch):
    client, db, group, _leader, _assignee, _outsider_agent, admin_user, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        snapshot = GroupSharedContextSnapshot(
            group_id=group.id,
            context_ref="ctx-panel-list",
            scope_kind="delegation",
            payload_json='{"secret":"hidden-in-list"}',
            created_by_user_id=leader_owner.id,
            source_delegation_id=None,
        )
        db.add(snapshot)
        db.commit()

        import app.web as web_module

        monkeypatch.setattr(web_module, "SessionLocal", lambda: db)

        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: None)
        unauthorized = client.get(f"/app/agent-groups/{group.id}/shared-contexts/panel")
        assert unauthorized.status_code == 401

        outsider_identity = {
            "id": outsider_user.id,
            "role": outsider_user.role,
            "username": outsider_user.username,
        }

        monkeypatch.setattr(
            web_module,
            "_current_user_from_cookie",
            lambda _request: SimpleNamespace(id=admin_user.id, role=admin_user.role, username=admin_user.username, nickname=admin_user.username),
        )
        allowed = client.get(f"/app/agent-groups/{group.id}/shared-contexts/panel")
        assert allowed.status_code == 200
        assert "ctx-panel-list" in allowed.text
        assert "hidden-in-list" not in allowed.text

        monkeypatch.setattr(
            web_module,
            "_current_user_from_cookie",
            lambda _request: SimpleNamespace(
                id=outsider_identity["id"],
                role=outsider_identity["role"],
                username=outsider_identity["username"],
                nickname=outsider_identity["username"],
            ),
        )
        forbidden = client.get(f"/app/agent-groups/{group.id}/shared-contexts/panel")
        assert forbidden.status_code == 403
    finally:
        cleanup()


def test_shared_context_detail_panel_auth_render_and_missing_ref(monkeypatch):
    client, db, group, _leader, _assignee, _outsider_agent, _admin_user, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        snapshot = GroupSharedContextSnapshot(
            group_id=group.id,
            context_ref="ctx-panel-detail",
            scope_kind="delegation",
            payload_json='{"repo":"portal","ticket":123}',
            created_by_user_id=leader_owner.id,
            source_delegation_id=None,
        )
        db.add(snapshot)
        db.commit()

        import app.web as web_module

        monkeypatch.setattr(web_module, "SessionLocal", lambda: db)

        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: None)
        unauthorized = client.get(f"/app/agent-groups/{group.id}/shared-contexts/ctx-panel-detail/panel")
        assert unauthorized.status_code == 401

        outsider_identity = {
            "id": outsider_user.id,
            "role": outsider_user.role,
            "username": outsider_user.username,
        }

        monkeypatch.setattr(
            web_module,
            "_current_user_from_cookie",
            lambda _request: SimpleNamespace(id=leader_owner.id, role=leader_owner.role, username=leader_owner.username, nickname=leader_owner.username),
        )
        allowed = client.get(f"/app/agent-groups/{group.id}/shared-contexts/ctx-panel-detail/panel")
        assert allowed.status_code == 200
        assert "ctx-panel-detail" in allowed.text
        assert "ticket" in allowed.text
        assert "123" in allowed.text

        missing = client.get(f"/app/agent-groups/{group.id}/shared-contexts/ctx-missing/panel")
        assert missing.status_code == 404

        monkeypatch.setattr(
            web_module,
            "_current_user_from_cookie",
            lambda _request: SimpleNamespace(
                id=outsider_identity["id"],
                role=outsider_identity["role"],
                username=outsider_identity["username"],
                nickname=outsider_identity["username"],
            ),
        )
        forbidden = client.get(f"/app/agent-groups/{group.id}/shared-contexts/ctx-panel-detail/panel")
        assert forbidden.status_code == 403
    finally:
        cleanup()



def test_delegation_creation_persists_shared_context_snapshot_with_explicit_ref(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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


def test_group_shared_context_list_endpoint_auth_and_shape(monkeypatch):
    client, db, group, _leader, _assignee, _outsider_agent, admin_user, leader_owner, direct_member_user, member_agent_owner, outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        db.add(
            GroupSharedContextSnapshot(
                group_id=group.id,
                context_ref="ctx-shared-a",
                scope_kind="delegation",
                payload_json='{"topic":"alpha"}',
                created_by_user_id=leader_owner.id,
                source_delegation_id=None,
            )
        )
        db.commit()

        for readable_user in [admin_user, leader_owner, direct_member_user, member_agent_owner]:
            set_user(readable_user)
            response = client.get(f"/api/agent-groups/{group.id}/shared-contexts")
            assert response.status_code == 200
            items = response.json()
            assert len(items) == 1
            assert items[0]["context_ref"] == "ctx-shared-a"
            assert "payload_json" not in items[0]

        set_user(outsider_user)
        forbidden = client.get(f"/api/agent-groups/{group.id}/shared-contexts")
        assert forbidden.status_code == 403
    finally:
        cleanup()


def test_group_shared_context_detail_endpoint_and_not_found(monkeypatch):
    client, db, group, _leader, _assignee, _outsider_agent, _admin_user, leader_owner, _direct_member_user, _member_agent_owner, outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        db.add(
            GroupSharedContextSnapshot(
                group_id=group.id,
                context_ref="ctx-shared-b",
                scope_kind="delegation",
                payload_json='{"repo":"portal","pr":22}',
                created_by_user_id=leader_owner.id,
                source_delegation_id=None,
            )
        )
        db.commit()

        set_user(leader_owner)
        response = client.get(f"/api/agent-groups/{group.id}/shared-contexts/ctx-shared-b")
        assert response.status_code == 200
        body = response.json()
        assert body["context_ref"] == "ctx-shared-b"
        assert json.loads(body["payload_json"])["pr"] == 22

        missing = client.get(f"/api/agent-groups/{group.id}/shared-contexts/ctx-does-not-exist")
        assert missing.status_code == 404

        set_user(outsider_user)
        forbidden = client.get(f"/api/agent-groups/{group.id}/shared-contexts/ctx-shared-b")
        assert forbidden.status_code == 403
    finally:
        cleanup()


def test_malformed_runtime_delegation_result_marks_failed_but_keeps_task_result(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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


def test_internal_api_rejects_missing_and_invalid_api_key(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, _leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = {
            "group_id": group.id,
            "leader_agent_id": leader.id,
            "assignee_agent_id": assignee.id,
            "objective": "Internal delegation",
            "visibility": "leader_only",
            "skill_name": "review",
        }
        missing_key = client.post("/api/internal/agent-delegations", json=payload)
        assert missing_key.status_code == 401

        bad_key = client.post("/api/internal/agent-delegations", json=payload, headers={"X-Internal-Api-Key": "wrong"})
        assert bad_key.status_code == 401
    finally:
        cleanup()


def test_internal_api_rejects_leader_agent_mismatch(monkeypatch):
    client, _db, group, _leader, assignee, outsider_agent, _admin, _leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = {
            "group_id": group.id,
            "leader_agent_id": outsider_agent.id,
            "assignee_agent_id": assignee.id,
            "objective": "Internal delegation mismatch",
            "visibility": "leader_only",
            "skill_name": "review",
        }
        response = client.post("/api/internal/agent-delegations", json=payload, headers={"X-Internal-Api-Key": "internal-test-key"})
        assert response.status_code == 403
        assert "leader_agent_id" in response.json()["detail"]
    finally:
        cleanup()


def test_internal_api_creates_delegation_task_snapshot_dispatch_and_audit(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, _leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        payload = {
            "group_id": group.id,
            "leader_agent_id": leader.id,
            "assignee_agent_id": assignee.id,
            "objective": "Internal delegation success",
            "visibility": "leader_only",
            "skill_name": "review",
            "coordination_run_id": "run-42",
            "round_index": 2,
            "scoped_context_ref": "ctx-internal-1",
            "scoped_context_payload": {"repo": "portal", "pr": 44},
            "input_artifacts": [{"type": "pull_request", "id": 44}],
            "skill_kwargs": {"agent_mode": "task"},
        }
        response = client.post("/api/internal/agent-delegations", json=payload, headers={"X-Internal-Api-Key": "internal-test-key"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "done"
        assert body["coordination_run_id"] == "run-42"
        assert body["round_index"] == 2

        task = db.get(AgentTask, body["agent_task_id"])
        assert task is not None
        assert task.task_type == "delegation_task"
        task_payload = json.loads(task.input_payload_json)
        assert task_payload["coordination_run_id"] == "run-42"
        assert task_payload["round_index"] == 2
        run_row = db.query(AgentCoordinationRun).filter(AgentCoordinationRun.coordination_run_id == "run-42").first()
        assert run_row is not None
        assert run_row.latest_round_index == 2
        assert run_row.status == "done"

        snapshot = db.query(GroupSharedContextSnapshot).filter(
            GroupSharedContextSnapshot.group_id == group.id,
            GroupSharedContextSnapshot.context_ref == "ctx-internal-1",
        ).first()
        assert snapshot is not None

        assert state["captured_bodies"]
        assert state["captured_bodies"][0]["metadata"]["portal_delegation_id"] == body["id"]

        audit_row = db.query(AuditLog).filter(AuditLog.action == "create_delegation", AuditLog.target_id == body["id"]).first()
        assert audit_row is not None
        details = json.loads(audit_row.details_json)
        assert details["source"] == "internal_runtime_api"
        assert details["group_id"] == group.id
        assert details["visibility"] == "leader_only"
        assert details["scoped_context_ref"] == "ctx-internal-1"
        assert details["coordination_run_id"] == "run-42"
        assert details["round_index"] == 2
    finally:
        cleanup()


def test_internal_read_routes_are_key_protected_and_unfiltered(monkeypatch):
    client, _db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        leader_only = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="leader_only"))
        group_visible = client.post("/api/agent-delegations", json=_payload(group.id, leader.id, assignee.id, visibility="group_visible"))
        internal_with_run = client.post(
            "/api/internal/agent-delegations",
            json={
                "group_id": group.id,
                "leader_agent_id": leader.id,
                "assignee_agent_id": assignee.id,
                "objective": "round task",
                "visibility": "leader_only",
                "skill_name": "review",
                "coordination_run_id": "run-abc",
                "round_index": 3,
            },
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert leader_only.status_code == 200
        assert group_visible.status_code == 200
        assert internal_with_run.status_code == 200

        for url in [
            f"/api/internal/agent-groups/{group.id}/delegations",
            f"/api/internal/agent-groups/{group.id}/task-board",
        ]:
            assert client.get(url).status_code == 401
            assert client.get(url, headers={"X-Internal-Api-Key": "wrong"}).status_code == 401

        delegations_response = client.get(
            f"/api/internal/agent-groups/{group.id}/delegations",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert delegations_response.status_code == 200
        items = delegations_response.json()
        assert len(items) == 3
        assert {item["visibility"] for item in items} == {"leader_only", "group_visible"}
        assert {item["reply_target_type"] for item in items} == {"leader"}
        assert all("origin_session_id" in item for item in items)
        assert any(item["coordination_run_id"] == "run-abc" and item["round_index"] == 3 for item in items)

        board_response = client.get(
            f"/api/internal/agent-groups/{group.id}/task-board",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert board_response.status_code == 200
        board = board_response.json()
        assert board["summary"]["total"] == 3
        assert board["summary"]["blocked"] == 0
        assert board["effective_max_parallel_tasks"] is None
        assert {item["reply_target_type"] for item in board["items"]} == {"leader"}
        assert all("origin_session_id" in item for item in board["items"])
        assert any(run["coordination_run_id"] == "run-abc" and run["latest_round_index"] == 3 for run in board["runs"])
    finally:
        cleanup()


def test_auto_cleanup_task_agent_policies_and_audit(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        import app.services.agent_group_service as group_service_module

        group_service_module.K8sService.delete_agent_runtime = lambda _self, _agent, destroy_data=False: SimpleNamespace(status="deleted", message=None)

        assignee.agent_type = "task"
        assignee.template_agent_id = leader.id
        assignee.task_scope_label = "scope-a"
        assignee.task_cleanup_policy = None
        db.add(assignee)
        db.commit()
        set_user(leader_owner)

        done_resp = client.post(
            "/api/agent-delegations",
            json={**_payload(group.id, leader.id, assignee.id), "skill_kwargs_json": '{"cleanup_policy":"delete_on_done"}'},
        )
        assert done_resp.status_code == 200
        assert db.get(Agent, assignee.id) is None
        done_delegation = db.get(AgentDelegation, done_resp.json()["id"])
        done_trace = json.loads(done_delegation.audit_trace_json or "{}")
        assert done_trace["cleanup"]["deleted_task_agent_ids"] == [assignee.id]
        done_audit = db.query(AuditLog).filter(AuditLog.action == "auto_cleanup_group_task_agent").order_by(AuditLog.id.desc()).first()
        assert done_audit is not None
        done_details = json.loads(done_audit.details_json)
        assert done_details["cleanup_policy"] == "delete_on_done"
        assert "coordination_run_id" in done_details

        recreated = Agent(
            id=assignee.id,
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
            agent_type="task",
        )
        db.add(recreated)
        db.commit()
        AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=recreated.id, role="member")
        group.specialist_agent_pool_json = json.dumps([recreated.id])
        db.add(group)
        db.commit()

        class _MalformedResp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result": "ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_self, _url: str, _body: dict):
            return _MalformedResp()

        monkeypatch.setattr("app.services.task_dispatcher.TaskDispatcherService._post_to_runtime", _fake_post)

        failed_resp = client.post(
            "/api/agent-delegations",
            json={**_payload(group.id, leader.id, assignee.id), "skill_kwargs_json": '{"cleanup_policy":"delete_on_terminal"}'},
        )
        assert failed_resp.status_code == 200
        assert db.get(Agent, assignee.id) is None
        failed_delegation = db.get(AgentDelegation, failed_resp.json()["id"])
        failed_trace = json.loads(failed_delegation.audit_trace_json or "{}")
        assert failed_trace["cleanup"]["deleted_task_agent_ids"] == [assignee.id]
        terminal_audit = db.query(AuditLog).filter(AuditLog.action == "auto_cleanup_group_task_agent").order_by(AuditLog.id.desc()).first()
        assert terminal_audit is not None
        terminal_details = json.loads(terminal_audit.details_json)
        assert terminal_details["cleanup_policy"] == "delete_on_terminal"
        assert "coordination_run_id" in terminal_details

        retained = Agent(
            id=assignee.id,
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
            agent_type="task",
        )
        db.add(retained)
        db.commit()
        AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=retained.id, role="member")
        group.specialist_agent_pool_json = json.dumps([retained.id])
        db.add(group)
        db.commit()

        class _SuccessResp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"delegation_result": {"status": "done"}}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"delegation_result": {"status": "done"}}}

        async def _fake_success_post(_self, _url: str, _body: dict):
            return _SuccessResp()

        monkeypatch.setattr("app.services.task_dispatcher.TaskDispatcherService._post_to_runtime", _fake_success_post)
        retain_resp = client.post(
            "/api/agent-delegations",
            json={**_payload(group.id, leader.id, assignee.id), "skill_kwargs_json": '{"cleanup_policy":"retain"}'},
        )
        assert retain_resp.status_code == 200
        assert db.get(Agent, assignee.id) is not None
    finally:
        cleanup()


def test_task_board_runs_summary_groups_by_coordination_run(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        set_user(leader_owner)
        ok_resp = client.post(
            "/api/internal/agent-delegations",
            json={
                "group_id": group.id,
                "leader_agent_id": leader.id,
                "assignee_agent_id": assignee.id,
                "objective": "run round 1",
                "visibility": "leader_only",
                "skill_name": "review",
                "coordination_run_id": "run-z",
                "round_index": 1,
            },
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert ok_resp.status_code == 200

        class _MalformedResp:
            status_code = 200
            text = '{"ok": true, "status": "success", "output_payload": {"result": "ok"}}'

            @staticmethod
            def json():
                return {"ok": True, "status": "success", "output_payload": {"result": "ok"}}

        async def _fake_post(_self, _url: str, _body: dict):
            return _MalformedResp()

        monkeypatch.setattr("app.services.task_dispatcher.TaskDispatcherService._post_to_runtime", _fake_post)
        failed_resp = client.post(
            "/api/internal/agent-delegations",
            json={
                "group_id": group.id,
                "leader_agent_id": leader.id,
                "assignee_agent_id": assignee.id,
                "objective": "run round 2",
                "visibility": "leader_only",
                "skill_name": "review",
                "coordination_run_id": "run-z",
                "round_index": 2,
            },
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert failed_resp.status_code == 200

        board = client.get(
            f"/api/internal/agent-groups/{group.id}/task-board",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert board.status_code == 200
        runs = {item["coordination_run_id"]: item for item in board.json()["runs"]}
        assert "run-z" in runs
        blocked_target = db.query(AgentDelegation).filter(AgentDelegation.coordination_run_id == "run-z", AgentDelegation.status == "failed").first()
        blocked_target.status = "blocked"
        db.add(blocked_target)
        db.commit()

        from app.services.task_dispatcher import TaskDispatcherService

        TaskDispatcherService()._sync_coordination_run_from_delegation(db, blocked_target)

        board = client.get(
            f"/api/internal/agent-groups/{group.id}/task-board",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert board.status_code == 200
        runs = {item["coordination_run_id"]: item for item in board.json()["runs"]}
        assert runs["run-z"]["total"] == 2
        assert runs["run-z"]["done"] == 1
        assert runs["run-z"]["failed"] == 0
        assert runs["run-z"]["blocked"] == 1
        assert runs["run-z"]["latest_round_index"] == 2
        assert runs["run-z"]["deleted_task_agent_ids"] == []
        assert board.json()["summary"]["blocked"] == 1
        run_row = db.query(AgentCoordinationRun).filter(AgentCoordinationRun.coordination_run_id == "run-z").first()
        run_summary = json.loads(run_row.summary_json or "{}")
        assert run_summary["blocked"] == 1
    finally:
        cleanup()


def test_internal_task_board_exposes_effective_max_parallel_tasks(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, _leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        policy = PolicyProfile(name="bounded", max_parallel_tasks=4)
        db.add(policy)
        db.commit()
        db.refresh(policy)
        leader.policy_profile_id = policy.id
        db.add(leader)
        db.commit()

        board = client.get(
            f"/api/internal/agent-groups/{group.id}/task-board",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert board.status_code == 200
        assert board.json()["effective_max_parallel_tasks"] == 4
    finally:
        cleanup()


def test_internal_coordination_run_read_endpoints(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, _leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create = client.post(
            "/api/internal/agent-delegations",
            json={
                "group_id": group.id,
                "leader_agent_id": leader.id,
                "assignee_agent_id": assignee.id,
                "objective": "run api check",
                "visibility": "leader_only",
                "skill_name": "review",
                "coordination_run_id": "run-read-1",
                "round_index": 1,
            },
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert create.status_code == 200
        assert client.get(f"/api/internal/agent-groups/{group.id}/coordination-runs").status_code == 401
        assert client.get(f"/api/internal/coordination-runs/run-read-1").status_code == 401

        run_list = client.get(
            f"/api/internal/agent-groups/{group.id}/coordination-runs",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert run_list.status_code == 200
        assert any(item["coordination_run_id"] == "run-read-1" for item in run_list.json())

        run_detail = client.get(
            "/api/internal/coordination-runs/run-read-1",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert run_detail.status_code == 200
        body = run_detail.json()
        assert body["coordination_run_id"] == "run-read-1"
        assert body["group_id"] == group.id
        assert body["latest_round_index"] >= 1
        assert body["status"] in {"running", "done", "failed", "blocked"}
    finally:
        cleanup()


def test_coordination_run_status_updates_to_failed_on_terminal_failure(monkeypatch):
    client, db, group, leader, assignee, _outsider_agent, _admin, _leader_owner, _direct_member_user, _member_agent_owner, _outsider_user, _state, _set_user, _deps, cleanup = _build_client_with_overrides(monkeypatch)
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
        create = client.post(
            "/api/internal/agent-delegations",
            json={
                "group_id": group.id,
                "leader_agent_id": leader.id,
                "assignee_agent_id": assignee.id,
                "objective": "run failure check",
                "visibility": "leader_only",
                "skill_name": "review",
                "coordination_run_id": "run-failed-1",
                "round_index": 1,
            },
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert create.status_code == 200
        run_row = db.query(AgentCoordinationRun).filter(AgentCoordinationRun.coordination_run_id == "run-failed-1").first()
        assert run_row is not None
        assert run_row.status == "failed"
        assert run_row.completed_at is not None
    finally:
        cleanup()
