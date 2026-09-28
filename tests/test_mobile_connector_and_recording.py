"""BrowserStack connector (advanced fields, the bridge test, the panel) and what
the Recording panel gets from Portal: the member's settings and the hosted
Appium Inspector. Portal itself never calls BrowserStack; the member's
computer does, through the local bridge."""
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

from app.config import get_settings
from app.db import Base, get_db
from app.deps import get_current_user
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.services.connector_registry import get_connector_spec
from app.web import _settings_merge_payload
from tests.test_web_connector_settings import _bind_profile, _build_env

MOBILE_JS = Path("app/static/js/mobile_testing.js")


# --- connector ----------------------------------------------------------------


def test_browserstack_connector_tests_from_the_page_not_the_server():
    spec = get_connector_spec("browserstack")
    # Portal cannot reach BrowserStack; the panel signs in through the local bridge.
    assert spec.test_targets == ()
    assert spec.panel_extra_template == "partials/connectors/browserstack_extra.html"


def _mobile_form(**fields):
    form = {"__touch_mobile": "1", "mobile_enabled": "on", "mobile_browserstack_username": "alice"}
    form.update(fields)
    return form


def test_advanced_fields_merge_into_defaults_and_browserstack():
    merged, error = _settings_merge_payload(
        {"mobile-auto": {"browserstack": {"access_key": "keep-me"}}},
        _mobile_form(
            mobile_default_platform="android",
            mobile_network_mode="private-managed",
            mobile_idle_timeout_seconds="300",
            mobile_video__present="1",
            mobile_interactive_debugging__present="1",
            mobile_interactive_debugging="on",
            mobile_browserstack_appium_base_url="https://hub-cloud.browserstack.com/wd/hub/",
            mobile_browserstack_local_mode="managed",
            mobile_browserstack_verify_ssl__present="1",
            mobile_browserstack_verify_ssl="on",
        ),
    )
    assert error is None
    mobile = merged["mobile-auto"]
    assert mobile["defaults"] == {
        "platform": "android",
        "network_mode": "private-managed",
        "idle_timeout_seconds": 300,
        "video": False,
        "interactive_debugging": True,
    }
    bs = mobile["browserstack"]
    assert bs["access_key"] == "keep-me"
    assert bs["appium_base_url"] == "https://hub-cloud.browserstack.com/wd/hub"
    assert bs["local"] == {"mode": "managed"}
    assert bs["verify_ssl"] is True


def test_fields_not_posted_keep_their_stored_values():
    stored = {"mobile-auto": {"defaults": {"idle_timeout_seconds": 200, "video": False}, "browserstack": {"api_base_url": "https://api-cloud.browserstack.com"}}}
    merged, error = _settings_merge_payload(stored, _mobile_form())
    assert error is None
    assert merged["mobile-auto"]["defaults"] == {"idle_timeout_seconds": 200, "video": False}
    assert merged["mobile-auto"]["browserstack"]["api_base_url"] == "https://api-cloud.browserstack.com"


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("mobile_idle_timeout_seconds", "900", "30 to 300"),
        ("mobile_idle_timeout_seconds", "abc", "whole number"),
        ("mobile_browserstack_appium_base_url", "http://hub-cloud.browserstack.com/wd/hub", "https://"),
        ("mobile_browserstack_api_base_url", "https://evil.example.com", "browserstack.com"),
        ("mobile_network_mode", "vpn", "Unsupported"),
    ],
)
def test_invalid_advanced_values_are_rejected(field, value, message):
    _, error = _settings_merge_payload({}, _mobile_form(**{field: value}))
    assert error and message in error


def test_browserstack_panel_tests_through_the_bridge_and_says_where_runs_happen(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"mobile-auto": {"enabled": True, "browserstack": {"username": "alice", "access_key": "k"}}})
        html = env.client.get("/app/connectors/browserstack/panel").text
        assert "data-mobile-bridge-test" in html and 'data-test-target="browserstack"' not in html
        assert 'name="mobile_idle_timeout_seconds"' in html
        assert "data-test-secrets" not in html and "data-app-packages" not in html
        # Where recordings and runs happen sits below the settings form.
        assert html.index("data-mobile-overview") > html.index("</form>")
        assert "Test runs in Jenkins" in html
        # Without a hosted Inspector the page points at the desktop app.
        assert "appium-inspector/releases" in html
    finally:
        env.cleanup()


def test_no_portal_route_talks_to_browserstack():
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/mobile/recording-config" in paths
    for gone in ("/api/app-packages", "/api/mobile-recordings", "/app/mobile/wd/{recording_id}/{token}/{wd_path:path}"):
        assert not any(path.startswith(gone.split("{")[0].rstrip("/")) for path in paths if path), gone


# --- what the Recording panel gets from Portal -----------------------------------


@pytest.fixture
def api(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    owner = User(username="owner", password_hash="x", role="user", is_active=True)
    other = User(username="other", password_hash="x", role="user", is_active=True)
    db.add_all([owner, other])
    db.commit()
    profile = RuntimeProfile(owner_user_id=owner.id, name="p", revision=1, is_default=True, config_json=json.dumps({
        "mobile-auto": {
            "enabled": True,
            "defaults": {"platform": "android", "idle_timeout_seconds": 300, "video": True},
            "browserstack": {"username": "owner-bs", "access_key": "key", "appium_base_url": "https://hub-cloud.browserstack.com/wd/hub"},
        },
    }))
    agent = Agent(name="qa", owner_user_id=owner.id, visibility="private", status="running", image="img", disk_size_gi=20,
                  mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace")
    db.add_all([profile, agent])
    db.commit()

    inspector_dir = tmp_path / "inspector"
    (inspector_dir / "assets").mkdir(parents=True)
    (inspector_dir / "index.html").write_text("<html>inspector</html>", encoding="utf-8")
    (inspector_dir / "assets" / "index-1.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "appium_inspector_dir", str(inspector_dir))

    from app.main import app

    state = {"user": owner}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    try:
        yield SimpleNamespace(client=TestClient(app), db=db, owner=owner, other=other, agent=agent, profile=profile, state=state)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_recording_config_hands_the_owner_their_settings_for_the_bridge(api):
    resp = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert body["configured"] is True and body["problem"] is None
    assert body["credentials"] == {
        "username": "owner-bs",
        "access_key": "key",
        "api_base_url": "",
        "appium_base_url": "https://hub-cloud.browserstack.com/wd/hub",
    }
    assert body["defaults"]["platform"] == "android" and body["defaults"]["idle_timeout_seconds"] == 300
    assert body["defaults"]["video"] is True
    assert body["inspector_available"] is True and body["recordings_dir"] == "mobile/recordings"


def test_recording_config_is_for_the_owner_only(api):
    api.state["user"] = api.other
    assert api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").status_code == 403
    api.state["user"] = api.owner
    assert api.client.get("/api/mobile/recording-config?agent_id=nope").status_code == 404


@pytest.mark.parametrize(
    "mobile, problem",
    [
        ({"enabled": False, "browserstack": {"username": "u", "access_key": "k"}}, "Turn the BrowserStack connector on"),
        ({"enabled": True, "browserstack": {"username": "u"}}, "Add your BrowserStack username and access key"),
    ],
)
def test_recording_config_says_what_is_missing_and_hands_out_nothing(api, mobile, problem):
    api.profile.config_json = json.dumps({"mobile-auto": mobile})
    api.db.commit()
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["configured"] is False and body["problem"].startswith(problem)
    assert body["credentials"] is None


def test_hosted_inspector_serves_its_build_and_nothing_else(api, monkeypatch):
    import app.web as web_module

    monkeypatch.setattr(web_module, "_authorized_web_user", lambda _request: (api.owner, None))
    assert api.client.get("/inspector/").text == "<html>inspector</html>"
    asset = api.client.get("/inspector/assets/index-1.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    assert api.client.get("/inspector/some/client/route").text == "<html>inspector</html>"
    assert "FastAPI" not in api.client.get("/inspector/%2e%2e/%2e%2e/app/main.py").text
    redirect = api.client.get("/inspector?state=x&autoStart=1", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/inspector/?state=x&autoStart=1"

    monkeypatch.setattr(get_settings(), "appium_inspector_dir", "")
    assert api.client.get("/inspector/").status_code == 404
    assert api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()["inspector_available"] is False


# --- the page's side ------------------------------------------------------------


def test_the_page_drives_the_local_bridge_not_portal():
    js = MOBILE_JS.read_text(encoding="utf-8")
    for gone in ("/api/app-packages", "/api/mobile-recordings", "/app/mobile/wd/"):
        assert gone not in js, gone
    assert "http://127.0.0.1:${state.port}/mobile/run" in js
    assert "http://127.0.0.1:${state.port}/mobile/apps/upload" in js
    assert "/api/mobile/recording-config" in js
    # The hosted Inspector attaches to the bridge on this computer.
    assert 'hostname: "127.0.0.1"' in js and "path: `/mobile/wd/${rec.id}`" in js and "ssl: false" in js
    # Segments land where the record-mobile-segment skill looks for them.
    assert 'const RECORDINGS_DIR = "mobile/recordings";' in js
    assert "Segment ${segment} recorded: ${path}" in js


def test_recording_button_is_wired_into_the_tool_panel():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'id="btn-recording"' in html
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert '"recording",' in js[js.index("const ALLOWED_UTILITY_PANEL_KEYS"): js.index("]);", js.index("const ALLOWED_UTILITY_PANEL_KEYS"))]
    assert "window.EfpMobileTesting.openRecordingPanel" in js


def test_recording_panel_helpers_and_bridge_calls_in_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = MOBILE_JS.read_text(encoding="utf-8")
    shim = textwrap.dedent(
        """
        globalThis.window = globalThis;
        globalThis.document = { readyState: "complete", addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; } };
        const calls = [];
        globalThis.fetch = async (url, options = {}) => {
          calls.push({ url, options });
          if (url.endsWith("/ping")) {
            if (!url.startsWith("http://127.0.0.1:8766/")) throw new TypeError("Failed to fetch");
            return { ok: true, status: 200, json: async () => ({ ok: true, data: { version: "1.2.3", capabilities: ["browser", "mobile"], mobile: { available: true } } }) };
          }
          if (url === "http://127.0.0.1:8766/mobile/run") {
            const body = JSON.parse(options.body);
            if (body.command === "plan") {
              return { ok: true, status: 200, json: async () => ({ ok: true, data: { username: body.credentials.username, parallel_sessions_running: 1, parallel_sessions_max_allowed: 5 } }) };
            }
            return { ok: false, status: 404, json: async () => ({ ok: false, error: { code: "not_found", message: "recording not found", hint: "Start a new recording from the Recording panel." } }) };
          }
          throw new Error("unexpected " + url);
        };
        """
    )
    script = shim + source + textwrap.dedent(
        """
        const assert = require("node:assert/strict");
        const M = window.EfpMobileTesting;
        assert.deepEqual(M.parseSegments("seg-login\\n seg skip intro , ../x\\n\\n"), ["seg-login", "seg-skip-intro", "x"]);
        assert.equal(M.suggestCustomId("FXApp-1.4.2-uat.apk"), "fxapp-uat-android");
        assert.equal(M.suggestCustomId("notes.txt"), "");
        (async () => {
          const state = await M.probeBridge({ force: true });
          assert.deepEqual(state, { alive: true, port: 8766, version: "1.2.3", mobile: true, mobileAuto: true });
          const plan = await M.callBridge("plan", {}, { credentials: { username: "alice", access_key: "k" } });
          assert.equal(plan.username, "alice");
          const sent = JSON.parse(calls.find((c) => c.url.endsWith("/mobile/run")).options.body);
          assert.deepEqual(sent, { command: "plan", params: {}, credentials: { username: "alice", access_key: "k" }, proxy: "" });
          await assert.rejects(M.callBridge("session.status", { id: "x" }), (err) => err.code === "not_found" && /recording not found/.test(err.message));
        })().catch((error) => {
          console.error(error);
          process.exit(1);
        });
        """
    )
    # A file, not node -e: the inlined module is past Windows' command-line limit.
    script_path = tmp_path / "recording_panel_test.js"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(script_path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
