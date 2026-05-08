from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.agent import Agent
from app.models.runtime_profile import RuntimeProfile
from app.models.runtime_profile_sync_job import RuntimeProfileSyncJob
from app.models.user import User
from app.repositories.agent_repo import AgentRepository
from app.services.runtime_profile_sync_queue_service import RuntimeProfileSyncQueueService


def _db_with_foreign_keys():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def test_agent_delete_removes_runtime_profile_sync_jobs_with_fk_enabled():
    db = _db_with_foreign_keys()
    try:
        user = User(username="owner", password_hash="x", role="admin", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        profile = RuntimeProfile(
            owner_user_id=user.id,
            name="rp",
            config_json="{}",
            revision=1,
            is_default=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        agent = Agent(
            name="delete-me",
            owner_user_id=user.id,
            visibility="private",
            status="creating",
            image="img",
            runtime_type="opencode",
            runtime_profile_id=profile.id,
            namespace="efp-agents",
            deployment_name="dep",
            service_name="svc",
            pvc_name="pvc",
            endpoint_path="/a/delete-me",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        job = RuntimeProfileSyncQueueService().enqueue_agent_runtime_profile_sync(
            db,
            agent,
            reason="agent_create",
        )
        assert job is not None
        assert db.query(RuntimeProfileSyncJob).filter(
            RuntimeProfileSyncJob.agent_id == agent.id
        ).count() == 1

        AgentRepository(db).delete(agent)

        assert AgentRepository(db).get_by_id(agent.id) is None
        assert db.query(RuntimeProfileSyncJob).filter(
            RuntimeProfileSyncJob.agent_id == agent.id
        ).count() == 0
    finally:
        db.close()
