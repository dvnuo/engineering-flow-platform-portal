/**
 * Mobile testing in the Portal page: the BrowserStack connector's checks and
 * the Mobile testing panel (assistant chat tool bar > Recording).
 *
 * Portal never talks to BrowserStack. The member's own computer does, through
 * the local bridge (`efp-bridge`, the Local bridge connector's program):
 * its /mobile/* routes list and upload builds, start a recording device with
 * mobile-auto and hold it, and proxy Appium Inspector's WebDriver traffic for
 * that device, logging what the compiler needs. The page sends the
 * member's BrowserStack credentials with each call; the bridge keeps them in
 * memory. Test runs go through the team's Jenkins pipeline, which the
 * assistant starts.
 */
(function () {
  "use strict";

  // ---- the local bridge ---------------------------------------------------

  const PORTS = [8765, 8766, 8767, 8768, 8769, 8770];
  const PING_TIMEOUT_MS = 1500;
  const PROBE_CACHE_MS = 4000;
  const PROXY_KEY = "efp.mobile.bridge_proxy";
  const PROXY_USER_KEY = "efp.mobile.bridge_proxy_user";
  const PROXY_PASSWORD_KEY = "efp.mobile.bridge_proxy_password";
  const BRIDGE_MESSAGES = {
    not_running: "The local bridge is not running on this computer. Start it here; if it is not installed yet, install it from Connectors > Local bridge first.",
    outdated: "The local bridge on this computer is an older version without mobile recording. Download it again from Connectors > Local bridge and restart it.",
    no_mobile_auto: "The local bridge cannot find mobile-auto next to it. Download the bridge package again from Connectors > Local bridge.",
  };

  const bridgeState = { port: 0, info: null, probedAt: 0, probing: null };

  function readStorage(key) {
    try {
      return window.localStorage.getItem(key) || "";
    } catch (_error) {
      return "";
    }
  }

  function writeStorage(key, value) {
    try {
      if (value) window.localStorage.setItem(key, value);
      else window.localStorage.removeItem(key);
    } catch (_error) {
      /* storage is a convenience */
    }
  }

  // The proxy to BrowserStack from this computer is kept in this browser: its
  // address, and apart from it the login a corporate proxy asks for, so the
  // password sits in a password field instead of in the address.
  function proxySettings() {
    return {
      url: readStorage(PROXY_KEY).trim(),
      username: readStorage(PROXY_USER_KEY),
      password: readStorage(PROXY_PASSWORD_KEY),
    };
  }

  // Takes a login typed into a proxy address out of it, decoded.
  function splitProxyLogin(value) {
    const text = String(value || "").trim();
    let parsed = null;
    try {
      parsed = new URL(text);
    } catch (_error) {
      parsed = null;
    }
    if (!parsed || (!parsed.username && !parsed.password)) return { url: text, username: "", password: "", login: false };
    const decode = (part) => {
      try {
        return decodeURIComponent(part);
      } catch (_error) {
        return part;
      }
    };
    return { url: `${parsed.protocol}//${parsed.host}`, username: decode(parsed.username), password: decode(parsed.password), login: true };
  }

  // The proxy as the bridge takes it: one address with the login in it, each
  // part percent-encoded (a domain user's backslash, an @ in a password).
  function proxyForBridge(settings) {
    const { url, username, password } = settings || proxySettings();
    if (!url || (!username && !password)) return url;
    let parsed = null;
    try {
      parsed = new URL(url);
    } catch (_error) {
      return url;
    }
    const login = encodeURIComponent(username) + (password ? `:${encodeURIComponent(password)}` : "");
    return `${parsed.protocol}//${login}@${parsed.host}`;
  }

  // Shows the saved proxy in the fields of the connector page or the
  // Mobile testing panel, leaving alone the one being typed in.
  function fillProxyFields(container) {
    if (!container) return;
    const settings = proxySettings();
    container.querySelectorAll("[data-bridge-proxy]").forEach((input) => {
      if (document.activeElement !== input) input.value = settings[input.dataset.bridgeProxy] || "";
    });
  }

  // A login saved inside the address, before it had fields of its own, moves
  // to them, so it is no longer shown in the clear.
  function moveProxyLoginOutOfTheAddress() {
    const saved = splitProxyLogin(readStorage(PROXY_KEY));
    if (!saved.login) return;
    writeStorage(PROXY_KEY, saved.url);
    if (!readStorage(PROXY_USER_KEY) && !readStorage(PROXY_PASSWORD_KEY)) {
      writeStorage(PROXY_USER_KEY, saved.username);
      writeStorage(PROXY_PASSWORD_KEY, saved.password);
    }
  }

  moveProxyLoginOutOfTheAddress();

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function ping(port) {
    try {
      const response = await fetchWithTimeout(`http://127.0.0.1:${port}/ping`, { method: "GET", mode: "cors", cache: "no-store" }, PING_TIMEOUT_MS);
      if (!response.ok) return null;
      const payload = await response.json();
      return payload && payload.ok === true ? (payload.data || {}) : null;
    } catch (_error) {
      return null;
    }
  }

  function bridgeView() {
    const info = bridgeState.info;
    const capabilities = info && Array.isArray(info.capabilities) ? info.capabilities : [];
    return {
      alive: Boolean(info),
      port: bridgeState.port,
      version: info ? String(info.version || "") : "",
      mobile: capabilities.indexOf("mobile") >= 0,
      mobileAuto: Boolean(info && info.mobile && info.mobile.available),
    };
  }

  async function probe({ force = false } = {}) {
    if (!force && bridgeState.probedAt && Date.now() - bridgeState.probedAt < PROBE_CACHE_MS) return bridgeView();
    if (bridgeState.probing) return bridgeState.probing;
    bridgeState.probing = (async () => {
      // All ports at once; the last known one wins when several answer.
      const ports = bridgeState.port ? [bridgeState.port, ...PORTS.filter((port) => port !== bridgeState.port)] : PORTS;
      const results = await Promise.all(ports.map(async (port) => ({ port, data: await ping(port) })));
      const found = results.find((entry) => entry.data) || null;
      bridgeState.port = found ? found.port : 0;
      bridgeState.info = found ? found.data : null;
      bridgeState.probedAt = Date.now();
      bridgeState.probing = null;
      return bridgeView();
    })();
    return bridgeState.probing;
  }

  function bridgeProblem(state) {
    if (!state || !state.alive) return "not_running";
    if (!state.mobile) return "outdated";
    if (!state.mobileAuto) return "no_mobile_auto";
    return "";
  }

  function bridgeError(error, data) {
    const detail = error && typeof error === "object" ? error : {};
    const err = new Error(String(detail.message || "The local bridge could not do that."));
    err.code = String(detail.code || "bridge_error");
    err.hint = String(detail.hint || "");
    if (data && typeof data === "object") err.data = data;
    return err;
  }

  function errorText(error) {
    if (!error) return "Something went wrong.";
    const message = String(error.message || error);
    const hint = error.hint ? String(error.hint) : "";
    return hint && message.indexOf(hint) < 0 ? `${message} ${hint}` : message;
  }

  async function call(command, params, { credentials = null, timeoutMs = 60000 } = {}) {
    let state = bridgeView();
    if (!state.alive) state = await probe({ force: true });
    if (!state.alive) throw bridgeError({ code: "bridge_unreachable", message: BRIDGE_MESSAGES.not_running });
    const body = JSON.stringify({ command, params: params || {}, credentials: credentials || {}, proxy: proxyForBridge() });
    let response;
    try {
      response = await fetchWithTimeout(`http://127.0.0.1:${state.port}/mobile/run`, {
        method: "POST",
        mode: "cors",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body,
      }, timeoutMs);
    } catch (error) {
      const aborted = error && error.name === "AbortError";
      if (!aborted) {
        bridgeState.info = null;
        bridgeState.probedAt = 0;
      }
      throw bridgeError({
        code: aborted ? "bridge_timeout" : "bridge_unreachable",
        message: aborted ? "The local bridge did not answer in time." : "Could not reach the local bridge on this computer.",
      });
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (payload && payload.ok === true) return payload.data || {};
    throw bridgeError(payload && payload.error ? payload.error : { code: "bridge_error", message: `The local bridge answered HTTP ${response.status}.` }, payload && payload.data);
  }

  function safeFileName(name) {
    return String(name || "build").replace(/[^A-Za-z0-9._-]+/g, "_");
  }

  // Streams a build to the bridge, which uploads it to BrowserStack. XHR for
  // the progress events fetch does not have.
  function uploadBuild(file, { credentials, customId, onProgress }) {
    return new Promise((resolve, reject) => {
      const state = bridgeView();
      if (!state.alive) {
        reject(bridgeError({ code: "bridge_unreachable", message: BRIDGE_MESSAGES.not_running }));
        return;
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `http://127.0.0.1:${state.port}/mobile/apps/upload`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-EFP-File-Name", safeFileName(file.name));
      xhr.setRequestHeader("X-EFP-BS-User", credentials.username || "");
      xhr.setRequestHeader("X-EFP-BS-Key", credentials.access_key || "");
      if (credentials.api_base_url) xhr.setRequestHeader("X-EFP-BS-API", credentials.api_base_url);
      if (customId) xhr.setRequestHeader("X-EFP-Custom-Id", customId);
      const proxy = proxyForBridge();
      if (proxy) xhr.setRequestHeader("X-EFP-Proxy", proxy);
      xhr.timeout = 30 * 60 * 1000;
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
      };
      xhr.onload = () => {
        let payload = null;
        try {
          payload = JSON.parse(xhr.responseText);
        } catch (_error) {
          payload = null;
        }
        if (payload && payload.ok === true) resolve((payload.data || {}).app || {});
        else reject(bridgeError(payload && payload.error ? payload.error : { code: "bridge_error", message: `The upload failed (HTTP ${xhr.status}).` }));
      };
      xhr.onerror = () => reject(bridgeError({ code: "bridge_unreachable", message: "Could not reach the local bridge on this computer." }));
      xhr.ontimeout = () => reject(bridgeError({ code: "bridge_timeout", message: "The upload took longer than 30 minutes." }));
      xhr.send(file);
    });
  }

  function launchBridge() {
    if (window.portalConnectors && typeof window.portalConnectors.launchBridge === "function") {
      window.portalConnectors.launchBridge();
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = `efp-bridge://start?origin=${encodeURIComponent(window.location.origin)}&port=${PORTS[0]}`;
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    window.setTimeout(() => anchor.remove(), 0);
  }

  async function launchAndWait(timeoutMs = 10000) {
    launchBridge();
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
      await sleep(700);
      const state = await probe({ force: true });
      if (state.alive) return state;
    }
    return probe({ force: true });
  }

  // ---- shared helpers -------------------------------------------------------

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setInline(el, text, tone) {
    if (!el) return;
    el.textContent = text || "";
    el.className = `portal-inline-state${text ? " is-visible" : ""}${tone ? ` is-${tone}` : ""}`;
  }

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try {
        window.lucide.createIcons();
      } catch (_error) {
        /* icons are cosmetic */
      }
    }
  }

  function copyText(button, value) {
    const done = () => {
      button.classList.add("is-copied");
      window.setTimeout(() => button.classList.remove("is-copied"), 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done).catch(() => window.prompt("Copy:", value));
    } else {
      window.prompt("Copy:", value);
    }
  }

  // ---- Connectors > BrowserStack -------------------------------------------

  function connectorCredentials(form) {
    const value = (name) => {
      const input = form ? form.querySelector(`[name="${name}"]`) : null;
      return input ? String(input.value || "").trim() : "";
    };
    return {
      username: value("mobile_browserstack_username"),
      access_key: value("mobile_browserstack_access_key"),
      api_base_url: value("mobile_browserstack_api_base_url"),
      appium_base_url: value("mobile_browserstack_appium_base_url"),
    };
  }

  async function testConnector(button) {
    const form = button.closest("form") || document;
    const result = form.querySelector("[data-mobile-bridge-test-result]");
    const credentials = connectorCredentials(form);
    if (!credentials.username || !credentials.access_key) {
      setInline(result, "Enter your BrowserStack username and access key first.", "error");
      return;
    }
    button.disabled = true;
    setInline(result, "Signing in to BrowserStack from this computer…", "");
    try {
      const problem = bridgeProblem(await probe({ force: true }));
      if (problem) {
        setInline(result, BRIDGE_MESSAGES[problem], "error");
        return;
      }
      const plan = await call("plan", {}, { credentials, timeoutMs: 45000 });
      const max = Number(plan.parallel_sessions_max_allowed) || 0;
      const running = Number(plan.parallel_sessions_running) || 0;
      const queued = Number(plan.queued_sessions) || 0;
      const sessions = max ? `${running} of ${max} parallel sessions in use` : `${running} parallel sessions in use`;
      setInline(result, `Signed in as ${plan.username || credentials.username}. ${sessions}${queued ? `, ${queued} queued` : ""}.`, "success");
    } catch (error) {
      setInline(result, errorText(error), "error");
    } finally {
      button.disabled = false;
    }
  }

  async function refreshOverview(root) {
    const status = root.querySelector("[data-mobile-bridge-status]");
    const start = root.querySelector('[data-mobile-bridge-action="start"]');
    fillProxyFields(root);
    const state = await probe({ force: true });
    const problem = bridgeProblem(state);
    if (problem) setInline(status, BRIDGE_MESSAGES[problem], problem === "not_running" ? "warning" : "error");
    else setInline(status, `Local bridge ready on 127.0.0.1:${state.port}${state.version ? ` (version ${state.version})` : ""}.`, "success");
    if (start) start.classList.toggle("hidden", problem !== "not_running");
  }

  async function startBridgeFromOverview(button) {
    const root = button.closest("[data-mobile-overview]");
    button.disabled = true;
    setInline(root && root.querySelector("[data-mobile-bridge-status]"), "Starting the local bridge… allow the efp-bridge link if Chrome asks.", "");
    try {
      await launchAndWait();
    } finally {
      button.disabled = false;
      if (root) await refreshOverview(root);
    }
  }

  // ---- Mobile testing panel --------------------------------------------------
  //
  // The member's computer's side of mobile scenario testing: a BrowserStack
  // device the local bridge holds, recording on it with Appium Inspector, and
  // replaying compiled segments on it. The chat is where the assistant splits
  // a recording, reviews a replay, and generates scripts; the panel's progress
  // strip says which of those comes next.
  //
  // 1. Start: session.start answers at once with the recording "starting";
  //    the panel polls session.status until it is "active" (or "failed", with
  //    the error). When Portal hosts the Inspector, Start also opens a tab
  //    that lands in the Inspector, attached to the device, once it is ready.
  // 2. Record: the member records the whole scenario; "Save recording" takes
  //    the log from the bridge (segment.done) and writes it into the
  //    assistant's workspace. The chat message tells the assistant: a
  //    recording it splits into segments with the member, or, when the member
  //    listed segment names, a segment it compiles whole.
  // 3. Replay: compiled segments run on the held device (segment.replay); the
  //    result goes into the workspace and the chat, where the assistant
  //    reviews it.
  // 4. Finish releases the device.

  const PANEL_TITLE = "Mobile testing";
  const RECORDINGS_DIR = "mobile/recordings";
  const SEGMENTS_DIR = "mobile/segments";
  const REPLAYS_DIR = "mobile/replays";
  const SCENARIOS_DIR = "mobile/scenarios";
  const POLL_MS = 5000;
  const STATUS_EVERY = 12;
  const START_POLL_MS = 3000;
  const START_TIMEOUT_MS = 16 * 60 * 1000;
  const REPLAY_POLL_MS = 2000;
  const CODE_EXTENSIONS = [".py", ".java", ".js", ".rb", ".robot", ".cs"];
  const BUILD_EXTENSIONS = [".apk", ".aab", ".ipa"];
  const SEGMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  const CUSTOM_ID = /^[A-Za-z0-9._-]{1,100}$/;
  const RECORDING_KEY_PREFIX = "efp.mobile.recording:";
  const AUTO_INSPECTOR_KEY = "efp.mobile.auto_inspector";

  const view = {
    agentId: "",
    config: null,
    bridge: null,
    apps: null,
    appsError: "",
    selectedApp: "",
    recording: null,
    others: [],
    planned: [],
    done: [],
    unsaved: null,
    busy: "",
    sessionKey: "",
    formKey: "",
    polls: 0,
    replay: freshReplay(),
    // flow is what the workspace says about the steps after recording: the
    // latest split, the compiled segments, and the scenario plan.
    flow: null,
    settingsOpen: false,
    connectionOpen: false,
    // inspectorTab is the tab Start opened to land in the Inspector.
    inspectorTab: null,
    // workspace is the overlay that holds the Inspector inside Portal.
    workspace: null,
    // tab is Session (the device and the step at hand) or Library (what the
    // workspace keeps: recordings, segments, replays).
    tab: "session",
    library: freshLibrary(),
  };
  let pollTimer = 0;
  let tickTimer = 0;
  let replayTimer = 0;

  function currentAgentId() {
    return typeof window.currentPortalAgentId === "function" ? (window.currentPortalAgentId() || "") : "";
  }

  // The panel's root: in the Inspector workspace while that is open, else in
  // the tool panel.
  function panelRoot() {
    return document.querySelector("[data-recording-workspace] [data-recording-root]") || document.querySelector("#tool-panel-body [data-recording-root]");
  }

  function sendChat(text) {
    return Boolean(window.EfpCards && typeof window.EfpCards.sendChatText === "function" && window.EfpCards.sendChatText(text));
  }

  function setStatus(text, tone) {
    setInline(panelRoot()?.querySelector("[data-recording-status]"), text, tone);
  }

  function credentials() {
    return view.config && view.config.credentials ? view.config.credentials : null;
  }

  function inspectorAvailable() {
    return Boolean(view.config && view.config.inspector_available);
  }

  // Start opens a tab for the Inspector unless the member switched it off.
  function autoInspector() {
    return readStorage(AUTO_INSPECTOR_KEY) !== "off";
  }

  function parseSegments(text) {
    return String(text || "")
      .split(/[\n,]+/)
      .map((item) => item.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+/, ""))
      .filter(Boolean)
      .slice(0, 30);
  }

  // The chat message for a saved file: a name the member listed is a segment
  // the assistant compiles whole; any other is a recording of the scenario,
  // which the assistant proposes to split into segments.
  function savedMessage(name, path, planned) {
    const names = Array.isArray(planned) ? planned : view.planned;
    return names.indexOf(name) >= 0 ? `Segment ${name} recorded: ${path}` : `Recording ${name} saved: ${path}`;
  }

  function nextPlanned(name) {
    const index = view.planned.indexOf(name);
    if (index >= 0 && index + 1 < view.planned.length) return view.planned[index + 1];
    return "";
  }

  function remaining(deadline) {
    const ms = new Date(deadline).getTime() - Date.now();
    if (!Number.isFinite(ms)) return "";
    if (ms <= 0) return "expired";
    const total = Math.floor(ms / 1000);
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  function suggestCustomId(fileName) {
    const name = String(fileName || "");
    const dot = name.lastIndexOf(".");
    const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
    const platform = ext === "ipa" ? "ios" : (ext === "apk" || ext === "aab" ? "android" : "");
    if (!platform) return "";
    let stem = dot >= 0 ? name.slice(0, dot) : name;
    stem = stem.replace(/[-_.]v?\d+(?:[._]\d+)*(?=$|[-_])/g, "");
    const base = stem.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "").slice(0, 100);
    if (!base) return "";
    return base.endsWith(platform) ? base : `${base}-${platform}`.slice(0, 100);
  }

  function storageKey() {
    return RECORDING_KEY_PREFIX + view.agentId;
  }

  function remember() {
    if (!view.agentId) return;
    if (!view.recording) {
      writeStorage(storageKey(), "");
      return;
    }
    writeStorage(storageKey(), JSON.stringify({ id: view.recording.id, planned: view.planned, done: view.done, replays: view.replay.saved.slice(-20) }));
  }

  function remembered() {
    try {
      const value = JSON.parse(readStorage(storageKey()) || "null");
      return value && typeof value.id === "string" ? value : null;
    } catch (_error) {
      return null;
    }
  }

  function ready() {
    return Boolean(view.config && view.config.configured && !bridgeProblem(view.bridge));
  }

  function ensureReady() {
    if (!view.config || !view.config.configured) {
      setStatus((view.config && view.config.problem) || "Set up the BrowserStack connector first (Connectors > BrowserStack).", "error");
      return false;
    }
    const problem = bridgeProblem(view.bridge);
    if (problem) {
      setStatus(BRIDGE_MESSAGES[problem], "error");
      return false;
    }
    return true;
  }

  // ---- rendering ---------------------------------------------------------------

  function shellHtml() {
    return `
      <div class="efp-mobile" data-recording-root>
        <div class="efp-mobile-bar" data-recording-bar></div>
        <div data-recording-device></div>
        <div class="efp-mobile-tabs" data-recording-tabs role="tablist"></div>
        <div data-recording-flow></div>
        <section class="efp-mobile-section hidden" data-recording-record></section>
        <section class="efp-mobile-section hidden" data-recording-replay></section>
        <section class="efp-mobile-section hidden" data-recording-library></section>
        <section class="efp-mobile-section efp-mobile-settings hidden" data-recording-settings></section>
        <div class="portal-inline-state" data-recording-status role="status"></div>
      </div>`;
  }

  function renderAll() {
    renderBar();
    renderDevice();
    renderApps();
    renderOthers();
    renderFlow();
    renderTabs();
    renderRecord();
    renderReplay();
    renderLibrary();
    renderSettings();
    renderIcons();
  }

  function renderTabs() {
    const target = panelRoot()?.querySelector("[data-recording-tabs]");
    if (!target) return;
    const tab = (key, label) => `<button type="button" role="tab" data-recording-tab="${key}" aria-selected="${view.tab === key ? "true" : "false"}">${label}</button>`;
    target.innerHTML = tab("session", "Session") + tab("library", "Library");
  }

  function switchTab(tab) {
    view.tab = tab === "library" ? "library" : "session";
    view.formKey = "";
    renderTabs();
    renderDevice();
    renderFlow();
    renderFlow();
    renderRecord();
    renderReplay();
    renderLibrary();
    renderIcons();
    if (view.tab === "library") loadLibrary();
  }

  // renderSession re-renders what a recording's state changes: the device
  // card, the Record block, and the Replay block.
  function renderSession() {
    renderDevice();
    renderRecord();
    renderReplay();
    renderFlow();
    renderIcons();
  }

  // The bar names the bridge, or what is wrong with it, and holds Settings.
  function renderBar() {
    const bar = panelRoot()?.querySelector("[data-recording-bar]");
    if (!bar) return;
    const config = view.config;
    let main;
    if (!config) {
      main = `<span class="efp-card-meta">Checking your BrowserStack settings and the local bridge…</span>`;
    } else if (!config.configured) {
      main = `<div class="portal-inline-state is-visible is-warning">${esc(config.problem || "Set up the BrowserStack connector first (Connectors > BrowserStack).")}</div>`;
    } else {
      const problem = bridgeProblem(view.bridge);
      if (!problem) {
        main = `<span class="efp-card-meta">Local bridge on 127.0.0.1:${esc(view.bridge.port)}</span>`;
      } else {
        const start = problem === "not_running"
          ? `<button type="button" class="portal-btn is-primary" data-recording-action="start-bridge"${view.busy === "bridge" ? " disabled" : ""}><i data-lucide="plug" class="w-4 h-4"></i>Start bridge</button>`
          : "";
        main = `
          <div class="portal-inline-state is-visible ${problem === "not_running" ? "is-warning" : "is-error"}">${esc(BRIDGE_MESSAGES[problem])}</div>
          <div class="efp-card-actions">${start}<button type="button" class="portal-btn is-secondary" data-recording-action="check-bridge"><i data-lucide="refresh-cw" class="w-4 h-4"></i>Check again</button></div>`;
      }
    }
    bar.innerHTML = `<div class="efp-mobile-bar-main">${main}</div>
      <button type="button" class="toolbar-icon-btn" data-recording-action="settings" title="Settings" aria-label="Settings" aria-pressed="${view.settingsOpen ? "true" : "false"}"><i data-lucide="settings" class="w-4 h-4"></i></button>`;
  }

  function startLabel() {
    return inspectorAvailable() && autoInspector() ? "Start and open Inspector" : "Start recording device";
  }

  function startFormHtml() {
    return `
      <div class="efp-card efp-device">
        <div class="efp-card-head"><strong class="efp-card-title">Start a device</strong></div>
        <p class="efp-card-meta">The local bridge starts a BrowserStack device with the build and holds it while you record and replay.</p>
        <label class="portal-form-label"><span class="portal-form-label">Build</span>
          <select class="portal-form-select" data-recording-app><option value="">Looking for your builds…</option></select>
        </label>
        <div class="grid grid-cols-2 gap-3">
          <label class="portal-form-label"><span class="portal-form-label">Platform</span>
            <select class="portal-form-select" data-recording-platform><option value="android">Android</option><option value="ios">iOS</option></select>
          </label>
          <label class="portal-form-label"><span class="portal-form-label">Device (optional)</span>
            <input class="portal-form-input" data-recording-device-name placeholder="Google Pixel 8" />
          </label>
        </div>
        <details class="portal-collapsible">
          <summary class="portal-collapsible-summary"><span>Segment names (optional)</span></summary>
          <div class="portal-panel-stack">
            <textarea class="portal-form-textarea" rows="2" data-recording-segments placeholder="seg-login&#10;seg-select-currency"></textarea>
            <p class="portal-inline-note">Leave this empty to record the whole scenario in one go: the assistant proposes how to split it into segments, and you confirm. List names only to save each part yourself, in this order.</p>
          </div>
        </details>
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-primary" data-recording-action="start"><i data-lucide="play" class="w-4 h-4"></i>${esc(startLabel())}</button>
          <button type="button" class="portal-btn is-secondary" data-recording-action="settings-upload"><i data-lucide="upload" class="w-4 h-4"></i>Upload a build</button>
        </div>
      </div>
      <div data-recording-others></div>`;
  }

  function appLabel(app) {
    const name = app.custom_id || app.app_name || app.app_url;
    const platform = app.platform === "ios" ? "iOS" : (app.platform === "android" ? "Android" : "");
    const version = app.app_version ? ` ${app.app_version}` : "";
    const uploaded = app.uploaded_at ? ` · ${String(app.uploaded_at).slice(0, 10)}` : "";
    return `${name}${version}${platform ? ` · ${platform}` : ""}${uploaded}`;
  }

  function renderApps() {
    const select = panelRoot()?.querySelector("[data-recording-app]");
    if (!select) return;
    let options = "";
    if (!ready()) {
      options = `<option value="">Your builds show up once the local bridge is ready</option>`;
    } else if (view.appsError) {
      options = `<option value="">Could not list your builds</option>`;
    } else if (!view.apps) {
      options = `<option value="">Looking for your builds…</option>`;
    } else if (!view.apps.length) {
      options = `<option value="">No builds on BrowserStack yet; upload one</option>`;
    } else {
      options = view.apps.map((app) => {
        const value = app.custom_id || app.app_url;
        const selected = value === view.selectedApp ? " selected" : "";
        return `<option value="${esc(value)}" data-platform="${esc(app.platform || "")}"${selected}>${esc(appLabel(app))}</option>`;
      }).join("");
    }
    select.innerHTML = options;
    syncPlatform();
  }

  function syncPlatform() {
    const root = panelRoot();
    const select = root?.querySelector("[data-recording-app]");
    const platform = root?.querySelector("[data-recording-platform]");
    if (!select || !platform) return;
    const option = select.selectedOptions && select.selectedOptions[0];
    const fromApp = option ? option.dataset.platform : "";
    const fallback = view.config && view.config.defaults ? view.config.defaults.platform : "";
    const value = fromApp || fallback;
    if (value === "android" || value === "ios") platform.value = value;
    view.selectedApp = select.value || view.selectedApp;
  }

  function renderOthers() {
    const target = panelRoot()?.querySelector("[data-recording-others]");
    if (!target) return;
    if (!view.others.length) {
      target.innerHTML = "";
      return;
    }
    target.innerHTML = view.others.map((rec) => `
      <div class="portal-inline-state is-visible is-warning">
        This computer still holds a device from another recording: ${esc(rec.device || rec.platform || "a device")}, recording ${esc(rec.segment || "")}.
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-secondary" data-recording-action="adopt" data-id="${esc(rec.id)}">Continue it here</button>
          <button type="button" class="portal-btn is-secondary" data-recording-action="finish-other" data-id="${esc(rec.id)}">Release the device</button>
        </div>
      </div>`).join("");
  }

  function summaryText(summary) {
    const s = summary || {};
    const actions = Number(s.actions) || 0;
    const secrets = Number(s.secrets) || 0;
    if (!actions) return "Nothing recorded since the last save";
    return `${actions} ${actions === 1 ? "action" : "actions"} not saved${secrets ? ` (${secrets} typed into password fields, not stored)` : ""}`;
  }

  function kvRow(label, value) {
    return `<div class="efp-recording-kv"><span>${esc(label)}</span><code>${esc(value)}</code><button type="button" class="composer-pill-btn" data-recording-copy="${esc(value)}" title="Copy"><i data-lucide="copy" class="w-4 h-4"></i></button></div>`;
  }

  function elapsedText(since) {
    const total = Math.max(0, Math.round((Date.now() - since) / 1000));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes ? `${minutes}:${String(seconds).padStart(2, "0")}` : `${seconds}s`;
  }

  function platformLabel(rec) {
    return rec.platform === "ios" ? "iOS" : "Android";
  }

  function startingCardHtml(rec) {
    const since = Date.parse(rec.started_at || "") || Date.now();
    return `
      <div class="efp-card efp-device">
        <div class="efp-card-head">
          <span class="portal-status-badge is-warning">Starting</span>
          <strong class="efp-card-title">${esc(rec.device || "BrowserStack device")}</strong>
          <span class="efp-card-meta">${esc(platformLabel(rec))}</span>
        </div>
        <div class="efp-card-meta">Build ${esc(rec.app || "")}</div>
        <div class="efp-card-meta">${esc(rec.progress || "starting the device on BrowserStack")}… <span data-recording-countdown data-since="${esc(String(since))}">${esc(elapsedText(since))}</span></div>
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-secondary" data-recording-action="finish"><i data-lucide="square" class="w-4 h-4"></i>Cancel</button>
        </div>
      </div>`;
  }

  // How the desktop Inspector, or any Appium Inspector of the member's own,
  // reaches the device through the bridge.
  function connectionHtml(rec) {
    const port = view.bridge && view.bridge.port ? String(view.bridge.port) : "";
    return `
      <div class="efp-device-connection">
        <p class="portal-inline-note">In Appium Inspector 2026.5.1 or later choose <strong>Appium Server</strong>, enter these, leave SSL off, then <strong>Attach to Session</strong> and pick this session.</p>
        ${kvRow("Remote host", "127.0.0.1")}
        ${port ? kvRow("Remote port", port) : ""}
        ${kvRow("Remote path", `/mobile/wd/${rec.id}`)}
        ${rec.session_id ? kvRow("Session id", rec.session_id) : ""}
      </div>`;
  }

  function deviceCardHtml(rec) {
    const ended = rec.status && rec.status !== "active";
    const held = rec.hold_deadline && remaining(rec.hold_deadline) !== "expired";
    const badge = ended ? "Ended" : (held ? "Held" : "Hold expired");
    const tone = !ended && held ? "success" : "warning";
    const replaying = Boolean(rec.replay && rec.replay.status === "running");
    const inspector = inspectorAvailable();
    const menu = [
      inspector ? `<button type="button" data-recording-action="open-inspector"><i data-lucide="external-link" class="w-4 h-4"></i>Open Inspector in a new tab</button>` : "",
      inspector ? `<button type="button" data-recording-action="open-workspace"><i data-lucide="layout-panel-left" class="w-4 h-4"></i>Open Inspector here</button>` : "",
      `<button type="button" data-recording-action="connection"><i data-lucide="link" class="w-4 h-4"></i>${view.connectionOpen ? "Hide connection details" : "Connection details"}</button>`,
      rec.dashboard_url ? `<a href="${esc(rec.dashboard_url)}" target="_blank" rel="noopener noreferrer"><i data-lucide="smartphone" class="w-4 h-4"></i>BrowserStack dashboard</a>` : "",
      `<button type="button" class="is-danger" data-recording-action="finish"><i data-lucide="square" class="w-4 h-4"></i>Finish recording</button>`,
    ].filter(Boolean).join("");
    return `
      <div class="efp-card efp-device">
        <div class="efp-card-head">
          <span class="portal-status-badge is-${tone}">${esc(badge)}</span>
          <strong class="efp-card-title">${esc(rec.device || "BrowserStack device")}</strong>
          <span class="efp-card-meta">${esc(platformLabel(rec))}${rec.os_version ? ` ${esc(rec.os_version)}` : ""}</span>
          <span class="efp-device-tools">
            ${rec.hold_deadline && !ended ? `<span class="efp-card-meta" title="Held for another"><i data-lucide="timer" class="w-3 h-3"></i> <span data-recording-countdown data-deadline="${esc(rec.hold_deadline)}">${esc(remaining(rec.hold_deadline))}</span></span>` : ""}
            <button type="button" class="toolbar-icon-btn" data-recording-action="extend" title="Hold 30 more minutes" aria-label="Hold 30 more minutes"${replaying ? " disabled" : ""}><i data-lucide="timer-reset" class="w-4 h-4"></i></button>
            <details class="efp-menu">
              <summary class="toolbar-icon-btn" title="More" aria-label="More"><i data-lucide="ellipsis" class="w-4 h-4"></i></summary>
              <div class="efp-menu-list">${menu}</div>
            </details>
          </span>
        </div>
        <div class="efp-card-meta">Build ${esc(rec.app || "")}</div>
        ${view.connectionOpen ? connectionHtml(rec) : ""}
      </div>`;
  }

  function renderDevice() {
    const target = panelRoot()?.querySelector("[data-recording-device]");
    if (!target) return;
    const rec = view.recording;
    if (!rec && view.tab === "library") {
      view.formKey = "";
      target.innerHTML = `<div class="efp-card efp-device"><div class="efp-card-meta">No device held. Start one under Session to record or replay.</div></div>`;
      return;
    }
    if (!rec) {
      // The start form keeps what the member typed across polls.
      const key = ["form", startLabel(), view.config ? view.config.configured : ""].join("|");
      if (view.formKey !== key || !target.querySelector("[data-recording-app]")) {
        view.formKey = key;
        target.innerHTML = startFormHtml();
        const segments = target.querySelector("[data-recording-segments]");
        if (segments && view.planned.length) segments.value = view.planned.join("\n");
        renderApps();
        renderOthers();
      }
      return;
    }
    view.formKey = "";
    target.innerHTML = rec.status === "starting" ? startingCardHtml(rec) : deviceCardHtml(rec);
  }

  // ---- progress ------------------------------------------------------------------

  function recordingPlatform() {
    if (view.recording) return view.recording.platform === "ios" ? "ios" : "android";
    const platform = panelRoot()?.querySelector("[data-recording-platform]")?.value;
    return platform === "ios" ? "ios" : "android";
  }

  // What the workspace says: recordings saved, the latest split and whether its
  // parts are compiled, the compiled segments, and the scenario plan with
  // each segment's status.
  async function loadFlow() {
    if (!view.agentId) return;
    const platform = recordingPlatform();
    const key = `${view.agentId}|${platform}|${view.recording ? view.recording.app : ""}`;
    try {
      const [recordings, segments, scenarios] = await Promise.all([
        listWorkspace(RECORDINGS_DIR),
        listWorkspace(`${SEGMENTS_DIR}/${platform}`),
        listWorkspace(SCENARIOS_DIR),
      ]);
      const files = (items) => items.filter((item) => item.is_file !== false);
      const segmentNames = files(segments).filter((item) => /\.ya?ml$/i.test(item.name || "")).map((item) => String(item.name).replace(/\.ya?ml$/i, ""));
      const splits = files(recordings).filter((item) => /\.split\.json$/.test(item.name || ""));
      splits.sort((a, b) => String(b.modified_at || "").localeCompare(String(a.modified_at || "")));
      let split = null;
      for (const item of splits) {
        try {
          const doc = JSON.parse(await readWorkspaceText(`${RECORDINGS_DIR}/${item.name}`));
          if (doc.platform && doc.platform !== platform) continue;
          const parts = (Array.isArray(doc.parts) ? doc.parts : []).map((part) => String(part.segment || "")).filter(Boolean);
          split = { name: item.name, parts, compiled: parts.filter((name) => segmentNames.includes(name)) };
          break;
        } catch (_error) {
          /* a split the assistant is still writing */
        }
      }
      const plans = await readPlans(scenarios);
      const app = view.recording ? view.recording.app : "";
      const plan = plans.find((doc) => app && doc.apps && doc.apps[platform] === app) || plans[0] || null;
      view.flow = {
        key,
        platform,
        recordings: files(recordings).filter((item) => /\.wdlog\.json$/.test(item.name || "")).length,
        segments: segmentNames,
        split,
        plan: plan ? {
          key: plan.__key,
          segments: plan.segments.map((seg) => ({ name: String(seg.name || ""), status: seg.status && typeof seg.status === "object" ? String(seg.status[platform] || "") : String(seg.status || "") })).filter((seg) => seg.name),
          pipeline: plan.pipeline && typeof plan.pipeline === "object" ? plan.pipeline : {},
        } : null,
      };
    } catch (_error) {
      view.flow = { key, platform, error: true, recordings: 0, segments: [], split: null, plan: null };
    }
    renderFlow();
    renderReplay();
    renderIcons();
  }

  // The scenario plans in the workspace, newest first.
  async function readPlans(scenarios) {
    const plans = [];
    const dirs = scenarios.filter((item) => item.is_dir || item.type === "directory").slice(0, 8);
    for (const dir of dirs) {
      try {
        const doc = JSON.parse(await readWorkspaceText(`${SCENARIOS_DIR}/${dir.name}/scenarios.json`));
        if (doc && Array.isArray(doc.segments)) plans.push(Object.assign({ __key: dir.name }, doc));
      } catch (_error) {
        /* not a plan */
      }
    }
    plans.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    return plans;
  }

  // A plan segment's status on a platform.
  function planStatus(plan, name, platform) {
    const seg = plan && Array.isArray(plan.segments) ? plan.segments.find((item) => String(item.name || "") === name) : null;
    if (!seg) return "";
    return seg.status && typeof seg.status === "object" ? String(seg.status[platform] || "") : String(seg.status || "");
  }

  // The four steps after a device is up, each with where it stands.
  function flowSteps() {
    const flow = view.flow || { recordings: 0, segments: [], split: null, plan: null };
    const rec = view.recording;
    const unsaved = rec ? Number(rec.summary && rec.summary.actions) || 0 : 0;
    const savedNow = view.done.length;
    const record = {};
    if (!rec) record.text = flow.recordings ? `${flow.recordings} saved` : "start a device";
    else if (savedNow) record.text = `${savedNow} saved${unsaved ? `, ${unsaved} not saved` : ""}`;
    else record.text = unsaved ? `${unsaved} ${unsaved === 1 ? "action" : "actions"} not saved` : "record in the Inspector";
    record.done = savedNow > 0 || (!rec && flow.recordings > 0);

    const split = {};
    const planned = flow.plan ? flow.plan.segments.filter((seg) => seg.status !== "to_record") : [];
    if (flow.split && flow.split.parts.length) {
      const all = flow.split.compiled.length === flow.split.parts.length;
      split.text = all ? `${flow.split.parts.length} segments` : "approve the split in the chat";
      split.done = all;
    } else if (flow.segments.length) {
      split.text = `${flow.segments.length} ${flow.segments.length === 1 ? "segment" : "segments"}`;
      split.done = true;
    } else if (record.done) {
      split.text = "the assistant proposes it in the chat";
    } else {
      split.text = "after recording";
    }

    const replay = {};
    const lastResult = view.replay.result && view.replay.result.result ? view.replay.result.result.report : null;
    if (planned.length) {
      const passed = planned.filter((seg) => seg.status === "replayed").length;
      replay.text = passed === planned.length ? "all passed" : `${passed} of ${planned.length} passed`;
      replay.done = passed === planned.length;
    } else if (lastResult && Array.isArray(lastResult.segments)) {
      const passed = lastResult.segments.filter((seg) => seg.status === "passed").length;
      replay.text = passed === lastResult.segments.length ? "all passed" : `${passed} of ${lastResult.segments.length} passed`;
      replay.done = passed === lastResult.segments.length;
    } else if (split.done) {
      replay.text = "replay the segments";
    } else {
      replay.text = "after the split";
    }

    const scripts = {};
    const pipeline = flow.plan ? flow.plan.pipeline : {};
    if (pipeline.pull_request) {
      scripts.text = "pull request opened";
      scripts.done = true;
    } else if (pipeline.branch) {
      scripts.text = `exported to ${pipeline.branch}`;
      scripts.done = true;
    } else if (replay.done) {
      scripts.text = "ask the assistant in the chat";
    } else {
      scripts.text = "after the replay";
    }

    const steps = [
      { label: "Record", ...record },
      { label: "Split", ...split },
      { label: "Replay", ...replay },
      { label: "Scripts", ...scripts },
    ];
    let current = true;
    return steps.map((step) => {
      let state = "todo";
      if (step.done) state = "done";
      else if (current) {
        state = "current";
        current = false;
      }
      return { label: step.label, text: step.text, state };
    });
  }

  function renderFlow() {
    const target = panelRoot()?.querySelector("[data-recording-flow]");
    if (!target) return;
    if (view.tab !== "session" || (!view.recording && !(view.flow && (view.flow.recordings || view.flow.segments.length)))) {
      target.innerHTML = "";
      return;
    }
    target.innerHTML = `<ol class="efp-flow">${flowSteps().map((step) => `
      <li class="efp-flow-step is-${step.state}"><span class="efp-flow-label">${esc(step.label)}</span><span class="efp-flow-text">${esc(step.text)}</span></li>`).join("")}</ol>`;
  }

  // ---- Record ------------------------------------------------------------------------

  function recordHtml(rec) {
    const inspector = inspectorAvailable();
    const replaying = Boolean(rec.replay && rec.replay.status === "running");
    const planned = view.planned.length > 0;
    const name = rec.segment || view.planned[view.done.length] || "recording-1";
    const unsaved = view.unsaved
      ? `<div class="portal-inline-state is-visible is-error">Recording ${esc(view.unsaved.name)} is not in the assistant's workspace yet: ${esc(view.unsaved.error)}
          <div class="efp-card-actions">
            <button type="button" class="portal-btn is-secondary" data-recording-action="save-again">Save again</button>
            <button type="button" class="portal-btn is-secondary" data-recording-action="download-log">Download it</button>
          </div>
        </div>`
      : "";
    const doneList = view.done.length
      ? `<ul class="efp-recording-done">${view.done.map((item) => `<li><i data-lucide="check" class="w-3 h-3"></i> ${esc(item.name)} <span class="efp-card-meta">${esc(item.detail)}</span></li>`).join("")}</ul>`
      : "";
    return `
      <div class="efp-mobile-section-head">
        <h5>Record</h5>
        <span class="efp-card-meta" data-recording-summary>${esc(summaryText(rec.summary))}</span>
      </div>
      <p class="portal-inline-note">${planned
        ? "Save after each segment you listed; the name moves on to the next one."
        : "Record the whole scenario in the Inspector, then save it. The assistant proposes how to split it into segments in the chat."}</p>
      <div class="efp-record-name">
        <label class="portal-form-label"><span class="portal-form-label">${planned ? "Segment" : "Save as"}</span>
          <input class="portal-form-input" data-recording-name value="${esc(name)}" autocomplete="off" spellcheck="false" />
        </label>
      </div>
      <div class="efp-card-actions">
        ${inspector ? `<button type="button" class="portal-btn is-primary" data-recording-action="open-inspector"><i data-lucide="external-link" class="w-4 h-4"></i>Open Inspector</button>` : ""}
        <button type="button" class="portal-btn ${inspector ? "is-secondary" : "is-primary"}" data-recording-action="save-recording"${replaying ? " disabled" : ""}><i data-lucide="save" class="w-4 h-4"></i>Save recording</button>
      </div>
      ${unsaved}
      ${doneList}
      <details class="portal-collapsible"${inspector ? "" : " open"}>
        <summary class="portal-collapsible-summary"><span>Other ways to record</span></summary>
        <div class="portal-panel-stack">
          <p class="portal-inline-note">The desktop Appium Inspector, or the Inspector plugin of an Appium server on this computer, attaches to the device through the bridge with the connection details in the device menu.</p>
          <p class="portal-inline-note">Code from Appium Inspector's recorder compiles too (Python is easiest), as one segment under the name above.</p>
          <div class="efp-card-actions">
            <input type="file" accept="${CODE_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-code />
            <button type="button" class="portal-btn is-secondary" data-recording-action="upload-code"><i data-lucide="upload" class="w-4 h-4"></i>Upload recorded code</button>
          </div>
        </div>
      </details>`;
  }

  function renderRecord() {
    const target = panelRoot()?.querySelector("[data-recording-record]");
    if (!target) return;
    const rec = view.recording;
    if (!rec || rec.status !== "active" || view.tab !== "session") {
      target.innerHTML = "";
      target.classList.add("hidden");
      view.sessionKey = "";
      return;
    }
    target.classList.remove("hidden");
    const summary = target.querySelector("[data-recording-summary]");
    if (summary) summary.textContent = summaryText(rec.summary);
    // Re-render only when the recording changes, so typing in the name field
    // is not interrupted by the poll.
    const key = [rec.id, rec.session_id, rec.status, rec.segment, view.done.length, view.unsaved ? view.unsaved.name : "", view.planned.length, inspectorAvailable(), rec.replay ? rec.replay.status : ""].join("|");
    if (key === view.sessionKey) return;
    const focused = document.activeElement && document.activeElement.matches && document.activeElement.matches("[data-recording-name]");
    const typedName = focused ? document.activeElement.value : null;
    view.sessionKey = key;
    target.innerHTML = recordHtml(rec);
    if (typedName !== null) {
      const input = target.querySelector("[data-recording-name]");
      if (input) {
        input.value = typedName;
        input.focus();
      }
    }
  }

  // ---- Settings ----------------------------------------------------------------------

  function settingsHtml() {
    const problem = view.config && view.config.configured ? bridgeProblem(view.bridge) : "";
    const bridge = !view.bridge
      ? "Looking for the local bridge…"
      : (problem ? BRIDGE_MESSAGES[problem] : `Running on 127.0.0.1:${view.bridge.port}${view.bridge.version ? ` (version ${view.bridge.version})` : ""}.`);
    return `
      <div class="efp-mobile-section-head"><h5>Settings</h5></div>
      <div class="efp-settings-group">
        <h6>Local bridge</h6>
        <p class="efp-card-meta">${esc(bridge)}</p>
        <div class="efp-card-actions">
          ${problem === "not_running" ? `<button type="button" class="portal-btn is-secondary" data-recording-action="start-bridge"${view.busy === "bridge" ? " disabled" : ""}><i data-lucide="plug" class="w-4 h-4"></i>Start bridge</button>` : ""}
          <button type="button" class="portal-btn is-secondary" data-recording-action="check-bridge"><i data-lucide="refresh-cw" class="w-4 h-4"></i>Check again</button>
          <a class="portal-link-inline" href="#/connectors/local_bridge">Connectors &gt; Local bridge</a>
        </div>
      </div>
      ${inspectorAvailable() ? `
      <div class="efp-settings-group">
        <h6>Appium Inspector</h6>
        <label class="efp-replay-row"><input type="checkbox" data-recording-auto-inspector${autoInspector() ? " checked" : ""} /><span>Open the Inspector in a new tab when the device is ready</span></label>
        <p class="portal-inline-note">Portal hosts the Inspector; it attaches to the device through the bridge on this computer. The device menu opens it inside Portal instead.</p>
      </div>` : `
      <div class="efp-settings-group">
        <h6>Appium Inspector</h6>
        <p class="portal-inline-note">This Portal does not host the Inspector, so recording uses the desktop Appium Inspector with the connection details in the device menu. An administrator can bundle it in the Portal image.</p>
      </div>`}
      <div class="efp-settings-group">
        <h6>Proxy to BrowserStack from this computer</h6>
        <label class="portal-form-label"><span class="portal-form-label">Proxy (optional)</span>
          <input class="portal-form-input" data-bridge-proxy="url" placeholder="http://proxy.example.com:8080" autocomplete="off" spellcheck="false" />
        </label>
        <div class="grid grid-cols-2 gap-3">
          <label class="portal-form-label"><span class="portal-form-label">Proxy user name</span>
            <input class="portal-form-input" data-bridge-proxy="username" placeholder="Optional" autocomplete="off" spellcheck="false" />
          </label>
          <label class="portal-form-label"><span class="portal-form-label">Proxy password</span>
            <input type="password" class="portal-form-input" data-bridge-proxy="password" placeholder="Optional" autocomplete="new-password" />
          </label>
        </div>
        <p class="portal-inline-note">Empty uses this computer's proxy settings. Saved in this browser only. A proxy that asks for a login takes it in the two fields, a domain user as DOMAIN\\user; the password stays hidden on screen.</p>
      </div>
      <div class="efp-settings-group" data-recording-upload>
        <h6>Upload a build to BrowserStack</h6>
        <input type="file" accept="${BUILD_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-build />
        <label class="portal-form-label"><span class="portal-form-label">Custom id (optional)</span>
          <input class="portal-form-input" data-recording-custom-id placeholder="fxapp-android-uat" autocomplete="off" spellcheck="false" />
        </label>
        <p class="portal-inline-note">A custom id names the latest build uploaded with it, so recordings and the pipeline keep using the same name. BrowserStack keeps builds for 30 days.</p>
        <div class="portal-progress hidden" data-recording-upload-progress><i></i></div>
        <div class="efp-card-actions"><button type="button" class="portal-btn is-secondary" data-recording-action="upload-build"><i data-lucide="upload" class="w-4 h-4"></i>Upload to BrowserStack</button></div>
      </div>`;
  }

  function renderSettings() {
    const target = panelRoot()?.querySelector("[data-recording-settings]");
    if (!target) return;
    target.classList.toggle("hidden", !view.settingsOpen);
    if (!view.settingsOpen) {
      target.innerHTML = "";
      return;
    }
    if (target.querySelector("[data-recording-build]")) return;
    target.innerHTML = settingsHtml();
    fillProxyFields(target);
    if (typeof window.initPasswordToggles === "function") window.initPasswordToggles(target);
  }

  function toggleSettings(open) {
    view.settingsOpen = typeof open === "boolean" ? open : !view.settingsOpen;
    renderBar();
    renderSettings();
    renderIcons();
  }

  // ---- loading -----------------------------------------------------------------------

  async function loadConfig() {
    try {
      const response = await fetch(`/api/mobile/recording-config?agent_id=${encodeURIComponent(view.agentId)}`, { credentials: "same-origin", cache: "no-store" });
      if (response.ok) {
        view.config = await response.json();
      } else {
        view.config = {
          configured: false,
          problem: response.status === 403 ? "Only the assistant's owner can record on it." : `Could not load your BrowserStack settings (HTTP ${response.status}).`,
        };
      }
    } catch (_error) {
      view.config = { configured: false, problem: "Could not load your BrowserStack settings." };
    }
  }

  async function loadApps(select) {
    if (!ready()) return;
    if (select) view.selectedApp = select;
    try {
      const data = await call("apps.list", {}, { credentials: credentials(), timeoutMs: 45000 });
      view.apps = Array.isArray(data.apps) ? data.apps : [];
      if (view.appsError) setStatus("", "");
      view.appsError = "";
    } catch (error) {
      view.apps = null;
      view.appsError = errorText(error);
      setStatus(`Could not list your builds: ${view.appsError}`, "error");
    }
    renderApps();
  }

  // Picks up the recording this panel started before a reload, and lists the
  // ones other panels left holding a device.
  async function loadRecordings() {
    if (bridgeProblem(view.bridge)) return;
    let recordings = [];
    try {
      const data = await call("recordings.list", {}, { timeoutMs: 8000 });
      recordings = Array.isArray(data.recordings) ? data.recordings : [];
    } catch (_error) {
      return;
    }
    const mine = remembered();
    if (!view.recording && mine) {
      const found = recordings.find((rec) => rec.id === mine.id);
      if (found) {
        view.recording = found;
        view.planned = Array.isArray(mine.planned) ? mine.planned : [];
        view.done = Array.isArray(mine.done) ? mine.done : [];
        view.replay.saved = Array.isArray(mine.replays) ? mine.replays : [];
      } else {
        writeStorage(storageKey(), "");
      }
    }
    if (view.recording) {
      const found = recordings.find((rec) => rec.id === view.recording.id);
      if (found) view.recording = Object.assign({}, view.recording, found);
    }
    const current = view.recording ? view.recording.id : "";
    view.others = recordings.filter((rec) => rec.id !== current);
  }

  async function refreshAll() {
    await Promise.all([loadConfig(), probe({ force: true }).then((state) => { view.bridge = state; })]);
    renderBar();
    renderSettings();
    if (!bridgeProblem(view.bridge)) {
      // A held recording shows up without waiting for the build list.
      await Promise.all([
        ready() ? loadApps() : Promise.resolve(),
        loadRecordings().then(() => renderSession()),
      ]);
    }
    renderAll();
    loadFlow();
    if (view.tab === "library") loadLibrary();
  }

  function recordingGone(message) {
    view.recording = null;
    view.unsaved = null;
    stopReplayPolling();
    view.replay = freshReplay();
    remember();
    closeWorkspace({ rerender: false });
    setStatus(message || "The local bridge no longer holds this recording (it was finished elsewhere, or the bridge restarted). Start a new one when you are ready.", "warning");
    renderAll();
  }

  async function poll() {
    if (bridgeProblem(view.bridge)) {
      view.bridge = await probe({ force: true });
      if (!bridgeProblem(view.bridge)) await refreshAll();
      return;
    }
    if (!view.recording) {
      await loadRecordings();
      renderOthers();
      renderSession();
      return;
    }
    view.polls += 1;
    const id = view.recording.id;
    try {
      const data = await call("recordings.list", {}, { timeoutMs: 8000 });
      const recordings = Array.isArray(data.recordings) ? data.recordings : [];
      const found = recordings.find((rec) => rec.id === id);
      if (!found) {
        recordingGone();
        return;
      }
      view.recording = Object.assign({}, view.recording, found);
      view.others = recordings.filter((rec) => rec.id !== id);
      // A recording found starting (after a reload, say) is asked after on
      // every poll until the device is up.
      if (view.polls % STATUS_EVERY === 0 || found.status === "starting") {
        const status = await call("session.status", { id }, { credentials: credentials(), timeoutMs: 30000 });
        if (view.recording && view.recording.id === id) view.recording = Object.assign({}, view.recording, status);
        if (status.status === "failed") {
          recordingGone(startFailureText(status));
          return;
        }
      }
    } catch (error) {
      if (error.code === "not_found") {
        recordingGone();
        return;
      }
      if (error.code === "bridge_unreachable") {
        view.bridge = await probe({ force: true });
        renderBar();
        setStatus("Lost the local bridge. Start it again to carry on; BrowserStack releases the device about 5 minutes after the last command.", "warning");
        return;
      }
    }
    renderOthers();
    renderSession();
  }

  function schedule() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(async () => {
      if (!panelRoot() || currentAgentId() !== view.agentId) return;
      if (document.visibilityState !== "hidden" && !view.busy) {
        try {
          await poll();
        } catch (_error) {
          /* the next poll tries again */
        }
      }
      schedule();
    }, POLL_MS);
    if (!tickTimer) {
      tickTimer = window.setInterval(() => {
        const root = panelRoot();
        if (!root) {
          window.clearInterval(tickTimer);
          tickTimer = 0;
          return;
        }
        root.querySelectorAll("[data-recording-countdown]").forEach((el) => {
          el.textContent = el.dataset.since ? elapsedText(Number(el.dataset.since)) : remaining(el.dataset.deadline);
        });
      }, 1000);
    }
  }

  async function openRecordingPanel() {
    const agentId = currentAgentId();
    const show = typeof window.setToolPanel === "function" ? window.setToolPanel : null;
    if (!show) return;
    if (!agentId) {
      show(PANEL_TITLE, "<div class='portal-inline-state is-visible'>Select an assistant first.</div>", "recording");
      return;
    }
    if (view.workspace && view.agentId === agentId) {
      // The panel lives in the Inspector workspace while that is open.
      view.workspace.focus();
      return;
    }
    if (view.agentId !== agentId) {
      closeWorkspace({ rerender: false });
      Object.assign(view, {
        agentId, config: null, bridge: null, apps: null, appsError: "", selectedApp: "",
        recording: null, others: [], planned: [], done: [], unsaved: null, busy: "", sessionKey: "", formKey: "", polls: 0,
        replay: freshReplay(), flow: null, settingsOpen: false, connectionOpen: false, inspectorTab: null,
        tab: "session", library: freshLibrary(),
      });
    } else {
      view.sessionKey = "";
      view.formKey = "";
    }
    show(PANEL_TITLE, shellHtml(), "recording");
    renderAll();
    await refreshAll();
    schedule();
  }

  async function startBridge() {
    view.busy = "bridge";
    renderBar();
    renderSettings();
    setStatus("Starting the local bridge… allow the efp-bridge link if Chrome asks.", "");
    try {
      view.bridge = await launchAndWait();
    } finally {
      view.busy = "";
    }
    if (bridgeProblem(view.bridge)) {
      setStatus("The bridge did not start. Install it from Connectors > Local bridge, or start it from there and check again.", "error");
      renderBar();
      renderSettings();
      return;
    }
    setStatus("", "");
    await refreshAll();
  }

  // ---- starting a device ------------------------------------------------------------------

  // A tab opened while the member's click is still being handled is not
  // blocked as a pop-up; it waits for the device and then lands in the
  // Inspector.
  function openInspectorTabEarly() {
    if (!inspectorAvailable() || !autoInspector()) return null;
    let tab = null;
    try {
      tab = window.open("", "_blank");
      if (tab) {
        tab.document.write(`<!doctype html><title>Appium Inspector</title><body style="margin:0;font-family:system-ui,sans-serif;color:#333;display:flex;align-items:center;justify-content:center;height:100vh"><p>Starting the BrowserStack device… this tab opens Appium Inspector once it is ready.</p></body>`);
        tab.document.close();
      }
    } catch (_error) {
      tab = null;
    }
    return tab;
  }

  function landInspectorTab() {
    const tab = view.inspectorTab;
    view.inspectorTab = null;
    if (!tab || tab.closed) return false;
    try {
      tab.location.href = inspectorUrl();
      tab.focus();
      return true;
    } catch (_error) {
      return false;
    }
  }

  function closeInspectorTab() {
    const tab = view.inspectorTab;
    view.inspectorTab = null;
    if (tab && !tab.closed) {
      try {
        tab.close();
      } catch (_error) {
        /* the member closes it */
      }
    }
  }

  async function startRecording(root, button) {
    if (!ensureReady()) return;
    const select = root.querySelector("[data-recording-app]");
    const app = select ? select.value : "";
    if (!app) {
      setStatus("Pick a build, or upload one in Settings first.", "error");
      return;
    }
    const platform = root.querySelector("[data-recording-platform]")?.value || "android";
    const device = (root.querySelector("[data-recording-device-name]")?.value || "").trim();
    view.planned = parseSegments(root.querySelector("[data-recording-segments]")?.value);
    view.done = [];
    const defaults = (view.config && view.config.defaults) || {};
    const params = { app, platform, segment: view.planned[0] || "recording-1" };
    if (device) params.device = device;
    if (defaults.network) params.network = defaults.network;
    if (Number(defaults.idle_timeout_seconds) > 0) params.idle_timeout_seconds = Number(defaults.idle_timeout_seconds);
    if (typeof defaults.video === "boolean") params.video = defaults.video;
    if (typeof defaults.interactive_debugging === "boolean") params.interactive_debugging = defaults.interactive_debugging;
    if (defaults.appium_version) params.appium_version = String(defaults.appium_version);
    closeInspectorTab();
    view.inspectorTab = openInspectorTabEarly();
    view.busy = "start";
    if (button) button.disabled = true;
    setStatus("Starting a BrowserStack device… about a minute, longer when all your parallel sessions are busy.", "");
    try {
      const started = await call("session.start", params, { credentials: credentials(), timeoutMs: 60000 });
      view.recording = started;
      view.unsaved = null;
      view.replay = freshReplay();
      view.connectionOpen = false;
      remember();
      renderAll();
      const recording = started.status === "starting" ? await waitForDevice(started.id) : started;
      const lines = [
        "/record-mobile-segment",
        "Recording on my computer through the Mobile testing panel.",
        `App: ${recording.app || app} (${recording.platform || platform})`,
        recording.device ? `Device: ${recording.device}${recording.os_version ? ` ${recording.os_version}` : ""}` : "",
        view.planned.length ? `Segments: ${view.planned.join(", ")}` : "",
      ].filter(Boolean);
      sendChat(lines.join("\n"));
      const what = view.planned.length ? recording.segment : "the scenario";
      if (landInspectorTab()) setStatus(`Device ready. The Inspector opened in its own tab; record ${what} there, then press Save recording.`, "success");
      else if (inspectorAvailable()) setStatus(`Device ready. Open the Inspector and record ${what}.`, "success");
      else setStatus(`Device ready. Attach the desktop Inspector with the connection details in the device menu and record ${what}.`, "success");
    } catch (error) {
      closeInspectorTab();
      if (error.code !== "start_timeout") {
        // The bridge no longer lists a failed start; nothing to keep.
        view.recording = null;
        remember();
      }
      setStatus(errorText(error), "error");
    } finally {
      view.busy = "";
      if (button) button.disabled = false;
    }
    await loadRecordings();
    renderAll();
    loadFlow();
  }

  function startFailureText(status) {
    const error = status && status.error ? status.error : {};
    return errorText(bridgeError({ code: error.code || "start_failed", message: error.message || "The device did not start.", hint: error.hint }));
  }

  // Asks the bridge after a starting recording every few seconds until the
  // device is up, showing the bridge's progress meanwhile.
  async function waitForDevice(id, { pollMs = START_POLL_MS, timeoutMs = START_TIMEOUT_MS } = {}) {
    const since = Date.now();
    for (;;) {
      const status = await call("session.status", { id }, { credentials: credentials(), timeoutMs: 30000 });
      if (view.recording && view.recording.id === id) view.recording = Object.assign({}, view.recording, status);
      if (status.status === "active") return status;
      if (status.status !== "starting") throw bridgeError(status.error ? { code: status.error.code || "start_failed", message: status.error.message || "The device did not start.", hint: status.error.hint } : { code: "start_failed", message: "The device did not start." });
      if (Date.now() - since > timeoutMs) throw bridgeError({ code: "start_timeout", message: "The device has not started after 16 minutes. Cancel this recording and try again, or wait: the panel keeps asking." });
      setStatus(`${status.progress || "Starting a BrowserStack device"}… ${elapsedText(since)}. About a minute; longer when all your parallel sessions are busy.`, "");
      renderSession();
      await sleep(pollMs);
    }
  }

  // ---- the Inspector ----------------------------------------------------------------------

  function inspectorUrl() {
    const rec = view.recording;
    const state = {
      serverType: "remote",
      server: { remote: { hostname: "127.0.0.1", port: view.bridge.port, path: `/mobile/wd/${rec.id}`, ssl: false } },
      attachSessId: rec.session_id,
    };
    // The Inspector reads ?state= into its session builder and, with
    // autoStart=1 and a session id, attaches straight away.
    return `/inspector/?state=${encodeURIComponent(JSON.stringify(state))}&autoStart=1`;
  }

  function openInspector() {
    if (!view.recording || !view.bridge || !view.bridge.port) {
      setStatus("Start the local bridge and a recording device first.", "error");
      return;
    }
    const url = inspectorUrl();
    const tab = window.open(url, "_blank");
    if (tab) {
      try {
        tab.opener = null;
      } catch (_error) {
        /* cross-origin already */
      }
      setStatus("Inspector opened, attached to the device. Tap and type there, then press Save recording.", "success");
      return;
    }
    setStatus("Your browser blocked the new tab. Open the Inspector from this link, then press Save recording when you are done.", "warning");
    const el = panelRoot()?.querySelector("[data-recording-status]");
    if (el) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "portal-link-inline";
      link.textContent = " Open Appium Inspector";
      el.appendChild(link);
    }
  }

  // The workspace holds the Inspector inside Portal, with the panel beside
  // it: the panel's root moves into the overlay and back.
  function openWorkspace() {
    if (!view.recording || !view.bridge || !view.bridge.port || !inspectorAvailable()) {
      setStatus("Start the local bridge and a recording device first.", "error");
      return;
    }
    const root = panelRoot();
    if (!root) return;
    const url = inspectorUrl();
    if (view.workspace) {
      const frame = view.workspace.querySelector("iframe");
      if (frame && frame.getAttribute("src") !== url) frame.setAttribute("src", url);
      view.workspace.focus();
      return;
    }
    const rec = view.recording;
    const overlay = document.createElement("div");
    overlay.className = "efp-workspace";
    overlay.setAttribute("data-recording-workspace", "");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Appium Inspector");
    overlay.tabIndex = -1;
    overlay.innerHTML = `
      <div class="efp-workspace-head">
        <strong>Appium Inspector</strong>
        <span class="efp-card-meta">${esc(rec.device || "BrowserStack device")}${rec.os_version ? ` · ${esc(platformLabel(rec))} ${esc(rec.os_version)}` : ""}</span>
        <span class="efp-workspace-head-actions">
          <button type="button" class="portal-btn is-secondary" data-recording-action="open-inspector"><i data-lucide="external-link" class="w-4 h-4"></i>Open in a new tab</button>
          <button type="button" class="portal-btn is-secondary" data-recording-action="close-workspace"><i data-lucide="arrow-left" class="w-4 h-4"></i>Back to chat</button>
        </span>
      </div>
      <div class="efp-workspace-body">
        <iframe class="efp-workspace-frame" src="${esc(url)}" title="Appium Inspector" allow="clipboard-read; clipboard-write"></iframe>
        <aside class="efp-workspace-side"></aside>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector(".efp-workspace-side").appendChild(root);
    document.body.classList.add("efp-workspace-open");
    view.workspace = overlay;
    view.connectionOpen = false;
    renderSession();
    renderIcons();
    overlay.focus();
  }

  // Closes the workspace; the panel goes back to the tool panel.
  function closeWorkspace({ rerender = true } = {}) {
    const overlay = view.workspace;
    if (!overlay) return;
    view.workspace = null;
    overlay.remove();
    document.body.classList.remove("efp-workspace-open");
    if (rerender) openRecordingPanel();
  }

  function currentName(root) {
    return (root.querySelector("[data-recording-name]")?.value || "").trim();
  }

  async function writeToWorkspace(fileName, blob, dir = RECORDINGS_DIR) {
    const form = new FormData();
    form.append("path", dir);
    form.append("file", new File([blob], fileName, { type: blob.type || "application/octet-stream" }));
    const response = await fetch(`/a/${encodeURIComponent(view.agentId)}/api/server-files/upload`, { method: "POST", credentials: "same-origin", body: form });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = body && (typeof body.detail === "string" ? body.detail : body.error);
      throw new Error(detail ? String(detail) : `HTTP ${response.status}`);
    }
    return `${dir}/${fileName}`;
  }

  // ---- Replay ---------------------------------------------------------------
  //
  // Compiled segments replay on the device this recording holds, before the
  // assistant generates scripts from them. The panel lists the segments in
  // the assistant's workspace for the recording's platform (the parts of the
  // latest split first, ticked), asks for the values of the secrets they
  // type (kept in this page's memory only and sent to the bridge alone), and
  // has the bridge replay them: it borrows the device from the Inspector and
  // gives it back when the replay ends. The result goes into the workspace
  // under mobile/replays/<id>/, and the chat message tells the assistant,
  // which reviews it and answers with the result card.

  function freshReplay() {
    return {
      segments: null, loading: false, error: "", checked: [], touched: false, start: "restart",
      secretNames: [], extraSecretNames: [], secrets: {}, yaml: {}, running: null, polling: false,
      result: null, saved: [],
    };
  }

  function workspaceApi(path) {
    return `/a/${encodeURIComponent(view.agentId)}/api/server-files${path}`;
  }

  async function listWorkspace(dir) {
    const response = await fetch(workspaceApi(`?path=${encodeURIComponent(dir)}`), { credentials: "same-origin", cache: "no-store" });
    if (response.status === 404) return [];
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    return Array.isArray(body.items) ? body.items : [];
  }

  async function readWorkspaceText(path) {
    const response = await fetch(workspaceApi(`/read?path=${encodeURIComponent(path)}`), { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    const body = await response.json();
    if (body.truncated) throw new Error(`${path} is too large to read`);
    return String(body.content || "");
  }

  // The secrets a segment types: those its secrets list names, and every
  // text_env of its steps.
  function segmentSecrets(yaml) {
    const names = new Set();
    const lines = String(yaml || "").split(/\r?\n/);
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const flow = line.match(/^secrets:\s*\[(.*)\]\s*$/);
      if (flow) {
        flow[1].split(",").map((item) => item.trim().replace(/^["']|["']$/g, "")).filter(Boolean).forEach((name) => names.add(name));
        continue;
      }
      if (/^secrets:\s*$/.test(line)) {
        for (let j = i + 1; j < lines.length; j += 1) {
          const item = lines[j].match(/^\s+-\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*$/);
          if (!item) break;
          names.add(item[1]);
        }
        continue;
      }
      const env = line.match(/(?:^|[\s{,-])text_env:\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?/);
      if (env) names.add(env[1]);
    }
    return [...names].sort();
  }

  async function loadReplaySegments() {
    const rec = view.recording;
    if (!rec || !view.agentId) return;
    const r = view.replay;
    r.loading = true;
    renderReplay();
    try {
      await loadFlow();
      const flow = view.flow || { segments: [], split: null };
      const order = flow.split ? flow.split.parts : [];
      const rank = (name) => {
        const index = order.indexOf(name);
        return index >= 0 ? index : order.length;
      };
      const platform = recordingPlatform();
      const segments = flow.segments.map((name) => ({ name, path: `${SEGMENTS_DIR}/${platform}/${name}.yaml` }));
      segments.sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name));
      r.segments = segments;
      if (!r.touched) r.checked = order.filter((name) => segments.some((seg) => seg.name === name));
      else r.checked = r.checked.filter((name) => segments.some((seg) => seg.name === name));
      if (!r.touched && !r.checked.length) r.checked = segments.map((seg) => seg.name);
      r.error = flow.error ? "Could not list the compiled segments. Is the assistant running?" : "";
      await loadReplaySecrets();
    } catch (error) {
      r.error = `Could not list the compiled segments: ${errorText(error)}`;
    }
    r.loading = false;
    renderReplay();
    renderIcons();
  }

  function selectedSegments() {
    const r = view.replay;
    return (r.segments || []).filter((seg) => r.checked.includes(seg.name));
  }

  async function segmentYaml(seg) {
    if (!(seg.path in view.replay.yaml)) view.replay.yaml[seg.path] = await readWorkspaceText(seg.path);
    return view.replay.yaml[seg.path];
  }

  async function loadReplaySecrets() {
    const r = view.replay;
    const names = new Set(r.extraSecretNames);
    // A segment that cannot be read leaves it to the bridge to name what is
    // missing.
    const found = await Promise.all(selectedSegments().map((seg) => segmentYaml(seg).then(segmentSecrets, () => [])));
    found.forEach((list) => list.forEach((name) => names.add(name)));
    r.secretNames = [...names].sort();
  }

  function base64Blob(value, type) {
    const raw = atob(String(value || ""));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    return new Blob([bytes], { type });
  }

  function mimeFor(path) {
    if (/\.png$/i.test(path)) return "image/png";
    if (/\.xml$/i.test(path)) return "application/xml";
    return "application/octet-stream";
  }

  // The chat message that hands a finished replay to the assistant.
  function replayMessage(report, path) {
    const segments = Array.isArray(report && report.segments) ? report.segments : [];
    const passed = segments.filter((seg) => seg.status === "passed").length;
    let line = `Replay finished: ${passed} of ${segments.length} ${segments.length === 1 ? "segment" : "segments"} passed`;
    const failure = report && report.failure;
    if (report && report.status === "failed" && failure && failure.segment) line += `; ${failure.segment} failed at step ${failure.step || "?"}`;
    return `${line}.\nReport: ${path}`;
  }

  // What the list says next to a segment: this session's last replay first,
  // else the plan's status.
  function segmentMark(name) {
    const res = view.replay.result;
    const report = res && res.result ? res.result.report : null;
    const seg = report && Array.isArray(report.segments) ? report.segments.find((item) => item.name === name) : null;
    if (seg && seg.status === "passed") return { icon: "check", tone: "success", text: "passed" };
    if (seg && seg.status === "failed") {
      const failed = (seg.steps || []).find((step) => step.ok === false) || {};
      return { icon: "x", tone: "error", text: `failed at step ${failed.step || "?"}` };
    }
    const planned = view.flow && view.flow.plan ? view.flow.plan.segments.find((item) => item.name === name) : null;
    if (planned && planned.status === "replayed") return { icon: "check", tone: "success", text: "replayed" };
    return null;
  }

  function replayOutcomeHtml() {
    const res = view.replay.result;
    if (!res) return "";
    const replay = res.replay || {};
    const report = (res.result && res.result.report) || {};
    const status = replay.status || report.status || "";
    const tone = status === "passed" ? "success" : (status === "cancelled" ? "warning" : "error");
    const label = { passed: "Replay passed", failed: "Replay failed", cancelled: "Replay stopped", error: "Replay could not run" }[status] || "Replay";
    const failure = report.failure || {};
    const where = status === "failed" && failure.segment ? ` at ${failure.segment}, step ${failure.step || "?"}` : "";
    const session = report.session_url ? ` <a class="portal-link-inline" href="${esc(report.session_url)}" target="_blank" rel="noopener noreferrer">BrowserStack session</a>` : "";
    const error = replay.error ? `<div class="portal-inline-state is-visible is-error">${esc(errorText(replay.error))}</div>` : "";
    let saved = "";
    if (res.path) saved = `<span class="efp-card-meta">The result and screenshots are in the chat; the assistant reviews them.</span>`;
    else if (res.error) saved = `<div class="portal-inline-state is-visible is-error">${esc(res.error)}<div class="efp-card-actions"><button type="button" class="portal-btn is-secondary" data-replay-action="save-again">Save again</button></div></div>`;
    return `<div class="efp-replay-outcome">
        <div><span class="portal-status-badge is-${tone}">${esc(label)}</span>${where ? `<span class="efp-card-meta">${esc(where)}</span>` : ""}${session}</div>
        ${error}
        ${saved}
      </div>`;
  }

  function replayHtml() {
    const r = view.replay;
    const rec = view.recording;
    const running = r.running && r.running.status === "running" ? r.running : null;
    const platform = platformLabel(rec);
    let list = "";
    if (r.loading && !r.segments) list = `<p class="portal-inline-note">Looking for compiled segments…</p>`;
    else if (r.error) list = `<div class="portal-inline-state is-visible is-error">${esc(r.error)}</div>`;
    else if (!r.segments || !r.segments.length) list = `<p class="portal-inline-note">No compiled ${esc(platform)} segments yet. The assistant compiles them after you save a recording and approve its split.</p>`;
    else {
      list = `<div class="efp-replay-list">${r.segments.map((seg) => {
        const mark = segmentMark(seg.name);
        return `<label class="efp-replay-row"><input type="checkbox" data-replay-segment="${esc(seg.name)}"${r.checked.includes(seg.name) ? " checked" : ""}${running ? " disabled" : ""} /><span>${esc(seg.name)}</span>${mark ? `<span class="efp-replay-mark is-${mark.tone}"><i data-lucide="${mark.icon}" class="w-3 h-3"></i>${esc(mark.text)}</span>` : ""}</label>`;
      }).join("")}</div>`;
    }
    const ready = Boolean(r.segments && r.segments.length);
    const starts = [["restart", "Restart the app"]];
    if (rec.platform !== "ios") starts.push(["reset", "Clear the app's data first"]);
    starts.push(["current", "The screen the device shows"]);
    const startHtml = ready
      ? `<label class="efp-replay-start"><span class="efp-card-meta">Start from</span><select class="portal-form-select" data-replay-start${running ? " disabled" : ""}>${starts.map(([value, label]) => `<option value="${value}"${r.start === value ? " selected" : ""}>${esc(label)}</option>`).join("")}</select></label>`
      : "";
    const secretsHtml = ready && r.secretNames.length
      ? `${r.secretNames.map((name) => `<label class="portal-form-label"><span class="portal-form-label">${esc(name)}</span><input type="password" class="portal-form-input" data-replay-secret="${esc(name)}" placeholder="The value the segment types" autocomplete="new-password"${running ? " disabled" : ""} /></label>`).join("")}
        <p class="portal-inline-note">Kept in this page only and sent to the bridge on this computer, never to the assistant.</p>`
      : "";
    let actions;
    if (running) {
      const since = Date.parse(running.started_at || "") || Date.now();
      actions = `<div class="efp-card-meta">Replaying: <span data-replay-progress>${esc(running.progress || "starting")}</span> · <span data-recording-countdown data-since="${esc(String(since))}">${esc(elapsedText(since))}</span></div>
        <p class="portal-inline-note">While it runs, the Inspector can only watch.</p>
        <div class="efp-card-actions"><button type="button" class="portal-btn is-secondary" data-replay-action="stop"><i data-lucide="square" class="w-4 h-4"></i>Stop replay</button></div>`;
    } else {
      actions = `<div class="efp-card-actions">
          <button type="button" class="portal-btn is-primary" data-replay-action="start"${ready ? "" : " disabled"}><i data-lucide="play" class="w-4 h-4"></i>Replay</button>
        </div>`;
    }
    return `<div class="efp-mobile-section-head">
        <h5>Replay</h5>
        ${running ? "" : `<button type="button" class="toolbar-icon-btn" data-replay-action="refresh" title="Look for compiled segments again" aria-label="Refresh"><i data-lucide="refresh-cw" class="w-4 h-4"></i></button>`}
      </div>
      <p class="portal-inline-note">Check the compiled segments on this device before the assistant generates scripts from them. The replay borrows the device from the Inspector and hands it back when it ends.</p>
      ${list}${startHtml}${secretsHtml}${actions}${running ? "" : replayOutcomeHtml()}`;
  }

  function renderReplay() {
    const box = panelRoot()?.querySelector("[data-recording-replay]");
    if (!box) return;
    if (!view.recording || view.recording.status !== "active" || view.tab !== "session") {
      box.innerHTML = "";
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    syncReplay();
    if (view.replay.segments === null && !view.replay.loading) loadReplaySegments();
    const active = document.activeElement;
    const focused = active && active.matches && active.matches("[data-replay-secret]") ? active.dataset.replaySecret : "";
    box.innerHTML = replayHtml();
    box.querySelectorAll("[data-replay-secret]").forEach((input) => {
      input.value = view.replay.secrets[input.dataset.replaySecret] || "";
      if (input.dataset.replaySecret === focused) input.focus();
    });
    if (typeof window.initPasswordToggles === "function") window.initPasswordToggles(box);
  }

  function scheduleReplayPoll() {
    window.clearTimeout(replayTimer);
    view.replay.polling = true;
    replayTimer = window.setTimeout(pollReplay, REPLAY_POLL_MS);
  }

  function stopReplayPolling() {
    window.clearTimeout(replayTimer);
    replayTimer = 0;
    view.replay.polling = false;
  }

  // Picks up a replay this page did not start, or that finished while the
  // page was away: one running is followed, one over and not yet saved is
  // saved.
  function syncReplay() {
    const rec = view.recording;
    const known = rec && rec.replay;
    const r = view.replay;
    if (!known || r.polling) return;
    const shown = r.result && r.result.replay && r.result.replay.id === known.id;
    if (known.status === "running" || (!shown && !r.saved.includes(known.id))) {
      r.polling = true;
      pollReplay();
    }
  }

  async function pollReplay() {
    const rec = view.recording;
    if (!rec || !panelRoot() || currentAgentId() !== view.agentId) {
      view.replay.polling = false;
      return;
    }
    let data;
    try {
      data = await call("replay.status", { id: rec.id }, { timeoutMs: 15000 });
    } catch (error) {
      if (error.code === "no_replay" || error.code === "not_found") {
        view.replay.running = null;
        view.replay.polling = false;
        renderReplay();
        return;
      }
      scheduleReplayPoll();
      return;
    }
    const replay = data.replay || {};
    if (replay.status === "running") {
      const same = view.replay.running && view.replay.running.id === replay.id;
      view.replay.running = replay;
      const progress = panelRoot()?.querySelector("[data-replay-progress]");
      if (same && progress) progress.textContent = replay.progress || "starting";
      else {
        renderReplay();
        renderIcons();
      }
      scheduleReplayPoll();
      return;
    }
    view.replay.running = null;
    view.replay.polling = false;
    await finishReplay(replay, data.result);
  }

  async function finishReplay(replay, result) {
    const r = view.replay;
    if (r.result && r.result.replay && r.result.replay.id === replay.id) {
      renderReplay();
      renderIcons();
      return;
    }
    r.result = { replay, result, path: "", error: "" };
    if (view.recording) view.recording = Object.assign({}, view.recording, { replay });
    const report = (result && result.report) || {};
    const failure = report.failure || {};
    const tail = "the device is back with the Inspector.";
    if (replay.status === "passed") setStatus(`Replay passed; ${tail}`, "success");
    else if (replay.status === "failed") setStatus(`Replay failed${failure.segment ? ` at ${failure.segment} step ${failure.step || "?"}` : ""}; ${tail}`, "error");
    else if (replay.status === "cancelled") setStatus(`Replay stopped; ${tail}`, "warning");
    else setStatus(`The replay could not run: ${errorText(replay.error || {})}`, "error");
    renderSession();
    // A replay that ran to its end goes to the assistant; a stopped one,
    // or one that could not start, has nothing to review.
    if ((replay.status === "passed" || replay.status === "failed") && result && !r.saved.includes(replay.id)) {
      await saveReplay();
    } else if (!r.saved.includes(replay.id)) {
      r.saved.push(replay.id);
      remember();
    }
  }

  // Writes the report and its files into the workspace, the report last so
  // the assistant finds the files it names, then tells the assistant.
  async function saveReplay() {
    const r = view.replay;
    const res = r.result;
    if (!res || !res.result || !res.replay) return;
    const dir = `${REPLAYS_DIR}/${res.replay.id}`;
    try {
      for (const [rel, value] of Object.entries(res.result.files || {})) {
        const slash = rel.lastIndexOf("/");
        const folder = slash >= 0 ? `${dir}/${rel.slice(0, slash)}` : dir;
        await writeToWorkspace(rel.slice(slash + 1), base64Blob(value, mimeFor(rel)), folder);
      }
      const report = new Blob([JSON.stringify(res.result.report, null, 2)], { type: "application/json" });
      const path = await writeToWorkspace("report.json", report, dir);
      res.path = path;
      res.error = "";
      if (!r.saved.includes(res.replay.id)) r.saved.push(res.replay.id);
      remember();
      sendChat(replayMessage(res.result.report, path));
    } catch (error) {
      res.error = `Could not save the replay into the assistant's workspace: ${errorText(error)}`;
    }
    renderReplay();
    renderIcons();
    loadFlow();
  }

  async function startReplay(button) {
    const r = view.replay;
    const rec = view.recording;
    if (!rec || (r.running && r.running.status === "running")) return;
    const chosen = selectedSegments();
    if (!chosen.length) {
      setStatus("Tick the segments to replay; they run in the order listed.", "error");
      return;
    }
    if (button) button.disabled = true;
    try {
      await loadReplaySecrets();
      const missing = r.secretNames.filter((name) => !r.secrets[name]);
      if (missing.length) {
        renderReplay();
        renderIcons();
        setStatus(`Fill in ${missing.join(", ")} first: the segments type ${missing.length === 1 ? "it" : "them"}.`, "error");
        return;
      }
      const segments = [];
      for (const seg of chosen) segments.push({ name: seg.name, path: seg.path, yaml: await segmentYaml(seg) });
      const secrets = {};
      r.secretNames.forEach((name) => {
        secrets[name] = r.secrets[name];
      });
      const data = await call("segment.replay", { id: rec.id, segments, secrets, start: r.start }, { timeoutMs: 30000 });
      r.result = null;
      r.running = data.replay || null;
      // The card disables saving and holding at once, not at the next poll.
      if (view.recording && r.running) view.recording = Object.assign({}, view.recording, { replay: r.running });
      setStatus(`Replaying ${chosen.map((seg) => seg.name).join(", ")}.`, "");
      renderSession();
      scheduleReplayPoll();
    } catch (error) {
      if (error.code === "missing_secrets" && error.data && Array.isArray(error.data.missing)) {
        r.extraSecretNames = error.data.missing.map(String);
        await loadReplaySecrets();
        renderReplay();
        renderIcons();
        setStatus(`Fill in ${error.data.missing.join(", ")} first: the segments type ${error.data.missing.length === 1 ? "it" : "them"}.`, "error");
      } else {
        setStatus(`Could not start the replay: ${errorText(error)}`, "error");
      }
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function stopReplay(button) {
    const rec = view.recording;
    if (!rec) return;
    if (button) button.disabled = true;
    try {
      const data = await call("replay.stop", { id: rec.id }, { timeoutMs: 15000 });
      if (data.replay) view.replay.running = data.replay;
      setStatus("Stopping the replay after the step it is on.", "");
      renderReplay();
      renderIcons();
    } catch (error) {
      setStatus(errorText(error), "error");
      if (button) button.disabled = false;
    }
  }

  // ---- Library ----------------------------------------------------------------
  //
  // What the workspace keeps for mobile testing, with what can be done to it:
  // recordings (and their split's state), segments (with the grade and the
  // review count inspector import writes into the file, and whether a replay
  // passed), and replays (their reports as cards). A deletion is done here
  // and told to the assistant, which keeps the scenario plan in step.

  const LIBRARY_PLATFORMS = ["android", "ios"];
  const LIBRARY_MAX_FILES = 40;

  function freshLibrary() {
    return { loading: false, loaded: false, error: "", segments: {}, recordings: [], replays: [], plans: [], texts: {}, open: {} };
  }

  function deletedMessage(kind, name, path) {
    return `${kind} ${name} deleted: ${path}`;
  }

  // The little a list needs from a segment file, without a YAML parser: its
  // name, how many steps, and what the compiler wrote into source.
  function parseSegmentYaml(yaml) {
    const text = String(yaml || "");
    const first = (re) => {
      const m = text.match(re);
      return m ? m[1].trim().replace(/^["']|["']$/g, "") : "";
    };
    return {
      name: first(/^name:\s*(.+)$/m),
      platform: first(/^platform:\s*(.+)$/m),
      steps: (text.match(/^\s*-\s*action:/gm) || []).length,
      grade: first(/^\s+grade:\s*(.+)$/m),
      needsReview: Number(first(/^\s+needs_review:\s*(\d+)/m)) || 0,
      compiledAt: first(/^\s+compiled_at:\s*(.+)$/m),
    };
  }

  async function cachedText(path, stamp) {
    const key = `${path}|${stamp || ""}`;
    const cache = view.library.texts;
    if (!(key in cache)) cache[key] = await readWorkspaceText(path);
    return cache[key];
  }

  async function deleteWorkspace(paths) {
    const response = await fetch(workspaceApi("/delete"), { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.success === false) throw new Error(String(body.error || body.detail || `HTTP ${response.status}`));
    return body;
  }

  function dateText(value) {
    const t = Date.parse(value || "");
    if (!Number.isFinite(t)) return "";
    const d = new Date(t);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function sizeText(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
    if (n >= 1024) return `${Math.round(n / 1024)} KB`;
    return `${n} B`;
  }

  async function loadLibrary() {
    if (!view.agentId) return;
    const lib = view.library;
    if (lib.loading) return;
    lib.loading = true;
    renderLibrary();
    try {
      const files = (items) => items.filter((item) => item.is_file !== false && !(item.is_dir || item.type === "directory"));
      const [recordingItems, replayItems, scenarioItems, ...segmentLists] = await Promise.all([
        listWorkspace(RECORDINGS_DIR),
        listWorkspace(REPLAYS_DIR),
        listWorkspace(SCENARIOS_DIR),
        ...LIBRARY_PLATFORMS.map((platform) => listWorkspace(`${SEGMENTS_DIR}/${platform}`)),
      ]);
      lib.plans = await readPlans(scenarioItems);

      // Replays, newest first; their reports name the segments they ran.
      const replayDirs = replayItems.filter((item) => item.is_dir || item.type === "directory").sort((a, b) => String(b.name).localeCompare(String(a.name))).slice(0, 20);
      lib.replays = (await Promise.all(replayDirs.map(async (dir) => {
        const path = `${REPLAYS_DIR}/${dir.name}/report.json`;
        try {
          const report = JSON.parse(await cachedText(path, dir.modified_at));
          return {
            id: String(dir.name), path, dir: `${REPLAYS_DIR}/${dir.name}`, status: String(report.status || ""), platform: String(report.platform || ""),
            device: String(report.device || ""), finishedAt: String(report.finished_at || ""), start: String(report.start || ""),
            segments: (Array.isArray(report.segments) ? report.segments : []).map((seg) => ({ name: String(seg.name || ""), status: String(seg.status || "") })),
            failure: report.failure && typeof report.failure === "object" ? { segment: String(report.failure.segment || ""), step: Number(report.failure.step) || 0 } : null,
          };
        } catch (_error) {
          return { id: String(dir.name), path, dir: `${REPLAYS_DIR}/${dir.name}`, status: "", segments: [], broken: true };
        }
      }))).filter(Boolean);
      // Newest first by the report's clock; the folder name only breaks ties.
      lib.replays.sort((a, b) => String(b.finishedAt || "").localeCompare(String(a.finishedAt || "")) || String(b.id).localeCompare(String(a.id)));

      // Segments per platform, with the plan's word and the latest replay's.
      lib.segments = {};
      for (let i = 0; i < LIBRARY_PLATFORMS.length; i += 1) {
        const platform = LIBRARY_PLATFORMS[i];
        const items = files(segmentLists[i]).filter((item) => /\.ya?ml$/i.test(item.name || "")).slice(0, LIBRARY_MAX_FILES);
        lib.segments[platform] = await Promise.all(items.map(async (item) => {
          const name = String(item.name).replace(/\.ya?ml$/i, "");
          const path = `${SEGMENTS_DIR}/${platform}/${item.name}`;
          let parsed = { steps: 0, grade: "", needsReview: 0, compiledAt: "" };
          try {
            parsed = parseSegmentYaml(await cachedText(path, item.modified_at));
          } catch (_error) {
            /* listed without its details */
          }
          const plan = lib.plans.find((doc) => planStatus(doc, name, platform)) || null;
          const latest = lib.replays.find((rp) => rp.platform === platform && rp.segments.some((seg) => seg.name === name && seg.status !== "not_run")) || null;
          const latestSeg = latest ? latest.segments.find((seg) => seg.name === name) : null;
          return {
            name, path, platform, modifiedAt: String(item.modified_at || ""), steps: parsed.steps, grade: parsed.grade, needsReview: parsed.needsReview,
            compiledAt: parsed.compiledAt || String(item.modified_at || ""), planStatus: plan ? planStatus(plan, name, platform) : "",
            lastReplay: latestSeg ? { status: latestSeg.status, at: latest.finishedAt, step: latest.failure && latest.failure.segment === name ? latest.failure.step : 0 } : null,
          };
        }));
      }

      // Recordings with their split's state.
      const recordingFiles = files(recordingItems);
      const splits = {};
      for (const item of recordingFiles.filter((it) => /\.split\.json$/.test(it.name || "")).slice(0, LIBRARY_MAX_FILES)) {
        try {
          const doc = JSON.parse(await cachedText(`${RECORDINGS_DIR}/${item.name}`, item.modified_at));
          const platform = String(doc.platform || "");
          const parts = (Array.isArray(doc.parts) ? doc.parts : []).map((part) => String(part.segment || "")).filter(Boolean);
          const compiled = parts.filter((name) => (lib.segments[platform] || []).some((seg) => seg.name === name));
          splits[String(item.name).replace(/\.split\.json$/, "")] = { path: `${RECORDINGS_DIR}/${item.name}`, platform, parts, compiled };
        } catch (_error) {
          /* a split still being written */
        }
      }
      lib.recordings = recordingFiles.filter((it) => /\.wdlog\.json$/.test(it.name || "")).sort((a, b) => String(b.modified_at || "").localeCompare(String(a.modified_at || ""))).slice(0, LIBRARY_MAX_FILES).map((item) => {
        const name = String(item.name).replace(/\.wdlog\.json$/, "");
        return { name, path: `${RECORDINGS_DIR}/${item.name}`, savedAt: String(item.modified_at || ""), size: Number(item.size) || 0, split: splits[name] || null };
      });
      lib.error = "";
      lib.loaded = true;
    } catch (error) {
      lib.error = `Could not read the workspace: ${errorText(error)}. Is the assistant running?`;
    }
    lib.loading = false;
    renderLibrary();
    renderIcons();
  }

  function segmentLine(seg) {
    const parts = [`${seg.steps} ${seg.steps === 1 ? "step" : "steps"}`];
    if (seg.grade) parts.push(`grade ${seg.grade}`);
    if (seg.needsReview) parts.push(`${seg.needsReview} to review`);
    if (seg.compiledAt) parts.push(`compiled ${dateText(seg.compiledAt)}`);
    return parts.join(" · ");
  }

  function segmentMarkHtml(seg) {
    if (seg.lastReplay && seg.lastReplay.status === "passed") return `<span class="efp-replay-mark is-success"><i data-lucide="check" class="w-3 h-3"></i>passed ${esc(dateText(seg.lastReplay.at))}</span>`;
    if (seg.lastReplay && seg.lastReplay.status === "failed") return `<span class="efp-replay-mark is-error"><i data-lucide="x" class="w-3 h-3"></i>failed${seg.lastReplay.step ? ` at step ${esc(String(seg.lastReplay.step))}` : ""}</span>`;
    if (seg.planStatus === "replayed") return `<span class="efp-replay-mark is-success"><i data-lucide="check" class="w-3 h-3"></i>replayed</span>`;
    if (seg.planStatus === "to_record") return `<span class="efp-replay-mark is-error"><i data-lucide="minus" class="w-3 h-3"></i>to record</span>`;
    return `<span class="efp-replay-mark">not replayed</span>`;
  }

  function libraryHtml() {
    const lib = view.library;
    const rec = view.recording;
    const held = rec && rec.status === "active";
    const platformName = (p) => (p === "ios" ? "iOS" : "Android");
    const segmentGroups = LIBRARY_PLATFORMS.map((platform) => {
      const list = lib.segments[platform] || [];
      if (!list.length) return "";
      const rows = list.map((seg) => {
        const open = Boolean(lib.open[`file:${seg.path}`]);
        return `
        <div class="efp-lib-row">
          <div class="efp-lib-main"><strong>${esc(seg.name)}</strong> ${segmentMarkHtml(seg)}<span class="efp-card-meta">${esc(segmentLine(seg))}</span></div>
          <div class="efp-lib-actions">
            <button type="button" class="composer-pill-btn" data-recording-action="library-replay" data-name="${esc(seg.name)}" data-platform="${esc(platform)}" title="${held ? "Replay on the held device" : "Tick it for the next replay"}">Replay</button>
            <button type="button" class="composer-pill-btn" data-recording-action="library-view" data-path="${esc(seg.path)}" aria-expanded="${open ? "true" : "false"}">${open ? "Hide" : "View"}</button>
            <button type="button" class="composer-pill-btn is-danger" data-recording-action="library-delete-segment" data-name="${esc(seg.name)}" data-path="${esc(seg.path)}">Delete</button>
          </div>
        </div>
        ${open ? `<div class="efp-lib-card" data-library-file="${esc(seg.path)}"><pre class="efp-lib-pre">Loading…</pre></div>` : ""}`;
      }).join("");
      return `<div class="efp-lib-group"><span class="efp-card-meta">${esc(platformName(platform))}</span>${rows}</div>`;
    }).join("");
    const recordingRows = lib.recordings.map((item) => {
      let split = "no split yet";
      if (item.split && item.split.parts.length) split = item.split.compiled.length === item.split.parts.length ? `split into ${item.split.parts.length} ${item.split.parts.length === 1 ? "segment" : "segments"}` : `split proposed, ${item.split.parts.length - item.split.compiled.length} of ${item.split.parts.length} parts not compiled`;
      return `
        <div class="efp-lib-row">
          <div class="efp-lib-main"><strong>${esc(item.name)}</strong><span class="efp-card-meta">saved ${esc(dateText(item.savedAt))} · ${esc(sizeText(item.size))} · ${esc(split)}</span></div>
          <div class="efp-lib-actions">
            <button type="button" class="composer-pill-btn" data-recording-action="library-split" data-name="${esc(item.name)}" data-path="${esc(item.path)}" title="Ask the assistant to propose the split again">Split</button>
            <button type="button" class="composer-pill-btn is-danger" data-recording-action="library-delete-recording" data-name="${esc(item.name)}" data-path="${esc(item.path)}">Delete</button>
          </div>
        </div>`;
    }).join("");
    const replayRows = lib.replays.map((rp) => {
      const passed = rp.segments.filter((seg) => seg.status === "passed").length;
      const summary = rp.broken ? "report unreadable" : `${passed} of ${rp.segments.length} passed · ${rp.segments.map((seg) => seg.name).join(", ")}`;
      const tone = rp.status === "passed" ? "success" : (rp.status === "failed" ? "error" : "neutral");
      const open = Boolean(lib.open[rp.id]);
      return `
        <div class="efp-lib-row">
          <div class="efp-lib-main"><span class="portal-status-badge is-${tone}">${esc(rp.status || "?")}</span><strong>${esc(dateText(rp.finishedAt) || rp.id)}</strong><span class="efp-card-meta">${esc([platformName(rp.platform), rp.device].filter(Boolean).join(" "))}${rp.device ? " · " : ""}${esc(summary)}</span></div>
          <div class="efp-lib-actions">
            <button type="button" class="composer-pill-btn" data-recording-action="library-details" data-id="${esc(rp.id)}" aria-expanded="${open ? "true" : "false"}">${open ? "Hide" : "Details"}</button>
            <button type="button" class="composer-pill-btn is-danger" data-recording-action="library-delete-replay" data-id="${esc(rp.id)}" data-path="${esc(rp.dir)}">Delete</button>
          </div>
        </div>
        ${open ? `<div class="efp-lib-card" data-replay-card="${esc(rp.id)}"><div class="portal-inline-state is-visible">Loading the report…</div></div>` : ""}`;
    }).join("");
    const empty = (text) => `<p class="portal-inline-note">${esc(text)}</p>`;
    return `
      <div class="efp-mobile-section-head">
        <h5>Library</h5>
        <button type="button" class="toolbar-icon-btn" data-recording-action="library-refresh" title="Read the workspace again" aria-label="Refresh"${lib.loading ? " disabled" : ""}><i data-lucide="refresh-cw" class="w-4 h-4"></i></button>
      </div>
      <p class="portal-inline-note">What this assistant keeps for mobile testing. Deleting a segment or a recording here tells the assistant, which updates the scenario plan.</p>
      ${lib.error ? `<div class="portal-inline-state is-visible is-error">${esc(lib.error)}</div>` : ""}
      ${lib.loading && !lib.loaded ? `<p class="portal-inline-note">Reading the workspace…</p>` : ""}
      <h6>Segments</h6>
      ${segmentGroups || (lib.loaded ? empty("No compiled segments yet. Save a recording and approve its split in the chat.") : "")}
      <h6>Recordings</h6>
      ${recordingRows || (lib.loaded ? empty("No recordings saved yet.") : "")}
      <h6>Replays</h6>
      ${replayRows || (lib.loaded ? empty("No replays yet.") : "")}`;
  }

  function renderLibrary() {
    const box = panelRoot()?.querySelector("[data-recording-library]");
    if (!box) return;
    if (view.tab !== "library") {
      box.innerHTML = "";
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    box.innerHTML = libraryHtml();
    Object.keys(view.library.open).forEach((key) => (key.startsWith("file:") ? fillFile(key.slice(5)) : fillReplayCard(key)));
  }

  // The segment file as the assistant wrote it.
  async function fillFile(path) {
    const slot = [...(panelRoot()?.querySelectorAll("[data-library-file]") || [])].find((el) => el.dataset.libraryFile === path);
    if (!slot) return;
    try {
      const seg = Object.values(view.library.segments).flat().find((item) => item.path === path);
      slot.innerHTML = `<pre class="efp-lib-pre">${esc(await cachedText(path, seg ? seg.modifiedAt : ""))}</pre>`;
    } catch (error) {
      slot.innerHTML = `<div class="portal-inline-state is-visible is-error">${esc(errorText(error))}</div>`;
    }
  }

  // The replay's report as the chat shows it, rendered by efp_cards.js.
  async function fillReplayCard(id) {
    const slot = panelRoot()?.querySelector(`[data-replay-card="${id}"]`);
    const rp = view.library.replays.find((item) => item.id === id);
    if (!slot || !rp) return;
    const cards = window.EfpCards;
    if (!cards || typeof cards.replayCardHtml !== "function") {
      slot.innerHTML = `<div class="portal-inline-state is-visible is-warning">The report is at ${esc(rp.path)}.</div>`;
      return;
    }
    try {
      const doc = JSON.parse(await cachedText(rp.path, ""));
      slot.innerHTML = cards.replayCardHtml(cards.normalizeReplay(Object.assign({}, doc, { __source: rp.path }), rp.dir), view.agentId);
      renderIcons();
    } catch (error) {
      slot.innerHTML = `<div class="portal-inline-state is-visible is-error">${esc(errorText(error))}</div>`;
    }
  }

  async function libraryAction(action, button, root) {
    const lib = view.library;
    const name = button.dataset.name || "";
    const path = button.dataset.path || "";
    const say = setStatus;
    if (action === "library-refresh") {
      lib.loaded = false;
      await loadLibrary();
      return;
    }
    if (action === "library-view") {
      if (lib.open[`file:${path}`]) delete lib.open[`file:${path}`];
      else lib.open[`file:${path}`] = true;
      renderLibrary();
      renderIcons();
      return;
    }
    if (action === "library-replay") {
      const platform = button.dataset.platform || "";
      const rec = view.recording;
      const r = view.replay;
      r.touched = true;
      if (!r.checked.includes(name)) r.checked.push(name);
      if (!rec || rec.status !== "active") {
        say(`${name} is ticked for the next replay. Start a device under Session first.`, "warning");
        return;
      }
      if (recordingPlatform() !== platform) {
        say(`${name} is a ${platform === "ios" ? "iOS" : "Android"} segment; the held device runs ${platformLabel(rec)}.`, "error");
        return;
      }
      switchTab("session");
      if (r.segments === null) await loadReplaySegments();
      else {
        await loadReplaySecrets();
        renderReplay();
        renderIcons();
      }
      root.querySelector("[data-recording-replay]")?.scrollIntoView({ block: "start", behavior: "smooth" });
      say(`${name} is ticked under Replay.`, "");
      return;
    }
    if (action === "library-split") {
      sendChat(savedMessage(name, path, []));
      say(`Asked the assistant to propose the split of ${name} again.`, "success");
      return;
    }
    if (action === "library-details") {
      const id = button.dataset.id || "";
      if (lib.open[id]) delete lib.open[id];
      else lib.open[id] = true;
      renderLibrary();
      renderIcons();
      return;
    }
    if (action === "library-delete-segment" || action === "library-delete-recording" || action === "library-delete-replay") {
      const kind = action === "library-delete-segment" ? "Segment" : (action === "library-delete-recording" ? "Recording" : "Replay");
      const what = kind === "Replay" ? `the replay of ${dateText((lib.replays.find((rp) => rp.id === button.dataset.id) || {}).finishedAt) || button.dataset.id}` : `${kind.toLowerCase()} ${name}`;
      const hint = kind === "Segment" ? " Scenarios that use it will need it recorded again." : (kind === "Recording" ? " Its split goes with it; segments already compiled from it stay." : "");
      if (!(await confirmAction(`Delete ${what} from the assistant's workspace?${hint}`, "Delete", "Delete?"))) return;
      const paths = [path];
      if (kind === "Recording") {
        const item = lib.recordings.find((it) => it.name === name);
        if (item && item.split) paths.push(item.split.path);
      }
      button.disabled = true;
      try {
        await deleteWorkspace(paths);
      } catch (error) {
        button.disabled = false;
        say(`Could not delete ${what}: ${errorText(error)}`, "error");
        return;
      }
      if (kind !== "Replay") sendChat(deletedMessage(kind, name, path));
      say(`Deleted ${what}.`, "success");
      lib.loaded = false;
      view.replay.segments = null;
      view.replay.yaml = {};
      await Promise.all([loadLibrary(), loadFlow()]);
    }
  }

  // ---- saving a recording -----------------------------------------------------------

  // Writes a saved log into the assistant's workspace and tells the
  // assistant. The bridge has already moved on to the next log, so a failed
  // write keeps this one here to save again or download.
  async function saveLog(name, log, summary) {
    const fileName = `${name}.wdlog.json`;
    const blob = new Blob([JSON.stringify(log, null, 2)], { type: "application/json" });
    try {
      const path = await writeToWorkspace(fileName, blob);
      view.unsaved = null;
      const s = summary || {};
      view.done.push({ name, detail: `${Number(s.actions) || 0} actions${s.secrets ? `, ${s.secrets} secret` : ""}` });
      remember();
      sendChat(savedMessage(name, path));
      return path;
    } catch (error) {
      view.unsaved = { name, log, summary, error: errorText(error) };
      return "";
    }
  }

  async function saveRecording(root, button) {
    if (!view.recording) return;
    if (view.unsaved) {
      setStatus(`Save ${view.unsaved.name} first (Save again), or download it.`, "error");
      return;
    }
    const name = currentName(root);
    if (name && !SEGMENT_NAME.test(name)) {
      setStatus("Names use letters, digits, dot, dash, and underscore, and start with a letter or digit.", "error");
      return;
    }
    view.busy = "save";
    if (button) button.disabled = true;
    try {
      const data = await call("segment.done", { id: view.recording.id, segment: name, next_segment: nextPlanned(name || view.recording.segment) }, { timeoutMs: 30000 });
      view.recording = Object.assign({}, view.recording, { segment: data.next_segment, summary: {} });
      const path = await saveLog(data.segment, data.log, data.summary);
      if (!path) setStatus(`Could not save ${data.segment} into the assistant's workspace.`, "error");
      else if (view.planned.indexOf(data.segment) >= 0) setStatus(`Saved ${data.segment}; the assistant compiles it. Carry on with ${data.next_segment} in the Inspector.`, "success");
      else setStatus(`Saved ${data.segment}; the assistant proposes how to split it in the chat. Anything you record from here goes into ${data.next_segment}.`, "success");
    } catch (error) {
      setStatus(error.code === "nothing_recorded" ? "Nothing recorded yet: tap and type in the Inspector attached through the bridge. What you do on BrowserStack’s own site is not seen by the bridge." : errorText(error), "error");
    } finally {
      view.busy = "";
      if (button) button.disabled = false;
    }
    renderSession();
    loadFlow();
  }

  async function saveAgain() {
    const unsaved = view.unsaved;
    if (!unsaved) return;
    const path = await saveLog(unsaved.name, unsaved.log, unsaved.summary);
    setStatus(path ? `Saved ${unsaved.name}.` : `Still could not save ${unsaved.name}: ${view.unsaved.error}`, path ? "success" : "error");
    renderSession();
    if (path) loadFlow();
  }

  function downloadLog() {
    const unsaved = view.unsaved;
    if (!unsaved) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(unsaved.log, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${unsaved.name}.wdlog.json`;
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      link.remove();
    }, 0);
    setStatus(`Downloaded ${unsaved.name}.wdlog.json. Upload it to ${RECORDINGS_DIR}/ in the assistant's files when you can.`, "success");
  }

  async function uploadCode(root) {
    const input = root.querySelector("[data-recording-code]");
    const file = input && input.files && input.files[0];
    if (!file) {
      setStatus("Choose the code file the Inspector recorder generated.", "error");
      return;
    }
    const dot = file.name.lastIndexOf(".");
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
    if (CODE_EXTENSIONS.indexOf(ext) < 0) {
      setStatus(`Upload the recorder's code (${CODE_EXTENSIONS.join(", ")}).`, "error");
      return;
    }
    const name = currentName(root) || (view.recording && view.recording.segment) || "segment";
    if (!SEGMENT_NAME.test(name)) {
      setStatus("Names use letters, digits, dot, dash, and underscore, and start with a letter or digit.", "error");
      return;
    }
    try {
      const path = await writeToWorkspace(`${name}${ext}`, file);
      view.done.push({ name, detail: file.name });
      remember();
      const next = nextPlanned(name);
      // Recorder code has no screens to split by: it is one segment.
      sendChat(`Segment ${name} recorded: ${path}`);
      setStatus(`Uploaded ${path}.${next ? ` Next: ${next}.` : ""}`, "success");
      if (next && view.recording) view.recording = Object.assign({}, view.recording, { segment: next });
      input.value = "";
      renderSession();
      loadFlow();
    } catch (error) {
      setStatus(`Upload failed: ${errorText(error)}`, "error");
    }
  }

  async function uploadBuildFromPanel(root, button) {
    const input = root.querySelector("[data-recording-build]");
    const file = input && input.files && input.files[0];
    if (!file) {
      setStatus("Choose an .apk, .aab, or .ipa build first.", "error");
      return;
    }
    const dot = file.name.lastIndexOf(".");
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
    if (BUILD_EXTENSIONS.indexOf(ext) < 0) {
      setStatus("Upload an .apk, .aab, or .ipa build.", "error");
      return;
    }
    const customId = (root.querySelector("[data-recording-custom-id]")?.value || "").trim();
    if (customId && !CUSTOM_ID.test(customId)) {
      setStatus("A custom id uses letters, digits, dot, dash, and underscore (up to 100).", "error");
      return;
    }
    if (!ensureReady()) return;
    const progress = root.querySelector("[data-recording-upload-progress]");
    const bar = progress && progress.querySelector("i");
    const setProgress = (fraction) => {
      if (bar) bar.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
      if (fraction >= 1) setStatus(`Sending ${file.name} on to BrowserStack…`, "");
    };
    view.busy = "upload";
    if (button) button.disabled = true;
    if (progress) progress.classList.remove("hidden");
    setProgress(0);
    setStatus(`Uploading ${file.name} through the local bridge…`, "");
    try {
      const app = await uploadBuild(file, { credentials: credentials(), customId, onProgress: setProgress });
      setStatus(`Uploaded ${file.name}${app.custom_id ? ` as ${app.custom_id}` : ""}. BrowserStack keeps builds for 30 days.`, "success");
      input.value = "";
      await loadApps(app.custom_id || app.app_url || "");
    } catch (error) {
      setStatus(errorText(error), "error");
    } finally {
      view.busy = "";
      if (button) button.disabled = false;
      if (progress) progress.classList.add("hidden");
    }
  }

  // ---- holding and releasing the device ------------------------------------------------

  async function extend(button) {
    if (!view.recording) return;
    if (button) button.disabled = true;
    try {
      const recording = await call("session.extend", { id: view.recording.id }, { credentials: credentials(), timeoutMs: 60000 });
      view.recording = Object.assign({}, view.recording, recording);
      setStatus("The device is held for another 30 minutes.", "success");
    } catch (error) {
      setStatus(errorText(error), "error");
    } finally {
      if (button) button.disabled = false;
    }
    renderSession();
  }

  async function confirmAction(message, confirmText, title = "Finish recording?") {
    if (typeof window.showConfirm === "function") return window.showConfirm({ title, message, confirmText, danger: true });
    return window.confirm(message);
  }

  async function finish(button) {
    const rec = view.recording;
    if (!rec) return;
    const pending = Number(rec.summary && rec.summary.actions) || 0;
    const replaying = Boolean(rec.replay && rec.replay.status === "running");
    if (replaying) {
      if (!(await confirmAction("A replay is running on this device. Stop it and finish the recording?", "Finish"))) return;
    } else if (view.unsaved) {
      if (!(await confirmAction(`Recording ${view.unsaved.name} is not saved in the assistant's workspace. Finish anyway? Download it first to keep it.`, "Finish"))) return;
    } else if (pending > 0) {
      if (!(await confirmAction(`${pending} recorded ${pending === 1 ? "action is" : "actions are"} not saved. Finish without ${pending === 1 ? "it" : "them"}? Press Save recording first to keep ${pending === 1 ? "it" : "them"}.`, "Finish"))) return;
    }
    if (button) button.disabled = true;
    try {
      // The bridge lets a running replay finish its step and hand the
      // device back first.
      await call("session.finish", { id: rec.id }, { credentials: credentials(), timeoutMs: replaying ? 300000 : 120000 });
    } catch (error) {
      if (error.code !== "not_found") {
        setStatus(errorText(error), "error");
        if (button) button.disabled = false;
        return;
      }
    }
    closeInspectorTab();
    view.recording = null;
    view.unsaved = null;
    stopReplayPolling();
    view.replay = freshReplay();
    view.connectionOpen = false;
    remember();
    closeWorkspace({ rerender: false });
    if (view.done.length) sendChat("Recording finished. Compile any segment not imported yet.");
    view.done = [];
    await loadRecordings();
    renderAll();
    setStatus("Recording finished; the device is released.", "success");
    loadFlow();
  }

  async function finishOther(id, button) {
    if (button) button.disabled = true;
    try {
      await call("session.finish", { id }, { credentials: credentials(), timeoutMs: 120000 });
      setStatus("Released the device.", "success");
    } catch (error) {
      if (error.code !== "not_found") setStatus(errorText(error), "error");
    }
    await loadRecordings();
    renderOthers();
  }

  function adopt(id) {
    const rec = view.others.find((item) => item.id === id);
    if (!rec || view.recording) return;
    view.recording = rec;
    view.others = view.others.filter((item) => item.id !== id);
    view.done = [];
    view.replay = freshReplay();
    remember();
    setStatus("Continuing that recording here; what you save goes into this assistant's workspace.", "success");
    renderAll();
    loadFlow();
  }

  // Saves the proxy fields of the connector page or the panel's settings (the
  // container); the report goes to whichever status element the caller
  // shows. The login is kept in this browser's storage on this computer and
  // goes only to the local bridge, which hands it to mobile-auto and
  // BrowserStack Local. A login pasted into the address moves to the user
  // name and password fields, so the password is never shown in the clear.
  function saveProxy(container, report) {
    const say = report || setStatus;
    const field = (name) => container.querySelector(`[data-bridge-proxy="${name}"]`);
    const urlInput = field("url");
    const userInput = field("username");
    const passwordInput = field("password");
    const typed = splitProxyLogin(urlInput ? urlInput.value : "");
    let username = userInput ? userInput.value.trim() : "";
    let password = passwordInput ? passwordInput.value : "";
    if (typed.login) {
      username = typed.username;
      password = typed.password;
      if (urlInput) urlInput.value = typed.url;
      if (userInput) userInput.value = username;
      if (passwordInput) passwordInput.value = password;
    }
    if (typed.url) {
      let parsed = null;
      try {
        parsed = new URL(typed.url);
      } catch (_error) {
        parsed = null;
      }
      if (!parsed || !/^https?:$/.test(parsed.protocol) || !parsed.hostname) {
        say("The proxy is a URL such as http://proxy.example.com:8080; its user name and password go in their own fields.", "error");
        return false;
      }
    }
    writeStorage(PROXY_KEY, typed.url);
    writeStorage(PROXY_USER_KEY, username);
    writeStorage(PROXY_PASSWORD_KEY, password);
    const login = Boolean(username || password);
    if (typed.url) say(login ? "Proxy saved for this computer; its login stays in this browser." : "Proxy saved for this computer.", "success");
    else say(login ? "Using this computer's proxy settings; the user name and password apply only with a proxy address." : "Using this computer's proxy settings.", "success");
    if (panelRoot() && ready()) loadApps();
    return true;
  }

  // ---- events -----------------------------------------------------------------

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const test = target.closest("[data-mobile-bridge-test]");
    if (test) {
      event.preventDefault();
      testConnector(test);
      return;
    }
    const overviewStart = target.closest('[data-mobile-bridge-action="start"]');
    if (overviewStart) {
      event.preventDefault();
      startBridgeFromOverview(overviewStart);
      return;
    }
    const copy = target.closest("[data-recording-copy]");
    if (copy) {
      copyText(copy, copy.dataset.recordingCopy || "");
      return;
    }
    const replayButton = target.closest("[data-replay-action]");
    if (replayButton && replayButton.closest("[data-recording-root]")) {
      const action = replayButton.dataset.replayAction;
      if (action === "start") startReplay(replayButton);
      else if (action === "stop") stopReplay(replayButton);
      else if (action === "refresh") loadReplaySegments();
      else if (action === "save-again") saveReplay();
      return;
    }
    const tab = target.closest("[data-recording-tab]");
    if (tab && tab.closest("[data-recording-root]")) {
      switchTab(tab.dataset.recordingTab);
      return;
    }
    const button = target.closest("[data-recording-action]");
    if (!button) return;
    const root = button.closest("[data-recording-root]") || (button.closest("[data-recording-workspace]") ? panelRoot() : null);
    if (!root) return;
    if (button.dataset.recordingAction.startsWith("library-")) {
      libraryAction(button.dataset.recordingAction, button, root);
      return;
    }
    // A menu closes once one of its entries is chosen.
    const menu = button.closest("details.efp-menu");
    if (menu) menu.removeAttribute("open");
    const action = button.dataset.recordingAction;
    if (action === "start-bridge") startBridge();
    else if (action === "check-bridge") refreshAll();
    else if (action === "settings") toggleSettings();
    else if (action === "settings-upload") {
      toggleSettings(true);
      root.querySelector("[data-recording-build]")?.scrollIntoView({ block: "center" });
    } else if (action === "upload-build") uploadBuildFromPanel(root, button);
    else if (action === "start") startRecording(root, button);
    else if (action === "open-inspector") openInspector();
    else if (action === "open-workspace") openWorkspace();
    else if (action === "close-workspace") closeWorkspace();
    else if (action === "connection") {
      view.connectionOpen = !view.connectionOpen;
      renderDevice();
      renderIcons();
    } else if (action === "save-recording") saveRecording(root, button);
    else if (action === "save-again") saveAgain();
    else if (action === "download-log") downloadLog();
    else if (action === "upload-code") uploadCode(root);
    else if (action === "extend") extend(button);
    else if (action === "finish") finish(button);
    else if (action === "finish-other") finishOther(button.dataset.id || "", button);
    else if (action === "adopt") adopt(button.dataset.id || "");
  });

  // An open device menu closes on a click elsewhere.
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    document.querySelectorAll("details.efp-menu[open]").forEach((menu) => {
      if (!target || !menu.contains(target)) menu.removeAttribute("open");
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && view.workspace && !event.defaultPrevented) closeWorkspace();
  });

  document.addEventListener("change", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target && target.matches("[data-bridge-proxy]")) {
      const overview = target.closest("[data-mobile-overview]");
      const panel = target.closest("[data-recording-root]");
      if (overview) {
        const result = overview.parentElement ? overview.parentElement.querySelector("[data-mobile-bridge-test-result]") : null;
        saveProxy(overview, (text, tone) => setInline(result, text, tone));
      } else if (panel) {
        saveProxy(panel);
      }
      return;
    }
    if (!target || !target.closest("[data-recording-root]")) return;
    if (target.matches("[data-recording-app]")) {
      syncPlatform();
    } else if (target.matches("[data-recording-platform]")) {
      loadFlow();
    } else if (target.matches("[data-recording-build]")) {
      const root = target.closest("[data-recording-root]");
      const custom = root.querySelector("[data-recording-custom-id]");
      const file = target.files && target.files[0];
      if (custom && file && !custom.value.trim()) custom.value = suggestCustomId(file.name);
    } else if (target.matches("[data-recording-auto-inspector]")) {
      writeStorage(AUTO_INSPECTOR_KEY, target.checked ? "" : "off");
      view.formKey = "";
      renderDevice();
      renderIcons();
    } else if (target.matches("[data-replay-segment]")) {
      const r = view.replay;
      const name = target.dataset.replaySegment;
      r.touched = true;
      r.checked = r.checked.filter((item) => item !== name);
      if (target.checked) r.checked.push(name);
      loadReplaySecrets().then(() => {
        renderReplay();
        renderIcons();
      });
    } else if (target.matches("[data-replay-start]")) {
      view.replay.start = target.value;
    }
  });

  // Secret values stay in this page's memory as they are typed.
  document.addEventListener("input", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target && target.matches("[data-replay-secret]")) view.replay.secrets[target.dataset.replaySecret] = target.value;
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    const swapped = event.target && event.target.querySelector ? event.target : null;
    const root = swapped ? (swapped.matches("[data-mobile-overview]") ? swapped : swapped.querySelector("[data-mobile-overview]")) : null;
    if (root) refreshOverview(root);
  });

  function initOverviews() {
    document.querySelectorAll("[data-mobile-overview]").forEach((root) => refreshOverview(root));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initOverviews, { once: true });
  else initOverviews();

  window.EfpMobileTesting = Object.assign(window.EfpMobileTesting || {}, {
    openRecordingPanel,
    parseSegments,
    savedMessage,
    replayMessage,
    segmentSecrets,
    flowSteps,
    deletedMessage,
    parseSegmentYaml,
    proxyForBridge,
    splitProxyLogin,
    suggestCustomId,
    probeBridge: probe,
    callBridge: call,
    waitForDevice,
  });
})();
