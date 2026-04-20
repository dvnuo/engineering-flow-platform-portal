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
async def test_run_once_event_retry_and_dedupe(monkeypatch):
    db = _session()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"github": {"enabled": True, "base_url": "", "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)

    svc = AutomationRuleService(db)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    payload = AutomationRuleCreate(
        name="r1", target_agent_id=agent.id, owner="acme", repo="portal", review_target_type="user", review_target="alice"
    )
    rule = svc.create_rule(payload, current_user_id=user.id)

    async def _poll_sha1(*_args, **_kwargs):
        return [{"owner": "acme", "repo": "portal", "pull_number": 3, "head_sha": "sha1", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}]

    monkeypatch.setattr(svc.poller, "poll_review_requests", _poll_sha1)

    original_create = svc.task_repo.create
    state = {"count": 0}

    def _flaky_create(**kwargs):
        state["count"] += 1
        if state["count"] == 1:
            raise RuntimeError("create failure once")
        return original_create(**kwargs)

    monkeypatch.setattr(svc.task_repo, "create", _flaky_create)

    first = await svc.run_rule_once(rule.id)
    assert first.status == "failed"
    event = AutomationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "failed"
    assert event.task_id is None

    second = await svc.run_rule_once(rule.id)
    assert second.created_task_count == 1
    event2 = AutomationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event2.status == "task_created"
    assert event2.task_id

    third = await svc.run_rule_once(rule.id)
    assert third.created_task_count == 0
    assert third.skipped_count == 1

    async def _poll_sha2(*_args, **_kwargs):
        return [{"owner": "acme", "repo": "portal", "pull_number": 3, "head_sha": "sha2", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}]

    monkeypatch.setattr(svc.poller, "poll_review_requests", _poll_sha2)
    fourth = await svc.run_rule_once(rule.id)
    assert fourth.created_task_count == 1

    tasks = db.query(AgentTask).all()
    assert len(tasks) == 2
    payload_obj = json.loads(tasks[0].input_payload_json)
    assert payload_obj["automation_rule"] == "github.pr_review_requested"
    assert payload_obj["automation_rule_id"] == rule.id
    assert payload_obj["rule_id"] == rule.id
