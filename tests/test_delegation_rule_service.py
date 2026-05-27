import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

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


def _stub_start_reaction(svc: DelegationRuleService, calls: list | None = None, reaction_id: int = 9001):
    async def _add_github_reaction(_db, *, rule, reaction_target, content="eyes"):
        if calls is not None:
            calls.append({"rule_id": rule.id, "reaction_target": reaction_target, "content": content})
        api_path = reaction_target["api_path"]
        return {
            "provider": "github",
            "content": content,
            "api_path": api_path,
            "reaction_id": reaction_id,
            "cleanup_api_path": f"{api_path.rstrip('/')}/{reaction_id}",
            "target": dict(reaction_target),
        }

    svc.reply_service.add_github_reaction = _add_github_reaction


def _stub_reaction_cleanup(svc: DelegationRuleService, calls: list | None = None):
    async def _delete_github_reaction(_db, *, rule, cleanup_api_path=None, portal_start_reaction=None):
        path = cleanup_api_path or portal_start_reaction["cleanup_api_path"]
        if calls is not None:
            calls.append({"rule_id": rule.id, "cleanup_api_path": path, "portal_start_reaction": portal_start_reaction})
        return {"provider": "github", "status": "deleted", "cleanup_api_path": path}

    svc.reply_service.delete_github_reaction = _delete_github_reaction


def _stub_jira_start_comment(svc: DelegationRuleService, calls: list | None = None, comment_id: str = "5001"):
    async def _add_jira_start_comment(
        _db,
        rule,
        reply_target,
        *,
        source,
        source_url=None,
        source_comment=None,
        event=None,
        marker=None,
    ):
        issue_key = reply_target["issue_key"]
        if calls is not None:
            calls.append(
                {
                    "rule_id": rule.id,
                    "reply_target": reply_target,
                    "source": source,
                    "source_url": source_url,
                    "source_comment": source_comment,
                }
            )
        _ = event, marker
        content_lines = [
            "Automated EFP delegation run has started.",
            "",
            f"Source: {source}",
            f"Issue: {issue_key}",
        ]
        if source_url:
            content_lines.append(f"Link: {source_url}")
        content = "\n".join(content_lines)
        return {
            "provider": "jira",
            "status": "created",
            "issue_key": issue_key,
            "comment_id": comment_id,
            "api_path": f"/rest/api/2/issue/{issue_key}/comment",
            "content": content,
        }

    svc.reply_service.add_jira_start_comment = _add_jira_start_comment


def _stub_jira_start_comment_failure(svc: DelegationRuleService, message: str = "jira comment unavailable"):
    async def _add_jira_start_comment(*_args, **_kwargs):
        raise RuntimeError(message)

    svc.reply_service.add_jira_start_comment = _add_jira_start_comment


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
            "source_payload": {
                "pull_request": {
                    "owner": "acme",
                    "repo": "portal",
                    "number": 1,
                    "url": "https://github.com/acme/portal/pull/1",
                    "title": "Improve portal",
                    "head_sha": "sha1",
                    "base_sha": "base1",
                    "author": "alice",
                }
            },
            "reply_target": {"provider": "github", "kind": "pr_comment", "owner": "acme", "repo": "portal", "pull_number": 1},
            "reaction_target": {
                "provider": "github",
                "kind": "pull_request",
                "owner": "acme",
                "repo": "portal",
                "pull_number": 1,
                "html_url": "https://github.com/acme/portal/pull/1",
                "api_path": "/repos/acme/portal/issues/1/reactions",
            },
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
            "source_payload": {
                "pull_request": {
                    "owner": "acme",
                    "repo": "portal",
                    "number": 2,
                    "url": "https://github.com/acme/portal/pull/2",
                    "head_sha": "sha2",
                    "base_sha": "base2",
                },
                "comment": {
                    "kind": "issue_comment",
                    "id": 100,
                    "body": "@octocat please handle this",
                    "html_url": "https://github.com/acme/portal/pull/2#issuecomment-100",
                    "author": "alice",
                },
            },
            "reply_target": {
                "provider": "github",
                "kind": "pr_comment",
                "owner": "acme",
                "repo": "portal",
                "pull_number": 2,
                "reply_mode": "quote_reply",
                "comment_kind": "issue_comment",
                "comment_id": 100,
                "comment_html_url": "https://github.com/acme/portal/pull/2#issuecomment-100",
                "comment_author": "alice",
                "comment_body": "@octocat please handle this",
            },
            "reaction_target": {
                "provider": "github",
                "kind": "issue_comment",
                "owner": "acme",
                "repo": "portal",
                "pull_number": 2,
                "comment_id": 100,
                "html_url": "https://github.com/acme/portal/pull/2#issuecomment-100",
                "api_path": "/repos/acme/portal/issues/comments/100/reactions",
            },
        }
    if source == "jira_assignee":
        return {
            "source": source,
            "provider": "jira",
            "dedupe_key": "jira_assignee:ENG-1",
            "version_key": "2026-01-01",
            "source_url": "https://jira.local/browse/ENG-1",
            "task_content": "Work on this Jira issue:\nhttps://jira.local/browse/ENG-1",
            "represented_identity": "Bot User",
            "source_payload": {
                "issue": {
                    "key": "ENG-1",
                    "url": "https://jira.local/browse/ENG-1",
                    "summary": "Fix flow",
                    "status": {"name": "In Progress"},
                    "reporter": {"accountId": "reporter-1", "displayName": "Reporter User"},
                    "assignee": {"accountId": "bot-1", "displayName": "Bot User"},
                }
            },
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
            "source_payload": {
                "issue": {
                    "key": "ENG-2",
                    "url": "https://jira.local/browse/ENG-2",
                    "summary": "Mention task",
                    "reporter": {"accountId": "reporter-2", "displayName": "Second Reporter"},
                },
                "comment": {
                    "id": "200",
                    "body": "Bot User please check this",
                    "author": {"accountId": "reporter-2", "displayName": "Second Reporter"},
                },
            },
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
    if source.startswith("github"):
        _stub_start_reaction(svc)
    if source.startswith("jira"):
        _stub_jira_start_comment(svc)
    source_item = _source_item(source)

    result = _run_once_with_items(svc, rule.id, [source_item])

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
    assert payload["delegation"]["source_payload"] == source_item["source_payload"]
    if "reaction_target" in source_item:
        assert payload["delegation"]["reaction_target"] == source_item["reaction_target"]
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "task_created"
    assert event.task_id == task.id
    assert json.loads(event.source_payload_json) == source_item["source_payload"]


def test_github_pr_review_task_creation_records_portal_start_reaction():
    db = _session()
    user, agent = _create_user_agent(db, username="u-review-start-reaction")
    svc, rule = _create_rule(db, user, agent, source="github_pr_review")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    reaction_calls = []
    _stub_start_reaction(svc, calls=reaction_calls, reaction_id=23456)
    source_item = _source_item("github_pr_review")

    _run_once_with_items(svc, rule.id, [source_item])

    assert len(reaction_calls) == 1
    assert reaction_calls[0]["reaction_target"] == source_item["reaction_target"]
    assert reaction_calls[0]["content"] == "eyes"
    task = db.query(AgentTask).one()
    task_payload = json.loads(task.input_payload_json)
    start_reaction = task_payload["delegation"]["portal_start_reaction"]
    assert start_reaction["reaction_id"] == 23456
    assert start_reaction["api_path"] == "/repos/acme/portal/issues/1/reactions"
    assert start_reaction["cleanup_api_path"] == "/repos/acme/portal/issues/1/reactions/23456"
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    normalized = json.loads(event.normalized_payload_json)
    assert normalized["portal_start_reaction"] == start_reaction
    assert "portal_start_reaction_error" not in normalized


def test_github_pr_mention_task_creation_records_portal_start_reaction():
    db = _session()
    user, agent = _create_user_agent(db, username="u-start-reaction")
    svc, rule = _create_rule(db, user, agent, source="github_pr_mention")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    reaction_calls = []
    _stub_start_reaction(svc, calls=reaction_calls, reaction_id=12345)
    source_item = _source_item("github_pr_mention")

    _run_once_with_items(svc, rule.id, [source_item])

    assert len(reaction_calls) == 1
    assert reaction_calls[0]["reaction_target"] == source_item["reaction_target"]
    assert reaction_calls[0]["content"] == "eyes"
    task = db.query(AgentTask).one()
    task_payload = json.loads(task.input_payload_json)
    start_reaction = task_payload["delegation"]["portal_start_reaction"]
    assert start_reaction["reaction_id"] == 12345
    assert start_reaction["api_path"] == "/repos/acme/portal/issues/comments/100/reactions"
    assert start_reaction["cleanup_api_path"] == "/repos/acme/portal/issues/comments/100/reactions/12345"
    assert start_reaction["content"] == "eyes"
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    normalized = json.loads(event.normalized_payload_json)
    assert normalized["portal_start_reaction"] == start_reaction
    assert "portal_start_reaction_error" not in normalized


@pytest.mark.parametrize(
    "source,expected_issue_key,expected_source_comment",
    [
        ("jira_assignee", "ENG-1", None),
        ("jira_mention", "ENG-2", "Bot User please check this"),
    ],
)
def test_jira_task_creation_records_portal_start_reply(source, expected_issue_key, expected_source_comment):
    db = _session()
    user, agent = _create_user_agent(db, username=f"u-{source}-start-reply")
    svc, rule = _create_rule(db, user, agent, source=source)
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    start_calls = []
    _stub_jira_start_comment(svc, calls=start_calls, comment_id="jira-start-123")
    source_item = _source_item(source)

    _run_once_with_items(svc, rule.id, [source_item])

    assert start_calls == [
        {
            "rule_id": rule.id,
            "reply_target": source_item["reply_target"],
            "source": source,
            "source_url": source_item["source_url"],
            "source_comment": expected_source_comment,
        }
    ]
    task = db.query(AgentTask).one()
    task_payload = json.loads(task.input_payload_json)
    reply_target = task_payload["delegation"]["reply_target"]
    assert reply_target == {
        "provider": "jira",
        "kind": "issue_comment",
        "issue_key": expected_issue_key,
        "reply_mode": "update_comment",
        "comment_id": "jira-start-123",
    }
    start_reply = task_payload["delegation"]["portal_start_reply"]
    assert start_reply["provider"] == "jira"
    assert start_reply["status"] == "created"
    assert start_reply["issue_key"] == expected_issue_key
    assert start_reply["comment_id"] == "jira-start-123"
    assert "Automated EFP delegation run has started." in start_reply["content"]
    assert f"Source: {source}" in start_reply["content"]
    assert f"Issue: {expected_issue_key}" in start_reply["content"]
    assert f"Link: {source_item['source_url']}" in start_reply["content"]
    assert "<!-- efp:delegation-reply" not in start_reply["content"]
    assert f"delegation_id={rule.id}" not in start_reply["content"]
    assert "Bot User please check this" not in start_reply["content"]
    assert "portal_start_reply_error" not in task_payload["delegation"]

    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert f"event_id={event.id}" not in start_reply["content"]
    normalized = json.loads(event.normalized_payload_json)
    assert normalized["reply_target"] == reply_target
    assert normalized["portal_start_reply"] == start_reply
    assert "portal_start_reply_error" not in normalized


@pytest.mark.parametrize("source", ["jira_assignee", "jira_mention"])
def test_jira_task_creation_succeeds_when_portal_start_reply_fails(source):
    db = _session()
    user, agent = _create_user_agent(db, username=f"u-{source}-start-reply-fail")
    svc, rule = _create_rule(db, user, agent, source=source)
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_jira_start_comment_failure(svc)
    source_item = _source_item(source)

    result = _run_once_with_items(svc, rule.id, [source_item])

    assert result.created_task_count == 1
    task = db.query(AgentTask).one()
    task_payload = json.loads(task.input_payload_json)
    assert task_payload["delegation"]["reply_target"] == source_item["reply_target"]
    assert "reply_mode" not in task_payload["delegation"]["reply_target"]
    assert "comment_id" not in task_payload["delegation"]["reply_target"]
    assert "portal_start_reply" not in task_payload["delegation"]
    error_payload = task_payload["delegation"]["portal_start_reply_error"]
    assert error_payload["type"] == "RuntimeError"
    assert error_payload["message"] == "jira comment unavailable"
    assert error_payload["issue_key"] == source_item["reply_target"]["issue_key"]

    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "task_created"
    normalized = json.loads(event.normalized_payload_json)
    assert normalized["reply_target"] == source_item["reply_target"]
    assert normalized["portal_start_reply_error"] == error_payload
    assert "portal_start_reply" not in normalized


def test_dedupe_same_source_item_does_not_create_duplicate_task():
    db = _session()
    user, agent = _create_user_agent(db, username="u-dedupe")
    svc, rule = _create_rule(db, user, agent, source="github_pr_review")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_start_reaction(svc)
    item = _source_item("github_pr_review")

    first = _run_once_with_items(svc, rule.id, [item])
    second = _run_once_with_items(svc, rule.id, [item])

    assert first.created_task_count == 1
    assert second.created_task_count == 0
    assert second.skipped_count == 1
    assert db.query(AgentTask).count() == 1
    assert len(DelegationRuleRepository(db).list_events(rule.id, 10)) == 1


def test_jira_assignee_stable_dedupe_skips_changed_version_key():
    db = _session()
    user, agent = _create_user_agent(db, username="u-jira-assignee-dedupe")
    svc, rule = _create_rule(db, user, agent, source="jira_assignee")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_jira_start_comment(svc)
    first_item = _source_item("jira_assignee")
    second_item = json.loads(json.dumps(first_item))
    second_item["version_key"] = "2026-01-02"
    second_item["source_payload"]["issue"]["updated"] = "2026-01-02"

    first = _run_once_with_items(svc, rule.id, [first_item])
    second = _run_once_with_items(svc, rule.id, [second_item])

    assert first.created_task_count == 1
    assert second.created_task_count == 0
    assert second.skipped_count == 1
    assert db.query(AgentTask).count() == 1
    events = DelegationRuleRepository(db).list_events(rule.id, 10)
    assert len(events) == 1
    assert events[0].dedupe_key == "jira_assignee:ENG-1"


def test_reply_sent_when_task_done(monkeypatch):
    db = _session()
    user, agent = _create_user_agent(db, username="u-reply")
    svc, rule = _create_rule(db, user, agent, source="github_pr_mention")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_start_reaction(svc)
    _stub_reaction_cleanup(svc)
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


def test_github_pr_mention_pending_reply_posts_quote_reply_and_cleans_reaction():
    db = _session()
    user, agent = _create_user_agent(db, username="u-quote-reply")
    svc, rule = _create_rule(db, user, agent, source="github_pr_mention")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_start_reaction(svc, reaction_id=777)
    cleanup_calls = []
    _stub_reaction_cleanup(svc, calls=cleanup_calls)
    _run_once_with_items(svc, rule.id, [_source_item("github_pr_mention")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.result_payload_json = json.dumps({"status": "success", "output_payload": {"final_response": "Final answer"}})
    db.add(task)
    db.commit()

    captured = {}

    async def _send_github_reply(_db, *, rule, reply_target, text):
        captured["rule_id"] = rule.id
        captured["reply_target"] = reply_target
        captured["text"] = text

    svc.reply_service._send_github_reply = _send_github_reply

    _run_once_with_items(svc, rule.id, [])

    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_sent"
    assert captured["reply_target"]["reply_mode"] == "quote_reply"
    assert captured["text"].startswith("<!-- efp:delegation-reply ")
    assert f"delegation_id={rule.id}" in captured["text"]
    assert f"event_id={event.id}" in captured["text"]
    assert "Replying to @alice's [comment](https://github.com/acme/portal/pull/2#issuecomment-100):" in captured["text"]
    assert "> @octocat please handle this" in captured["text"]
    assert captured["text"].endswith("\n\nFinal answer")
    assert cleanup_calls == [
        {
            "rule_id": rule.id,
            "cleanup_api_path": "/repos/acme/portal/issues/comments/100/reactions/777",
            "portal_start_reaction": json.loads(event.normalized_payload_json)["portal_start_reaction"],
        }
    ]
    normalized = json.loads(event.normalized_payload_json)
    assert normalized["portal_start_reaction_cleanup"]["status"] == "deleted"


def test_github_reply_uses_final_response_before_output_summary():
    db = _session()
    user, agent = _create_user_agent(db, username="u-reply-final")
    svc, rule = _create_rule(db, user, agent, source="github_pr_review")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_start_reaction(svc)
    _stub_reaction_cleanup(svc)
    _run_once_with_items(svc, rule.id, [_source_item("github_pr_review")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.result_payload_json = json.dumps(
        {
            "status": "success",
            "output_payload": {
                "summary": "Short summary",
                "final_response": "Detailed final response",
            },
        }
    )
    db.add(task)
    db.commit()

    captured = {}

    async def _send_reply(_db, *, rule, event, reply_target, text):
        captured["reply_target"] = reply_target
        captured["text"] = text

    svc.reply_service.send_reply = _send_reply
    result = _run_once_with_items(svc, rule.id, [])

    assert result.created_task_count == 0
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_sent"
    assert captured["reply_target"]["kind"] == "pr_comment"
    assert captured["text"].endswith("\n\nDetailed final response")
    assert "Short summary" not in captured["text"]


@pytest.mark.parametrize(
    "payload",
    [
        {"output_payload": {"summary": "Short summary", "final_response": "Detailed final response"}},
        {"summary": "Short summary", "final_response": "Detailed final response"},
    ],
)
def test_extract_task_result_text_prefers_final_response_over_summary(payload):
    task = SimpleNamespace(result_payload_json=json.dumps(payload), summary="Fallback summary")

    assert DelegationRuleService._extract_task_result_text(task) == "Detailed final response"


def test_reply_skipped_when_task_result_was_handled_by_skill():
    db = _session()
    user, agent = _create_user_agent(db, username="u-reply-skip")
    svc, rule = _create_rule(db, user, agent, source="jira_assignee")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_jira_start_comment(svc)
    _run_once_with_items(svc, rule.id, [_source_item("jira_assignee")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.result_payload_json = json.dumps({"status": "success", "output_payload": {"reply_handled_by_skill": True}})
    db.add(task)
    db.commit()

    async def _send_reply(*_args, **_kwargs):
        raise AssertionError("send_reply should not be called")

    svc.reply_service.send_reply = _send_reply
    result = _run_once_with_items(svc, rule.id, [])

    assert result.created_task_count == 0
    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_sent"
    assert event.error_message is None


def test_jira_pending_reply_updates_portal_start_comment():
    db = _session()
    user, agent = _create_user_agent(db, username="u-jira-update-reply")
    svc, rule = _create_rule(db, user, agent, source="jira_mention")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_jira_start_comment(svc, comment_id="jira-start-789")
    _run_once_with_items(svc, rule.id, [_source_item("jira_mention")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.result_payload_json = json.dumps({"status": "success", "output_payload": {"final_response": "Jira final answer"}})
    db.add(task)
    db.commit()

    captured = {}

    async def _send_reply(_db, *, rule, event, reply_target, text):
        captured["reply_target"] = reply_target
        captured["text"] = text

    svc.reply_service.send_reply = _send_reply
    _run_once_with_items(svc, rule.id, [])

    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_sent"
    assert captured["reply_target"] == {
        "provider": "jira",
        "kind": "issue_comment",
        "issue_key": "ENG-2",
        "reply_mode": "update_comment",
        "comment_id": "jira-start-789",
    }
    assert captured["text"] == "Jira final answer"
    assert "<!-- efp:delegation-reply" not in captured["text"]


def test_reply_handled_by_skill_skips_github_reply_but_cleans_start_reaction():
    db = _session()
    user, agent = _create_user_agent(db, username="u-skill-cleanup")
    svc, rule = _create_rule(db, user, agent, source="github_pr_mention")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_start_reaction(svc, reaction_id=888)
    cleanup_calls = []
    _stub_reaction_cleanup(svc, calls=cleanup_calls)
    _run_once_with_items(svc, rule.id, [_source_item("github_pr_mention")])
    task = db.query(AgentTask).one()
    task.status = "done"
    task.result_payload_json = json.dumps({"status": "success", "output_payload": {"reply_handled_by_skill": True}})
    db.add(task)
    db.commit()

    async def _send_reply(*_args, **_kwargs):
        raise AssertionError("send_reply should not be called")

    svc.reply_service.send_reply = _send_reply
    _run_once_with_items(svc, rule.id, [])

    event = DelegationRuleRepository(db).list_events(rule.id, 10)[0]
    assert event.status == "reply_sent"
    assert cleanup_calls[0]["cleanup_api_path"] == "/repos/acme/portal/issues/comments/100/reactions/888"
    normalized = json.loads(event.normalized_payload_json)
    assert normalized["portal_start_reaction_cleanup"]["status"] == "deleted"


@pytest.mark.parametrize(
    "payload",
    [
        {"reply_handled_by_skill": True},
        {"output_payload": {"reply_handled_by_skill": True}},
        {"normalized_payload": {"reply_handled_by_skill": True}},
        {"external_actions": [{"type": "reply_handled_by_skill", "status": "success"}]},
    ],
)
def test_reply_handled_by_skill_flag_supports_expected_result_shapes(payload):
    task = SimpleNamespace(result_payload_json=json.dumps(payload))

    assert DelegationRuleService._task_reply_handled_by_skill(task) is True


def test_reply_failure_marks_event_failed():
    db = _session()
    user, agent = _create_user_agent(db, username="u-reply-fail")
    svc, rule = _create_rule(db, user, agent, source="jira_assignee")
    svc.dispatcher.dispatch_task_in_background = lambda _task_id: None
    _stub_jira_start_comment(svc)
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
