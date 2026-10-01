/**
 * Mobile testing in the Portal page: the BrowserStack connector's checks and
 * the Recording panel (assistant chat tool bar > Recording).
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

  function proxySetting() {
    return readStorage(PROXY_KEY).trim();
  }

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

  function bridgeError(error) {
    const detail = error && typeof error === "object" ? error : {};
    const err = new Error(String(detail.message || "The local bridge could not do that."));
    err.code = String(detail.code || "bridge_error");
    err.hint = String(detail.hint || "");
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
    const body = JSON.stringify({ command, params: params || {}, credentials: credentials || {}, proxy: proxySetting() });
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
    throw bridgeError(payload && payload.error ? payload.error : { code: "bridge_error", message: `The local bridge answered HTTP ${response.status}.` });
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
      const proxy = proxySetting();
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
    const proxyInput = root.querySelector("[data-mobile-bridge-proxy]");
    if (proxyInput && document.activeElement !== proxyInput) proxyInput.value = proxySetting();
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

  // ---- Recording panel --------------------------------------------------------
  //
  // 1. Start: the bridge starts a BrowserStack device with the chosen build
  //    and holds it for 30 minutes at a time. session.start answers at once
  //    with the recording "starting"; the panel polls session.status until
  //    it is "active" (or "failed", with the error), so a slow start neither
  //    holds one request open for minutes nor gets killed by a page timeout.
  // 2. Record: the hosted Inspector opens attached to the device through the
  //    bridge (or the desktop Inspector attaches to the bridge by hand). The
  //    member records the whole scenario; "Save recording" takes the log from
  //    the bridge (the bridge's segment.done) and writes it into the
  //    assistant's workspace. The chat message tells the assistant: a
  //    recording it splits into segments with the member, or, when the member
  //    listed segment names, a segment it compiles whole.
  // 3. Finish releases the device.

  const RECORDINGS_DIR = "mobile/recordings";
  const POLL_MS = 5000;
  const STATUS_EVERY = 12;
  const START_POLL_MS = 3000;
  const START_TIMEOUT_MS = 16 * 60 * 1000;
  const CODE_EXTENSIONS = [".py", ".java", ".js", ".rb", ".robot", ".cs"];
  const BUILD_EXTENSIONS = [".apk", ".aab", ".ipa"];
  const SEGMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  const CUSTOM_ID = /^[A-Za-z0-9._-]{1,100}$/;
  const RECORDING_KEY_PREFIX = "efp.mobile.recording:";

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
    polls: 0,
  };
  let pollTimer = 0;
  let tickTimer = 0;

  function currentAgentId() {
    return typeof window.currentPortalAgentId === "function" ? (window.currentPortalAgentId() || "") : "";
  }

  function panelRoot() {
    return document.querySelector("#tool-panel-body [data-recording-root]");
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
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
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

  // The recording this assistant's panel holds survives a page reload: the
  // bridge keeps it, the browser remembers which one it was.
  function storageKey() {
    return RECORDING_KEY_PREFIX + view.agentId;
  }

  function remember() {
    if (!view.agentId) return;
    if (!view.recording) {
      writeStorage(storageKey(), "");
      return;
    }
    writeStorage(storageKey(), JSON.stringify({ id: view.recording.id, planned: view.planned, done: view.done }));
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

  function shellHtml() {
    const proxy = proxySetting();
    return `
      <div class="efp-recording" data-recording-root>
        <div data-recording-setup></div>
        <section class="efp-recording-section" data-recording-start-section>
          <h5>1 · Start a recording device</h5>
          <p class="portal-panel-note">The local bridge on this computer starts a BrowserStack device with the build and holds it while you record.</p>
          <label class="portal-form-label"><span class="portal-form-label">Build</span>
            <select class="portal-form-select" data-recording-app><option value="">Looking for your builds…</option></select>
          </label>
          <details class="portal-collapsible" data-recording-upload>
            <summary class="portal-collapsible-summary"><span>Upload a build</span></summary>
            <div class="portal-panel-stack">
              <input type="file" accept="${BUILD_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-build />
              <label class="portal-form-label"><span class="portal-form-label">Custom id (optional)</span>
                <input class="portal-form-input" data-recording-custom-id placeholder="fxapp-android-uat" autocomplete="off" spellcheck="false" />
              </label>
              <p class="portal-inline-note">A custom id names the latest build uploaded with it, so recordings and the pipeline can keep using the same name.</p>
              <div class="portal-progress hidden" data-recording-upload-progress><i></i></div>
              <div class="efp-card-actions"><button type="button" class="portal-btn is-secondary" data-recording-action="upload-build"><i data-lucide="upload" class="w-4 h-4"></i>Upload to BrowserStack</button></div>
            </div>
          </details>
          <div class="grid grid-cols-2 gap-3">
            <label class="portal-form-label"><span class="portal-form-label">Platform</span>
              <select class="portal-form-select" data-recording-platform><option value="android">Android</option><option value="ios">iOS</option></select>
            </label>
            <label class="portal-form-label"><span class="portal-form-label">Device (optional)</span>
              <input class="portal-form-input" data-recording-device placeholder="Google Pixel 8" />
            </label>
          </div>
          <label class="portal-form-label"><span class="portal-form-label">Segment names (optional)</span>
            <textarea class="portal-form-textarea" rows="2" data-recording-segments placeholder="seg-login&#10;seg-select-currency"></textarea>
          </label>
          <p class="portal-inline-note">Leave it empty to record the whole scenario in one go: the assistant proposes how to split it into segments, and you confirm. List names only to save each part yourself, in this order.</p>
          <div class="efp-card-actions"><button type="button" class="portal-btn is-primary" data-recording-action="start"><i data-lucide="play" class="w-4 h-4"></i>Start recording device</button></div>
          <div data-recording-others></div>
        </section>
        <section class="efp-recording-section hidden" data-recording-session-section>
          <h5>2 · Record</h5>
          <div data-recording-session></div>
        </section>
        <details class="portal-collapsible">
          <summary class="portal-collapsible-summary"><span>Network from this computer</span></summary>
          <div class="portal-panel-stack">
            <label class="portal-form-label"><span class="portal-form-label">Proxy for BrowserStack (optional)</span>
              <input class="portal-form-input" data-recording-proxy value="${esc(proxy)}" placeholder="http://proxy.example.com:8080" autocomplete="off" spellcheck="false" />
            </label>
            <p class="portal-inline-note">Empty uses this computer's proxy settings. Saved in this browser only; a proxy that asks for a login takes http://user:password@host:port.</p>
          </div>
        </details>
        <div class="portal-inline-state" data-recording-status role="status"></div>
      </div>`;
  }

  function renderSetup() {
    const target = panelRoot()?.querySelector("[data-recording-setup]");
    if (!target) return;
    const config = view.config;
    if (!config) {
      target.innerHTML = `<div class="portal-inline-state is-visible">Checking your BrowserStack settings and the local bridge…</div>`;
      return;
    }
    if (!config.configured) {
      target.innerHTML = `<div class="portal-inline-state is-visible is-warning">${esc(config.problem || "Set up the BrowserStack connector first.")}</div>`;
      return;
    }
    const problem = bridgeProblem(view.bridge);
    if (!problem) {
      target.innerHTML = `<div class="efp-card-meta">Recording through the local bridge on 127.0.0.1:${esc(view.bridge.port)}.</div>`;
      return;
    }
    const start = problem === "not_running"
      ? `<button type="button" class="portal-btn is-primary" data-recording-action="start-bridge"${view.busy === "bridge" ? " disabled" : ""}><i data-lucide="plug" class="w-4 h-4"></i>Start bridge</button>`
      : "";
    target.innerHTML = `
      <div class="portal-inline-state is-visible ${problem === "not_running" ? "is-warning" : "is-error"}">${esc(BRIDGE_MESSAGES[problem])}</div>
      <div class="efp-card-actions">${start}<button type="button" class="portal-btn is-secondary" data-recording-action="check-bridge"><i data-lucide="refresh-cw" class="w-4 h-4"></i>Check again</button></div>`;
    renderIcons();
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
      options = `<option value="">No builds on BrowserStack yet; upload one below</option>`;
      const upload = panelRoot()?.querySelector("[data-recording-upload]");
      if (upload) upload.open = true;
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
    const finds = Number(s.finds) || 0;
    const secrets = Number(s.secrets) || 0;
    const parts = [`${actions} ${actions === 1 ? "action" : "actions"}`, `${finds} element ${finds === 1 ? "lookup" : "lookups"}`];
    return `Not saved yet: ${parts.join(", ")}${secrets ? `, ${secrets} typed into password fields (not stored)` : ""}.`;
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

  function startingCardHtml(rec) {
    const platform = rec.platform === "ios" ? "iOS" : "Android";
    const since = Date.parse(rec.started_at || "") || Date.now();
    return `
      <div class="efp-card efp-recording-card">
        <div class="efp-card-head">
          <span class="portal-status-badge is-warning">Starting</span>
          <strong class="efp-card-title">${esc(rec.device || "BrowserStack device")}</strong>
          <span class="efp-card-meta">${esc(platform)}</span>
        </div>
        <div class="efp-card-meta">Build ${esc(rec.app || "")}</div>
        <div class="efp-card-meta">${esc(rec.progress || "starting the device on BrowserStack")}… <span data-recording-countdown data-since="${esc(String(since))}">${esc(elapsedText(since))}</span></div>
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-secondary" data-recording-action="finish"><i data-lucide="square" class="w-4 h-4"></i>Cancel</button>
        </div>
      </div>`;
  }

  function sessionCardHtml() {
    const rec = view.recording;
    if (rec.status === "starting") return startingCardHtml(rec);
    const inspector = Boolean(view.config && view.config.inspector_available);
    const ended = rec.status && rec.status !== "active";
    const held = rec.hold_deadline && remaining(rec.hold_deadline) !== "expired";
    const badge = ended ? "Ended" : (held ? "Held for you" : "Hold expired");
    const tone = !ended && held ? "success" : "warning";
    const platform = rec.platform === "ios" ? "iOS" : "Android";
    const name = rec.segment || view.planned[view.done.length] || "recording-1";
    const planned = view.planned.length > 0;
    const port = view.bridge && view.bridge.port ? String(view.bridge.port) : "";
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
      <div class="efp-card efp-recording-card">
        <div class="efp-card-head">
          <span class="portal-status-badge is-${tone}">${esc(badge)}</span>
          <strong class="efp-card-title">${esc(rec.device || "BrowserStack device")}</strong>
          <span class="efp-card-meta">${esc(platform)}${rec.os_version ? ` ${esc(rec.os_version)}` : ""}</span>
        </div>
        ${rec.hold_deadline && !ended ? `<div class="efp-card-meta">Held for another <span data-recording-countdown data-deadline="${esc(rec.hold_deadline)}">${esc(remaining(rec.hold_deadline))}</span></div>` : ""}
        <div class="efp-card-meta">Build ${esc(rec.app || "")}${rec.dashboard_url ? ` · <a class="portal-link-inline" href="${esc(rec.dashboard_url)}" target="_blank" rel="noopener noreferrer">BrowserStack dashboard</a>` : ""}</div>
      </div>
      <label class="portal-form-label"><span class="portal-form-label">${planned ? "Segment" : "Recording name"}</span>
        <input class="portal-form-input" data-recording-name value="${esc(name)}" autocomplete="off" spellcheck="false" />
      </label>
      <p class="portal-inline-note">${planned
        ? "Save after each segment you listed; the name moves on to the next one."
        : "Record the whole scenario, then save it. The assistant proposes how to split it into segments in the chat, and you confirm."}</p>
      <div class="efp-card-meta" data-recording-summary>${esc(summaryText(rec.summary))}</div>
      <div class="efp-card-actions">
        ${inspector ? `<button type="button" class="portal-btn is-primary" data-recording-action="open-inspector"><i data-lucide="external-link" class="w-4 h-4"></i>Open Inspector</button>` : ""}
        <button type="button" class="portal-btn ${inspector ? "is-secondary" : "is-primary"}" data-recording-action="save-recording"><i data-lucide="save" class="w-4 h-4"></i>Save recording</button>
      </div>
      ${unsaved}
      <details class="portal-collapsible"${inspector ? "" : " open"}>
        <summary class="portal-collapsible-summary"><span>Record with the desktop Appium Inspector</span></summary>
        <ol class="portal-setup-guide-steps">
          <li>In Appium Inspector 2026.5.1 or later, choose <strong>Appium Server</strong>, enter the host, port, and path below, and leave SSL off.</li>
          <li>Open <strong>Attach to Session</strong>, pick this session (or paste its id), and attach.</li>
          <li>Tap and type in the Inspector, not on the screenshot; the bridge records it. Press <strong>Save recording</strong> here when you are done.</li>
        </ol>
        ${kvRow("Remote host", "127.0.0.1")}
        ${port ? kvRow("Remote port", port) : ""}
        ${kvRow("Remote path", `/mobile/wd/${rec.id}`)}
        ${rec.session_id ? kvRow("Session id", rec.session_id) : ""}
      </details>
      <details class="portal-collapsible">
        <summary class="portal-collapsible-summary"><span>Recorded somewhere else? Upload the recorder's code</span></summary>
        <p class="portal-inline-note">Code from Appium Inspector's recorder compiles too (Python is easiest), as one segment under the name above.</p>
        <div class="efp-card-actions">
          <input type="file" accept="${CODE_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-code />
          <button type="button" class="portal-btn is-secondary" data-recording-action="upload-code"><i data-lucide="upload" class="w-4 h-4"></i>Upload recorded code</button>
        </div>
      </details>
      ${doneList}
      <div class="efp-card-actions">
        <button type="button" class="portal-btn is-secondary" data-recording-action="extend"><i data-lucide="timer" class="w-4 h-4"></i>Hold 30 more minutes</button>
        <button type="button" class="portal-btn is-secondary" data-recording-action="finish"><i data-lucide="square" class="w-4 h-4"></i>Finish recording</button>
      </div>`;
  }

  function renderSession() {
    const root = panelRoot();
    if (!root) return;
    const startSection = root.querySelector("[data-recording-start-section]");
    const sessionSection = root.querySelector("[data-recording-session-section]");
    const target = root.querySelector("[data-recording-session]");
    if (startSection) startSection.classList.toggle("hidden", Boolean(view.recording));
    if (sessionSection) sessionSection.classList.toggle("hidden", !view.recording);
    if (!target) return;
    if (!view.recording) {
      target.innerHTML = "";
      view.sessionKey = "";
      return;
    }
    const summary = root.querySelector("[data-recording-summary]");
    if (summary) summary.textContent = summaryText(view.recording.summary);
    // Re-render only when the recording changes, so typing in the name field
    // is not interrupted by the poll.
    const rec = view.recording;
    const key = [rec.id, rec.session_id, rec.hold_deadline, rec.status, rec.progress, rec.segment, view.done.length, view.unsaved ? view.unsaved.name : "", view.bridge ? view.bridge.port : "", view.config ? view.config.inspector_available : ""].join("|");
    if (key === view.sessionKey) return;
    const focused = document.activeElement && document.activeElement.matches && document.activeElement.matches("[data-recording-name]");
    const typedName = focused ? document.activeElement.value : null;
    view.sessionKey = key;
    target.innerHTML = sessionCardHtml();
    if (typedName !== null) {
      const input = target.querySelector("[data-recording-name]");
      if (input) {
        input.value = typedName;
        input.focus();
      }
    }
    renderIcons();
  }

  function renderAll() {
    renderSetup();
    renderApps();
    renderOthers();
    renderSession();
    renderIcons();
  }

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
    renderSetup();
    if (!bridgeProblem(view.bridge)) {
      // A held recording shows up without waiting for the build list.
      await Promise.all([
        ready() ? loadApps() : Promise.resolve(),
        loadRecordings().then(() => {
          renderOthers();
          renderSession();
        }),
      ]);
    }
    renderAll();
  }

  function recordingGone(message) {
    view.recording = null;
    view.unsaved = null;
    remember();
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
        renderSetup();
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
      show("Recording", "<div class='portal-inline-state is-visible'>Select an assistant first.</div>", "recording");
      return;
    }
    if (view.agentId !== agentId) {
      Object.assign(view, {
        agentId, config: null, bridge: null, apps: null, appsError: "", selectedApp: "",
        recording: null, others: [], planned: [], done: [], unsaved: null, busy: "", sessionKey: "", polls: 0,
      });
    } else {
      view.sessionKey = "";
    }
    show("Recording", shellHtml(), "recording");
    const segments = panelRoot()?.querySelector("[data-recording-segments]");
    if (segments && view.planned.length) segments.value = view.planned.join("\n");
    renderAll();
    await refreshAll();
    schedule();
  }

  async function startBridge() {
    view.busy = "bridge";
    renderSetup();
    setStatus("Starting the local bridge… allow the efp-bridge link if Chrome asks.", "");
    try {
      view.bridge = await launchAndWait();
    } finally {
      view.busy = "";
    }
    if (bridgeProblem(view.bridge)) {
      setStatus("The bridge did not start. Install it from Connectors > Local bridge, or start it from there and check again.", "error");
      renderSetup();
      return;
    }
    setStatus("", "");
    await refreshAll();
  }

  async function startRecording(root, button) {
    if (!ensureReady()) return;
    const select = root.querySelector("[data-recording-app]");
    const app = select ? select.value : "";
    if (!app) {
      setStatus("Pick a build, or upload one first.", "error");
      return;
    }
    const platform = root.querySelector("[data-recording-platform]")?.value || "android";
    const device = (root.querySelector("[data-recording-device]")?.value || "").trim();
    view.planned = parseSegments(root.querySelector("[data-recording-segments]")?.value);
    view.done = [];
    const defaults = (view.config && view.config.defaults) || {};
    const params = { app, platform, segment: view.planned[0] || "recording-1" };
    if (device) params.device = device;
    if (defaults.network) params.network = defaults.network;
    if (Number(defaults.idle_timeout_seconds) > 0) params.idle_timeout_seconds = Number(defaults.idle_timeout_seconds);
    if (typeof defaults.video === "boolean") params.video = defaults.video;
    if (typeof defaults.interactive_debugging === "boolean") params.interactive_debugging = defaults.interactive_debugging;
    view.busy = "start";
    if (button) button.disabled = true;
    setStatus("Starting a BrowserStack device… about a minute, longer when all your parallel sessions are busy.", "");
    try {
      const started = await call("session.start", params, { credentials: credentials(), timeoutMs: 60000 });
      view.recording = started;
      view.unsaved = null;
      remember();
      renderAll();
      const recording = started.status === "starting" ? await waitForDevice(started.id) : started;
      const lines = [
        "/record-mobile-segment",
        "Recording on my computer through the Recording panel.",
        `App: ${recording.app || app} (${recording.platform || platform})`,
        recording.device ? `Device: ${recording.device}${recording.os_version ? ` ${recording.os_version}` : ""}` : "",
        view.planned.length ? `Segments: ${view.planned.join(", ")}` : "",
      ].filter(Boolean);
      sendChat(lines.join("\n"));
      const inspector = view.config && view.config.inspector_available;
      setStatus(`Device ready. ${inspector ? "Open the Inspector" : "Attach the desktop Inspector"} and record ${view.planned.length ? recording.segment : "the scenario"}.`, "success");
    } catch (error) {
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
  }

  function startFailureText(status) {
    const error = status && status.error ? status.error : {};
    return errorText(bridgeError({ code: error.code || "start_failed", message: error.message || "The device did not start.", hint: error.hint }));
  }

  // Asks the bridge after a starting recording every few seconds until the
  // device is up. Resolves with the active recording; rejects with the
  // bridge's error when the start failed, or with start_timeout.
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

  function currentName(root) {
    return (root.querySelector("[data-recording-name]")?.value || "").trim();
  }

  async function writeToWorkspace(fileName, blob) {
    const form = new FormData();
    form.append("path", RECORDINGS_DIR);
    form.append("file", new File([blob], fileName, { type: blob.type || "application/octet-stream" }));
    const response = await fetch(`/a/${encodeURIComponent(view.agentId)}/api/server-files/upload`, { method: "POST", credentials: "same-origin", body: form });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = body && (typeof body.detail === "string" ? body.detail : body.error);
      throw new Error(detail ? String(detail) : `HTTP ${response.status}`);
    }
    return `${RECORDINGS_DIR}/${fileName}`;
  }

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
      setStatus(error.code === "nothing_recorded" ? "Nothing recorded yet: tap and type in the Inspector attached through the bridge. What you do on BrowserStack\u2019s own site is not seen by the bridge." : errorText(error), "error");
    } finally {
      view.busy = "";
      if (button) button.disabled = false;
    }
    renderSession();
  }

  async function saveAgain() {
    const unsaved = view.unsaved;
    if (!unsaved) return;
    const path = await saveLog(unsaved.name, unsaved.log, unsaved.summary);
    setStatus(path ? `Saved ${unsaved.name}.` : `Still could not save ${unsaved.name}: ${view.unsaved.error}`, path ? "success" : "error");
    renderSession();
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

  async function confirmAction(message, confirmText) {
    if (typeof window.showConfirm === "function") return window.showConfirm({ title: "Finish recording?", message, confirmText, danger: true });
    return window.confirm(message);
  }

  async function finish(button) {
    const rec = view.recording;
    if (!rec) return;
    const pending = Number(rec.summary && rec.summary.actions) || 0;
    if (view.unsaved) {
      if (!(await confirmAction(`Recording ${view.unsaved.name} is not saved in the assistant's workspace. Finish anyway? Download it first to keep it.`, "Finish"))) return;
    } else if (pending > 0) {
      if (!(await confirmAction(`${pending} recorded ${pending === 1 ? "action is" : "actions are"} not saved. Finish without ${pending === 1 ? "it" : "them"}? Press Save recording first to keep ${pending === 1 ? "it" : "them"}.`, "Finish"))) return;
    }
    if (button) button.disabled = true;
    try {
      await call("session.finish", { id: rec.id }, { credentials: credentials(), timeoutMs: 120000 });
    } catch (error) {
      if (error.code !== "not_found") {
        setStatus(errorText(error), "error");
        if (button) button.disabled = false;
        return;
      }
    }
    view.recording = null;
    view.unsaved = null;
    remember();
    if (view.done.length) sendChat("Recording finished. Compile any segment not imported yet.");
    setStatus("Recording finished; the device is released.", "success");
    view.done = [];
    await loadRecordings();
    renderAll();
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
    remember();
    setStatus("Continuing that recording here; what you save goes into this assistant's workspace.", "success");
    renderAll();
  }

  // The proxy is shared by the connector page and the Recording panel; the
  // report goes to whichever status element the caller shows.
  // The proxy may carry the login a corporate proxy asks for. It is kept in
  // this browser's storage on this computer and goes only to the local
  // bridge, which hands it to mobile-auto and BrowserStack Local.
  function saveProxy(input, report) {
    const say = report || setStatus;
    const value = String(input.value || "").trim();
    let parsed = null;
    if (value) {
      try {
        parsed = new URL(value);
      } catch (_error) {
        parsed = null;
      }
      if (!parsed || !/^https?:$/.test(parsed.protocol)) {
        say("The proxy is a URL such as http://proxy.example.com:8080, with user:password@ in front of the host when the proxy asks for a login.", "error");
        return false;
      }
    }
    writeStorage(PROXY_KEY, value);
    const saved = parsed && (parsed.username || parsed.password) ? "Proxy saved for this computer; its login stays in this browser." : "Proxy saved for this computer.";
    say(value ? saved : "Using this computer's proxy settings.", "success");
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
    const button = target.closest("[data-recording-action]");
    const root = button && button.closest("[data-recording-root]");
    if (!button || !root) return;
    const action = button.dataset.recordingAction;
    if (action === "start-bridge") startBridge();
    else if (action === "check-bridge") refreshAll();
    else if (action === "upload-build") uploadBuildFromPanel(root, button);
    else if (action === "start") startRecording(root, button);
    else if (action === "open-inspector") openInspector();
    else if (action === "save-recording") saveRecording(root, button);
    else if (action === "save-again") saveAgain();
    else if (action === "download-log") downloadLog();
    else if (action === "upload-code") uploadCode(root);
    else if (action === "extend") extend(button);
    else if (action === "finish") finish(button);
    else if (action === "finish-other") finishOther(button.dataset.id || "", button);
    else if (action === "adopt") adopt(button.dataset.id || "");
  });

  document.addEventListener("change", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target && target.matches("[data-mobile-bridge-proxy]")) {
      const overview = target.closest("[data-mobile-overview]");
      const result = overview && overview.parentElement ? overview.parentElement.querySelector("[data-mobile-bridge-test-result]") : null;
      saveProxy(target, (text, tone) => setInline(result, text, tone));
      return;
    }
    if (!target || !target.closest("[data-recording-root]")) return;
    if (target.matches("[data-recording-app]")) {
      syncPlatform();
    } else if (target.matches("[data-recording-build]")) {
      const root = target.closest("[data-recording-root]");
      const custom = root.querySelector("[data-recording-custom-id]");
      const file = target.files && target.files[0];
      if (custom && file && !custom.value.trim()) custom.value = suggestCustomId(file.name);
    } else if (target.matches("[data-recording-proxy]")) {
      saveProxy(target);
    }
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
    suggestCustomId,
    probeBridge: probe,
    callBridge: call,
    waitForDevice,
  });
})();
