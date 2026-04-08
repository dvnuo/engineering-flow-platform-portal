from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.workflow_transition_rules as rules_api

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    admin_user = User(username="admin", password_hash=hash_password("pw"), role="admin", is_active=True)
    owner_user = User(username="owner", password_hash=hash_password("pw"), role="viewer", is_active=True)
    other_user = User(username="other", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, owner_user, other_user])
    db.commit()
    for item in [admin_user, owner_user, other_user]:
        db.refresh(item)

    agent = Agent(
        name="Workflow Agent",
        description="workflow",
        owner_user_id=owner_user.id,
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
        deployment_name="dep-wf",
        service_name="svc-wf",
        pvc_name="pvc-wf",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    other_agent = Agent(
        name="Other Workflow Agent",
        description="workflow-other",
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
        deployment_name="dep-wf-other",
        service_name="svc-wf-other",
        pvc_name="pvc-wf-other",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(other_agent)
    db.commit()
    db.refresh(agent)
    db.refresh(other_agent)

    state = {"user": admin_user}

    def _override_user():
        user = state["user"]
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[rules_api.get_current_user] = _override_user
    app.dependency_overrides[rules_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user_obj):
        state["user"] = user_obj

    return TestClient(app), agent, other_agent, admin_user, owner_user, other_user, _set_user, _cleanup


def test_create_list_and_get_workflow_transition_rules():
    client, agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(owner_user)
        create_resp = client.post(
            "/api/workflow-transition-rules",
            json={
                "system_type": "jira",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "assignee_binding": "user-1",
                "target_agent_id": agent.id,
                "skill_name": "workflow-review",
                "success_transition": "Done",
                "failure_transition": "Needs Changes",
                "success_reassign_to": "reporter",
                "failure_reassign_to": "requester",
                "config_json": '{"strict": true, "max_reviews": 2}',
                "enabled": True,
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["target_agent_id"] == agent.id
        assert created["system_type"] == "jira"
        assert created["config_json"] == '{"max_reviews": 2, "strict": true}'

        set_user(_admin_user)
        list_resp = client.get("/api/workflow-transition-rules")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) == 1
        assert items[0]["project_key"] == "EFP"

        set_user(owner_user)
        get_resp = client.get(f"/api/workflow-transition-rules/{created['id']}")
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["id"] == created["id"]
        assert fetched["trigger_status"] == "In Review"
    finally:
        cleanup()


def test_create_workflow_transition_rule_rejects_malformed_config_json():
    client, agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(owner_user)
        response = client.post(
            "/api/workflow-transition-rules",
            json={
                "system_type": "jira",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "target_agent_id": agent.id,
                "config_json": "{bad-json",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "config_json must be valid JSON"
    finally:
        cleanup()


def test_create_workflow_transition_rule_rejects_non_object_config_json():
    client, agent, _other_agent, _admin_user, owner_user, _other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(owner_user)
        invalid_values = ["[]", '"x"', "42", "true", "null"]
        for value in invalid_values:
            response = client.post(
                "/api/workflow-transition-rules",
                json={
                    "system_type": "jira",
                    "project_key": "EFP",
                    "issue_type": "Story",
                    "trigger_status": "In Review",
                    "target_agent_id": agent.id,
                    "config_json": value,
                },
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "config_json must decode to a JSON object"
    finally:
        cleanup()


def test_workflow_transition_rule_authorization():
    client, agent, other_agent, admin_user, owner_user, other_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(other_user)
        forbidden_create = client.post(
            "/api/workflow-transition-rules",
            json={
                "system_type": "jira",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "target_agent_id": agent.id,
            },
        )
        assert forbidden_create.status_code == 403

        set_user(owner_user)
        allowed_create = client.post(
            "/api/workflow-transition-rules",
            json={
                "system_type": "jira",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "target_agent_id": agent.id,
            },
        )
        assert allowed_create.status_code == 200
        created_id = allowed_create.json()["id"]

        set_user(other_user)
        forbidden_list = client.get("/api/workflow-transition-rules")
        assert forbidden_list.status_code == 403

        set_user(owner_user)
        own_get = client.get(f"/api/workflow-transition-rules/{created_id}")
        assert own_get.status_code == 200

        set_user(other_user)
        forbidden_get = client.get(f"/api/workflow-transition-rules/{created_id}")
        assert forbidden_get.status_code == 403

        set_user(admin_user)
        admin_list = client.get("/api/workflow-transition-rules")
        assert admin_list.status_code == 200
        admin_get = client.get(f"/api/workflow-transition-rules/{created_id}")
        assert admin_get.status_code == 200

        admin_create_other = client.post(
            "/api/workflow-transition-rules",
            json={
                "system_type": "jira",
                "project_key": "OPS",
                "issue_type": "Task",
                "trigger_status": "Todo",
                "target_agent_id": other_agent.id,
            },
        )
        assert admin_create_other.status_code == 200
    finally:
        cleanup()
