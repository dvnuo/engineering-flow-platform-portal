"""Jira status-change delegations: a requirement moving to a status starts work."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import delegation_source_pollers as pollers
from app.services.delegation_rule_service import DelegationRuleService
from app.services.delegation_source_config import DELEGATION_SOURCE_PROVIDER, JIRA_DELEGATION_SOURCES
from app.services.delegation_source_pollers import SUPPORTED_DELEGATION_SOURCES, DelegationSourcePoller
from app.services.provider_config_resolver import JiraProviderConfig


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def _issue(key, status, history):
    return {
        "key": key,
        "fields": {
            "summary": f"Requirement {key}",
            "status": {"id": "10", "name": status, "statusCategory": {"name": "To Do"}},
            "updated": "2026-09-27T09:00:00.000+0000",
            "project": {"key": "FX"},
            "issuetype": {"name": "Story"},
        },
        "changelog": {"histories": history},
    }


class _FakeJira:
    seen = {}

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url.endswith("/myself"):
            return _Resp({"displayName": "QA Bot", "accountId": "bot-1"})
        if url.endswith("/search"):
            _FakeJira.seen = dict(params)
            return _Resp({"issues": [
                _issue("FX-101", "Ready for Test", [
                    {"created": "2026-09-27T08:00:00.000+0000", "items": [{"field": "status", "toString": "In Dev"}]},
                    {"created": "2026-09-27T08:30:00.000+0000", "items": [{"field": "status", "toString": "Ready for Test"}]},
                ]),
                _issue("FX-102", "Ready for Test", []),
            ]})
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def jira(monkeypatch):
    monkeypatch.setattr(pollers, "resolve_jira_for_agent", lambda _db, _agent_id, source_scope=None: JiraProviderConfig(
        base_url="https://jira.local", headers={"Authorization": "Bearer x"}, runtime_profile_id="rp", api_version="2",
    ))
    monkeypatch.setattr(pollers.httpx, "AsyncClient", lambda **kwargs: _FakeJira(**kwargs))
    return _FakeJira


def test_jira_status_is_a_supported_jira_source():
    assert "jira_status" in SUPPORTED_DELEGATION_SOURCES
    assert "jira_status" in JIRA_DELEGATION_SOURCES
    assert DELEGATION_SOURCE_PROVIDER["jira_status"] == "jira"


def test_poll_finds_transitions_since_the_rule_began(jira):
    rule = SimpleNamespace(
        target_agent_id="agent-1",
        trigger_type="jira_status",
        created_at=datetime.utcnow() - timedelta(minutes=90),
        scope_json="{}",
        trigger_config_json='{"project_key": "FX", "status_include": ["Ready for Test"]}',
    )
    result = asyncio.run(DelegationSourcePoller().poll(object(), rule))
    jql = jira.seen["jql"]
    assert jql.startswith('status CHANGED TO ("Ready for Test") AFTER -9')
    assert 'project = "FX"' in jql and 'status in ("Ready for Test")' in jql
    assert jira.seen["expand"] == "changelog"
    first, second = result.items
    assert first["dedupe_key"] == "jira_status:FX-101:Ready for Test:2026-09-27T08:30:00.000+0000"
    assert first["task_content"].startswith("The Jira issue FX-101 moved to Ready for Test.")
    assert first["source_payload"]["issue"]["key"] == "FX-101"
    assert first["reply_target"] == {"provider": "jira", "kind": "issue_comment", "issue_key": "FX-101"}
    # Without a changelog entry the issue's update time stands in.
    assert second["dedupe_key"].endswith(":2026-09-27T09:00:00.000+0000")


def test_poll_needs_a_status(jira):
    rule = SimpleNamespace(target_agent_id="agent-1", trigger_type="jira_status", created_at=datetime.utcnow(), scope_json="{}", trigger_config_json="{}")
    with pytest.raises(ValueError, match="status that starts the work"):
        asyncio.run(DelegationSourcePoller().poll(object(), rule))


def test_rules_require_a_trigger_status():
    with pytest.raises(HTTPException) as exc:
        DelegationRuleService._require_trigger_statuses("jira_status", {})
    assert exc.value.status_code == 400
    DelegationRuleService._require_trigger_statuses("jira_status", {"status_include": ["Ready for Test"]})
    DelegationRuleService._require_trigger_statuses("jira_assignee", {})
