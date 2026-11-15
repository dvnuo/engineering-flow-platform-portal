"""The single sign-on callback is accepted only for the sign-in this browser started."""

import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.security import session_cookie
from app.security.sso_state import SSO_STATE_COOKIE, sso_state_cookie_name, sso_state_is_valid


def _set_cookies(response) -> list[str]:
    return list(response.headers.get_list("set-cookie"))


@pytest.fixture
def harness(monkeypatch):
    from app.main import app
    import app.web as web_module

    exchanged = []

    async def fake_login(db, *, redirect_uri, code):
        exchanged.append(code)
        return "session-token"

    monkeypatch.setattr(web_module.settings, "sso_issuer_url", "https://sso.example.com/realms/persons")
    monkeypatch.setattr(web_module.settings, "base_uri", "http://testserver")
    monkeypatch.setattr(session_cookie.settings, "base_uri", "http://testserver")
    monkeypatch.setattr(web_module, "login_user_by_code", fake_login)
    return SimpleNamespace(client=TestClient(app), web=web_module, exchanged=exchanged)


def _start(harness):
    response = harness.client.get("/login/sso", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return response, query["state"][0]


def test_sso_start_sends_a_random_state_and_keeps_it_in_a_cookie(harness):
    response, state = _start(harness)
    assert response.headers["location"].startswith("https://sso.example.com/realms/persons/protocol/openid-connect/auth?")
    assert len(state) >= 32
    cookie = next(value for value in _set_cookies(response) if value.startswith(f"{SSO_STATE_COOKIE}="))
    assert cookie.startswith(f"{SSO_STATE_COOKIE}={state};")
    assert "HttpOnly" in cookie and "Max-Age=600" in cookie and "Path=/" in cookie
    # Lax: the identity provider's redirect back is a cross-site navigation.
    assert "SameSite=lax" in cookie
    assert harness.client.cookies.get(SSO_STATE_COOKIE) == state

    _again, another = _start(harness)
    assert another != state


def test_the_callback_with_the_issued_state_signs_in(harness):
    _response, state = _start(harness)
    callback = harness.client.get(f"/auth?code=abc&state={state}&session_state=x", follow_redirects=False)
    assert callback.status_code == 302
    assert callback.headers["location"] == "/app"
    assert harness.exchanged == ["abc"]
    cookies = _set_cookies(callback)
    assert any(value.startswith("portal_session=session-token") for value in cookies)
    # One use: the state cookie is expired with the answer.
    assert any(value.startswith(f'{SSO_STATE_COOKIE}="";') and "Max-Age=0" in value for value in cookies)


@pytest.mark.parametrize(
    "query",
    ["code=abc", "code=abc&state=", "code=abc&state=someone-elses-state", "code=abc&state=%20"],
)
def test_a_callback_without_the_issued_state_is_sent_back_to_the_login_page(harness, caplog, query):
    _start(harness)
    with caplog.at_level(logging.WARNING, logger="app.web"):
        callback = harness.client.get(f"/auth?{query}", follow_redirects=False)
    assert callback.status_code == 302
    assert callback.headers["location"] == "/login"
    # The code was never exchanged and no session was started.
    assert harness.exchanged == []
    cookies = _set_cookies(callback)
    assert not any(value.startswith("portal_session=session-token") for value in cookies)
    assert any("SSO callback rejected" in record.getMessage() for record in caplog.records)


def test_a_callback_in_a_browser_that_never_started_a_sign_in_is_rejected(harness):
    """Login CSRF: an attacker's own code and state, opened in the member's browser."""
    callback = harness.client.get("/auth?code=attacker-code&state=attacker-state", follow_redirects=False)
    assert callback.headers["location"] == "/login"
    assert harness.exchanged == []


def test_a_forged_callback_does_not_sign_the_member_out(harness):
    harness.client.cookies.set("portal_session", "the-members-session")
    callback = harness.client.get("/auth?code=attacker-code&state=attacker-state", follow_redirects=False)
    assert callback.headers["location"] == "/login"
    assert not any(value.startswith('portal_session="";') for value in _set_cookies(callback))


def test_the_state_cannot_be_replayed(harness):
    _response, state = _start(harness)
    first = harness.client.get(f"/auth?code=abc&state={state}", follow_redirects=False)
    assert first.headers["location"] == "/app"
    second = harness.client.get(f"/auth?code=def&state={state}", follow_redirects=False)
    assert second.headers["location"] == "/login"
    assert harness.exchanged == ["abc"]


def test_callback_without_a_code_still_goes_to_login(harness):
    _response, state = _start(harness)
    callback = harness.client.get(f"/auth?state={state}", follow_redirects=False)
    assert callback.status_code == 302 and callback.headers["location"] == "/login"


def test_the_state_cookie_is_host_bound_and_secure_over_https(harness, monkeypatch):
    from app.main import app

    monkeypatch.setattr(harness.web.settings, "base_uri", "https://efp.example.com")
    monkeypatch.setattr(session_cookie.settings, "base_uri", "https://efp.example.com")
    client = TestClient(app, base_url="https://efp.example.com")
    response = client.get("/login/sso", follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert sso_state_cookie_name() == "__Host-portal_sso_state"
    cookie = next(value for value in _set_cookies(response) if value.startswith("__Host-portal_sso_state="))
    assert f"={state};" in cookie and "Secure" in cookie and "HttpOnly" in cookie and "Path=/" in cookie
    assert "Domain" not in cookie

    # A state planted under the plain name (which a sibling subdomain could set
    # for the parent domain) is not the one the callback is checked against.
    forged = TestClient(app, base_url="https://efp.example.com")
    forged.cookies.set("portal_sso_state", "planted")
    callback = forged.get("/auth?code=attacker-code&state=planted", follow_redirects=False)
    assert callback.headers["location"] == "/login"
    assert harness.exchanged == []

    callback = client.get(f"/auth?code=abc&state={state}", follow_redirects=False)
    assert callback.headers["location"] == "/app"
    cookies = _set_cookies(callback)
    cleared = next(value for value in cookies if value.startswith('__Host-portal_sso_state="";'))
    assert "Secure" in cleared and "Path=/" in cleared
    assert any(value.startswith("__Host-portal_session=session-token") and "Secure" in value for value in cookies)


def test_sso_state_is_valid():
    name = sso_state_cookie_name()
    assert sso_state_is_valid({name: "abc"}, "abc") is True
    assert sso_state_is_valid({name: "abc"}, "abd") is False
    assert sso_state_is_valid({name: "abc"}, "") is False
    assert sso_state_is_valid({name: "abc"}, None) is False
    assert sso_state_is_valid({name: ""}, "") is False
    assert sso_state_is_valid({}, "abc") is False
    assert sso_state_is_valid(None, "abc") is False
    assert sso_state_is_valid({name: "abc"}, ["abc"]) is False


def test_authorize_url_carries_the_state():
    import app.utils.sso_auth as sso

    query = parse_qs(urlsplit(sso.sso_authorize_url("s-123")).query)
    assert query["state"] == ["s-123"]
