from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, DelegationRule, DelegationRuleRun, User
from app.services.dashboard_summary import DashboardSummaryService


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


def test_dashboard_summary_rolls_up_attention_workload_and_delegations():
    db = _session()
    try:
        user = User(id=1, username="owner", password_hash="test", role="user")
        db.add(user)
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

        summary = DashboardSummaryService(db).build(SimpleNamespace(id=1, role="user"), scope="mine")

        assert summary["agents"]["total"] == 2
        assert summary["agents"]["attention"] == 1
        assert summary["tasks"]["active"] == 1
        assert summary["tasks"]["attention"] == 1
        assert summary["delegations"]["enabled"] == 1
        assert summary["delegations"]["due"] == 1
        assert any(item["target_id"] == "task-failed" for item in summary["attention_items"])
        assert summary["workload"][0]["agent_id"] == "agent-bad"
        assert summary["delegation_health"][0]["rule_id"] == "rule-1"
    finally:
        db.close()
