"""BrowserStack connector (advanced fields, the bridge test, the panel) and what
the Mobile testing panel gets from Portal: the member's settings and the hosted
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
            mobile_appium_version="2.19.0",
            mobile_video__present="1",
            mobile_interactive_debugging__present="1",
            mobile_interactive_debugging="on",
            mobile_browserstack_appium_base_url="https://hub-cloud.browserstack.com/wd/hub/",
        ),
    )
    assert error is None
    mobile = merged["mobile-auto"]
    assert mobile["defaults"] == {
        "platform": "android",
        "network_mode": "private-managed",
        "idle_timeout_seconds": 300,
        "appium_version": "2.19.0",
        "video": False,
        "interactive_debugging": True,
    }
    bs = mobile["browserstack"]
    assert bs["access_key"] == "keep-me"
    assert bs["appium_base_url"] == "https://hub-cloud.browserstack.com/wd/hub"
    # Settings nothing reads any more are not on the form.
    assert "local" not in bs and "verify_ssl" not in bs


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
        ("mobile_appium_version", "2.x", "look like 2.19.0"),
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
        # The proxy for this computer is set on the connector page too, with
        # the login a corporate proxy asks for in fields of its own: the
        # password in a password input, never in the address.
        assert 'data-bridge-proxy="url"' in html and 'data-bridge-proxy="username"' in html
        assert '<input type="password" class="portal-form-input" data-bridge-proxy="password"' in html
        assert "user:password@" not in html
        assert 'name="mobile_idle_timeout_seconds"' in html
        assert 'name="mobile_browserstack_local_mode"' not in html and 'name="mobile_browserstack_verify_ssl"' not in html
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


# --- what the Mobile testing panel gets from Portal -----------------------------------


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
            "defaults": {"platform": "android", "idle_timeout_seconds": 300, "video": True, "appium_version": "2.19.0"},
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
    # The device's Appium version travels to the bridge with the other defaults.
    assert body["defaults"]["appium_version"] == "2.19.0"
    assert body["inspector_available"] is True and body["recordings_dir"] == "mobile/recordings"


def test_recording_config_falls_back_to_the_deployments_network(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "mobile_default_network", "private-managed")
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["defaults"]["network"] == "private-managed"
    api.profile.config_json = json.dumps({"mobile-auto": {"enabled": True, "defaults": {"network_mode": "public"}, "browserstack": {"username": "u", "access_key": "k"}}})
    api.db.commit()
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["defaults"]["network"] == "public", "a member's own choice wins"


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
    # The page carries Portal's add-on script; the build's files stay as published.
    page = api.client.get("/inspector/").text
    assert page.startswith("<html>inspector</html>") and 'src="/static/js/inspector_addon.js' in page
    asset = api.client.get("/inspector/assets/index-1.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    assert api.client.get("/inspector/some/client/route").text == page
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
    # Recordings land where the record-mobile-segment skill looks for them,
    # announced with the messages it answers to.
    assert 'const RECORDINGS_DIR = "mobile/recordings";' in js
    assert "Segment ${name} recorded: ${path}" in js and "Recording ${name} saved: ${path}" in js
    assert "Recording finished. Compile any segment not imported yet." in js
    # The panel is Mobile testing: the device on top, a progress strip, then
    # the step at hand; settings behind a gear; the Inspector opens itself.
    assert 'const PANEL_TITLE = "Mobile testing";' in js
    assert "Recording on my computer through the Mobile testing panel." in js
    for marker in ("data-recording-flow", "data-recording-settings", 'data-recording-action="settings"', "efp-menu", "openInspectorTabEarly", "data-recording-workspace", "efp-workspace-frame", "data-recording-auto-inspector"):
        assert marker in js, marker
    # The Library lists what the workspace keeps and deletes through the
    # runtime's workspace API, telling the assistant so the plan follows.
    for marker in ("data-recording-tabs", "data-recording-library", 'data-recording-action="library-replay"', 'data-recording-action="library-view"', "library-delete-segment", "library-delete-recording", "library-delete-replay", 'workspaceApi("/delete")', "JSON.stringify({ paths })", "${kind} ${name} deleted: ${path}", "efp-lib-pre", "cards.replayCardHtml(cards.normalizeReplay("):
        assert marker in js, marker
    # Compiled segments replay on the held device; the result lands in the
    # workspace beside the recordings and the chat message hands it over.
    assert '"segment.replay"' in js and '"replay.status"' in js and '"replay.stop"' in js
    assert 'const REPLAYS_DIR = "mobile/replays";' in js and "Replay finished: " in js
    assert '<input type="password" class="portal-form-input" data-replay-secret' in js
    # The Mobile testing panel has the same proxy fields, the password masked.
    assert '<input type="password" class="portal-form-input" data-bridge-proxy="password"' in js
    # Segment names are optional: a member records the whole scenario.
    assert "Segment names (optional)" in js and 'segment: view.planned[0] || "recording-1"' in js
    assert "params.appium_version = String(defaults.appium_version)" in js
    # The panel starts the bridge for its /mobile routes without the EFP
    # browser window; the pre-opened tab animates and follows the start.
    assert "&browser=off" in js and "window.portalConnectors.launchBridge" not in js
    for marker in ("INSPECTOR_TAB_HTML", 'id="efp-status"', "@keyframes efp-fill", "updateInspectorTab(rec.progress"):
        assert marker in js, marker
    assert "Everything you do through the Inspector is recorded; there is nothing to start there." in js
    # The chat composer's "Local bridge offline" keeps up: the panel tells it
    # when the bridge appears, and the composer looks again while offline.
    assert "window.portalConnectors.refreshToggle()" in js
    bridge_js = Path("app/static/js/connectors/local_bridge.js").read_text(encoding="utf-8")
    for marker in ("const OFFLINE_RECHECK_MS = 15000", "scheduleOfflineRecheck();", "async function refreshToggle()", "refreshToggle,"):
        assert marker in bridge_js, marker
    addon = Path("app/static/js/inspector_addon.js").read_text(encoding="utf-8")
    for marker in ("tabler-icon-object-scan", "tabler-icon-crosshair", "ant-btn-primary", "efp.inspector.hint.dismissed", "own recorder is hidden"):
        assert marker in addon, marker
    # The Inspector's own recorder is hidden: the bridge records everything.
    assert '#btnStartRecording,#btnPause,.ant-tabs-tab[data-node-key="recorder"]{display:none !important}' in addon
    assert addon.isascii(), "code files keep non-ASCII text as escapes"
    # After an action the Inspector refreshes once; when that source shows the
    # app loading, the add-on keeps refreshing until the loading is gone, and
    # leaves a screen without such signs alone.
    for marker in ('getElementById(REFRESH_BUTTON_ID)', 'const REFRESH_BUTTON_ID = "btnReload"', "window.fetch = function", "const LOADING_MAX_MS = 60000", "XCUIElementTypeActivityIndicator", "function isLoading(source)", 'pathname.endsWith("/source")', "efp-inspector-loading"):
        assert marker in addon, marker
    # The polls fetch the source in the background; the Inspector's own
    # refresh, which blocks its screen, is pressed once, when the loading is over.
    assert 'nativeFetch(sourceUrl, { method: "GET"' in addon and "function pressRefresh()" in addon
    assert "SETTLE" not in addon


def test_recording_button_is_wired_into_the_tool_panel():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'id="btn-recording"' in html
    assert '<span class="portal-header-action-label">Mobile testing</span>' in html
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
        // A proxy saved the old way, its login inside the address.
        const store = { "efp.mobile.bridge_proxy": "http://CORP%5Calice:s3cret@proxy2:8080" };
        globalThis.localStorage = {
          getItem: (key) => (key in store ? store[key] : null),
          setItem: (key, value) => { store[key] = String(value); },
          removeItem: (key) => { delete store[key]; },
        };
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
            if (body.command === "session.status" && body.params.id === "r-up") {
              // Starting on the first ask, active on the second: the bridge answers session.start at once.
              const asks = calls.filter((c) => c.options.body && c.options.body.indexOf('"r-up"') >= 0).length;
              const data = asks < 2
                ? { id: "r-up", status: "starting", progress: "device up; holding it for the Inspector" }
                : { id: "r-up", status: "active", session_id: "sess-1", segment: "seg-login" };
              return { ok: true, status: 200, json: async () => ({ ok: true, data }) };
            }
            if (body.command === "session.status" && body.params.id === "r-down") {
              return { ok: true, status: 200, json: async () => ({ ok: true, data: { id: "r-down", status: "failed", error: { code: "local_tunnel_connection_failed", message: "BrowserStack refused the session", hint: "Read the tunnel log." } } }) };
            }
            return { ok: false, status: 404, json: async () => ({ ok: false, error: { code: "not_found", message: "recording not found", hint: "Start a new recording from the Mobile testing panel." } }) };
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
        assert.equal(M.savedMessage("seg-login", "mobile/recordings/seg-login.wdlog.json", ["seg-login"]), "Segment seg-login recorded: mobile/recordings/seg-login.wdlog.json");
        assert.equal(M.savedMessage("recording-1", "mobile/recordings/recording-1.wdlog.json", []), "Recording recording-1 saved: mobile/recordings/recording-1.wdlog.json");
        // The login saved inside the address moved to its own fields.
        assert.deepEqual(store, { "efp.mobile.bridge_proxy": "http://proxy2:8080", "efp.mobile.bridge_proxy_user": "CORP\\\\alice", "efp.mobile.bridge_proxy_password": "s3cret" });
        assert.equal(M.proxyForBridge({ url: "http://proxy2:8080", username: "CORP\\\\alice", password: "p@ss:w/rd 50%" }), "http://CORP%5Calice:p%40ss%3Aw%2Frd%2050%25@proxy2:8080");
        assert.equal(M.proxyForBridge({ url: "http://proxy2:8080", username: "", password: "" }), "http://proxy2:8080");
        assert.equal(
          M.replayMessage({ status: "passed", segments: [{ name: "seg-login", status: "passed" }, { name: "seg-open", status: "passed" }] }, "mobile/replays/r1/report.json"),
          "Replay finished: 2 of 2 segments passed.\\nReport: mobile/replays/r1/report.json",
        );
        assert.equal(
          M.replayMessage({ status: "failed", segments: [{ name: "seg-login", status: "failed" }, { name: "seg-open", status: "not_run" }], failure: { segment: "seg-login", step: 3 } }, "p/report.json"),
          "Replay finished: 0 of 2 segments passed; seg-login failed at step 3.\\nReport: p/report.json",
        );
        assert.deepEqual(M.segmentSecrets("name: seg\\nsecrets:\\n    - MOBILE_SECRET_PASSWORD\\nsteps:\\n    - action: type\\n      text_env: MOBILE_SECRET_PIN\\n"), ["MOBILE_SECRET_PASSWORD", "MOBILE_SECRET_PIN"]);
        assert.deepEqual(M.segmentSecrets("secrets: [A_ONE, 'B_TWO']\\nsteps:\\n  - {action: type, text_env: C_THREE}\\n"), ["A_ONE", "B_TWO", "C_THREE"]);
        assert.deepEqual(M.segmentSecrets("name: seg\\nsteps:\\n  - action: tap\\n"), []);
        // The progress strip: nothing recorded yet, then a saved recording whose split waits in the chat.
        assert.deepEqual(M.flowSteps().map((s) => s.label + ":" + s.state), ["Record:current", "Split:todo", "Replay:todo", "Scripts:todo"]);
        assert.equal(M.proxyForBridge({ url: "", username: "alice", password: "x" }), "");
        assert.deepEqual(M.splitProxyLogin("http://CORP%5Calice:p%40ss@proxy2:8080"), { url: "http://proxy2:8080", username: "CORP\\\\alice", password: "p@ss", login: true });
        assert.deepEqual(M.splitProxyLogin("http://proxy2:8080"), { url: "http://proxy2:8080", username: "", password: "", login: false });
        assert.equal(M.suggestCustomId("FXApp-1.4.2-uat.apk"), "fxapp-uat-android");
        assert.equal(M.suggestCustomId("notes.txt"), "");
        // The Library's messages and what it reads out of a segment file.
        assert.equal(M.deletedMessage("Segment", "seg-login", "mobile/segments/android/seg-login.yaml"), "Segment seg-login deleted: mobile/segments/android/seg-login.yaml");
        assert.equal(M.deletedMessage("Recording", "recording-1", "mobile/recordings/recording-1.wdlog.json"), "Recording recording-1 deleted: mobile/recordings/recording-1.wdlog.json");
        assert.deepEqual(
          M.parseSegmentYaml("name: seg-login\\nplatform: android\\nsource:\\n  kind: webdriver-log\\n  grade: fair\\n  needs_review: 2\\n  compiled_at: '2026-10-01T10:15:00Z'\\nsteps:\\n  - action: tap\\n    name: Login\\n  - action: type\\n"),
          { name: "seg-login", platform: "android", steps: 2, grade: "fair", needsReview: 2, compiledAt: "2026-10-01T10:15:00Z" },
        );
        assert.deepEqual(M.parseSegmentYaml("steps: []\\n"), { name: "", platform: "", steps: 0, grade: "", needsReview: 0, compiledAt: "" });
        (async () => {
          const state = await M.probeBridge({ force: true });
          assert.deepEqual(state, { alive: true, port: 8766, version: "1.2.3", mobile: true, mobileAuto: true });
          const plan = await M.callBridge("plan", {}, { credentials: { username: "alice", access_key: "k" } });
          assert.equal(plan.username, "alice");
          const sent = JSON.parse(calls.find((c) => c.url.endsWith("/mobile/run")).options.body);
          // The bridge still gets one address, the login percent-encoded in it.
          assert.deepEqual(sent, { command: "plan", params: {}, credentials: { username: "alice", access_key: "k" }, proxy: "http://CORP%5Calice:s3cret@proxy2:8080" });
          await assert.rejects(M.callBridge("session.status", { id: "x" }), (err) => err.code === "not_found" && /recording not found/.test(err.message));
          const up = await M.waitForDevice("r-up", { pollMs: 1 });
          assert.equal(up.status, "active");
          assert.equal(up.session_id, "sess-1");
          assert.equal(calls.filter((c) => c.options.body && c.options.body.indexOf('"r-up"') >= 0).length, 2);
          await assert.rejects(M.waitForDevice("r-down", { pollMs: 1 }), (err) => err.code === "local_tunnel_connection_failed" && /refused the session/.test(err.message) && /tunnel log/.test(err.hint));
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
