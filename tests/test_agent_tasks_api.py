from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.agent_tasks as tasks_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    admin_user = User(username="admin", password_hash=hash_password("pw"), role="admin", is_active=True)
    leader_owner = User(username="leader-owner", password_hash=hash_password("pw"), role="viewer", is_active=True)
    participant_user = User(username="participant", password_hash=hash_password("pw"), role="viewer", is_active=True)
    outsider_user = User(username="outsider", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, leader_owner, participant_user, outsider_user])
    db.commit()
    for u in [admin_user, leader_owner, participant_user, outsider_user]:
        db.refresh(u)

    leader_agent = Agent(
        name="Leader Agent",
        description="leader",
        owner_user_id=leader_owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-leader.git",
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
    specialist_agent = Agent(
        name="Specialist Agent",
        description="specialist",
        owner_user_id=leader_owner.id,
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
    outsider_agent = Agent(
        name="Outsider Agent",
        description="outsider",
        owner_user_id=outsider_user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-outsider.git",
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
        agent_type="specialist",
    )
    db.add_all([leader_agent, specialist_agent, outsider_agent])
    db.commit()
    for a in [leader_agent, specialist_agent, outsider_agent]:
        db.refresh(a)

    group = AgentGroupRepository(db).create(name="Task Group", leader_agent_id=leader_agent.id, created_by_user_id=leader_owner.id)
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=leader_agent.id, role="leader")
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="agent", user_id=None, agent_id=specialist_agent.id, role="member")
    AgentGroupMemberRepository(db).create(group_id=group.id, member_type="user", user_id=participant_user.id, agent_id=None, role="member")

    state = {"user": SimpleNamespace(id=admin_user.id, role=admin_user.role, username=admin_user.username, nickname=admin_user.username)}

    def _override_user():
        return state["user"]

    def _override_db():
        yield db

    app.dependency_overrides[tasks_api.get_current_user] = _override_user
    app.dependency_overrides[tasks_api.get_db] = _override_db

    def _set_user(user_obj):
        state["user"] = SimpleNamespace(id=user_obj.id, role=user_obj.role, username=user_obj.username, nickname=user_obj.username)

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, group, leader_agent, specialist_agent, outsider_agent, admin_user, leader_owner, participant_user, outsider_user, _set_user, _cleanup


def test_post_agent_tasks_with_group_id_enforces_group_permissions():
    client, _db, group, leader_agent, specialist_agent, _outsider_agent, _admin_user, _leader_owner, _participant_user, outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(outsider_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "group_id": group.id,
                "parent_agent_id": leader_agent.id,
                "assignee_agent_id": specialist_agent.id,
                "source": "portal",
                "task_type": "group-task",
                "status": "queued",
            },
        )
        assert response.status_code == 403
    finally:
        cleanup()


def test_get_agent_tasks_with_group_id_rejects_outsider():
    client, db, group, leader_agent, specialist_agent, _outsider_agent, _admin_user, _leader_owner, _participant_user, outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        task = AgentTask(
            group_id=group.id,
            parent_agent_id=leader_agent.id,
            assignee_agent_id=specialist_agent.id,
            source="portal",
            task_type="group-task",
            status="queued",
        )
        db.add(task)
        db.commit()

        set_user(outsider_user)
        response = client.get(f"/api/agent-tasks?group_id={group.id}")
        assert response.status_code == 403
    finally:
        cleanup()


def test_get_agent_tasks_without_group_id_is_admin_only():
    client, _db, _group, _leader_agent, _specialist_agent, _outsider_agent, _admin_user, _leader_owner, participant_user, _outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(participant_user)
        response = client.get("/api/agent-tasks")
        assert response.status_code == 403
        assert response.json()["detail"] == "Only admin can list all tasks"
    finally:
        cleanup()


def test_dispatch_endpoint_enforces_group_manage_permission(monkeypatch):
    client, db, group, leader_agent, specialist_agent, _outsider_agent, _admin_user, leader_owner, participant_user, _outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        import app.api.agent_tasks as tasks_api

        task = AgentTask(
            group_id=group.id,
            parent_agent_id=leader_agent.id,
            assignee_agent_id=specialist_agent.id,
            source="portal",
            task_type="group-task",
            input_payload_json='{"k": "v"}',
            status="queued",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        set_user(participant_user)
        forbidden = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert forbidden.status_code == 403

        scheduled = []
        monkeypatch.setattr(tasks_api.task_dispatcher_service, "dispatch_task_in_background", lambda task_id: scheduled.append(task_id))

        set_user(leader_owner)
        allowed = client.post(f"/api/agent-tasks/{task.id}/dispatch")
        assert allowed.status_code == 202
        assert allowed.json()["accepted"] is True
        assert scheduled == [task.id]
    finally:
        cleanup()


def test_agent_tasks_by_agent_is_admin_or_owner_only():
    client, _db, _group, _leader_agent, _specialist_agent, outsider_agent, _admin_user, _leader_owner, participant_user, _outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(participant_user)
        forbidden = client.get(f"/api/agents/{outsider_agent.id}/tasks")
        assert forbidden.status_code == 403
    finally:
        cleanup()


def test_non_admin_cannot_create_task_for_other_users_assignee_agent():
    client, _db, _group, leader_agent, _specialist_agent, outsider_agent, _admin_user, _leader_owner, _participant_user, outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(outsider_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "parent_agent_id": outsider_agent.id,
                "assignee_agent_id": leader_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Forbidden"
    finally:
        cleanup()


def test_non_admin_cannot_create_task_with_parent_agent_they_do_not_own():
    client, _db, _group, leader_agent, _specialist_agent, outsider_agent, _admin_user, _leader_owner, _participant_user, outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(outsider_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "parent_agent_id": leader_agent.id,
                "assignee_agent_id": outsider_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Forbidden"
    finally:
        cleanup()


def test_owner_can_create_non_group_task_for_owned_agents():
    client, _db, _group, leader_agent, specialist_agent, _outsider_agent, _admin_user, leader_owner, _participant_user, _outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(leader_owner)
        response = client.post(
            "/api/agent-tasks",
            json={
                "parent_agent_id": leader_agent.id,
                "assignee_agent_id": specialist_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 200
        assert response.json()["assignee_agent_id"] == specialist_agent.id
        assert response.json()["owner_user_id"] == leader_owner.id
        assert response.json()["created_by_user_id"] == leader_owner.id
    finally:
        cleanup()


def test_get_my_tasks_filters_to_visible_scope():
    client, db, group, leader_agent, specialist_agent, outsider_agent, _admin_user, leader_owner, participant_user, _outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        owned_task = AgentTask(
            assignee_agent_id=specialist_agent.id,
            owner_user_id=leader_owner.id,
            created_by_user_id=None,
            source="portal",
            task_type="owned",
            status="queued",
        )
        created_task = AgentTask(
            assignee_agent_id=outsider_agent.id,
            owner_user_id=outsider_agent.owner_user_id,
            created_by_user_id=participant_user.id,
            source="portal",
            task_type="created",
            status="queued",
        )
        group_visible_task = AgentTask(
            group_id=group.id,
            parent_agent_id=leader_agent.id,
            assignee_agent_id=specialist_agent.id,
            owner_user_id=leader_owner.id,
            source="portal",
            task_type="group-visible",
            status="queued",
        )
        outsider_task = AgentTask(
            assignee_agent_id=outsider_agent.id,
            owner_user_id=outsider_agent.owner_user_id,
            source="portal",
            task_type="outsider",
            status="queued",
        )
        db.add_all([owned_task, created_task, group_visible_task, outsider_task])
        db.commit()

        set_user(participant_user)
        response = client.get("/api/my/tasks")
        assert response.status_code == 200
        task_types = {item["task_type"] for item in response.json()}
        assert "created" in task_types
        assert "group-visible" in task_types
        assert "outsider" not in task_types
    finally:
        cleanup()


def test_get_agent_task_detail_visibility_rules():
    client, db, group, leader_agent, specialist_agent, outsider_agent, _admin_user, _leader_owner, participant_user, outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        visible_task = AgentTask(
            group_id=group.id,
            parent_agent_id=leader_agent.id,
            assignee_agent_id=specialist_agent.id,
            owner_user_id=specialist_agent.owner_user_id,
            source="portal",
            task_type="group-visible",
            status="queued",
        )
        hidden_task = AgentTask(
            assignee_agent_id=outsider_agent.id,
            owner_user_id=outsider_user.id,
            source="portal",
            task_type="hidden",
            status="queued",
        )
        db.add_all([visible_task, hidden_task])
        db.commit()
        db.refresh(visible_task)
        db.refresh(hidden_task)

        set_user(participant_user)
        allowed = client.get(f"/api/agent-tasks/{visible_task.id}")
        assert allowed.status_code == 200

        denied = client.get(f"/api/agent-tasks/{hidden_task.id}")
        assert denied.status_code == 404
    finally:
        cleanup()


def test_admin_can_create_non_group_task_for_any_agent():
    client, _db, _group, leader_agent, _specialist_agent, outsider_agent, admin_user, _leader_owner, _participant_user, _outsider_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(admin_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "parent_agent_id": leader_agent.id,
                "assignee_agent_id": outsider_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 200
    finally:
        cleanup()
