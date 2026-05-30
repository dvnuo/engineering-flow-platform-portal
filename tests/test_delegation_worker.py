from datetime import datetime, timedelta
import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.delegation_rule_repo import DelegationRuleRepository
from app.services.delegation_worker import DelegationWorker


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal


def _mk_agent(user_id: int):
    return Agent(
        name="a",
        owner_user_id=user_id,
        visibility="private",
        status="running",
        image="img",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp",
        deployment_name="d",
        service_name="s",
        pvc_name="p",
        endpoint_path="/",
        agent_type="workspace",
    )


def _create_rule(repo: DelegationRuleRepository, user, agent, **overrides):
    data = {
        "name": "r",
        "enabled": True,
        "source_type": "github",
        "trigger_type": "github_pr_review",
        "target_agent_id": agent.id,
        "task_type": "agent_async_task",
        "scope_json": "{}",
        "trigger_config_json": "{}",
        "task_config_json": json.dumps({"skill_name": "review"}),
        "schedule_json": json.dumps({"interval_seconds": 60}),
        "state_json": "{}",
        "next_run_at": datetime.utcnow() - timedelta(seconds=1),
        "owner_user_id": user.id,
        "created_by_user_id": user.id,
    }
    data.update(overrides)
    return repo.create(data, current_user_id=user.id)


def test_worker_lock_semantics_and_next_run_update():
    SessionLocal = _session_factory()
    db = SessionLocal()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = _mk_agent(user.id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    repo = DelegationRuleRepository(db)
    rule = _create_rule(repo, user, agent)

    s1 = SessionLocal()
    s2 = SessionLocal()
    r1 = DelegationRuleRepository(s1)
    r2 = DelegationRuleRepository(s2)
    now = datetime.utcnow()

    lock1 = r1.acquire_due_rule_lock(rule.id, now=now, lease_seconds=120)
    lock2 = r2.acquire_due_rule_lock(rule.id, now=now, lease_seconds=120)
    assert lock1 is not None
    assert lock2 is None

    lock1.locked_until = now - timedelta(seconds=1)
    s1.add(lock1)
    s1.commit()

    lock3 = r2.acquire_due_rule_lock(rule.id, now=now, lease_seconds=120)
    assert lock3 is not None


def test_worker_stop_is_idempotent():
    worker = DelegationWorker()
    worker.stop()
    worker.stop()


def test_due_list_excludes_deleted_rules():
    SessionLocal = _session_factory()
    db = SessionLocal()
    user = User(username="u2", password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = _mk_agent(user.id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    repo = DelegationRuleRepository(db)
    now = datetime.utcnow()
    _create_rule(repo, user, agent, state_json='{"deleted": true}', next_run_at=now - timedelta(seconds=1))
    assert repo.list_due_rules(now=now, limit=10) == []


def test_worker_failure_path_schedules_next_run(monkeypatch):
    SessionLocal = _session_factory()
    db = SessionLocal()
    user = User(username="u3", password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = _mk_agent(user.id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    repo = DelegationRuleRepository(db)
    rule = _create_rule(repo, user, agent)
    db.close()

    monkeypatch.setattr("app.services.delegation_worker.SessionLocal", SessionLocal)
    worker = DelegationWorker()
    asyncio.run(worker._run_once())

    check_db = SessionLocal()
    refreshed = DelegationRuleRepository(check_db).get(rule.id)
    assert refreshed.last_run_at is not None
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > datetime.utcnow() - timedelta(seconds=1)
    assert refreshed.locked_until is None
