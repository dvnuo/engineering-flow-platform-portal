"""Node harness: how the chat page finds the local bridge and which browser
window an assistant drives.

The page asks the port that answered last before it sweeps the range, shares
one probe between the Portal's tabs through localStorage, waits longer and
longer between looks while the bridge is offline, and names a bridge session
per assistant once the member gave that assistant an own window. These tests
drive those functions with stubbed pings, storage, and timers.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


BRIDGE_SRC = Path("app/static/js/connectors/local_bridge.js")

BRIDGE_FUNCTIONS = [
    "storedPort",
    "rememberPort",
    "candidatePorts",
    "readSharedProbe",
    "writeSharedProbe",
    "probeView",
    "applyPing",
    "mobileState",
    "sessionAliveFor",
    "probeLocalBridge",
    "pingBridge",
    "scheduleOfflineRecheck",
    "resetOfflineBackoff",
    "windowKey",
    "windowChoice",
    "setWindowChoice",
    "bridgeSessionName",
    "localBridgeStartUrl",
    "windowStartUrl",
    "launchUrl",
    "waitForBridge",
    "runLocalBridge",
]


def _node_bin() -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping local bridge probe test")
    return node_bin


def _run(script: str) -> None:
    result = subprocess.run([_node_bin(), "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ok" in result.stdout


# The constants and collaborators the extracted functions expect, with pings,
# storage, and timers recorded instead of performed.
HARNESS = r"""
const assert = require("node:assert/strict");
const PORT_RANGE = [8765, 8766, 8767, 8768, 8769, 8770];
const PING_TIMEOUT_MS = 1500;
const PROBE_CACHE_MS = 10000;
const OFFLINE_RECHECK_MS = 15000;
const OFFLINE_RECHECK_MAX_MS = 120000;
const RUN_TIMEOUT_MS = 65000;
const RUN_TIMEOUT_GRACE_MS = 5000;
const DEFAULT_SESSION = "default";
const LOCAL_BRIDGE_TYPE = "local_bridge";
const PORT_KEY = "efp.connectors.local_bridge.port";
const PROBE_KEY = "efp.connectors.local_bridge.probe";
const WINDOW_KEY_PREFIX = "efp.connectors.local_bridge.window:";
const WINDOW_SHARED = "shared";
const WINDOW_OWN = "own";
let offlineRecheck = null;
let offlineRecheckDelay = OFFLINE_RECHECK_MS;
let offlineRecheckPending = false;
const store = {};
globalThis.localStorage = {
  getItem: (key) => (key in store ? store[key] : null),
  setItem: (key, value) => { store[key] = String(value); },
  removeItem: (key) => { delete store[key]; },
};
const timers = [];
globalThis.window = {
  setTimeout: (fn, ms) => { timers.push(ms); return timers.length; },
  clearTimeout() {},
  location: { pathname: "/app" },
};
globalThis.document = { visibilityState: "visible" };
const state = { localBridge: { port: 0, alive: false, sessionName: DEFAULT_SESSION, sessionAlive: false, sessions: {}, sessionsKnown: false, lastPing: null, version: "", probedAt: 0, probing: null } };
const config = { preferred_port: 8765, auto_enable_in_new_chats: true };
function localBridgeConfig() { return config; }
let startUrlSetting = "";
function connectorEntry() { return { settings: { start_url: startUrlSetting } }; }
function portalOrigin() { return "https://portal.example.test"; }
function toggleElements() { return null; }
function renderToggle() {}
// Which ports answer /ping, and every ping the page made, in order.
let alivePorts = {};
const pings = [];
async function pingPort(port) { pings.push(port); return alivePorts[port] || null; }
// Every /run body the page sent.
const runCalls = [];
async function fetchWithTimeout(url, options) {
  runCalls.push(JSON.parse(options.body));
  return { ok: true, status: 200, json: async () => ({ ok: true, data: { tabs: [] } }) };
}
"""


def _script(body: str) -> str:
    src = BRIDGE_SRC.read_text(encoding="utf-8")
    return HARNESS + "\n".join(_extract_js_function(src, name) for name in BRIDGE_FUNCTIONS) + textwrap.dedent(body)


def test_the_page_asks_the_known_port_first_and_shares_the_answer_between_tabs():
    _run(_script(r"""
        (async () => {
          // The preferred port answers: one request, the port remembered for
          // the next page load, the answer shared with the other tabs.
          const newBridge = { version: "1.4.0", capabilities: ["browser", "sessions"], session: { name: "default", alive: true, tab_count: 2 }, sessions: [{ name: "default", alive: true, tab_count: 2 }, { name: "agent-A", alive: false, tab_count: 0 }] };
          alivePorts = { 8765: newBridge };
          let probe = await probeLocalBridge({ force: true });
          assert.deepEqual(pings, [8765]);
          assert.equal(probe.alive, true);
          assert.equal(probe.port, 8765);
          assert.equal(probe.sessionAlive, true);
          assert.equal(store[PORT_KEY], "8765");
          const shared = JSON.parse(store[PROBE_KEY]);
          assert.equal(shared.port, 8765);
          assert.equal(shared.data.version, "1.4.0");
          // The sessions list says which windows are open; a bridge that lists
          // them knows an unlisted one is not.
          assert.equal(sessionAliveFor("default"), true);
          assert.equal(sessionAliveFor("agent-A"), false);
          assert.equal(sessionAliveFor("agent-B"), false);

          // Nothing on the preferred port: the rest of the range is swept
          // together, and the port found is asked first from then on.
          pings.length = 0;
          const oldBridge = { version: "1.3.0", capabilities: ["browser"], session: { name: "default", alive: false, tab_count: 0 } };
          alivePorts = { 8767: oldBridge };
          probe = await probeLocalBridge({ force: true });
          assert.deepEqual(pings, [8765, 8766, 8767, 8768, 8769, 8770]);
          assert.equal(probe.port, 8767);
          assert.equal(sessionAliveFor("default"), false);
          assert.equal(sessionAliveFor("agent-A"), null, "an older bridge cannot say whether an own window is open");
          pings.length = 0;
          probe = await probeLocalBridge({ force: true });
          assert.deepEqual(pings, [8767]);

          // Nothing answers: six requests, the last known port first; a
          // request in flight asks two ports only. The empty answer is shared.
          pings.length = 0;
          alivePorts = {};
          probe = await probeLocalBridge({ force: true });
          assert.equal(probe.alive, false);
          assert.equal(pings.length, 6);
          assert.equal(pings[0], 8767);
          pings.length = 0;
          await probeLocalBridge({ force: true, quick: true });
          assert.deepEqual(pings, [8767, 8765]);
          assert.equal(JSON.parse(store[PROBE_KEY]).data, null);

          // Another tab's fresh answer serves this one without a request; a
          // stale one does not.
          pings.length = 0;
          state.localBridge.probedAt = 0;
          store[PROBE_KEY] = JSON.stringify({ at: Date.now() - 2000, port: 8766, data: { version: "1.4.0", capabilities: ["browser", "sessions"], session: { name: "default", alive: true, tab_count: 1 }, sessions: [{ name: "default", alive: true, tab_count: 1 }] } });
          probe = await probeLocalBridge();
          assert.deepEqual(pings, []);
          assert.equal(probe.port, 8766);
          assert.equal(probe.alive, true);
          state.localBridge.probedAt = 0;
          store[PROBE_KEY] = JSON.stringify({ at: Date.now() - PROBE_CACHE_MS - 1, port: 8766, data: null });
          probe = await probeLocalBridge();
          assert.ok(pings.length > 0, "a stale record is not trusted");

          // The Mobile testing panel gets the raw answer through pingBridge.
          alivePorts = { 8766: { version: "1.4.0", capabilities: ["browser", "mobile"], mobile: { available: true } } };
          const raw = await pingBridge({ force: true });
          assert.equal(raw.port, 8766);
          assert.equal(raw.data.mobile.available, true);
          console.log("ok");
        })().catch((error) => { console.error(error); process.exit(1); });
        """))


def test_offline_looks_back_off_and_the_launch_wait_asks_the_expected_port():
    _run(_script(r"""
        (async () => {
          // 15 s, 30 s, 60 s, then 120 s at most; anything the member does
          // starts over at 15 s.
          const delays = [scheduleOfflineRecheck(), scheduleOfflineRecheck(), scheduleOfflineRecheck(), scheduleOfflineRecheck(), scheduleOfflineRecheck()];
          assert.deepEqual(delays, [15000, 30000, 60000, 120000, 120000]);
          assert.deepEqual(timers, delays);
          resetOfflineBackoff();
          assert.equal(scheduleOfflineRecheck(), 15000);

          // After the protocol link the expected port is asked alone; the
          // full sweep every fourth look finds a bridge that moved to the
          // next port because the expected one was busy.
          alivePorts = { 8766: { version: "1.4.0", capabilities: ["browser"], session: { name: "default", alive: true, tab_count: 1 } } };
          const probe = await waitForBridge(3000, 8765);
          assert.equal(probe.port, 8766);
          assert.deepEqual(pings.slice(0, 3), [8765, 8765, 8765]);
          assert.ok(pings.slice(3).includes(8766));
          console.log("ok");
        })().catch((error) => { console.error(error); process.exit(1); });
        """))


def test_an_assistant_drives_the_shared_window_unless_given_its_own():
    _run(_script(r"""
        (async () => {
          assert.equal(windowChoice("A"), "shared");
          assert.equal(bridgeSessionName("A"), "default");
          assert.equal(bridgeSessionName(""), "default");
          setWindowChoice("A", "own");
          assert.equal(windowChoice("A"), "own");
          assert.equal(bridgeSessionName("A"), "agent-A");
          // Session names allow letters, digits, dot, underscore, and hyphen.
          setWindowChoice("b c/d", "own");
          assert.equal(bridgeSessionName("b c/d"), "agent-b-c-d");
          setWindowChoice("A", "shared");
          assert.equal(windowChoice("A"), "shared");
          assert.equal(store[WINDOW_KEY_PREFIX + "A"], undefined);

          // The launch link asks the bridge for no shared window when the
          // page opens an own one itself right after.
          assert.equal(launchUrl(8765).includes("browser=off"), false);
          assert.ok(launchUrl(8765, { noBrowser: true }).endsWith("&browser=off"));
          // An own window opens on the Portal page of its assistant, unless
          // the administrator configured a start page.
          assert.equal(windowStartUrl("A", false), "");
          assert.equal(windowStartUrl("A", true), "https://portal.example.test/app#/assistants/A");
          startUrlSetting = "/welcome";
          assert.equal(windowStartUrl("A", true), "https://portal.example.test/welcome");
          startUrlSetting = "";

          // A command runs in the named session; none named means the
          // shared window.
          state.localBridge.alive = true;
          state.localBridge.port = 8766;
          await runLocalBridge("tab.list", {}, 20, "agent-A");
          await runLocalBridge("tab.list", {}, 20);
          assert.deepEqual(runCalls.map((call) => call.session), ["agent-A", "default"]);
          console.log("ok");
        })().catch((error) => { console.error(error); process.exit(1); });
        """))
