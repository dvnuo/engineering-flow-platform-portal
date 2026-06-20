import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.services.agent_task_reconcile_worker import AgentTaskReconcileWorker
from app.services.auth_service import hash_password


def test_agent_task_reconcile_worker_schedules_queued_and_reconciles_running(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        agent = Agent(
            name="Worker Agent",
            description="worker",
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
            deployment_name="dep-worker",
            service_name="svc-worker",
            pvc_name="pvc-worker",
            endpoint_path="/",
            agent_type="workspace",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        queued = AgentTask(
            assignee_agent_id=agent.id,
            owner_user_id=user.id,
            source="portal",
            task_type="agent_async_task",
            input_payload_json='{"user_task": "queued"}',
            status="queued",
            retry_count=0,
        )
        running = AgentTask(
            assignee_agent_id=agent.id,
            owner_user_id=user.id,
            source="portal",
            task_type="agent_async_task",
            input_payload_json='{"user_task": "running"}',
            status="running",
            retry_count=0,
        )
        db.add_all([queued, running])
        db.commit()
        queued_id = queued.id
        running_id = running.id
    finally:
        db.close()

    worker = AgentTaskReconcileWorker()
    worker.settings = SimpleNamespace(agent_task_reconcile_worker_batch_size=10)
    calls = {"queued": [], "running": []}

    class FakeDispatcher:
        def dispatch_task_in_background(self, task_id):
            calls["queued"].append(task_id)

        async def reconcile_running_task(self, task_id, db_arg):
            calls["running"].append((task_id, db_arg is not None))

    worker.dispatcher = FakeDispatcher()
    monkeypatch.setattr("app.services.agent_task_reconcile_worker.SessionLocal", TestingSessionLocal)

    asyncio.run(worker._run_once())

    assert calls["queued"] == [queued_id]
    assert calls["running"] == [(running_id, True)]


def test_agent_task_reconcile_worker_uses_safe_defaults_for_missing_settings():
    worker = AgentTaskReconcileWorker()
    worker.settings = SimpleNamespace()

    assert worker._initial_delay_seconds() == 30
    assert worker._interval_seconds() == 5
    assert worker._batch_size() == 50

    worker.settings = SimpleNamespace(
        agent_task_reconcile_worker_initial_delay_seconds="-1",
        agent_task_reconcile_worker_interval_seconds="0",
        agent_task_reconcile_worker_batch_size="bad",
    )

    assert worker._initial_delay_seconds() == 0
    assert worker._interval_seconds() == 1
    assert worker._batch_size() == 50
