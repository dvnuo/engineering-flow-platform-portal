"""GitHub and CI picker sources.

"Branches I pushed lately" is assembled from the member's event feed and one
GraphQL request; these tests pin what is asked, what is offered, and what is
held back (the default branch, a deleted branch, a branch already merged).
"""
from __future__ import annotations

import httpx
import pytest

from app.services.suggestions import clear_cache
from app.services.suggestions.ci import jenkins_job_url
from app.services.suggestions.github import GithubApi, match_paths, pushed_branches
from tests._suggestions_helpers import GITHUB_CONFIG, SHARED, Recorder, group_ids, make_context, run, values

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


def test_own_token_lists_reviews_waiting_and_the_members_own_pull_requests():
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


def test_a_shared_token_names_the_member_by_their_enterprise_login():
    github = _github(
        {
            ("GET", "/users/alice_corp"): {"login": "alice_corp"},
            ("GET", "/search/issues"): {"items": [_pull_search_item(3)]},
        },
        login="svc-bot",
    )
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, modes=SHARED, suffix="_corp"))

    assert result.identity == "shared"
    assert github.queries("/search/issues", "q") == [
        "is:pr is:open review-requested:alice_corp archived:false",
        "is:pr is:open author:alice_corp archived:false",
    ]


def test_a_shared_token_that_is_the_member_after_all_is_their_own():
    github = _github({("GET", "/search/issues"): {"items": []}}, login="Alice_Corp")
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, modes=SHARED, suffix="_corp"))

    assert result.identity == "personal"
    assert all("@me" in q for q in github.queries("/search/issues", "q"))


def test_a_member_github_does_not_know_gets_no_list():
    github = _github({}, login="svc-bot")
    result = run("github.pull_requests", make_context(github, GITHUB_CONFIG, modes=SHARED))

    assert result.state == "ok" and result.identity == "unknown" and group_ids(result) == []
    assert "/search/issues" not in github.paths()


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


def test_the_event_feed_yields_each_pushed_branch_once_newest_first():
    events = [
        _push("acme/portal", "feat/a", "2026-11-12T09:00:00Z"),
        {"type": "DeleteEvent", "repo": {"name": "acme/tools"}, "payload": {"ref": "old", "ref_type": "branch"}},
        _push("acme/tools", "old", "2026-11-11T09:00:00Z"),
        _push("acme/portal", "feat/a", "2026-11-10T09:00:00Z"),
        {"type": "CreateEvent", "repo": {"name": "acme/skills"}, "payload": {"ref": "chore/b", "ref_type": "branch"}, "created_at": "2026-11-09T09:00:00Z"},
        {"type": "CreateEvent", "repo": {"name": "acme/skills"}, "payload": {"ref": "v1.0", "ref_type": "tag"}},
        {"type": "PushEvent", "repo": {"name": "acme/portal"}, "payload": {"ref": "refs/tags/v2"}},
        {"type": "WatchEvent", "repo": {"name": "acme/portal"}, "payload": {}},
        "not an event",
    ]

    assert pushed_branches(events) == [
        ("acme/portal", "feat/a", "2026-11-12T09:00:00Z"),
        ("acme/skills", "chore/b", "2026-11-09T09:00:00Z"),
    ]
    # Pushed again after it was deleted: the branch is back.
    again = [_push("acme/tools", "old"), {"type": "DeleteEvent", "repo": {"name": "acme/tools"}, "payload": {"ref": "old", "ref_type": "branch"}}]
    assert [row[:2] for row in pushed_branches(again)] == [("acme/tools", "old")]
    assert pushed_branches(None) == [] and pushed_branches({"message": "x"}) == []


def _ref(name: str, when: str, pulls: list[dict] | None = None) -> dict:
    return {"name": name, "target": {"committedDate": when}, "associatedPullRequests": {"nodes": pulls or []}}


def _branch_feed() -> list[dict]:
    return [
        _push("acme/portal", "feat/card-pickers", "2026-11-12T09:00:00Z"),
        _push("acme/portal", "master", "2026-11-12T08:00:00Z"),
        _push("acme/portal", "fix/layout", "2026-11-11T09:00:00Z"),
        _push("acme/tools", "fix/merged", "2026-11-10T09:00:00Z"),
        _push("acme/tools", "gone", "2026-11-09T09:00:00Z"),
        _push("acme/secret", "feat/x", "2026-11-08T09:00:00Z"),
    ]


def _branch_graphql(request: httpx.Request):
    return {
        "data": {
            "r0": {
                "nameWithOwner": "acme/portal",
                "url": "https://github.com/acme/portal",
                "isArchived": False,
                "defaultBranchRef": {"name": "master"},
                "b0": _ref("feat/card-pickers", "2026-11-12T08:59:00Z"),
                "b1": _ref("master", "2026-11-12T08:00:00Z"),
                "b2": _ref("fix/layout", "2026-11-11T08:00:00Z", [{"number": 531, "url": "https://github.com/acme/portal/pull/531", "state": "OPEN", "isDraft": False}]),
            },
            "r1": {
                "nameWithOwner": "acme/tools",
                "url": "https://github.com/acme/tools",
                "isArchived": False,
                "defaultBranchRef": {"name": "main"},
                "b0": _ref("fix/merged", "2026-11-10T08:00:00Z", [{"number": 90, "url": "u", "state": "MERGED", "isDraft": False}]),
                "b1": None,
            },
            # A repository the token can no longer see comes back null.
            "r2": None,
        },
        "errors": [{"message": "Could not resolve to a Repository"}],
    }


def test_pushed_branches_are_split_by_whether_a_pull_request_is_open():
    github = _github({("GET", "/users/alice/events"): _branch_feed(), ("POST", "/graphql"): _branch_graphql})
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    assert group_ids(result) == ["no_pr", "has_pr"]
    # Only identifiers reach the prompt: branch, repository, base.
    assert values(result, "no_pr") == ["branch feat/card-pickers of acme/portal (base master)"]
    assert values(result, "has_pr") == ["branch fix/layout of acme/portal (base master)"]
    ready = result.groups[0].items[0]
    assert ready.title == "feat/card-pickers" and ready.subtitle == "acme/portal"
    assert ready.fields == {"repo": "acme/portal", "branch": "feat/card-pickers", "base": "master"}
    assert ready.url == "https://github.com/acme/portal/tree/feat/card-pickers"
    opened = result.groups[1].items[0]
    assert opened.badge == "#531" and opened.status == "open"
    assert opened.url == "https://github.com/acme/portal/pull/531"
    # The default branch, the merged one, the deleted one and the unreadable
    # repository are not offered.
    offered = [item.title for group in result.groups for item in group.items]
    assert offered == ["feat/card-pickers", "fix/layout"]


def test_branch_names_travel_as_variables_and_never_inside_the_query_text():
    hostile = 'x") { viewer { login } } #'
    feed = [_push("acme/portal", hostile)]
    github = _github({("GET", "/users/alice/events"): feed, ("POST", "/graphql"): {"data": {"r0": None}}})
    run("github.branches", make_context(github, GITHUB_CONFIG))

    body = github.bodies("/graphql")[0]
    assert hostile not in body["query"] and "viewer" not in body["query"]
    assert body["variables"] == {"o0": "acme", "n0": "portal", "b0_0": f"refs/heads/{hostile}"}


def test_a_token_closed_to_graphql_still_names_the_branches_from_the_feed():
    github = _github({("GET", "/users/alice/events"): _branch_feed()[:1], ("POST", "/graphql"): httpx.Response(403, json={})})
    result = run("github.branches", make_context(github, GITHUB_CONFIG))

    assert group_ids(result) == ["pushed"]
    assert values(result, "pushed") == ["branch feat/card-pickers of acme/portal"]


def test_typing_a_repository_lists_its_branches_and_filters_them():
    def graphql(request: httpx.Request):
        return {
            "data": {
                "repository": {
                    "nameWithOwner": "acme/portal",
                    "url": "https://github.com/acme/portal",
                    "isArchived": False,
                    "defaultBranchRef": {"name": "master"},
                    "refs": {"nodes": [_ref("master", "3"), _ref("fix/a", "2"), _ref("feat/b", "1")]},
                }
            }
        }

    github = _github({("POST", "/graphql"): graphql})
    listed = run("github.branches", make_context(github, GITHUB_CONFIG, q="acme/portal"))
    assert [item.title for item in listed.groups[0].items] == ["fix/a", "feat/b"]

    narrowed = run("github.branches", make_context(github, GITHUB_CONFIG, q="acme/portal fix"))
    assert values(narrowed, "results") == ["branch fix/a of acme/portal (base master)"]
    assert github.bodies("/graphql")[0]["variables"] == {"owner": "acme", "name": "portal"}
    assert "/users/alice/events" not in github.paths()


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
    github = _github({("GET", "/users/alice/events"): _branch_feed(), ("POST", "/graphql"): _branch_graphql})
    result = run("github.branches", make_context(github, GITHUB_CONFIG, runtime_get=_workspace(repos)))

    assert group_ids(result) == ["workspace", "has_pr"]
    assert values(result, "workspace") == [
        "branch feat/card-pickers of acme/portal (base master), checked out at repos/portal in the workspace",
        "branch wip of acme/tools (base main), checked out at repos/tools in the workspace",
    ]
    ahead, dirty = result.groups[0].items
    assert (ahead.status, ahead.count) == ("ahead", 3)
    assert (dirty.status, dirty.count) == ("uncommitted", None)


def test_the_workspace_alone_answers_when_github_is_out():
    repos = [{"path": "repos/portal", "repo": "acme/portal", "branch": "feat/x", "default_branch": "master", "ahead": 1, "dirty": False}]
    github = _github({("GET", "/users/alice/events"): httpx.Response(503, text="down")})
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


def test_repositories_the_member_pushed_to_lately_and_a_search_by_name():
    mine = [
        {"full_name": "acme/portal", "description": "Web", "pushed_at": "2026-11-12T08:00:00Z", "html_url": "https://github.com/acme/portal", "default_branch": "master"},
        {"full_name": "acme/old", "archived": True},
    ]
    github = _github(
        {
            ("GET", "/user/repos"): mine,
            ("GET", "/repos/acme/tools"): {"full_name": "acme/tools", "default_branch": "main"},
            ("GET", "/search/repositories"): {"items": [{"full_name": "acme/portal-ux", "default_branch": "main"}]},
        }
    )
    recent = run("github.repos", make_context(github, GITHUB_CONFIG))
    assert values(recent, "recent") == ["acme/portal"]
    assert recent.groups[0].items[0].fields == {"repo": "acme/portal", "base": "master"}
    assert github.queries("/user/repos", "sort") == ["pushed"]

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
