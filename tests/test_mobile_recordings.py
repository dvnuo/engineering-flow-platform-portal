"""Recording segments with the hosted Appium Inspector: handshake, command log,
the WebDriver proxy, segment write-back, and the Inspector route."""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import Agent, MobileRecording, MobileRecordingEvent, User
from app.models.runtime_profile import RuntimeProfile
from app.services import mobile_recording_service as recording_module
from app.services.mobile_recording_service import (
    MobileRecordingService,
    RecordingError,
    element_attributes,
    is_secret_element,
    parse_handshake,
    source_cache,
)

ANDROID_SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <android.widget.FrameLayout class="android.widget.FrameLayout">
    <android.widget.EditText class="android.widget.EditText" resource-id="com.bank:id/username" text="Username" />
    <android.widget.EditText class="android.widget.EditText" resource-id="com.bank:id/password" password="true" text="hunter2" />
    <android.widget.Button class="android.widget.Button" content-desc="Login" resource-id="com.bank:id/login" text="Log in" />
    <android.widget.TextView class="android.widget.TextView" text="USD" />
    <android.widget.TextView class="android.widget.TextView" text="EUR" />
  </android.widget.FrameLayout>
</hierarchy>"""

HANDSHAKE = {
    "format": "efp-mobile-recording/v1",
    "run_id": "run-1",
    "session_id": "abc123session",
    "platform": "android",
    "device": "Google Pixel 8",
    "remote_url": "https://evil.example.com/wd/hub",
    "control_owner": "human",
    "hold_deadline": "2026-09-27T12:30:00Z",
}


# --- service ----------------------------------------------------------------------


def test_parse_handshake_takes_only_session_fields():
    parsed = parse_handshake(json.dumps(HANDSHAKE))
    assert parsed["session_id"] == "abc123session" and parsed["platform"] == "android"
    assert "remote_url" not in parsed, "the hub must never come from the workspace file"
    with pytest.raises(RecordingError):
        parse_handshake('{"format": "other"}')
    with pytest.raises(RecordingError):
        parse_handshake(json.dumps({**HANDSHAKE, "session_id": "../../x"}))


@pytest.mark.parametrize(
    "using, value, expected",
    [
        ("id", "com.bank:id/login", "Login"),
        ("id", "login", "Login"),
        ("accessibility id", "Login", "Login"),
        ("xpath", '//android.widget.Button[@text="Log in"]', "Login"),
        ("-android uiautomator", 'new UiSelector().resourceId("com.bank:id/login")', "Login"),
    ],
)
def test_element_attributes_resolve_common_locators(using, value, expected):
    attrs = element_attributes(ANDROID_SOURCE, using, value, "android")[0]
    assert attrs["content-desc"] == expected
    assert attrs["resource-id"] == "com.bank:id/login"


def test_element_attributes_for_several_matches_and_positional_xpath():
    attrs = element_attributes(ANDROID_SOURCE, "class name", "android.widget.TextView", "android", count=2)
    assert [a["text"] for a in attrs] == ["USD", "EUR"]
    second = element_attributes(ANDROID_SOURCE, "xpath", '(//android.widget.TextView)[2]', "android")[0]
    assert second["text"] == "EUR"
    assert element_attributes(ANDROID_SOURCE, "xpath", "/hierarchy/android.widget.FrameLayout[1]/x", "android") == [None]
    assert element_attributes(None, "id", "x", "android", count=2) == [None, None]


def test_password_fields_lose_their_value_and_count_as_secret():
    attrs = element_attributes(ANDROID_SOURCE, "id", "com.bank:id/password", "android")[0]
    assert "text" not in attrs and attrs["password"] == "true"
    assert is_secret_element(attrs)
    assert is_secret_element({"type": "XCUIElementTypeSecureTextField"})
    assert is_secret_element(None, "com.bank:id/pin")
    assert not is_secret_element({"resource-id": "com.bank:id/username"})


# --- API -------------------------------------------------------------------------------


class _FakeHub:
    """httpx.AsyncClient stand-in for the BrowserStack hub."""

    requests: list = []
    source = ANDROID_SOURCE

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, params=None, content=None, headers=None, auth=None):
        _FakeHub.requests.append({"method": method, "url": url, "body": content, "auth": auth})
        path = url.split("/session/abc123session", 1)[1]
        if path == "/source":
            payload = {"value": _FakeHub.source}
        elif path == "/window/rect":
            payload = {"value": {"x": 0, "y": 0, "width": 1080, "height": 2400}}
        elif path == "/element":
            value = json.loads(content)["value"]
            element_id = "el-" + "".join(ch if ch.isalnum() else "-" for ch in value)
            payload = {"value": {"element-6066-11e4-a52e-4f735466cecf": element_id}}
        else:
            payload = {"value": None}
        return SimpleNamespace(status_code=200, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    owner = User(username="owner", password_hash="x", role="user", is_active=True)
    other = User(username="other", password_hash="x", role="user", is_active=True)
    db.add_all([owner, other])
    db.commit()
    db.add(RuntimeProfile(owner_user_id=owner.id, name="p", revision=1, is_default=True, config_json=json.dumps(
        {"mobile-auto": {"enabled": True, "browserstack": {"username": "owner-bs", "access_key": "key"}}})))
    agent = Agent(name="qa", owner_user_id=owner.id, visibility="private", status="running", image="img", disk_size_gi=20,
                  mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace")
    db.add(agent)
    db.commit()

    import app.api.mobile_recordings as api_module

    forwarded = {"uploads": []}

    async def fake_forward(**kwargs):
        forwarded["read"] = kwargs
        if forwarded.get("missing"):
            return 404, b"not found", "text/plain"
        return 200, json.dumps(HANDSHAKE).encode(), "application/json"

    async def fake_forward_multipart(**kwargs):
        forwarded["uploads"].append(kwargs)
        return 200, b'{"success": true}', "application/json"

    monkeypatch.setattr(api_module.proxy_service, "forward", fake_forward)
    monkeypatch.setattr(api_module.proxy_service, "forward_multipart", fake_forward_multipart)
    _FakeHub.requests = []
    monkeypatch.setattr(api_module.httpx, "AsyncClient", _FakeHub)

    inspector_dir = tmp_path / "inspector"
    (inspector_dir / "assets").mkdir(parents=True)
    (inspector_dir / "index.html").write_text("<html>inspector</html>", encoding="utf-8")
    (inspector_dir / "assets" / "index-1.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(recording_module.get_settings(), "appium_inspector_dir", str(inspector_dir))

    from app.main import app

    state = {"user": owner}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    try:
        yield SimpleNamespace(client=TestClient(app), db=db, owner=owner, other=other, agent=agent, forwarded=forwarded, state=state)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _open(env) -> dict:
    resp = env.client.post("/api/mobile-recordings", json={"agent_id": env.agent.id, "segment": "seg-login"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_open_binds_the_published_session_to_the_members_hub(env):
    opened = _open(env)
    assert opened["session_id"] == "abc123session" and opened["segment"] == "seg-login"
    assert env.forwarded["read"]["query_items"] == [("path", "mobile/recording/active.json")]
    row = env.db.get(MobileRecording, opened["id"])
    assert row.hub_url == "https://hub-cloud.browserstack.com/wd/hub"
    assert opened["token"] and row.token_hash != opened["token"]
    url = opened["inspector_url"]
    assert url.startswith("/inspector/?state=") and url.endswith("&autoStart=1")
    state = json.loads(__import__("urllib.parse").parse.unquote(url.split("state=", 1)[1].split("&", 1)[0]))
    assert state["attachSessId"] == "abc123session"
    assert state["server"]["remote"]["path"] == f"/app/mobile/wd/{opened['id']}/{opened['token']}"

    # A second open replaces the first: only the newest Inspector tab works.
    second = _open(env)
    env.db.refresh(row)
    assert row.status == "closed" and second["id"] != opened["id"]


def test_open_needs_the_owner_and_a_published_session(env):
    env.state["user"] = env.other
    assert env.client.post("/api/mobile-recordings", json={"agent_id": env.agent.id}).status_code == 403
    env.state["user"] = env.owner
    env.forwarded["missing"] = True
    resp = env.client.post("/api/mobile-recordings", json={"agent_id": env.agent.id})
    assert resp.status_code == 409 and "not published" in resp.json()["detail"]


def _wd(env, opened, method, path, **kwargs):
    return env.client.request(method, f"/app/mobile/wd/{opened['id']}/{opened['token']}/{path}", **kwargs)


def test_proxy_answers_attach_itself_and_never_ends_the_session(env):
    opened = _open(env)
    caps = _wd(env, opened, "GET", "session/abc123session").json()["value"]
    assert caps["platformName"] == "Android" and caps["appium:deviceName"] == "Google Pixel 8"
    assert _wd(env, opened, "GET", "sessions").json()["value"][0]["id"] == "abc123session"
    assert _wd(env, opened, "DELETE", "session/abc123session").status_code == 200
    assert _wd(env, opened, "POST", "session", json={}).status_code == 403
    assert _wd(env, opened, "GET", "session/someone-else/source").status_code == 404
    assert _FakeHub.requests == [], "none of these may reach BrowserStack"
    bad = env.client.get(f"/app/mobile/wd/{opened['id']}/wrong-token/session/abc123session/source")
    assert bad.status_code == 404


def test_proxy_forwards_with_member_credentials_and_logs_the_segment(env):
    opened = _open(env)
    assert _wd(env, opened, "GET", "session/abc123session/window/rect").status_code == 200
    assert _wd(env, opened, "GET", "session/abc123session/source").status_code == 200
    found = _wd(env, opened, "POST", "session/abc123session/element", json={"using": "id", "value": "com.bank:id/password"}).json()
    element = found["value"]["element-6066-11e4-a52e-4f735466cecf"]
    _wd(env, opened, "POST", f"session/abc123session/element/{element}/value", json={"text": "hunter2", "value": list("hunter2")})
    login = _wd(env, opened, "POST", "session/abc123session/element", json={"using": "accessibility id", "value": "Login"}).json()["value"]["element-6066-11e4-a52e-4f735466cecf"]
    _wd(env, opened, "POST", f"session/abc123session/element/{login}/click", json={})
    assert _FakeHub.requests[0]["url"] == "https://hub-cloud.browserstack.com/wd/hub/session/abc123session/window/rect"
    assert _FakeHub.requests[0]["auth"] == ("owner-bs", "key")

    stored = json.dumps([e.request_json for e in env.db.query(MobileRecordingEvent).all()])
    assert "hunter2" not in stored, "a password must never be stored"

    resp = env.client.post(f"/api/mobile-recordings/{opened['id']}/segments", json={"next_segment": "seg-skip-intro"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"segment": "seg-login", "path": "mobile/recordings/seg-login.wdlog.json", "actions": 2, "finds": 2, "secrets": 1, "next_segment": "seg-skip-intro"}
    upload = env.forwarded["uploads"][0]
    assert upload["subpath"] == "api/server-files/upload" and upload["data"] == {"path": "mobile/recordings"}
    name, content, _ctype = upload["files"]["file"]
    log = json.loads(content)
    assert name == "seg-login.wdlog.json" and log["format"] == "efp-webdriver-log/v1"
    assert log["viewport"] == {"width": 1080, "height": 2400}
    typed = [e for e in log["events"] if e["path"].endswith("/value")][0]
    assert typed["secret"] is True and typed["request"] == {"text": None}
    login_find = [e for e in log["events"] if e.get("request", {}).get("value") == "Login"][0]
    assert login_find["element"]["content-desc"] == "Login"

    # The next segment starts empty.
    resp = env.client.post(f"/api/mobile-recordings/{opened['id']}/segments", json={})
    assert resp.status_code == 409


def test_closed_recordings_stop_proxying(env):
    opened = _open(env)
    assert env.client.post(f"/api/mobile-recordings/{opened['id']}/close").status_code == 200
    assert _wd(env, opened, "GET", "session/abc123session/source").status_code == 404
    status = env.client.get(f"/api/mobile-recordings/status?agent_id={env.agent.id}").json()
    assert status["recording"] is None and status["inspector_available"] is True
    assert status["hub_url"] == "https://hub-cloud.browserstack.com/wd/hub"


def test_hosted_inspector_serves_its_build_and_nothing_else(env, monkeypatch):
    import app.web as web_module

    monkeypatch.setattr(web_module, "_authorized_web_user", lambda _request: (env.owner, None))
    assert env.client.get("/inspector/").text == "<html>inspector</html>"
    asset = env.client.get("/inspector/assets/index-1.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    assert env.client.get("/inspector/some/client/route").text == "<html>inspector</html>"
    assert env.client.get("/inspector/../../app/main.py").status_code in (200, 404)
    assert "FastAPI" not in env.client.get("/inspector/%2e%2e/%2e%2e/app/main.py").text
    redirect = env.client.get("/inspector?state=x&autoStart=1", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/inspector/?state=x&autoStart=1"

    monkeypatch.setattr(recording_module.get_settings(), "appium_inspector_dir", "")
    assert env.client.get("/inspector/").status_code == 404


def test_recording_panel_segment_parsing_in_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = Path("app/static/js/mobile_testing.js").read_text(encoding="utf-8")
    shim = "globalThis.window = globalThis; globalThis.document = { addEventListener() {}, querySelector() { return null; } };\n"
    script = shim + source + textwrap.dedent(
        """
        const assert = require("node:assert/strict");
        const M = window.EfpMobileTesting;
        assert.deepEqual(M.parseSegments("seg-login\\n seg skip intro , ../x\\n\\n"), ["seg-login", "seg-skip-intro", "x"]);
        assert.equal(M.suggestCustomId("FXApp-1.4.2-uat.apk"), "fxapp-uat-android");
        assert.equal(M.suggestCustomId("notes.txt"), "");
        """
    )
    # A file, not node -e: the inlined module is past Windows' command-line limit.
    script_path = tmp_path / "recording_panel_test.js"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(script_path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_recording_button_is_wired_into_the_tool_panel():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'id="btn-recording"' in html
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert '"recording",' in js[js.index("const ALLOWED_UTILITY_PANEL_KEYS"): js.index("]);", js.index("const ALLOWED_UTILITY_PANEL_KEYS"))]
    assert "window.EfpMobileTesting?.openRecordingPanel?.()" in js
