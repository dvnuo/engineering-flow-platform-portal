from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.services.auth_service import hash_password


def _build_client():
    from app.main import app
    from app.db import get_db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="Export Agent",
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
        deployment_name="dep-export",
        service_name="svc-export",
        pvc_name="pvc-export",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    AgentIdentityBindingRepository(db).create(
        agent_id=agent.id,
        system_type="jira",
        external_account_id="jira-acct-1",
        username="jira.user",
        scope_json='{"projects":["EFP"]}',
        enabled=True,
    )
    AgentIdentityBindingRepository(db).create(
        agent_id=agent.id,
        system_type="jira",
        external_account_id="jira-acct-2",
        username="jira.disabled",
        scope_json='{"projects":["LEGACY"]}',
        enabled=False,
    )
    WorkflowTransitionRuleRepository(db).create(
        system_type="jira",
        project_key="EFP",
        issue_type="Story",
        trigger_status="In Review",
        assignee_binding="jira-acct-1",
        target_agent_id=agent.id,
        skill_name="workflow-review",
        success_transition="Done",
        failure_transition="Needs Changes",
        enabled=True,
        config_json='{"strict": true}',
    )

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), _cleanup


def test_internal_exports_list_workflow_rules_and_bindings_with_filters():
    client, cleanup = _build_client()
    try:
        rules_resp = client.get("/api/internal/workflow-transition-rules?system_type=jira&enabled=true&project_key=EFP")
        assert rules_resp.status_code == 200
        rules_body = rules_resp.json()
        assert isinstance(rules_body, dict)
        assert len(rules_body["items"]) == 1

        bindings_resp = client.get("/api/internal/agent-identity-bindings?system_type=jira&enabled=true")
        assert bindings_resp.status_code == 200
        bindings_body = bindings_resp.json()
        assert isinstance(bindings_body, dict)
        assert len(bindings_body["items"]) == 1
        assert bindings_body["items"][0]["external_account_id"] == "jira-acct-1"

        no_subs = client.get("/api/internal/external-event-subscriptions")
        assert no_subs.status_code == 404
    finally:
        cleanup()
