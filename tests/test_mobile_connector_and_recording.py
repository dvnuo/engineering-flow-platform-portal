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
        # The test also says whether BrowserStack Local is on this computer.
        assert "data-mobile-local-binary" in html
        js = MOBILE_JS.read_text(encoding="utf-8")
        assert 'renderLocalBinary(form.querySelector("[data-mobile-local-binary]"), plan.local_binary)' in js
        assert "download it for this computer" in js and "status.download_url" in js
        # The proxy for this computer is set on the connector page too, with
        # the login a corporate proxy asks for in fields of its own: the
        # password in a password input, never in the address.
        assert 'data-bridge-proxy="url"' in html and 'data-bridge-proxy="username"' in html
        assert '<input type="password" class="portal-form-input" data-bridge-proxy="password"' in html
        assert "user:password@" not in html
        # Saved into the member's Local bridge connector, not this browser.
        assert "data-bridge-proxy-save" in html and "Saved with your Local bridge connector" in html
        # The test runs through the proxy as typed, saved or not.
        assert 'readProxyFields(document.querySelector("[data-mobile-overview]"));' in js
        assert "Kept in this browser only" not in html
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
    # No proxy set for this computer: the bridge uses the computer's own.
    assert body["bridge_proxy"] == {"url": "", "username": "", "password": ""}


def test_recording_config_carries_the_bridges_proxy_to_browserstack(api):
    from app.repositories.user_connector_repo import UserConnectorRepository

    # The proxy lives in the member's Local bridge connector, so it follows
    # the member and is never admin-managed; the panel hands it to the bridge
    # with the credentials.
    UserConnectorRepository(api.db).upsert(
        api.owner.id, "local_bridge", enabled=True,
        config_json=json.dumps({"preferred_port": 8765, "mobile_proxy": {"url": "http://proxy.example.com:3128", "username": "CORP\\alice", "password": "s3cret"}}),
    )
    api.db.commit()
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["bridge_proxy"] == {"url": "http://proxy.example.com:3128", "username": "CORP\\alice", "password": "s3cret"}


def test_recording_config_falls_back_to_the_deployments_network(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "mobile_default_network", "private-managed")
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["defaults"]["network"] == "private-managed"
    api.profile.config_json = json.dumps({"mobile-auto": {"enabled": True, "defaults": {"network_mode": "public"}, "browserstack": {"username": "u", "access_key": "k"}}})
    api.db.commit()
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["defaults"]["network"] == "public", "a member's own choice wins"


def test_recording_config_reads_the_administrators_network_while_the_connector_follows_the_default(api):
    from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

    RuntimeProfileSeedService(api.db).save_seed(
        {"mobile-auto": {"enabled": True, "defaults": {"network_mode": "private-managed"}, "browserstack": {"username": "shared", "access_key": "k"}}}
    )
    api.profile.connector_modes_json = "{}"  # every connector follows the system default
    api.db.commit()
    body = api.client.get(f"/api/mobile/recording-config?agent_id={api.agent.id}").json()
    assert body["defaults"]["network"] == "private-managed"
    assert body["credentials"]["username"] == "shared"


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
    # Recordings land where the record-mobile-segment skill looks for them.
    # A recording saved under a scenario's name is compiled into its script
    # here, through the bridge, and the assistant is asked to review it; any
    # other recording goes to the assistant by message.
    assert 'const RECORDINGS_DIR = "mobile/recordings";' in js and 'const SCRIPTS_DIR = "mobile/scripts";' in js
    assert '"script.compile"' in js and "Script ${name} compiled: ${path}\\nReport: ${reportPath}" in js and "${fileName}.report.json" in js
    # The review is a step of its own: a Review button on the saved line, on
    # the Library's script row, and under a replay that passed; saving sends
    # nothing, and nothing sends the review by itself.
    for marker in ('data-recording-action="review-script"', 'data-recording-action="replay-script"', "library-review", 'data-replay-action="review"'):
        assert marker in js, marker
    assert "AUTO_REVIEW_KEY" not in js and "data-recording-auto-review" not in js and "autoReview()" not in js
    # A replay is set up, followed and read in a dialog opened on the file:
    # where to start, the secret values, the bridge's phases with a link to
    # watch it live, then the outcome. The panel has no replay section.
    for marker in ("data-recording-dialog-host", "data-recording-dialog", 'data-replay-action="show"', 'data-replay-action="close"', 'data-replay-action="again"', "Watch on BrowserStack", "REPLAY_PHASES", "data-replay-chain"):
        assert marker in js, marker
    # A replay sends the file as it is now (the hash is the status), Show and
    # Replay again know the file the replay ran, and the done line names the
    # script the compiler wrote.
    assert "r.yaml = {};\n    await loadReplaySecrets();" in js.replace("\r\n", "\n") and "r.lastItem = Object.assign({}, d.item);" in js
    assert "done.scriptName = compiled.name;" in js and 'data-recording-action="replay-script" data-name="${esc(scriptName)}" data-path="${esc(item.scriptPath)}"' in js
    assert "data-recording-replay" not in js and "data-replay-segment" not in js
    # Finishing asks first and shows the release; the status line sits under the strip.
    assert "Finish recording?" in js and "releasingCardHtml" in js and "Releasing…" in js
    assert js.index('data-recording-status role="status"') < js.index("data-recording-record></section>")
    assert js.count("sendChat(compiledMessage(") == 1
    # A recording is just a recording: it replays as it is (the bridge
    # compiles it on the way), and is compiled as a scenario the member picks
    # in the Library; only the cut of several scenarios asks the assistant.
    for marker in ('"recording.replay"', "library-replay-recording", "library-compile-as", "library-cut", "data-library-scenario", 'data-recording-action="compile-recording"', 'data-recording-action="replay-recording"', "file: `${name}.wdlog.json`"):
        assert marker in js, marker
    assert js.count("sendChat(savedMessage(") == 2, "the cut, and the fallback when this computer cannot compile"
    assert "Script ${name} recorded: ${path}" in js and "Recording ${name} saved: ${path}" in js
    # Finishing and deleting say nothing: the plan's statuses come from the files.
    assert "Recording finished." not in js and "deleted: ${path}" not in js
    assert "script_sha256" in js and 'data-recording-action="ask-assistant"' in js and "data-recording-next" in js
    assert 'Scripts: ${view.planned.join(", ")}' in js
    # Code from the Inspector's recorder keeps no screens: an older segment.
    assert "Segment ${name} recorded: ${path}" in js
    # The panel is Mobile testing: the device on top, a progress strip, then
    # the step at hand; the Inspector opens itself. It has no settings of its
    # own: the configuration lives in Connectors, and the build upload sits
    # in the start form with the build it adds to.
    assert 'const PANEL_TITLE = "Mobile testing";' in js
    assert "Recording on my computer through the Mobile testing panel." in js
    for marker in ("data-recording-flow", "efp-menu", "openInspectorTabEarly", "data-recording-workspace", "efp-workspace-frame", 'data-recording-action="upload-toggle"', 'class="efp-upload${view.uploadOpen ? "" : " hidden"}" data-recording-upload'):
        assert marker in js, marker
    for gone in ("data-recording-settings", 'data-recording-action="settings"', "settings-upload", "data-recording-auto-inspector", "AUTO_INSPECTOR_KEY", "efp.mobile.auto_"):
        assert gone not in js, gone
    assert js.index('data-recording-action="upload-toggle"') < js.index('data-recording-action="start"><i data-lucide="play"')
    # The device area is the same on both tabs: the Library never hides the
    # start form, and a replay asked for without a device points at it.
    assert "No device held" not in js and "under Session" not in js and js.count("askForDevice(") == 4
    # The tabs name what the member does there: Record, or the Library.
    assert 'tab("record", "Record") + tab("library", "Library")' in js and '"session"' not in js
    # A collapsible reads as something to open, in the panel and on the
    # connector pages alike: the base style draws the chevron.
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert ".portal-collapsible-summary::after" in css and ".portal-collapsible[open] > .portal-collapsible-summary::after" in css
    assert ".portal-collapsible-summary:has(> i, > svg)::after" in css
    # The Library lists what the workspace keeps and deletes through the
    # runtime's workspace API; a script's report goes with it.
    for marker in ("data-recording-tabs", "data-recording-library", 'data-recording-action="library-replay"', 'data-recording-action="library-view"', "library-delete-script", "library-delete-segment", "library-delete-recording", "library-delete-replay", 'workspaceApi("/delete")', "JSON.stringify({ paths })", "row.reportPath", "efp-lib-pre", "cards.replayCardHtml(cards.normalizeReplay(", 'data-recording-action="library-copy"', "library-copy-send", "Copy script ${name} as a variant: "):
        assert marker in js, marker
    # A scenario script (or older segments) replays on the held device; the
    # result lands in the workspace beside the recordings and the chat
    # message hands it over.
    assert '"segment.replay"' in js and '"replay.status"' in js and '"replay.stop"' in js
    assert 'const REPLAYS_DIR = "mobile/replays";' in js and "Replay finished: " in js
    assert '<input type="password" class="portal-form-input" data-replay-secret' in js
    # The proxy to BrowserStack from this computer is set on the connector
    # page alone and saved in the Local bridge connector; the panel gets it
    # with its recording settings, never from this browser's storage.
    assert 'data-bridge-proxy="password"' not in js and "efp.mobile.bridge_proxy" not in js
    assert "setBridgeProxy(view.config.bridge_proxy)" in js and "config.mobile_proxy = { url: typed.url, username, password }" in js
    assert "data-bridge-proxy-save" in js and "/api/connectors/${LOCAL_BRIDGE_CONNECTOR}" in js
    # Script names are optional: a member can name each recording when saving.
    assert "Script names (optional)" in js and 'segment: view.planned[0] || "recording-1"' in js
    assert "params.appium_version = String(defaults.appium_version)" in js
    # The panel starts the bridge for its /mobile routes without the EFP
    # browser window; the pre-opened tab animates and follows the start.
    assert "&browser=off" in js and "window.portalConnectors.launchBridge" not in js
    for marker in ("INSPECTOR_TAB_HTML", 'id="efp-status"', "@keyframes efp-fill", "updateInspectorTab(rec.progress"):
        assert marker in js, marker
    assert "Everything you do through the Inspector is recorded; there is nothing to start there." in js
    # A build upload: the copy to the bridge first, then, with a bridge that
    # answers at once (upload_status on /ping), the send to BrowserStack
    # followed with apps.upload.status and a Cancel upload button that asks
    # apps.upload.cancel; an older bridge holds the request, and the wait says
    # how long it has been. The upload sits in the view, so a re-render draws
    # the area as it is.
    for marker in ('"apps.upload.status"', '"apps.upload.cancel"', "info.mobile.upload_status", 'data-recording-action="upload-cancel"', "Copying ${file.name} to the local bridge\\u2026", "Sending ${file.name} on to BrowserStack\\u2026 (${elapsedLabel(since)})", "BrowserStack is checking the build\\u2026", "upload: null,", "view.upload.fraction", "const UPLOAD_POLL_MS = 1500;"):
        assert marker in js, marker
    # A lost status ask (a slow answer, a moment's "Failed to fetch") keeps
    # the last progress on the line; only several in a row end the follow.
    assert "const UPLOAD_STATUS_RETRIES = 3;" in js and "(waiting for the local bridge\\u2026)" in js
    assert "Sending ${file.name} on to BrowserStack\u2026`" not in js, "the wait names its length"
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
    assert 'nativeFetch(sourceUrl, { method: "GET"' in addon and "function pressRefresh(attempt)" in addon
    # A spinner's source rarely changes while it turns: an unchanged source
    # slows the polls down but never ends the watch; the time limit does, and
    # Refresh is pressed at the end either way.
    assert "LOADING_STUCK" not in addon and "const LOADING_SLOW_MS = 4000" in addon
    assert "SETTLE" not in addon


def test_the_connector_page_links_the_browserstack_local_downloads():
    html = Path("app/templates/partials/connectors/browserstack_extra.html").read_text(encoding="utf-8")
    # BrowserStack's own downloads (there is no darwin-arm64 build: the x64
    # one runs on Apple silicon), put next to the bridge in ~/bin or on PATH.
    assert "https://www.browserstack.com/browserstack-local/BrowserStackLocal-win32.zip" in html
    assert "https://www.browserstack.com/browserstack-local/BrowserStackLocal-darwin-x64.zip" in html
    assert "<code>~/bin</code>" in html and "BrowserStackLocal.exe" in html
    help_text = Path("app/help/connect-mobile.md").read_text(encoding="utf-8")
    assert "### BrowserStack Local" in help_text and "BrowserStackLocal-win32.zip" in help_text


def test_recording_button_is_wired_into_the_tool_panel():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'id="btn-recording"' in html
    assert '<span class="portal-header-action-label">Mobile testing</span>' in html
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert '"recording",' in js[js.index("const ALLOWED_UTILITY_PANEL_KEYS"): js.index("]);", js.index("const ALLOWED_UTILITY_PANEL_KEYS"))]
    assert "window.EfpMobileTesting.openRecordingPanel" in js
    # The button shows only for the assistant's owner while their BrowserStack
    # connector is on; the chat asks the connectors list and follows changes.
    assert 'data-owner-only="true"' in html[html.index('id="btn-recording"'): html.index("</button>", html.index('id="btn-recording"'))]
    assert 'btn.id !== "btn-recording" || state.browserstackEnabled === true' in js
    assert "async function refreshBrowserstackEnabled()" in js and 'item.type === "browserstack"' in js
    assert js.count("refreshBrowserstackEnabled();") >= 3


def test_recording_panel_helpers_and_bridge_calls_in_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = MOBILE_JS.read_text(encoding="utf-8")
    shim = textwrap.dedent(
        """
        globalThis.window = globalThis;
        const store = {};
        globalThis.localStorage = {
          getItem: (key) => (key in store ? store[key] : null),
          setItem: (key, value) => { store[key] = String(value); },
          removeItem: (key) => { delete store[key]; },
        };
        globalThis.document = { readyState: "complete", addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; } };
        const calls = [];
        // What apps.upload.status answers per upload id, in order; the last
        // answer repeats. apps.upload.cancel turns an upload cancelled.
        const uploadScripts = {
          "u-ok": [
            { id: "u-ok", status: "sending", sent: 0, total: 300, file_name: "fx.ipa" },
            { id: "u-ok", status: "sending", sent: 150, total: 300, file_name: "fx.ipa" },
            { id: "u-ok", status: "processing", sent: 300, total: 300, file_name: "fx.ipa" },
            { id: "u-ok", status: "done", sent: 300, total: 300, file_name: "fx.ipa", app: { app_url: "bs://new1", custom_id: "fx-ios", platform: "ios" } },
          ],
          "u-bad": [
            { id: "u-bad", status: "sending", sent: 10, total: 300 },
            { id: "u-bad", status: "failed", sent: 10, total: 300, error: { code: "network_error", message: "The upload stalled: no data reached BrowserStack for 2 minutes.", hint: "Check this computer's network and its proxy to BrowserStack, then upload the build again." } },
          ],
          "u-slow": [{ id: "u-slow", status: "sending", sent: 50, total: 300 }],
          // One ask lost to the network, one to the 20 s limit, between answers.
          "u-blip": [
            { fail: "fetch" },
            { id: "u-blip", status: "sending", sent: 100, total: 300 },
            { fail: "abort" },
            { id: "u-blip", status: "done", sent: 300, total: 300, app: { app_url: "bs://blip" } },
          ],
          "u-gone": [{ fail: "fetch" }],
        };
        const cancelled = [];
        // The page copies a build to the bridge with XMLHttpRequest, for its
        // progress events: one scripted answer per request.
        const xhrs = [];
        const xhrAnswers = [];
        globalThis.XMLHttpRequest = class {
          constructor() {
            this.upload = {};
            this.headers = {};
            this.status = 0;
            this.responseText = "";
            this.aborted = false;
            xhrs.push(this);
          }
          open(method, url) {
            this.method = method;
            this.url = url;
          }
          setRequestHeader(name, value) {
            this.headers[name] = value;
          }
          send(body) {
            this.body = body;
            const answer = xhrAnswers.shift() || { status: 500, payload: { ok: false, error: { code: "bridge_error", message: "no answer scripted" } } };
            setTimeout(() => {
              if (this.aborted) return;
              if (this.upload.onprogress) {
                this.upload.onprogress({ lengthComputable: true, loaded: body.size / 2, total: body.size });
                this.upload.onprogress({ lengthComputable: true, loaded: body.size, total: body.size });
              }
              if (answer.hold) return;
              this.status = answer.status;
              this.responseText = JSON.stringify(answer.payload);
              this.onload();
            }, 1);
          }
          abort() {
            this.aborted = true;
            if (this.onabort) this.onabort();
          }
        };
        globalThis.fetch = async (url, options = {}) => {
          calls.push({ url, options });
          if (url.endsWith("/ping")) {
            if (!url.startsWith("http://127.0.0.1:8766/")) throw new TypeError("Failed to fetch");
            return { ok: true, status: 200, json: async () => ({ ok: true, data: { version: "1.2.3", capabilities: ["browser", "mobile"], mobile: { available: true, upload_status: true } } }) };
          }
          if (url === "http://127.0.0.1:8766/mobile/run") {
            const body = JSON.parse(options.body);
            if (body.command === "plan") {
              return { ok: true, status: 200, json: async () => ({ ok: true, data: { username: body.credentials.username, parallel_sessions_running: 1, parallel_sessions_max_allowed: 5 } }) };
            }
            if (body.command === "apps.upload.status") {
              const script = uploadScripts[body.params.id] || [];
              const upload = script.length > 1 ? script.shift() : script[0];
              if (!upload) return { ok: false, status: 404, json: async () => ({ ok: false, error: { code: "not_found", message: "upload not found", hint: "Upload the build again." } }) };
              if (upload.fail === "fetch") throw new TypeError("Failed to fetch");
              if (upload.fail === "abort") {
                const aborted = new Error("The operation was aborted.");
                aborted.name = "AbortError";
                throw aborted;
              }
              return { ok: true, status: 200, json: async () => ({ ok: true, data: { upload } }) };
            }
            if (body.command === "apps.upload.cancel") {
              cancelled.push(body.params.id);
              uploadScripts[body.params.id] = [{ id: body.params.id, status: "cancelled", sent: 50, total: 300 }];
              return { ok: true, status: 200, json: async () => ({ ok: true, data: { upload: uploadScripts[body.params.id][0] } }) };
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
        assert.equal(M.savedMessage("FX-12-buy-100-usd", "mobile/recordings/FX-12-buy-100-usd.wdlog.json", ["FX-12-buy-100-usd"]), "Script FX-12-buy-100-usd recorded: mobile/recordings/FX-12-buy-100-usd.wdlog.json");
        assert.equal(M.savedMessage("recording-1", "mobile/recordings/recording-1.wdlog.json", []), "Recording recording-1 saved: mobile/recordings/recording-1.wdlog.json");
        // The proxy comes with the recording settings and reaches the bridge
        // as one address with the login in it; nothing is kept in this browser.
        M.setBridgeProxy({ url: " http://proxy2:8080 ", username: "CORP\\\\alice", password: "s3cret" });
        assert.deepEqual(M.proxySettings(), { url: "http://proxy2:8080", username: "CORP\\\\alice", password: "s3cret" });
        assert.equal(M.proxyForBridge(), "http://CORP%5Calice:s3cret@proxy2:8080");
        assert.deepEqual(store, {});
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
        // A scenario script replays on its own, and the message says so.
        assert.equal(
          M.replayMessage({ status: "failed", segments: [{ name: "FX-12-buy-100-usd", kind: "script", status: "failed" }], failure: { segment: "FX-12-buy-100-usd", step: 14 } }, "p/report.json"),
          "Replay finished: 0 of 1 script passed; FX-12-buy-100-usd failed at step 14.\\nReport: p/report.json",
        );
        // A replay's phases, as the bridge's progress text names them.
        assert.equal(M.replayPhaseIndex("starting"), 0);
        assert.equal(M.replayPhaseIndex("taking the device back from the Inspector"), 0);
        assert.equal(M.replayPhaseIndex("restarting the app"), 1);
        assert.equal(M.replayPhaseIndex("replaying"), 2);
        assert.equal(M.replayPhaseIndex("FX-12-buy-100-usd: step 3 of 10 (tap)"), 2);
        assert.equal(M.replayPhaseIndex("handing the device back to the Inspector"), 3);
        // A recording replays on its own too.
        assert.equal(M.replayNoun([{ name: "recording-3", kind: "recording" }]), "recording");
        // A replay's result marks the row of its kind; a report without kinds marks by name.
        assert.equal(M.sameReplayRow({ name: "FX-12-x", kind: "script" }, { name: "FX-12-x", kind: "segment" }), false);
        assert.equal(M.sameReplayRow({ name: "FX-12-x", kind: "script" }, { name: "FX-12-x", kind: "script" }), true);
        assert.equal(M.sameReplayRow({ name: "seg-a" }, { name: "seg-a", kind: "segment" }), true);
        // Where a recording stands: parts still to compile first, then what it became.
        assert.equal(M.recordingState({ split: { parts: ["FX-12-a", "FX-12-b"], compiled: ["FX-12-a"] }, compiledInto: ["FX-12-a"] }), "cut proposed, 1 of 2 parts not compiled");
        assert.equal(M.recordingState({ split: null, compiledInto: ["seg-login"] }), "compiled into seg-login");
        assert.equal(M.recordingState({ split: { parts: ["FX-12-a"], compiled: ["FX-12-a"] }, compiledInto: [] }), "cut into 1 part");
        assert.equal(M.recordingState({ split: null, compiledInto: [] }), "not compiled yet");
        assert.equal(M.replayNoun([{ name: "x", kind: "script" }, { name: "y", kind: "script" }]), "scripts");
        assert.equal(M.replayNoun([{ name: "x", kind: "script" }, { name: "seg-y", kind: "segment" }]), "segments");
        assert.equal(M.copyMessage("FX-12-buy-100-usd", " EUR instead of USD,\\n 250 instead of 100 "), "Copy script FX-12-buy-100-usd as a variant: EUR instead of USD, 250 instead of 100");
        // From the Library the message names the file, and so the platform.
        assert.equal(M.copyMessage("FX-12-a", "EUR", "mobile/scripts/ios/FX-12-a.yaml"), "Copy script FX-12-a as a variant: EUR\\nScript: mobile/scripts/ios/FX-12-a.yaml");
        assert.deepEqual(M.segmentSecrets("name: seg\\nsecrets:\\n    - MOBILE_SECRET_PASSWORD\\nsteps:\\n    - action: type\\n      text_env: MOBILE_SECRET_PIN\\n"), ["MOBILE_SECRET_PASSWORD", "MOBILE_SECRET_PIN"]);
        assert.deepEqual(M.segmentSecrets("secrets: [A_ONE, 'B_TWO']\\nsteps:\\n  - {action: type, text_env: C_THREE}\\n"), ["A_ONE", "B_TWO", "C_THREE"]);
        assert.deepEqual(M.segmentSecrets("name: seg\\nsteps:\\n  - action: tap\\n"), []);
        // The progress strip: nothing recorded yet, then a saved recording whose split waits in the chat.
        assert.deepEqual(M.flowSteps().map((s) => s.label + ":" + s.state), ["Record:current", "Compile:todo", "Replay:todo", "Export:todo"]);
        assert.equal(M.proxyForBridge({ url: "", username: "alice", password: "x" }), "");
        assert.deepEqual(M.splitProxyLogin("http://CORP%5Calice:p%40ss@proxy2:8080"), { url: "http://proxy2:8080", username: "CORP\\\\alice", password: "p@ss", login: true });
        assert.deepEqual(M.splitProxyLogin("http://proxy2:8080"), { url: "http://proxy2:8080", username: "", password: "", login: false });
        assert.equal(M.suggestCustomId("FXApp-1.4.2-uat.apk"), "fxapp-uat-android");
        assert.equal(M.suggestCustomId("notes.txt"), "");
        // What Test BrowserStack says about BrowserStack Local on this computer.
        assert.equal(M.localBinaryHtml(undefined), null);
        const haveLocal = M.localBinaryHtml({ found: true, path: "/Users/alice/bin/BrowserStackLocal", source: "beside_program" });
        assert.equal(haveLocal.tone, "success");
        assert.equal(haveLocal.html, "BrowserStack Local is on this computer (/Users/alice/bin/BrowserStackLocal).");
        const noLocal = M.localBinaryHtml({ found: false, name: "BrowserStackLocal", folder: "/Users/alice/bin", download_url: "https://www.browserstack.com/browserstack-local/BrowserStackLocal-darwin-x64.zip" });
        assert.equal(noLocal.tone, "warning");
        assert.ok(noLocal.html.startsWith("BrowserStack Local is not on this computer."));
        assert.ok(noLocal.html.includes('href="https://www.browserstack.com/browserstack-local/BrowserStackLocal-darwin-x64.zip"'));
        assert.ok(noLocal.html.includes("<code>BrowserStackLocal</code> in <code>/Users/alice/bin</code>"));
        assert.ok(M.localBinaryHtml({ found: false }).html.includes("download it from BrowserStack"));
        // A compiled script goes to the assistant for review, with its report.
        assert.equal(M.compiledMessage("FX-12-buy-100-usd", "mobile/scripts/android/FX-12-buy-100-usd.yaml", "mobile/scripts/android/FX-12-buy-100-usd.report.json"), "Script FX-12-buy-100-usd compiled: mobile/scripts/android/FX-12-buy-100-usd.yaml\\nReport: mobile/scripts/android/FX-12-buy-100-usd.report.json");
        // Which scenario a recording is: the plan's script of that name, else the name's own <KEY>-<id>.
        const plan = M.planView({ __key: "FX-12", issue: "FX-12", scripts: [{ name: "FX-12-buy-100-usd", scenario: "buy-100-usd" }, { name: "FX-12-buy-250-eur", scenario: "buy-250-eur", copy_of: "FX-12-buy-100-usd" }],
          scenarios: [{ id: "buy-100-usd", title: "Buy 100 USD", given: ["the customer signs in"], when: ["the customer buys 100 USD"], then: ["the confirmation shows 100.00 USD"] }] });
        assert.deepEqual(M.scriptScenario("FX-12-buy-100-usd", plan), { issue: "FX-12", scenario: "buy-100-usd", title: "Buy 100 USD" });
        assert.deepEqual(M.scriptScenario("FX-13-sell-usd", plan), { issue: "FX-13", scenario: "sell-usd", title: "" });
        assert.equal(M.scriptScenario("recording-1", plan), null);
        assert.deepEqual(plan.scripts.map((s) => s.name + ":" + s.copyOf), ["FX-12-buy-100-usd:", "FX-12-buy-250-eur:FX-12-buy-100-usd"]);
        // A script's status from the files: the latest replay of the file as it is now.
        const replays = [
          { platform: "android", finishedAt: "2026-10-09T12:00:00Z", segments: [{ name: "FX-12-a", kind: "script", status: "failed", sha256: "new" }] },
          { platform: "android", finishedAt: "2026-10-09T11:00:00Z", segments: [{ name: "FX-12-a", kind: "script", status: "passed", sha256: "old" }, { name: "FX-12-b", kind: "script", status: "passed", sha256: "b1" }] },
          { platform: "ios", finishedAt: "2026-10-09T10:00:00Z", segments: [{ name: "FX-12-b", kind: "script", status: "passed", sha256: "b1" }] },
        ];
        assert.equal(M.scriptStatus({ exists: false, hash: "" }, replays, "android", "FX-12-a"), "to_record");
        assert.equal(M.scriptStatus({ exists: true, hash: "new" }, replays, "android", "FX-12-a"), "exists");
        assert.equal(M.scriptStatus({ exists: true, hash: "old" }, replays, "android", "FX-12-a"), "replayed");
        assert.equal(M.scriptStatus({ exists: true, hash: "b1" }, replays, "android", "FX-12-b"), "replayed");
        assert.equal(M.scriptStatus({ exists: true, hash: "b2" }, replays, "android", "FX-12-b"), "exists");
        assert.equal(M.scriptStatus({ exists: true, hash: "" }, replays, "android", "FX-12-b"), "exists");
        // What it reads out of a segment file.
        assert.deepEqual(
          M.parseSegmentYaml("name: seg-login\\nplatform: android\\nsource:\\n  kind: webdriver-log\\n  grade: fair\\n  needs_review: 2\\n  compiled_at: '2026-10-01T10:15:00Z'\\nsteps:\\n  - action: tap\\n    name: Login\\n  - action: type\\n"),
          { name: "seg-login", platform: "android", steps: 2, grade: "fair", needsReview: 2, compiledAt: "2026-10-01T10:15:00Z", issue: "", scenario: "", description: "", copiedFrom: "", sourceFile: "" },
        );
        assert.deepEqual(M.parseSegmentYaml("steps: []\\n"), { name: "", platform: "", steps: 0, grade: "", needsReview: 0, compiledAt: "", issue: "", scenario: "", description: "", copiedFrom: "", sourceFile: "" });
        // A scenario script names its issue and scenario, and where it came from.
        const script = M.parseSegmentYaml("name: FX-12-buy-250-eur\\nissue: FX-12\\nscenario: buy-250-eur\\ndescription: Buy 250 EUR within the daily limit\\nplatform: android\\nsource:\\n    kind: copy\\n    copied_from: FX-12-buy-100-usd\\nsteps:\\n    - action: tap\\n      gherkin: Given the customer signs in\\n");
        assert.equal(script.scenario, "buy-250-eur");
        assert.equal(script.issue, "FX-12");
        assert.equal(script.description, "Buy 250 EUR within the daily limit");
        assert.equal(script.copiedFrom, "FX-12-buy-100-usd");
        assert.equal(M.parseSegmentYaml("name: FX-12-a\\nsource:\\n    kind: webdriver-log\\n    file: FX-12-a.wdlog.json\\nsteps: []\\n").sourceFile, "FX-12-a.wdlog.json");
        // The status line of a build on its way to BrowserStack.
        const MB = 1024 * 1024;
        assert.equal(M.formatBytes(512), "512 B");
        assert.equal(M.formatBytes(734 * 1024), "734 KB");
        assert.equal(M.formatBytes(1.2 * MB), "1.2 MB");
        assert.equal(M.formatBytes(87 * MB), "87 MB");
        assert.equal(M.formatBytes(2.5 * 1024 * MB), "2.50 GB");
        assert.equal(M.uploadProgressText("fx.ipa", { status: "sending", sent: 87 * MB, total: 200 * MB }, 1.2 * MB), "Sending fx.ipa to BrowserStack: 87 MB of 200 MB (1.2 MB/s, about 2 min left)");
        assert.equal(M.uploadProgressText("fx.ipa", { status: "sending", sent: 199 * MB, total: 200 * MB }, 1.2 * MB), "Sending fx.ipa to BrowserStack: 199 MB of 200 MB (1.2 MB/s, under a minute left)");
        assert.equal(M.uploadProgressText("fx.ipa", { status: "sending", sent: 0, total: 200 * MB }, 0), "Sending fx.ipa to BrowserStack: 0 B of 200 MB");
        assert.equal(M.uploadProgressText("fx.ipa", { status: "processing", sent: 10, total: 10 }, 5), "BrowserStack is checking the build\\u2026");
        // The rate over the last answers; a count that went back starts over.
        const meter = M.uploadMeter();
        meter.add(0, 1000);
        assert.equal(meter.rate(), 0);
        meter.add(1024, 2000);
        assert.equal(meter.rate(), 1024);
        meter.add(512, 3000);
        assert.equal(meter.rate(), 0);
        meter.add(1536, 4000);
        assert.equal(meter.rate(), 1024);
        assert.equal(M.elapsedLabel(Date.now() - 3 * 60 * 1000 - 5000), "3 min");
        assert.equal(M.elapsedLabel(Date.now() - 40 * 1000), "40 s");
        (async () => {
          const state = await M.probeBridge({ force: true });
          assert.deepEqual(state, { alive: true, port: 8766, version: "1.2.3", mobile: true, mobileAuto: true, uploadStatus: true });
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
          // A build goes in two legs: the copy to the bridge (the request
          // answers as soon as the bridge has the file), then the send to
          // BrowserStack, asked after until the build is there.
          const file = { name: "fx.ipa", size: 300 };
          const creds = { username: "alice", access_key: "k" };
          xhrAnswers.push({ status: 200, payload: { ok: true, data: { upload: { id: "u-ok", status: "sending", sent: 0, total: 300, file_name: "fx.ipa" } } } });
          const copied = [];
          const seen = [];
          let started = null;
          const app = await M.transferBuild(file, { credentials: creds, customId: "fx-ios", pollMs: 1, onCopy: (fraction) => copied.push(fraction), onStarted: (upload) => { started = upload; }, onSend: (upload, rate) => seen.push(`${upload.status}:${upload.sent}:${rate >= 0}`) });
          assert.deepEqual(app, { app_url: "bs://new1", custom_id: "fx-ios", platform: "ios" });
          assert.deepEqual(copied, [0.5, 1]);
          assert.equal(started.id, "u-ok");
          assert.deepEqual(seen, ["sending:0:true", "sending:150:true", "processing:300:true", "done:300:true"]);
          const copy = xhrs[xhrs.length - 1];
          assert.equal(copy.url, "http://127.0.0.1:8766/mobile/apps/upload");
          assert.equal(copy.headers["X-EFP-File-Name"], "fx.ipa");
          assert.equal(copy.headers["X-EFP-BS-User"], "alice");
          assert.equal(copy.headers["X-EFP-BS-Key"], "k");
          assert.equal(copy.headers["X-EFP-Custom-Id"], "fx-ios");
          assert.equal(copy.headers["X-EFP-Proxy"], "http://CORP%5Calice:s3cret@proxy2:8080");
          const asked = calls.filter((c) => c.options.body && c.options.body.indexOf('"apps.upload.status"') >= 0);
          assert.equal(asked.length, 4);
          assert.deepEqual(JSON.parse(asked[0].options.body).params, { id: "u-ok" });
          // A send that failed ends with the bridge's error, hint and all.
          xhrAnswers.push({ status: 200, payload: { ok: true, data: { upload: { id: "u-bad", status: "sending", sent: 0, total: 300 } } } });
          await assert.rejects(M.transferBuild(file, { credentials: creds, pollMs: 1 }), (err) => err.code === "network_error" && /The upload stalled/.test(err.message) && /proxy/.test(err.hint));
          // Cancel asks the bridge to give the send up; the next answer ends it.
          xhrAnswers.push({ status: 200, payload: { ok: true, data: { upload: { id: "u-slow", status: "sending", sent: 0, total: 300 } } } });
          const slow = {};
          await assert.rejects(M.transferBuild(file, { credentials: creds, controller: slow, pollMs: 1, onStarted: () => slow.cancel() }), (err) => err.code === "upload_cancelled");
          assert.deepEqual(cancelled, ["u-slow"]);
          // An older bridge answers the copy request itself with the app once
          // the build is on BrowserStack; nothing is asked after.
          const before = calls.length;
          xhrAnswers.push({ status: 200, payload: { ok: true, data: { app: { app_url: "bs://old", custom_id: "legacy" } } } });
          assert.deepEqual(await M.transferBuild(file, { credentials: creds, pollMs: 1 }), { app_url: "bs://old", custom_id: "legacy" });
          assert.equal(calls.length, before);
          // ...and a cancel there drops the request, which stops that bridge's send.
          xhrAnswers.push({ hold: true });
          const old = {};
          await assert.rejects(M.transferBuild(file, { credentials: creds, controller: old, onCopy: (fraction) => { if (fraction >= 1) old.cancel(); } }), (err) => err.code === "upload_cancelled");
          assert.equal(xhrs[xhrs.length - 1].aborted, true);
          assert.equal(calls.length, before);
          // A lost status ask does not end the follow: the bridge keeps
          // sending. The line keeps the last answer, the bridge is probed
          // again after a "Failed to fetch", and the next answer carries on.
          xhrAnswers.push({ status: 200, payload: { ok: true, data: { upload: { id: "u-blip", status: "sending", sent: 0, total: 300 } } } });
          const waits = [];
          const pings = calls.filter((c) => c.url.endsWith("/ping")).length;
          const blip = await M.transferBuild(file, { credentials: creds, pollMs: 1, onWaiting: (upload, lost) => waits.push(`${upload ? upload.sent : "none"}:${lost}`) });
          assert.deepEqual(blip, { app_url: "bs://blip" });
          assert.deepEqual(waits, ["none:1", "100:1"]);
          assert.ok(calls.filter((c) => c.url.endsWith("/ping")).length > pings, "the bridge is probed again after a failed fetch");
          // A bridge gone for several asks in a row ends it as unreachable.
          xhrAnswers.push({ status: 200, payload: { ok: true, data: { upload: { id: "u-gone", status: "sending", sent: 0, total: 300 } } } });
          const asksBefore = calls.filter((c) => c.options.body && c.options.body.indexOf('"u-gone"') >= 0).length;
          const lostCounts = [];
          await assert.rejects(M.transferBuild(file, { credentials: creds, pollMs: 1, onWaiting: (upload, lost) => lostCounts.push(lost) }), (err) => err.code === "bridge_unreachable" && /stopped answering while it was sending/.test(err.message) && /look for the build/.test(err.hint));
          assert.deepEqual(lostCounts, [1, 2, 3]);
          assert.equal(calls.filter((c) => c.options.body && c.options.body.indexOf('"u-gone"') >= 0).length - asksBefore, 4);
          assert.deepEqual(await M.probeBridge({ force: true }), { alive: true, port: 8766, version: "1.2.3", mobile: true, mobileAuto: true, uploadStatus: true });
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
