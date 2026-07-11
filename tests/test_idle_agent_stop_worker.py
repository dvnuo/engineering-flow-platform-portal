from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, User
from app.services.idle_agent_stop_worker import IdleAgentStopWorker


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal


def _make_user(db):
    user = User(username="owner", password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_agent(db, owner_id, *, name, status="running", last_activity_at=None, created_at=None):
    agent = Agent(
        name=name,
        owner_user_id=owner_id,
        visibility="private",
        status=status,
        image="example/image:latest",
        deployment_name=f"dep-{name}",
        service_name=f"svc-{name}",
        pvc_name=f"pvc-{name}",
        agent_type="workspace",
    )
    if created_at is not None:
        agent.created_at = created_at
    agent.last_activity_at = last_activity_at
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


class FakeK8s:
    enabled = True

    def __init__(self):
        self.stopped = []

    def stop_agent(self, agent):
        self.stopped.append(agent.id)
        return SimpleNamespace(status="stopped", message=None)


def _worker(TestingSessionLocal, monkeypatch, *, idle_after=259200):
    worker = IdleAgentStopWorker()
    worker.settings = SimpleNamespace(
        agent_idle_stop_after_seconds=idle_after,
        idle_agent_stop_worker_batch_size=100,
    )
    worker.k8s = FakeK8s()
    monkeypatch.setattr("app.services.idle_agent_stop_worker.SessionLocal", TestingSessionLocal)
    return worker


def test_stops_idle_running_agent_but_not_recent_or_stopped(monkeypatch):
    TestingSessionLocal = _session_factory()
    db = TestingSessionLocal()
    now = datetime.utcnow()
    try:
        user = _make_user(db)
        idle = _make_agent(db, user.id, name="idle", last_activity_at=now - timedelta(days=5))
        recent = _make_agent(db, user.id, name="recent", last_activity_at=now - timedelta(hours=1))
        already = _make_agent(db, user.id, name="already", status="stopped",
                              last_activity_at=now - timedelta(days=10))
        idle_id, recent_id, already_id = idle.id, recent.id, already.id
    finally:
        db.close()

    worker = _worker(TestingSessionLocal, monkeypatch)
    stopped = worker.run_once()

    assert stopped == 1
    assert worker.k8s.stopped == [idle_id]
    db = TestingSessionLocal()
    try:
        assert db.get(Agent, idle_id).status == "stopped"
        assert db.get(Agent, recent_id).status == "running"
        assert db.get(Agent, already_id).status == "stopped"  # untouched
    finally:
        db.close()


def test_does_not_stop_idle_agent_with_active_task(monkeypatch):
    TestingSessionLocal = _session_factory()
    db = TestingSessionLocal()
    now = datetime.utcnow()
    try:
        user = _make_user(db)
        busy = _make_agent(db, user.id, name="busy", last_activity_at=now - timedelta(days=5))
        db.add(AgentTask(
            assignee_agent_id=busy.id, owner_user_id=user.id, source="portal",
            task_type="agent_async_task", input_payload_json="{}", status="running", retry_count=0,
        ))
        db.commit()
        busy_id = busy.id
    finally:
        db.close()

    worker = _worker(TestingSessionLocal, monkeypatch)
    stopped = worker.run_once()

    assert stopped == 0
    assert worker.k8s.stopped == []
    db = TestingSessionLocal()
    try:
        assert db.get(Agent, busy_id).status == "running"
    finally:
        db.close()


def test_null_last_activity_falls_back_to_created_at(monkeypatch):
    TestingSessionLocal = _session_factory()
    db = TestingSessionLocal()
    now = datetime.utcnow()
    try:
        user = _make_user(db)
        old = _make_agent(db, user.id, name="old", last_activity_at=None,
                          created_at=now - timedelta(days=5))
        fresh = _make_agent(db, user.id, name="fresh", last_activity_at=None,
                            created_at=now - timedelta(minutes=1))
        old_id, fresh_id = old.id, fresh.id
    finally:
        db.close()

    worker = _worker(TestingSessionLocal, monkeypatch)
    worker.run_once()

    assert worker.k8s.stopped == [old_id]
    db = TestingSessionLocal()
    try:
        assert db.get(Agent, old_id).status == "stopped"
        assert db.get(Agent, fresh_id).status == "running"
    finally:
        db.close()


def test_noop_when_k8s_disabled_or_threshold_nonpositive(monkeypatch):
    TestingSessionLocal = _session_factory()
    db = TestingSessionLocal()
    try:
        user = _make_user(db)
        _make_agent(db, user.id, name="idle", last_activity_at=datetime.utcnow() - timedelta(days=9))
    finally:
        db.close()

    # k8s disabled -> no-op
    worker = _worker(TestingSessionLocal, monkeypatch)
    worker.k8s.enabled = False
    assert worker.run_once() == 0
    assert worker.k8s.stopped == []

    # threshold <= 0 -> no-op (feature effectively off)
    worker2 = _worker(TestingSessionLocal, monkeypatch, idle_after=0)
    assert worker2.run_once() == 0
    assert worker2.k8s.stopped == []


def test_worker_uses_safe_defaults_for_missing_settings():
    worker = IdleAgentStopWorker()
    worker.settings = SimpleNamespace()
    assert worker._interval_seconds() == 600
    assert worker._batch_size() == 100
    assert worker._idle_after_seconds() == 259200


def test_touch_agent_activity_updates_then_throttles(monkeypatch):
    from app.services import agent_activity

    TestingSessionLocal = _session_factory()
    db = TestingSessionLocal()
    try:
        user = _make_user(db)
        agent = _make_agent(db, user.id, name="a", last_activity_at=None)
        agent_id = agent.id
    finally:
        db.close()

    monkeypatch.setattr(agent_activity, "SessionLocal", TestingSessionLocal)
    agent_activity.reset_throttle_cache()

    # First call writes last_activity_at.
    agent_activity.touch_agent_activity(agent_id)
    db = TestingSessionLocal()
    try:
        assert db.get(Agent, agent_id).last_activity_at is not None
        # Reset the column, then a second immediate call is throttled (no write).
        db.get(Agent, agent_id).last_activity_at = None
        db.commit()
    finally:
        db.close()

    agent_activity.touch_agent_activity(agent_id)  # within 60s -> throttled
    db = TestingSessionLocal()
    try:
        assert db.get(Agent, agent_id).last_activity_at is None
    finally:
        db.close()
