import asyncio
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.agent import Agent
from app.models.runtime_profile import RuntimeProfile
from app.models.user import User
from app.repositories.runtime_profile_sync_job_repo import RuntimeProfileSyncJobRepository
from app.services.runtime_profile_sync_queue_service import RuntimeProfileSyncQueueService


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json="{}", revision=1, is_default=True)
    db.add(rp)
    db.commit()
    db.refresh(rp)
    agent = Agent(name="a", owner_user_id=user.id, visibility="private", status="creating", image="img", runtime_type="native", runtime_profile_id=rp.id, namespace="efp-agents", deployment_name="dep", service_name="svc", pvc_name="pvc", endpoint_path="/a/x")
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return db, agent, rp


def test_enqueue_creates_and_deduplicates_job():
    db, agent, rp = _db()
    svc = RuntimeProfileSyncQueueService()
    try:
        a = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")
        b = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")
        assert a is not None
        assert a.id == b.id
    finally:
        db.close()


def test_run_job_not_running_retries_without_push(monkeypatch):
    db, agent, _rp = _db()
    svc = RuntimeProfileSyncQueueService()
    job = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")
    repo = RuntimeProfileSyncJobRepository(db)
    locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
    called = {"push": 0}
    monkeypatch.setattr(svc.k8s_service, "get_agent_runtime_status", lambda _a: type("S", (), {"status": "creating", "message": None})())
    async def _push(*_args, **_kwargs):
        called["push"] += 1
    monkeypatch.setattr(svc.runtime_profile_sync_service, "push_payload_to_agent", _push)
    asyncio.run(svc.run_job(db, locked))
    done = repo.get(job.id)
    assert done.status == "retrying"
    assert called["push"] == 0
    db.close()


def test_run_job_running_push_success(monkeypatch):
    db, agent, _rp = _db()
    svc = RuntimeProfileSyncQueueService()
    job = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")
    repo = RuntimeProfileSyncJobRepository(db)
    locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
    monkeypatch.setattr(svc.k8s_service, "get_agent_runtime_status", lambda _a: type("S", (), {"status": "running", "message": None})())
    monkeypatch.setattr(svc.runtime_profile_sync_service, "build_apply_payload_for_agent", lambda *_: {"x": 1})
    async def _push(*_args, **_kwargs):
        return type("R", (), {"ok": True, "pending_restart": False, "partially_applied": False})()
    monkeypatch.setattr(svc.runtime_profile_sync_service, "push_payload_to_agent", _push)
    asyncio.run(svc.run_job(db, locked))
    assert repo.get(job.id).status == "succeeded"
    db.close()

def test_run_job_push_failed_retries_and_exhaustion(monkeypatch):
    db, agent, _rp = _db()
    svc = RuntimeProfileSyncQueueService()
    job = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")
    repo = RuntimeProfileSyncJobRepository(db)
    locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
    monkeypatch.setattr(svc.k8s_service, "get_agent_runtime_status", lambda _a: type("S", (), {"status": "running", "message": None})())
    monkeypatch.setattr(svc.runtime_profile_sync_service, "build_apply_payload_for_agent", lambda *_: {"x": 1})
    async def _push(*_args, **_kwargs):
        return type("R", (), {"ok": False, "apply_status": "failed", "message": "x", "pending_restart": False, "partially_applied": False})()
    monkeypatch.setattr(svc.runtime_profile_sync_service, "push_payload_to_agent", _push)
    asyncio.run(svc.run_job(db, locked))
    assert repo.get(job.id).status == "retrying"

    job2 = repo.enqueue(agent_id=agent.id, runtime_profile_id=agent.runtime_profile_id, requested_revision=1, action="apply_once")
    job2.max_attempts = 1
    db.add(job2); db.commit(); db.refresh(job2)
    locked2 = repo.acquire_lock(job2.id, now=datetime.utcnow(), lease_seconds=60)
    asyncio.run(svc.run_job(db, locked2))
    assert repo.get(job2.id).status == "failed"
    db.close()


def test_run_job_skips_stale_or_missing(monkeypatch):
    db, agent, rp = _db()
    svc = RuntimeProfileSyncQueueService()
    repo = RuntimeProfileSyncJobRepository(db)
    job = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")
    locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
    agent.runtime_profile_id = "other"; db.add(agent); db.commit()
    asyncio.run(svc.run_job(db, locked))
    assert repo.get(job.id).status == "skipped"

    job2 = repo.enqueue(agent_id="missing-agent", runtime_profile_id=rp.id, requested_revision=rp.revision)
    locked2 = repo.acquire_lock(job2.id, now=datetime.utcnow(), lease_seconds=60)
    asyncio.run(svc.run_job(db, locked2))
    assert repo.get(job2.id).status == "skipped"

    job3 = repo.enqueue(agent_id=agent.id, runtime_profile_id="missing-profile", requested_revision=1)
    locked3 = repo.acquire_lock(job3.id, now=datetime.utcnow(), lease_seconds=60)
    monkeypatch.setattr(svc.k8s_service, "get_agent_runtime_status", lambda _a: type("S", (), {"status": "running", "message": None})())
    asyncio.run(svc.run_job(db, locked3))
    assert repo.get(job3.id).status == "skipped"
    db.close()


def test_expired_running_job_is_due_and_can_be_reacquired():
    from datetime import timedelta

    db, agent, _rp = _db()
    try:
        svc = RuntimeProfileSyncQueueService()
        repo = RuntimeProfileSyncJobRepository(db)
        job = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")

        first_locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
        assert first_locked is not None
        assert first_locked.status == "running"
        assert first_locked.attempts == 1

        first_locked.locked_until = datetime.utcnow() - timedelta(seconds=1)
        db.add(first_locked)
        db.commit()

        due = repo.list_due_jobs(now=datetime.utcnow(), limit=10)
        assert any(item.id == job.id for item in due)

        second_locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
        assert second_locked is not None
        assert second_locked.status == "running"
        assert second_locked.attempts == 2
    finally:
        db.close()


def test_non_expired_running_job_is_not_due():
    from datetime import timedelta

    db, agent, _rp = _db()
    try:
        svc = RuntimeProfileSyncQueueService()
        repo = RuntimeProfileSyncJobRepository(db)
        job = svc.enqueue_agent_runtime_profile_sync(db, agent, reason="x")

        locked = repo.acquire_lock(job.id, now=datetime.utcnow(), lease_seconds=60)
        assert locked is not None
        locked.locked_until = datetime.utcnow() + timedelta(seconds=60)
        db.add(locked)
        db.commit()

        due = repo.list_due_jobs(now=datetime.utcnow(), limit=10)
        assert all(item.id != job.id for item in due)
    finally:
        db.close()
