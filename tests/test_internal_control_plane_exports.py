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
        description="export",
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
        system_type="github",
        external_account_id="github-acct-1",
        username="octocat",
        scope_json='{"repos":["engineering-flow-platform-portal"]}',
        enabled=True,
    )
    AgentIdentityBindingRepository(db).create(
        agent_id=agent.id,
        system_type="jira",
        external_account_id="jira-acct-2",
        username="jira.disabled",
        scope_json='{"projects":["EFP-LEGACY"]}',
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
    import app.deps as deps_module

    client, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        rules_resp = client.get(
            "/api/internal/workflow-transition-rules?system_type=jira&enabled=true&project_key=EFP",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert rules_resp.status_code == 200
        rules = rules_resp.json()
        assert len(rules) == 1
        assert rules[0]["system_type"] == "jira"
        assert rules[0]["provider_type"] == "jira"
        assert rules[0]["is_enabled"] is True
        assert rules[0]["enabled"] is True
        assert rules[0]["project_key"] == "EFP"
        assert rules[0]["project_keys"] == ["EFP"]
        assert rules[0]["trigger_status"] == "In Review"
        assert rules[0]["trigger_statuses"] == ["In Review"]

        bindings_resp = client.get(
            "/api/internal/agent-identity-bindings?system_type=jira&enabled=true",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert bindings_resp.status_code == 200
        bindings = bindings_resp.json()
        assert len(bindings) == 1
        assert bindings[0]["system_type"] == "jira"
        assert bindings[0]["provider_type"] == "jira"
        assert bindings[0]["external_account_id"] == "jira-acct-1"
        assert bindings[0]["scope"] == '{"projects":["EFP"]}'
        assert bindings[0]["scope_json"] == '{"projects":["EFP"]}'

        jira_disabled_resp = client.get(
            "/api/internal/agent-identity-bindings?system_type=jira&enabled=false",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert jira_disabled_resp.status_code == 200
        jira_disabled = jira_disabled_resp.json()
        assert len(jira_disabled) == 1
        assert jira_disabled[0]["system_type"] == "jira"
        assert jira_disabled[0]["external_account_id"] == "jira-acct-2"
        assert jira_disabled[0]["enabled"] is False

        enabled_resp = client.get(
            "/api/internal/agent-identity-bindings?enabled=true",
            headers={"X-Internal-Api-Key": "internal-key"},
        )
        assert enabled_resp.status_code == 200
        enabled_bindings = enabled_resp.json()
        assert len(enabled_bindings) == 2
        assert {item["system_type"] for item in enabled_bindings} == {"jira", "github"}
        assert {item["external_account_id"] for item in enabled_bindings} == {"jira-acct-1", "github-acct-1"}
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()


def test_internal_exports_require_internal_api_key():
    import app.deps as deps_module

    client, cleanup = _build_client()
    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "internal-key"
    try:
        missing = client.get("/api/internal/workflow-transition-rules")
        wrong = client.get(
            "/api/internal/agent-identity-bindings",
            headers={"X-Internal-Api-Key": "wrong"},
        )
        assert missing.status_code == 401
        assert wrong.status_code == 401
    finally:
        deps_module.settings.portal_internal_api_key = original
        cleanup()
