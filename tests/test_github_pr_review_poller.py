import pytest

from app.services.github_pr_review_poller import GithubPrReviewPoller
from app.services.provider_config_resolver import GithubProviderConfig


class _Resp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.content = b"1"

    def json(self):
        return self._data


class _Client:
    def __init__(self, responses, calls):
        self.responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, params or {}, headers or {}))
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_poller_user_target(monkeypatch):
    calls = []
    responses = [
        _Resp(200, {"items": [{"number": 12, "html_url": "u", "title": "t"}]}),
        _Resp(200, {"head": {"sha": "abc123"}}),
    ]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses, calls))

    items = await GithubPrReviewPoller().poll_review_requests(
        provider_config=GithubProviderConfig(base_url="https://api.github.com", api_token="t", runtime_profile_id="rp"),
        owner="acme",
        repo="portal",
        review_target_type="user",
        review_target="alice",
    )
    assert len(items) == 1
    assert items[0]["head_sha"] == "abc123"
    assert "review-requested:alice" in calls[0][1]["q"]


@pytest.mark.anyio
async def test_poller_team_target_and_empty(monkeypatch):
    calls = []
    responses = [_Resp(200, {"items": []})]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses, calls))

    items = await GithubPrReviewPoller().poll_review_requests(
        provider_config=GithubProviderConfig(base_url="https://api.github.com", api_token="t", runtime_profile_id="rp"),
        owner="acme",
        repo="portal",
        review_target_type="team",
        review_target="acme/reviewers",
    )
    assert items == []
    assert "team-review-requested:acme/reviewers" in calls[0][1]["q"]
