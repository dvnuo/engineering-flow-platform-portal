from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.services.auth_service import hash_password


def _build_db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _create_agent(db: Session) -> Agent:
    user = User(username="binding-owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="Binding Agent",
        description="bindings",
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
        deployment_name="dep-binding",
        service_name="svc-binding",
        pvc_name="pvc-binding",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def test_create_normalizes_system_type_and_find_binding_works():
    db = _build_db()
    try:
        agent = _create_agent(db)
        repo = AgentIdentityBindingRepository(db)

        binding = repo.create(
            agent_id=agent.id,
            system_type=" GitHub ",
            external_account_id="acct-1",
            enabled=True,
        )

        assert binding.system_type == "github"

        found = repo.find_binding("github", "acct-1")
        assert found is not None
        assert found.id == binding.id
    finally:
        db.close()


def test_save_normalizes_modified_system_type():
    db = _build_db()
    try:
        agent = _create_agent(db)
        repo = AgentIdentityBindingRepository(db)
        binding = repo.create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-2",
            enabled=True,
        )

        binding.system_type = " Jira "
        saved = repo.save(binding)

        assert saved.system_type == "jira"
    finally:
        db.close()
