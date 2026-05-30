import asyncio
from types import SimpleNamespace

from app.services import delegation_reply_service as reply_module
from app.services.delegation_reply_service import DelegationReplyService


def _reply_target(body: str) -> dict:
    return {
        "provider": "github",
        "kind": "pr_comment",
        "owner": "acme",
        "repo": "portal",
        "pull_number": 2,
        "reply_mode": "quote_reply",
        "comment_html_url": "https://github.com/acme/portal/pull/2#issuecomment-100",
        "comment_author": "alice",
        "comment_body": body,
    }


def _jira_reply_target(**overrides) -> dict:
    target = {"provider": "jira", "kind": "issue_comment", "issue_key": "ENG-1"}
    target.update(overrides)
    return target


class _FakeJiraResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeJiraAsyncClient:
    def __init__(self, calls: list, *, response_payload=None, status_code: int = 200, **_kwargs):
        self.calls = calls
        self.response_payload = response_payload or {}
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, headers=None, json=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        return _FakeJiraResponse(self.response_payload, self.status_code)

    async def put(self, url, *, headers=None, json=None):
        self.calls.append({"method": "PUT", "url": url, "headers": headers, "json": json})
        return _FakeJiraResponse(self.response_payload, self.status_code)


def _patch_jira_client(monkeypatch, calls: list, *, response_payload=None, status_code: int = 200):
    monkeypatch.setattr(
        reply_module,
        "resolve_jira_for_agent",
        lambda _db, _agent_id: SimpleNamespace(
            base_url="https://jira.local",
            headers={"Authorization": "Bearer jira-secret"},
            api_version="2",
        ),
    )
    monkeypatch.setattr(
        reply_module.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeJiraAsyncClient(
            calls,
            response_payload=response_payload,
            status_code=status_code,
            **kwargs,
        ),
    )


def test_github_quote_reply_preserves_marker_at_top():
    marker = "<!-- efp:delegation-reply delegation_id=rule-1 event_id=event-1 -->"

    formatted = DelegationReplyService.format_github_quote_reply_body(
        reply_target=_reply_target("@octocat please summarize"),
        text=f"{marker}\n\nFinal response",
    )

    assert formatted.startswith(f"{marker}\n\nReplying to @alice's [comment]")
    assert formatted.endswith("\n\nFinal response")


def test_github_quote_reply_quotes_multiline_source_comment():
    formatted = DelegationReplyService.format_github_quote_reply_body(
        reply_target=_reply_target("line one\n\nline three"),
        text="Final response",
    )

    assert "> line one\n> \n> line three" in formatted


def test_github_quote_reply_truncates_long_source_comment():
    formatted = DelegationReplyService.format_github_quote_reply_body(
        reply_target=_reply_target("abcdefghijklmnopqrstuvwxyz"),
        text="Final response",
        max_quote_chars=10,
    )

    assert "> abcdefghij\n> [truncated]" in formatted
    assert "klmnopqrstuvwxyz" not in formatted


def test_send_jira_reply_posts_issue_comment_by_default(monkeypatch):
    calls = []
    _patch_jira_client(monkeypatch, calls, status_code=201)
    svc = DelegationReplyService()
    rule = SimpleNamespace(target_agent_id="agent-1")

    asyncio.run(
        svc._send_jira_reply(
            object(),
            rule=rule,
            reply_target=_jira_reply_target(),
            text="Final Jira reply",
        )
    )

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://jira.local/rest/api/2/issue/ENG-1/comment"
    assert calls[0]["headers"]["Authorization"] == "Bearer jira-secret"
    assert calls[0]["headers"]["Accept"] == "application/json"
    assert calls[0]["headers"]["Content-Type"] == "application/json"
    assert calls[0]["json"] == {"body": "Final Jira reply"}


def test_send_jira_reply_updates_start_comment_when_comment_id_present(monkeypatch):
    calls = []
    _patch_jira_client(monkeypatch, calls, status_code=200)
    svc = DelegationReplyService()
    rule = SimpleNamespace(target_agent_id="agent-1")

    asyncio.run(
        svc._send_jira_reply(
            object(),
            rule=rule,
            reply_target=_jira_reply_target(reply_mode="update_comment", comment_id="7001"),
            text="Final Jira reply",
        )
    )

    assert len(calls) == 1
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"] == "https://jira.local/rest/api/2/issue/ENG-1/comment/7001"
    assert calls[0]["json"] == {"body": "Final Jira reply"}


def test_add_jira_start_comment_returns_created_comment_metadata(monkeypatch):
    calls = []
    _patch_jira_client(
        monkeypatch,
        calls,
        response_payload={"id": "7001", "self": "https://jira.local/rest/api/2/issue/ENG-1/comment/7001"},
        status_code=201,
    )
    svc = DelegationReplyService()
    rule = SimpleNamespace(id="rule-1", target_agent_id="agent-1")
    event = SimpleNamespace(id="event-1")

    metadata = asyncio.run(
        svc.add_jira_start_comment(
            object(),
            rule,
            _jira_reply_target(),
            source="jira_mention",
            source_url="https://jira.local/browse/ENG-1",
            source_comment="Bot User please check this",
            event=event,
        )
    )

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://jira.local/rest/api/2/issue/ENG-1/comment"
    body = calls[0]["json"]["body"]
    assert "Automated EFP delegation run has started." in body
    assert "Source: jira_mention" in body
    assert "Issue: ENG-1" in body
    assert "Link: https://jira.local/browse/ENG-1" in body
    assert "<!-- efp:delegation-reply" not in body
    assert "Bot User please check this" not in body
    assert metadata["provider"] == "jira"
    assert metadata["status"] == "created"
    assert metadata["issue_key"] == "ENG-1"
    assert metadata["comment_id"] == "7001"
    assert metadata["api_path"] == "/rest/api/2/issue/ENG-1/comment"
    assert metadata["content"] == body
