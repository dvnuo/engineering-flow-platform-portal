"""A shared session link sends a visitor to sign in and back to that session afterwards."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


SAFE_NEXT = "/share/abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def harness(monkeypatch):
    from app.main import app
    import app.web as web_module

    monkeypatch.setattr(web_module, "sso_enabled", lambda: True)
    monkeypatch.setattr(web_module, "sso_authorize_url", lambda: "https://idp.example.com/authorize")
    monkeypatch.setattr(web_module, "sso_redirect_uri", lambda: "https://portal.example.com/auth")
    monkeypatch.setattr(web_module, "_session_user_access", lambda _request: (None, "missing_session"))

    async def fake_login(db, *, redirect_uri, code):
        return "session-token"

    monkeypatch.setattr(web_module, "login_user_by_code", fake_login)
    monkeypatch.setattr(web_module, "set_session_cookie", lambda response, token: None)
    return SimpleNamespace(client=TestClient(app), web=web_module)


def test_login_page_carries_a_safe_next_into_the_sso_link(harness):
    response = harness.client.get(f"/login?next={SAFE_NEXT}")
    assert response.status_code == 200
    assert f'href="/login/sso?next={SAFE_NEXT}"' in response.text


@pytest.mark.parametrize("unsafe", ["https://evil.example.com/share/x", "/app", "//evil/share/x", "/share/../x", "/share/short"])
def test_login_page_ignores_an_unsafe_next(harness, unsafe):
    response = harness.client.get("/login", params={"next": unsafe})
    assert response.status_code == 200
    assert "/login/sso?next=" not in response.text


def test_sso_start_remembers_next_in_a_cookie(harness):
    response = harness.client.get(f"/login/sso?next={SAFE_NEXT}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://idp.example.com/authorize"
    cookie = response.headers.get("set-cookie", "")
    # Starlette quotes a value with a slash in it; the browser sends it back unquoted.
    assert f'{harness.web.POST_LOGIN_NEXT_COOKIE}="{SAFE_NEXT}"' in cookie
    assert "HttpOnly" in cookie

    harness.client.cookies.clear()
    plain = harness.client.get("/login/sso", follow_redirects=False)
    assert harness.web.POST_LOGIN_NEXT_COOKIE not in plain.headers.get("set-cookie", "")


def test_sso_start_without_next_clears_a_stale_cookie(harness):
    # A share link from a sign-in that never completed must not send this
    # plain sign-in to that session.
    harness.client.cookies.set(harness.web.POST_LOGIN_NEXT_COOKIE, SAFE_NEXT)
    response = harness.client.get("/login/sso", follow_redirects=False)
    assert response.status_code == 302
    assert f'{harness.web.POST_LOGIN_NEXT_COOKIE}=""' in response.headers.get("set-cookie", "")


def test_sso_callback_returns_to_the_shared_session(harness):
    harness.client.cookies.set(harness.web.POST_LOGIN_NEXT_COOKIE, SAFE_NEXT)
    response = harness.client.get("/auth?code=abc", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == SAFE_NEXT
    assert f'{harness.web.POST_LOGIN_NEXT_COOKIE}=""' in response.headers.get("set-cookie", "")


def test_sso_callback_ignores_a_tampered_cookie(harness):
    harness.client.cookies.set(harness.web.POST_LOGIN_NEXT_COOKIE, "https://evil.example.com/")
    response = harness.client.get("/auth?code=abc", follow_redirects=False)
    assert response.headers["location"] == "/app"


def test_signed_in_visitor_goes_straight_to_the_shared_session(harness, monkeypatch):
    user = SimpleNamespace(id=1, username="owner", role="user")
    monkeypatch.setattr(harness.web, "_session_user_access", lambda _request: (user, None))
    response = harness.client.get(f"/login?next={SAFE_NEXT}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == SAFE_NEXT
    plain = harness.client.get("/login", follow_redirects=False)
    assert plain.headers["location"] == "/app"


def test_safe_next_path():
    assert harness_safe("/share/abcdefghij") == "/share/abcdefghij"
    assert harness_safe("/share/abcdefghij/") == "/share/abcdefghij/"
    assert harness_safe("/share/abc") == ""
    assert harness_safe("/share/abcdefghij?x=1") == ""
    assert harness_safe(None) == ""


def harness_safe(value):
    import app.web as web_module

    return web_module._safe_next_path(value)
