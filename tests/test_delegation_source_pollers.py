import asyncio
from datetime import datetime
from types import SimpleNamespace

from app.services.delegation_source_pollers import DelegationSourcePoller
from app.services.provider_config_resolver import GithubProviderConfig, JiraProviderConfig


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class _FakeGithubAsyncClient:
    def __init__(self, pull_payload, expected_query: str, **_kwargs):
        self.pull_payload = pull_payload
        self.expected_query = expected_query

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url == "https://api.github.com/user":
            return _FakeResponse({"login": "octocat"})
        if url == "https://api.github.com/search/issues":
            assert params["q"] == self.expected_query
            return _FakeResponse(
                {
                    "items": [
                        {
                            "html_url": "https://github.com/acme/portal/pull/1",
                            "url": "https://api.github.com/repos/acme/portal/issues/1",
                            "pull_request": {
                                "url": "https://api.github.com/repos/acme/portal/pulls/1",
                            },
                        }
                    ]
                }
            )
        if url == "https://api.github.com/repos/acme/portal/pulls/1":
            return _FakeResponse(self.pull_payload)
        raise AssertionError(f"Unexpected GitHub request: {url}")


def _pull_payload(*, requested_reviewers: list[dict], requested_teams: list[dict]) -> dict:
    return {
        "html_url": "https://github.com/acme/portal/pull/1",
        "title": "Improve portal flow",
        "head": {"sha": "abc123"},
        "base": {"sha": "def456", "ref": "main"},
        "labels": [{"name": "backend"}],
        "user": {"login": "alice"},
        "requested_reviewers": requested_reviewers,
        "requested_teams": requested_teams,
    }


def _poll_github_pr_review(monkeypatch, pull_payload):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_github_for_agent",
        lambda _db, _agent_id: GithubProviderConfig(
            base_url="https://api.github.com",
            api_token="gh-secret",
            runtime_profile_id="runtime-profile-1",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeGithubAsyncClient(
            pull_payload,
            expected_query="is:pr is:open review-requested:octocat",
            **kwargs,
        ),
    )
    rule = SimpleNamespace(target_agent_id="agent-1")
    return asyncio.run(DelegationSourcePoller()._poll_github_pr_review(object(), rule))


def test_timer_poller_uses_current_time_when_next_run_is_future(monkeypatch):
    fixed_now = datetime(2026, 6, 17, 1, 0, 0)
    monkeypatch.setattr("app.services.delegation_source_pollers.utc_now_naive", lambda: fixed_now)
    rule = SimpleNamespace(
        next_run_at=datetime(2026, 6, 18, 1, 0, 0),
        task_config_json='{"task_prompt": "Run a manual timer check."}',
        schedule_json='{"type": "cron", "expression": "30 9 * * 1-5", "timezone": "Asia/Shanghai"}',
    )

    result = DelegationSourcePoller()._poll_timer(rule)

    assert len(result.items) == 1
    assert result.items[0]["dedupe_key"] == "timer:2026-06-17T01:00:00Z"
    assert result.items[0]["task_content"] == "Run a manual timer check."
    assert result.items[0]["source_payload"]["scheduled_for"] == "2026-06-17T01:00:00Z"


def test_github_pr_review_direct_requested_reviewer_creates_source_item(monkeypatch):
    result = _poll_github_pr_review(
        monkeypatch,
        _pull_payload(
            requested_reviewers=[{"login": "octocat"}],
            requested_teams=[],
        ),
    )

    assert len(result.items) == 1
    assert result.items[0]["source"] == "github_pr_review"
    assert result.items[0]["represented_identity"] == "octocat"
    assert result.items[0]["source_url"] == "https://github.com/acme/portal/pull/1"
    assert result.items[0]["reaction_target"] == {
        "provider": "github",
        "kind": "pull_request",
        "owner": "acme",
        "repo": "portal",
        "pull_number": 1,
        "html_url": "https://github.com/acme/portal/pull/1",
        "api_path": "/repos/acme/portal/issues/1/reactions",
    }
    assert result.items[0]["source_payload"]["pull_request"]["owner"] == "acme"
    assert result.items[0]["source_payload"]["pull_request"]["repo"] == "portal"
    assert result.items[0]["source_payload"]["pull_request"]["number"] == 1
    assert result.items[0]["source_payload"]["pull_request"]["title"] == "Improve portal flow"
    assert result.items[0]["source_payload"]["pull_request"]["head_sha"] == "abc123"
    assert result.items[0]["source_payload"]["pull_request"]["base_sha"] == "def456"
    assert result.items[0]["source_payload"]["pull_request"]["author"] == "alice"


def test_github_pr_review_team_only_request_creates_no_source_items(monkeypatch):
    result = _poll_github_pr_review(
        monkeypatch,
        _pull_payload(
            requested_reviewers=[],
            requested_teams=[{"slug": "backend", "name": "Backend"}],
        ),
    )

    assert result.items == []


def test_github_pr_review_direct_requested_reviewer_matches_case_insensitively(monkeypatch):
    result = _poll_github_pr_review(
        monkeypatch,
        _pull_payload(
            requested_reviewers=[{"login": "OCTOCAT"}],
            requested_teams=[],
        ),
    )

    assert len(result.items) == 1
    assert result.items[0]["represented_identity"] == "octocat"


def test_github_pr_review_query_includes_common_conditions(monkeypatch):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_github_for_agent",
        lambda _db, _agent_id: GithubProviderConfig(
            base_url="https://api.github.com",
            api_token="gh-secret",
            runtime_profile_id="runtime-profile-1",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeGithubAsyncClient(
            _pull_payload(requested_reviewers=[{"login": "octocat"}], requested_teams=[]),
            expected_query="is:pr is:open review-requested:octocat repo:acme/portal base:main",
            **kwargs,
        ),
    )
    rule = SimpleNamespace(
        target_agent_id="agent-1",
        trigger_type="github_pr_review",
        scope_json="{}",
        trigger_config_json='{"repository":"acme/portal","base_branch":"main"}',
    )

    result = asyncio.run(DelegationSourcePoller()._poll_github_pr_review(object(), rule))

    assert len(result.items) == 1
    assert result.items[0]["source_payload"]["pull_request"]["base_branch"] == "main"
    assert result.items[0]["source_payload"]["pull_request"]["labels"] == ["backend"]


def test_github_pr_mention_reaction_target_preserves_comment_kind():
    issue_target = DelegationSourcePoller._github_comment_reaction_target(
        owner="acme",
        repo="portal",
        pull_number=12,
        comment_id=456,
        comment_kind="issue_comment",
        html_url="https://github.com/acme/portal/pull/12#issuecomment-456",
    )
    review_target = DelegationSourcePoller._github_comment_reaction_target(
        owner="acme",
        repo="portal",
        pull_number=12,
        comment_id=789,
        comment_kind="pull_request_review_comment",
        html_url="https://github.com/acme/portal/pull/12#discussion_r789",
    )

    assert issue_target["kind"] == "issue_comment"
    assert issue_target["api_path"] == "/repos/acme/portal/issues/comments/456/reactions"
    assert issue_target["comment_id"] == 456
    assert issue_target["html_url"] == "https://github.com/acme/portal/pull/12#issuecomment-456"
    assert review_target["kind"] == "pull_request_review_comment"
    assert review_target["api_path"] == "/repos/acme/portal/pulls/comments/789/reactions"
    assert review_target["comment_id"] == 789
    assert review_target["html_url"] == "https://github.com/acme/portal/pull/12#discussion_r789"


class _FakeGithubMentionAsyncClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url == "https://api.github.com/user":
            return _FakeResponse({"login": "octocat"})
        if url == "https://api.github.com/search/issues":
            assert params["q"] == "is:pr is:open mentions:octocat"
            return _FakeResponse(
                {
                    "items": [
                        {
                            "html_url": "https://github.com/acme/portal/pull/2",
                            "url": "https://api.github.com/repos/acme/portal/issues/2",
                            "pull_request": {"url": "https://api.github.com/repos/acme/portal/pulls/2"},
                        }
                    ]
                }
            )
        if url == "https://api.github.com/repos/acme/portal/pulls/2":
            return _FakeResponse(
                {
                    "html_url": "https://github.com/acme/portal/pull/2",
                    "title": "Mentioned PR",
                    "head": {"sha": "head2"},
                    "base": {"sha": "base2"},
                    "user": {"login": "bob"},
                }
            )
        if url == "https://api.github.com/repos/acme/portal/issues/2/comments":
            return _FakeResponse(
                [
                    {
                        "id": 100,
                        "body": "@octocat please summarize this PR\nInclude tests.",
                        "html_url": "https://github.com/acme/portal/pull/2#issuecomment-100",
                        "user": {"login": "alice"},
                    }
                ]
            )
        if url == "https://api.github.com/repos/acme/portal/pulls/2/comments":
            return _FakeResponse([])
        raise AssertionError(f"Unexpected GitHub request: {url}")


class _FakeGithubSelfMentionAsyncClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url == "https://api.github.com/user":
            return _FakeResponse({"login": "octocat"})
        if url == "https://api.github.com/search/issues":
            assert params["q"] == "is:pr is:open mentions:octocat"
            return _FakeResponse(
                {
                    "items": [
                        {
                            "html_url": "https://github.com/acme/portal/pull/2",
                            "url": "https://api.github.com/repos/acme/portal/issues/2",
                            "pull_request": {"url": "https://api.github.com/repos/acme/portal/pulls/2"},
                        }
                    ]
                }
            )
        if url == "https://api.github.com/repos/acme/portal/pulls/2":
            return _FakeResponse(
                {
                    "html_url": "https://github.com/acme/portal/pull/2",
                    "title": "Mentioned PR",
                    "head": {"sha": "head2"},
                    "base": {"sha": "base2"},
                    "user": {"login": "bob"},
                }
            )
        if url == "https://api.github.com/repos/acme/portal/issues/2/comments":
            return _FakeResponse(
                [
                    {
                        "id": 101,
                        "body": "@octocat portal-created issue comment",
                        "html_url": "https://github.com/acme/portal/pull/2#issuecomment-101",
                        "user": {"login": "octocat"},
                    }
                ]
            )
        if url == "https://api.github.com/repos/acme/portal/pulls/2/comments":
            return _FakeResponse(
                [
                    {
                        "id": 102,
                        "body": "@octocat portal-created review comment",
                        "html_url": "https://github.com/acme/portal/pull/2#discussion_r102",
                        "user": {"login": "OCTOCAT"},
                    }
                ]
            )
        raise AssertionError(f"Unexpected GitHub request: {url}")


def test_github_pr_mention_reply_target_includes_quote_context(monkeypatch):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_github_for_agent",
        lambda _db, _agent_id: GithubProviderConfig(
            base_url="https://api.github.com",
            api_token="gh-secret",
            runtime_profile_id="runtime-profile-1",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeGithubMentionAsyncClient(**kwargs),
    )

    rule = SimpleNamespace(target_agent_id="agent-1")
    result = asyncio.run(DelegationSourcePoller()._poll_github_pr_mention(object(), rule))

    assert len(result.items) == 1
    reply_target = result.items[0]["reply_target"]
    assert reply_target["provider"] == "github"
    assert reply_target["kind"] == "pr_comment"
    assert reply_target["owner"] == "acme"
    assert reply_target["repo"] == "portal"
    assert reply_target["pull_number"] == 2
    assert reply_target["reply_mode"] == "quote_reply"
    assert reply_target["comment_kind"] == "issue_comment"
    assert reply_target["comment_id"] == 100
    assert reply_target["comment_html_url"] == "https://github.com/acme/portal/pull/2#issuecomment-100"
    assert reply_target["comment_author"] == "alice"
    assert reply_target["comment_body"] == "@octocat please summarize this PR\nInclude tests."


def test_github_pr_mention_ignores_self_authored_issue_and_review_comments(monkeypatch):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_github_for_agent",
        lambda _db, _agent_id: GithubProviderConfig(
            base_url="https://api.github.com",
            api_token="gh-secret",
            runtime_profile_id="runtime-profile-1",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeGithubSelfMentionAsyncClient(**kwargs),
    )

    rule = SimpleNamespace(target_agent_id="agent-1")
    result = asyncio.run(DelegationSourcePoller()._poll_github_pr_mention(object(), rule))

    assert result.items == []


class _FakeJiraAsyncClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url == "https://jira.local/rest/api/2/myself":
            return _FakeResponse({"displayName": "Bot User", "accountId": "bot-1"})
        if url == "https://jira.local/rest/api/2/search":
            assert params["jql"] == "assignee = currentUser() ORDER BY updated DESC"
            assert params["fields"] == "summary,status,reporter,assignee,updated,comment,project,issuetype,priority,labels"
            return _FakeResponse(
                {
                    "issues": [
                        {
                            "key": "ENG-1",
                            "fields": {
                                "summary": "Handle delegated issue",
                                "updated": "2026-01-01T00:00:00.000+0000",
                                "status": {"id": "3", "name": "In Progress", "statusCategory": {"name": "In Progress"}},
                                "reporter": {
                                    "accountId": "reporter-1",
                                    "displayName": "Reporter User",
                                    "key": "reporter",
                                    "name": "reporter",
                                },
                                "assignee": {"accountId": "bot-1", "displayName": "Bot User"},
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected Jira request: {url}")


def test_jira_assignee_source_payload_includes_reporter_identity(monkeypatch):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_jira_for_agent",
        lambda _db, _agent_id: JiraProviderConfig(
            base_url="https://jira.local",
            headers={"Authorization": "Bearer jira-secret"},
            runtime_profile_id="runtime-profile-1",
            api_version="2",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeJiraAsyncClient(**kwargs),
    )

    rule = SimpleNamespace(target_agent_id="agent-1")
    result = asyncio.run(DelegationSourcePoller()._poll_jira_assignee(object(), rule))

    assert len(result.items) == 1
    issue = result.items[0]["source_payload"]["issue"]
    assert issue["key"] == "ENG-1"
    assert issue["url"] == "https://jira.local/browse/ENG-1"
    assert issue["summary"] == "Handle delegated issue"
    assert issue["status"] == {"id": "3", "name": "In Progress", "category": "In Progress"}
    assert issue["reporter"]["accountId"] == "reporter-1"
    assert issue["reporter"]["displayName"] == "Reporter User"
    assert issue["reporter"]["key"] == "reporter"
    assert issue["assignee"]["accountId"] == "bot-1"
    assert result.items[0]["dedupe_key"] == "jira_assignee:ENG-1"
    assert result.items[0]["version_key"] == "2026-01-01T00:00:00.000+0000"


def test_jira_comment_identity_match_uses_display_name_only_without_stable_ids():
    assert DelegationSourcePoller._jira_comment_authored_by_identity(
        {"author": {"accountId": "bot-1", "displayName": "Bot User"}},
        {"accountId": "bot-1", "displayName": "Bot User"},
    )
    assert not DelegationSourcePoller._jira_comment_authored_by_identity(
        {"author": {"displayName": "Bot User"}},
        {"accountId": "bot-1", "displayName": "Bot User"},
    )
    assert DelegationSourcePoller._jira_comment_authored_by_identity(
        {"author": {"displayName": "Bot User"}},
        {"displayName": "Bot User"},
    )


class _FakeJiraMentionMarkerAsyncClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url == "https://jira.local/rest/api/2/myself":
            return _FakeResponse({"displayName": "Bot User", "accountId": "bot-1"})
        if url == "https://jira.local/rest/api/2/search":
            assert params["jql"] == '(text ~ "Bot User" OR text ~ "bot-1") ORDER BY updated DESC'
            return _FakeResponse(
                {
                    "issues": [
                        {
                            "key": "ENG-2",
                            "fields": {
                                "summary": "Mention task",
                                "updated": "2026-01-01T00:00:00.000+0000",
                                "comment": {
                                    "comments": [
                                        {
                                            "id": "300",
                                            "body": (
                                                "<!-- efp:delegation-reply delegation_id=rule-1 event_id=event-1 -->\n\n"
                                                "Automated EFP delegation run has started.\n\n"
                                                "Source: jira_mention\n"
                                                "Issue: ENG-2\n"
                                                "Link: https://jira.local/browse/ENG-2\n\n"
                                                "Bot User"
                                            ),
                                            "author": {"accountId": "bot-1", "displayName": "Bot User"},
                                            "created": "2026-01-01T00:00:00.000+0000",
                                        }
                                    ]
                                },
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected Jira request: {url}")


class _FakeJiraMentionSelfAuthoredAsyncClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, headers=None, params=None):
        if url == "https://jira.local/rest/api/2/myself":
            return _FakeResponse(
                {
                    "displayName": "Bot User",
                    "accountId": "bot-1",
                    "emailAddress": "bot@example.com",
                }
            )
        if url == "https://jira.local/rest/api/2/search":
            assert params["jql"] == '(text ~ "Bot User" OR text ~ "bot@example.com") ORDER BY updated DESC'
            return _FakeResponse(
                {
                    "issues": [
                        {
                            "key": "ENG-2",
                            "fields": {
                                "summary": "Mention task",
                                "updated": "2026-01-01T00:00:00.000+0000",
                                "comment": {
                                    "comments": [
                                        {
                                            "id": "301",
                                            "body": "Automated EFP delegation run has started.\n\nBot User",
                                            "author": {
                                                "accountId": "bot-1",
                                                "displayName": "Bot User",
                                                "emailAddress": "BOT@example.com",
                                            },
                                            "created": "2026-01-01T00:00:00.000+0000",
                                        }
                                    ]
                                },
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected Jira request: {url}")


def test_jira_mention_skips_marker_prefixed_portal_start_comment(monkeypatch):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_jira_for_agent",
        lambda _db, _agent_id: JiraProviderConfig(
            base_url="https://jira.local",
            headers={"Authorization": "Bearer jira-secret"},
            runtime_profile_id="runtime-profile-1",
            api_version="2",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeJiraMentionMarkerAsyncClient(**kwargs),
    )

    rule = SimpleNamespace(target_agent_id="agent-1")
    result = asyncio.run(DelegationSourcePoller()._poll_jira_mention(object(), rule))

    assert result.items == []


def test_jira_mention_skips_self_authored_comment_without_marker(monkeypatch):
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.resolve_jira_for_agent",
        lambda _db, _agent_id: JiraProviderConfig(
            base_url="https://jira.local",
            headers={"Authorization": "Bearer jira-secret"},
            runtime_profile_id="runtime-profile-1",
            api_version="2",
        ),
    )
    monkeypatch.setattr(
        "app.services.delegation_source_pollers.httpx.AsyncClient",
        lambda **kwargs: _FakeJiraMentionSelfAuthoredAsyncClient(**kwargs),
    )

    rule = SimpleNamespace(target_agent_id="agent-1")
    result = asyncio.run(DelegationSourcePoller()._poll_jira_mention(object(), rule))

    assert result.items == []
