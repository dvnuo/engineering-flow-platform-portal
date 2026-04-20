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


def _create_review_rule(db: Session, *, user_id: int, agent_id: str):
    return AutomationRuleRepository(db).create(
        {
            "name": "r",
            "enabled": True,
            "source_type": "github",
            "trigger_type": "github_pr_review_requested",
            "target_agent_id": agent_id,
            "task_type": "github_review_task",
            "scope_json": json.dumps({"owner": "acme", "repo": "portal"}),
            "trigger_config_json": json.dumps({"review_target_type": "user", "review_target": "alice"}),
            "task_config_json": json.dumps({"skill_name": "review-pull-request", "review_event": "COMMENT"}),
            "schedule_json": json.dumps({"interval_seconds": 60}),
            "state_json": "{}",
            "owner_user_id": user_id,
            "created_by_user_id": user_id,
        }
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


def test_get_or_create_event_by_dedupe_handles_unique_conflict():
    db = _session()
    user = User(username="u2", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp2", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    repo = AutomationRuleRepository(db)
    event1, created1 = repo.get_or_create_event_by_dedupe(
        rule_id=rule.id,
        dedupe_key="k1",
        source_payload_json="{}",
        normalized_payload_json="{}",
    )
    event2, created2 = repo.get_or_create_event_by_dedupe(
        rule_id=rule.id,
        dedupe_key="k1",
        source_payload_json="{}",
        normalized_payload_json="{}",
    )
    assert created1 is True
    assert created2 is False
    assert event1.id == event2.id


def test_create_github_review_task_concurrent_claim_creates_single_task(monkeypatch):
    db = _session()
    user = User(username="u3", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp3", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    svc1 = AutomationRuleService(db)
    svc2 = AutomationRuleService(db)
    monkeypatch.setattr(svc1.dispatcher, "dispatch_task_in_background", lambda _task_id: None)
    monkeypatch.setattr(svc2.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    item = {
        "owner": "acme",
        "repo": "portal",
        "pull_number": 3,
        "head_sha": "sha1",
        "review_target": {"type": "user", "name": "alice"},
        "source_payload": {},
    }
    task_cfg = {"skill_name": "review-pull-request", "review_event": "COMMENT"}

    original_create = svc1.task_repo.create
    state = {"second_result": None}

    def _create_with_interleaving(**kwargs):
        if state["second_result"] is None:
            state["second_result"] = svc2.create_github_review_task_for_discovered_item(rule=rule, item=item, task_cfg=task_cfg)
        return original_create(**kwargs)

    monkeypatch.setattr(svc1.task_repo, "create", _create_with_interleaving)

    task, skipped = svc1.create_github_review_task_for_discovered_item(rule=rule, item=item, task_cfg=task_cfg)
    assert skipped is False
    assert task is not None
    second_task, second_skipped = state["second_result"]
    assert second_task is None
    assert second_skipped is True

    assert db.query(AgentTask).count() == 1
    events = AutomationRuleRepository(db).list_events(rule.id, 10)
    assert len(events) == 1
    assert events[0].status == "task_created"
