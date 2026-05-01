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


def _provider():
    return GithubProviderConfig(base_url="https://api.github.com", api_token="t", runtime_profile_id="r")


@pytest.mark.anyio
async def test_poll_mentions_issue_comment(monkeypatch):
    responses = [_Resp(200, [{"id": 1, "body": "hi @efp-agent", "issue_url": "https://api.github.com/repos/acme/portal/issues/12", "html_url": "https://github.com/acme/portal/issues/12#issuecomment-1", "user": {"login": "alice", "type": "User"}, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])
    assert len(items) == 1
    assert items[0]["comment_kind"] == "issue_comment"
    assert "poll_cursors" in state


@pytest.mark.anyio
async def test_poll_mentions_pr_timeline_issue_comment(monkeypatch):
    responses = [_Resp(200, [{"id": 2, "body": "@efp-agent please check", "issue_url": "https://api.github.com/repos/acme/portal/issues/123", "html_url": "https://github.com/acme/portal/pull/123#issuecomment-2", "user": {"login": "alice", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])
    assert items[0]["context_type"] == "pull_request"
    assert items[0]["pull_number"] == 123


@pytest.mark.anyio
async def test_poll_mentions_pull_request_review_comment(monkeypatch):
    responses = [_Resp(200, [{"id": 10, "body": "@efp-agent", "pull_request_url": "https://api.github.com/repos/acme/portal/pulls/9", "html_url": "https://github.com/acme/portal/pull/9#discussion_r10", "path": "a.py", "line": 3, "side": "RIGHT", "diff_hunk": "@@", "user": {"login": "bob", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["pull_request_review_comment"])
    assert items[0]["comment_kind"] == "pull_request_review_comment"
    assert items[0]["path"] == "a.py"
    assert items[0]["line"] == 3
    assert items[0]["diff_hunk"] == "@@"


@pytest.mark.anyio
async def test_poll_mentions_ignores_no_mention_but_advances_cursor(monkeypatch):
    comment = {"id": 5, "body": "hello", "issue_url": "https://api.github.com/repos/acme/portal/issues/12", "html_url": "https://github.com/acme/portal/issues/12#issuecomment-5", "user": {"login": "alice", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"}
    responses = [_Resp(200, [comment])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])
    assert items == []
    assert state["poll_cursors"]["issue_comment"]["last_seen_updated_at"] >= comment["updated_at"]


@pytest.mark.anyio
async def test_poll_mentions_no_batch_advances_cursor_to_poll_time_with_overlap(monkeypatch):
    responses = [_Resp(200, [])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    initial_since = "2026-01-01T00:00:00Z"
    _, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={"issue_comment": {"last_seen_updated_at": initial_since, "last_seen_comment_id": 0}}, surfaces=["issue_comment"], overlap_seconds=0)
    assert state["poll_cursors"]["issue_comment"]["last_seen_updated_at"] > initial_since


@pytest.mark.anyio
async def test_poll_mentions_code_block_and_blockquote_ignored(monkeypatch):
    responses = [_Resp(200, [{"id": 7, "body": "```\n@efp-agent\n```\n> @efp-agent", "issue_url": "https://api.github.com/repos/acme/portal/issues/7", "html_url": "https://github.com/acme/portal/issues/7#issuecomment-7", "user": {"login": "alice", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])
    assert items == []


@pytest.mark.anyio
async def test_poll_mentions_ignores_self_bot_and_marker(monkeypatch):
    responses = [_Resp(200, [
        {"id": 1, "body": "@efp-agent", "issue_url": "https://api.github.com/repos/acme/portal/issues/1", "html_url": "https://github.com/acme/portal/issues/1#issuecomment-1", "user": {"login": "efp-agent", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"},
        {"id": 2, "body": "@efp-agent", "issue_url": "https://api.github.com/repos/acme/portal/issues/2", "html_url": "https://github.com/acme/portal/issues/2#issuecomment-2", "user": {"login": "botty", "type": "bot"}, "updated_at": "2026-01-01T00:00:01Z"},
        {"id": 3, "body": "@efp-agent <!-- efp:auto-reply -->", "issue_url": "https://api.github.com/repos/acme/portal/issues/3", "html_url": "https://github.com/acme/portal/issues/3#issuecomment-3", "user": {"login": "alice", "type": "User"}, "updated_at": "2026-01-01T00:00:02Z"},
    ])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])
    assert items == []


@pytest.mark.anyio
async def test_poll_mentions_github_error(monkeypatch):
    responses = [_Resp(500, [])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    with pytest.raises(ValueError, match="surface=issue_comment status=500"):
        await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["issue_comment"])


@pytest.mark.anyio
async def test_poll_mentions_page_limit_does_not_jump_cursor_to_poll_time(monkeypatch):
    batch = [{"id": i, "body": "no mention", "issue_url": "https://api.github.com/repos/acme/portal/issues/1", "html_url": f"https://github.com/acme/portal/issues/1#issuecomment-{i}", "user": {"login": "alice", "type": "User"}, "updated_at": f"2026-01-01T00:00:{i % 60:02d}Z"} for i in range(1, 101)]
    responses = [_Resp(200, batch)]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    _, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={"issue_comment": {"last_seen_updated_at": "2025-12-31T00:00:00Z", "last_seen_comment_id": 0}}, surfaces=["issue_comment"], max_pages_per_surface=1, overlap_seconds=0)
    assert state["poll_cursors"]["issue_comment"]["last_seen_updated_at"] == "2026-01-01T00:00:59Z"


@pytest.mark.anyio
async def test_poll_mentions_commit_comment(monkeypatch):
    responses = [_Resp(200, [{"id": 99, "commit_id": "abc123", "body": "@efp-agent ping", "user": {"login": "alice", "type": "User"}, "html_url": "u", "url": "a", "updated_at": "2026-01-01T00:00:00Z"}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["commit_comment"], initial_since=__import__("datetime").datetime(2025,12,31,0,0,0))
    assert items[0]["comment_kind"] == "commit_comment"
    assert items[0]["commit_id"] == "abc123"
    assert items[0]["context_type"] == "commit"


@pytest.mark.anyio
async def test_poll_mentions_unknown_surface_raises(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client([]))
    with pytest.raises(ValueError, match="Unsupported GitHub comment mention surface"):
        await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["bad_surface"])


@pytest.mark.anyio
async def test_list_org_repositories_filters_archived_forks_and_patterns(monkeypatch):
    responses = [_Resp(200, [
        {"name": "portal", "full_name": "acme/portal", "archived": False, "fork": False},
        {"name": "api-core", "full_name": "acme/api-core", "archived": False, "fork": False},
        {"name": "archived-repo", "full_name": "acme/archived-repo", "archived": True, "fork": False},
        {"name": "forked-repo", "full_name": "acme/forked-repo", "archived": False, "fork": True},
    ])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    repos = await GithubCommentMentionPoller().list_org_repositories(provider_config=_provider(), org="acme", repo_selector={"include": ["api-*", "portal"], "exclude": ["archived-*"], "include_forks": False, "include_archived": False})
    assert [r["repo"] for r in repos] == ["api-core", "portal"]

@pytest.mark.anyio
async def test_poll_mentions_commit_comment_does_not_send_since_param(monkeypatch):
    seen = {}
    class C(_Client):
        async def get(self, *_args, **kwargs):
            seen.update(kwargs.get("params") or {})
            return _Resp(200, [])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["commit_comment"])
    assert "since" not in seen and "sort" not in seen and "direction" not in seen


@pytest.mark.anyio
async def test_poll_mentions_commit_comment_filters_old_history_on_initial_run(monkeypatch):
    responses = [_Resp(200, [
        {"id": 1, "commit_id": "a", "body": "@efp-agent", "user": {"login": "u", "type": "User"}, "updated_at": "2025-12-01T00:00:00Z"},
        {"id": 2, "commit_id": "b", "body": "@efp-agent", "user": {"login": "u", "type": "User"}, "updated_at": "2026-01-01T00:00:01Z"},
    ])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["commit_comment"], initial_since=__import__("datetime").datetime(2026,1,1,0,0,0))
    assert [i["comment_id"] for i in items] == [2]


@pytest.mark.anyio
async def test_poll_mentions_commit_comment_uses_id_cursor(monkeypatch):
    responses = [_Resp(200, [
        {"id": 50, "commit_id": "a", "body": "@efp-agent", "user": {"login": "u", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "commit_id": "b", "body": "@efp-agent", "user": {"login": "u", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"},
    ])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={"commit_comment": {"last_seen_comment_id": 100, "last_seen_updated_at": "2026-01-01T00:00:00Z"}}, surfaces=["commit_comment"])
    assert [i["comment_id"] for i in items] == [101]


@pytest.mark.anyio
async def test_poll_mentions_commit_comment_updates_next_scan_page_when_page_limit_hit(monkeypatch):
    batch = [{"id": i, "commit_id": f"c{i}", "body": "nope", "user": {"login": "u", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"} for i in range(1,101)]
    responses = [_Resp(200, batch)]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    _, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["commit_comment"], max_pages_per_surface=1)
    assert state["poll_cursors"]["commit_comment"]["next_scan_page"] == 2


def test_parse_last_page_from_link_header():
    from app.services.github_comment_mention_poller import _parse_last_page_from_link_header
    header = '<https://api.github.com/repositories/1/comments?page=5>; rel="last"'
    assert _parse_last_page_from_link_header(header) == 5


@pytest.mark.anyio
async def test_list_org_repositories_uses_stable_sort_params(monkeypatch):
    seen = {}
    class C(_Client):
        async def get(self, *_args, **kwargs):
            seen.update(kwargs.get("params") or {})
            return _Resp(200, [])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    await GithubCommentMentionPoller().list_org_repositories(provider_config=_provider(), org="acme")
    assert seen.get("sort") == "full_name"
    assert seen.get("direction") == "asc"

@pytest.mark.anyio
async def test_poll_mentions_commit_comment_page_limit_with_start_page_gt_one(monkeypatch):
    batch = [{"id": i+100, "commit_id": f"c{i}", "body": "x", "user": {"login": "u", "type": "User"}, "updated_at": "2026-01-01T00:00:00Z"} for i in range(100)]
    responses = [_Resp(200, batch), _Resp(200, batch)]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    _, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={"commit_comment": {"next_scan_page": 5, "last_seen_page": 5, "last_seen_comment_id": 100, "last_seen_updated_at": "2026-01-01T00:00:00Z"}}, surfaces=["commit_comment"], max_pages_per_surface=2)
    assert state["poll_cursors"]["commit_comment"]["next_scan_page"] == 7


@pytest.mark.anyio
async def test_poll_mentions_discussion_comment_graphql(monkeypatch):
    class C(_Client):
        async def post(self, *_args, **_kwargs):
            return _Resp(200, {"data": {"repository": {"discussions": {"nodes": [{"id": "D_1", "number": 1, "comments": {"nodes": [{"id": "DC_1", "body": "@efp-agent hi", "url": "u", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z", "author": {"login": "alice"}, "authorAssociation": "NONE", "replies": {"nodes": []}}]}}]}}}})
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    items, state = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["discussion_comment"], initial_since=__import__("datetime").datetime(2025,1,1))
    assert items[0]["comment_kind"] == "discussion_comment"
    assert items[0]["discussion_id"] == "D_1"
    assert items[0]["discussion_comment_id"] == "DC_1"
    assert items[0]["comment_id"] == "DC_1"
    assert state["poll_cursors"]["discussion_comment"]["last_seen_updated_at"]


@pytest.mark.anyio
async def test_poll_mentions_discussion_comment_reply_graphql(monkeypatch):
    class C(_Client):
        async def post(self, *_args, **_kwargs):
            return _Resp(200, {"data": {"repository": {"discussions": {"nodes": [{"id": "D_1", "number": 1, "comments": {"nodes": [{"id": "DC_TOP", "body": "top", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z", "author": {"login": "alice"}, "replies": {"nodes": [{"id": "DC_R", "body": "@efp-agent reply", "createdAt": "2026-01-01T00:00:01Z", "updatedAt": "2026-01-01T00:00:01Z", "author": {"login": "bob"}, "replyTo": {"id": "DC_TOP"}}]}}]}}]}}}})
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    items, _ = await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner="acme", repo="portal", mention_target="efp-agent", since_by_surface={}, surfaces=["discussion_comment"], initial_since=__import__("datetime").datetime(2025,1,1))
    assert items[0]["reply_to_id"] == "DC_TOP"

@pytest.mark.anyio
async def test_poll_mentions_commit_comment_initial_tail_respects_max_pages(monkeypatch):
    calls=[]
    class C(_Client):
        async def get(self, url, **kwargs):
            calls.append((kwargs.get('params') or {}).get('page'))
            if len(calls)==1:
                r=_Resp(200, []); r.headers={"Link": '<x?page=10>; rel="last"'}; return r
            return _Resp(200, [])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    await GithubCommentMentionPoller().poll_mentions(provider_config=_provider(), owner='acme', repo='r', mention_target='efp-agent', since_by_surface={}, surfaces=['commit_comment'], max_pages_per_surface=1, commit_comment_initial_tail_pages=2)
    assert calls == [1, 10]

@pytest.mark.anyio
async def test_list_account_notifications_filters_reasons_and_extracts_repo(monkeypatch):
    responses=[_Resp(200,[{"id":"1","reason":"mention","updated_at":"2026-01-01T00:00:00Z","repository":{"full_name":"acme/a"},"subject":{}},{"id":"2","reason":"subscribed","updated_at":"2026-01-01T00:00:01Z","repository":{"full_name":"acme/b"},"subject":{}}])]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client(responses))
    items,_=await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), reasons=["mention"])
    assert [i["repository_full_name"] for i in items]==["acme/a"]

@pytest.mark.anyio
async def test_list_account_notifications_sends_since_and_participating_params(monkeypatch):
    seen={}
    class C(_Client):
        async def get(self,*_a,**kw): seen.update(kw.get("params") or {}); return _Resp(200,[])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    from datetime import datetime
    await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), since=datetime(2026,1,1))
    assert seen["all"]=="true" and seen["participating"]=="true" and seen.get("since")

@pytest.mark.anyio
async def test_list_account_notifications_page_limit_does_not_advance_to_poll_time(monkeypatch):
    batch=[{"id":str(i),"reason":"mention","updated_at":"2026-01-01T00:00:00Z","repository":{"full_name":"acme/a"},"subject":{}} for i in range(100)]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client([_Resp(200,batch)]))
    _,state=await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), max_pages=1)
    assert state["hit_page_limit"] is True and state["next_notification_page"]==2
    assert state["last_seen_notification_updated_at"].startswith("2026-01-01T00:00:00")

@pytest.mark.anyio
async def test_list_account_notifications_start_page(monkeypatch):
    pages=[]
    class C(_Client):
        async def get(self,*_a,**kw): pages.append((kw.get("params") or {}).get("page")); return _Resp(200,[])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), start_page=3)
    assert pages[0]==3

@pytest.mark.anyio
async def test_list_account_notifications_user_token_error(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client([_Resp(403,[])]))
    with pytest.raises(ValueError, match="requires a user/PAT-compatible token"):
        await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider())


@pytest.mark.anyio
async def test_list_account_notifications_updates_seen_cursor_for_unmatched_reasons(monkeypatch):
    batch=[{"id":str(i),"reason":"subscribed","updated_at":"2026-01-01T00:00:00Z","repository":{"full_name":"acme/a"},"subject":{}} for i in range(1,101)]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client([_Resp(200,batch)]))
    items,state=await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), reasons=["mention"], max_pages=1, scan_since=__import__('datetime').datetime(2026,1,1))
    assert items==[]
    assert state["last_seen_notification_id"]=="100"
    assert state["hit_page_limit"] is True
    assert state["next_notification_page"]==2
    assert state["scan_since"].startswith("2026-01-01T00:00:00")

@pytest.mark.anyio
async def test_list_account_notifications_continues_with_scan_since(monkeypatch):
    seen={}
    class C(_Client):
        async def get(self,*_a,**kw):
            seen.update(kw.get("params") or {})
            return _Resp(200,[])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: C([]))
    dt=__import__('datetime').datetime(2026,1,1)
    await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), scan_since=dt, start_page=3)
    assert seen.get("page")==3
    assert seen.get("since").startswith("2026-01-01T00:00:00")

@pytest.mark.anyio
async def test_list_account_notifications_page_limit_does_not_use_poll_started_as_completed_cursor(monkeypatch):
    batch=[{"id":"1","reason":"subscribed","updated_at":"2026-01-01T00:00:00Z","repository":{"full_name":"acme/a"},"subject":{}} for _ in range(100)]
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client([_Resp(200,batch)]))
    _,state=await GithubCommentMentionPoller().list_account_notifications(provider_config=_provider(), reasons=["mention"], max_pages=1)
    assert state["last_seen_notification_updated_at"].startswith("2026-01-01T00:00:00")
