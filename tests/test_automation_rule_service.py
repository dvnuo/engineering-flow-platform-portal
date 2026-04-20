import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.models.capability_profile import CapabilityProfile
from app.models.agent_task import AgentTask
from app.repositories.agent_task_repo import AgentTaskRepository
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


def test_create_github_review_task_reclaims_stale_creating_task(monkeypatch):
    db = _session()
    user = User(username="u4", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp4", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    svc = AutomationRuleService(db)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    item = {"owner": "acme", "repo": "portal", "pull_number": 5, "head_sha": "sha-stale", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}
    dedupe_key = f"github:pr_review_requested:{rule.id}:acme/portal:5:sha-stale:user:alice"
    event = AutomationRuleRepository(db).create_event(
        rule_id=rule.id,
        dedupe_key=dedupe_key,
        source_payload_json="{}",
        normalized_payload_json="{}",
        status="creating_task",
    )
    event.updated_at = datetime.utcnow() - timedelta(minutes=10)
    db.add(event); db.commit()

    task, skipped = svc.create_github_review_task_for_discovered_item(rule=rule, item=item, task_cfg={"skill_name": "review-pull-request", "review_event": "COMMENT"})
    assert skipped is False
    assert task is not None
    refreshed = AutomationRuleRepository(db).get_event(event.id)
    assert refreshed.status == "task_created"
    assert refreshed.task_id == task.id


def test_create_github_review_task_skips_fresh_creating_task(monkeypatch):
    db = _session()
    user = User(username="u5", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp5", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    svc = AutomationRuleService(db)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    item = {"owner": "acme", "repo": "portal", "pull_number": 6, "head_sha": "sha-fresh", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}
    dedupe_key = f"github:pr_review_requested:{rule.id}:acme/portal:6:sha-fresh:user:alice"
    event = AutomationRuleRepository(db).create_event(
        rule_id=rule.id,
        dedupe_key=dedupe_key,
        source_payload_json="{}",
        normalized_payload_json="{}",
        status="creating_task",
    )
    event.updated_at = datetime.utcnow()
    db.add(event); db.commit()

    task, skipped = svc.create_github_review_task_for_discovered_item(rule=rule, item=item, task_cfg={"skill_name": "review-pull-request", "review_event": "COMMENT"})
    assert task is None
    assert skipped is True
    assert db.query(AgentTask).count() == 0


def test_create_github_review_task_repairs_event_when_task_already_exists(monkeypatch):
    db = _session()
    user = User(username="u8", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp8", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    svc = AutomationRuleService(db)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    item = {"owner": "acme", "repo": "portal", "pull_number": 10, "head_sha": "sha-existing", "review_target": {"type": "user", "name": "alice"}, "source_payload": {}}
    full_dedupe_key = f"github:pr_review_requested:{rule.id}:acme/portal:10:sha-existing:user:alice"
    short_dedupe_key = svc._agent_task_dedupe_key(full_dedupe_key)
    event = AutomationRuleRepository(db).create_event(
        rule_id=rule.id,
        dedupe_key=full_dedupe_key,
        source_payload_json="{}",
        normalized_payload_json="{}",
        status="creating_task",
    )
    event.updated_at = datetime.utcnow() - timedelta(minutes=10)
    db.add(event); db.commit()

    existing_task = AgentTaskRepository(db).create(
        parent_agent_id=None,
        assignee_agent_id=rule.target_agent_id,
        owner_user_id=rule.owner_user_id,
        created_by_user_id=rule.created_by_user_id,
        source="automation_rule",
        task_type=rule.task_type,
        input_payload_json="{}",
        shared_context_ref=None,
        task_family="triggered_work",
        provider="github",
        trigger="github_pr_review_requested",
        bundle_id="github:pr_review:acme/portal:10",
        version_key="sha-existing",
        dedupe_key=short_dedupe_key,
        status="queued",
        result_payload_json=None,
        retry_count=0,
    )

    task, skipped = svc.create_github_review_task_for_discovered_item(rule=rule, item=item, task_cfg={"skill_name": "review-pull-request", "review_event": "COMMENT"})
    assert task is None
    assert skipped is True
    assert db.query(AgentTask).count() == 1
    refreshed = AutomationRuleRepository(db).get_event(event.id)
    assert refreshed.status == "task_created"
    assert refreshed.task_id == existing_task.id


def test_github_review_task_payload_contract_for_efp_runtime(monkeypatch):
    db = _session()
    user = User(username="u9", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp9", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    svc = AutomationRuleService(db)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)

    item = {
        "owner": "Acme",
        "repo": "Portal",
        "pull_number": 42,
        "head_sha": "sha-contract",
        "review_target": {"type": "team", "name": "Acme/Reviewers"},
        "source_payload": {"html_url": "https://example.local/pr/42"},
    }
    task, skipped = svc.create_github_review_task_for_discovered_item(
        rule=rule,
        item=item,
        task_cfg={"skill_name": "review-pull-request", "review_event": "COMMENT"},
    )
    assert skipped is False
    assert task is not None

    payload = json.loads(task.input_payload_json)
    assert set(payload.keys()) == {
        "source",
        "automation_rule",
        "automation_rule_id",
        "rule_id",
        "provider",
        "owner",
        "repo",
        "pull_number",
        "head_sha",
        "review_target",
        "task_type",
        "skill_name",
        "review_event",
        "dedupe_key",
    }
    assert payload["source"] == "automation_rule"
    assert payload["automation_rule"] == "github.pr_review_requested"
    assert payload["automation_rule_id"] == rule.id
    assert payload["rule_id"] == rule.id
    assert payload["provider"] == "github"
    assert payload["owner"] == "Acme"
    assert payload["repo"] == "Portal"
    assert payload["pull_number"] == 42
    assert payload["head_sha"] == "sha-contract"
    assert payload["task_type"] == "github_review_task"
    assert payload["skill_name"] == "review-pull-request"
    assert payload["review_event"] in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}
    assert payload["review_target"] == {"type": "team", "name": "Acme/Reviewers"}
    assert "dedupe_key" in payload
    assert len(task.dedupe_key or "") <= 255


@pytest.mark.anyio
async def test_run_once_failure_schedules_next_run(monkeypatch):
    db = _session()
    user = User(username="u6", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp6", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    svc = AutomationRuleService(db)
    monkeypatch.setattr("app.services.automation_rule_service.resolve_github_for_agent", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("resolver boom")))

    before = datetime.utcnow()
    with pytest.raises(Exception):
        await svc.run_rule_once(rule.id)

    refreshed_rule = AutomationRuleRepository(db).get(rule.id)
    runs = AutomationRuleRepository(db).list_runs(rule.id, 5)
    assert runs[0].status == "failed"
    assert refreshed_rule.last_run_at is not None
    assert refreshed_rule.next_run_at is not None
    assert refreshed_rule.next_run_at > before
    assert refreshed_rule.locked_until is None


@pytest.mark.anyio
async def test_run_once_blocked_by_capability_profile(monkeypatch):
    db = _session()
    user = User(username="u7", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp7", config_json=json.dumps({"github": {"enabled": True, "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    rule = _create_review_rule(db, user_id=user.id, agent_id=agent.id)

    cp = CapabilityProfile(name="cap-jira-only", allowed_external_systems_json='["jira"]')
    db.add(cp); db.commit(); db.refresh(cp)
    agent.capability_profile_id = cp.id
    db.add(agent); db.commit()

    svc = AutomationRuleService(db)
    with pytest.raises(Exception):
        await svc.run_rule_once(rule.id)

    runs = AutomationRuleRepository(db).list_runs(rule.id, 5)
    assert runs[0].status == "failed"
