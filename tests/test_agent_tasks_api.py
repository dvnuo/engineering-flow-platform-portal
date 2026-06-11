from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.agent_tasks as tasks_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    admin_user = User(username="admin", password_hash=hash_password("pw"), role="admin", is_active=True)
    owner_user = User(username="owner", password_hash=hash_password("pw"), role="viewer", is_active=True)
    other_user = User(username="other", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, owner_user, other_user])
    db.commit()
    for user in [admin_user, owner_user, other_user]:
        db.refresh(user)

    owned_agent = Agent(
        name="Owned Agent",
        description="owned",
        owner_user_id=owner_user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-owned.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-owned",
        service_name="svc-owned",
        pvc_name="pvc-owned",
        endpoint_path="/",
        agent_type="workspace",
    )
    other_agent = Agent(
        name="Other Agent",
        description="other",
        owner_user_id=other_user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-other.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-other",
        service_name="svc-other",
        pvc_name="pvc-other",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add_all([owned_agent, other_agent])
    db.commit()
    for agent in [owned_agent, other_agent]:
        db.refresh(agent)

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

    return TestClient(app), db, owned_agent, other_agent, admin_user, owner_user, other_user, _set_user, _cleanup


def test_get_agent_tasks_is_admin_only():
    client, _db, _owned_agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(owner_user)
        response = client.get("/api/agent-tasks")
        assert response.status_code == 403
        assert response.json()["detail"] == "Only admin can list all tasks"
    finally:
        cleanup()


def test_non_admin_cannot_create_task_for_other_users_assignee_agent():
    client, _db, _owned_agent, other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(owner_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "assignee_agent_id": other_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Forbidden"
    finally:
        cleanup()


def test_owner_can_create_task_for_owned_agent():
    client, _db, owned_agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(owner_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "assignee_agent_id": owned_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 200
        assert response.json()["assignee_agent_id"] == owned_agent.id
        assert response.json()["owner_user_id"] == owner_user.id
        assert response.json()["created_by_user_id"] == owner_user.id
    finally:
        cleanup()


def test_get_my_tasks_lists_all_tasks_with_owner_management_flags():
    client, db, owned_agent, other_agent, _admin_user, owner_user, other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        owned_task = AgentTask(
            assignee_agent_id=owned_agent.id,
            owner_user_id=owner_user.id,
            created_by_user_id=None,
            source="portal",
            task_type="owned",
            status="queued",
        )
        created_task = AgentTask(
            assignee_agent_id=other_agent.id,
            owner_user_id=other_user.id,
            created_by_user_id=owner_user.id,
            source="portal",
            task_type="created",
            status="queued",
        )
        hidden_task = AgentTask(
            assignee_agent_id=other_agent.id,
            owner_user_id=other_user.id,
            source="portal",
            task_type="hidden",
            status="queued",
        )
        db.add_all([owned_task, created_task, hidden_task])
        db.commit()

        set_user(owner_user)
        response = client.get("/api/my/tasks")
        assert response.status_code == 200
        by_type = {item["task_type"]: item for item in response.json()}
        assert set(by_type) == {"owned", "created", "hidden"}
        assert by_type["owned"]["owner_display_name"] == owner_user.username
        assert by_type["owned"]["can_manage"] is True
        assert by_type["created"]["owner_display_name"] == other_user.username
        assert by_type["created"]["can_manage"] is False
        assert by_type["hidden"]["owner_display_name"] == other_user.username
        assert by_type["hidden"]["can_manage"] is False

        paged = client.get("/api/my/tasks?limit=2&offset=0")
        assert paged.status_code == 200
        assert len(paged.json()) == 2
    finally:
        cleanup()


def test_get_agent_task_detail_visibility_rules():
    client, db, owned_agent, other_agent, _admin_user, owner_user, other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        visible_task = AgentTask(
            assignee_agent_id=owned_agent.id,
            owner_user_id=owner_user.id,
            source="portal",
            task_type="visible",
            status="queued",
        )
        hidden_task = AgentTask(
            assignee_agent_id=other_agent.id,
            owner_user_id=other_user.id,
            source="portal",
            task_type="hidden",
            status="queued",
        )
        db.add_all([visible_task, hidden_task])
        db.commit()
        db.refresh(visible_task)
        db.refresh(hidden_task)

        set_user(owner_user)
        allowed = client.get(f"/api/agent-tasks/{visible_task.id}")
        assert allowed.status_code == 200

        visible_to_all = client.get(f"/api/agent-tasks/{hidden_task.id}")
        assert visible_to_all.status_code == 200
        assert visible_to_all.json()["can_manage"] is False
    finally:
        cleanup()


def test_admin_can_create_task_for_any_agent_but_not_manage_without_ownership():
    client, _db, _owned_agent, other_agent, admin_user, _owner_user, other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(admin_user)
        response = client.post(
            "/api/agent-tasks",
            json={
                "assignee_agent_id": other_agent.id,
                "source": "portal",
                "task_type": "review",
                "status": "queued",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["owner_user_id"] == other_user.id
        assert body["can_manage"] is False

        cancel_resp = client.post(f"/api/agent-tasks/{body['id']}/cancel")
        assert cancel_resp.status_code == 403
        assert cancel_resp.json()["detail"] == "Not allowed to cancel task"
    finally:
        cleanup()
