import pytest

from app.services.github_comment_mention_poller import GithubCommentMentionPoller
from app.services.provider_config_resolver import GithubProviderConfig


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


class _Client:
    def __init__(self, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, *_, **__):
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_poll_mentions_issue_comment(monkeypatch):
    responses = [_Resp(200, [{"id": 1, "body": "hi @efp-agent", "issue_url": "https://api.github.com/repos/acme/portal/issues/12", "html_url": "https://github.com/acme/portal/issues/12#issuecomment-1", "user": {"login": "alice", "type": "User"}, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, state = await GithubCommentMentionPoller().poll_mentions(provider_config=GithubProviderConfig(base_url="https://api.github.com", api_token="t", runtime_profile_id="r"), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])
    assert len(items) == 1
    assert items[0]["comment_kind"] == "issue_comment"
    assert "poll_cursors" in state
