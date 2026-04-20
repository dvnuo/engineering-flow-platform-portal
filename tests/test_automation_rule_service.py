import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.models.agent_task import AgentTask
from app.repositories.automation_rule_repo import AutomationRuleRepository
from app.schemas.automation_rule import AutomationRuleCreate
from app.services.automation_rule_service import AutomationRuleService


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _mk_agent(user_id: int, runtime_profile_id: str | None = None):
    return Agent(
        name="a", owner_user_id=user_id, visibility="private", status="running", image="example/image:latest",
        runtime_profile_id=runtime_profile_id, disk_size_gi=20, mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace"
    )


@pytest.mark.anyio
async def test_run_once_creates_and_dedupes_tasks(monkeypatch):
    db = _session()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)

    svc = AutomationRuleService(db)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    payload = AutomationRuleCreate(
        name="r1", target_agent_id=agent.id, owner="acme", repo="portal", review_target_type="user", review_target="alice"
    )
    rule = svc.create_rule(payload, current_user_id=user.id)

    async def _poll(*_args, **_kwargs):
        return [{"owner": "acme", "repo": "portal", "pull_number": 3, "head_sha": "sha1", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}]

    monkeypatch.setattr(svc.poller, "poll_review_requests", _poll)

    first = await svc.run_rule_once(rule.id)
    assert first.created_task_count == 1

    second = await svc.run_rule_once(rule.id)
    assert second.created_task_count == 0
    assert second.skipped_count == 1

    async def _poll_new(*_args, **_kwargs):
        return [{"owner": "acme", "repo": "portal", "pull_number": 3, "head_sha": "sha2", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}]

    monkeypatch.setattr(svc.poller, "poll_review_requests", _poll_new)
    third = await svc.run_rule_once(rule.id)
    assert third.created_task_count == 1

    tasks = db.query(AgentTask).all()
    assert len(tasks) == 2
    task = tasks[0]
    assert task.source == "automation_rule"
    assert task.provider == "github"
    assert task.trigger == "github_pr_review_requested"
    assert task.task_type == "github_review_task"
    assert task.assignee_agent_id == rule.target_agent_id
    payload_obj = json.loads(task.input_payload_json)
    assert payload_obj["owner"] == "acme"
    assert payload_obj["repo"] == "portal"
    assert payload_obj["rule_id"] == rule.id
    assert payload_obj["skill_name"] == "review-pull-request"

    events = AutomationRuleRepository(db).list_events(rule.id, 10)
    assert len(events) == 2
