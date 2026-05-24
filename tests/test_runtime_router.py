from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
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


def test_resolve_binding_decision_returns_runtime_profile_context():
    db, agent = _build_db()
    try:
        profile = RuntimeProfile(
            owner_user_id=agent.owner_user_id,
            name="Router profile",
            config_json='{"llm":{"provider":"openai","model":"gpt-5-mini","tool_loop":{"one_tool_per_turn":true}}}',
            revision=3,
            is_default=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.runtime_profile_id = profile.id
        db.add(agent)
        db.commit()

        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-runtime",
            enabled=True,
        )
        decision = RuntimeRouterService().resolve_binding_decision("github", "acct-runtime", db)
        assert decision.runtime_profile_id == profile.id
        assert decision.runtime_profile_context is not None
        assert decision.runtime_profile_context.runtime_profile_id == profile.id
        assert decision.runtime_profile_context.config["llm"]["provider"] == "openai"
    finally:
        db.close()


def test_resolve_binding_decision_without_runtime_profile_returns_no_profile_context():
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
        assert decision.runtime_profile_id is None
        assert decision.runtime_profile_context is None
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
