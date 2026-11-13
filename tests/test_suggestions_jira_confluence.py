"""Jira and Confluence picker sources.

What matters is the query each source sends: "my issues" must mean the member
and not whoever owns a shared token, a typed search must not be able to carry
query syntax, and a server that lacks a function must cost one group, not the
picker.
"""
from __future__ import annotations

import httpx
import pytest

from app.services.suggestions import clear_cache
from tests._suggestions_helpers import (
    CONFLUENCE_CONFIG,
    JIRA_CONFIG,
    OWN,
    SHARED,
    Recorder,
    group_ids,
    make_context,
    run,
    values,
)

SEARCH = "/rest/api/2/search"


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_cache()
    yield
    clear_cache()


def _issue(key: str, summary: str = "Summary", status: str = "In Progress", issue_type: str = "Story") -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "issuetype": {"name": issue_type},
            "project": {"key": key.split("-")[0]},
            "updated": "2026-11-12T08:00:00.000+0000",
        },
    }


def _jira(by_jql=None, *, myself=None, user=None) -> Recorder:
    """A Jira that answers /search by the JQL it is sent."""

    def search(request: httpx.Request):
        jql = request.url.params.get("jql", "")
        answer = by_jql(jql) if by_jql else []
        if isinstance(answer, httpx.Response):
            return answer
        return {"issues": answer}

    routes = {
        ("GET", "/rest/api/2/myself"): myself or {"name": "alice", "key": "alice"},
        ("GET", SEARCH): search,
    }
    if user is not None:
        routes[("GET", "/rest/api/2/user")] = user
    return Recorder(routes)


# --------------------------------------------------------------------- issues


def test_own_credentials_list_what_the_member_viewed_and_what_is_assigned_to_them():
    def by_jql(jql):
        if "issueHistory()" in jql:
            return [_issue("ABC-1", "Viewed one"), _issue("ABC-2", "Viewed two")]
        return [_issue("ABC-2", "Viewed two"), _issue("ABC-3", "Assigned only")]

    jira = _jira(by_jql)
    result = run("jira.issues", make_context(jira, JIRA_CONFIG))

    assert result.state == "ok" and result.identity == "personal"
    assert group_ids(result) == ["viewed", "assigned"]
    assert values(result, "viewed") == ["ABC-1", "ABC-2"]
    # ABC-2 is listed once, in the first group that has it.
    assert values(result, "assigned") == ["ABC-3"]
    assert jira.queries(SEARCH, "jql") == [
        "issuekey in issueHistory() ORDER BY lastViewed DESC",
        "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC",
    ]

    row = result.groups[0].items[0]
    # The key goes into the prompt; the summary is only shown.
    assert row.value == "ABC-1" and row.title == "ABC-1" and row.subtitle == "Viewed one"
    assert row.badge == "In Progress"
    assert row.url == "https://jira.corp/browse/ABC-1"
    assert row.fields == {"key": "ABC-1", "project": "ABC", "type": "Story"}


def test_the_card_can_narrow_the_issue_types_and_lead_with_assigned_work():
    jira = _jira(lambda jql: [_issue("ABC-9")] if "assignee" in jql else [_issue("ABC-8")])
    context = make_context(jira, JIRA_CONFIG, params={"types": ["Epic", "Story"], "lead": ["assigned"]})

    result = run("jira.issues", context)

    assert group_ids(result) == ["assigned", "viewed"]
    assert jira.queries(SEARCH, "jql") == [
        'issuekey in issueHistory() AND issuetype in ("Epic", "Story") ORDER BY lastViewed DESC',
        'assignee = currentUser() AND resolution = Unresolved AND issuetype in ("Epic", "Story") ORDER BY updated DESC',
    ]


def test_an_issue_type_that_could_carry_query_syntax_is_left_out():
    jira = _jira(lambda jql: [])
    run("jira.issues", make_context(jira, JIRA_CONFIG, params={"types": ['Epic") OR project = SECRET --', "Bug"]}))

    assert all('issuetype in ("Bug")' in jql for jql in jira.queries(SEARCH, "jql"))
    assert not any("SECRET" in jql for jql in jira.queries(SEARCH, "jql"))


def test_a_shared_token_names_the_member_instead_of_the_token_owner():
    jira = _jira(
        lambda jql: [_issue("ABC-4")] if "assignee" in jql else [_issue("ABC-5")],
        myself={"name": "svc-efp", "key": "svc-efp"},
        user={"name": "alice", "key": "alice"},
    )
    result = run("jira.issues", make_context(jira, JIRA_CONFIG, modes=SHARED))

    assert result.identity == "shared"
    assert group_ids(result) == ["assigned", "reported"]
    jqls = jira.queries(SEARCH, "jql")
    assert jqls == [
        'assignee = "alice" AND resolution = Unresolved ORDER BY updated DESC',
        'reporter = "alice" AND resolution = Unresolved ORDER BY updated DESC',
    ]
    # The viewing history belongs to the service account, so it is not asked for.
    assert not any("issueHistory" in jql or "currentUser" in jql for jql in jqls)
    assert jira.queries("/rest/api/2/user", "username") == ["alice"]


def test_a_shared_token_whose_account_is_the_member_counts_as_their_own():
    jira = _jira(lambda jql: [_issue("ABC-1")], myself={"name": "Alice", "key": "alice"})
    result = run("jira.issues", make_context(jira, JIRA_CONFIG, modes=SHARED))

    assert result.identity == "personal"
    assert any("currentUser()" in jql for jql in jira.queries(SEARCH, "jql"))


def test_a_member_the_shared_account_cannot_find_gets_search_and_no_recent_list():
    jira = _jira(lambda jql: [_issue("ABC-7", "Login page")], myself={"name": "svc-efp"})
    listing = run("jira.issues", make_context(jira, JIRA_CONFIG, modes=SHARED))

    assert listing.state == "ok" and listing.identity == "unknown"
    assert group_ids(listing) == []
    assert jira.queries(SEARCH, "jql") == []

    searched = run("jira.issues", make_context(jira, JIRA_CONFIG, modes=SHARED, q="login"))
    assert values(searched, "results") == ["ABC-7"]


def test_a_service_account_that_may_not_browse_users_does_not_fail_the_picker():
    jira = _jira(lambda jql: [], myself={"name": "svc-efp"}, user=httpx.Response(403, json={}))
    result = run("jira.issues", make_context(jira, JIRA_CONFIG, modes=SHARED))

    assert result.state == "ok" and result.identity == "unknown"


def test_a_typed_key_or_a_pasted_browse_link_finds_that_issue_whatever_its_type():
    jira = _jira(lambda jql: [_issue("ABC-12", issue_type="Bug")])

    by_key = run("jira.issues", make_context(jira, JIRA_CONFIG, q="abc-12", params={"types": ["Epic"]}))
    by_link = run("jira.issues", make_context(jira, JIRA_CONFIG, q="https://jira.corp/browse/ABC-12?focusedId=1"))

    assert values(by_key, "results") == ["ABC-12"] and values(by_link, "results") == ["ABC-12"]
    assert jira.queries(SEARCH, "jql") == ['issuekey = "ABC-12"', 'issuekey = "ABC-12"']


def test_a_browse_link_of_another_jira_is_not_read_as_a_key_of_this_one():
    jira = _jira(lambda jql: [])
    run("jira.issues", make_context(jira, JIRA_CONFIG, q="https://other.example/browse/ABC-12"))

    assert not any("issuekey =" in jql for jql in jira.queries(SEARCH, "jql"))


def test_typed_text_searches_summaries_and_cannot_carry_query_syntax():
    jira = _jira(lambda jql: [_issue("ABC-20", "Login page")])
    result = run("jira.issues", make_context(jira, JIRA_CONFIG, q='login" OR project = SECRET ORDER BY', params={"types": ["Story"]}))

    assert values(result, "results") == ["ABC-20"]
    assert jira.queries(SEARCH, "jql") == [
        'summary ~ "login OR project SECRET ORDER BY*" AND issuetype in ("Story") ORDER BY updated DESC'
    ]


def test_a_server_without_the_history_function_still_lists_assigned_issues():
    def by_jql(jql):
        if "issueHistory" in jql:
            return httpx.Response(400, json={"errorMessages": ["Unable to find JQL function 'issueHistory()'."]})
        return [_issue("ABC-3")]

    result = run("jira.issues", make_context(_jira(by_jql), JIRA_CONFIG))

    assert result.state == "ok"
    assert group_ids(result) == ["assigned"]


def test_cloud_instances_use_the_search_endpoint_cloud_kept():
    config = {"jira": {"enabled": True, "instances": [{"url": "https://corp.atlassian.net", "username": "a@corp", "token": "t", "api_version": "3"}]}}
    jira = Recorder(
        {
            ("GET", "/rest/api/3/myself"): {"accountId": "5b10ac", "displayName": "Alice"},
            ("GET", "/rest/api/3/search/jql"): {"issues": [_issue("ABC-1")]},
        }
    )
    result = run("jira.issues", make_context(jira, config))

    assert values(result, "viewed") == ["ABC-1"]
    assert "/rest/api/3/search/jql" in jira.paths() and "/rest/api/2/search" not in jira.paths()


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"jira": {"enabled": False, "instances": [{"url": "https://jira.corp", "token": "t"}]}},
        {"jira": {"enabled": True, "instances": [{"url": "https://jira.corp"}]}},
        {"jira": {"enabled": True, "instances": [{"url": "https://jira.corp", "token": "t", "enabled": False}]}},
    ],
)
def test_a_connector_that_is_off_or_has_no_credentials_is_reported_as_not_configured(config):
    jira = Recorder()
    result = run("jira.issues", make_context(jira, config))

    assert result.state == "not_configured" and result.connector == "jira"
    assert jira.requests == []


@pytest.mark.parametrize(
    "answer, reason",
    [
        (httpx.Response(401, json={}), "unauthorized"),
        (httpx.Response(403, json={}), "unauthorized"),
        (httpx.Response(302, headers={"location": "https://sso.corp/login"}), "unauthorized"),
        (httpx.Response(429, json={}), "rate_limited"),
        (httpx.Response(503, text="down"), "error"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("no route"), "unreachable"),
    ],
)
def test_a_system_that_does_not_answer_is_a_state_with_a_reason_and_no_server_text(answer, reason):
    jira = Recorder({("GET", "/rest/api/2/myself"): answer})
    result = run("jira.issues", make_context(jira, JIRA_CONFIG))

    assert result.state == "unavailable" and result.reason == reason
    assert result.to_dict() == {"source": "jira.issues", "state": "unavailable", "groups": [], "connector": "jira", "reason": reason}


def test_the_request_leaves_through_the_proxy_the_connector_is_assigned():
    config = {
        **JIRA_CONFIG,
        "proxy": {
            "enabled": True,
            "default": "corp",
            "proxies": [{"name": "corp", "url": "http://proxy.corp:3128"}],
            "assignments": {"jira": "corp"},
        },
    }
    context = make_context(_jira(lambda jql: []), config)
    run("jira.issues", context)

    assert context.http.egress_log
    assert {entry for entry in context.http.egress_log} == {("jira", "jira.corp", "proxy")}


def test_an_answer_is_reused_for_the_same_member_and_never_for_another():
    jira = _jira(lambda jql: [_issue("ABC-1")])
    run("jira.issues", make_context(jira, JIRA_CONFIG))
    first = len(jira.requests)
    run("jira.issues", make_context(jira, JIRA_CONFIG))
    assert len(jira.requests) == first

    run("jira.issues", make_context(jira, JIRA_CONFIG, user_id=2, username="bob"))
    assert len(jira.requests) > first

    # Saving the connector bumps the revision, so the next picker reads afresh.
    before = len(jira.requests)
    run("jira.issues", make_context(jira, JIRA_CONFIG, revision=2))
    assert len(jira.requests) > before


# ------------------------------------------------------------------- projects


def test_projects_lead_with_the_ones_the_member_used_lately():
    everything = [{"key": "ABC", "name": "Alpha"}, {"key": "XYZ", "name": "Zeta billing"}, {"key": "OPS", "name": "Operations"}]

    def project(request: httpx.Request):
        return [everything[1]] if request.url.params.get("recent") else everything

    jira = Recorder({("GET", "/rest/api/2/project"): project})
    result = run("jira.projects", make_context(jira, JIRA_CONFIG))

    assert values(result, "recent") == ["XYZ"]
    assert values(result, "all") == ["ABC", "OPS"]
    assert jira.queries("/rest/api/2/project", "recent") == ["10"]

    searched = run("jira.projects", make_context(jira, JIRA_CONFIG, q="op"))
    # A key that starts with what was typed comes before a name that contains it.
    assert values(searched, "results") == ["OPS"]
    by_name = run("jira.projects", make_context(jira, JIRA_CONFIG, q="billing"))
    assert values(by_name, "results") == ["XYZ"]


# ----------------------------------------------------------------- confluence


def _page(page_id: str, title: str, space: str = "ENG") -> dict:
    return {
        "id": page_id,
        "title": title,
        "space": {"key": space, "name": "Engineering"},
        "version": {"when": "2026-11-11T10:00:00.000Z"},
        "_links": {"webui": f"/display/{space}/{title.replace(' ', '+')}"},
    }


def _confluence(by_cql, *, current=None, user=None, prefix="") -> Recorder:
    def search(request: httpx.Request):
        answer = by_cql(request.url.params.get("cql", ""))
        if isinstance(answer, httpx.Response):
            return answer
        return {"results": answer, "_links": {"base": "https://wiki.corp" + prefix}}

    routes = {
        ("GET", f"{prefix}/rest/api/user/current"): current or {"username": "alice", "userKey": "abc"},
        ("GET", f"{prefix}/rest/api/content/search"): search,
    }
    if user is not None:
        routes[("GET", f"{prefix}/rest/api/user")] = user
    return Recorder(routes)


def test_pages_the_member_viewed_and_edited_come_back_as_links():
    def by_cql(cql):
        if "recentlyViewedContent" in cql:
            return [_page("1", "Release plan"), _page("2", "Refund flow")]
        return [_page("2", "Refund flow"), _page("3", "Ledger design")]

    wiki = _confluence(by_cql)
    result = run("confluence.pages", make_context(wiki, CONFLUENCE_CONFIG))

    assert group_ids(result) == ["viewed", "edited"]
    assert values(result, "viewed") == [
        "https://wiki.corp/display/ENG/Release+plan",
        "https://wiki.corp/display/ENG/Refund+flow",
    ]
    assert values(result, "edited") == ["https://wiki.corp/display/ENG/Ledger+design"]
    assert wiki.queries("/rest/api/content/search", "cql") == [
        "type = page AND id in recentlyViewedContent(8)",
        "type = page AND contributor = currentUser() ORDER BY lastmodified DESC",
    ]
    row = result.groups[0].items[0]
    assert row.title == "Release plan" and row.subtitle == "Engineering"
    assert row.fields == {"id": "1", "space": "ENG"}


def test_a_confluence_without_the_recently_viewed_function_still_lists_edited_pages():
    def by_cql(cql):
        if "recentlyViewedContent" in cql:
            return httpx.Response(400, json={"message": "Could not parse cql"})
        return [_page("3", "Ledger design")]

    result = run("confluence.pages", make_context(_confluence(by_cql), CONFLUENCE_CONFIG))
    assert group_ids(result) == ["edited"]


def test_a_shared_confluence_account_lists_what_the_member_edited():
    wiki = _confluence(lambda cql: [_page("3", "Ledger design")], current={"username": "svc-wiki"}, user={"username": "alice"})
    result = run("confluence.pages", make_context(wiki, CONFLUENCE_CONFIG, modes=SHARED))

    assert result.identity == "shared"
    assert wiki.queries("/rest/api/content/search", "cql") == [
        'type = page AND contributor = "alice" ORDER BY lastmodified DESC'
    ]


def test_typed_text_searches_page_titles_without_query_syntax():
    wiki = _confluence(lambda cql: [_page("9", "Refund flow")])
    result = run("confluence.pages", make_context(wiki, CONFLUENCE_CONFIG, q='refund" OR space = HR'))

    assert values(result, "results") == ["https://wiki.corp/display/ENG/Refund+flow"]
    assert wiki.queries("/rest/api/content/search", "cql") == [
        'type = page AND title ~ "refund OR space HR*" ORDER BY lastmodified DESC'
    ]


def test_a_pasted_page_link_needs_no_lookup():
    wiki = _confluence(lambda cql: [])
    result = run("confluence.pages", make_context(wiki, CONFLUENCE_CONFIG, q="https://wiki.corp/display/ENG/Refund+flow"))

    assert result.state == "ok" and group_ids(result) == []
    assert "/rest/api/content/search" not in wiki.paths()


def test_a_cloud_site_is_read_under_wiki():
    config = {"confluence": {"enabled": True, "instances": [{"url": "https://corp.atlassian.net", "username": "a@corp", "token": "t"}]}}
    wiki = _confluence(lambda cql: [_page("1", "Release plan")], current={"accountId": "5b10ac"}, prefix="/wiki")
    result = run("confluence.pages", make_context(wiki, config))

    assert values(result, "viewed") == ["https://wiki.corp/wiki/display/ENG/Release+plan"]
    assert all(path.startswith("/wiki/rest/api/") for path in wiki.paths())


def test_modes_default_to_the_members_own_for_rows_that_were_never_classified():
    # make_context() uses OWN unless told otherwise; this pins that SHARED is
    # an explicit choice in these tests and not an accident of an empty dict.
    assert OWN["jira"] == "custom" and SHARED == {}
