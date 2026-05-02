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
            "task_template_id": "github_pr_review",
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
        name="r1", target_agent_id=agent.id, task_template_id="github_pr_review",
        scope={"owner": "acme", "repo": "portal"}, trigger_config={"review_target_type": "user", "review_target": "alice"}
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
    assert payload_obj["task_template_id"] == "github_pr_review"
    assert payload_obj["task_type"] == "github_review_task"
    assert payload_obj["automation_rule"] == "github.pr_review_requested"
    assert payload_obj["automation_rule_id"] == rule.id
    assert payload_obj["rule_id"] == rule.id


@pytest.mark.anyio
async def test_github_pr_reviewer_rule_end_to_end_mocked(monkeypatch):
    db = _session()
    user = User(username="u-e2e", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(
        owner_user_id=user.id,
        name="rp-e2e",
        config_json=json.dumps({"github": {"enabled": True, "api_token": "gh-token", "base_url": "https://api.github.com"}}),
        is_default=True,
    )
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)

    svc = AutomationRuleService(db)
    payload = AutomationRuleCreate(
        name="rule-e2e",
        target_agent_id=agent.id,
        task_template_id="github_pr_review",
        scope={"owner": "Acme", "repo": "Portal"},
        trigger_config={"review_target_type": "team", "review_target": "Acme/Reviewers"},
        task_input_defaults={"review_event": "APPROVE", "skill_name": "review-pull-request"},
        schedule={"interval_seconds": 60},
    )
    rule = svc.create_rule(payload, current_user_id=user.id)

    dispatched_task_ids: list[str] = []
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda task_id: dispatched_task_ids.append(task_id))

    state = {"head_sha": "sha-1"}

    async def _poll(*_args, **_kwargs):
        return [{
            "owner": "Acme",
            "repo": "Portal",
            "pull_number": 42,
            "head_sha": state["head_sha"],
            "review_target": {"type": "team", "name": "Acme/Reviewers"},
            "html_url": "https://github.example/Acme/Portal/pull/42",
            "title": "Demo PR",
            "source_payload": {"number": 42},
        }]

    monkeypatch.setattr(svc.poller, "poll_review_requests", _poll)

    first = await svc.run_rule_once(rule.id, triggered_by="test")
    assert first.found_count == 1
    assert first.created_task_count == 1
    assert first.skipped_count == 0

    events = AutomationRuleRepository(db).list_events(rule.id, 10)
    assert len(events) == 1
    assert events[0].status == "task_created"

    tasks = db.query(AgentTask).order_by(AgentTask.created_at.asc()).all()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.source == "automation_rule"
    assert task.provider == "github"
    assert task.trigger == "github_pr_review_requested"
    assert task.template_id == "github_pr_review"
    assert task.task_type == "github_review_task"
    assert task.assignee_agent_id == agent.id
    assert len(task.dedupe_key or "") <= 255
    assert len(dispatched_task_ids) == 1

    task_payload = json.loads(task.input_payload_json)
    assert task_payload["source"] == "automation_rule"
    assert task_payload["automation_rule"] == "github.pr_review_requested"
    assert task_payload["automation_rule_id"] == rule.id
    assert task_payload["rule_id"] == rule.id
    assert task_payload["provider"] == "github"
    assert task_payload["task_template_id"] == "github_pr_review"
    assert task_payload["task_type"] == "github_review_task"
    assert task_payload["owner"] == "Acme"
    assert task_payload["repo"] == "Portal"
    assert task_payload["pull_number"] == 42
    assert task_payload["head_sha"] == "sha-1"
    assert task_payload["review_target"] == {"type": "team", "name": "Acme/Reviewers"}
    assert task_payload["skill_name"] == "review-pull-request"
    assert task_payload["execution_mode"] == "chat_tool_loop"
    assert task_payload["review_event"] == "APPROVE"
    assert task_payload["trigger"] == "github_pr_review_requested"
    assert task_payload.get("dedupe_key")

    second = await svc.run_rule_once(rule.id, triggered_by="test")
    assert second.created_task_count == 0
    assert second.skipped_count == 1
    assert db.query(AgentTask).count() == 1

    state["head_sha"] = "sha-2"
    third = await svc.run_rule_once(rule.id, triggered_by="test")
    assert third.created_task_count == 1
    assert db.query(AgentTask).count() == 2
    assert db.query(AgentTask).filter(AgentTask.status == "stale").count() >= 1


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
    assert payload["source"] == "automation_rule"
    assert payload["automation_rule_id"] == rule.id
    assert payload["owner"] == "Acme"
    assert payload["repo"] == "Portal"
    assert payload["pull_number"] == 42
    assert payload["review_event"] == "COMMENT"
    assert payload["skill_name"] == "review-pull-request"
    assert payload["execution_mode"] == "chat_tool_loop"
    assert payload["task_type"] == "github_review_task"
    assert payload["source"] == "automation_rule"
    assert payload["automation_rule"] == "github.pr_review_requested"
    assert payload["automation_rule_id"] == rule.id
    assert payload["rule_id"] == rule.id
    assert payload["provider"] == "github"
    assert payload["owner"] == "Acme"
    assert payload["repo"] == "Portal"
    assert payload["pull_number"] == 42
    assert payload["head_sha"] == "sha-contract"
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


def _create_runtime_and_agent(db: Session, username: str = "u-mention"):
    user = User(username=username, password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name=f"rp-{username}", config_json=json.dumps({"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    return user, agent


def test_create_github_comment_mention_rule_without_trigger_type_sets_default():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-mention-default")
    svc = AutomationRuleService(db)
    payload = AutomationRuleCreate(name="m1", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"})
    rule = svc.create_rule(payload, current_user_id=user.id)
    assert rule.trigger_type == "github_comment_mention"
    assert rule.task_type == "triggered_event_task"


def test_create_github_pr_review_without_trigger_type_still_sets_pr_default():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-pr-default")
    svc = AutomationRuleService(db)
    payload = AutomationRuleCreate(name="p1", target_agent_id=agent.id, task_template_id="github_pr_review", scope={"owner": "acme", "repo": "portal"}, trigger_config={"review_target_type": "user", "review_target": "alice"})
    rule = svc.create_rule(payload, current_user_id=user.id)
    assert rule.trigger_type == "github_pr_review_requested"


def test_create_github_comment_mention_rejects_wrong_source_type():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-bad-source")
    svc = AutomationRuleService(db)
    with pytest.raises(Exception) as exc:
        svc.create_rule(
            AutomationRuleCreate(
                name="bad-source",
                target_agent_id=agent.id,
                source_type="jira",
                task_template_id="github_comment_mention",
                scope={"owner": "acme", "repo": "portal"},
                trigger_config={"mention_target": "efp-agent"},
            ),
            current_user_id=user.id,
        )
    assert "source_type must be github" in str(exc.value)


def test_create_github_comment_mention_rejects_wrong_trigger_type():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-bad-trigger-mention")
    svc = AutomationRuleService(db)
    with pytest.raises(Exception) as exc:
        svc.create_rule(
            AutomationRuleCreate(
                name="bad-trigger",
                target_agent_id=agent.id,
                source_type="github",
                trigger_type="github_pr_review_requested",
                task_template_id="github_comment_mention",
                scope={"owner": "acme", "repo": "portal"},
                trigger_config={"mention_target": "efp-agent"},
            ),
            current_user_id=user.id,
        )
    assert "trigger_type must be github_comment_mention" in str(exc.value)


def test_create_github_pr_review_rejects_wrong_trigger_type():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-bad-trigger-pr")
    svc = AutomationRuleService(db)
    with pytest.raises(Exception) as exc:
        svc.create_rule(
            AutomationRuleCreate(
                name="bad-pr-trigger",
                target_agent_id=agent.id,
                trigger_type="github_comment_mention",
                task_template_id="github_pr_review",
                scope={"owner": "acme", "repo": "portal"},
                trigger_config={"review_target_type": "user", "review_target": "alice"},
            ),
            current_user_id=user.id,
        )
    assert "trigger_type must be github_pr_review_requested" in str(exc.value)


@pytest.mark.anyio
async def test_run_once_github_comment_mention_creates_triggered_event_task(monkeypatch):
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-run-once")
    svc = AutomationRuleService(db)
    payload = AutomationRuleCreate(name="m2", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"})
    rule = svc.create_rule(payload, current_user_id=user.id)

    async def _poll_mentions(**_kwargs):
        return ([{"owner": "acme", "repo": "portal", "comment_kind": "issue_comment", "context_type": "issue", "issue_number": 11, "comment_id": 1, "body": "@efp-agent hi", "mentioned_account": "efp-agent", "mentioned_logins": ["efp-agent"], "source_event": "poll.issue_comment", "source_payload": {}}], {"poll_cursors": {"issue_comment": {"last_seen_updated_at": "2026-01-01T00:00:00Z", "last_seen_comment_id": 1}}})

    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    dispatched = []
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda task_id: dispatched.append(task_id))

    result = await svc.run_rule_once(rule.id)
    assert result.found_count == 1
    assert result.created_task_count == 1
    task = db.query(AgentTask).one()
    payload_obj = json.loads(task.input_payload_json)
    assert task.task_type == "triggered_event_task"
    assert task.template_id == "github_comment_mention"
    assert task.provider == "github"
    assert task.trigger == "github_comment_mention"
    assert payload_obj["source_kind"] == "github.mention"
    assert payload_obj["automation_rule"] == "github.comment_mention"
    assert payload_obj["skill_name"] == "handle-triggered-event"
    assert payload_obj["reply_mode"] == "same_surface"
    assert payload_obj["session_id"].startswith("github:mention:")
    assert dispatched == [task.id]
    event = AutomationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "task_created"


@pytest.mark.anyio
async def test_run_once_github_comment_mention_dedupes_same_comment(monkeypatch):
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-dedupe")
    svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="m3", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)

    async def _poll_mentions(**_kwargs):
        item = {"owner": "acme", "repo": "portal", "comment_kind": "issue_comment", "context_type": "issue", "issue_number": 1, "comment_id": 1, "body": "@efp-agent", "mentioned_account": "efp-agent", "mentioned_logins": ["efp-agent"], "source_event": "poll.issue_comment", "source_payload": {}}
        return ([item], {"poll_cursors": {"issue_comment": {"last_seen_updated_at": "2026-01-01T00:00:00Z", "last_seen_comment_id": 1}}})

    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)
    first = await svc.run_rule_once(rule.id)
    second = await svc.run_rule_once(rule.id)
    assert first.created_task_count == 1
    assert second.created_task_count == 0
    assert second.skipped_count == 1
    assert db.query(AgentTask).count() == 1


def test_github_comment_mention_capability_gate_blocks_missing_add_comment():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-cap-add")
    cp = CapabilityProfile(name="no-add", allowed_external_systems_json='["github"]', allowed_actions_json='["read_only"]')
    db.add(cp); db.commit(); db.refresh(cp)
    agent.capability_profile_id = cp.id
    db.add(agent); db.commit()
    svc = AutomationRuleService(db)
    with pytest.raises(Exception) as exc:
        svc.create_rule(AutomationRuleCreate(name="m4", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    assert "does not allow" in str(exc.value)


def test_github_comment_mention_capability_gate_blocks_missing_reply_review_comment_for_same_surface():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-cap-reply")
    cp = CapabilityProfile(name="no-reply", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment"]')
    db.add(cp); db.commit(); db.refresh(cp)
    agent.capability_profile_id = cp.id
    db.add(agent); db.commit()
    svc = AutomationRuleService(db)
    with pytest.raises(Exception) as exc:
        svc.create_rule(AutomationRuleCreate(name="m5", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["pull_request_review_comment"]}, trigger_config={"mention_target": "efp-agent"}, task_input_defaults={"reply_mode": "same_surface"}), current_user_id=user.id)
    assert "does not allow" in str(exc.value)


@pytest.mark.anyio
async def test_github_comment_mention_task_creation_failure_marks_event_failed(monkeypatch):
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-fail-event")
    svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="m6", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)

    async def _poll_mentions(**_kwargs):
        return ([{"owner": "acme", "repo": "portal", "comment_kind": "issue_comment", "context_type": "issue", "issue_number": 1, "comment_id": 9, "mentioned_account": "efp-agent", "source_event": "poll.issue_comment", "source_payload": {}}], {"poll_cursors": {"issue_comment": {"last_seen_updated_at": "2026-01-01T00:00:00Z", "last_seen_comment_id": 9}}})

    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)
    await svc.run_rule_once(rule.id)
    event = AutomationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "failed"


def test_create_github_comment_mention_rule_allows_commit_comment_when_capability_allows():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-commit-ok")
    cp = CapabilityProfile(name="commit-ok", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment","reply_review_comment","add_commit_comment"]')
    db.add(cp); db.commit(); db.refresh(cp)
    agent.capability_profile_id = cp.id
    db.add(agent); db.commit()
    svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="mc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    assert rule.id


def test_create_github_comment_mention_rule_blocks_commit_comment_without_commit_adapter():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-commit-no")
    cp = CapabilityProfile(name="commit-no", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment","reply_review_comment"]')
    db.add(cp); db.commit(); db.refresh(cp)
    agent.capability_profile_id = cp.id
    db.add(agent); db.commit()
    svc = AutomationRuleService(db)
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="mc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)


@pytest.mark.anyio
async def test_run_once_github_comment_mention_commit_comment_creates_triggered_event_task(monkeypatch):
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-run-commit")
    svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="mcc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    async def _poll_mentions(**_kwargs):
        return ([{"owner": "acme", "repo": "portal", "comment_kind": "commit_comment", "context_type": "commit", "comment_id": 3, "commit_id": "abc", "commit_sha": "abc", "body": "@efp-agent", "mentioned_account": "efp-agent", "source_event": "poll.commit_comment", "source_payload": {}}], {"poll_cursors": {"commit_comment": {"last_seen_updated_at": "2026-01-01T00:00:00Z", "last_seen_comment_id": 3}}})
    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)
    result = await svc.run_rule_once(rule.id)
    assert result.created_task_count == 1
    payload_obj = json.loads(db.query(AgentTask).one().input_payload_json)
    assert payload_obj["comment_kind"] == "commit_comment"
    assert payload_obj["commit_id"] == "abc"


def test_create_org_scope_rule_validates_repo_selector():
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-org-scope")
    svc = AutomationRuleService(db)
    ok = svc.create_rule(AutomationRuleCreate(name="org", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode": "org", "owner": "acme", "repo_selector": {"include": ["api-*"], "exclude": ["old-*"], "include_forks": False, "include_archived": False}}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    assert ok.id
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="org-bad", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode": "org", "owner": "acme", "repo_selector": {"include": "api-*"}}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)


@pytest.mark.anyio
async def test_run_once_org_scope_polls_multiple_repos_and_stores_per_repo_cursors(monkeypatch):
    db = _session()
    user, agent = _create_runtime_and_agent(db, "u-org-run")
    svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="org-run", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode": "org", "owner": "acme", "repo_selector": {"include": ["*"]}}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    async def _list_org_repositories(**_kwargs):
        return [{"owner": "acme", "repo": "portal", "full_name": "acme/portal"}, {"owner": "acme", "repo": "api", "full_name": "acme/api"}]
    monkeypatch.setattr(svc.comment_mention_poller, "list_org_repositories", _list_org_repositories)
    async def _poll_mentions(**kwargs):
        r = kwargs["repo"]
        return ([{"owner": "acme", "repo": r, "comment_kind": "issue_comment", "context_type": "issue", "issue_number": 1, "comment_id": 1 if r=="portal" else 2, "body": "@efp-agent", "mentioned_account": "efp-agent", "source_event": "poll.issue_comment", "source_payload": {}}], {"poll_cursors": {"issue_comment": {"last_seen_updated_at": "2026-01-01T00:00:00Z", "last_seen_comment_id": 2}}})
    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)
    result = await svc.run_rule_once(rule.id)
    assert result.created_task_count == 2
    refreshed = AutomationRuleRepository(db).get(rule.id)
    state = json.loads(refreshed.state_json)
    assert "acme/portal" in state.get("poll_cursors_by_repo", {})
    assert "acme/api" in state.get("poll_cursors_by_repo", {})

def test_create_github_comment_mention_rule_accepts_commit_comment_initial_tail_pages():
    db = _session(); user, agent = _create_runtime_and_agent(db, "u-tail-ok"); svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="tail", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, trigger_config={"mention_target": "efp-agent"}, schedule={"interval_seconds": 60, "commit_comment_initial_tail_pages": 3}), current_user_id=user.id)
    assert rule.id


def test_create_github_comment_mention_rule_rejects_bad_commit_tail_pages():
    db = _session(); user, agent = _create_runtime_and_agent(db, "u-tail-bad"); svc = AutomationRuleService(db)
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="tail0", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"}, schedule={"interval_seconds": 60, "commit_comment_initial_tail_pages": 0}), current_user_id=user.id)
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="tail50", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"}, schedule={"interval_seconds": 60, "commit_comment_initial_tail_pages": 50}), current_user_id=user.id)


@pytest.mark.anyio
async def test_run_once_github_comment_mention_passes_commit_tail_pages_to_poller(monkeypatch):
    db = _session(); user, agent = _create_runtime_and_agent(db, "u-tail-pass"); svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="tail-pass", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal"}, trigger_config={"mention_target": "efp-agent"}, schedule={"interval_seconds": 60, "commit_comment_initial_tail_pages": 3}), current_user_id=user.id)
    seen = {}
    async def _poll_mentions(**kwargs):
        seen.update(kwargs)
        return ([], {"poll_cursors": {}})
    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    await svc.run_rule_once(rule.id)
    assert seen.get("commit_comment_initial_tail_pages") == 3

def test_create_github_comment_mention_rule_allows_discussion_comment_when_capability_allows():
    db = _session(); user, agent = _create_runtime_and_agent(db, "u-disc-ok"); svc = AutomationRuleService(db)
    cp = CapabilityProfile(name="disc-ok", allowed_external_systems_json='["github"]', allowed_actions_json='["add_discussion_comment"]')
    db.add(cp); db.commit(); db.refresh(cp); agent.capability_profile_id = cp.id; db.add(agent); db.commit()
    rule = svc.create_rule(AutomationRuleCreate(name="d", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["discussion_comment"]}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    assert rule.id


def test_create_github_comment_mention_rule_blocks_discussion_comment_without_adapter():
    db = _session(); user, agent = _create_runtime_and_agent(db, "u-disc-no"); svc = AutomationRuleService(db)
    cp = CapabilityProfile(name="disc-no", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment"]')
    db.add(cp); db.commit(); db.refresh(cp); agent.capability_profile_id = cp.id; db.add(agent); db.commit()
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="d", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["discussion_comment"]}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)


@pytest.mark.anyio
async def test_run_once_github_comment_mention_discussion_comment_creates_triggered_event_task(monkeypatch):
    db = _session(); user, agent = _create_runtime_and_agent(db, "u-disc-run"); svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name="d", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"owner": "acme", "repo": "portal", "surfaces": ["discussion_comment"]}, trigger_config={"mention_target": "efp-agent"}), current_user_id=user.id)
    async def _poll_mentions(**_kwargs):
        return ([{"owner": "acme", "repo": "portal", "comment_kind": "discussion_comment", "context_type": "discussion", "discussion_id": "D1", "discussion_number": 1, "discussion_comment_id": "DC1", "comment_id": "DC1", "body": "@efp-agent", "mentioned_account": "efp-agent", "source_event": "poll.discussion_comment", "source_payload": {}}], {"poll_cursors": {"discussion_comment": {"last_seen_updated_at": "2026-01-01T00:00:00Z", "last_seen_comment_id": "DC1"}}})
    monkeypatch.setattr(svc.comment_mention_poller, "poll_mentions", _poll_mentions)
    monkeypatch.setattr(svc.dispatcher, "dispatch_task_in_background", lambda _task_id: None)
    await svc.run_rule_once(rule.id)
    payload = json.loads(db.query(AgentTask).one().input_payload_json)
    assert payload["comment_kind"] == "discussion_comment"
    assert payload["discussion_id"] == "D1"
    assert payload["discussion_comment_id"] == "DC1"
    assert payload["source_event"] == "poll.discussion_comment"
    assert payload["source_kind"] == "github.mention"

@pytest.mark.anyio
async def test_run_once_org_scope_does_not_duplicate_repos_when_repo_count_less_than_max(monkeypatch):
    db = _session()
    user = User(username="u-org-nodup", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "secret"}, "allowed_actions": ["adapter:github:add_comment", "adapter:github:reply_review_comment"]}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)
    svc = AutomationRuleService(db)
    rule = svc.create_rule(AutomationRuleCreate(name='org', target_agent_id=agent.id, task_template_id='github_comment_mention', scope={'mode':'org','owner':'acme','repo_selector':{'include':['*']}}, trigger_config={'mention_target':'efp-agent'}), current_user_id=user.id)
    async def list_org_repositories(**_): return [{'owner':'acme','repo':'a','full_name':'acme/a'},{'owner':'acme','repo':'b','full_name':'acme/b'}]
    called=[]
    async def poll_mentions(**kwargs): called.append(kwargs['repo']); return [], {'poll_cursors':{}}
    monkeypatch.setattr(svc.comment_mention_poller, 'list_org_repositories', list_org_repositories)
    monkeypatch.setattr(svc.comment_mention_poller, 'poll_mentions', poll_mentions)
    await svc.run_rule_once(rule.id)
    assert len(called)==2
    assert set(called)=={'a','b'}

def test_create_account_notifications_mode_rule_succeeds():
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-create"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"],"notification_reasons":["mention","team_mention"],"repo_selector":{"include":["*"],"exclude":[]}}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    assert rule.id

def test_create_account_notifications_mode_rejects_bad_notification_reasons():
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-bad"); svc=AutomationRuleService(db)
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","notification_reasons":"mention"}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    with pytest.raises(Exception):
        svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","notification_reasons":[""]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)

@pytest.mark.anyio
async def test_run_once_account_notifications_passes_notification_start_page(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-start"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_notifications":{"next_notification_page":3}}); db.add(rule); db.commit()
    seen={}
    async def l(**kw): seen.update(kw); return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","next_notification_page":None,"hit_page_limit":False}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    await svc.run_rule_once(rule.id)
    assert seen.get("start_page")==3

@pytest.mark.anyio
async def test_run_once_account_notifications_respects_max_repos_per_run(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-limit"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"max_repos_per_run":2}), current_user_id=user.id)
    async def l(**_):
        ns=[{"repository_full_name":f"acme/r{i}","source_payload":{"repository":{}},"reason":"mention"} for i in range(5)]
        return ns,{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None}
    called=[]
    async def p(**kw): called.append(kw["repo"]); return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    res=await svc.run_rule_once(rule.id)
    assert len(called)==2
    refreshed=svc.repo.get(rule.id); st=json.loads(refreshed.state_json)
    assert len(st.get("account_candidate_queue") or [])==3

@pytest.mark.anyio
async def test_run_once_account_notifications_updates_account_and_per_repo_cursors(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-state"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    async def l(**_): return [{"repository_full_name":"acme/a","source_payload":{"repository":{}},"reason":"mention"}], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None}
    async def p(**_): return [], {"poll_cursors":{"issue_comment":{"last_seen_updated_at":"2026-01-01T00:00:00Z","last_seen_comment_id":1}}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert st["account_notifications"]["last_seen_notification_updated_at"]
    assert st["poll_cursors_by_repo"]["acme/a"]["issue_comment"]["last_seen_comment_id"]==1


@pytest.mark.anyio
async def test_run_once_account_notifications_persists_unpolled_candidate_queue(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-q"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"max_repos_per_run":2}), current_user_id=user.id)
    async def l(**_):
        ns=[{"repository_full_name":f"acme/r{i}","notification_id":str(i),"reason":"mention","subject_type":"Issue","subject_url":"u","updated_at":"2026-01-01T00:00:00Z","source_payload":{"repository":{}}} for i in range(5)]
        return ns,{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None,"scan_since":None}
    called=[]
    async def p(**kw): called.append(kw["repo"]); return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert len(st.get("account_candidate_queue") or [])==3
    assert len(called)==2 and len(set(called))==2

@pytest.mark.anyio
async def test_run_once_account_notifications_drains_existing_candidate_queue_before_dropping_it(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-drain"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"max_repos_per_run":1}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"},{"full_name":"acme/b"}]}); db.add(rule); db.commit()
    async def l(**_): return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None,"scan_since":None}
    called=[]
    async def p(**kw): called.append(kw["repo"]); return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    assert called==["a"]
    assert len(json.loads(svc.repo.get(rule.id).state_json).get("account_candidate_queue"))==1
    await svc.run_rule_once(rule.id)
    assert called==["a","b"]
    assert json.loads(svc.repo.get(rule.id).state_json).get("account_candidate_queue")==[]

@pytest.mark.anyio
async def test_run_once_account_notifications_keeps_failed_repo_in_queue(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-failq"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"}]}); db.add(rule); db.commit()
    async def l(**_): return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None,"scan_since":None}
    async def p(**_): raise RuntimeError("x")
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    assert json.loads(svc.repo.get(rule.id).state_json).get("account_candidate_queue")[0]["full_name"]=="acme/a"

@pytest.mark.anyio
async def test_run_once_account_notifications_uses_scan_since_and_start_page(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-sc"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_notifications":{"next_notification_page":3,"scan_since":"2026-01-01T00:00:00Z"}}); db.add(rule); db.commit()
    seen={}
    async def l(**kw): seen.update(kw); return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None,"scan_since":None}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    await svc.run_rule_once(rule.id)
    assert seen.get("start_page")==3 and seen.get("scan_since") is not None

@pytest.mark.anyio
async def test_run_once_account_notifications_applies_archived_fork_filters_from_payload(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-flt"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"],"repo_selector":{"include":["*"],"include_archived":False,"include_forks":False}}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    async def l(**_):
        return [
            {"repository_full_name":"acme/a","notification_id":"1","reason":"mention","subject_type":"Issue","subject_url":"u","updated_at":"2026-01-01T00:00:00Z","source_payload":{"repository":{"archived":True}}},
            {"repository_full_name":"acme/b","notification_id":"2","reason":"mention","subject_type":"Issue","subject_url":"u","updated_at":"2026-01-01T00:00:00Z","source_payload":{"repository":{"fork":True}}},
        ],{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None,"scan_since":None}
    called=[]
    async def p(**kw): called.append(kw["repo"]); return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    assert called==[]

@pytest.mark.anyio
async def test_run_once_account_notifications_uses_scan_since_and_start_page_when_completed_cursor_empty(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-c-empty"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_notifications":{"next_notification_page":2,"scan_since":None,"last_seen_notification_updated_at":None}}); db.add(rule); db.commit()
    seen={}
    async def l(**kw): seen.update(kw); return [], {"last_seen_notification_updated_at":None,"hit_page_limit":True,"next_notification_page":3,"scan_since":None}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    await svc.run_rule_once(rule.id)
    assert seen.get("start_page")==2 and seen.get("since") is None and seen.get("scan_since") is None

@pytest.mark.anyio
async def test_run_once_account_notifications_persists_notif_patch_with_null_completed_cursor(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-null"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    async def l(**_): return [], {"last_seen_notification_updated_at":None,"hit_page_limit":True,"next_notification_page":2,"scan_since":None}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert st["account_notifications"]["last_seen_notification_updated_at"] is None
    assert st["account_notifications"]["next_notification_page"]==2

@pytest.mark.anyio
async def test_run_once_account_notifications_queue_survives_after_notification_scan_completed(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-acc-survive"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"max_repos_per_run":1}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"},{"full_name":"acme/b"},{"full_name":"acme/c"}]}); db.add(rule); db.commit()
    async def l(**_): return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False,"next_notification_page":None,"scan_since":None}
    async def p(**_): return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert len(st.get("account_candidate_queue") or [])==2


@pytest.mark.anyio
async def test_run_once_account_notifications_keeps_repo_in_queue_when_surface_poll_incomplete(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-incomp"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"}]}); db.add(rule); db.commit()
    async def l(**_): return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False}
    async def p(**_): return [], {"poll_cursors":{"issue_comment":{"last_seen_updated_at":"2026-01-01T00:00:00Z","last_seen_comment_id":123,"hit_page_limit":True}}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l); monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert st["account_candidate_queue"][0]["full_name"]=="acme/a"
    assert st["poll_cursors_by_repo"]["acme/a"]["issue_comment"]["hit_page_limit"] is True

@pytest.mark.anyio
async def test_run_once_account_notifications_removes_repo_from_queue_when_poll_complete(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-comp"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"}]}); db.add(rule); db.commit()
    async def l(**_): return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False}
    async def p(**_): return [], {"poll_cursors":{"issue_comment":{"last_seen_updated_at":"2026-01-01T00:00:00Z","last_seen_comment_id":123,"hit_page_limit":False}}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l); monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert st.get("account_candidate_queue")==[]

@pytest.mark.anyio
async def test_run_once_account_notifications_keeps_repo_in_queue_when_discussion_poll_incomplete(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-discinc"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["discussion_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"}]}); db.add(rule); db.commit()
    async def l(**_): return [], {"last_seen_notification_updated_at":"2026-01-01T00:00:00Z","hit_page_limit":False}
    async def p(**_): return [], {"poll_cursors":{"discussion_comment":{"last_seen_updated_at":"2026-01-01T00:00:00Z","last_seen_comment_id":"1","hit_page_limit":True}}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l); monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert st["account_candidate_queue"][0]["full_name"]=="acme/a"

def test_github_comment_mention_repo_poll_incomplete_helper():
    db=_session(); svc=AutomationRuleService(db)
    assert svc._github_comment_mention_repo_poll_incomplete({"issue_comment":{"hit_page_limit":True}}, ["issue_comment"]) is True
    assert svc._github_comment_mention_repo_poll_incomplete({"issue_comment":{"hit_page_limit":False}}, ["issue_comment","discussion_comment"]) is False

@pytest.mark.anyio
async def test_run_once_account_notifications_rotates_incomplete_repo_to_queue_tail(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-rot-inc"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"max_repos_per_run":1}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"},{"full_name":"acme/b"}]}); db.add(rule); db.commit()
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications', lambda **_: __import__('asyncio').sleep(0, result=([],{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z"})))
    async def p(**_): return [], {"poll_cursors":{"issue_comment":{"hit_page_limit":True}}}
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    q=[e["full_name"] for e in json.loads(svc.repo.get(rule.id).state_json)["account_candidate_queue"]]
    assert q==["acme/b","acme/a"]

@pytest.mark.anyio
async def test_run_once_account_notifications_rotates_failed_repo_to_queue_tail(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-rot-fail"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"max_repos_per_run":1}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_candidate_queue":[{"full_name":"acme/a"},{"full_name":"acme/b"}]}); db.add(rule); db.commit()
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications', lambda **_: __import__('asyncio').sleep(0, result=([],{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z"})))
    async def p(**_): raise RuntimeError('x')
    monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    q=[e["full_name"] for e in json.loads(svc.repo.get(rule.id).state_json)["account_candidate_queue"]]
    assert q==["acme/b","acme/a"]

@pytest.mark.anyio
async def test_run_once_account_notifications_repo_selector_matches_full_name_patterns(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-full-inc"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"],"repo_selector":{"include":["acme/*"]}}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    async def l(**_): return [{"repository_full_name":"acme/a","source_payload":{"repository":{}},"reason":"mention"},{"repository_full_name":"other/b","source_payload":{"repository":{}},"reason":"mention"}],{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z"}
    called=[]
    async def p(**kw): called.append(f"{kw['owner']}/{kw['repo']}"); return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l); monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    assert called==["acme/a"]

@pytest.mark.anyio
async def test_run_once_account_notifications_repo_selector_excludes_full_name_patterns(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-full-ex"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"],"repo_selector":{"include":["*"],"exclude":["acme/private-*"]}}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    async def l(**_): return [{"repository_full_name":"acme/private-api","source_payload":{"repository":{}},"reason":"mention"},{"repository_full_name":"acme/public-api","source_payload":{"repository":{}},"reason":"mention"}],{"last_seen_notification_updated_at":"2026-01-01T00:00:00Z"}
    called=[]
    async def p(**kw): called.append(f"{kw['owner']}/{kw['repo']}"); return [], {"poll_cursors":{}}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l); monkeypatch.setattr(svc.comment_mention_poller,'poll_mentions',p)
    await svc.run_rule_once(rule.id)
    assert called==["acme/public-api"]

@pytest.mark.anyio
async def test_run_once_account_notifications_passes_completed_and_query_since_separately(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-sep"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}, schedule={"interval_seconds":60,"overlap_seconds":120}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_notifications":{"last_seen_notification_updated_at":"2026-01-10T00:00:00Z"}}); db.add(rule); db.commit()
    seen={}
    async def l(**kw): seen.update(kw); return [], {"last_seen_notification_updated_at":"2026-01-10T00:00:00Z"}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    await svc.run_rule_once(rule.id)
    assert seen.get("completed_since").isoformat().startswith("2026-01-10T00:00:00")
    assert seen.get("query_since").isoformat().startswith("2026-01-09T23:58:00")
    assert seen.get("scan_since") is None

@pytest.mark.anyio
async def test_run_once_account_notifications_continuation_preserves_completed_cursor(monkeypatch):
    db=_session(); user,agent=_create_runtime_and_agent(db,"u-cont"); svc=AutomationRuleService(db)
    rule=svc.create_rule(AutomationRuleCreate(name="acc", target_agent_id=agent.id, task_template_id="github_comment_mention", scope={"mode":"account_notifications","surfaces":["issue_comment"]}, trigger_config={"mention_target":"efp-agent"}), current_user_id=user.id)
    rule.state_json=json.dumps({"account_notifications":{"last_seen_notification_updated_at":"2026-01-10T00:00:00Z","scan_since":"2026-01-09T23:58:00Z","next_notification_page":3,"hit_page_limit":True}}); db.add(rule); db.commit()
    seen={}
    async def l(**kw):
        seen.update(kw)
        return [], {"last_seen_notification_updated_at":"2026-01-10T00:00:00Z","scan_since":"2026-01-09T23:58:00Z","next_notification_page":4,"hit_page_limit":True}
    monkeypatch.setattr(svc.comment_mention_poller,'list_account_notifications',l)
    await svc.run_rule_once(rule.id)
    st=json.loads(svc.repo.get(rule.id).state_json)
    assert seen.get("completed_since").isoformat().startswith("2026-01-10T00:00:00")
    assert seen.get("scan_since").isoformat().startswith("2026-01-09T23:58:00")
    assert seen.get("start_page")==3
    assert st["account_notifications"]["last_seen_notification_updated_at"]=="2026-01-10T00:00:00Z"
