"""/apps/launch/{slug}: the Portal's half of the sign-in hand-off, and the way back through sign-in."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi.testclient import TestClient

from _apps_helpers import REPO_URL, FakeHost, FakeSource, add_group, add_user, enable_apps, make_database, reset_apps_state
from app.services.apps.access import handoff_codes
from app.services.apps.app_service import AppService

APPS = "https://app.example.com"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def harness(monkeypatch):
    from app.main import app
    import app.apps_pages as pages
    import app.web as web_module

    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    qa = add_group(db, "QA", is_default=True)
    owner = add_user(db, "owner", group=qa, nickname="Olive <b>O</b>")
    mate = add_user(db, "mate", group=qa)
    service = AppService(db, host=FakeHost(), source=FakeSource())
    private = service.create(owner, repo_url=REPO_URL, ref="main", name="Mind map", slug="mindmap")
    public = service.create(owner, repo_url=REPO_URL, ref="main", name="Board", slug="board", visibility="public")

    monkeypatch.setattr(pages, "SessionLocal", factory)
    viewer = {"value": (None, "missing_session")}
    monkeypatch.setattr(web_module, "_session_user_access", lambda _request: viewer["value"])
    monkeypatch.setattr(web_module, "sso_enabled", lambda: True)
    monkeypatch.setattr(web_module, "sso_authorize_url", lambda: "https://idp.example.com/authorize")

    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, db=db, owner=owner, mate=mate, private=private, public=public, viewer=viewer, web=web_module, service=service)
    finally:
        db.close()
        reset_apps_state()


def _get(harness, path, **params):
    return harness.client.get(path, params=params or None, follow_redirects=False)


def _callback(response):
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    return f"{location.scheme}://{location.netloc}{location.path}", query


# --- signed out -----------------------------------------------------------------------


def test_signed_out_goes_straight_to_company_sso_and_back_to_the_launch_page(harness):
    response = _get(harness, "/apps/launch/mindmap", next="/mindmap/board?id=3")
    assert response.status_code == 302
    launch = "/apps/launch/mindmap?next=%2Fmindmap%2Fboard%3Fid%3D3"
    assert response.headers["location"] == f"/login/sso?next={quote(launch, safe='')}"
    assert response.headers["cache-control"] == "no-store"

    # /login/sso keeps that address across the round trip to the identity provider.
    start = harness.client.get(response.headers["location"], follow_redirects=False)
    assert start.status_code == 302
    assert start.headers["location"] == "https://idp.example.com/authorize"
    assert f'{harness.web.POST_LOGIN_NEXT_COOKIE}="{launch}"' in start.headers["set-cookie"]


def test_signed_out_without_company_sso_goes_to_the_sign_in_page(harness, monkeypatch):
    monkeypatch.setattr(harness.web, "sso_enabled", lambda: False)
    response = _get(harness, "/apps/launch/mindmap")
    assert response.headers["location"] == f"/login?next={quote('/apps/launch/mindmap', safe='')}"
    # The sign-in page keeps the address in its own; its scripts read it from
    # there once the member has signed in (postLoginDestination, tested below).
    page = harness.client.get(response.headers["location"])
    assert page.status_code == 200
    assert "auth_form.js" in page.text

    # The password form (the bootstrap administrator's) carries it explicitly.
    form = harness.client.get(f"/admlogin?next={quote('/apps/launch/mindmap?next=%2Fmindmap%2Fx', safe='')}")
    assert 'nextPath: "/apps/launch/mindmap?next=%2Fmindmap%2Fx"' in form.text


def test_after_sign_in_the_member_lands_on_the_launch_page_again(harness, monkeypatch):
    async def fake_login(db, *, redirect_uri, code):
        return "session-token"

    monkeypatch.setattr(harness.web, "login_user_by_code", fake_login)
    monkeypatch.setattr(harness.web, "set_session_cookie", lambda response, token: None)
    monkeypatch.setattr(harness.web, "sso_redirect_uri", lambda: "https://portal.example.com/auth")
    launch = "/apps/launch/mindmap?next=%2Fmindmap%2Fboard%3Fid%3D3"
    harness.client.cookies.set(harness.web.POST_LOGIN_NEXT_COOKIE, launch)
    response = harness.client.get("/auth?code=abc", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == launch

    # And from there, now signed in, on to the app with a code.
    harness.viewer["value"] = (harness.owner, None)
    onward = harness.client.get(launch, follow_redirects=False)
    target, query = _callback(onward)
    assert target == f"{APPS}/_efp/callback"
    assert query["next"] == ["/mindmap/board?id=3"]


def test_a_session_that_is_no_longer_valid_signs_in_again(harness):
    harness.viewer["value"] = (None, "invalid_session")
    assert _get(harness, "/apps/launch/mindmap").headers["location"].startswith("/login/sso?next=")


def test_a_member_off_the_allowlist_sees_the_portals_own_page(harness):
    harness.viewer["value"] = (harness.mate, "not_allowlisted")
    response = _get(harness, "/apps/launch/board")
    assert response.status_code == 302
    assert response.headers["location"] == "/unauthorized"


# --- signed in ------------------------------------------------------------------------


def test_allowed_member_is_sent_back_with_a_one_time_code(harness):
    harness.viewer["value"] = (harness.mate, None)
    response = _get(harness, "/apps/launch/board", next="/board/items/7?tab=a&x=1#frag")
    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    target, query = _callback(response)
    assert target == f"{APPS}/_efp/callback"
    assert query["next"] == ["/board/items/7?tab=a&x=1#frag"]
    # The code is the one the host will redeem: bound to this app and this member, once.
    grant = handoff_codes.redeem(query["code"][0])
    assert (grant.app_id, grant.user_id) == (harness.public.id, harness.mate.id)
    assert handoff_codes.redeem(query["code"][0]) is None


def test_every_launch_gets_its_own_code(harness):
    harness.viewer["value"] = (harness.owner, None)
    codes = {_callback(_get(harness, "/apps/launch/mindmap"))[1]["code"][0] for _ in range(3)}
    assert len(codes) == 3


def test_without_next_the_visitor_goes_to_the_apps_home(harness):
    harness.viewer["value"] = (harness.owner, None)
    _target, query = _callback(_get(harness, "/apps/launch/mindmap"))
    assert query["next"] == ["/mindmap/"]


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.com/",
        "//evil.example.com/mindmap/",
        "/board/",
        "/mindmapper/x",
        "/mindmap/../board/",
        "/mindmap/\\evil.example.com",
        "/mindmap/a\r\nLocation: https://evil.example.com",
        "javascript:alert(1)",
        "/mindmap",
        "mindmap/",
        "/mindmap/" + "a" * 3000,
    ],
    ids=["absolute", "scheme-relative", "other-app", "prefix-of-another", "parent", "backslash", "header-injection", "javascript", "no-slash", "relative", "too-long"],
)
def test_a_hostile_next_is_replaced_by_the_apps_home(harness, hostile):
    harness.viewer["value"] = (harness.owner, None)
    response = _get(harness, "/apps/launch/mindmap", next=hostile)
    assert response.status_code == 302
    target, query = _callback(response)
    # Always the configured app host, never anything the visitor named.
    assert target == f"{APPS}/_efp/callback"
    assert query["next"] == ["/mindmap/"]
    assert "evil.example.com" not in response.headers["location"]

    # Signed out, the same value never reaches the sign-in round trip either.
    harness.viewer["value"] = (None, "missing_session")
    signed_out = _get(harness, "/apps/launch/mindmap", next=hostile)
    assert signed_out.headers["location"] == f"/login/sso?next={quote('/apps/launch/mindmap', safe='')}"


def test_refused_member_reads_who_owns_the_app(harness):
    harness.viewer["value"] = (harness.mate, None)
    response = _get(harness, "/apps/launch/mindmap", next="/mindmap/")
    assert response.status_code == 403
    assert "location" not in response.headers
    assert "You do not have access to this app" in response.text
    assert "<strong>Mind map</strong>" in response.text
    # The owner is named, and a name is text: never markup.
    assert "Olive &lt;b&gt;O&lt;/b&gt;" in response.text
    assert "<b>O</b>" not in response.text
    assert "<strong>mate</strong>" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert handoff_codes._codes == {}


def test_unknown_and_deleted_apps_are_404(harness):
    harness.viewer["value"] = (harness.owner, None)
    for slug in ("nope", "Mindmap", "a", "x" * 60):
        response = _get(harness, f"/apps/launch/{slug}")
        assert response.status_code == 404
        assert "This app does not exist" in response.text
    harness.service.delete(harness.private, harness.owner)
    assert _get(harness, "/apps/launch/mindmap").status_code == 404
    # Signed out: a missing app is not a reason to sign in.
    harness.viewer["value"] = (None, "missing_session")
    assert _get(harness, "/apps/launch/nope").status_code == 404


def test_everything_is_404_while_apps_are_off(harness, monkeypatch):
    harness.viewer["value"] = (harness.owner, None)
    enable_apps(monkeypatch, apps_enabled=False)
    assert _get(harness, "/apps/launch/mindmap").status_code == 404


def test_no_public_address_is_said_rather_than_guessed(harness, monkeypatch):
    harness.viewer["value"] = (harness.owner, None)
    enable_apps(monkeypatch, apps_public_base_url="")
    response = _get(harness, "/apps/launch/mindmap")
    assert response.status_code == 503
    assert "Apps are not available" in response.text


def test_an_app_open_to_anyone_still_works_here(harness, monkeypatch):
    enable_apps(monkeypatch, apps_anyone_enabled=True)
    admin = add_user(harness.db, "admin", role="admin")
    harness.service.update(harness.public, admin, visibility="anyone")

    # No session: nothing to sign in for, straight to the app.
    response = _get(harness, "/apps/launch/board", next="/board/x")
    assert response.status_code == 302
    assert response.headers["location"] == f"{APPS}/board/x"

    # A signed-in member still gets a code, so the app knows who they are.
    stranger = add_user(harness.db, "stranger")
    harness.viewer["value"] = (stranger, None)
    target, query = _callback(_get(harness, "/apps/launch/board"))
    assert target == f"{APPS}/_efp/callback"
    assert handoff_codes.redeem(query["code"][0]).user_id == stranger.id

    # Asked for but not approved: a visitor signs in like for any other app.
    harness.service.set_anyone_approval(harness.public, admin, False)
    harness.viewer["value"] = (None, "missing_session")
    assert _get(harness, "/apps/launch/board").headers["location"].startswith("/login/sso?next=")


def test_the_page_follows_the_members_language(harness):
    from app import i18n

    token = i18n.bind_locale("zh-Hans")
    try:
        harness.viewer["value"] = (harness.mate, None)
        response = _get(harness, "/apps/launch/mindmap")
    finally:
        i18n.reset_locale(token)
    assert response.status_code == 403


# --- what the sign-in pages accept as "next" ---------------------------------------------


def _safe(value):
    import app.web as web_module

    return web_module._safe_next_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "/apps/launch/mindmap",
        "/apps/launch/a-1",
        "/apps/launch/mindmap?next=%2Fmindmap%2F",
        "/apps/launch/mindmap?next=%2Fmindmap%2Fboard%3Fid%3D3%26x%3D%252F",
        "/apps/launch/mindmap?next=%2fmindmap%2fx.y_z~-",
        "/share/abcdefghij",
    ],
)
def test_safe_next_accepts_the_launch_page_of_one_app(value):
    assert _safe(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example.com/apps/launch/mindmap",
        "//evil.example.com/apps/launch/mindmap",
        "/apps/launch/mindmap/",
        "/apps/launch/mindmap/extra",
        "/apps/launch/../admin",
        "/apps/launch/Mindmap",
        "/apps/launch/ab",
        "/apps/launch/-ab",
        "/apps/launch/" + "a" * 41,
        "/apps/launch/mindmap?next=/mindmap/",
        "/apps/launch/mindmap?next=https://evil.example.com/",
        "/apps/launch/mindmap?next=%2F%2Fevil.example.com%2F",
        "/apps/launch/mindmap?next=%2Fboard%2F",
        "/apps/launch/mindmap?next=%2Fmindmapper%2F",
        "/apps/launch/mindmap?next=%2Fmindmap",
        "/apps/launch/mindmap?next=%2Fmindmap%2F&other=1",
        "/apps/launch/mindmap?next=%2Fmindmap%2F#fragment",
        "/apps/launch/mindmap?next=%2Fmindmap%2F%ZZ",
        "/apps/launch/mindmap?next=%2Fmindmap%2F%2",
        "/apps/launch/mindmap?next=%2Fmindmap%2F a",
        "/apps/launch/mindmap?other=%2Fmindmap%2F",
        "/apps/launch/mindmap?next=%2Fmindmap%2F\n/x",
        "/apps/launch/mindmap?next=%2Fmindmap%2F" + "a" * 2100,
        "/apps",
        "/apps/launch/",
        "/app",
        "\\apps\\launch\\mindmap",
    ],
)
def test_safe_next_refuses_everything_else(value):
    assert _safe(value) == ""


def test_what_the_launch_page_writes_is_what_the_sign_in_pages_accept():
    from app.apps_pages import launch_path

    assert launch_path("mindmap", "/mindmap/") == "/apps/launch/mindmap"
    for inner in ("/mindmap/board", "/mindmap/a b?q=1&r=%2F#top", "/mindmap/\u4e2d\u6587/x", "/mindmap/" + "seg/" * 300):
        path = launch_path("mindmap", inner)
        assert _safe(path) == path, inner


# --- the sign-in page's own script follows the same rule -------------------------------


def test_the_sign_in_script_uses_the_same_rule_as_the_server():
    import app.web as web_module

    js = (REPO / "app" / "static" / "js" / "auth_form.js").read_text(encoding="utf-8")
    assert "var APP_LAUNCH_NEXT_PATH = " in js
    assert "candidate.length <= 2048 && APP_LAUNCH_NEXT_PATH.test(candidate)" in js
    # The share rule of before is still there, untouched.
    assert "/^\\/share\\/[A-Za-z0-9_-]+\\/?$/" in js
    # One rule in two languages: the slug and the encoded-path parts are spelled the same.
    pattern = web_module._SAFE_NEXT_PATH.pattern
    for fragment in ("[a-z0-9][a-z0-9-]{1,38}[a-z0-9]", "%2[Ff]", "(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})*"):
        assert fragment in pattern and fragment in js


def test_the_sign_in_script_returns_only_to_a_share_or_a_launch_path():
    import json
    import shutil
    import subprocess

    from _js_extract_helpers import _extract_js_function

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the sign-in script behaviour test")
    js = (REPO / "app" / "static" / "js" / "auth_form.js").read_text(encoding="utf-8")
    rule = next(line for line in js.splitlines() if line.startswith("var APP_LAUNCH_NEXT_PATH = "))
    accepted = [
        "/share/abcdefghij",
        "/apps/launch/mindmap",
        "/apps/launch/mindmap?next=%2Fmindmap%2Fboard%3Fid%3D3",
    ]
    refused = [
        "https://evil.example.com/apps/launch/mindmap",
        "//evil.example.com/apps/launch/mindmap",
        "/apps/launch/mindmap?next=https://evil.example.com/",
        "/apps/launch/mindmap?next=%2F%2Fevil.example.com%2F",
        "/apps/launch/mindmap?next=%2Fboard%2F",
        "/apps/launch/mindmap?next=/mindmap/",
        "/apps/launch/mindmap/extra",
        "/apps/launch/Mindmap",
        "/apps/launch/mindmap?next=%2Fmindmap%2F" + "a" * 2100,
        "/app",
        "",
    ]
    script = (
        "globalThis.window = { location: { search: '' } };\n"
        + rule
        + "\n"
        + _extract_js_function(js, "postLoginDestination")
        + "\nconst cases = "
        + json.dumps(accepted + refused)
        + ";\nprocess.stdout.write(JSON.stringify(cases.map((value) => postLoginDestination(value))));\n"
        # With no explicit value the page's own ?next= is read.
        + "globalThis.window.location.search = '?next=' + encodeURIComponent('/apps/launch/mindmap?next=%2Fmindmap%2Fx');\n"
        + "process.stdout.write('\\n' + postLoginDestination(''));\n"
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    answers, from_query = result.stdout.split("\n", 1)
    assert json.loads(answers) == accepted + ["/app"] * len(refused)
    assert from_query == "/apps/launch/mindmap?next=%2Fmindmap%2Fx"
    # And the server agrees with every one of them.
    for value in accepted:
        assert _safe(value) == value
    for value in refused:
        assert _safe(value) == ""
