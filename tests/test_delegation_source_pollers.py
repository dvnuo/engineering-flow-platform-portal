import asyncio
from types import SimpleNamespace

from app.services.delegation_source_pollers import DelegationSourcePoller
from app.services.provider_config_resolver import GithubProviderConfig


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
        "head": {"sha": "abc123"},
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
