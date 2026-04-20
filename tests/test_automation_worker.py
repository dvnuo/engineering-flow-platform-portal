from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.automation_rule_repo import AutomationRuleRepository


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _mk_agent(user_id: int):
    return Agent(
        name="a", owner_user_id=user_id, visibility="private", status="running", image="img",
        disk_size_gi=20, mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace"
    )


def test_worker_lock_semantics_and_next_run_update():
    db = _session()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    agent = _mk_agent(user.id)
    db.add(agent); db.commit(); db.refresh(agent)
    repo = AutomationRuleRepository(db)
    rule = repo.create(
        {
            "name": "r",
            "enabled": True,
            "source_type": "github",
            "trigger_type": "github_pr_review_requested",
            "target_agent_id": agent.id,
            "task_type": "github_review_task",
            "scope_json": "{}",
            "trigger_config_json": "{}",
            "task_config_json": "{}",
            "schedule_json": "{\"interval_seconds\":60}",
            "state_json": "{}",
            "next_run_at": datetime.utcnow() - timedelta(seconds=1),
            "owner_user_id": user.id,
        },
        current_user_id=user.id,
    )

    now = datetime.utcnow()
    lock1 = repo.acquire_due_rule_lock(rule.id, now=now, lease_seconds=120)
    lock2 = repo.acquire_due_rule_lock(rule.id, now=now, lease_seconds=120)
    assert lock1 is not None
    assert lock2 is None

    next_run = now + timedelta(seconds=60)
    repo.release_lock_and_schedule_next(lock1, now=now, next_run_at=next_run)
    refreshed = repo.get(rule.id)
    assert refreshed.locked_until is None
    assert refreshed.last_run_at is not None
    assert refreshed.next_run_at == next_run
