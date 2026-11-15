"""The session cookie over https is ``__Host-portal_session`` with ``Secure``, read through one helper.

A ``__Host-`` cookie is accepted by browsers only from the host itself, over
https, with ``Path=/`` and no ``Domain``: a page on a sibling subdomain can
neither read nor replace it.
"""

from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.security import session_cookie
from app.security.session_cookie import (
    cookies_are_hardened,
    host_bound_cookie_name,
    read_session_token,
    session_cookie_name,
)

REPO = Path(__file__).resolve().parents[1]


def _settings(base_uri="", name="portal_session"):
    return SimpleNamespace(base_uri=base_uri, session_cookie_name=name)


def _set_cookie_headers(response) -> list[str]:
    """Every Set-Cookie of a Starlette response or of a test client's answer, one per cookie."""
    if hasattr(response, "raw_headers"):
        return [value.decode("latin-1") for key, value in response.raw_headers if key == b"set-cookie"]
    return list(response.headers.get_list("set-cookie"))


@pytest.fixture
def https_portal(monkeypatch):
    # The object every module captured at import (app.web.settings is the same one).
    settings = session_cookie.settings
    monkeypatch.setattr(settings, "base_uri", "https://efp.example.com")
    monkeypatch.setattr(settings, "session_cookie_name", "portal_session")
    return settings


@pytest.fixture
def http_portal(monkeypatch):
    settings = session_cookie.settings
    monkeypatch.setattr(settings, "base_uri", "http://localhost:8010")
    monkeypatch.setattr(settings, "session_cookie_name", "portal_session")
    return settings


# --- the name -------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_uri, configured, expected_name, hardened",
    [
        ("https://efp.example.com", "portal_session", "__Host-portal_session", True),
        ("HTTPS://efp.example.com/", "portal_session", "__Host-portal_session", True),
        ("  https://efp.example.com", "portal_session", "__Host-portal_session", True),
        ("http://efp.example.com", "portal_session", "portal_session", False),
        ("", "portal_session", "portal_session", False),
        # A custom name is the operator's own choice: kept, with today's flags.
        ("https://efp.example.com", "team_session", "team_session", False),
        ("http://efp.example.com", "team_session", "team_session", False),
        # An empty setting falls back to the default name.
        ("https://efp.example.com", "", "__Host-portal_session", True),
        ("", "", "portal_session", False),
    ],
)
def test_effective_cookie_name(base_uri, configured, expected_name, hardened):
    settings = _settings(base_uri, configured)
    assert session_cookie_name(settings) == expected_name
    assert cookies_are_hardened(settings) is hardened
    assert host_bound_cookie_name("portal_sso_state", settings) == (
        "__Host-portal_sso_state" if hardened else "portal_sso_state"
    )


def test_the_old_name_is_not_read_as_a_fallback_over_https():
    settings = _settings("https://efp.example.com")
    assert read_session_token({"portal_session": "old"}, settings) is None
    assert read_session_token({"portal_session": "old", "__Host-portal_session": "new"}, settings) == "new"
    assert read_session_token({}, settings) is None
    assert read_session_token(None, settings) is None


def test_the_prefixed_name_is_not_read_without_https():
    settings = _settings("http://efp.example.com")
    assert read_session_token({"__Host-portal_session": "x"}, settings) is None
    assert read_session_token({"portal_session": "plain"}, settings) == "plain"


# --- what the Portal sets -------------------------------------------------------


def test_set_session_cookie_over_https_is_host_bound_and_secure(https_portal):
    from app.services.auth_service import SESSION_COOKIE_MAX_AGE_SECONDS, set_session_cookie

    response = Response()
    set_session_cookie(response, "signed-token")
    cookies = _set_cookie_headers(response)
    session = next(value for value in cookies if value.startswith("__Host-portal_session="))
    attributes = {part.strip().lower() for part in session.split(";")[1:]}
    assert session.startswith("__Host-portal_session=signed-token;")
    assert "secure" in attributes
    assert "httponly" in attributes
    assert "path=/" in attributes
    assert "samesite=lax" in attributes
    assert f"max-age={SESSION_COOKIE_MAX_AGE_SECONDS}" in attributes
    # A __Host- cookie must not name a Domain, or browsers drop it.
    assert not any(attribute.startswith("domain") for attribute in attributes)
    # The cookie from before the upgrade is expired alongside: nothing reads it.
    legacy = next(value for value in cookies if value.startswith("portal_session="))
    assert legacy.startswith('portal_session="";') and "Max-Age=0" in legacy


def test_set_session_cookie_without_https_is_what_it_was(http_portal):
    from app.services.auth_service import SESSION_COOKIE_MAX_AGE_SECONDS, set_session_cookie

    response = Response()
    set_session_cookie(response, "signed-token")
    cookies = _set_cookie_headers(response)
    assert cookies == [
        f"portal_session=signed-token; HttpOnly; Max-Age={SESSION_COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=lax"
    ]


def test_clear_session_cookie_carries_the_flags_the_cookie_was_set_with(https_portal):
    from app.services.auth_service import clear_session_cookie

    response = Response()
    clear_session_cookie(response)
    (cookie,) = _set_cookie_headers(response)
    # Browsers ignore a Set-Cookie for a __Host- name without Secure and Path=/,
    # deletions included.
    assert cookie.startswith('__Host-portal_session="";')
    assert "Max-Age=0" in cookie and "Path=/" in cookie and "Secure" in cookie


def test_clear_session_cookie_without_https(http_portal):
    from app.services.auth_service import clear_session_cookie

    response = Response()
    clear_session_cookie(response)
    (cookie,) = _set_cookie_headers(response)
    assert cookie.startswith('portal_session="";')
    assert "Secure" not in cookie


# --- through the real routes ------------------------------------------------------


def _login_client(monkeypatch):
    from unittest.mock import MagicMock

    from app.main import app
    import app.api.auth as auth_api

    user = MagicMock()
    user.id = 41
    user.username = "member"
    user.password_hash = "hashed"
    repo = MagicMock()
    repo.get_by_username_case_insensitive.return_value = user
    monkeypatch.setattr(auth_api, "UserRepository", lambda _db: repo)
    monkeypatch.setattr(auth_api, "verify_password", lambda _raw, _hashed: True)
    monkeypatch.setattr(
        auth_api, "UserAllowlistRepository", lambda _db: SimpleNamespace(get_active_by_username=lambda _name: object())
    )
    app.dependency_overrides[auth_api.get_db] = lambda: iter([object()])
    return app, user


def test_login_and_logout_use_the_hardened_cookie_over_https(monkeypatch, https_portal):
    app, _user = _login_client(monkeypatch)
    client = TestClient(app, base_url="https://efp.example.com")
    try:
        login = client.post("/api/auth/login", json={"username": "member", "password": "pw"})
        assert login.status_code == 200
        assert login.cookies.get("__Host-portal_session")
        assert "Secure" in login.headers.get("set-cookie", "")

        logout = client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "same-origin"})
        assert logout.status_code == 200
        cleared = logout.headers.get("set-cookie", "")
        assert '__Host-portal_session=""' in cleared and "Secure" in cleared
    finally:
        app.dependency_overrides.clear()


def test_api_routes_do_not_accept_the_old_cookie_over_https(monkeypatch, https_portal):
    from app.main import app
    import app.deps as deps
    from app.services.auth_service import issue_session_token

    user = SimpleNamespace(id=7, username="member", nickname="Member", role="user", preferences_json=None, onboarding_completed_at=None)
    monkeypatch.setattr(deps, "UserRepository", lambda _db: SimpleNamespace(get_by_id=lambda _id: user))
    monkeypatch.setattr(
        deps, "UserAllowlistRepository", lambda _db: SimpleNamespace(get_active_by_username=lambda _name: object())
    )
    app.dependency_overrides[deps.get_db] = lambda: iter([object()])
    token = issue_session_token(7)
    try:
        old = TestClient(app, base_url="https://efp.example.com")
        old.cookies.set("portal_session", token)
        assert old.get("/api/auth/me").status_code == 401

        new = TestClient(app, base_url="https://efp.example.com")
        new.cookies.set("__Host-portal_session", token)
        me = new.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["username"] == "member"
    finally:
        app.dependency_overrides.clear()


def test_web_pages_do_not_accept_the_old_cookie_over_https(monkeypatch, https_portal):
    from app.main import app
    import app.web as web_module
    from app.services.auth_service import issue_session_token

    seen = []

    def fake_parse(token):
        seen.append(token)
        return None

    monkeypatch.setattr(web_module, "parse_session_token", fake_parse)
    client = TestClient(app, base_url="https://efp.example.com")
    client.cookies.set("portal_session", issue_session_token(7))
    response = client.get("/app", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/login"
    # The old cookie was never even parsed.
    assert seen == []

    client.cookies.set("__Host-portal_session", "candidate")
    client.get("/app", follow_redirects=False)
    assert seen == ["candidate"]


def test_the_post_login_next_cookie_is_secure_over_https(monkeypatch, https_portal):
    from app.main import app
    import app.web as web_module

    monkeypatch.setattr(web_module, "sso_enabled", lambda: True)
    monkeypatch.setattr(web_module, "sso_authorize_url", lambda state="": "https://idp.example.com/authorize")
    monkeypatch.setattr(web_module, "_session_user_access", lambda _request: (None, "missing_session"))
    client = TestClient(app, base_url="https://efp.example.com")
    response = client.get("/login/sso?next=/share/abcdefghijklmnopqrstuvwxyz012345", follow_redirects=False)
    cookies = _set_cookie_headers(response)
    next_cookie = next(value for value in cookies if value.startswith(f"{web_module.POST_LOGIN_NEXT_COOKIE}="))
    assert "Secure" in next_cookie and "HttpOnly" in next_cookie


def test_the_post_login_next_cookie_is_not_secure_without_https(monkeypatch, http_portal):
    from app.main import app
    import app.web as web_module

    monkeypatch.setattr(web_module, "sso_enabled", lambda: True)
    monkeypatch.setattr(web_module, "sso_authorize_url", lambda state="": "https://idp.example.com/authorize")
    monkeypatch.setattr(web_module, "_session_user_access", lambda _request: (None, "missing_session"))
    client = TestClient(app)
    response = client.get("/login/sso?next=/share/abcdefghijklmnopqrstuvwxyz012345", follow_redirects=False)
    cookies = _set_cookie_headers(response)
    next_cookie = next(value for value in cookies if value.startswith(f"{web_module.POST_LOGIN_NEXT_COOKIE}="))
    assert "Secure" not in next_cookie


# --- nothing reads the setting on its own ------------------------------------------


def test_only_the_helper_reads_the_cookie_name_setting():
    """A reader that used ``settings.session_cookie_name`` directly would look for the wrong cookie over https."""
    helper = Path(session_cookie.__file__).resolve()
    # Attribute access (settings.session_cookie_name) or a getattr by name; a
    # call to the helper, session_cookie_name(), has no dot in front of it.
    direct_read = re.compile(r"\.session_cookie_name\b|[\"']session_cookie_name[\"']")
    offenders = []
    for path in sorted((REPO / "app").rglob("*")):
        if path.suffix not in {".py", ".html", ".js"} or "lib" in path.parts:
            continue
        if path.resolve() == helper:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if direct_read.search(line):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{number}: {line.strip()}")
    assert offenders == []


def test_tests_set_the_cookie_under_its_effective_name():
    """A test that used the configured name would stop signing in the day it ran against an https BASE_URI."""
    attribute_read = re.compile(r"settings\.session_cookie_name\b")
    # These two only mention the attribute in assertions about the source.
    mentions_only = {"test_session_cookie_hardening.py", "test_proxy_websocket.py"}
    offenders = []
    for path in sorted((REPO / "tests").glob("*.py")):
        if path.name in mentions_only:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if attribute_read.search(line):
                offenders.append(f"tests/{path.name}:{number}: {line.strip()}")
    assert offenders == []


def test_the_literal_cookie_name_lives_in_the_helper_only():
    hits = []
    for path in sorted((REPO / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if '"portal_session"' in text or "'portal_session'" in text:
            hits.append(path.relative_to(REPO).as_posix())
    assert hits == ["app/config.py", "app/security/session_cookie.py"]
