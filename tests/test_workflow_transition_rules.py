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
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="Workflow Agent",
        description="workflow",
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
        deployment_name="dep-wf",
        service_name="svc-wf",
        pvc_name="pvc-wf",
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

    app.dependency_overrides[rules_api.get_current_user] = _override_user
    app.dependency_overrides[rules_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), agent, _cleanup


def test_create_list_and_get_workflow_transition_rules():
    client, agent, cleanup = _build_client_with_overrides()
    try:
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
                "enabled": True,
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["target_agent_id"] == agent.id
        assert created["system_type"] == "jira"

        list_resp = client.get("/api/workflow-transition-rules")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) == 1
        assert items[0]["project_key"] == "EFP"

        get_resp = client.get(f"/api/workflow-transition-rules/{created['id']}")
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["id"] == created["id"]
        assert fetched["trigger_status"] == "In Review"
    finally:
        cleanup()
