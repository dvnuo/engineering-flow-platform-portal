import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.models.agent_task import AgentTask
from app.repositories.delegation_rule_repo import DelegationRuleRepository
from app.schemas.delegation_rule import DelegationRuleCreate
from app.services.delegation_source_pollers import DelegationSourcePoller, SourcePollResult
from app.services.delegation_rule_service import DelegationRuleService


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _mk_agent(user_id: int, runtime_profile_id: str | None = None):
    return Agent(
        name="a",
        owner_user_id=user_id,
        visibility="private",
        status="running",
        image="example/image:latest",
        runtime_profile_id=runtime_profile_id,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp",
        deployment_name="d",
        service_name="s",
        pvc_name="p",
        endpoint_path="/",
        agent_type="workspace",
    )


def _create_user_agent(db: Session, *, username: str = "u"):
    user = User(username=username, password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    profile = RuntimeProfile(
        owner_user_id=user.id,
        name=f"rp-{username}",
        config_json=json.dumps(
            {
                "github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "gh-secret"},
                "jira": {
                    "enabled": True,
                    "instances": [
                        {
                            "url": "https://jira.local",
                            "username": "bot@example.com",
                            "token": "jira-secret",
                            "enabled": True,
                            "api_version": "2",
                        }
                    ],
                },
            }
        ),
        is_default=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    agent = _mk_agent(user.id, profile.id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return user, agent


def test_jira_mention_jql_literal_preserves_spaces_and_escapes_quotes():
    assert DelegationSourcePoller._jira_jql_text_literal("Alice Smith") == "Alice Smith"
    assert DelegationSourcePoller._jira_jql_text_literal('Alice "Bot"') == 'Alice \\"Bot\\"'


def _create_rule(db: Session, user, agent, source: str = "github_pr_review", skill_name: str = "selected-skill"):
    svc = DelegationRuleService(db)
    rule = svc.create_rule(
        DelegationRuleCreate(
            name=f"rule-{source}",
            target_agent_id=agent.id,
            skill_name=skill_name,
            source=source,
            interval_seconds=60,
        ),
        current_user_id=user.id,
    )
    return svc, rule


def _source_item(source: str) -> dict:
    if source == "github_pr_review":
        return {
            "source": source,
            "provider": "github",
            "dedupe_key": "github-pr:acme/portal:1:sha1",
            "version_key": "sha1",
            "source_url": "https://github.com/acme/portal/pull/1",
            "task_content": "Review this GitHub PR:\nhttps://github.com/acme/portal/pull/1",
            "represented_identity": "octocat",
            "source_payload": {"pull_number": 1},
            "reply_target": {"provider": "github", "kind": "pr_comment", "owner": "acme", "repo": "portal", "pull_number": 1},
        }
    if source == "github_pr_mention":
        return {
            "source": source,
            "provider": "github",
            "dedupe_key": "github-mention:comment:100",
            "version_key": "100",
            "source_url": "https://github.com/acme/portal/pull/2",
            "source_comment": "@octocat please handle this",
            "task_content": "You are responding as @octocat.\nGitHub PR:\nhttps://github.com/acme/portal/pull/2\n\nComment:\n@octocat please handle this",
            "represented_identity": "@octocat",
            "source_payload": {"comment_id": 100},
            "reply_target": {"provider": "github", "kind": "pr_comment", "owner": "acme", "repo": "portal", "pull_number": 2},
        }
    if source == "jira_assignee":
        return {
            "source": source,
            "provider": "jira",
            "dedupe_key": "jira-assignee:ENG-1:2026-01-01",
            "version_key": "2026-01-01",
            "source_url": "https://jira.local/browse/ENG-1",
            "task_content": "Work on this Jira issue:\nhttps://jira.local/browse/ENG-1",
            "represented_identity": "Bot User",
            "source_payload": {"issue_key": "ENG-1"},
            "reply_target": {"provider": "jira", "kind": "issue_comment", "issue_key": "ENG-1"},
        }
    if source == "jira_mention":
        return {
            "source": source,
            "provider": "jira",
            "dedupe_key": "jira-mention:ENG-2:comment:200",
            "version_key": "200",
            "source_url": "https://jira.local/browse/ENG-2",
            "source_comment": "Bot User please check this",
            "task_content": "You are responding as Bot User.\nJira issue:\nhttps://jira.local/browse/ENG-2\n\nComment:\nBot User please check this",
            "represented_identity": "Bot User",
            "source_payload": {"comment_id": 200},
            "reply_target": {"provider": "jira", "kind": "issue_comment", "issue_key": "ENG-2"},
        }
    raise AssertionError(source)


def _run_once_with_items(svc: DelegationRuleService, rule_id: str, items: list[dict]):
    async def _poll(_db, _rule):
        return SourcePollResult(items=items)

    svc.source_poller.poll = _poll
    return asyncio.run(svc.run_rule_once(rule_id, triggered_by="test"))


@pytest.mark.parametrize(
    "source,expected_fragments",
    [
        ("github_pr_review", ["Review this GitHub PR:", "https://github.com/acme/portal/pull/1"]),
        ("github_pr_mention", ["You are responding as @octocat.", "https://github.com/acme/portal/pull/2", "@octocat please handle this"]),
        ("jira_assignee", ["Work on this Jira issue:", "https://jira.local/browse/ENG-1"]),
        ("jira_mention", ["You are responding as Bot User.", "https://jira.local/browse/ENG-2", "Bot User please check this"]),
    ],
)
def test_run_once_creates_agent_async_task_for_each_source(source, expected_fragments):
    db = _session()
    user, agent = _create_user_agent(db, username=f"u-{source}")
    svc, rule = _create_rule(db, user, agent, source=source, skill_name="custom-skill")
    dispatched = []
    svc.dispatcher.dispatch_task_in_background = lambda task_id: dispatched.append(task_id)

    result = _run_once_with_items(svc, rule.id, [_source_item(source)])

    assert result.found_count == 1
    assert result.created_task_count == 1
    task = db.query(AgentTask).one()
    assert task.source == "delegation"
    assert task.task_type == "agent_async_task"
    assert task.task_family == "agent_task"
    assert task.provider == ("github" if source.startswith("github") else "jira")
    assert task.trigger == source
    assert task.skill_name == "custom-skill"
    assert task.assignee_agent_id == agent.id
    assert dispatched == [task.id]
    payload = json.loads(task.input_payload_json)
    assert payload["schema"] == "agent_async_task.v1"
    assert payload["skill_name"] == "custom-skill"
    assert payload["root_task_id"] == task.id
    assert payload["parent_task_id"] is None
    assert task.task_session_id == f"agent-task:{task.id}"
    assert payload["task_session_id"] == task.task_session_id
    assert not payload["task_session_id"].startswith("delegation:")
    for fragment in expected_fragments:
        assert fragment in payload["user_task"]
    assert payload["delegation_rule_id"] == rule.id
    assert payload["delegation"]["delegation_rule_id"] == rule.id
    assert payload["delegation"]["source"] == source
    assert payload["delegation"]["reply_target"]
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "task_created"
    assert event.task_id == task.id


def test_dedupe_same_source_item_does_not_create_duplicate_task():
    db = _session()
    user, agent = _create_user_agent(db, username="u-dedupe")
    svc, rule = _create_rule(db, user, agent, source="github_pr_review")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    item = _source_item("github_pr_review")

    first = _run_once_with_items(svc, rule.id, [item])
    second = _run_once_with_items(svc, rule.id, [item])

    assert first.created_task_count == 1
    assert second.created_task_count == 0
    assert second.skipped_count == 1
    assert db.query(AgentTask).count() == 1
    assert len(DelegationRuleRepository(db).list_events(rule.id, 10)) == 1


def test_reply_sent_when_task_done(monkeypatch):
    db = _session()
    user, agent = _create_user_agent(db, username="u-reply")
    svc, rule = _create_rule(db, user, agent, source="github_pr_mention")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _run_once_with_items(svc, rule.id, [_source_item("github_pr_mention")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.summary = "Fallback summary"
    task.result_payload_json = json.dumps({"status": "success", "output_payload": {"summary": "Reply body"}})
    db.add(task)
    db.commit()

    captured = {}

    async def _send_reply(_db, *, rule, event, reply_target, text):
        captured["rule_id"] = rule.id
        captured["event_id"] = event.id
        captured["reply_target"] = reply_target
        captured["text"] = text

    svc.reply_service.send_reply = _send_reply
    result = _run_once_with_items(svc, rule.id, [])

    assert result.created_task_count == 0
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_sent"
    assert captured["reply_target"]["kind"] == "pr_comment"
    assert "Reply body" in captured["text"]
    assert "<!-- efp:delegation-reply " in captured["text"]
    assert f"delegation_id={rule.id}" in captured["text"]
    assert f"event_id={event.id}" in captured["text"]
    assert "efp:auto-reply" not in captured["text"]


def test_reply_failure_marks_event_failed():
    db = _session()
    user, agent = _create_user_agent(db, username="u-reply-fail")
    svc, rule = _create_rule(db, user, agent, source="jira_assignee")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _run_once_with_items(svc, rule.id, [_source_item("jira_assignee")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.result_payload_json = json.dumps({"status": "success", "final_response": "Done"})
    db.add(task)
    db.commit()

    async def _send_reply(*_args, **_kwargs):
        raise RuntimeError("reply unavailable")

    svc.reply_service.send_reply = _send_reply
    _run_once_with_items(svc, rule.id, [])

    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_failed"
    assert "reply unavailable" in event.error_message


def test_run_once_failure_schedules_next_run():
    db = _session()
    user, agent = _create_user_agent(db, username="u-failure")
    svc, rule = _create_rule(db, user, agent, source="github_pr_review")

    async def _poll(_db, _rule):
        raise RuntimeError("poll failure")

    svc.source_poller.poll = _poll
    before = datetime.utcnow()
    with pytest.raises(Exception):
        asyncio.run(svc.run_rule_once(rule.id))

    refreshed_rule = DelegationRuleRepository(db).get(rule.id)
    runs = DelegationRuleRepository(db).list_runs(rule.id, 5)
    assert runs[0].status == "failed"
    assert refreshed_rule.last_run_at is not None
    assert refreshed_rule.next_run_at is not None
    assert refreshed_rule.next_run_at > before
    assert refreshed_rule.locked_until is None


def test_get_or_create_event_by_dedupe_handles_unique_conflict():
    db = _session()
    user, agent = _create_user_agent(db, username="u-event")
    _svc, rule = _create_rule(db, user, agent, source="github_pr_review")
    repo = DelegationRuleRepository(db)
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
