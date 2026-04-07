import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AuditLog, User
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.agent_groups as groups_api

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    user_member = User(username="member-user", password_hash=hash_password("pw"), role="viewer", is_active=True)
    outsider = User(username="outsider-user", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(owner)
    db.add(user_member)
    db.add(outsider)
    db.commit()
    db.refresh(owner)
    db.refresh(user_member)
    db.refresh(outsider)

    leader_agent = Agent(
        name="Leader Agent",
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
    member_agent = Agent(
        name="Member Agent",
        description="member",
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
        deployment_name="dep-member",
        service_name="svc-member",
        pvc_name="pvc-member",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(leader_agent)
    db.add(member_agent)
    db.commit()
    db.refresh(leader_agent)
    db.refresh(member_agent)

    state = {"user": SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname="Owner")}

    def _override_user():
        return state["user"]

    def _override_db():
        yield db

    app.dependency_overrides[groups_api.get_current_user] = _override_user
    app.dependency_overrides[groups_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user):
        state["user"] = SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)

    return TestClient(app), leader_agent, member_agent, user_member, outsider, _set_user, _cleanup


def _create_group(client: TestClient, leader_agent_id: str):
    response = client.post(
        "/api/agent-groups",
        json={
            "name": "Control Group",
            "leader_agent_id": leader_agent_id,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_add_user_member():
    client, leader_agent, _member_agent, user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "user", "user_id": user_member.id, "role": "member"},
        )
        assert add_resp.status_code == 200
        body = add_resp.json()
        assert body["member_type"] == "user"
        assert body["user_id"] == user_member.id
    finally:
        cleanup()


def test_add_agent_member():
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": member_agent.id, "role": "specialist"},
        )
        assert add_resp.status_code == 200
        body = add_resp.json()
        assert body["member_type"] == "agent"
        assert body["agent_id"] == member_agent.id
        assert body["role"] == "specialist"
    finally:
        cleanup()


def test_remove_non_leader_member():
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": member_agent.id, "role": "member"},
        )
        assert add_resp.status_code == 200
        member_id = add_resp.json()["id"]

        remove_resp = client.delete(f"/api/agent-groups/{group['id']}/members/{member_id}")
        assert remove_resp.status_code == 200
        assert remove_resp.json()["ok"] is True
    finally:
        cleanup()


def test_remove_leader_member_is_rejected():
    client, leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)
        leader_member = next(item for item in group["members"] if item["role"] == "leader")

        remove_resp = client.delete(f"/api/agent-groups/{group['id']}/members/{leader_member['id']}")
        assert remove_resp.status_code == 409
        assert remove_resp.json()["detail"] == "Cannot remove current group leader member"
    finally:
        cleanup()


def test_create_group_rolls_back_when_member_write_fails(monkeypatch):
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.repositories.agent_group_member_repo import AgentGroupMemberRepository

        original_create_no_commit = AgentGroupMemberRepository.create_no_commit

        def _boom_after_leader(self, **kwargs):
            role = kwargs.get("role")
            if role != "leader":
                raise RuntimeError("simulated member insert failure")
            return original_create_no_commit(self, **kwargs)

        monkeypatch.setattr(AgentGroupMemberRepository, "create_no_commit", _boom_after_leader)

        response = client.post(
            "/api/agent-groups",
            json={
                "name": "Broken Group",
                "leader_agent_id": leader_agent.id,
                "member_agent_ids": [member_agent.id],
            },
        )
        assert response.status_code == 400

        list_resp = client.get("/api/agent-groups")
        assert list_resp.status_code == 200
        assert list_resp.json() == []
    finally:
        cleanup()


def test_group_list_and_detail_enforce_view_permissions():
    client, leader_agent, _member_agent, user_member, outsider, set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "user", "user_id": user_member.id, "role": "member"},
        )

        set_user(user_member)
        member_list = client.get("/api/agent-groups")
        assert member_list.status_code == 200
        assert len(member_list.json()) == 1
        member_detail = client.get(f"/api/agent-groups/{group['id']}")
        assert member_detail.status_code == 200

        set_user(outsider)
        outsider_list = client.get("/api/agent-groups")
        assert outsider_list.status_code == 200
        assert outsider_list.json() == []
        outsider_detail = client.get(f"/api/agent-groups/{group['id']}")
        assert outsider_detail.status_code == 403
    finally:
        cleanup()


def test_participant_cannot_add_or_remove_members():
    client, leader_agent, member_agent, user_member, _outsider, set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)
        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": member_agent.id, "role": "member"},
        )
        member_id = add_resp.json()["id"]

        client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "user", "user_id": user_member.id, "role": "member"},
        )
        set_user(user_member)
        participant_add = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": leader_agent.id, "role": "member"},
        )
        assert participant_add.status_code == 403

        participant_remove = client.delete(f"/api/agent-groups/{group['id']}/members/{member_id}")
        assert participant_remove.status_code == 403
    finally:
        cleanup()


def test_group_leader_must_be_workspace_agent():
    client, _leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.main import app
        import app.api.agent_groups as groups_api

        db_gen = app.dependency_overrides[groups_api.get_db]()
        db = next(db_gen)
        non_workspace = Agent(
            name="Non Workspace",
            description="non-workspace",
            owner_user_id=1,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-non-workspace.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-non-workspace",
            service_name="svc-non-workspace",
            pvc_name="pvc-non-workspace",
            endpoint_path="/",
            agent_type="expert",
        )
        db.add(non_workspace)
        db.commit()
        db.refresh(non_workspace)

        response = client.post(
            "/api/agent-groups",
            json={
                "name": "Invalid Leader Group",
                "leader_agent_id": non_workspace.id,
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Leader agent must be a workspace agent"
    finally:
        cleanup()


def test_group_create_derives_default_specialist_pool_from_specialist_members():
    client, leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.main import app
        import app.api.agent_groups as groups_api

        db_gen = app.dependency_overrides[groups_api.get_db]()
        db = next(db_gen)
        specialist = Agent(
            name="Specialist",
            description="specialist",
            owner_user_id=leader_agent.owner_user_id,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-specialist.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-specialist",
            service_name="svc-specialist",
            pvc_name="pvc-specialist",
            endpoint_path="/",
            agent_type="specialist",
        )
        db.add(specialist)
        db.commit()
        db.refresh(specialist)

        response = client.post(
            "/api/agent-groups",
            json={"name": "Pool Group", "leader_agent_id": leader_agent.id, "member_agent_ids": [specialist.id]},
        )
        assert response.status_code == 200
        detail = response.json()
        assert specialist.id in detail["specialist_agent_pool_json"]
    finally:
        cleanup()


def test_explicit_specialist_pool_rejects_leader_and_workspace():
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        leader_in_pool = client.post(
            "/api/agent-groups",
            json={
                "name": "Bad Pool Group 1",
                "leader_agent_id": leader_agent.id,
                "member_agent_ids": [member_agent.id],
                "specialist_agent_ids": [leader_agent.id],
            },
        )
        assert leader_in_pool.status_code == 422

        workspace_in_pool = client.post(
            "/api/agent-groups",
            json={
                "name": "Bad Pool Group 2",
                "leader_agent_id": leader_agent.id,
                "member_agent_ids": [member_agent.id],
                "specialist_agent_ids": [member_agent.id],
            },
        )
        assert workspace_in_pool.status_code == 422
    finally:
        cleanup()


def test_manage_specialist_pool_and_task_agent_lifecycle():
    client, leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.main import app
        import app.api.agent_groups as groups_api
        import app.api.agents as agents_api

        db_gen = app.dependency_overrides[groups_api.get_db]()
        db = next(db_gen)

        specialist_template = Agent(
            name="Template Specialist",
            description="template",
            owner_user_id=leader_agent.owner_user_id,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-template.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-template",
            service_name="svc-template",
            pvc_name="pvc-template",
            endpoint_path="/",
            agent_type="specialist",
        )
        db.add(specialist_template)
        db.commit()
        db.refresh(specialist_template)

        group = _create_group(client, leader_agent.id)
        add_member = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": specialist_template.id, "role": "member"},
        )
        assert add_member.status_code == 200
        update_pool = client.put(
            f"/api/agent-groups/{group['id']}/specialist-pool",
            json={"specialist_agent_ids": [specialist_template.id]},
        )
        assert update_pool.status_code == 200
        pool_audit = db.query(AuditLog).filter(AuditLog.action == "update_specialist_pool", AuditLog.target_id == group["id"]).first()
        assert pool_audit is not None
        assert json.loads(pool_audit.details_json)["specialist_pool_size"] == 1

        # mock runtime create/delete path
        agents_api.k8s_service.create_agent_runtime = lambda _agent: SimpleNamespace(status="running", message=None)
        agents_api.k8s_service.delete_agent_runtime = lambda _agent, destroy_data=False: SimpleNamespace(status="deleted", message=None)
        import app.services.agent_group_service as group_service_module
        group_service_module.K8sService.create_agent_runtime = lambda _self, _agent: SimpleNamespace(status="running", message=None)
        group_service_module.K8sService.delete_agent_runtime = lambda _self, _agent, destroy_data=False: SimpleNamespace(status="deleted", message=None)

        create_task_agent = client.post(
            f"/api/agent-groups/{group['id']}/task-agents",
            json={"name": "Ephemeral Task Agent", "template_agent_id": specialist_template.id, "scope_label": "s1", "cleanup_policy": "on_done"},
        )
        assert create_task_agent.status_code == 200
        created = create_task_agent.json()
        assert created["agent_type"] == "task"
        create_audit = db.query(AuditLog).filter(AuditLog.action == "create_group_task_agent", AuditLog.target_id == created["id"]).first()
        assert create_audit is not None
        assert json.loads(create_audit.details_json)["group_id"] == group["id"]

        pool_after_create = client.get(f"/api/agent-groups/{group['id']}/specialist-pool")
        assert created["id"] in pool_after_create.json()["specialist_agent_ids"]

        delete_task_agent = client.delete(f"/api/agent-groups/{group['id']}/task-agents/{created['id']}")
        assert delete_task_agent.status_code == 200
        delete_audit = db.query(AuditLog).filter(AuditLog.action == "delete_group_task_agent", AuditLog.target_id == created["id"]).first()
        assert delete_audit is not None
        assert json.loads(delete_audit.details_json)["group_id"] == group["id"]
        pool_after_delete = client.get(f"/api/agent-groups/{group['id']}/specialist-pool")
        assert created["id"] not in pool_after_delete.json()["specialist_agent_ids"]
    finally:
        cleanup()


def test_internal_specialist_pool_requires_key_and_returns_expected_ids():
    client, leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.main import app
        import app.api.agent_groups as groups_api
        import app.deps as deps_module

        db_gen = app.dependency_overrides[groups_api.get_db]()
        db = next(db_gen)
        deps_module.settings.portal_internal_api_key = "internal-test-key"

        specialist_a = Agent(
            name="Internal Specialist A",
            description="specialist",
            owner_user_id=leader_agent.owner_user_id,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-specialist-internal-a.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-specialist-internal-a",
            service_name="svc-specialist-internal-a",
            pvc_name="pvc-specialist-internal-a",
            endpoint_path="/",
            agent_type="specialist",
        )
        specialist_b = Agent(
            name="Internal Specialist B",
            description="specialist",
            owner_user_id=leader_agent.owner_user_id,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-specialist-internal-b.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-specialist-internal-b",
            service_name="svc-specialist-internal-b",
            pvc_name="pvc-specialist-internal-b",
            endpoint_path="/",
            agent_type="specialist",
        )
        db.add(specialist_a)
        db.add(specialist_b)
        db.commit()
        db.refresh(specialist_a)
        db.refresh(specialist_b)

        group = _create_group(client, leader_agent.id)
        assert client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": specialist_a.id, "role": "member"},
        ).status_code == 200
        assert client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": specialist_b.id, "role": "member"},
        ).status_code == 200
        assert client.put(
            f"/api/agent-groups/{group['id']}/specialist-pool",
            json={"specialist_agent_ids": [specialist_b.id, specialist_a.id]},
        ).status_code == 200

        assert client.get(f"/api/internal/agent-groups/{group['id']}/specialist-pool").status_code == 401
        assert (
            client.get(
                f"/api/internal/agent-groups/{group['id']}/specialist-pool",
                headers={"X-Internal-Api-Key": "wrong"},
            ).status_code
            == 401
        )
        ok = client.get(
            f"/api/internal/agent-groups/{group['id']}/specialist-pool",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["group_id"] == group["id"]
        assert body["specialist_agent_ids"] == [specialist_b.id, specialist_a.id]
        assert [item["agent_id"] for item in body["items"]] == [specialist_b.id, specialist_a.id]
        assert body["items"][0]["name"] == specialist_b.name
        assert body["items"][0]["agent_type"] == "specialist"
        assert body["items"][0]["status"] == "running"
        assert body["items"][0]["visibility"] == "private"
    finally:
        cleanup()


def test_internal_task_agent_create_delete_requires_key_and_preserves_safeguards():
    client, leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.main import app
        import app.api.agent_groups as groups_api
        import app.api.agents as agents_api
        import app.deps as deps_module
        import app.services.agent_group_service as group_service_module
        from app.repositories.agent_group_member_repo import AgentGroupMemberRepository

        db_gen = app.dependency_overrides[groups_api.get_db]()
        db = next(db_gen)
        deps_module.settings.portal_internal_api_key = "internal-test-key"

        specialist_template = Agent(
            name="Internal Template Specialist",
            description="template",
            owner_user_id=leader_agent.owner_user_id,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-template-internal.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-template-internal",
            service_name="svc-template-internal",
            pvc_name="pvc-template-internal",
            endpoint_path="/",
            agent_type="specialist",
        )
        db.add(specialist_template)
        db.commit()
        db.refresh(specialist_template)

        group = _create_group(client, leader_agent.id)
        assert client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": specialist_template.id, "role": "member"},
        ).status_code == 200
        assert client.put(
            f"/api/agent-groups/{group['id']}/specialist-pool",
            json={"specialist_agent_ids": [specialist_template.id]},
        ).status_code == 200

        agents_api.k8s_service.create_agent_runtime = lambda _agent: SimpleNamespace(status="running", message=None)
        agents_api.k8s_service.delete_agent_runtime = lambda _agent, destroy_data=False: SimpleNamespace(status="deleted", message=None)
        group_service_module.K8sService.create_agent_runtime = lambda _self, _agent: SimpleNamespace(status="running", message=None)
        group_service_module.K8sService.delete_agent_runtime = lambda _self, _agent, destroy_data=False: SimpleNamespace(status="deleted", message=None)

        payload = {
            "leader_agent_id": group["leader_agent_id"],
            "name": "Internal Ephemeral Task Agent",
            "template_agent_id": specialist_template.id,
            "scope_label": "runtime-scope",
            "visibility": "private",
            "task_agent_cleanup_policy": "delete_on_done",
        }
        assert client.post(f"/api/internal/agent-groups/{group['id']}/task-agents", json=payload).status_code == 401
        missing_leader = client.post(
            f"/api/internal/agent-groups/{group['id']}/task-agents",
            json={k: v for k, v in payload.items() if k != "leader_agent_id"},
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert missing_leader.status_code == 422
        blank_leader = client.post(
            f"/api/internal/agent-groups/{group['id']}/task-agents",
            json={**payload, "leader_agent_id": "   "},
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert blank_leader.status_code == 422
        mismatch_leader = client.post(
            f"/api/internal/agent-groups/{group['id']}/task-agents",
            json={**payload, "leader_agent_id": specialist_template.id},
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert mismatch_leader.status_code == 409
        bad_visibility = client.post(
            f"/api/internal/agent-groups/{group['id']}/task-agents",
            json={**payload, "visibility": "INVALID"},
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert bad_visibility.status_code == 422
        bad_cleanup = client.post(
            f"/api/internal/agent-groups/{group['id']}/task-agents",
            json={**payload, "task_agent_cleanup_policy": "never"},
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert bad_cleanup.status_code == 422
        created_resp = client.post(
            f"/api/internal/agent-groups/{group['id']}/task-agents",
            json=payload,
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert created_resp.status_code == 200
        created = created_resp.json()
        assert created["template_agent_id"] == specialist_template.id
        assert created["scope_label"] == "runtime-scope"
        assert created["task_agent_cleanup_policy"] == "delete_on_done"
        assert created["source"] == "internal_api"
        assert created["group_id"] == group["id"]
        assert created["leader_agent_id"] == group["leader_agent_id"]
        assert created["agent_type"] == "task"
        create_audit = db.query(AuditLog).filter(AuditLog.action == "create_group_task_agent", AuditLog.target_id == created["id"]).first()
        assert create_audit is not None
        create_details = json.loads(create_audit.details_json)
        assert create_details["template_agent_id"] == specialist_template.id
        assert create_details["scope_label"] == "runtime-scope"
        assert create_details["task_agent_cleanup_policy"] == "delete_on_done"
        assert create_details["visibility"] == "private"
        assert create_details["source"] == "internal_api"
        member = AgentGroupMemberRepository(db).get_by_group_and_agent(group["id"], created["id"])
        assert member is not None
        pool = client.get(
            f"/api/internal/agent-groups/{group['id']}/specialist-pool",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        ).json()
        assert created["id"] in pool["specialist_agent_ids"]

        assert client.delete(f"/api/internal/agent-groups/{group['id']}/task-agents/{created['id']}").status_code == 401
        assert (
            client.delete(
                f"/api/internal/agent-groups/{group['id']}/task-agents/{group['leader_agent_id']}",
                headers={"X-Internal-Api-Key": "internal-test-key"},
            ).status_code
            == 409
        )

        deleted = client.delete(
            f"/api/internal/agent-groups/{group['id']}/task-agents/{created['id']}",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}
        delete_audit = db.query(AuditLog).filter(AuditLog.action == "delete_group_task_agent", AuditLog.target_id == created["id"]).first()
        assert delete_audit is not None
        delete_details = json.loads(delete_audit.details_json)
        assert delete_details["source"] == "internal_api"
        assert delete_details["destroyed_runtime"] is True
        assert delete_details["previous_scope_label"] == "runtime-scope"
        assert AgentGroupMemberRepository(db).get_by_group_and_agent(group["id"], created["id"]) is None
        pool_after = client.get(
            f"/api/internal/agent-groups/{group['id']}/specialist-pool",
            headers={"X-Internal-Api-Key": "internal-test-key"},
        ).json()
        assert created["id"] not in pool_after["specialist_agent_ids"]

        public_create = client.post(
            f"/api/agent-groups/{group['id']}/task-agents",
            json={
                "name": "Public Ephemeral Task Agent",
                "template_agent_id": specialist_template.id,
                "scope_label": "public-scope",
                "cleanup_policy": "on_done",
            },
        )
        assert public_create.status_code == 200
        public_body = public_create.json()
        assert "template_agent_id" not in public_body
        assert "source" not in public_body
    finally:
        cleanup()
