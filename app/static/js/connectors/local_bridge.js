/**
 * Connectors bridge: lets an assistant reach programs on the member's own PC.
 *
 * The runtime cannot open a connection to the member's machine, so the chat
 * page relays for it. When a run needs a connector it publishes a
 * `connector.request` event on the events socket; this bundle executes the
 * request against the local program and posts the outcome back to
 * `/a/{agent}/api/sessions/{session}/connectors/respond`. Which browser tab
 * answers is decided by `target_client_id`: the tab that sent the chat message
 * generated it (see `chatRequestConnectors`), so two Portal tabs on the same
 * session never execute the same request twice.
 *
 * The first (and for now only) connector type is `local_bridge`, shown as
 * Local bridge: the efp-bridge process on 127.0.0.1 that drives a dedicated
 * Chrome window and serves the mobile Mobile testing panel (mobile_testing.js).
 * Contract: docs/CONNECTORS_CONTRACT.md.
 */
(function () {
  "use strict";

  const PROTOCOL_VERSION = 1;
  const LOCAL_BRIDGE_TYPE = "local_bridge";
  const PORT_RANGE = [8765, 8766, 8767, 8768, 8769, 8770];
  const PING_TIMEOUT_MS = 1500;
  // A /run call is given the request's own timeout plus this grace, so a
  // playbook the runtime waits 120 s for is not cut off by the page first.
  const RUN_TIMEOUT_GRACE_MS = 5000;
  const RUN_TIMEOUT_MS = 65000;
  const STAGE_TIMEOUT_MS = 120000;
  const PROBE_CACHE_MS = 10000;
  // While the composer says the bridge is offline, it looks again: a bridge
  // started from the Mobile testing panel or a terminal, after the chat
  // loaded, is picked up without a reload. The first look comes after
  // OFFLINE_RECHECK_MS and each next one waits twice as long, up to the
  // maximum, so an idle tab with no bridge asks a few times an hour, not every
  // 15 s; anything the member does (the toggle, another assistant, the tab
  // coming back to the front) starts over at the short wait.
  const OFFLINE_RECHECK_MS = 15000;
  const OFFLINE_RECHECK_MAX_MS = 120000;
  let offlineRecheck = null;
  let offlineRecheckDelay = OFFLINE_RECHECK_MS;
  let offlineRecheckPending = false;
  // The bridge's own browser session: the EFP window every assistant shares
  // unless the member gives one its own window (bridgeSessionName).
  const DEFAULT_SESSION = "default";
  const CLIENT_ID_KEY = "efp.connectors.client_id";
  const TOGGLE_KEY_PREFIX = "efp.connectors.local_bridge.toggle:";
  // Per browser, not per tab: the port that answered last, the last probe's
  // answer (one tab's sweep serves every tab of this Portal), and the window
  // each assistant drives.
  const PORT_KEY = "efp.connectors.local_bridge.port";
  const PROBE_KEY = "efp.connectors.local_bridge.probe";
  const WINDOW_KEY_PREFIX = "efp.connectors.local_bridge.window:";
  const WINDOW_SHARED = "shared";
  const WINDOW_OWN = "own";
  const TOGGLE_ID = "composer-browser-toggle";
  const TOGGLE_TEXT_ID = "composer-browser-toggle-text";
  const WRAP_ID = "composer-browser-wrap";
  const MENU_ID = "composer-browser-menu";
  const POPOVER_ID = "composer-browser-popover";
  const PANEL_ROOT_ID = "connector-panel-root";

  const modules = new Map();
  const state = {
    connectors: null,
    connectorsLoadedAt: 0,
    connectorsLoading: null,
    featureEnabled: Boolean(document.getElementById("connectors-menu-btn")),
    handled: new Set(),
    localBridge: {
      port: 0,
      alive: false,
      // The bridge's own session as /ping names it, and every session its
      // computer knows (name -> {alive, tabCount}); sessionsKnown says the
      // bridge is new enough to list them.
      sessionName: DEFAULT_SESSION,
      sessionAlive: false,
      sessions: {},
      sessionsKnown: false,
      lastPing: null,
      version: "",
      probedAt: 0,
      probing: null,
    },
  };

  // ---- small helpers ------------------------------------------------------

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function currentAgentId() {
    return typeof window.currentPortalAgentId === "function" ? window.currentPortalAgentId() : null;
  }

  function currentSessionId() {
    return typeof window.currentPortalSessionId === "function" ? window.currentPortalSessionId() : null;
  }

  function isChromium() {
    const brands = navigator.userAgentData && Array.isArray(navigator.userAgentData.brands)
      ? navigator.userAgentData.brands
      : [];
    if (brands.length) {
      return brands.some((entry) => /chromium|google chrome|microsoft edge/i.test(String(entry.brand || "")));
    }
    return /Chrome\//.test(navigator.userAgent || "") && !/Firefox\//.test(navigator.userAgent || "");
  }

  function clientId() {
    try {
      let value = sessionStorage.getItem(CLIENT_ID_KEY);
      if (!value) {
        const random = (window.crypto && typeof window.crypto.randomUUID === "function")
          ? window.crypto.randomUUID().replace(/-/g, "").slice(0, 16)
          : Math.random().toString(36).slice(2, 18);
        value = `tab-${random}`;
        sessionStorage.setItem(CLIENT_ID_KEY, value);
      }
      return value;
    } catch (_error) {
      if (!state.fallbackClientId) state.fallbackClientId = `tab-${Math.random().toString(36).slice(2, 18)}`;
      return state.fallbackClientId;
    }
  }

  function portalOrigin() {
    return window.location.origin;
  }

  async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch (_error) {
      payload = null;
    }
    return { ok: response.ok, status: response.status, payload };
  }

  // ---- connector settings (Portal side) -----------------------------------

  async function loadConnectors({ force = false } = {}) {
    if (!state.featureEnabled) {
      state.connectors = [];
      return state.connectors;
    }
    if (!force && state.connectors && Date.now() - state.connectorsLoadedAt < 60000) return state.connectors;
    if (state.connectorsLoading) return state.connectorsLoading;
    state.connectorsLoading = (async () => {
      try {
        const { ok, status, payload } = await requestJson("/api/connectors", { method: "GET" });
        if (status === 404) {
          state.featureEnabled = false;
          state.connectors = [];
        } else if (ok && Array.isArray(payload)) {
          state.connectors = payload;
        } else if (ok && payload && Array.isArray(payload.connectors)) {
          state.connectors = payload.connectors;
        } else {
          state.connectors = state.connectors || [];
        }
        state.connectorsLoadedAt = Date.now();
      } catch (_error) {
        state.connectors = state.connectors || [];
      } finally {
        state.connectorsLoading = null;
      }
      return state.connectors;
    })();
    return state.connectorsLoading;
  }

  function connectorEntry(type) {
    const list = Array.isArray(state.connectors) ? state.connectors : [];
    return list.find((item) => item && item.type === type) || null;
  }

  function localBridgeEnabled() {
    const entry = connectorEntry(LOCAL_BRIDGE_TYPE);
    return Boolean(entry && entry.enabled);
  }

  function localBridgeConfig() {
    const entry = connectorEntry(LOCAL_BRIDGE_TYPE);
    const config = entry && entry.config && typeof entry.config === "object" ? entry.config : {};
    return {
      auto_enable_in_new_chats: config.auto_enable_in_new_chats !== false,
      preferred_port: Number(config.preferred_port) || PORT_RANGE[0],
    };
  }

  // ---- local bridge ------------------------------------------------------

  function storedPort() {
    try {
      const value = Number(localStorage.getItem(PORT_KEY));
      return Number.isInteger(value) && value > 0 ? value : 0;
    } catch (_error) {
      return 0;
    }
  }

  function rememberPort(port) {
    try {
      if (port) localStorage.setItem(PORT_KEY, String(port));
    } catch (_error) {
      /* storage unavailable */
    }
  }

  // Ports in the order they are worth asking: the one that answered last (to
  // this page, then to any page of this Portal in this browser), the member's
  // preferred port, then the rest of the range the bridge falls back to.
  function candidatePorts() {
    const known = [state.localBridge.port, storedPort(), localBridgeConfig().preferred_port];
    return [...known, ...PORT_RANGE].filter((port, index, list) => Number.isInteger(port) && port > 0 && list.indexOf(port) === index);
  }

  async function pingPort(port) {
    try {
      const response = await fetchWithTimeout(`http://127.0.0.1:${port}/ping`, { method: "GET", mode: "cors" }, PING_TIMEOUT_MS);
      if (!response.ok) return null;
      const payload = await response.json();
      if (!payload || payload.ok !== true) return null;
      return payload.data || {};
    } catch (_error) {
      return null;
    }
  }

  // The last probe, shared with the other tabs of this Portal through
  // localStorage: one tab's requests serve them all for PROBE_CACHE_MS, so a
  // member with several chats open does not have each tab ask six ports.
  function readSharedProbe() {
    try {
      const raw = localStorage.getItem(PROBE_KEY);
      if (!raw) return null;
      const record = JSON.parse(raw);
      if (!record || typeof record !== "object" || !Number.isFinite(record.at)) return null;
      const age = Date.now() - record.at;
      if (age < 0 || age >= PROBE_CACHE_MS) return null;
      return record;
    } catch (_error) {
      return null;
    }
  }

  function writeSharedProbe(port, data, at) {
    try {
      localStorage.setItem(PROBE_KEY, JSON.stringify({ at, port: port || 0, data: data || null }));
    } catch (_error) {
      /* storage unavailable */
    }
  }

  // The bridge stopped answering a command: the next probe, in every tab,
  // asks for real instead of trusting a record from moments before.
  function forgetSharedProbe() {
    try {
      localStorage.removeItem(PROBE_KEY);
    } catch (_error) {
      /* storage unavailable */
    }
  }

  function probeView() {
    const lb = state.localBridge;
    return { alive: lb.alive, port: lb.port, sessionAlive: lb.sessionAlive, version: lb.version, tabCount: lb.tabCount, mobile: lb.mobile };
  }

  // Reads one /ping answer (or none, when nothing listened) into the bridge
  // state. Whichever probe sees the bridge appear or go away redraws the
  // composer toggle, so a sweep started by another panel updates it too.
  function applyPing(port, data, at) {
    const lb = state.localBridge;
    const wasAlive = lb.alive;
    const found = Boolean(data && typeof data === "object");
    lb.probedAt = at || Date.now();
    lb.alive = found;
    lb.port = found ? port : 0;
    lb.lastPing = found ? data : null;
    const session = found && data.session && typeof data.session === "object" ? data.session : null;
    lb.sessionName = session && session.name ? String(session.name) : DEFAULT_SESSION;
    lb.sessionAlive = Boolean(session && session.alive);
    lb.tabCount = session ? Number(session.tab_count || 0) : 0;
    lb.version = found ? String(data.version || "") : "";
    lb.protocolVersion = found ? Number(data.protocol_version || 0) : 0;
    lb.mobile = mobileState(found ? data : null);
    // browser_files and workflow: the bridge stages workspace files for
    // page.upload and runs playbooks; an older bridge has neither and the
    // assistant is told so instead of the page trying. sessions: the bridge
    // lists every browser session its computer knows, which is how the page
    // tells whether an assistant's own window is open; an older bridge lists
    // none and the page says so instead of guessing.
    lb.capabilities = found && Array.isArray(data.capabilities) ? data.capabilities.slice() : [];
    lb.browserFiles = lb.capabilities.indexOf("browser_files") >= 0;
    lb.workflow = lb.capabilities.indexOf("workflow") >= 0;
    lb.sessionsKnown = lb.capabilities.indexOf("sessions") >= 0;
    lb.sessions = {};
    if (found && Array.isArray(data.sessions)) {
      data.sessions.forEach((entry) => {
        if (!entry || typeof entry !== "object" || !entry.name) return;
        lb.sessions[String(entry.name)] = { alive: Boolean(entry.alive), tabCount: Number(entry.tab_count || 0) };
      });
    }
    if (session && !lb.sessions[lb.sessionName]) lb.sessions[lb.sessionName] = { alive: lb.sessionAlive, tabCount: lb.tabCount };
    lb.maxFileBytes = found && data.browser && typeof data.browser === "object" ? Number(data.browser.max_file_bytes || 0) : 0;
    if (found) rememberPort(port);
    if (wasAlive !== found && typeof document !== "undefined" && toggleElements()) renderToggle({ probe: false });
  }

  // Whether a browser session (the shared window, or an assistant's own) is
  // open, from the last probe: true or false, or null when the bridge is too
  // old to list its sessions and the name is not its own.
  function sessionAliveFor(name) {
    const lb = state.localBridge;
    if (!lb.alive) return false;
    const wanted = String(name || "").trim() || DEFAULT_SESSION;
    const session = lb.sessions[wanted];
    if (session) return session.alive;
    if (wanted === lb.sessionName) return lb.sessionAlive;
    return lb.sessionsKnown ? false : null;
  }

  async function probeLocalBridge({ force = false, quick = false } = {}) {
    const lb = state.localBridge;
    if (!force && lb.probedAt && Date.now() - lb.probedAt < PROBE_CACHE_MS) return probeView();
    if (!force) {
      // Another tab of this Portal asked moments ago; its answer serves here.
      const shared = readSharedProbe();
      if (shared) {
        applyPing(shared.port, shared.data, shared.at);
        return probeView();
      }
    }
    if (lb.probing) return lb.probing;
    lb.probing = (async () => {
      let found = null;
      try {
        // The port that answered last (or the preferred one) is asked alone
        // first: while the bridge runs, the usual case, that is one request
        // and no failed one in the console. Only when it does not answer are
        // the other ports swept together (one by one would cost up to 9 s
        // when nothing listens). A request in flight cannot afford even that
        // sweep and asks the next candidate only.
        const ports = candidatePorts();
        const first = await pingPort(ports[0]);
        if (first) {
          found = { port: ports[0], data: first };
        } else {
          const rest = quick ? ports.slice(1, 2) : ports.slice(1);
          const results = await Promise.all(rest.map(async (port) => ({ port, data: await pingPort(port) })));
          found = results.find((entry) => entry.data) || null;
        }
      } finally {
        lb.probing = null;
      }
      const at = Date.now();
      applyPing(found ? found.port : 0, found ? found.data : null, at);
      // A quick look that found nothing asked two ports only; it is not the
      // sweep the other tabs may trust.
      if (found || !quick) writeSharedProbe(found ? found.port : 0, found ? found.data : null, at);
      return probeView();
    })();
    return lb.probing;
  }

  // For the Mobile testing panel: the bridge's last /ping answer, probing
  // first when the cached one is stale, so both panels share one probe and
  // one set of requests.
  async function pingBridge({ force = false } = {}) {
    await probeLocalBridge({ force });
    const lb = state.localBridge;
    return { port: lb.port, data: lb.alive ? lb.lastPing : null };
  }

  // What the bridge's /ping says about mobile recording: "ready", "outdated"
  // (a bridge without the mobile routes), "no_mobile_auto" (mobile-auto is not
  // next to it), or "" when no bridge answered.
  function mobileState(ping) {
    if (!ping) return "";
    const capabilities = Array.isArray(ping.capabilities) ? ping.capabilities : [];
    if (capabilities.indexOf("mobile") < 0) return "outdated";
    return ping.mobile && ping.mobile.available ? "ready" : "no_mobile_auto";
  }

  // Runs one bridge command in a browser session: the shared EFP window
  // (default) or an assistant's own window, named by bridgeSessionName.
  async function runLocalBridge(command, params, timeoutSeconds, session) {
    const lb = state.localBridge;
    if (!lb.alive || !lb.port) {
      const probe = await probeLocalBridge({ force: true, quick: true });
      if (!probe.alive) {
        return { ok: false, error: { code: "bridge_unreachable", message: "The local bridge is not running.", hint: "Open Connectors → Local bridge and start it." } };
      }
    }
    const seconds = Math.max(5, Math.min(120, Number(timeoutSeconds) || 30));
    const body = JSON.stringify({
      command,
      params: params && typeof params === "object" ? params : {},
      session: String(session || "").trim() || "default",
      timeout_seconds: seconds,
    });
    try {
      const response = await fetchWithTimeout(`http://127.0.0.1:${lb.port}/run`, {
        method: "POST",
        mode: "cors",
        headers: { "Content-Type": "application/json" },
        body,
      }, Math.max(RUN_TIMEOUT_MS, seconds * 1000 + RUN_TIMEOUT_GRACE_MS));
      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
      if (!payload || typeof payload !== "object") {
        return { ok: false, error: { code: "bridge_bad_response", message: `Bridge returned HTTP ${response.status} without a JSON body.` } };
      }
      if (payload.ok === true) {
        return { ok: true, result: payload.data !== undefined ? payload.data : payload };
      }
      const error = payload.error && typeof payload.error === "object" ? { ...payload.error } : { code: "bridge_error", message: `Bridge returned HTTP ${response.status}.` };
      // A failed playbook answers workflow_failed with the steps so far as
      // data; it travels with the error so the assistant sees which step.
      if (payload.data && typeof payload.data === "object" && error.data === undefined) error.data = payload.data;
      return { ok: false, error };
    } catch (error) {
      lb.alive = false;
      lb.probedAt = 0;
      forgetSharedProbe();
      const aborted = error && error.name === "AbortError";
      return {
        ok: false,
        error: {
          code: aborted ? "bridge_timeout" : "bridge_unreachable",
          message: aborted ? "The local bridge did not answer in time." : "Could not reach the local bridge.",
        },
      };
    }
  }

  // First tab of the EFP window (LOCAL_BRIDGE_BROWSER_START_URL on the server): an
  // absolute URL as configured, a path resolved against this Portal's origin,
  // or "" when the bridge should open the origin itself.
  function localBridgeStartUrl() {
    const entry = connectorEntry(LOCAL_BRIDGE_TYPE);
    const raw = String((entry && entry.settings && entry.settings.start_url) || "").trim();
    if (!raw) return "";
    if (/^https?:\/\//i.test(raw)) return raw;
    try {
      return new URL(raw, `${portalOrigin()}/`).toString();
    } catch (_error) {
      return "";
    }
  }

  // First tab of a window this page opens: the configured start page, or for
  // an assistant's own window the Portal on that assistant, so the member can
  // tell the windows apart and sign in to each.
  function windowStartUrl(agentId, own) {
    const configured = localBridgeStartUrl();
    if (configured || !own || !agentId) return configured;
    return `${portalOrigin()}${window.location.pathname}#/assistants/${encodeURIComponent(agentId)}`;
  }

  // noBrowser asks a bridge started by the link for no window (browser=off):
  // the page opens the assistant's own window itself right after, and the
  // shared one must not open as well.
  function launchUrl(port, { noBrowser = false } = {}) {
    const targetPort = port || localBridgeConfig().preferred_port || PORT_RANGE[0];
    let link = `efp-bridge://start?origin=${encodeURIComponent(portalOrigin())}&port=${encodeURIComponent(String(targetPort))}`;
    const startUrl = localBridgeStartUrl();
    if (startUrl) link += `&url=${encodeURIComponent(startUrl)}`;
    if (noBrowser) link += "&browser=off";
    return link;
  }

  function launchBridge(port, options) {
    // A registered protocol handler opens the launcher without leaving the
    // page; an unregistered one is simply ignored by Chrome (after an optional
    // "no app found" dialog). Either way the caller polls /ping afterwards.
    const anchor = document.createElement("a");
    anchor.href = launchUrl(port, options);
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(() => anchor.remove(), 0);
  }

  // After the protocol link: the bridge is expected on `port`, so that port
  // alone is asked every half second, with a full sweep every two seconds
  // for a bridge that landed on the next port because that one was busy.
  async function waitForBridge(timeoutMs = 6000, port = 0) {
    const startedAt = Date.now();
    let looks = 0;
    while (Date.now() - startedAt < timeoutMs) {
      if (port && looks % 4 !== 3) {
        const data = await pingPort(port);
        if (data) {
          const at = Date.now();
          applyPing(port, data, at);
          writeSharedProbe(port, data, at);
          return probeView();
        }
      } else {
        const probe = await probeLocalBridge({ force: true });
        if (probe.alive) return probe;
      }
      looks += 1;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return probeLocalBridge({ force: true });
  }

  // Makes the bridge reachable and the window the assistant drives open, in
  // that order: the shared EFP window, or the assistant's own when the member
  // picked one. The bridge outlives a window (closing Chrome leaves the
  // bridge running), so a reachable bridge whose window is closed, or that
  // has never opened this assistant's window, is asked for it through
  // session.ensure; the protocol link would only find the bridge already
  // running and do nothing. Resolves to the final probe, with `session`, its
  // `sessionAlive`, and `error` set when the window could not be opened.
  async function ensureBrowserSession({ port, launchTimeoutMs = 8000, agentId = "" } = {}) {
    // The launch link and the reopen carry the configured start page, which
    // arrives with the connector list.
    await loadConnectors();
    const session = bridgeSessionName(agentId);
    const own = session !== DEFAULT_SESSION;
    let probe = await probeLocalBridge({ force: true });
    if (!probe.alive) {
      const target = port || localBridgeConfig().preferred_port || PORT_RANGE[0];
      launchBridge(target, { noBrowser: own });
      probe = await waitForBridge(launchTimeoutMs, target);
      if (!probe.alive) return { ...probe, session, sessionAlive: false, error: { code: "bridge_unreachable" } };
    }
    if (sessionAliveFor(session) === true) return { ...probe, session, sessionAlive: true };
    // A bridge that has just started is still launching Chrome itself; the
    // call queues behind that and returns once the window is up.
    const startUrl = windowStartUrl(agentId, own);
    const outcome = await runLocalBridge("session.ensure", startUrl ? { url: startUrl } : {}, 60, session);
    // Chrome lists a transient extra target for a moment after it starts; let
    // it settle so the tab count shown next to the status is the real one.
    await new Promise((resolve) => setTimeout(resolve, 1500));
    probe = await probeLocalBridge({ force: true });
    // A bridge too old to list its sessions cannot say whether the own
    // window is open; the command's own answer stands in.
    const listed = sessionAliveFor(session);
    const sessionAlive = listed === null ? outcome.ok : listed;
    if (!outcome.ok) return { ...probe, session, sessionAlive, error: outcome.error || { code: "session_not_running" } };
    return { ...probe, session, sessionAlive };
  }

  // ---- workspace files for page.upload and playbooks -------------------------
  //
  // Chrome attaches only files on the member's computer, and the bridge holds
  // no Portal credentials, so this page is the carrier: it pulls each file the
  // runtime named through the Server files download (the request carries
  // paths, names, and sizes, never bytes), POSTs it to the bridge's staging
  // route, and runs the command with the staged ids in place of the paths.

  function workspaceFileUrl(agentId, path) {
    return `/a/${encodeURIComponent(agentId)}/api/server-files/download?paths=${encodeURIComponent(String(path))}`;
  }

  function bridgeFailure(code, message, hint) {
    const error = new Error(message);
    error.bridgeError = { code, message };
    if (hint) error.bridgeError.hint = hint;
    return error;
  }

  async function fetchWorkspaceFile(agentId, file) {
    let response;
    try {
      response = await fetchWithTimeout(workspaceFileUrl(agentId, file.path), { method: "GET", credentials: "same-origin" }, STAGE_TIMEOUT_MS);
    } catch (error) {
      throw bridgeFailure("file_fetch_failed", `Could not read ${file.path} from the workspace: ${error && error.name === "AbortError" ? "timed out" : String(error && error.message ? error.message : error)}.`);
    }
    if (!response.ok) {
      throw bridgeFailure("file_fetch_failed", `Could not read ${file.path} from the workspace (HTTP ${response.status}).`, "Check that the file still exists in the workspace.");
    }
    return response.blob();
  }

  async function stageFile(port, file, blob) {
    const form = new FormData();
    form.append("file", blob, String(file.name || "file"));
    let response;
    try {
      response = await fetchWithTimeout(`http://127.0.0.1:${port}/browser/files`, { method: "POST", mode: "cors", body: form }, STAGE_TIMEOUT_MS);
    } catch (error) {
      throw bridgeFailure(error && error.name === "AbortError" ? "bridge_timeout" : "bridge_unreachable", `The local bridge did not take ${file.name || file.path}.`);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    const staged = payload && payload.ok === true && payload.data && payload.data.file;
    if (!staged || !staged.id) {
      const error = payload && payload.error && typeof payload.error === "object" ? payload.error : {};
      throw bridgeFailure(error.code || "staging_failed", error.message || `Bridge returned HTTP ${response.status} while staging ${file.name || file.path}.`, error.hint);
    }
    return staged;
  }

  function withoutFiles(params) {
    const next = {};
    Object.keys(params || {}).forEach((key) => { if (key !== "files") next[key] = params[key]; });
    return next;
  }

  // Stages every file a page.upload or workflow.run request names and
  // returns the params to run: file_ids for page.upload, the staged ids
  // merged into vars for workflow.run. Resolves to {ok:false, error} when a
  // file cannot be fetched or staged, so nothing partial reaches the page.
  async function stageRequestFiles(agentId, action, params) {
    const files = Array.isArray(params.files) ? params.files : [];
    const lb = state.localBridge;
    if (!lb.alive || !lb.port) {
      const probe = await probeLocalBridge({ force: true, quick: true });
      if (!probe.alive) {
        return { ok: false, error: { code: "bridge_unreachable", message: "The local bridge is not running.", hint: "Open Connectors → Local bridge and start it." } };
      }
    }
    if ((files.length && !lb.browserFiles) || (action === "workflow.run" && !lb.workflow)) {
      return { ok: false, error: { code: "bridge_outdated", message: "The local bridge on this computer is too old to upload workspace files or run playbooks.", hint: "Ask the member to download the current bridge from Connectors → Local bridge, install it, and start it again." } };
    }
    const staged = [];
    for (const file of files) {
      if (!file || typeof file !== "object" || !file.path) {
        return { ok: false, error: { code: "invalid_args", message: "A file entry has no workspace path." } };
      }
      if (lb.maxFileBytes && Number(file.bytes) > lb.maxFileBytes) {
        return { ok: false, error: { code: "payload_too_large", message: `${file.name || file.path} is larger than the bridge accepts (${Math.round(lb.maxFileBytes / (1024 * 1024))} MB).` } };
      }
      try {
        const blob = await fetchWorkspaceFile(agentId, file);
        const entry = await stageFile(lb.port, file, blob);
        staged.push({ ...file, id: entry.id });
      } catch (error) {
        return { ok: false, error: error && error.bridgeError ? error.bridgeError : { code: "file_fetch_failed", message: String(error && error.message ? error.message : error) } };
      }
    }
    const next = withoutFiles(params);
    if (action === "page.upload") {
      next.file_ids = staged.map((file) => file.id);
    } else if (action === "workflow.run" && staged.length) {
      // Several files for one playbook variable become one comma-separated
      // value: the bridge splits it and attaches every staged file.
      next.vars = { ...(params.vars && typeof params.vars === "object" ? params.vars : {}) };
      const idsByVar = new Map();
      staged.forEach((file) => {
        if (!file.var) return;
        const key = String(file.var);
        idsByVar.set(key, [...(idsByVar.get(key) || []), file.id]);
      });
      idsByVar.forEach((ids, key) => { next.vars[key] = ids.join(","); });
    }
    return { ok: true, params: next, staged };
  }

  const localBridgeModule = {
    type: LOCAL_BRIDGE_TYPE,
    probe: probeLocalBridge,
    async execute(request, context) {
      const action = String(request.action || "");
      const params = request.params && typeof request.params === "object" ? request.params : {};
      const agentId = String((context && context.agentId) || currentAgentId() || "");
      // The window this assistant drives: the shared EFP window, or its own.
      const session = bridgeSessionName(agentId);
      if (action === "page.upload" || action === "workflow.run") {
        if (!agentId && Array.isArray(params.files) && params.files.length) {
          return { ok: false, error: { code: "file_fetch_failed", message: "No assistant is selected on this page, so its workspace files cannot be read." } };
        }
        const staged = await stageRequestFiles(agentId, action, params);
        if (!staged.ok) return staged;
        return runLocalBridge(action, staged.params, request.timeout_seconds, session);
      }
      return runLocalBridge(action, params, request.timeout_seconds, session);
    },
  };

  // ---- dispatcher -----------------------------------------------------------

  function register(module) {
    if (module && module.type) modules.set(String(module.type), module);
  }

  async function respond(agentId, sessionId, requestId, outcome) {
    const body = {
      request_id: requestId,
      client_id: clientId(),
      ok: outcome && outcome.ok === true,
    };
    if (body.ok) {
      body.result = outcome.result === undefined ? {} : outcome.result;
    } else {
      body.error = (outcome && outcome.error) || { code: "bridge_error", message: "Unknown bridge failure." };
    }
    try {
      const { ok, status } = await requestJson(
        `/a/${encodeURIComponent(agentId)}/api/sessions/${encodeURIComponent(sessionId)}/connectors/respond`,
        { method: "POST", body: JSON.stringify(body) }
      );
      if (!ok && status !== 409) {
        console.warn("[connectors] respond failed", status);
      }
    } catch (error) {
      console.warn("[connectors] respond failed", error);
    }
  }

  function eventData(detail) {
    const event = detail && detail.event ? detail.event : {};
    const data = event.data && typeof event.data === "object" ? event.data : event;
    return { event, data };
  }

  async function handleConnectorRequest(detail) {
    const { event, data } = eventData(detail);
    const request = data.connector_request || data.connectorRequest;
    if (!request || typeof request !== "object") return;
    const requestId = String(request.id || request.request_id || "");
    if (!requestId || state.handled.has(requestId)) return;
    const type = String(data.connector_type || request.connector_type || "");
    const module = modules.get(type);
    if (!module) return;
    const target = String(request.target_client_id || data.target_client_id || "");
    if (target && target !== clientId()) return;
    const sessionId = String(data.session_id || event.session_id || "");
    const agentId = String(detail.agentId || currentAgentId() || "");
    if (!sessionId || !agentId) return;
    state.handled.add(requestId);
    if (state.handled.size > 500) {
      const first = state.handled.values().next().value;
      state.handled.delete(first);
    }
    let outcome;
    try {
      outcome = await module.execute(request, { agentId, sessionId });
    } catch (error) {
      outcome = { ok: false, error: { code: "bridge_exception", message: String(error && error.message ? error.message : error) } };
    }
    await respond(agentId, sessionId, requestId, outcome);
  }

  // ---- composer toggle ------------------------------------------------------

  function toggleKey(agentId) {
    return `${TOGGLE_KEY_PREFIX}${agentId || "none"}`;
  }

  function toggleOn(agentId) {
    try {
      const stored = sessionStorage.getItem(toggleKey(agentId));
      if (stored === "on") return true;
      if (stored === "off") return false;
    } catch (_error) {
      /* storage unavailable */
    }
    return localBridgeConfig().auto_enable_in_new_chats;
  }

  function setToggle(agentId, on) {
    try {
      sessionStorage.setItem(toggleKey(agentId), on ? "on" : "off");
    } catch (_error) {
      /* storage unavailable */
    }
  }

  // ---- browser window per assistant ----------------------------------------
  //
  // An assistant drives the shared EFP window (the bridge's default session)
  // unless the member picks an own window for it in the panel behind the
  // arrow next to the composer's Browser pill: its commands then name the session
  // agent-<assistant id>, which the bridge creates on the first command as a
  // separate Chrome window and profile. Assistants with own windows work at
  // the same time without touching each other's tabs; each window is signed
  // in on its own. The choice is per assistant and per browser
  // (localStorage), since the windows live on this computer.

  function windowKey(agentId) {
    return `${WINDOW_KEY_PREFIX}${agentId || "none"}`;
  }

  function windowChoice(agentId) {
    if (!agentId) return WINDOW_SHARED;
    try {
      return localStorage.getItem(windowKey(agentId)) === WINDOW_OWN ? WINDOW_OWN : WINDOW_SHARED;
    } catch (_error) {
      return WINDOW_SHARED;
    }
  }

  function setWindowChoice(agentId, choice) {
    if (!agentId) return;
    try {
      if (choice === WINDOW_OWN) localStorage.setItem(windowKey(agentId), WINDOW_OWN);
      else localStorage.removeItem(windowKey(agentId));
    } catch (_error) {
      /* storage unavailable */
    }
  }

  // The bridge session an assistant's commands run in. A session name holds
  // letters, digits, dot, underscore, and hyphen; an assistant id is a UUID,
  // and anything else is made to fit.
  function bridgeSessionName(agentId) {
    if (!agentId || windowChoice(agentId) !== WINDOW_OWN) return DEFAULT_SESSION;
    const safe = String(agentId).replace(/[^A-Za-z0-9_.-]/g, "-");
    return `agent-${safe || "x"}`;
  }

  function toggleElements() {
    const button = document.getElementById(TOGGLE_ID);
    const text = document.getElementById(TOGGLE_TEXT_ID);
    const wrap = document.getElementById(WRAP_ID);
    const menu = document.getElementById(MENU_ID);
    const popover = document.getElementById(POPOVER_ID);
    return button ? { button, text, wrap, menu, popover } : null;
  }

  function applyToggleView(view) {
    const elements = toggleElements();
    if (!elements) return;
    const { button, text, wrap, menu } = elements;
    // The wrap holds the arrow and the panel too, so hiding it hides all three.
    (wrap || button).classList.toggle("hidden", view.mode === "hidden");
    button.dataset.state = view.mode;
    button.setAttribute("aria-pressed", view.mode === "on" ? "true" : "false");
    button.classList.toggle("is-active", view.mode === "on");
    button.classList.toggle("is-muted", view.mode === "offline" || view.mode === "setup");
    button.title = view.title || "";
    if (text) text.textContent = view.label || t("Browser");
    // The arrow is the pill's other half while there is a window to talk
    // about; until the connector is set up the pill is a single button.
    const allowed = popoverAllowed();
    if (menu) {
      menu.dataset.state = view.mode;
      menu.classList.toggle("hidden", !allowed);
    }
    if (wrap) wrap.classList.toggle("has-menu", Boolean(menu) && allowed);
    if (!allowed) closeWindowPopover();
  }

  async function renderToggle({ probe = true } = {}) {
    if (!toggleElements()) return;
    if (!state.featureEnabled || !isChromium()) {
      applyToggleView({ mode: "hidden" });
      return;
    }
    await loadConnectors();
    const agentId = currentAgentId();
    if (!agentId) {
      applyToggleView({ mode: "hidden" });
      return;
    }
    if (!localBridgeEnabled()) {
      applyToggleView({ mode: "setup", label: t("Set up browser"), title: t("Let the assistant use your local browser: open Connectors to set it up.") });
      return;
    }
    if (!toggleOn(agentId)) {
      applyToggleView({ mode: "off", label: t("Browser off"), title: t("The assistant will not use your local browser in this chat. Click to switch on.") });
      renderWindowPopover(agentId);
      return;
    }
    const status = probe ? await probeLocalBridge() : { alive: state.localBridge.alive };
    if (!status.alive) {
      applyToggleView({ mode: "offline", label: t("Local bridge offline"), title: t("The local bridge is not running. Click to open the setup steps.") });
      renderWindowPopover(agentId);
      scheduleOfflineRecheck();
      return;
    }
    resetOfflineBackoff();
    // The pill names an own window, so which assistants work apart from the
    // shared window shows without opening the panel.
    const own = windowChoice(agentId) === WINDOW_OWN;
    applyToggleView({
      mode: "on",
      label: own ? t("Browser on · own window") : t("Browser on"),
      title: own
        ? t("The assistant can read and operate its own EFP browser window in this chat. Click to switch off.")
        : t("The assistant can read and operate your EFP browser window in this chat. Click to switch off."),
    });
    renderWindowPopover(agentId);
  }

  // Schedules the next look for a bridge while the composer says offline,
  // unless one is already pending: a redraw while waiting leaves the wait as
  // it is. Each look waits twice as long as the one before, up to the
  // maximum. A look that comes due while the tab is hidden waits for the tab
  // instead (onTabVisible). Returns the wait it scheduled, 0 when a look was
  // already pending.
  function scheduleOfflineRecheck() {
    if (offlineRecheck) return 0;
    const delay = offlineRecheckDelay;
    offlineRecheckDelay = Math.min(offlineRecheckDelay * 2, OFFLINE_RECHECK_MAX_MS);
    offlineRecheck = window.setTimeout(() => {
      offlineRecheck = null;
      if (document.visibilityState !== "visible") {
        offlineRecheckPending = true;
        return;
      }
      recheckOffline();
    }, delay);
    return delay;
  }

  // Back to the short wait with no look pending: the member did something,
  // or the bridge answered, so the next offline redraw looks again soon.
  function resetOfflineBackoff() {
    window.clearTimeout(offlineRecheck);
    offlineRecheck = null;
    offlineRecheckPending = false;
    offlineRecheckDelay = OFFLINE_RECHECK_MS;
  }

  // A look while offline goes through the caches, so a tab that asked moments
  // ago serves this one; renderToggle schedules the next look when the
  // bridge is still missing.
  async function recheckOffline() {
    offlineRecheckPending = false;
    await probeLocalBridge();
    await renderToggle({ probe: false });
  }

  // The member is back at this tab: a bridge started meanwhile is wanted now,
  // so a pending or long-scheduled look happens at once, at the short wait.
  function onTabVisible() {
    const elements = toggleElements();
    if (!elements || elements.button.dataset.state !== "offline") return;
    if (!offlineRecheckPending && !offlineRecheck) return;
    resetOfflineBackoff();
    recheckOffline();
  }

  // Probe now and redraw: the Mobile testing panel calls this when it finds
  // or starts the bridge, so the composer does not keep saying offline.
  async function refreshToggle() {
    resetOfflineBackoff();
    await probeLocalBridge({ force: true });
    await renderToggle({ probe: false });
  }

  async function onToggleClick() {
    const agentId = currentAgentId();
    const elements = toggleElements();
    if (!elements || !agentId) return;
    resetOfflineBackoff();
    const mode = elements.button.dataset.state;
    if (mode === "setup") {
      window.location.hash = "#/connectors/local_bridge";
      return;
    }
    if (mode === "offline") {
      applyToggleView({ mode: "offline", label: t("Starting browser…"), title: t("Starting the local bridge and its Chrome window.") });
      const probe = await ensureBrowserSession({ agentId });
      if (!probe.alive) window.location.hash = "#/connectors/local_bridge?step=2";
      await renderToggle({ probe: false });
      return;
    }
    if (mode === "on") {
      setToggle(agentId, false);
    } else {
      setToggle(agentId, true);
    }
    await renderToggle({ probe: true });
  }

  // ---- window panel (the arrow next to the Browser pill) ---------------------

  // The panel has something to say once the connector is set up: which
  // window this assistant drives and whether that window is open.
  function popoverAllowed() {
    const elements = toggleElements();
    if (!elements || !elements.popover) return false;
    const mode = elements.button.dataset.state;
    return mode === "on" || mode === "off" || mode === "offline";
  }

  function renderWindowPopover(agentId) {
    const elements = toggleElements();
    if (!elements || !elements.popover) return;
    const { popover } = elements;
    const choice = windowChoice(agentId);
    popover.querySelectorAll('input[name="composer-browser-window"]').forEach((input) => {
      input.checked = input.value === choice;
    });
    const lb = state.localBridge;
    const session = bridgeSessionName(agentId);
    const alive = sessionAliveFor(session);
    let text;
    let tone = "";
    if (!lb.alive) {
      text = t("Local bridge offline");
    } else if (alive === true) {
      const entry = lb.sessions[session];
      text = tn(entry ? entry.tabCount : 0, "Window open ({n} tab)", "Window open ({n} tabs)");
      tone = "ok";
    } else if (alive === false) {
      text = choice === WINDOW_OWN ? t("Window not open yet") : t("Window closed");
    } else {
      text = t("Update the bridge to see whether this window is open.");
    }
    const stateNode = popover.querySelector("[data-browser-window-state]");
    if (stateNode) {
      stateNode.textContent = text;
      stateNode.dataset.tone = tone;
    }
    const openButton = popover.querySelector("[data-browser-window-open]");
    if (openButton) openButton.classList.toggle("hidden", !lb.alive || alive === true);
  }

  function windowPopoverOpen() {
    const elements = toggleElements();
    return Boolean(elements && elements.popover && !elements.popover.classList.contains("hidden"));
  }

  // Opens the panel with its radios on the current choice. With `focus` (the
  // arrow was pressed from the keyboard) the chosen radio takes focus, so the
  // arrow keys move the choice; a click leaves focus where it is, like the
  // Run settings panel next to it.
  function openWindowPopover({ focus = false } = {}) {
    const elements = toggleElements();
    if (!popoverAllowed()) return;
    renderWindowPopover(currentAgentId());
    elements.popover.classList.remove("hidden");
    if (elements.menu) elements.menu.setAttribute("aria-expanded", "true");
    if (focus) {
      const checked = elements.popover.querySelector('input[name="composer-browser-window"]:checked');
      if (checked) checked.focus();
    }
  }

  function closeWindowPopover() {
    const elements = toggleElements();
    if (!elements || !elements.popover) return;
    elements.popover.classList.add("hidden");
    if (elements.menu) elements.menu.setAttribute("aria-expanded", "false");
  }

  function toggleWindowPopover(options) {
    if (windowPopoverOpen()) closeWindowPopover();
    else openWindowPopover(options);
  }

  async function onWindowChoice(choice) {
    const agentId = currentAgentId();
    if (!agentId) return;
    const own = choice === WINDOW_OWN;
    setWindowChoice(agentId, own ? WINDOW_OWN : WINDOW_SHARED);
    resetOfflineBackoff();
    await renderToggle({ probe: false });
    // An own window picked while the browser is on for this chat is opened
    // now, so the member can sign in there before the assistant needs it.
    if (own && toggleOn(agentId) && state.localBridge.alive && sessionAliveFor(bridgeSessionName(agentId)) !== true) {
      await openAssistantWindow();
    }
  }

  async function openAssistantWindow() {
    const agentId = currentAgentId();
    const elements = toggleElements();
    if (!agentId || !elements || !elements.popover) return;
    const stateNode = elements.popover.querySelector("[data-browser-window-state]");
    if (stateNode) {
      stateNode.textContent = t("Opening window…");
      stateNode.dataset.tone = "";
    }
    const probe = await ensureBrowserSession({ agentId });
    await renderToggle({ probe: false });
    if (probe.error && stateNode) {
      const error = probe.error;
      stateNode.textContent = troubleshootFor(error) || String(error.message || error.code || t("The window could not be opened."));
      stateNode.dataset.tone = "warn";
    }
  }

  // The panel opens and closes by a click on the arrow (Enter and Space on
  // it included), closes on a click anywhere else, on Escape, and when focus
  // leaves it, and never opens on hover or focus alone: a pointer crossing
  // the toolbar or a Tab through the composer shows nothing.
  function bindWindowPopover() {
    const elements = toggleElements();
    if (!elements || !elements.wrap || !elements.popover || !elements.menu || elements.wrap.dataset.popoverBound === "1") return;
    const { wrap, popover, menu } = elements;
    wrap.dataset.popoverBound = "1";
    menu.addEventListener("click", (event) => {
      event.preventDefault();
      // A click's detail counts mouse clicks; Enter and Space report 0.
      toggleWindowPopover({ focus: event.detail === 0 });
    });
    document.addEventListener("mousedown", (event) => {
      if (windowPopoverOpen() && !wrap.contains(event.target)) closeWindowPopover();
    });
    wrap.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !wrap.contains(event.relatedTarget)) closeWindowPopover();
    });
    wrap.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (!windowPopoverOpen()) return;
        event.preventDefault();
        closeWindowPopover();
        menu.focus();
        return;
      }
      // The panel sits inside the chat form, where Enter on a radio would
      // submit the draft (Chrome's implicit submission); it picks the window.
      if (event.key === "Enter" && popover.contains(event.target)) {
        event.preventDefault();
        const input = event.target.closest('input[name="composer-browser-window"]');
        if (input && !input.checked) {
          input.checked = true;
          onWindowChoice(input.value);
        }
      }
    });
    popover.addEventListener("change", (event) => {
      const input = event.target.closest('input[name="composer-browser-window"]');
      if (input) onWindowChoice(input.value);
    });
    popover.addEventListener("click", (event) => {
      const open = event.target.closest("[data-browser-window-open]");
      if (!open) return;
      event.preventDefault();
      openAssistantWindow();
    });
  }

  function chatRequestConnectors(agentId) {
    const agent = agentId || currentAgentId();
    if (!state.featureEnabled || !agent || !isChromium()) return null;
    if (!localBridgeEnabled() || !toggleOn(agent)) return null;
    if (!state.localBridge.alive) return null;
    return {
      [LOCAL_BRIDGE_TYPE]: { client_id: clientId(), protocol_version: PROTOCOL_VERSION },
    };
  }

  // ---- connector panel (Connectors → Local bridge) --------------------------

  function panelStatus(root, key, text, tone) {
    const node = root.querySelector(`[data-connector-status="${key}"]`);
    if (!node) return;
    node.textContent = text;
    node.dataset.tone = tone || "";
  }

  function setPanelResult(root, key, html, tone) {
    const node = root.querySelector(`[data-connector-result="${key}"]`);
    if (!node) return;
    node.innerHTML = html;
    node.className = `portal-inline-state${tone ? ` is-${tone}` : ""}`;
    node.classList.toggle("hidden", !html);
  }

  async function refreshPanelStatus(root, { force = true } = {}) {
    panelStatus(root, "bridge", t("checking…"), "");
    const probe = await probeLocalBridge({ force });
    const bridgeStatus = probe.version
      ? t("running on port {port} (v{version})", { port: probe.port, version: probe.version })
      : t("running on port {port}", { port: probe.port });
    panelStatus(root, "bridge", probe.alive ? bridgeStatus : t("not detected"), probe.alive ? "ok" : "warn");
    const sessionStatus = probe.sessionAlive
      ? tn(probe.tabCount || 0, "Chrome window open ({n} tab)", "Chrome window open ({n} tabs)")
      : t("Chrome window closed (Start bridge reopens it)");
    panelStatus(root, "session", probe.alive ? sessionStatus : "–", probe.sessionAlive ? "ok" : "");
    const mobile = MOBILE_STATUS[probe.alive ? probe.mobile || "outdated" : ""];
    panelStatus(root, "mobile", tv(mobile.short), mobile.tone);
    panelStatus(root, "mobile-detail", tv(mobile.detail), mobile.tone);
    root.querySelectorAll("[data-connector-step]").forEach((section) => {
      const step = section.dataset.connectorStep;
      // Step 3 is browser automation (tested and switched on), step 4 mobile
      // recording (the bridge has it and mobile-auto next to it).
      const done = (step === "2" && probe.alive) || (step === "3" && root.dataset.lastVerified && root.dataset.enabled === "true") || (step === "4" && probe.alive && probe.mobile === "ready");
      section.classList.toggle("is-done", Boolean(done));
    });
    return probe;
  }

  // The short and detail texts are catalogue keys (N_), shown through tv().
  const MOBILE_STATUS = {
    "": { short: "–", tone: "", detail: N_("Start the bridge (step 2) to check.") },
    ready: { short: N_("ready"), tone: "ok", detail: N_("Ready. Add your BrowserStack username and access key under Connectors → BrowserStack, then choose Recording in an assistant's chat.") },
    outdated: { short: N_("not in this bridge"), tone: "warn", detail: N_("This bridge is older than mobile recording. Download the package again (step 1), run its installer, and start the bridge again.") },
    no_mobile_auto: { short: N_("mobile-auto missing"), tone: "warn", detail: N_("mobile-auto is not next to the bridge. Unzip the whole package again (step 1) into the same folder, then start the bridge again.") },
  };

  // What the panel tells the member next to a failed test or start; the
  // bridge's own error code and message stay as they are.
  function troubleshootFor(error) {
    const code = String((error && error.code) || "");
    if (code === "bridge_unreachable" || code === "bridge_timeout") {
      return t("The bridge is not running. Go back to step 2 and start it; if you just installed it, allow the efp-bridge link when Chrome asks.");
    }
    if (code === "devtools_unavailable") {
      return t("Chrome refused to expose its DevTools port. Check that the RemoteDebuggingAllowed policy is not set to false in chrome://policy.");
    }
    if (code === "origin_denied") {
      return t("The bridge was started for a different Portal address. Restart it with --origin {origin}.", { origin: portalOrigin() });
    }
    if (code === "command_not_allowed") {
      return t("Your bridge is older than this Portal. Download the bridge again (step 1) and run the installer once more.");
    }
    if (code === "browser_not_found") {
      return t("No Chrome, Edge, or Chromium was found on this PC. Install Chrome, then click Start bridge again.");
    }
    if (code === "session_not_running" || code === "session_not_found") {
      return t("The EFP browser window is closed. Click Start bridge to reopen it.");
    }
    if (code === "session_busy") {
      return t("Another browser command was still running. Wait a moment and test again.");
    }
    return "";
  }

  async function runPanelTest(root) {
    setPanelResult(root, "test", esc(t("Testing the bridge…")), "");
    const probe = await refreshPanelStatus(root, { force: true });
    if (!probe.alive) {
      const hint = troubleshootFor({ code: "bridge_unreachable" });
      setPanelResult(root, "test", `<strong>${esc(t("Bridge not detected."))}</strong> ${esc(hint)}`, "error");
      await recordVerification(root, false, { reason: "bridge_unreachable" });
      return;
    }
    const outcome = await runLocalBridge("tab.list", {}, 20);
    if (!outcome.ok) {
      const error = outcome.error || {};
      const hint = troubleshootFor(error) || String(error.hint || "");
      setPanelResult(root, "test", `<strong>${esc(t("Test failed:"))}</strong> ${esc(error.code || "error")} ${esc(error.message || "")}${hint ? `<br>${esc(hint)}` : ""}`, "error");
      await recordVerification(root, false, { code: error.code || "error" });
      return;
    }
    const tabs = outcome.result && Array.isArray(outcome.result.tabs) ? outcome.result.tabs : [];
    const rows = tabs.slice(0, 12).map((tab) => `<li>${tab.active ? "▶ " : ""}${esc(tab.title || t("(untitled)"))} <small>${esc(tab.url || "")}</small></li>`).join("");
    const summary = tn(
      tabs.length,
      "Bridge v{version} on port {port}; {n} tab in your EFP browser window.",
      "Bridge v{version} on port {port}; {n} tabs in your EFP browser window.",
      { version: probe.version || "?", port: probe.port }
    );
    setPanelResult(
      root,
      "test",
      `<strong>${esc(t("Connected."))}</strong> ${esc(summary)}${rows ? `<ul class="portal-connector-tab-list">${rows}</ul>` : ""}`,
      "success"
    );
    await recordVerification(root, true, { version: probe.version, port: probe.port, tab_count: tabs.length });
  }

  async function recordVerification(root, ok, details) {
    const type = root.dataset.connectorType || LOCAL_BRIDGE_TYPE;
    try {
      const { ok: saved, payload } = await requestJson(`/api/connectors/${encodeURIComponent(type)}/verify`, {
        method: "POST",
        body: JSON.stringify({ ok, details: details || {} }),
      });
      if (saved && ok) {
        const at = payload && payload.last_verified_at ? payload.last_verified_at : new Date().toISOString();
        root.dataset.lastVerified = at;
        panelStatus(root, "verified", at.replace("T", " ").slice(0, 19), "ok");
      }
    } catch (_error) {
      /* verification record is best-effort */
    }
  }

  async function savePanelSettings(root) {
    const type = root.dataset.connectorType || LOCAL_BRIDGE_TYPE;
    const enabled = Boolean(root.querySelector('[data-connector-field="enabled"]')?.checked);
    const autoEnable = Boolean(root.querySelector('[data-connector-field="auto_enable_in_new_chats"]')?.checked);
    const portInput = root.querySelector('[data-connector-field="preferred_port"]');
    const preferredPort = portInput ? Number(portInput.value) || PORT_RANGE[0] : PORT_RANGE[0];
    setPanelResult(root, "save", esc(t("Saving…")), "");
    const { ok, payload } = await requestJson(`/api/connectors/${encodeURIComponent(type)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled, config: { auto_enable_in_new_chats: autoEnable, preferred_port: preferredPort } }),
    });
    if (!ok) {
      const detail = payload && (payload.detail || payload.error) ? (payload.detail || payload.error) : t("Save failed.");
      setPanelResult(root, "save", esc(typeof detail === "string" ? detail : JSON.stringify(detail)), "error");
      return;
    }
    root.dataset.enabled = enabled ? "true" : "false";
    setPanelResult(root, "save", esc(enabled ? t("Saved. New chats can use your local browser.") : t("Saved. Assistants will not use your local browser.")), "success");
    await loadConnectors({ force: true });
    await refreshPanelStatus(root, { force: false });
    await renderToggle({ probe: false });
    document.dispatchEvent(new CustomEvent("portal:connectors-changed", { detail: { type, enabled } }));
  }

  // Which bridge package fits this browser's system. The server picks from the
  // User-Agent, where a Mac reads as Intel even on Apple silicon; client hints
  // name the CPU, so the primary download link is corrected here. Returns ""
  // when nothing better than the server's choice is known.
  async function detectDownloadPlatform() {
    const uaData = navigator.userAgentData;
    let os = uaData && uaData.platform ? String(uaData.platform) : "";
    let arch = "";
    if (uaData && typeof uaData.getHighEntropyValues === "function") {
      try {
        const hints = await uaData.getHighEntropyValues(["architecture", "platform"]);
        os = String(hints.platform || os);
        arch = String(hints.architecture || "");
      } catch (_error) {
        arch = "";
      }
    }
    const ua = navigator.userAgent || "";
    const source = os || ua;
    const family = /windows/i.test(source) ? "windows" : /mac/i.test(source) ? "darwin" : /linux|x11|cros/i.test(source) ? "linux" : "";
    if (!family) return "";
    if (!arch) {
      if (family === "darwin") return "";
      return `${family}-${/aarch64|arm64/i.test(ua) ? "arm64" : "amd64"}`;
    }
    return `${family}-${/arm/i.test(arch) ? "arm64" : "amd64"}`;
  }

  async function refineDownloadLink(root) {
    const primary = root.querySelector("[data-connector-download-primary]");
    if (!primary) return;
    const platform = await detectDownloadPlatform();
    if (!platform || primary.dataset.platform === platform) return;
    const match = root.querySelector(`[data-connector-download][data-platform="${platform}"]`);
    if (!match) return;
    primary.href = match.href;
    primary.dataset.platform = platform;
    const label = primary.querySelector("[data-connector-download-label]");
    if (label) label.textContent = t("Download for {platform}", { platform: match.dataset.label || platform });
  }

  function initPanel(rootElement) {
    const root = rootElement || document.getElementById(PANEL_ROOT_ID);
    if (!root || root.dataset.connectorsBound === "1") return;
    root.dataset.connectorsBound = "1";
    const originNodes = root.querySelectorAll("[data-connector-origin]");
    originNodes.forEach((node) => { node.textContent = portalOrigin(); });
    const launchLink = root.querySelector("[data-connector-launch-link]");
    if (launchLink) launchLink.href = launchUrl(localBridgeConfig().preferred_port);
    refineDownloadLink(root);

    root.addEventListener("click", async (event) => {
      const actionNode = event.target.closest("[data-connector-action]");
      if (!actionNode) return;
      const action = actionNode.dataset.connectorAction;
      if (action === "launch") {
        event.preventDefault();
        setPanelResult(root, "launch", esc(t("Starting the bridge and its Chrome window… allow the efp-bridge link if Chrome asks.")), "");
        const port = Number(root.querySelector('[data-connector-field="preferred_port"]')?.value) || undefined;
        const probe = await ensureBrowserSession({ port });
        // The markup around a sentence's words is built here and passed in as
        // a placeholder, so each sentence stays one catalogue key.
        const running = `<strong>${esc(t("Bridge is running"))}</strong>`;
        if (probe.alive && probe.sessionAlive) {
          setPanelResult(root, "launch", t("{running} on port {port} and the EFP browser window is open. Test it under Browser automation below.", { running, port: esc(probe.port) }), "success");
        } else if (probe.alive) {
          const error = probe.error || {};
          const hint = troubleshootFor(error) || String(error.hint || "");
          const failure = `${esc(error.code || "error")} ${esc(error.message || "")}`;
          setPanelResult(root, "launch", `${t("{running} on port {port}, but its Chrome window did not open: {error}", { running, port: esc(probe.port), error: failure })}${hint ? `<br>${esc(hint)}` : ""}`, "error");
        } else {
          const notDetected = `<strong>${esc(t("Bridge not detected yet."))}</strong>`;
          const logPath = "<code>.efp/bridge/logs/bridge.log</code>";
          setPanelResult(root, "launch", `${notDetected} ${t("If nothing happened, the protocol link is not registered: run install-bridge.cmd (Windows) or install-bridge.sh (macOS, Linux) from the unzipped folder, then try again. If it did start, the launcher writes what happened to {log} in your home folder; a bridge left running for a different Portal address is refused there by name.", { log: logPath })}`, "error");
        }
        await refreshPanelStatus(root, { force: false });
        await renderToggle({ probe: false });
      } else if (action === "test") {
        event.preventDefault();
        await runPanelTest(root);
        await renderToggle({ probe: false });
      } else if (action === "refresh") {
        event.preventDefault();
        await refreshPanelStatus(root, { force: true });
      } else if (action === "save") {
        event.preventDefault();
        await savePanelSettings(root);
      }
    });
    root.querySelectorAll("[data-connector-field]").forEach((field) => {
      field.addEventListener("change", () => {
        const saveButton = root.querySelector('[data-connector-action="save"]');
        if (saveButton) saveButton.classList.add("is-dirty");
      });
    });
    const params = new URLSearchParams((window.location.hash.split("?")[1] || ""));
    const step = params.get("step");
    if (step) {
      const section = root.querySelector(`[data-connector-step="${step}"]`);
      if (section) section.scrollIntoView({ block: "start", behavior: "smooth" });
    }
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try {
        window.lucide.createIcons();
      } catch (_error) {
        /* icons are cosmetic */
      }
    }
    refreshPanelStatus(root, { force: true });
  }

  // ---- wiring -----------------------------------------------------------------

  function bind() {
    register(localBridgeModule);
    bindWindowPopover();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") onTabVisible();
    });
    window.addEventListener("focus", onTabVisible);
    document.addEventListener("portal:runtime-event", (browserEvent) => {
      const detail = browserEvent.detail || {};
      const type = String(detail.event?.type || "");
      if (type !== "connector.request") return;
      handleConnectorRequest(detail);
    });
    document.addEventListener("click", (event) => {
      const button = event.target.closest(`#${TOGGLE_ID}`);
      if (!button) return;
      event.preventDefault();
      onToggleClick();
    });
    document.addEventListener("portal:agent-selected", () => {
      // A panel left open belongs to the assistant just left.
      closeWindowPopover();
      resetOfflineBackoff();
      renderToggle({ probe: true });
    });
    document.addEventListener("portal:history-rendered", () => { renderToggle({ probe: false }); });
    document.addEventListener("portal:connectors-changed", (browserEvent) => {
      // Our own save already refreshed; only react to changes made elsewhere.
      if (browserEvent.detail && browserEvent.detail.type) return;
      resetOfflineBackoff();
      loadConnectors({ force: true }).then(() => renderToggle({ probe: true }));
    });
    document.addEventListener("htmx:afterSwap", (event) => {
      const root = event.target && event.target.querySelector ? event.target.querySelector(`#${PANEL_ROOT_ID}`) : null;
      if (root) initPanel(root);
    });
    window.portalConnectors = {
      register,
      clientId,
      loadConnectors,
      probeLocalBridge,
      pingBridge,
      runLocalBridge,
      stageRequestFiles,
      workspaceFileUrl,
      launchBridge,
      ensureBrowserSession,
      windowChoice,
      setWindowChoice,
      bridgeSessionName,
      sessionAliveFor,
      chatRequestConnectors,
      renderToggle,
      refreshToggle,
      initPanel,
      protocolVersion: PROTOCOL_VERSION,
    };
    loadConnectors().then(() => renderToggle({ probe: true }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind, { once: true });
  } else {
    bind();
  }
})();
