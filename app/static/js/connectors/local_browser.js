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
 * The first (and for now only) connector type is `local_browser`: a
 * `browser serve` process on 127.0.0.1 that drives a dedicated Chrome window.
 * Contract: docs/CONNECTORS_CONTRACT.md.
 */
(function () {
  "use strict";

  const PROTOCOL_VERSION = 1;
  const LOCAL_BROWSER_TYPE = "local_browser";
  const PORT_RANGE = [8765, 8766, 8767, 8768, 8769, 8770];
  const PING_TIMEOUT_MS = 1500;
  const RUN_TIMEOUT_MS = 65000;
  const PROBE_CACHE_MS = 10000;
  const CLIENT_ID_KEY = "efp.connectors.client_id";
  const TOGGLE_KEY_PREFIX = "efp.connectors.local_browser.toggle:";
  const TOGGLE_ID = "composer-browser-toggle";
  const TOGGLE_TEXT_ID = "composer-browser-toggle-text";
  const PANEL_ROOT_ID = "connector-panel-root";

  const modules = new Map();
  const state = {
    connectors: null,
    connectorsLoadedAt: 0,
    connectorsLoading: null,
    featureEnabled: Boolean(document.getElementById("connectors-menu-btn")),
    handled: new Set(),
    localBrowser: {
      port: 0,
      alive: false,
      sessionAlive: false,
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

  function localBrowserEnabled() {
    const entry = connectorEntry(LOCAL_BROWSER_TYPE);
    return Boolean(entry && entry.enabled);
  }

  function localBrowserConfig() {
    const entry = connectorEntry(LOCAL_BROWSER_TYPE);
    const config = entry && entry.config && typeof entry.config === "object" ? entry.config : {};
    return {
      auto_enable_in_new_chats: config.auto_enable_in_new_chats !== false,
      preferred_port: Number(config.preferred_port) || PORT_RANGE[0],
    };
  }

  // ---- local browser bridge ----------------------------------------------

  function candidatePorts() {
    const preferred = localBrowserConfig().preferred_port;
    const ports = [preferred, ...PORT_RANGE].filter((port, index, list) => Number.isInteger(port) && list.indexOf(port) === index);
    if (state.localBrowser.port && ports.includes(state.localBrowser.port)) {
      return [state.localBrowser.port, ...ports.filter((port) => port !== state.localBrowser.port)];
    }
    return ports;
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

  async function probeLocalBrowser({ force = false, quick = false } = {}) {
    const lb = state.localBrowser;
    if (!force && lb.probedAt && Date.now() - lb.probedAt < PROBE_CACHE_MS) {
      return { alive: lb.alive, port: lb.port, sessionAlive: lb.sessionAlive, version: lb.version, tabCount: lb.tabCount };
    }
    if (lb.probing) return lb.probing;
    lb.probing = (async () => {
      let found = null;
      // A request in flight cannot afford the full port sweep (six ports at
      // 1.5 s each); the bridge started from the panel listens on the
      // preferred or last known port, so that is enough while answering.
      const ports = quick ? candidatePorts().slice(0, 2) : candidatePorts();
      // Ping the candidates together; the first alive port in preference
      // order wins. Sequential pings cost up to 9 s when nothing listens.
      const results = await Promise.all(ports.map(async (port) => ({ port, data: await pingPort(port) })));
      found = results.find((entry) => entry.data) || null;
      lb.probedAt = Date.now();
      lb.alive = Boolean(found);
      lb.port = found ? found.port : 0;
      lb.sessionAlive = Boolean(found && found.data.session && found.data.session.alive);
      lb.version = found ? String(found.data.version || "") : "";
      lb.protocolVersion = found ? Number(found.data.protocol_version || 0) : 0;
      lb.tabCount = found && found.data.session ? Number(found.data.session.tab_count || 0) : 0;
      lb.probing = null;
      return { alive: lb.alive, port: lb.port, sessionAlive: lb.sessionAlive, version: lb.version, tabCount: lb.tabCount };
    })();
    return lb.probing;
  }

  async function runLocalBrowser(command, params, timeoutSeconds) {
    const lb = state.localBrowser;
    if (!lb.alive || !lb.port) {
      const probe = await probeLocalBrowser({ force: true, quick: true });
      if (!probe.alive) {
        return { ok: false, error: { code: "bridge_unreachable", message: "The local browser bridge is not running.", hint: "Open Connectors → Local browser and start the bridge." } };
      }
    }
    const body = JSON.stringify({
      command,
      params: params && typeof params === "object" ? params : {},
      session: "default",
      timeout_seconds: Math.max(5, Math.min(120, Number(timeoutSeconds) || 30)),
    });
    try {
      const response = await fetchWithTimeout(`http://127.0.0.1:${lb.port}/run`, {
        method: "POST",
        mode: "cors",
        headers: { "Content-Type": "application/json" },
        body,
      }, RUN_TIMEOUT_MS);
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
      const error = payload.error && typeof payload.error === "object" ? payload.error : { code: "bridge_error", message: `Bridge returned HTTP ${response.status}.` };
      return { ok: false, error };
    } catch (error) {
      lb.alive = false;
      lb.probedAt = 0;
      const aborted = error && error.name === "AbortError";
      return {
        ok: false,
        error: {
          code: aborted ? "bridge_timeout" : "bridge_unreachable",
          message: aborted ? "The local browser bridge did not answer in time." : "Could not reach the local browser bridge.",
        },
      };
    }
  }

  function launchUrl(port) {
    const targetPort = port || localBrowserConfig().preferred_port || PORT_RANGE[0];
    return `efp-bridge://start?origin=${encodeURIComponent(portalOrigin())}&port=${encodeURIComponent(String(targetPort))}`;
  }

  function launchBridge(port) {
    // A registered protocol handler opens the launcher without leaving the
    // page; an unregistered one is simply ignored by Chrome (after an optional
    // "no app found" dialog). Either way the caller polls /ping afterwards.
    const anchor = document.createElement("a");
    anchor.href = launchUrl(port);
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(() => anchor.remove(), 0);
  }

  async function waitForBridge(timeoutMs = 6000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
      const probe = await probeLocalBrowser({ force: true });
      if (probe.alive) return probe;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return probeLocalBrowser({ force: true });
  }

  const localBrowserModule = {
    type: LOCAL_BROWSER_TYPE,
    probe: probeLocalBrowser,
    async execute(request) {
      const action = String(request.action || "");
      return runLocalBrowser(action, request.params, request.timeout_seconds);
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
      outcome = await module.execute(request);
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
    return localBrowserConfig().auto_enable_in_new_chats;
  }

  function setToggle(agentId, on) {
    try {
      sessionStorage.setItem(toggleKey(agentId), on ? "on" : "off");
    } catch (_error) {
      /* storage unavailable */
    }
  }

  function toggleElements() {
    const button = document.getElementById(TOGGLE_ID);
    const text = document.getElementById(TOGGLE_TEXT_ID);
    return button ? { button, text } : null;
  }

  function applyToggleView(view) {
    const elements = toggleElements();
    if (!elements) return;
    const { button, text } = elements;
    button.classList.toggle("hidden", view.mode === "hidden");
    button.dataset.state = view.mode;
    button.setAttribute("aria-pressed", view.mode === "on" ? "true" : "false");
    button.classList.toggle("is-active", view.mode === "on");
    button.classList.toggle("is-muted", view.mode === "offline" || view.mode === "setup");
    button.title = view.title || "";
    if (text) text.textContent = view.label || "Browser";
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
    if (!localBrowserEnabled()) {
      applyToggleView({ mode: "setup", label: "Set up browser", title: "Let the assistant use your local browser: open Connectors to set it up." });
      return;
    }
    if (!toggleOn(agentId)) {
      applyToggleView({ mode: "off", label: "Browser off", title: "The assistant will not use your local browser in this chat. Click to switch on." });
      return;
    }
    const status = probe ? await probeLocalBrowser() : { alive: state.localBrowser.alive };
    if (!status.alive) {
      applyToggleView({ mode: "offline", label: "Browser bridge offline", title: "The local browser bridge is not running. Click to open the setup steps." });
      return;
    }
    applyToggleView({ mode: "on", label: "Browser on", title: "The assistant can read and operate your EFP browser window in this chat. Click to switch off." });
  }

  async function onToggleClick() {
    const agentId = currentAgentId();
    const elements = toggleElements();
    if (!elements || !agentId) return;
    const mode = elements.button.dataset.state;
    if (mode === "setup") {
      window.location.hash = "#/connectors/local_browser";
      return;
    }
    if (mode === "offline") {
      launchBridge();
      applyToggleView({ mode: "offline", label: "Starting bridge…", title: "Waiting for the local bridge." });
      const probe = await waitForBridge(6000);
      if (!probe.alive) window.location.hash = "#/connectors/local_browser?step=2";
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

  function chatRequestConnectors(agentId) {
    const agent = agentId || currentAgentId();
    if (!state.featureEnabled || !agent || !isChromium()) return null;
    if (!localBrowserEnabled() || !toggleOn(agent)) return null;
    if (!state.localBrowser.alive) return null;
    return {
      [LOCAL_BROWSER_TYPE]: { client_id: clientId(), protocol_version: PROTOCOL_VERSION },
    };
  }

  // ---- connector panel (Connectors → Local browser) -------------------------

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
    panelStatus(root, "bridge", "checking…", "");
    const probe = await probeLocalBrowser({ force });
    panelStatus(root, "bridge", probe.alive ? `running on port ${probe.port}${probe.version ? ` (v${probe.version})` : ""}` : "not detected", probe.alive ? "ok" : "warn");
    panelStatus(root, "session", probe.alive ? (probe.sessionAlive ? `Chrome window open (${probe.tabCount || 0} tabs)` : "Chrome window not started") : "–", probe.sessionAlive ? "ok" : "");
    root.querySelectorAll("[data-connector-step]").forEach((section) => {
      const step = section.dataset.connectorStep;
      const done = (step === "2" && probe.alive) || (step === "3" && root.dataset.lastVerified) || (step === "4" && root.dataset.enabled === "true");
      section.classList.toggle("is-done", Boolean(done));
    });
    return probe;
  }

  function troubleshootFor(error) {
    const code = String((error && error.code) || "");
    if (code === "bridge_unreachable" || code === "bridge_timeout") {
      return "The bridge is not running. Go back to step 2 and start it; if you just installed it, allow the efp-bridge link when Chrome asks.";
    }
    if (code === "devtools_unavailable") {
      return "Chrome refused to expose its DevTools port. Check that the RemoteDebuggingAllowed policy is not set to false in chrome://policy.";
    }
    if (code === "origin_denied") {
      return `The bridge was started for a different Portal address. Restart it with --origin ${portalOrigin()}.`;
    }
    if (code === "session_busy") {
      return "Another browser command was still running. Wait a moment and test again.";
    }
    return "";
  }

  async function runPanelTest(root) {
    setPanelResult(root, "test", "Testing the bridge…", "");
    const probe = await refreshPanelStatus(root, { force: true });
    if (!probe.alive) {
      const hint = troubleshootFor({ code: "bridge_unreachable" });
      setPanelResult(root, "test", `<strong>Bridge not detected.</strong> ${esc(hint)}`, "error");
      await recordVerification(root, false, { reason: "bridge_unreachable" });
      return;
    }
    const outcome = await runLocalBrowser("tab.list", {}, 20);
    if (!outcome.ok) {
      const error = outcome.error || {};
      const hint = troubleshootFor(error) || String(error.hint || "");
      setPanelResult(root, "test", `<strong>Test failed:</strong> ${esc(error.code || "error")} ${esc(error.message || "")}${hint ? `<br>${esc(hint)}` : ""}`, "error");
      await recordVerification(root, false, { code: error.code || "error" });
      return;
    }
    const tabs = outcome.result && Array.isArray(outcome.result.tabs) ? outcome.result.tabs : [];
    const rows = tabs.slice(0, 12).map((tab) => `<li>${tab.active ? "▶ " : ""}${esc(tab.title || "(untitled)")} <small>${esc(tab.url || "")}</small></li>`).join("");
    setPanelResult(
      root,
      "test",
      `<strong>Connected.</strong> Bridge v${esc(probe.version || "?")} on port ${esc(probe.port)}; ${tabs.length} tab${tabs.length === 1 ? "" : "s"} in your EFP browser window.${rows ? `<ul class="portal-connector-tab-list">${rows}</ul>` : ""}`,
      "success"
    );
    await recordVerification(root, true, { version: probe.version, port: probe.port, tab_count: tabs.length });
  }

  async function recordVerification(root, ok, details) {
    const type = root.dataset.connectorType || LOCAL_BROWSER_TYPE;
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
    const type = root.dataset.connectorType || LOCAL_BROWSER_TYPE;
    const enabled = Boolean(root.querySelector('[data-connector-field="enabled"]')?.checked);
    const autoEnable = Boolean(root.querySelector('[data-connector-field="auto_enable_in_new_chats"]')?.checked);
    const portInput = root.querySelector('[data-connector-field="preferred_port"]');
    const preferredPort = portInput ? Number(portInput.value) || PORT_RANGE[0] : PORT_RANGE[0];
    setPanelResult(root, "save", "Saving…", "");
    const { ok, payload } = await requestJson(`/api/connectors/${encodeURIComponent(type)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled, config: { auto_enable_in_new_chats: autoEnable, preferred_port: preferredPort } }),
    });
    if (!ok) {
      const detail = payload && (payload.detail || payload.error) ? (payload.detail || payload.error) : "Save failed.";
      setPanelResult(root, "save", esc(typeof detail === "string" ? detail : JSON.stringify(detail)), "error");
      return;
    }
    root.dataset.enabled = enabled ? "true" : "false";
    setPanelResult(root, "save", enabled ? "Saved. New chats can use your local browser." : "Saved. Assistants will not use your local browser.", "success");
    await loadConnectors({ force: true });
    await refreshPanelStatus(root, { force: false });
    await renderToggle({ probe: false });
    document.dispatchEvent(new CustomEvent("portal:connectors-changed", { detail: { type, enabled } }));
  }

  function initPanel(rootElement) {
    const root = rootElement || document.getElementById(PANEL_ROOT_ID);
    if (!root || root.dataset.connectorsBound === "1") return;
    root.dataset.connectorsBound = "1";
    const originNodes = root.querySelectorAll("[data-connector-origin]");
    originNodes.forEach((node) => { node.textContent = portalOrigin(); });
    const launchLink = root.querySelector("[data-connector-launch-link]");
    if (launchLink) launchLink.href = launchUrl(localBrowserConfig().preferred_port);

    root.addEventListener("click", async (event) => {
      const actionNode = event.target.closest("[data-connector-action]");
      if (!actionNode) return;
      const action = actionNode.dataset.connectorAction;
      if (action === "launch") {
        event.preventDefault();
        setPanelResult(root, "launch", "Starting the bridge… allow the efp-bridge link if Chrome asks.", "");
        launchBridge(Number(root.querySelector('[data-connector-field="preferred_port"]')?.value) || undefined);
        const probe = await waitForBridge(8000);
        if (probe.alive) {
          setPanelResult(root, "launch", `<strong>Bridge is running</strong> on port ${esc(probe.port)}. Continue with step 3.`, "success");
        } else {
          setPanelResult(root, "launch", "<strong>Bridge not detected yet.</strong> If nothing happened, the protocol link is not registered: run install-bridge.cmd (Windows) or install-bridge.sh (macOS, Linux) from the unzipped folder, then try again. If it did start, the launcher writes what happened to <code>.efp/browser/logs/bridge-serve.log</code> in your home folder; a bridge left running for a different Portal address is refused there by name.", "error");
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
    register(localBrowserModule);
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
    document.addEventListener("portal:agent-selected", () => { renderToggle({ probe: true }); });
    document.addEventListener("portal:history-rendered", () => { renderToggle({ probe: false }); });
    document.addEventListener("portal:connectors-changed", (browserEvent) => {
      // Our own save already refreshed; only react to changes made elsewhere.
      if (browserEvent.detail && browserEvent.detail.type) return;
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
      probeLocalBrowser,
      runLocalBrowser,
      launchBridge,
      chatRequestConnectors,
      renderToggle,
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
