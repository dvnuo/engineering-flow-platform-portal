"""GitHub and CI picker sources.

"My recent branches" is assembled from the activity of the repositories the
member works in, their event feed, and one GraphQL request; these tests pin
what is asked, what is offered, and what is held back (the default branch, a
deleted branch, a branch already merged).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services.suggestions import clear_cache
from app.services.suggestions import github as github_source
from app.services.suggestions.ci import jenkins_job_url
from app.services.suggestions.github import GithubApi, activity_marks, feed_marks, match_paths, recent_branches
from app.services.suggestions.identity import github_candidate_logins
from tests._suggestions_helpers import GITHUB_CONFIG, Recorder, group_ids, make_context, run, values

API = "https://api.github.com"


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_cache()
    yield
    clear_cache()


def _push(repo: str, branch: str, when: str = "2026-11-12T08:00:00Z") -> dict:
    return {"type": "PushEvent", "repo": {"name": repo}, "payload": {"ref": f"refs/heads/{branch}"}, "created_at": when}


def _pull_search_item(number: int, repo: str = "acme/portal", title: str = "Fix it", draft: bool = False) -> dict:
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "repository_url": f"{API}/repos/{repo}",
        "updated_at": "2026-11-12T07:00:00Z",
        "draft": draft,
    }


def _github(routes: dict | None = None, *, login: str = "alice") -> Recorder:
    base = {("GET", "/user"): {"login": login}}
    base.update(routes or {})
    return Recorder(base)


# ------------------------------------------------------------- pull requests


def test_a_token_of_the_members_own_account_lists_reviews_waiting_and_their_pull_requests():
    def search(request: httpx.Request):
        q = request.url.params.get("q", "")
        if "review-requested" in q:
            return {"items": [_pull_search_item(7, title="Add retry")]}
        return {"items": [_pull_search_item(9, title="Draft work", draft=True)]}

    github = _github({("GET", "/search/issues"): search})
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG))

    assert result.identity == "personal"
    assert group_ids(result) == ["review_requested", "mine"]
    assert github.queries("/search/issues", "q") == [
        "is:pr is:open review-requested:@me archived:false",
        "is:pr is:open author:@me archived:false",
    ]
    review = result.groups[0].items[0]
    assert review.value == "https://github.com/acme/portal/pull/7"
    assert review.title == "Add retry" and review.subtitle == "acme/portal#7"
    assert result.groups[1].items[0].status == "draft"


def test_another_accounts_token_names_the_member_by_their_enterprise_login():
    # Whose account the token opens is decided by comparing it with the
    # member, never by who typed it: a service account a member put into
    # their own connector settings is still a service account, and "@me"
    # would be that account.
    github = _github(
        {
            ("GET", "/users/alice_corp"): {"login": "alice_corp"},
            ("GET", "/search/issues"): {"items": [_pull_search_item(3)]},
        },
        login="svc-bot",
    )
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, suffix="_corp"))

    assert result.identity == "shared"
    assert github.queries("/search/issues", "q") == [
        "is:pr is:open review-requested:alice_corp archived:false",
        "is:pr is:open author:alice_corp archived:false",
    ]
    assert not any("@me" in q or "svc-bot" in q for q in github.queries("/search/issues", "q"))


def test_a_token_whose_account_is_the_member_is_their_own():
    github = _github({("GET", "/search/issues"): {"items": []}}, login="Alice_Corp")
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, suffix="_corp"))

    assert result.identity == "personal"
    assert all("@me" in q for q in github.queries("/search/issues", "q"))


def test_a_member_github_does_not_know_gets_no_list():
    github = _github({}, login="svc-bot")
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG))

    assert result.state == "ok" and result.identity == "unknown" and group_ids(result) == []
    assert "/search/issues" not in github.paths()


@pytest.mark.parametrize("source", ["github.branches", "github.repos", "ci.failed_builds"])
def test_the_account_behind_the_token_is_never_listed_in_the_members_place(source):
    """The reported defect: a service account's branches shown as the member's.

    With nobody to name, the lists that mean "mine" stay empty. Nothing is
    asked about the account: not its repositories, not its feed, not its
    pull requests.
    """
    github = _github(
        {
            ("GET", "/user/repos"): [{"full_name": "svc-bot/everything", "pushed_at": "2026-11-12T09:00:00Z", "default_branch": "main"}],
            ("GET", "/users/svc-bot/events"): [_push("acme/portal", "feat/somebody-elses", "2026-11-12T09:00:00Z")],
            ("GET", "/repos/acme/portal/activity"): [_activity("push", "feat/somebody-elses", "2026-11-12T09:00:00Z")],
            ("POST", "/graphql"): _graphql(),
        },
        login="svc-bot",
    )
    result = run(source, make_context(github, GITHUB_CONFIG, suffix="_corp"))

    assert result.state == "ok" and group_ids(result) == []
    assert github.paths() == ["/user", "/users/alice_corp"]


def test_the_members_login_is_their_username_with_the_enterprise_suffix_put_back():
    def logins(username: str, suffix: str, base: str = API) -> list[str]:
        return github_candidate_logins(make_context(Recorder(), GITHUB_CONFIG, username=username, suffix=suffix), base)

    assert logins("12345678", "_corp") == ["12345678_corp"]
    assert logins("12345678", "") == ["12345678"]
    # A username that already carries the suffix is not given it twice.
    assert logins("12345678_CORP", "_corp") == ["12345678_CORP"]
    # The suffix belongs to the GitHub the Portal signs members in from. A
    # GitHub Enterprise Server may know them by the bare name; on github.com
    # the bare name is whoever registered it, and is never asked about.
    assert logins("12345678", "_corp", "https://ghe.corp/api/v3") == ["12345678_corp", "12345678"]
    # A name that could not sit in an address as is names nobody.
    assert logins("a b", "") == [] and logins("", "_corp") == []


def test_an_enterprise_server_that_knows_the_member_by_the_bare_username():
    config = {"github": {"enabled": True, "api_token": "t", "base_url": "https://ghe.corp/api/v3"}}
    github = Recorder(
        {
            ("GET", "/api/v3/user"): {"login": "svc-bot"},
            ("GET", "/api/v3/users/alice"): {"login": "alice", "type": "User"},
            ("GET", "/api/v3/search/issues"): {"items": []},
        }
    )
    result = run("github.pull_requests", make_context(github, config, suffix="_corp"))

    assert result.identity == "shared"
    assert github.paths()[:3] == ["/api/v3/user", "/api/v3/users/alice_corp", "/api/v3/users/alice"]
    assert all("alice " in q + " " and "alice_corp" not in q for q in github.queries("/api/v3/search/issues", "q"))


def test_an_organization_named_like_the_member_is_not_the_member():
    github = _github({("GET", "/users/alice"): {"login": "alice", "type": "Organization"}}, login="svc-bot")
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG))

    assert result.identity == "unknown" and "/search/issues" not in github.paths()


def test_a_lookup_that_timed_out_is_asked_again_instead_of_remembered_as_nobody():
    answers = [httpx.ReadTimeout("slow"), {"login": "alice_corp", "type": "User"}]
    github = _github(
        {("GET", "/users/alice_corp"): lambda request: answers.pop(0), ("GET", "/search/issues"): {"items": [_pull_search_item(3)]}},
        login="svc-bot",
    )
    first = run("github.pull_requests", make_context(github, GITHUB_CONFIG, suffix="_corp"))
    assert first.state == "unavailable" and first.reason == "timeout"

    # Another question of the same member a moment later: who they are is
    # looked up again, not remembered as "nobody" for an hour.
    second = run("github.repos", make_context(github, GITHUB_CONFIG, suffix="_corp", q="acme/portal"))
    assert second.state == "ok" and second.identity == "shared"


def test_an_account_that_may_not_look_users_up_leaves_the_member_unknown():
    github = _github({("GET", "/users/alice_corp"): httpx.Response(403, json={})}, login="svc-bot")
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, suffix="_corp"))

    assert result.state == "ok" and result.identity == "unknown" and group_ids(result) == []


def test_typed_text_searches_titles_of_pull_requests_that_involve_the_member():
    github = _github({("GET", "/search/issues"): {"items": [_pull_search_item(11, title="Retry on 502")]}})
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, q="retry"))

    assert values(result, "results") == ["https://github.com/acme/portal/pull/11"]
    assert github.queries("/search/issues", "q") == ["is:pr is:open involves:@me in:title retry"]


def test_a_pasted_pull_request_link_needs_no_search():
    github = _github({})
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, q="https://github.com/acme/portal/pull/42"))

    assert result.state == "ok" and group_ids(result) == []
    assert "/search/issues" not in github.paths()


def test_github_off_is_not_configured_and_a_refused_token_is_unavailable():
    off = run("github.pull_requests", make_context(Recorder(), {"github": {"enabled": True}}))
    assert off.state == "not_configured" and off.connector == "github"

    refused = run("github.pull_requests", make_context(Recorder({("GET", "/user"): httpx.Response(401, json={})}), GITHUB_CONFIG))
    assert refused.state == "unavailable" and refused.reason == "unauthorized"

    limited = Recorder({("GET", "/user"): httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})})
    assert run("github.pull_requests", make_context(limited, GITHUB_CONFIG, user_id=5)).reason == "rate_limited"


# ------------------------------------------------------------------ branches


def _create(repo: str, branch: str, when: str) -> dict:
    return {"type": "CreateEvent", "repo": {"name": repo}, "payload": {"ref": branch, "ref_type": "branch"}, "created_at": when}


def _delete(repo: str, branch: str, when: str) -> dict:
    return {"type": "DeleteEvent", "repo": {"name": repo}, "payload": {"ref": branch, "ref_type": "branch"}, "created_at": when}


def _activity(kind: str, branch: str, when: str) -> dict:
    return {"activity_type": kind, "ref": f"refs/heads/{branch}", "timestamp": when, "actor": {"login": "alice"}}


def test_the_last_thing_that_happened_to_a_branch_decides_whether_it_is_offered():
    # The feed does not come in order: that was measured, not assumed.
    feed = [
        _push("acme/portal", "feat/a", "2026-11-10T09:00:00Z"),
        _delete("acme/tools", "old", "2026-11-12T09:00:00Z"),
        _push("acme/tools", "old", "2026-11-11T09:00:00Z"),
        _push("acme/portal", "feat/a", "2026-11-12T09:00:00Z"),
        _create("acme/skills", "chore/b", "2026-11-09T09:00:00Z"),
        _delete("acme/tools", "back", "2026-11-08T09:00:00Z"),
        _push("acme/tools", "back", "2026-11-08T10:00:00Z"),
        {"type": "CreateEvent", "repo": {"name": "acme/skills"}, "payload": {"ref": "v1.0", "ref_type": "tag"}},
        {"type": "PushEvent", "repo": {"name": "acme/portal"}, "payload": {"ref": "refs/tags/v2"}},
        {"type": "WatchEvent", "repo": {"name": "acme/portal"}, "payload": {}},
        "not an event",
    ]
    assert recent_branches(feed_marks(feed)) == [
        ("acme/portal", "feat/a", "2026-11-12T09:00:00Z"),
        ("acme/skills", "chore/b", "2026-11-09T09:00:00Z"),
        # Pushed again after it was deleted: the branch is back.
        ("acme/tools", "back", "2026-11-08T10:00:00Z"),
    ]
    assert feed_marks(None) == [] and feed_marks({"message": "x"}) == []

    log = [
        _activity("branch_creation", "feat/new", "2026-11-13T03:26:43Z"),
        _activity("branch_deletion", "feat/done", "2026-11-12T17:23:11Z"),
        _activity("pr_merge", "master", "2026-11-12T17:23:07Z"),
        _activity("push", "feat/done", "2026-11-12T17:16:41Z"),
        _activity("force_push", "fix/x", "2026-11-12T10:00:00Z"),
        {"activity_type": "push", "ref": "refs/tags/v1", "timestamp": "2026-11-12T09:00:00Z"},
        "junk",
    ]
    marks = activity_marks("acme/portal", log)
    # A merge is a write to the branch merged into, not a branch of the member's.
    assert all(branch != "master" for _repo, branch, _when, _alive in marks)
    assert recent_branches(marks) == [
        ("acme/portal", "feat/new", "2026-11-13T03:26:43Z"),
        ("acme/portal", "fix/x", "2026-11-12T10:00:00Z"),
    ]
    assert activity_marks("acme/portal", None) == [] and activity_marks("acme/portal", {"message": "Not Found"}) == []

    # The same push seen by the feed and by the log is one branch.
    both = feed_marks([_push("Acme/Portal", "feat/new", "2026-11-13T03:26:43Z")]) + marks
    assert [row[1] for row in recent_branches(both)] == ["feat/new", "fix/x"]
    assert [row[1] for row in recent_branches(both, limit=1)] == ["feat/new"]


def _ref(name: str, when: str, pulls: list[dict] | None = None) -> dict:
    return {"name": name, "target": {"committedDate": when}, "associatedPullRequests": {"nodes": pulls or []}}


def _pull(number: int, repo: str, branch: str, state: str = "OPEN", default: str = "master", updated: str = "2026-11-12T07:00:00Z") -> dict:
    """One of the member's pull requests, as the GraphQL search describes it."""
    return {
        "number": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "state": state,
        "isDraft": False,
        "updatedAt": updated,
        "headRefName": branch,
        "headRepository": {"nameWithOwner": repo},
        "repository": {"nameWithOwner": repo, "url": f"https://github.com/{repo}", "isArchived": False, "defaultBranchRef": {"name": default}},
    }


MEMBER_PULLS = [
    _pull(531, "acme/portal", "fix/layout"),
    _pull(90, "acme/tools", "fix/merged", state="MERGED", default="main", updated="2026-11-10T10:00:00Z"),
]

# What GitHub itself knows: repository -> (default branch, its branches).
# "vanished" of acme/tools is missing because a colleague deleted it, and
# acme/secret because the token can no longer see it.
GITHUB_BRANCHES = {
    "acme/portal": (
        "master",
        {
            "feat/card-pickers": _ref("feat/card-pickers", "2026-11-12T08:59:00Z"),
            "master": _ref("master", "2026-11-12T08:00:00Z"),
            "fix/layout": _ref("fix/layout", "2026-11-11T08:00:00Z", [{"number": 531, "url": "https://github.com/acme/portal/pull/531", "state": "OPEN", "isDraft": False}]),
        },
    ),
    "acme/tools": ("main", {"fix/merged": _ref("fix/merged", "2026-11-10T08:00:00Z", [{"number": 90, "url": "u", "state": "MERGED", "isDraft": False}])}),
}


def _graphql(pulls: list[dict] | None = None):
    """GitHub's GraphQL endpoint: the member's pull requests for a search, and
    for anything else the repositories and branches named in the variables."""
    listed = MEMBER_PULLS if pulls is None else pulls

    def answer(request: httpx.Request):
        body = json.loads(request.content)
        if "search(" in body["query"]:
            return {"data": {"open": {"nodes": [pull for pull in listed if pull["state"] == "OPEN"]}, "recent": {"nodes": listed}}}
        variables = body["variables"]
        data: dict = {}
        index = 0
        while f"o{index}" in variables:
            name = f"{variables[f'o{index}']}/{variables[f'n{index}']}"
            known = GITHUB_BRANCHES.get(name)
            node = None
            if known is not None:
                node = {"nameWithOwner": name, "url": f"https://github.com/{name}", "isArchived": False, "defaultBranchRef": {"name": known[0]}}
                position = 0
                while f"b{index}_{position}" in variables:
                    node[f"b{position}"] = known[1].get(variables[f"b{index}_{position}"][len("refs/heads/"):])
                    position += 1
            data[f"r{index}"] = node
            index += 1
        return {"data": data}

    return answer


def _own_routes(**replaced) -> dict:
    """A member with pull requests in acme/portal and acme/tools, a stale feed
    that also knows acme/secret, and a repository of their own."""
    routes = {
        ("POST", "/graphql"): _graphql(),
        ("GET", "/users/alice/events"): [
            _push("acme/secret", "feat/x", "2026-11-08T09:00:00Z"),
            _push("acme/tools", "fix/merged", "2026-11-10T09:00:00Z"),
        ],
        ("GET", "/user/repos"): [{"full_name": "alice/notes", "pushed_at": "2026-11-01T09:00:00Z", "default_branch": "main"}],
        ("GET", "/repos/acme/portal/activity"): [
            _activity("branch_creation", "feat/card-pickers", "2026-11-12T09:00:00Z"),
            _activity("pr_merge", "master", "2026-11-12T08:30:00Z"),
            _activity("push", "master", "2026-11-12T08:00:00Z"),
            _activity("push", "fix/layout", "2026-11-11T09:00:00Z"),
            _activity("branch_deletion", "gone", "2026-11-10T10:00:00Z"),
            _activity("push", "gone", "2026-11-10T09:30:00Z"),
        ],
        ("GET", "/repos/acme/tools/activity"): [
            _activity("push", "fix/merged", "2026-11-10T09:00:00Z"),
            _activity("push", "vanished", "2026-11-09T09:00:00Z"),
        ],
    }
    for path, answer in replaced.items():
        method = "POST" if path == "graphql" else "GET"
        routes[(method, "/" + path.replace("__", "/"))] = answer
    return routes


def test_the_members_branches_are_read_from_the_activity_of_the_repositories_they_work_in():
    github = _github(_own_routes())
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    assert group_ids(result) == ["no_pr", "has_pr"]
    # Only identifiers reach the prompt: branch, repository, its default branch.
    assert values(result, "no_pr") == ["branch feat/card-pickers of acme/portal (default branch master)"]
    assert values(result, "has_pr") == ["branch fix/layout of acme/portal (default branch master)"]
    ready = result.groups[0].items[0]
    assert ready.title == "feat/card-pickers" and ready.subtitle == "acme/portal"
    # The default branch also travels on its own, for a card's "merge into" field.
    assert ready.fields == {"repo": "acme/portal", "branch": "feat/card-pickers", "base": "master"}
    assert ready.url == "https://github.com/acme/portal/tree/feat/card-pickers"
    # When the member pushed it, not when the commit was made.
    assert ready.meta == "2026-11-12T09:00:00Z"
    opened = result.groups[1].items[0]
    assert opened.badge == "#531" and opened.status == "open"
    assert opened.url == "https://github.com/acme/portal/pull/531"
    # The default branch, the merged one, the two deleted ones and the
    # unreadable repository are not offered.
    offered = [item.title for group in result.groups for item in group.items]
    assert offered == ["feat/card-pickers", "fix/layout"]

    # Where the member works: their pull requests, their feed, their own
    # repositories. Never "everything I can reach, by last push".
    search, confirm = github.bodies("/graphql")
    assert search["variables"] == {
        "open": "is:pr is:open author:@me sort:updated-desc",
        "recent": "is:pr author:@me sort:updated-desc",
    }
    assert "/search/issues" not in github.paths()
    assert github.queries("/user/repos", "affiliation") == ["owner,collaborator"]
    asked = [path for path in github.paths() if path.endswith("/activity")]
    assert sorted(asked) == [
        "/repos/acme/portal/activity",
        "/repos/acme/secret/activity",
        "/repos/acme/tools/activity",
        "/repos/alice/notes/activity",
    ]
    # The feed names acme/secret and no more, so its default branch is asked
    # for; the others came with theirs.
    assert [path for path in github.paths() if path.startswith("/repos/") and not path.endswith("/activity")] == ["/repos/acme/secret"]
    # The log is filtered to the member by GitHub, not here.
    assert github.queries("/repos/acme/portal/activity", "actor") == ["alice"]
    assert github.queries("/repos/acme/portal/activity", "time_period") == ["quarter"]
    # The newest branch was only in the activity log: the feed is hours late.
    assert "feat/card-pickers" not in str(_own_routes()[("GET", "/users/alice/events")])
    # GitHub confirmed the list in one request: the member's push to the
    # default branch never got that far, and names travel as variables.
    assert confirm["variables"] == {
        "o0": "acme", "n0": "portal", "b0_0": "refs/heads/feat/card-pickers", "b0_1": "refs/heads/fix/layout",
        "o1": "acme", "n1": "tools", "b1_0": "refs/heads/fix/merged", "b1_1": "refs/heads/vanished",
        "o2": "acme", "n2": "secret", "b2_0": "refs/heads/feat/x",
    }


def test_on_a_slow_link_the_list_is_shown_as_found_instead_of_waiting_for_github_to_confirm_it(monkeypatch):
    monkeypatch.setattr(github_source, "CONFIRM_BY_SECONDS", 0.0)
    github = _github(_own_routes())
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    # One GraphQL request, the search; the confirmation was not waited for.
    assert len(github.bodies("/graphql")) == 1
    assert group_ids(result) == ["no_pr", "has_pr"]
    # The member's own pull requests still say what is open and what was
    # merged. What only GitHub knows is missing: a colleague deleted
    # "vanished". acme/secret offers nothing: with its default branch
    # unknown, nothing says "feat/x" is not that branch.
    assert values(result, "no_pr") == [
        "branch feat/card-pickers of acme/portal (default branch master)",
        "branch vanished of acme/tools (default branch main)",
    ]
    assert values(result, "has_pr") == ["branch fix/layout of acme/portal (default branch master)"]
    opened = result.groups[1].items[0]
    assert opened.badge == "#531" and opened.url == "https://github.com/acme/portal/pull/531"


def test_only_the_most_recent_repositories_have_their_activity_read():
    pulls = [_pull(index, f"acme/repo-{index}", "feat/x", updated=f"2026-11-{20 - index:02d}T07:00:00Z") for index in range(1, 10)]
    github = _github({("POST", "/graphql"): _graphql(pulls)})
    run("github.branches", make_context(github, GITHUB_CONFIG))

    asked = [path for path in github.paths() if path.endswith("/activity")]
    assert sorted(asked) == sorted(f"/repos/acme/repo-{index}/activity" for index in range(1, 7))


def test_another_accounts_token_reads_the_activity_of_the_member_not_of_the_account():
    github = _github(
        {
            ("GET", "/users/alice_corp"): {"login": "alice_corp"},
            ("GET", "/repos/acme/portal/activity"): [_activity("push", "feat/card-pickers", "2026-11-12T09:00:00Z")],
            ("POST", "/graphql"): _graphql(),
        },
        login="svc-bot",
    )
    result = run("github.branches", make_context(github, GITHUB_CONFIG, suffix="_corp"))

    assert result.identity == "shared"
    assert values(result, "no_pr") == ["branch feat/card-pickers of acme/portal (default branch master)"]
    assert github.bodies("/graphql")[0]["variables"] == {
        "open": "is:pr is:open author:alice_corp sort:updated-desc",
        "recent": "is:pr author:alice_corp sort:updated-desc",
    }
    assert github.queries("/repos/acme/portal/activity", "actor") == ["alice_corp"]
    paths = github.paths()
    assert "/users/alice_corp/events" in paths and "/users/alice_corp/repos" in paths
    assert "/user/repos" not in paths and not any("svc-bot" in path for path in paths)


def test_a_server_without_the_activity_log_still_lists_what_the_feed_knows():
    feed = [_push("acme/portal", "feat/card-pickers", "2026-11-12T09:00:00Z")]
    github = _github({("GET", "/users/alice/events"): feed, ("POST", "/graphql"): _graphql([])})
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    assert "/repos/acme/portal/activity" in github.paths()
    assert values(result, "no_pr") == ["branch feat/card-pickers of acme/portal (default branch master)"]


def test_one_listing_failing_costs_only_what_it_knew_and_all_three_failing_is_an_outage():
    down = httpx.Response(503, text="down")
    described = {"full_name": "acme/tools", "default_branch": "main", "html_url": "https://github.com/acme/tools"}
    partly = _github(_own_routes(graphql=down, search__issues=down, user__repos=down, repos__acme__tools=described))
    result = run("github.branches", make_context(partly, GITHUB_CONFIG))
    # Only the feed answered. acme/portal was known through the pull
    # requests alone; the feed's repositories are still read, and the one
    # whose default branch could be learned still offers its branches.
    assert result.state == "ok" and group_ids(result) == ["pushed"]
    assert "/repos/acme/portal/activity" not in partly.paths()
    assert values(result, "pushed") == [
        "branch fix/merged of acme/tools (default branch main)",
        "branch vanished of acme/tools (default branch main)",
    ]
    assert result.groups[2].items[0].url == "https://github.com/acme/tools/tree/fix/merged"

    out = _github(_own_routes(graphql=down, search__issues=down, users__alice__events=down, user__repos=down))
    failed = run("github.branches", make_context(out, GITHUB_CONFIG, user_id=2))
    assert failed.state == "unavailable" and failed.reason == "error"


def test_a_slow_repository_is_left_out_instead_of_holding_the_list_back(monkeypatch):
    monkeypatch.setattr(github_source, "OWN_BY_SECONDS", 0.0)
    monkeypatch.setattr(github_source, "MIN_CONFIRM_SECONDS", 0.2)

    slow = _github(_own_routes())

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/tools/activity":
            await asyncio.sleep(5)
        return slow(request)

    context = make_context(slow, GITHUB_CONFIG)
    context.http.transport = httpx.MockTransport(handler)
    result = run("github.branches", context)

    assert result.state == "ok"
    offered = [item.title for group in result.groups for item in group.items]
    # acme/portal answered at once; acme/tools did not, and only what the
    # feed already knew of it could be considered.
    assert offered == ["feat/card-pickers", "fix/layout"]


def test_a_branch_the_members_pull_requests_merge_into_is_not_their_work():
    # A branch per environment: the member merges into "ops" all the time,
    # and the feed shows each merge as a push to it.
    merged = _pull(61, "acme/agents", "fix/rule-ops", state="MERGED")
    merged["baseRefName"] = "ops"
    feed = [_push("acme/agents", "ops", "2026-11-08T09:22:52Z"), _push("acme/agents", "feat/next", "2026-11-07T09:00:00Z")]
    github = _github({("POST", "/graphql"): _graphql([merged]), ("GET", "/users/alice/events"): feed})
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    asked = github.bodies("/graphql")[1]["variables"]
    assert "refs/heads/ops" not in asked.values() and "refs/heads/feat/next" in asked.values()
    assert result.state == "ok"


def test_a_repository_whose_log_was_read_is_not_second_guessed_by_the_feed():
    # The log says the member merged into "release" (not listed) and pushed
    # "feat/a"; the feed, hours behind, shows that merge as a push and still
    # has a branch the member has since deleted.
    routes = {
        ("POST", "/graphql"): _graphql([_pull(7, "acme/portal", "feat/old", state="MERGED")]),
        ("GET", "/users/alice/events"): [
            _push("acme/portal", "release", "2026-11-12T10:00:00Z"),
            _push("acme/portal", "feat/deleted", "2026-11-12T09:30:00Z"),
        ],
        ("GET", "/repos/acme/portal/activity"): [
            _activity("pr_merge", "release", "2026-11-12T10:00:00Z"),
            _activity("branch_deletion", "feat/deleted", "2026-11-12T09:45:00Z"),
            _activity("push", "feat/card-pickers", "2026-11-12T09:00:00Z"),
        ],
    }
    github = _github(routes)
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    assert [item.title for group in result.groups for item in group.items] == ["feat/card-pickers"]
    asked = github.bodies("/graphql")[1]["variables"]
    assert sorted(value for name, value in asked.items() if name.startswith("b")) == ["refs/heads/feat/card-pickers"]


def test_when_no_repository_answers_in_time_the_picker_says_so_instead_of_showing_an_empty_list(monkeypatch):
    monkeypatch.setattr(github_source, "OWN_BY_SECONDS", 0.0)
    monkeypatch.setattr(github_source, "MIN_CONFIRM_SECONDS", 0.1)
    slow = _github(_own_routes())

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activity"):
            await asyncio.sleep(5)
        return slow(request)

    context = make_context(slow, GITHUB_CONFIG)
    context.http.transport = httpx.MockTransport(handler)
    result = run("github.branches", context)

    assert result.state == "unavailable" and result.reason == "timeout"


def test_an_old_pull_request_that_is_still_open_is_known_without_asking_again(monkeypatch):
    # Open for months, so far down "the newest fifty" that it is not in
    # it; the search for open ones still has it.
    monkeypatch.setattr(github_source, "CONFIRM_BY_SECONDS", 0.0)
    old = _pull(134, "acme/portal", "fix/size-limit", updated="2026-08-01T00:00:00Z")

    def graphql(request: httpx.Request):
        return {"data": {"open": {"nodes": [old]}, "recent": {"nodes": [_pull(600, "acme/portal", "feat/x", state="MERGED")]}}}

    github = _github(
        {("POST", "/graphql"): graphql, ("GET", "/repos/acme/portal/activity"): [_activity("push", "fix/size-limit", "2026-10-09T09:00:00Z")]}
    )
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    assert group_ids(result) == ["has_pr"]
    assert result.groups[1].items[0].badge == "#134"


def test_branch_names_travel_as_variables_and_never_inside_the_query_text():
    hostile = 'x") { viewer { login } } #'
    feed = [_push("acme/portal", hostile)]
    github = _github({("GET", "/users/alice/events"): feed, ("POST", "/graphql"): _graphql([])})
    run("github.branches", make_context(github, GITHUB_CONFIG))

    body = github.bodies("/graphql")[1]
    assert hostile not in body["query"] and "viewer" not in body["query"]
    assert body["variables"] == {"o0": "acme", "n0": "portal", "b0_0": f"refs/heads/{hostile}"}


def test_a_token_closed_to_graphql_still_names_the_branches():
    github = _github(
        _own_routes(
            graphql=httpx.Response(403, json={}),
            search__issues={"items": [_pull_search_item(531)]},
            repos__acme__portal={"full_name": "acme/portal", "default_branch": "master"},
            repos__acme__tools={"full_name": "acme/tools", "default_branch": "main"},
        )
    )
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    # The REST search still says where the member works.
    assert github.queries("/search/issues", "q") == ["is:pr author:@me"]
    assert "/repos/acme/portal/activity" in github.paths()

    assert group_ids(result) == ["pushed"]
    # Without GraphQL nothing says which branch has a pull request or was
    # deleted by someone else, so every branch left standing is named. The
    # default branch is still known, one plain request per repository.
    assert values(result, "pushed") == [
        "branch feat/card-pickers of acme/portal (default branch master)",
        "branch fix/layout of acme/portal (default branch master)",
        "branch fix/merged of acme/tools (default branch main)",
        "branch vanished of acme/tools (default branch main)",
    ]


def test_typing_a_repository_lists_its_branches_newest_first_and_filters_them():
    def graphql(request: httpx.Request):
        return {
            "data": {
                "repository": {
                    "nameWithOwner": "acme/portal",
                    "url": "https://github.com/acme/portal",
                    "isArchived": False,
                    "defaultBranchRef": {"name": "master"},
                    # GitHub orders branches by name, whatever is asked.
                    "refs": {
                        "nodes": [
                            _ref("feat/b", "2026-11-01T00:00:00Z"),
                            _ref("fix/a", "2026-11-10T00:00:00Z"),
                            _ref("master", "2026-11-12T00:00:00Z"),
                        ]
                    },
                }
            }
        }

    github = _github({("POST", "/graphql"): graphql})
    listed = run("github.branches", make_context(github, GITHUB_CONFIG, q="acme/portal"))
    assert [item.title for item in listed.groups[0].items] == ["fix/a", "feat/b"]

    narrowed = run("github.branches", make_context(github, GITHUB_CONFIG, q="acme/portal fix"))
    assert values(narrowed, "results") == ["branch fix/a of acme/portal (default branch master)"]
    first, second = github.bodies("/graphql")
    assert first["variables"] == {"owner": "acme", "name": "portal"}
    # The typed fragment is matched by GitHub across all branches, as a variable.
    assert second["variables"] == {"owner": "acme", "name": "portal", "needle": "fix"}
    assert "query: $needle" in first["query"] and "TAG_COMMIT_DATE" not in first["query"]
    assert not [path for path in github.paths() if path.endswith("/events") or path.endswith("/activity")]


def _workspace(repos: list[dict]):
    async def runtime_get(subpath, query_items):
        assert subpath == "api/workspace/repos"
        return 200, {"repos": repos, "truncated": False}

    return runtime_get


def test_work_in_the_assistants_workspace_is_offered_first_and_not_twice():
    repos = [
        {"path": "repos/portal", "repo": "acme/portal", "branch": "feat/card-pickers", "default_branch": "master", "ahead": 3, "dirty": False},
        {"path": "repos/tools", "repo": "acme/tools", "branch": "wip", "default_branch": "main", "ahead": 0, "dirty": True},
        # Nothing to show in a pull request: on the default branch, or clean and level.
        {"path": "repos/skills", "repo": "acme/skills", "branch": "main", "default_branch": "main", "ahead": 2, "dirty": True},
        {"path": "repos/agents", "repo": "acme/agents", "branch": "idle", "default_branch": "main", "ahead": 0, "dirty": False},
        {"path": "repos/local", "repo": "", "branch": "x", "default_branch": "", "ahead": 1, "dirty": False},
    ]
    github = _github(_own_routes())
    result = run("github.branches", make_context(github, GITHUB_CONFIG, runtime_get=_workspace(repos)))

    assert group_ids(result) == ["workspace", "has_pr"]
    assert values(result, "workspace") == [
        "branch feat/card-pickers of acme/portal (default branch master), checked out at repos/portal in the workspace",
        "branch wip of acme/tools (default branch main), checked out at repos/tools in the workspace",
    ]
    ahead, dirty = result.groups[0].items
    assert (ahead.status, ahead.count) == ("ahead", 3)
    assert (dirty.status, dirty.count) == ("uncommitted", None)
    assert ahead.fields["base"] == "master"


def test_the_workspace_alone_answers_when_github_is_out():
    repos = [{"path": "repos/portal", "repo": "acme/portal", "branch": "feat/x", "default_branch": "master", "ahead": 1, "dirty": False}]
    down = httpx.Response(503, text="down")
    github = _github(
        {("POST", "/graphql"): down, ("GET", "/search/issues"): down, ("GET", "/users/alice/events"): down, ("GET", "/user/repos"): down}
    )
    result = run("github.branches", make_context(github, GITHUB_CONFIG, runtime_get=_workspace(repos)))
    assert result.state == "ok" and group_ids(result) == ["workspace"]

    without = run("github.branches", make_context(github, GITHUB_CONFIG, user_id=2))
    assert without.state == "unavailable" and without.reason == "error"


def test_an_older_runtime_without_the_workspace_endpoint_is_simply_skipped():
    async def runtime_get(subpath, query_items):
        return 404, None

    github = _github({("GET", "/users/alice/events"): [], ("POST", "/graphql"): {"data": {}}})
    result = run("github.branches", make_context(github, GITHUB_CONFIG, runtime_get=runtime_get))
    assert result.state == "ok" and group_ids(result) == []


def test_enterprise_server_graphql_lives_beside_the_rest_api():
    def api_for(base_url):
        return GithubApi(make_context(Recorder(), {"github": {"enabled": True, "api_token": "t", "base_url": base_url}}))

    assert api_for("https://ghe.corp/api/v3/").graphql_url == "https://ghe.corp/api/graphql"
    assert api_for("").graphql_url == "https://api.github.com/graphql"
    assert api_for("https://api.corp.ghe.com").graphql_url == "https://api.corp.ghe.com/graphql"


# ----------------------------------------------------- repositories and paths


def test_recent_repositories_are_the_ones_the_member_works_in_and_a_search_by_name():
    mine = [
        {"full_name": "alice/notes", "description": "Notes", "pushed_at": "2026-11-01T08:00:00Z", "html_url": "https://github.com/alice/notes", "default_branch": "main"},
        {"full_name": "alice/old", "archived": True},
    ]
    github = _github(
        {
            ("GET", "/user/repos"): mine,
            ("POST", "/graphql"): _graphql([_pull(7, "acme/portal", "feat/a"), _pull(3, "alice/old", "feat/b")]),
            ("GET", "/users/alice/events"): [_push("acme/tools", "x", "2026-11-10T09:00:00Z")],
            ("GET", "/repos/acme/tools"): {"full_name": "acme/tools", "default_branch": "main"},
            ("GET", "/search/repositories"): {"items": [{"full_name": "acme/portal-ux", "default_branch": "main"}]},
        }
    )
    recent = run("github.repos", make_context(github, GITHUB_CONFIG))
    # Newest first across the three listings; the archived one is left out
    # even though a pull request names it.
    assert values(recent, "recent") == ["acme/portal", "acme/tools", "alice/notes"]
    by_pull, by_feed, own = recent.groups[0].items
    assert by_pull.fields == {"repo": "acme/portal", "base": "master"} and by_pull.meta == "2026-11-12T07:00:00Z"
    assert by_pull.url == "https://github.com/acme/portal"
    assert by_feed.fields == {"repo": "acme/tools"}
    assert own.fields == {"repo": "alice/notes", "base": "main"} and own.subtitle == "Notes"
    # "Everything I can push to, by last push" is other people's work in a
    # large organization, so the organization's repositories are not listed.
    assert github.queries("/user/repos", "affiliation") == ["owner,collaborator"]

    exact = run("github.repos", make_context(github, GITHUB_CONFIG, q="acme/tools"))
    assert values(exact, "results") == ["acme/tools"]

    searched = run("github.repos", make_context(github, GITHUB_CONFIG, q="portal"))
    assert values(searched, "results") == ["acme/portal-ux"]
    assert github.queries("/search/repositories", "q") == ["portal in:name fork:true"]


def test_paths_of_the_chosen_repository_are_read_once_and_matched_by_name_first():
    tree = {
        "tree": [
            {"path": "src", "type": "tree"},
            {"path": "README.md", "type": "blob"},
            {"path": "src/payments", "type": "tree"},
            {"path": "src/payments/client.py", "type": "blob"},
            {"path": "tests/payments/test_client.py", "type": "blob"},
            {"path": "docs/client-guide.md", "type": "blob"},
        ]
    }
    github = _github(
        {
            ("GET", "/repos/acme/portal"): {"full_name": "acme/portal", "default_branch": "master"},
            ("GET", "/repos/acme/portal/git/trees/master"): tree,
        }
    )
    top = run("github.paths", make_context(github, GITHUB_CONFIG, params={"repo": ["acme/portal"]}))
    assert values(top, "results") == ["src", "README.md"]
    assert top.groups[0].items[0].status == "folder"

    found = run("github.paths", make_context(github, GITHUB_CONFIG, params={"repo": ["acme/portal"]}, q="client"))
    assert values(found, "results") == ["src/payments/client.py", "tests/payments/test_client.py", "docs/client-guide.md"]
    assert github.paths().count("/repos/acme/portal/git/trees/master") == 1

    nothing = run("github.paths", make_context(github, GITHUB_CONFIG))
    assert nothing.state == "ok" and group_ids(nothing) == []
    hostile = run("github.paths", make_context(github, GITHUB_CONFIG, params={"repo": ["../../user"]}))
    assert group_ids(hostile) == [] and "/user" not in github.paths()[-1:]


def test_matching_paths_prefers_the_file_name_over_a_directory_it_passes_through():
    entries = [("payments/ledger.py", False), ("ledger", True), ("src/ledger/api.py", False)]
    assert match_paths(entries, "LEDGER") == [("payments/ledger.py", False), ("ledger", True), ("src/ledger/api.py", False)]
    assert match_paths(entries, "") == [("ledger", True)]


# ------------------------------------------------------------------------ CI

JENKINS = {"jenkins": {"enabled": True, "instances": [{"name": "ci", "url": "https://ci.corp/jenkins", "username": "alice", "token": "tok"}]}}


@pytest.mark.parametrize(
    "address, job",
    [
        ("https://ci.corp/jenkins/job/app/42/console", "https://ci.corp/jenkins/job/app"),
        ("https://ci.corp/jenkins/job/team/job/app/job/main/7/", "https://ci.corp/jenkins/job/team/job/app/job/main"),
        ("https://ci.corp:443/jenkins/job/app", "https://ci.corp/jenkins/job/app"),
        # Another host, another scheme, outside the instance, or climbing out of it.
        ("https://evil.example/jenkins/job/app/1", None),
        ("http://ci.corp/jenkins/job/app/1", None),
        ("https://ci.corp/other/job/app/1", None),
        ("https://ci.corp/jenkins/job/../../admin", None),
        ("https://ci.corp/jenkins/job/a%2F..%2Fb/1", None),
        ("https://ci.corp/jenkins/view/all", None),
        ("payments-api in prod", None),
        ("", None),
    ],
)
def test_only_jobs_on_the_members_own_jenkins_are_ever_asked(address, job):
    found = jenkins_job_url(address, JENKINS["jenkins"]["instances"])
    assert (found[1] if found else None) == job


def test_failed_builds_come_from_pull_request_checks_and_from_jobs_asked_about_before():
    checks = {
        "data": {
            "search": {
                "nodes": [
                    {
                        "number": 12,
                        "title": "Add retry",
                        "url": "https://github.com/acme/portal/pull/12",
                        "repository": {"nameWithOwner": "acme/portal"},
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "statusCheckRollup": {
                                            "contexts": {
                                                "nodes": [
                                                    {"__typename": "StatusContext", "context": "jenkins/pr", "state": "FAILURE", "targetUrl": "https://ci.corp/jenkins/job/app/42/"},
                                                    {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS", "targetUrl": "https://ci.corp/x"},
                                                    {"__typename": "CheckRun", "name": "unit", "conclusion": "TIMED_OUT", "detailsUrl": "https://github.com/acme/portal/runs/1"},
                                                    {"__typename": "CheckRun", "name": "no link", "conclusion": "FAILURE", "detailsUrl": "javascript:alert(1)"},
                                                ]
                                            }
                                        }
                                    }
                                }
                            ]
                        },
                    }
                ]
            }
        }
    }
    job = {
        "fullName": "team/app",
        "builds": [
            {"number": 44, "result": "SUCCESS", "timestamp": 1762934400000},
            {"number": 43, "result": "FAILURE", "timestamp": 1762930800000},
            {"number": 42, "result": "UNSTABLE", "timestamp": 1762927200000},
            {"number": 41, "result": None, "timestamp": 1762923600000},
        ],
    }
    server = _github({("POST", "/graphql"): checks, ("GET", "/jenkins/job/team/job/app/api/json"): job})
    context = make_context(
        server,
        {**GITHUB_CONFIG, **JENKINS},
        recent=["https://ci.corp/jenkins/job/team/job/app/40/console", "https://evil.example/job/x/1", "payments-api"],
    )
    result = run("ci.failed_builds", context)

    assert group_ids(result) == ["pr_checks", "jenkins"]
    assert values(result, "pr_checks") == ["https://ci.corp/jenkins/job/app/42/", "https://github.com/acme/portal/runs/1"]
    assert result.groups[0].items[0].subtitle == "acme/portal#12 Add retry"
    assert values(result, "jenkins") == ["https://ci.corp/jenkins/job/team/job/app/43/", "https://ci.corp/jenkins/job/team/job/app/42/"]
    assert [item.status for item in result.groups[1].items] == ["failed", "unstable"]
    assert result.groups[1].items[0].title == "team/app #43"
    assert server.bodies("/graphql")[0]["variables"] == {"q": "is:pr is:open author:@me archived:false sort:updated-desc"}

    # With another account's token the checks are those of the member's pull
    # requests, not of the account's.
    clear_cache()
    shared = _github(
        {("GET", "/users/alice"): {"login": "alice", "type": "User"}, ("POST", "/graphql"): checks},
        login="svc-bot",
    )
    run("ci.failed_builds", make_context(shared, GITHUB_CONFIG))
    assert shared.bodies("/graphql")[0]["variables"] == {"q": "is:pr is:open author:alice archived:false sort:updated-desc"}
    # Nothing was sent to the address that is not the member's Jenkins.
    assert {request.url.host for request in server.requests} == {"api.github.com", "ci.corp"}
    jenkins_call = next(request for request in server.requests if request.url.host == "ci.corp")
    assert jenkins_call.headers["authorization"].startswith("Basic ")


def test_builds_need_github_or_jenkins_and_work_with_either():
    neither = run("ci.failed_builds", make_context(Recorder(), {}))
    assert neither.state == "not_configured" and neither.connector == "jenkins"

    only_jenkins = Recorder({("GET", "/jenkins/job/app/api/json"): {"fullName": "app", "builds": [{"number": 5, "result": "FAILURE", "timestamp": 0}]}})
    result = run("ci.failed_builds", make_context(only_jenkins, JENKINS, q="https://ci.corp/jenkins/job/app/5/"))
    assert values(result, "jenkins") == ["https://ci.corp/jenkins/job/app/5/"]
    assert "/graphql" not in only_jenkins.paths()

    nothing_named = run("ci.failed_builds", make_context(Recorder(), JENKINS, user_id=3))
    assert nothing_named.state == "ok" and group_ids(nothing_named) == []
