"""The connector request proxy of the app SDK (app/services/app_sdk/connector_proxy.py).

Every request here is answered by ``httpx.MockTransport``: the tests prove
what the proxy sends, refuses and returns, not how a real Jira or Splunk
answers it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

import httpx
import pytest

from app.services.app_sdk import _outbound, connector_proxy
from app.services.app_sdk.connector_proxy import ConnectorProxyError
from app.services.tls_trust import trust_context

TOKEN = "tok-9f8e7d6c5b4a3210"
PASSWORD = "pw-Sup3rS3cret!"
MIB = 1024 * 1024


def basic(username: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{secret}".encode("utf-8")).decode("ascii")


def instances(kind: str, *rows: dict, **section) -> dict:
    return {kind: {"enabled": True, "instances": list(rows), **section}}


BASE_URLS = {
    "jira": "https://jira.example.test",
    "confluence": "https://wiki.example.test/confluence",
    "github": "https://api.github.com",
    "jenkins": "https://jenkins.example.test",
    "nexus": "https://nexus.example.test",
    "splunk": "https://splunk.example.test:8089",
}

CONFIGS = {
    "jira": instances("jira", {"name": "main", "url": "https://jira.example.test", "username": "svc", "token": TOKEN}),
    "confluence": instances("confluence", {"name": "wiki", "url": "https://wiki.example.test/confluence/", "token": TOKEN}),
    "github": {"github": {"enabled": True, "api_token": TOKEN}},
    "jenkins": instances("jenkins", {"name": "ci", "url": "https://jenkins.example.test", "username": "svc", "token": TOKEN}),
    "nexus": instances("nexus", {"name": "repo", "url": "https://nexus.example.test", "username": "svc", "password": PASSWORD}),
    "splunk": instances("splunk", {"name": "prod", "url": "https://splunk.example.test:8089", "token": TOKEN}),
}
JIRA = CONFIGS["jira"]


class Upstream:
    """A mock transport that remembers what it was asked."""

    def __init__(self, handler=None):
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda request: httpx.Response(200, json={"ok": True}))

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        return self._handler(request)

    def client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), **kwargs)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def send(upstream: Upstream | None = None, *, config=JIRA, connector_type="jira", method="GET", path="/rest/api/2/myself", **kwargs) -> dict:
    upstream = upstream or Upstream()
    return asyncio.run(
        connector_proxy.request(
            config=config,
            connector_type=connector_type,
            method=method,
            path=path,
            client=upstream.client(),
            **kwargs,
        )
    )


def refused(upstream: Upstream | None = None, **kwargs) -> ConnectorProxyError:
    with pytest.raises(ConnectorProxyError) as caught:
        send(upstream, **kwargs)
    return caught.value


class ChunkStream(httpx.AsyncByteStream):
    """A body sent in pieces; counts how many were asked for and can pause or fail between them."""

    def __init__(self, chunks, *, pause: float = 0.0, error: Exception | None = None):
        self._chunks = chunks
        self._pause = pause
        self._error = error
        self.sent = 0

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._pause:
                await asyncio.sleep(self._pause)
            self.sent += 1
            yield chunk
        if self._error is not None:
            raise self._error

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------- which connectors


def test_the_six_supported_types():
    assert connector_proxy.SUPPORTED == ("jira", "confluence", "github", "jenkins", "nexus", "splunk")


@pytest.mark.parametrize("kind", connector_proxy.SUPPORTED)
def test_describe_names_the_address_and_nothing_else(kind):
    described = connector_proxy.describe(CONFIGS[kind], kind)

    assert described == {"type": kind, "base_url": BASE_URLS[kind]}


@pytest.mark.parametrize("kind", connector_proxy.SUPPORTED)
def test_describe_is_none_for_a_connector_the_owner_has_not_set_up(kind):
    assert connector_proxy.describe({}, kind) is None
    assert connector_proxy.describe({kind: {"enabled": False}}, kind) is None
    assert connector_proxy.describe({kind: {**CONFIGS[kind][kind], "enabled": False}}, kind) is None
    assert connector_proxy.describe(None, kind) is None


@pytest.mark.parametrize("kind", ["aws", "pgsql", "llm", "proxy", "browserstack", "local_bridge", "", None, 7])
def test_describe_is_none_for_anything_but_the_six(kind):
    config = {"aws": {"enabled": True, "username": "u"}, "pgsql": {"enabled": True, "instances": [{"host": "db"}]}}

    assert connector_proxy.describe(config, kind) is None


def test_describe_never_shows_a_login_written_into_the_address():
    config = instances("jira", {"url": "https://admin:hunter2secret@jira.example.test:8443/jira/?os_authType=basic", "token": TOKEN})

    assert connector_proxy.describe(config, "jira") == {"type": "jira", "base_url": "https://jira.example.test:8443/jira"}


@pytest.mark.parametrize("kind", ["aws", "pgsql", "llm", "local_bridge", "JIRA2", "", None])
def test_a_request_to_anything_but_the_six_is_invalid(kind):
    upstream = Upstream()

    error = refused(upstream, config={**JIRA, "aws": {"enabled": True}}, connector_type=kind)

    assert (error.code, error.status) == ("invalid_request", 400)
    assert upstream.requests == []


def test_the_type_is_read_without_regard_to_case():
    upstream = Upstream()

    send(upstream, connector_type=" Jira ")

    assert upstream.last.url.host == "jira.example.test"


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("jira", {}),
        ("jira", {"jira": {"enabled": False, "instances": JIRA["jira"]["instances"]}}),
        ("jira", instances("jira")),
        ("jira", instances("jira", {"url": "https://jira.example.test"})),  # no credential
        ("jira", instances("jira", {"url": "https://jira.example.test", "username": "svc"})),
        ("jira", instances("jira", {"username": "svc", "token": TOKEN})),  # no address
        ("jira", instances("jira", {"url": "https://jira.example.test", "token": TOKEN, "enabled": False})),
        ("jira", instances("jira", {"url": "ftp://jira.example.test", "token": TOKEN})),
        ("jira", instances("jira", {"url": "jira.example.test", "token": TOKEN})),
        ("confluence", instances("confluence", {"url": "https://wiki.example.test", "password": PASSWORD})),
        ("github", {"github": {"enabled": True}}),
        ("github", {"github": {"enabled": False, "api_token": TOKEN}}),
        ("github", {"github": {"enabled": True, "api_token": TOKEN, "base_url": "file:///etc/passwd"}}),
        ("jenkins", instances("jenkins")),
        ("jenkins", instances("jenkins", {"name": "ci", "username": "svc", "token": TOKEN})),
        ("nexus", {"nexus": {"enabled": False, "instances": [{"url": "https://nexus.example.test"}]}}),
        ("splunk", instances("splunk", {"url": "https://splunk.example.test:8089"})),  # Splunk has no anonymous mode
        ("splunk", instances("splunk", {"url": "https://splunk.example.test:8089", "username": "svc"})),
        ("splunk", instances("splunk", {"url": "https://splunk.example.test:99999", "token": TOKEN})),
    ],
)
def test_a_connector_that_lacks_what_the_call_needs_is_not_configured(kind, config):
    upstream = Upstream()

    error = refused(upstream, config=config, connector_type=kind)

    assert (error.code, error.status) == ("connector_not_configured", 424)
    assert upstream.requests == []
    assert connector_proxy.describe(config, kind) is None


# ---------------------------------------------------------------- how each connector signs in


@pytest.mark.parametrize(
    ("kind", "config", "expected"),
    [
        # Jira and Confluence on a company-hosted site: a token is a personal access token and signs in as
        # Bearer, with the username left out (Data Center answers 401 to it as Basic); only a row without a
        # token signs in with its username and password. Atlassian Cloud has its own test below.
        ("jira", instances("jira", {"url": BASE_URLS["jira"], "username": "svc", "token": TOKEN}), f"Bearer {TOKEN}"),
        ("jira", instances("jira", {"url": BASE_URLS["jira"], "token": TOKEN}), f"Bearer {TOKEN}"),
        ("jira", instances("jira", {"url": BASE_URLS["jira"], "username": "svc", "password": PASSWORD}), basic("svc", PASSWORD)),
        ("jira", instances("jira", {"url": BASE_URLS["jira"], "username": "svc", "token": TOKEN, "password": PASSWORD}), f"Bearer {TOKEN}"),
        ("confluence", instances("confluence", {"url": BASE_URLS["confluence"], "username": "svc", "token": TOKEN}), f"Bearer {TOKEN}"),
        ("confluence", instances("confluence", {"url": BASE_URLS["confluence"], "token": TOKEN}), f"Bearer {TOKEN}"),
        ("confluence", instances("confluence", {"url": BASE_URLS["confluence"], "username": "svc", "password": PASSWORD}), basic("svc", PASSWORD)),
        # GitHub: the API token as Bearer.
        ("github", {"github": {"enabled": True, "api_token": TOKEN}}, f"Bearer {TOKEN}"),
        # Jenkins and Nexus: Basic with the token, else the password; nothing at all when anonymous.
        ("jenkins", instances("jenkins", {"url": BASE_URLS["jenkins"], "username": "svc", "token": TOKEN}), basic("svc", TOKEN)),
        ("jenkins", instances("jenkins", {"url": BASE_URLS["jenkins"], "username": "svc", "password": PASSWORD}), basic("svc", PASSWORD)),
        ("jenkins", instances("jenkins", {"url": BASE_URLS["jenkins"], "username": "svc", "token": TOKEN, "password": PASSWORD}), basic("svc", TOKEN)),
        ("jenkins", instances("jenkins", {"url": BASE_URLS["jenkins"]}), None),
        ("jenkins", instances("jenkins", {"url": BASE_URLS["jenkins"], "token": TOKEN}), None),  # a secret without a username signs nothing
        ("nexus", instances("nexus", {"url": BASE_URLS["nexus"], "username": "svc", "token": TOKEN}), basic("svc", TOKEN)),
        ("nexus", instances("nexus", {"url": BASE_URLS["nexus"], "username": "svc", "password": PASSWORD}), basic("svc", PASSWORD)),
        ("nexus", instances("nexus", {"url": BASE_URLS["nexus"]}), None),
        # Splunk: a token as Bearer, else Basic.
        ("splunk", instances("splunk", {"url": BASE_URLS["splunk"], "token": TOKEN}), f"Bearer {TOKEN}"),
        ("splunk", instances("splunk", {"url": BASE_URLS["splunk"], "username": "svc", "password": PASSWORD}), basic("svc", PASSWORD)),
        ("splunk", instances("splunk", {"url": BASE_URLS["splunk"], "username": "svc", "password": PASSWORD, "token": TOKEN}), f"Bearer {TOKEN}"),
    ],
)
def test_each_connector_signs_the_request_the_way_its_test_button_does(kind, config, expected):
    upstream = Upstream()

    send(upstream, config=config, connector_type=kind, path="/probe")

    request = upstream.last
    assert request.headers.get("authorization") == expected
    assert str(request.url) == f"{BASE_URLS[kind]}/probe"


@pytest.mark.parametrize(
    ("kind", "url"),
    [
        ("jira", "https://acme.atlassian.net"),
        ("confluence", "https://acme.atlassian.net/wiki"),
        ("jira", "https://acme.jira.com"),
    ],
)
def test_an_atlassian_cloud_token_signs_in_as_basic_with_the_account_email(kind, url):
    # On Atlassian Cloud a token is an API token: Basic, together with the account's email.
    upstream = Upstream()
    send(upstream, config=instances(kind, {"url": url, "username": "svc@example.test", "token": TOKEN}), connector_type=kind, path="/probe")
    assert upstream.last.headers.get("authorization") == basic("svc@example.test", TOKEN)
    assert str(upstream.last.url) == f"{url}/probe"

    # Without an email there is nothing to pair it with: Bearer, as everywhere.
    upstream = Upstream()
    send(upstream, config=instances(kind, {"url": url, "token": TOKEN}), connector_type=kind, path="/probe")
    assert upstream.last.headers.get("authorization") == f"Bearer {TOKEN}"


def test_the_proxy_signs_with_the_function_the_test_button_uses():
    # One rule for the Test button, the assistants and the apps: when it changes, all three follow.
    import inspect

    assert "connector_http.instance_auth_headers(instance)" in inspect.getsource(connector_proxy)


def test_jira_uses_the_first_row_that_is_on_and_complete():
    config = instances(
        "jira",
        {"name": "off", "url": "https://jira-off.example.test", "token": "tok-off-0000000000", "enabled": False},
        {"name": "empty", "url": "https://jira-empty.example.test"},
        {"name": "main", "url": "https://jira-main.example.test", "token": TOKEN},
        {"name": "second", "url": "https://jira-second.example.test", "token": "tok-second-00000000"},
    )
    upstream = Upstream()

    send(upstream, config=config)

    assert upstream.last.url.host == "jira-main.example.test"
    assert upstream.last.headers["authorization"] == f"Bearer {TOKEN}"
    assert connector_proxy.describe(config, "jira")["base_url"] == "https://jira-main.example.test"


@pytest.mark.parametrize("kind", ["jenkins", "nexus", "splunk"])
def test_named_instance_connectors_use_the_default_instance(kind):
    rows = (
        {"name": "first", "url": "https://first.example.test", "token": "tok-first-000000000", "username": "a"},
        {"name": "Second", "url": "https://second.example.test", "token": TOKEN, "username": "svc"},
    )
    upstream = Upstream()

    send(upstream, config=instances(kind, *rows, default_instance="second"), connector_type=kind, path="/x")
    assert upstream.last.url.host == "second.example.test"

    send(upstream, config=instances(kind, *rows), connector_type=kind, path="/x")
    assert upstream.last.url.host == "first.example.test"

    # A default that is switched off is skipped, as the assistant's tool skips it.
    off = ({**rows[0], "enabled": False}, rows[1])
    send(upstream, config=instances(kind, *off, default_instance="first"), connector_type=kind, path="/x")
    assert upstream.last.url.host == "second.example.test"


def test_github_enterprise_requests_stay_under_its_api_path():
    config = {"github": {"enabled": True, "api_token": TOKEN, "base_url": "https://ghe.example.test/api/v3/"}}
    upstream = Upstream()

    send(upstream, config=config, connector_type="github", path="/repos/acme/app/pulls", query={"state": "open"})

    assert str(upstream.last.url) == "https://ghe.example.test/api/v3/repos/acme/app/pulls?state=open"
    assert connector_proxy.describe(config, "github") == {"type": "github", "base_url": "https://ghe.example.test/api/v3"}


# ---------------------------------------------------------------- method


@pytest.mark.parametrize("method", ["GET", "HEAD", "get", " head "])
def test_reading_is_always_allowed(method):
    upstream = Upstream()

    send(upstream, method=method)

    assert upstream.last.method == method.strip().upper()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_writing_needs_the_owner_to_have_allowed_it(method):
    upstream = Upstream()

    error = refused(upstream, method=method, json_body={"fields": {}})
    assert (error.code, error.status) == ("method_not_allowed", 403)
    assert upstream.requests == []

    send(upstream, method=method, json_body={"fields": {}}, allow_write=True)
    assert upstream.last.method == method.upper()


@pytest.mark.parametrize("method", ["OPTIONS", "TRACE", "CONNECT", "PROPFIND", "GET /x HTTP/1.1", "", None, 7, ["GET"]])
def test_no_other_method_is_ever_sent(method):
    upstream = Upstream()

    for allow_write in (False, True):
        error = refused(upstream, method=method, allow_write=allow_write)
        assert (error.code, error.status) == ("method_not_allowed", 403)
    assert upstream.requests == []


# ---------------------------------------------------------------- path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "rest/api/2/myself",
        "?jql=x",
        # A host.
        "//evil.example/x",
        "///evil.example/x",
        "/%2f%2fevil.example/x",
        "http://evil.example/x",
        "https://evil.example",
        "HTTPS://evil.example/x",
        "@evil.example",
        "@evil.example/x",
        "evil.example/x",
        ":8443/x",
        # Backslashes.
        "\\\\evil.example\\x",
        "/\\evil.example/x",
        "/a\\b",
        "/a%5cb",
        "/a%5C..%5Cb",
        "/a%255cb",
        # ".." segments, plain and percent-encoded.
        "/..",
        "/../admin",
        "/a/../b",
        "/a/b/..",
        "/..%2f",
        "/..%2Fadmin",
        "/%2e%2e/",
        "/%2E%2E/admin",
        "/a/%2e%2e/b",
        "/.%2e/x",
        "/%2e./x",
        "/a/..%2f..%2fadmin",
        "/%252e%252e/x",
        "/%25252e%25252e/x",
        "/..;/x",
        "/a/..;jsessionid=1/b",
        "/%2e%2e;x/y",
        # Full-width dots, slash and backslash: ".." and "//" to a server that normalizes.
        "/\uff0e\uff0e/admin",
        "/%EF%BC%8E%EF%BC%8E/admin",
        "/a/..\uff0fadmin",
        "/a/..\uff3cadmin",
        "/\uff0fevil.example/x",
        # Control characters.
        "/a\x00b",
        "/a%00b",
        "/a\nb",
        "/a\rb",
        "/a\tb",
        "/a%0d%0aSet-Cookie:%20x=1",
        "/a\x7fb",
        "/a\u0085b",
        "/a?q=1\r\nHost: evil.example",
        # Not a path at all.
        "/a#fragment",
        "/" + "a" * 9000,
        None,
        42,
        ["/x"],
        b"/x",
    ],
)
def test_a_path_that_could_leave_the_connector_is_refused(path):
    upstream = Upstream()

    for allow_write in (False, True):
        error = refused(upstream, path=path, allow_write=allow_write)
        assert (error.code, error.status) == ("invalid_path", 400)
    assert upstream.requests == []


def test_a_path_percent_encoded_more_times_than_is_looked_at_is_refused():
    path = "/%" + "25" * 8 + "2e%" + "25" * 8 + "2e/x"

    assert refused(path=path).code == "invalid_path"


@pytest.mark.parametrize(
    ("path", "sent"),
    [
        ("/rest/api/2/issue/ABC-1", "/rest/api/2/issue/ABC-1"),
        ("/", "/"),
        ("/rest/api/2/search?jql=project%20%3D%20ABC&maxResults=5", "/rest/api/2/search?jql=project%20%3D%20ABC&maxResults=5"),
        # A query is not a path: it is passed on as written.
        ("/rest/api/2/search?jql=a/../b", "/rest/api/2/search?jql=a/../b"),
        ("/a%20b/c", "/a%20b/c"),
        ("/display/\u6587\u6863", "/display/%E6%96%87%E6%A1%A3"),
        ("/a..b/c../..d/...", "/a..b/c../..d/..."),
        # A full-width slash in a title is a character, not a separator.
        ("/display/A\uff0fB", "/display/A%EF%BC%8FB"),
        ("/job/a%2Fb/api/json", "/job/a%2Fb/api/json"),
        # Still on the connector: "@" after the first slash is part of the path.
        ("/@evil.example/x", "/@evil.example/x"),
        ("/login?from=//evil.example", "/login?from=//evil.example"),
        ("/rest/http://evil.example/x", "/rest/http://evil.example/x"),
    ],
)
def test_a_plain_path_is_joined_to_the_connectors_address(path, sent):
    upstream = Upstream()

    send(upstream, path=path)

    url = upstream.last.url
    assert (url.scheme, url.host, url.port) == ("https", "jira.example.test", None)
    assert url.raw_path.decode("ascii") == sent


def test_the_path_stays_below_the_address_the_connector_was_given():
    config = instances("jira", {"url": "https://tools.example.test/jira", "token": TOKEN})
    upstream = Upstream()

    send(upstream, config=config, path="/rest/api/2/myself")
    assert str(upstream.last.url) == "https://tools.example.test/jira/rest/api/2/myself"

    for path in ("/../confluence/rest/api/space", "/%2e%2e/admin", "/..%2f..%2fadmin"):
        assert refused(upstream, config=config, path=path).code == "invalid_path"
    assert len(upstream.requests) == 1


def test_the_joined_address_is_checked_again_after_joining():
    # Whatever the path rules let through, a URL on another host, scheme or port, or above the connector's path, is never sent.
    target = connector_proxy._resolve(instances("jira", {"url": "https://tools.example.test/jira", "token": TOKEN}), "jira")

    assert str(connector_proxy._target_url(target, "/rest/x", "a=1")) == "https://tools.example.test/jira/rest/x?a=1"
    for raw_path in ("@evil.example/x", ".evil.example/x", ":8443/x", "/../admin", "/../jira-admin/x", "-admin/x"):
        with pytest.raises(ConnectorProxyError) as caught:
            connector_proxy._target_url(target, raw_path, "")
        assert caught.value.code == "invalid_path"

    # An address without a path of its own: the host, the port and the login are what a join could change.
    bare = connector_proxy._resolve(JIRA, "jira")
    assert str(connector_proxy._target_url(bare, "/rest/x", "")) == "https://jira.example.test/rest/x"
    for raw_path in ("@evil.example/x", ".evil.example/x", ":8443/x", ":pw@evil.example/x", "x:y@jira.example.test/x"):
        with pytest.raises(ConnectorProxyError) as caught:
            connector_proxy._target_url(bare, raw_path, "")
        assert caught.value.code == "invalid_path"


# ---------------------------------------------------------------- query, headers, body


def test_the_query_is_encoded_and_follows_one_written_into_the_path():
    upstream = Upstream()

    send(
        upstream,
        path="/rest/api/2/search?startAt=0",
        query={
            "jql": 'project = "A&B" AND text ~ "a/b?c#d"',
            "fields": ["summary", "status"],
            "validateQuery": False,
            "maxResults": 25,
            "ratio": 0.5,
            "expand": None,
            "name": "\u6587",
        },
    )

    assert upstream.last.url.raw_path.decode("ascii") == (
        "/rest/api/2/search?startAt=0"
        "&jql=project%20%3D%20%22A%26B%22%20AND%20text%20~%20%22a%2Fb%3Fc%23d%22"
        "&fields=summary&fields=status&validateQuery=false&maxResults=25&ratio=0.5&name=%E6%96%87"
    )
    assert upstream.last.url.host == "jira.example.test"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": "jql=x"},
        {"query": [("jql", "x")]},
        {"query": {"jql": {"nested": 1}}},
        {"query": {"jql": [["x"]]}},
        {"query": {"": "x"}},
        {"query": {7: "x"}},
        {"headers": "Accept: application/json"},
        {"headers": [("Accept", "application/json")]},
        {"headers": {"X-Custom": 5}},
        {"headers": {"X-Custom": "a\r\nX-Injected: 1"}},
        {"headers": {"Accept": "text/plain\x00"}},
        {"headers": {"X-Bad Name": "1"}},
        {"json_body": {"a": 1}, "body": b"{}", "allow_write": True, "method": "POST"},
        {"body": "text", "allow_write": True, "method": "POST"},
        {"json_body": {"when": object()}, "allow_write": True, "method": "POST"},
        {"json_body": {"ratio": float("nan")}, "allow_write": True, "method": "POST"},
    ],
)
def test_arguments_of_the_wrong_shape_are_invalid(kwargs):
    upstream = Upstream()

    error = refused(upstream, **kwargs)

    assert (error.code, error.status) == ("invalid_request", 400)
    assert upstream.requests == []


def test_only_accept_content_type_and_harmless_x_headers_travel():
    upstream = Upstream()

    send(
        upstream,
        headers={
            "Accept": "application/json",
            "X-Custom": " 1 ",
            "x-atlassian-token": "no-check",
            "X-GitHub-Api-Version": "2022-11-28",
            "X-Requested-With": "XMLHttpRequest",
            "X-ExperimentalApi": "opt-in",
            # Not Accept, Content-Type or X-*.
            "Authorization": "Bearer mine",
            "Cookie": "JSESSIONID=1",
            "Host": "evil.example",
            "User-Agent": "mine",
            "Proxy-Authorization": "Basic bWluZQ==",
            "Referer": "https://evil.example/",
            "Origin": "https://evil.example",
            "Connection": "close",
            "Transfer-Encoding": "chunked",
            "Content-Length": "999",
            "Jenkins-Crumb": "abc",
            "Forwarded": "for=10.0.0.1",
            # X-* that carry an identity, a credential, or another method, host or path.
            "X-Auth-Token": "mine",
            "X-Authorization": "Bearer mine",
            "X-Api-Key": "mine",
            "X-Access-Token": "mine",
            "X-GitHub-OTP": "123456",
            "X-Csrf-Token": "mine",
            "X-Forwarded-For": "10.0.0.1",
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-User": "admin",
            "X-Remote-User": "admin",
            "X-WEBAUTH-USER": "admin",
            "X-SSO-USER": "admin",
            "X-Real-IP": "10.0.0.1",
            "X-HTTP-Method-Override": "DELETE",
            "X-HTTP-Method": "DELETE",
            "X-Method-Override": "DELETE",
            "X-Original-URL": "/admin",
            "X-Rewrite-URL": "/admin",
            "X-Atlassian-WebSudo": "1",
            "X-Amz-Security-Token": "mine",
            7: "ignored",
        },
    )

    sent = dict(upstream.last.headers)
    ours = {name: value for name, value in sent.items() if name not in ("host", "accept-encoding", "connection", "user-agent")}
    assert ours == {
        "accept": "application/json",
        "x-custom": "1",
        "x-atlassian-token": "no-check",
        "x-github-api-version": "2022-11-28",
        "x-requested-with": "XMLHttpRequest",
        "x-experimentalapi": "opt-in",
        "authorization": f"Bearer {TOKEN}",
    }
    assert sent["host"] == "jira.example.test"
    assert sent["user-agent"] != "mine"


def test_a_header_the_connector_signs_with_is_never_the_callers():
    # None of the six signs with an X- header today; the rule holds for any header that does.
    headers = connector_proxy._request_headers(
        {"X-Service-Login": "mine", "x-service-login": "mine too", "AUTHORIZATION": "Bearer mine", "X-Custom": "1"},
        {"X-Service-Login": "the connector's", "Authorization": "Bearer theirs"},
    )

    assert headers == {"x-custom": "1", "X-Service-Login": "the connector's", "Authorization": "Bearer theirs"}


def test_a_json_body_is_sent_as_json():
    upstream = Upstream()

    send(upstream, method="POST", path="/rest/api/2/issue", json_body={"fields": {"summary": "\u6587"}}, allow_write=True)

    request = upstream.last
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"fields": {"summary": "\u6587"}}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_a_raw_body_is_sent_as_given_with_the_callers_content_type():
    upstream = Upstream()
    payload = bytes(range(256))

    send(
        upstream,
        method="PUT",
        path="/rest/api/2/issue/ABC-1/attachments",
        body=payload,
        headers={"Content-Type": "application/octet-stream", "X-Atlassian-Token": "no-check"},
        allow_write=True,
    )

    request = upstream.last
    assert request.content == payload
    assert request.headers["content-type"] == "application/octet-stream"
    assert request.headers["content-length"] == "256"


def test_the_callers_content_type_wins_over_the_json_default():
    upstream = Upstream()

    send(upstream, method="PATCH", json_body=[{"op": "add"}], headers={"content-type": "application/json-patch+json"}, allow_write=True)

    assert upstream.last.headers["content-type"] == "application/json-patch+json"


def test_a_read_without_a_body_sends_none():
    upstream = Upstream()

    send(upstream)

    assert upstream.last.content == b""
    assert "content-type" not in upstream.last.headers


# ---------------------------------------------------------------- response


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "Application/JSON", "application/vnd.github+json", "application/problem+json", "text/json"],
)
def test_a_json_answer_comes_back_parsed(content_type):
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": content_type}, content=b'{"key": "ABC-1", "n": [1, 2]}'))

    result = send(upstream)

    assert result == {
        "status": 200,
        "headers": {"content-type": content_type},
        "json": {"key": "ABC-1", "n": [1, 2]},
        "body_base64": None,
        "truncated": False,
    }


def test_a_json_answer_with_a_byte_order_mark_is_still_json():
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b'\xef\xbb\xbf[1, 2]'))

    assert send(upstream)["json"] == [1, 2]


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        ("text/plain; charset=utf-8", b"Started by user svc\n"),
        ("text/html", b"<html><body>Sign in</body></html>"),
        ("application/xml", b"<feed/>"),
        ("application/octet-stream", bytes(range(256))),
        # Says JSON, is not.
        ("application/json", b"{oops"),
        ("application/json", b'{"ratio": NaN}'),
        ("application/json", b"\xff\xfe\x00"),
        # Is JSON, does not say so.
        ("text/plain", b'{"key": "ABC-1"}'),
        (None, b'{"key": "ABC-1"}'),
    ],
)
def test_anything_else_comes_back_as_bytes(content_type, content):
    headers = {"content-type": content_type} if content_type else {}
    upstream = Upstream(lambda request: httpx.Response(200, headers=headers, content=content))

    result = send(upstream)

    assert result["json"] is None
    assert base64.b64decode(result["body_base64"]) == content
    assert result["truncated"] is False


@pytest.mark.parametrize("status", [204, 304])
def test_an_answer_without_a_body_has_neither(status):
    upstream = Upstream(lambda request: httpx.Response(status, headers={"etag": '"v1"'}))

    assert send(upstream) == {"status": status, "headers": {"etag": '"v1"'}, "json": None, "body_base64": None, "truncated": False}


def test_head_returns_the_headers_and_no_body():
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "application/json", "x-total": "42"}))

    result = send(upstream, method="HEAD")

    assert upstream.last.method == "HEAD"
    assert result == {"status": 200, "headers": {"content-type": "application/json", "x-total": "42"}, "json": None, "body_base64": None, "truncated": False}


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429, 500, 503])
def test_the_upstreams_own_status_is_returned_not_raised(status):
    upstream = Upstream(lambda request: httpx.Response(status, json={"errorMessages": ["no"]}, headers={"retry-after": "5"}))

    result = send(upstream)

    assert result["status"] == status
    assert result["json"] == {"errorMessages": ["no"]}
    assert result["headers"]["retry-after"] == "5"


def test_only_a_few_response_headers_come_back():
    upstream = Upstream(
        lambda request: httpx.Response(
            200,
            headers=[
                ("Content-Type", "application/json"),
                ("Link", '<https://api.github.com/repos?page=2>; rel="next"'),
                ("X-Total", "120"),
                ("X-Total-Count", "120"),
                ("X-RateLimit-Limit", "5000"),
                ("X-RateLimit-Remaining", "4999"),
                ("X-RateLimit-Reset", "1731542400"),
                ("Retry-After", "30"),
                ("ETag", 'W/"abc"'),
                ("Last-Modified", "Sat, 14 Nov 2026 00:00:00 GMT"),
                ("Content-Disposition", 'attachment; filename="build.log"'),
                ("X-Text-Size", "2048"),
                ("X-More-Data", "true"),
                # Never returned.
                ("Set-Cookie", "JSESSIONID=abc; Path=/; HttpOnly"),
                ("Set-Cookie", "atlassian.xsrf.token=xyz; Path=/"),
                ("WWW-Authenticate", 'Basic realm="jira"'),
                ("Authorization", f"Bearer {TOKEN}"),
                ("X-AUSERNAME", "svc"),
                ("X-OAuth-Scopes", "repo, admin:org"),
                ("X-Auth-Token", TOKEN),
                ("X-Seraph-LoginReason", "OK"),
                ("Server", "nginx"),
                ("Via", "1.1 proxy"),
                ("X-Custom", "1"),
            ],
            content=b"{}",
        )
    )

    assert send(upstream)["headers"] == {
        "content-type": "application/json",
        "link": '<https://api.github.com/repos?page=2>; rel="next"',
        "x-total": "120",
        "x-total-count": "120",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-remaining": "4999",
        "x-ratelimit-reset": "1731542400",
        "retry-after": "30",
        "etag": 'W/"abc"',
        "last-modified": "Sat, 14 Nov 2026 00:00:00 GMT",
        "content-disposition": 'attachment; filename="build.log"',
        "x-text-size": "2048",
        "x-more-data": "true",
    }


def test_the_cap_is_five_mebibytes():
    assert connector_proxy.MAX_BODY_BYTES == 5 * MIB


def test_a_body_at_the_cap_is_whole():
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"a" * (5 * MIB)))

    result = send(upstream)

    assert result["truncated"] is False
    assert len(base64.b64decode(result["body_base64"])) == 5 * MIB


def test_a_body_over_the_cap_is_cut_and_the_rest_is_not_read():
    # Twelve one-mebibyte pieces, then a failure the proxy must never reach.
    body = ChunkStream([bytes([65 + index]) * MIB for index in range(12)], error=AssertionError("read past the cap"))
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "application/octet-stream"}, stream=body))

    result = send(upstream)

    raw = base64.b64decode(result["body_base64"])
    assert result["truncated"] is True
    assert len(raw) == 5 * MIB
    assert raw[:1] == b"A" and raw[-1:] == b"E"
    assert body.sent == 6  # the sixth piece showed there was more


def test_a_body_one_byte_over_the_cap_is_cut():
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"a" * (5 * MIB + 1)))

    result = send(upstream)

    assert result["truncated"] is True
    assert len(base64.b64decode(result["body_base64"])) == 5 * MIB


def test_a_cut_json_body_comes_back_as_bytes(monkeypatch):
    monkeypatch.setattr(connector_proxy, "MAX_BODY_BYTES", 16)
    upstream = Upstream(lambda request: httpx.Response(200, json={"values": list(range(100))}))

    result = send(upstream)

    assert result["truncated"] is True
    assert result["json"] is None
    assert base64.b64decode(result["body_base64"]) == b'{"values": [0, 1'


# ---------------------------------------------------------------- redirects


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_returned_and_never_followed(status):
    upstream = Upstream(lambda request: httpx.Response(status, headers={"location": "https://evil.example/steal"}, text="moved"))

    # Even a client that follows redirects by default does not follow this one.
    result = asyncio.run(
        connector_proxy.request(
            config=JIRA,
            connector_type="jira",
            method="GET",
            path="/secure/attachment/1/file",
            client=upstream.client(follow_redirects=True),
        )
    )

    assert result["status"] == status
    assert len(upstream.requests) == 1
    assert [request.url.host for request in upstream.requests] == ["jira.example.test"]
    # Where it points is another host: not passed on.
    assert "location" not in result["headers"]


@pytest.mark.parametrize(
    ("location", "told"),
    [
        ("/login.jsp?os_destination=%2Fsecure", True),
        ("https://jira.example.test/secure/Dashboard.jspa", True),
        ("secure/Dashboard.jspa", True),
        ("https://jira.example.test:8443/secure", False),
        ("http://jira.example.test/secure", False),
        ("//evil.example/x", False),
        ("https://codeload.example.test/acme/app/zip?token=AAAA", False),
        ("https://jira.example.test@evil.example/x", False),
        (f"/download?token={TOKEN}", False),
    ],
)
def test_a_redirect_target_is_told_only_when_it_stays_on_the_connector(location, told):
    upstream = Upstream(lambda request: httpx.Response(302, headers={"location": location}))

    result = send(upstream)

    assert result["status"] == 302
    assert result["headers"].get("location") == (location if told else None)
    assert len(upstream.requests) == 1


# ---------------------------------------------------------------- failures


@pytest.mark.parametrize(
    "raised",
    [httpx.ConnectTimeout("timed out"), httpx.ReadTimeout("timed out"), httpx.WriteTimeout("timed out"), httpx.PoolTimeout("timed out")],
)
def test_a_timeout_is_an_upstream_timeout(raised):
    def answer(request):
        raise raised

    error = refused(Upstream(answer))

    assert (error.code, error.status) == ("upstream_timeout", 504)


def test_the_whole_request_has_sixty_seconds_and_ten_to_connect():
    seen = {}

    def answer(request):
        seen.update(request.extensions["timeout"])
        return httpx.Response(200, json={})

    send(Upstream(answer))

    assert connector_proxy.TOTAL_TIMEOUT_SECONDS == 60.0
    assert seen["connect"] == 10.0
    assert seen["read"] == 60.0


def test_an_answer_that_takes_longer_than_the_total_is_an_upstream_timeout(monkeypatch):
    monkeypatch.setattr(connector_proxy, "TOTAL_TIMEOUT_SECONDS", 0.05)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    error = refused(Upstream(slow))

    assert (error.code, error.status) == ("upstream_timeout", 504)


def test_a_body_that_trickles_past_the_total_is_an_upstream_timeout(monkeypatch):
    # Each piece arrives well inside any read timeout; only the total catches it.
    monkeypatch.setattr(connector_proxy, "TOTAL_TIMEOUT_SECONDS", 0.2)
    body = ChunkStream([b"x"] * 200, pause=0.02)
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, stream=body))

    error = refused(upstream)

    assert (error.code, error.status) == ("upstream_timeout", 504)
    assert body.sent < 200


@pytest.mark.parametrize(
    "raised",
    [
        httpx.ConnectError("[Errno 111] Connection refused"),
        httpx.ReadError("connection reset by peer"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ProxyError("407 Proxy Authentication Required"),
        OSError("network unreachable"),
    ],
)
def test_a_connection_failure_is_an_upstream_error(raised):
    def answer(request):
        raise raised

    error = refused(Upstream(answer))

    assert (error.code, error.status) == ("upstream_error", 502)


def test_a_connection_lost_while_reading_the_body_is_an_upstream_error():
    body = ChunkStream([b"half"], error=httpx.ReadError("connection reset by peer"))
    upstream = Upstream(lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, stream=body))

    error = refused(upstream)

    assert (error.code, error.status) == ("upstream_error", 502)


# ---------------------------------------------------------------- credentials stay inside


def test_errors_never_carry_a_credential():
    config = {
        **JIRA,
        "proxy": {"enabled": True, "proxies": [{"name": "corp", "url": "http://proxy.example.test:3128", "username": "pu", "password": "pp-secret-1"}]},
    }

    def answer(request):
        raise httpx.ProxyError(
            f"407 from http://pu:pp-secret-1@proxy.example.test:3128 for {request.headers['authorization']} (token {TOKEN})"
        )

    error = refused(Upstream(answer), config=config)

    for secret in (TOKEN, basic("svc", TOKEN), basic("svc", TOKEN).split(" ", 1)[1], "pp-secret-1"):
        assert secret not in error.message
        assert secret not in str(error)
        assert secret not in repr(error)
    # The failed request, Authorization header and all, is not attached to the error.
    assert error.__cause__ is None and error.__context__ is None


CLOUD_JIRA = instances("jira", {"name": "cloud", "url": "https://acme.atlassian.net", "username": "svc@example.test", "token": TOKEN})


@pytest.mark.parametrize(
    ("kind", "config", "secrets"),
    [
        # Every form the proxy sent: the token itself, and the Basic value it was packed into.
        ("jira", CONFIGS["jira"], [TOKEN]),
        ("jira", CLOUD_JIRA, [TOKEN, basic("svc@example.test", TOKEN).split(" ", 1)[1]]),
        ("nexus", CONFIGS["nexus"], [PASSWORD, basic("svc", PASSWORD).split(" ", 1)[1]]),
        ("github", CONFIGS["github"], [TOKEN]),
    ],
)
def test_a_credential_the_upstream_echoes_is_not_returned(kind, config, secrets):
    def echo(request):
        return httpx.Response(
            200,
            json={"headers": dict(request.headers), "note": " and ".join(secrets)},
            headers={"link": f'<{request.url}?access_token={secrets[0]}>; rel="next"', "x-total": "1"},
        )

    result = send(Upstream(echo), config=config, connector_type=kind, path="/debug/echo")

    returned = json.dumps(result)
    for secret in secrets:
        assert secret not in returned
    assert result["json"]["headers"]["authorization"].endswith("[redacted]")
    assert "link" not in result["headers"]
    assert result["headers"]["x-total"] == "1"

    def echo_as_text(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="you sent " + request.headers["authorization"])

    result = send(Upstream(echo_as_text), config=config, connector_type=kind, path="/debug/echo")
    text = base64.b64decode(result["body_base64"]).decode("utf-8")
    assert all(secret not in text for secret in secrets)
    assert "[redacted]" in text


def test_the_result_carries_no_credential_and_the_target_does_not_print_one():
    result = send(Upstream(lambda request: httpx.Response(200, json={"name": "svc"})))

    assert TOKEN not in json.dumps(result)
    target = connector_proxy._resolve(JIRA, "jira")
    assert TOKEN not in repr(target) and "Basic" not in repr(target)


def test_the_request_line_is_kept_out_of_the_log(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    upstream = Upstream()

    send(upstream, path="/rest/api/2/search", query={"jql": "text ~ confidential", "token": "abc"})

    assert upstream.requests
    assert [record for record in caplog.records if record.name == "httpx"] == []

    # The same client outside the proxy is logged as usual: the silence is the proxy's doing.
    async def plain():
        async with upstream.client() as client:
            await client.get("https://jira.example.test/plain")

    asyncio.run(plain())
    assert any("jira.example.test/plain" in record.getMessage() for record in caplog.records if record.name == "httpx")


# ---------------------------------------------------------------- the way out


class _ClientFactory:
    """Stands in for httpx.AsyncClient: remembers how each client was built, answers from a mock."""

    def __init__(self, upstream: Upstream):
        self.built: list[dict] = []
        self.clients: list[httpx.AsyncClient] = []
        self._upstream = upstream
        self._real = httpx.AsyncClient

    def __call__(self, **kwargs):
        self.built.append(kwargs)
        client = self._real(transport=httpx.MockTransport(self._upstream))
        self.clients.append(client)
        return client


PROXIES = {
    "enabled": True,
    "default": "corp-a",
    "proxies": [
        {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "pu", "password": "pp-secret-1", "no_proxy": "nexus.example.test"},
        {"name": "corp-b", "url": "http://proxy-b.example.test:3128"},
    ],
    "assignments": {"jira": "corp-b", "splunk": "none"},
}


def _built_for(monkeypatch, kind: str, config: dict) -> dict:
    upstream = Upstream()
    factory = _ClientFactory(upstream)
    monkeypatch.setattr(_outbound.httpx, "AsyncClient", factory)
    asyncio.run(connector_proxy.request(config=config, connector_type=kind, method="GET", path="/probe"))
    assert len(upstream.requests) == 1
    assert len(factory.built) == 1
    assert all(client.is_closed for client in factory.clients)
    return factory.built[0]


def test_without_a_proxy_connector_the_request_leaves_like_the_test_button(monkeypatch):
    assert _built_for(monkeypatch, "jira", JIRA) == {"follow_redirects": False, "verify": trust_context()}


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        # Assigned a named proxy.
        ("jira", {"proxy": "http://proxy-b.example.test:3128", "trust_env": False}),
        # Assigned none: direct, the Portal's own environment left aside.
        ("splunk", {"trust_env": False}),
        # No assignment: the default proxy, with its login.
        ("jenkins", {"proxy": "http://pu:pp-secret-1@proxy-a.example.test:3128", "trust_env": False}),
        # GitHub's tools only read the environment, which is the default proxy.
        ("github", {"proxy": "http://pu:pp-secret-1@proxy-a.example.test:3128", "trust_env": False}),
        # The default proxy's NO_PROXY exempts this host.
        ("nexus", {"trust_env": False}),
    ],
)
def test_each_connector_leaves_through_the_proxy_the_proxy_connector_assigns_it(monkeypatch, kind, expected):
    built = _built_for(monkeypatch, kind, {**CONFIGS[kind], "proxy": PROXIES})

    assert built == {"follow_redirects": False, "verify": trust_context(), **expected}


def test_the_client_is_closed_after_a_failure_too(monkeypatch):
    def answer(request):
        raise httpx.ConnectError("refused")

    factory = _ClientFactory(Upstream(answer))
    monkeypatch.setattr(_outbound.httpx, "AsyncClient", factory)

    with pytest.raises(ConnectorProxyError):
        asyncio.run(connector_proxy.request(config=JIRA, connector_type="jira", method="GET", path="/probe"))

    assert factory.clients and all(client.is_closed for client in factory.clients)


def test_an_injected_client_is_left_open():
    upstream = Upstream()

    async def twice():
        client = upstream.client()
        for _ in range(2):
            await connector_proxy.request(config=JIRA, connector_type="jira", method="GET", path="/probe", client=client)
        closed = client.is_closed
        await client.aclose()
        return closed

    assert asyncio.run(twice()) is False
    assert len(upstream.requests) == 2
