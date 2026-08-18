from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, DelegationRule, DelegationRuleRun, User
from app.services.work_overview import WorkOverviewService


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return TestingSessionLocal()


def _agent(agent_id: str, *, owner_user_id: int, name: str, status: str = "running", last_error: str | None = None):
    return Agent(
        id=agent_id,
        name=name,
        owner_user_id=owner_user_id,
        visibility="private",
        status=status,
        image="example/image:latest",
        deployment_name=f"{agent_id}-deploy",
        service_name=f"{agent_id}-svc",
        pvc_name=f"{agent_id}-pvc",
        last_error=last_error,
    )


def _seed_work(db):
    db.add(User(id=1, username="owner", password_hash="test", role="user"))
    db.add(_agent("agent-ok", owner_user_id=1, name="Agent OK"))
    db.add(_agent("agent-bad", owner_user_id=1, name="Agent Bad", status="failed", last_error="pod failed"))
    db.add(
        AgentTask(
            id="task-failed",
            assignee_agent_id="agent-bad",
            source="portal",
            task_type="agent_async_task",
            title="Investigate failure",
            status="failed",
            owner_user_id=1,
            updated_at=datetime.utcnow(),
        )
    )
    db.add(
        AgentTask(
            id="task-running",
            assignee_agent_id="agent-ok",
            source="portal",
            task_type="agent_async_task",
            title="Build docs",
            status="running",
            owner_user_id=1,
            updated_at=datetime.utcnow(),
        )
    )
    db.add(
        DelegationRule(
            id="rule-1",
            name="PR Review",
            enabled=True,
            source_type="github",
            trigger_type="github_pr_review",
            target_agent_id="agent-ok",
            task_type="agent_async_task",
            scope_json="{}",
            trigger_config_json="{}",
            task_config_json='{"skill_name":"review"}',
            schedule_json="{}",
            state_json="{}",
            owner_user_id=1,
            next_run_at=datetime.utcnow() - timedelta(minutes=1),
        )
    )
    db.add(
        DelegationRuleRun(
            id="run-1",
            rule_id="rule-1",
            status="failed",
            started_at=datetime.utcnow(),
            error_message="GitHub unavailable",
        )
    )
    db.commit()


def test_task_overview_rolls_up_attention_workload_and_activity():
    db = _session()
    try:
        _seed_work(db)
        overview = WorkOverviewService(db).build_tasks(SimpleNamespace(id=1, role="user"), scope="mine")

        assert overview["total"] == 2
        assert overview["active"] == 1
        assert overview["attention"] == 1
        assert overview["health"]["tone"] == "error"
        assert any(segment["status"] == "failed" and segment["tone"] == "error" for segment in overview["segments"])
        assert overview["priority_items"][0]["target_id"] == "task-failed"
        assert overview["workload"][0]["agent_id"] == "agent-bad"
        assert overview["workload"][0]["attention_percent"] == 100
        assert any(item["target_id"] == "task-running" for item in overview["recent_activity"])
    finally:
        db.close()


def test_delegation_overview_rolls_up_due_failed_and_recent_runs():
    db = _session()
    try:
        _seed_work(db)
        overview = WorkOverviewService(db).build_delegations(SimpleNamespace(id=1, role="user"), scope="mine")

        assert overview["total"] == 1
        assert overview["enabled"] == 1
        assert overview["due"] == 1
        assert overview["failed_runs"] == 1
        assert overview["health"]["tone"] == "error"
        assert any(segment["label"] == "Due" and segment["tone"] == "warning" for segment in overview["segments"])
        assert any(item["target_id"] == "rule-1" for item in overview["priority_items"])
        assert overview["health_rows"][0]["rule_id"] == "rule-1"
        assert overview["health_rows"][0]["last_status_tone"] == "error"
        assert overview["health_rows"][0]["is_due"] is True
        assert overview["recent_activity"][0]["target_id"] == "rule-1"
    finally:
        db.close()
