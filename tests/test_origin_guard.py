"""A page on another origin cannot change anything with a member's session cookie.

Member apps are served from a sibling subdomain of the Portal, which browsers
treat as the same site: the SameSite=Lax session cookie rides along on the
requests those pages make. The guard tells the Portal's own pages from the
rest by what the browser states (Sec-Fetch-Site, else Origin).
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.origin_guard import BLOCKED_DETAIL, OriginGuardMiddleware
from app.security.request_origin import browser_request_is_same_origin, loggable_header, origin_is_portal

PORTAL = "https://efp.example.com"
SIBLING = "https://app.example.com"


def _settings(**overrides):
    values = {
        "origin_guard_enabled": True,
        "base_uri": PORTAL,
        "session_cookie_name": "portal_session",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(settings=None, cookie=True):
    """A tiny app behind the guard: every method on every path answers 200 and counts as a state change."""
    settings = settings or _settings()
    app = FastAPI()
    calls = []

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(path: str):
        calls.append(path)
        return {"ok": True}

    app.add_middleware(OriginGuardMiddleware, settings_source=lambda: settings)
    client = TestClient(app, base_url=PORTAL)
    if cookie:
        # https + default name: the hardened cookie is the one that counts.
        client.cookies.set("__Host-portal_session", "signed-session-token")
    return client, calls


# --- the rule itself ----------------------------------------------------------


@pytest.mark.parametrize(
    "origin, host, base_uri, expected",
    [
        ("https://efp.example.com", "efp.example.com", "", True),
        ("https://efp.example.com:443", "efp.example.com", "", True),
        ("https://efp.example.com", "efp.example.com:443", "", True),
        ("https://EFP.Example.com", "efp.example.com", "", True),
        ("http://localhost:8010", "localhost:8010", "", True),
        ("http://[::1]:8010", "[::1]:8010", "", True),
        # Behind an ingress that rewrites Host, BASE_URI still names the Portal.
        ("https://efp.example.com", "efp-portal-service.default.svc", "https://efp.example.com", True),
        ("https://efp.example.com", "10.0.0.7:8000", "https://efp.example.com/", True),
        # A sibling subdomain, a parent domain, a look-alike and another port are other origins.
        ("https://app.example.com", "efp.example.com", "https://efp.example.com", False),
        ("https://example.com", "efp.example.com", "https://efp.example.com", False),
        ("https://efp.example.com.evil.test", "efp.example.com", "https://efp.example.com", False),
        ("https://efp.example.com:8443", "efp.example.com", "https://efp.example.com", False),
        ("http://localhost:3000", "localhost:8010", "", False),
        # Not web origins at all.
        ("null", "efp.example.com", "https://efp.example.com", False),
        ("", "efp.example.com", "https://efp.example.com", False),
        ("efp.example.com", "efp.example.com", "", False),
        ("https://efp.example.com/path", "efp.example.com", "", False),
        ("https://user@efp.example.com", "efp.example.com", "", False),
        ("file://", "efp.example.com", "", False),
        ("https://efp.example.com", "", "", False),
    ],
)
def test_origin_is_portal(origin, host, base_uri, expected):
    assert origin_is_portal(origin, host, base_uri) is expected


@pytest.mark.parametrize(
    "sec_fetch_site, origin, expected",
    [
        ("same-origin", None, True),
        ("none", None, True),
        ("same-site", None, False),
        ("cross-site", None, False),
        ("Same-Site", None, False),
        # Sec-Fetch-Site decides when present, whatever Origin says.
        ("same-site", PORTAL, False),
        ("same-origin", SIBLING, True),
        ("something-new", PORTAL, False),
        # Older browser: Origin decides.
        (None, PORTAL, True),
        (None, SIBLING, False),
        (None, "null", False),
        # Neither header: not a browser page.
        (None, None, True),
        ("", "", True),
    ],
)
def test_browser_request_is_same_origin(sec_fetch_site, origin, expected):
    assert (
        browser_request_is_same_origin(
            sec_fetch_site=sec_fetch_site, origin=origin, host_header="efp.example.com", base_uri=PORTAL
        )
        is expected
    )


# --- the middleware -----------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("fetch_site", ["same-site", "cross-site"])
def test_a_cross_site_state_change_with_the_session_cookie_is_refused(method, fetch_site):
    client, calls = _client()
    response = client.request(method, "/api/agents/a1/start", headers={"Sec-Fetch-Site": fetch_site, "Origin": SIBLING})
    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-site request blocked"}
    assert BLOCKED_DETAIL == "Cross-site request blocked"
    assert calls == []


def test_a_form_post_from_a_sibling_subdomain_is_refused_without_sec_fetch_site():
    client, calls = _client()
    response = client.post(
        "/app/connectors/jira/save",
        data={"base_url": "https://evil.example.test"},
        headers={"Origin": SIBLING},
    )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin", "Origin": PORTAL},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": PORTAL},
        {},
    ],
)
def test_the_portals_own_pages_and_non_browser_clients_pass(headers):
    client, calls = _client()
    response = client.post("/api/agents/a1/start", headers=headers)
    assert response.status_code == 200
    assert calls == ["api/agents/a1/start"]


def test_reads_are_never_refused():
    client, calls = _client()
    for method in ("GET", "HEAD", "OPTIONS"):
        response = client.request(method, "/app", headers={"Sec-Fetch-Site": "cross-site", "Origin": SIBLING})
        assert response.status_code != 403, method


def test_a_request_without_the_session_cookie_is_left_alone():
    client, calls = _client(cookie=False)
    response = client.post("/api/auth/login", headers={"Sec-Fetch-Site": "cross-site", "Origin": SIBLING})
    assert response.status_code == 200
    # Some other cookie is not a session either.
    client.cookies.set("theme", "dark")
    assert client.post("/api/auth/login", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_the_old_cookie_name_is_not_a_session_over_https():
    client, calls = _client(cookie=False)
    client.cookies.set("portal_session", "left-over-from-before-the-upgrade")
    assert client.post("/api/agents/a1/start", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_the_plain_cookie_name_is_the_session_without_https():
    settings = _settings(base_uri="http://portal.local:8010")
    client, calls = _client(settings=settings, cookie=False)
    client.cookies.set("portal_session", "signed-session-token")
    refused = client.post("/api/agents/a1/start", headers={"Sec-Fetch-Site": "same-site"})
    assert refused.status_code == 403


@pytest.mark.parametrize(
    "path",
    ["/api/app-sdk/v1/llm/complete", "/api/app-host/v1/exchange", "/api/internal/agents/a1/sessions/s1/metadata"],
)
def test_token_authenticated_prefixes_are_exempt(path):
    client, calls = _client()
    response = client.post(path, headers={"Sec-Fetch-Site": "cross-site", "Origin": SIBLING})
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/internalx/agents", "/api/app-sdkx/v1", "/api/app-hostile", "/a/a1/api/internal/x"])
def test_only_the_exact_prefixes_are_exempt(path):
    client, calls = _client()
    assert client.post(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_the_setting_switches_the_guard_off():
    client, calls = _client(settings=_settings(origin_guard_enabled=False))
    response = client.post("/api/agents/a1/start", headers={"Sec-Fetch-Site": "cross-site", "Origin": SIBLING})
    assert response.status_code == 200


def test_origin_is_compared_with_the_request_host_when_base_uri_is_unset():
    settings = _settings(base_uri="")
    app = FastAPI()

    @app.post("/x")
    async def x():
        return {"ok": True}

    app.add_middleware(OriginGuardMiddleware, settings_source=lambda: settings)
    client = TestClient(app, base_url="http://portal.local:8010")
    client.cookies.set("portal_session", "signed-session-token")
    assert client.post("/x", headers={"Origin": "http://portal.local:8010"}).status_code == 200
    assert client.post("/x", headers={"Origin": "http://apps.portal.local:8010"}).status_code == 403


def test_sec_fetch_site_decides_behind_a_proxy_that_replaces_host():
    """Over https the browser says where the page is: no Host and no BASE_URI is compared at all."""
    settings = _settings(base_uri="")
    client, calls = _client(settings=settings, cookie=False)
    client.cookies.set("portal_session", "signed-session-token")
    proxied = {"Host": "efp-portal-service.default.svc.cluster.local", "Origin": PORTAL}
    assert client.post("/api/agents/a1/start", headers={**proxied, "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/api/agents/a1/start", headers={**proxied, "Sec-Fetch-Site": "same-site"}).status_code == 403
    # Without the header the Origin has only the proxy's Host to be compared with.
    assert client.post("/api/agents/a1/start", headers=proxied).status_code == 403


def test_a_refusal_logs_the_origin_and_the_host_it_was_compared_with(caplog):
    import logging

    client, calls = _client(settings=_settings(base_uri=""), cookie=False)
    client.cookies.set("portal_session", "signed-session-token")
    with caplog.at_level(logging.WARNING, logger="app.security.origin_guard"):
        response = client.post("/api/agents/a1/start", headers={"Host": "internal.name:8000", "Origin": PORTAL})
    assert response.status_code == 403
    (message,) = [record.getMessage() for record in caplog.records]
    assert "origin=https://efp.example.com" in message
    assert "host=internal.name:8000" in message
    assert "sec_fetch_site=-" in message


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "-"),
        ("", "-"),
        ("https://efp.example.com", "https://efp.example.com"),
        ("https://a.test status=200 forged=1", "https://a.test_status=200_forged=1"),
        ("line\r\nbreak\ttab", "line__break_tab"),
        ("x" * 500, "x" * 200),
    ],
)
def test_loggable_header_keeps_a_callers_text_on_one_field(value, expected):
    assert loggable_header(value) == expected


# --- wired into the real app --------------------------------------------------


def test_the_portal_app_refuses_a_cross_site_post_and_serves_its_own(monkeypatch):
    from app.main import app
    from app.security.session_cookie import session_cookie_name

    client = TestClient(app)
    client.cookies.set(session_cookie_name(), "any-session-value")

    refused = client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "same-site", "Origin": "http://app.testserver"})
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Cross-site request blocked"}
    # Refused inside the request-log middleware, so it carries a trace id like any response.
    assert refused.headers.get("x-trace-id")

    own = client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"})
    assert own.status_code == 200
    assert own.json() == {"ok": True}


def test_the_portal_app_refuses_a_cross_site_form_post_to_connector_save():
    from app.main import app
    from app.security.session_cookie import session_cookie_name

    client = TestClient(app)
    client.cookies.set(session_cookie_name(), "any-session-value")
    response = client.post(
        "/app/connectors/jira/save",
        data={"base_url": "https://evil.example.test"},
        headers={"Origin": "http://app.testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_the_portal_app_refuses_a_cross_site_bodiless_assistant_post():
    from app.main import app
    from app.security.session_cookie import session_cookie_name

    client = TestClient(app)
    client.cookies.set(session_cookie_name(), "any-session-value")
    for action in ("start", "stop", "restart", "share", "unshare"):
        response = client.post(f"/api/agents/agent-1/{action}", headers={"Sec-Fetch-Site": "same-site"})
        assert response.status_code == 403, action


def test_the_guards_sit_inside_the_request_log_middleware():
    from app.main import app
    from app.security.internal_api import ClusterOnlyApiMiddleware

    order = [middleware.cls.__name__ for middleware in app.user_middleware]
    # user_middleware lists the outermost first; the two @app.middleware("http")
    # functions (locale, request log) wrap the guards.
    assert order.index("OriginGuardMiddleware") > order.index("BaseHTTPMiddleware")
    assert order.index(ClusterOnlyApiMiddleware.__name__) > order.index("BaseHTTPMiddleware")
    assert order.count("OriginGuardMiddleware") == 1
