"""Node harness: how the chat page finds the local bridge and which browser
window an assistant drives.

The page asks the port that answered last before it sweeps the range, shares
one probe between the Portal's tabs through localStorage, waits longer and
longer between looks while the bridge is offline, and names a bridge session
per assistant once the member gave that assistant an own window. These tests
drive those functions with stubbed pings, storage, and timers; the last one
drives the window panel behind the Browser pill's arrow against a small fake
DOM.
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
    "forgetSharedProbe",
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
// Timers are recorded, never run: `timers` holds each wait scheduled,
// `cleared` each id cleared.
const timers = [];
const cleared = [];
globalThis.window = {
  setTimeout: (fn, ms) => { timers.push(ms); return timers.length; },
  clearTimeout: (id) => { if (id) cleared.push(id); },
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
// Every /run body the page sent; runFails makes the bridge unreachable.
const runCalls = [];
let runFails = false;
async function fetchWithTimeout(url, options) {
  if (runFails) throw new TypeError("Failed to fetch");
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

          // Nothing answers: six requests, the last known port first, and the
          // empty answer is shared. A request in flight asks two ports only,
          // and its miss is kept to itself: the other tabs may still trust
          // a sweep.
          pings.length = 0;
          alivePorts = {};
          probe = await probeLocalBridge({ force: true });
          assert.equal(probe.alive, false);
          assert.equal(pings.length, 6);
          assert.equal(pings[0], 8767);
          assert.equal(JSON.parse(store[PROBE_KEY]).data, null);
          store[PROBE_KEY] = JSON.stringify({ at: Date.now(), port: 8767, data: oldBridge });
          pings.length = 0;
          await probeLocalBridge({ force: true, quick: true });
          assert.deepEqual(pings, [8767, 8765]);
          assert.equal(JSON.parse(store[PROBE_KEY]).port, 8767, "a quick miss is not published");

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
          // 15 s, 30 s, 60 s, then 120 s at most, one look at a time: a
          // redraw while a look is pending schedules nothing more, and
          // anything the member does cancels the pending look and starts
          // over at 15 s.
          assert.equal(scheduleOfflineRecheck(), 15000);
          assert.equal(scheduleOfflineRecheck(), 0, "a pending look is kept as it is");
          assert.equal(timers.length, 1);
          const delays = [15000];
          for (const expected of [30000, 60000, 120000, 120000]) {
            offlineRecheck = null; // the pending look fired
            delays.push(scheduleOfflineRecheck());
          }
          assert.deepEqual(delays, [15000, 30000, 60000, 120000, 120000]);
          assert.deepEqual(timers, delays);
          const pendingId = offlineRecheck;
          resetOfflineBackoff();
          assert.equal(offlineRecheck, null);
          assert.ok(cleared.includes(pendingId), "the pending look is cancelled by a reset");
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

          // A command the bridge does not answer makes every tab ask again:
          // the shared record goes, and this tab no longer trusts its state.
          store[PROBE_KEY] = JSON.stringify({ at: Date.now(), port: 8766, data: { version: "1.4.0" } });
          runFails = true;
          const failed = await runLocalBridge("tab.list", {}, 20);
          assert.equal(failed.ok, false);
          assert.equal(failed.error.code, "bridge_unreachable");
          assert.equal(store[PROBE_KEY], undefined);
          assert.equal(state.localBridge.alive, false);
          assert.equal(state.localBridge.probedAt, 0);
          console.log("ok");
        })().catch((error) => { console.error(error); process.exit(1); });
        """))


PANEL_FUNCTIONS = [
    "toggleElements",
    "applyToggleView",
    "popoverAllowed",
    "windowPopoverOpen",
    "openWindowPopover",
    "closeWindowPopover",
    "toggleWindowPopover",
    "bindWindowPopover",
]


# A composer in miniature: the wrap holding the pill's two buttons and the
# panel with its radios, inside the chat form. Each node keeps classes,
# attributes, and listeners; an event fired on a node bubbles to its parents
# and then to the document, as in a browser.
PANEL_HARNESS = r"""
const assert = require("node:assert/strict");
const TOGGLE_ID = "composer-browser-toggle";
const TOGGLE_TEXT_ID = "composer-browser-toggle-text";
const WRAP_ID = "composer-browser-wrap";
const MENU_ID = "composer-browser-menu";
const POPOVER_ID = "composer-browser-popover";
const byId = {};
const documentListeners = {};
globalThis.document = {
  visibilityState: "visible",
  activeElement: null,
  getElementById: (id) => byId[id] || null,
  addEventListener: (type, fn) => { (documentListeners[type] = documentListeners[type] || []).push(fn); },
  handle: (type, event) => { (documentListeners[type] || []).forEach((fn) => fn(event)); },
};
function node(id, parent, { radio = "" } = {}) {
  const listeners = {};
  const classes = new Set();
  const self = {
    id, parent, attrs: {}, dataset: {}, title: "", textContent: "", checked: false, value: radio,
    classList: {
      add: (name) => { classes.add(name); },
      remove: (name) => { classes.delete(name); },
      contains: (name) => classes.has(name),
      toggle: (name, force) => { const on = force === undefined ? !classes.has(name) : Boolean(force); if (on) classes.add(name); else classes.delete(name); return on; },
    },
    setAttribute: (name, value) => { self.attrs[name] = String(value); },
    getAttribute: (name) => (name in self.attrs ? self.attrs[name] : null),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    handle: (type, event) => { (listeners[type] || []).forEach((fn) => fn(event)); },
    contains: (other) => { for (let n = other; n; n = n.parent) if (n === self) return true; return false; },
    focus: () => { document.activeElement = self; },
    closest: (selector) => (radio && selector.startsWith('input[name="composer-browser-window"]') ? self : null),
    querySelector: () => null,
    querySelectorAll: () => [],
    fire: (type, props = {}) => {
      const event = { type, target: self, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...props };
      for (let n = self; n; n = n.parent) n.handle(type, event);
      document.handle(type, event);
      return event;
    },
  };
  byId[id] = self;
  return self;
}
const root = node("root", null);
const form = node("chat-form", root);
const elsewhere = node("elsewhere", root);
const wrap = node(WRAP_ID, form);
const button = node(TOGGLE_ID, wrap);
const text = node(TOGGLE_TEXT_ID, button);
const menu = node(MENU_ID, wrap);
const popover = node(POPOVER_ID, wrap);
const shared = node("window-shared", popover, { radio: "shared" });
const own = node("window-own", popover, { radio: "own" });
const openButton = node("window-open", popover);
shared.checked = true;
popover.classList.add("hidden");
popover.querySelector = (selector) => (selector.endsWith(":checked") ? [shared, own].find((input) => input.checked) || null : null);
button.dataset.state = "on";
const rendered = [];
function renderWindowPopover(agentId) { rendered.push(agentId); }
const choices = [];
function onWindowChoice(choice) { choices.push(choice); }
function openAssistantWindow() {}
function currentAgentId() { return "A"; }
"""


def _panel_script(body: str) -> str:
    src = BRIDGE_SRC.read_text(encoding="utf-8")
    return PANEL_HARNESS + "\n".join(_extract_js_function(src, name) for name in PANEL_FUNCTIONS) + textwrap.dedent(body)


def test_the_window_panel_opens_by_click_and_closes_on_escape_or_a_click_elsewhere():
    _run(_panel_script(r"""
        bindWindowPopover();
        assert.equal(wrap.dataset.popoverBound, "1");
        // Hover and focus open nothing: the pill has no such listeners.
        wrap.fire("mouseenter");
        menu.fire("focusin");
        assert.equal(windowPopoverOpen(), false);

        // A click on the arrow opens the panel on the current assistant's
        // choice and leaves focus where it is; the next click closes it.
        let event = menu.fire("click", { detail: 1 });
        assert.ok(event.defaultPrevented);
        assert.equal(windowPopoverOpen(), true);
        assert.equal(menu.getAttribute("aria-expanded"), "true");
        assert.deepEqual(rendered, ["A"]);
        assert.equal(document.activeElement, null);
        menu.fire("click", { detail: 1 });
        assert.equal(windowPopoverOpen(), false);
        assert.equal(menu.getAttribute("aria-expanded"), "false");

        // Enter or Space on the arrow (a click with detail 0) hands focus to
        // the chosen radio, so the arrow keys move the choice.
        menu.fire("click", { detail: 0 });
        assert.equal(windowPopoverOpen(), true);
        assert.equal(document.activeElement, shared);

        // A mousedown inside the panel leaves it open; one anywhere else
        // closes it.
        own.fire("mousedown");
        assert.equal(windowPopoverOpen(), true);
        elsewhere.fire("mousedown");
        assert.equal(windowPopoverOpen(), false);

        // Escape closes an open panel and puts focus back on the arrow; with
        // the panel closed, Escape is not the pill's to handle.
        menu.fire("click", { detail: 0 });
        event = own.fire("keydown", { key: "Escape" });
        assert.ok(event.defaultPrevented);
        assert.equal(windowPopoverOpen(), false);
        assert.equal(document.activeElement, menu);
        event = menu.fire("keydown", { key: "Escape" });
        assert.equal(event.defaultPrevented, false);

        // Enter on a radio picks that window instead of submitting the chat
        // form around the panel; Enter on the panel's Open window button is
        // left to the button, which activates on it.
        menu.fire("click", { detail: 0 });
        event = own.fire("keydown", { key: "Enter" });
        assert.ok(event.defaultPrevented);
        assert.equal(own.checked, true);
        assert.deepEqual(choices, ["own"]);
        event = openButton.fire("keydown", { key: "Enter" });
        assert.equal(event.defaultPrevented, false);
        assert.deepEqual(choices, ["own"]);

        // Focus moving within the pill keeps the panel; focus leaving it
        // closes the panel.
        own.fire("focusout", { relatedTarget: menu });
        assert.equal(windowPopoverOpen(), true);
        own.fire("focusout", { relatedTarget: elsewhere });
        assert.equal(windowPopoverOpen(), false);

        // The arrow is the pill's other half once there is a window to talk
        // about (on, off, or offline); before the connector is set up the
        // pill is a single button and the arrow opens nothing.
        applyToggleView({ mode: "on", label: "Browser on" });
        assert.equal(menu.classList.contains("hidden"), false);
        assert.equal(wrap.classList.contains("has-menu"), true);
        assert.equal(menu.dataset.state, "on");
        menu.fire("click", { detail: 1 });
        assert.equal(windowPopoverOpen(), true);
        applyToggleView({ mode: "setup", label: "Set up browser" });
        assert.equal(menu.classList.contains("hidden"), true);
        assert.equal(wrap.classList.contains("has-menu"), false);
        assert.equal(windowPopoverOpen(), false);
        menu.fire("click", { detail: 1 });
        assert.equal(windowPopoverOpen(), false);
        applyToggleView({ mode: "offline", label: "Local bridge offline" });
        assert.equal(menu.classList.contains("hidden"), false);
        console.log("ok");
        """))
