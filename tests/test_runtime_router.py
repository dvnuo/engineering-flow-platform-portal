from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, CapabilityProfile, User
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.services.auth_service import hash_password
from app.services.runtime_router import RuntimeRouterService


def _build_db() -> tuple[Session, Agent]:
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
        name="Router Agent",
        description="router",
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
        deployment_name="dep-router",
        service_name="svc-router",
        pvc_name="pvc-router",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return db, agent


def _create_agent(db: Session, user: User, agent_type: str) -> Agent:
    agent = Agent(
        name=f"Router Agent {agent_type}",
        description="router",
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
        deployment_name=f"dep-router-{agent_type}",
        service_name=f"svc-router-{agent_type}",
        pvc_name=f"pvc-router-{agent_type}",
        endpoint_path="/",
        agent_type=agent_type,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def test_resolve_binding_decision_defaults_to_sync_execution_mode():
    db, agent = _build_db()
    try:
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-sync",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision("github", "acct-sync", db)
        assert decision.execution_mode == "sync"
        assert decision.matched_agent_id == agent.id
    finally:
        db.close()


def test_resolve_binding_decision_returns_capability_context():
    db, agent = _build_db()
    try:
        profile = CapabilityProfile(
            name="cap-router",
            tool_set_json='["shell"]',
            channel_set_json='["jira_get_issue"]',
            skill_set_json='["review"]',
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["pull_request_review_requested"]',
            allowed_actions_json='["review_pull_request"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-cap",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision("github", "acct-cap", db)
        assert decision.capability_context is not None
        assert decision.capability_context.capability_profile_id == profile.id
        assert decision.capability_context.allowed_external_systems == ["github"]
        assert decision.capability_context.allowed_webhook_triggers == ["pull_request_review_requested"]
        assert decision.capability_context.allowed_actions == ["review_pull_request"]
        assert "tool:shell" in decision.capability_context.allowed_capability_ids
        assert "skill:review" in decision.capability_context.allowed_capability_ids
        assert "channel_action:jira_get_issue" in decision.capability_context.allowed_capability_ids
        assert "adapter:github:review_pull_request" in decision.capability_context.allowed_capability_ids
        assert decision.capability_context.allowed_adapter_actions == ["adapter:github:review_pull_request"]
        assert decision.capability_context.unresolved_actions == []
        assert decision.capability_context.resolved_action_mappings == {
            "review_pull_request": "adapter:github:review_pull_request"
        }
        assert decision.capability_context.runtime_capability_catalog_version is not None
        assert decision.capability_context.runtime_capability_catalog_source in {"seed_fallback", "settings_snapshot", "runtime_api"}
        assert decision.capability_context.catalog_validation_mode in {"seed_fallback", "full_snapshot"}
        assert "adapter_action" in decision.capability_context.allowed_capability_types
    finally:
        db.close()


def test_resolve_binding_decision_without_profile_returns_empty_capability_context():
    db, agent = _build_db()
    try:
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-empty",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision("github", "acct-empty", db)
        assert decision.matched_agent_id == agent.id
        assert decision.capability_context is not None
        assert decision.capability_context.capability_profile_id is None
        assert decision.capability_context.allowed_capability_ids == []
        assert decision.capability_context.allowed_external_systems == []
        assert decision.capability_context.allowed_actions == []
        assert decision.capability_context.allowed_adapter_actions == []
        assert decision.capability_context.unresolved_actions == []
        assert decision.capability_context.resolved_action_mappings == {}
    finally:
        db.close()


def test_resolve_binding_decision_for_event_uses_async_task_execution_mode():
    db, agent = _build_db()
    try:
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-event",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision_for_event("github", "acct-event", db)
        assert decision.execution_mode == "async_task"
        assert decision.matched_agent_id == agent.id
    finally:
        db.close()


def test_resolve_binding_decision_defaults_to_async_task_for_specialist_agent():
    db, _agent = _build_db()
    try:
        user = db.query(User).first()
        specialist = _create_agent(db, user, "specialist")
        AgentIdentityBindingRepository(db).create(
            agent_id=specialist.id,
            system_type="github",
            external_account_id="acct-specialist",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision("github", "acct-specialist", db)
        assert decision.execution_mode == "async_task"
        assert decision.matched_agent_id == specialist.id
    finally:
        db.close()


def test_resolve_binding_decision_defaults_to_async_task_for_task_agent():
    db, _agent = _build_db()
    try:
        user = db.query(User).first()
        task_agent = _create_agent(db, user, "task")
        AgentIdentityBindingRepository(db).create(
            agent_id=task_agent.id,
            system_type="github",
            external_account_id="acct-task",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision("github", "acct-task", db)
        assert decision.execution_mode == "async_task"
        assert decision.matched_agent_id == task_agent.id
    finally:
        db.close()
