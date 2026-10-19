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

  // The proxy to BrowserStack from this computer: its address, and apart
  // from it the login a corporate proxy asks for. It is saved in the member's
  // Local bridge connector: the Mobile testing panel gets it with its
  // recording settings, the BrowserStack connector page from its fields.
  let bridgeProxy = { url: "", username: "", password: "" };

  function proxySettings() {
    return bridgeProxy;
  }

  function setBridgeProxy(value) {
    const source = value && typeof value === "object" ? value : {};
    bridgeProxy = { url: String(source.url || "").trim(), username: String(source.username || ""), password: String(source.password || "") };
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

  // The connector page renders the saved proxy into its fields; what they
  // hold is what the test from that page and the panel's calls use.
  function readProxyFields(container) {
    if (!container) return;
    const field = (name) => container.querySelector(`[data-bridge-proxy="${name}"]`);
    setBridgeProxy({
      url: field("url") ? field("url").value : "",
      username: field("username") ? field("username").value : "",
      password: field("password") ? field("password").value : "",
    });
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
      const wasAlive = Boolean(bridgeState.info);
      bridgeState.port = found ? found.port : 0;
      bridgeState.info = found ? found.data : null;
      bridgeState.probedAt = Date.now();
      bridgeState.probing = null;
      // The chat composer's bridge toggle probes on its own schedule; a
      // bridge that appeared or went away here is told to it at once.
      if (Boolean(found) !== wasAlive && window.portalConnectors && typeof window.portalConnectors.refreshToggle === "function") window.portalConnectors.refreshToggle();
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

  // The panel starts the bridge for its /mobile routes only: browser=off
  // keeps the EFP browser window (the chat's browser automation) closed; a
  // browser command from the chat opens it later.
  function launchBridge() {
    const anchor = document.createElement("a");
    anchor.href = `efp-bridge://start?origin=${encodeURIComponent(window.location.origin)}&port=${PORTS[0]}&browser=off`;
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
    renderLocalBinary(form.querySelector("[data-mobile-local-binary]"), null);
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
      renderLocalBinary(form.querySelector("[data-mobile-local-binary]"), plan.local_binary);
    } catch (error) {
      setInline(result, errorText(error), "error");
    } finally {
      button.disabled = false;
    }
  }

  // What the bridge found of BrowserStack Local on this computer (the tunnel
  // a recording of an app on the company's network needs), shown under the
  // test's result. An older bridge says nothing about it: then nothing shows.
  function localBinaryHtml(status) {
    if (!status || typeof status !== "object") return null;
    if (status.found) {
      return { tone: "success", html: `BrowserStack Local is on this computer${status.path ? ` (${esc(status.path)})` : ""}.` };
    }
    const name = status.name || "BrowserStackLocal";
    const folder = status.folder || "~/bin";
    const link = status.download_url
      ? `<a class="portal-link-inline" href="${esc(status.download_url)}" target="_blank" rel="noopener noreferrer">download it for this computer</a>`
      : "download it from BrowserStack";
    return {
      tone: "warning",
      html: `BrowserStack Local is not on this computer. Recording an app on your company's network needs it: mobile-auto downloads it when such a recording starts, and when your network blocks that download, ${link}, unzip it, and put <code>${esc(name)}</code> in <code>${esc(folder)}</code> (the local bridge's folder) or on your PATH.`,
    };
  }

  function renderLocalBinary(el, status) {
    if (!el) return;
    const view = localBinaryHtml(status);
    el.className = view ? `portal-inline-state is-visible is-${view.tone}` : "portal-inline-state";
    el.innerHTML = view ? view.html : "";
  }

  async function refreshOverview(root) {
    const status = root.querySelector("[data-mobile-bridge-status]");
    const start = root.querySelector('[data-mobile-bridge-action="start"]');
    readProxyFields(root);
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
  // replaying scenario scripts on it. Each test scenario is one script that
  // replays its recording exactly, kept in mobile/scripts/<platform>/; older
  // segments in mobile/segments/<platform>/ still list and replay. The chat
  // is where the assistant compiles a recording into its script, reviews a
  // replay, and exports the tests; the panel's progress strip says which of
  // those comes next.
  //
  // 1. Start: session.start answers at once with the recording "starting";
  //    the panel polls session.status until it is "active" (or "failed", with
  //    the error). When Portal hosts the Inspector, Start also opens a tab
  //    that lands in the Inspector, attached to the device, once it is ready.
  // 2. Record: the member records one scenario from the app's first screen to
  //    its result; "Save recording" takes the log from the bridge
  //    (segment.done) and writes it into the assistant's workspace. The chat
  //    message tells the assistant: the recording of a script (a name the
  //    member listed, or a script of the plan), which it compiles into that
  //    script, or a recording whose scenario it finds out first.
  // 3. Replay: a scenario script runs on the held device on its own, from the
  //    start the member chose (segment.replay); older segments chain. The
  //    result goes into the workspace and the chat, where the assistant
  //    reviews it.
  // 4. Finish releases the device.

  const PANEL_TITLE = "Mobile testing";
  const RECORDINGS_DIR = "mobile/recordings";
  const SEGMENTS_DIR = "mobile/segments";
  const SCRIPTS_DIR = "mobile/scripts";
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
    releasing: null,
    releasingOther: "",
    sessionKey: "",
    formKey: "",
    polls: 0,
    replay: freshReplay(),
    // flow is what the workspace says about the steps after recording: the
    // latest split, the compiled segments, and the scenario plan.
    flow: null,
    connectionOpen: false,
    // uploadOpen is the start form's upload area, shown by its button.
    uploadOpen: false,
    // inspectorTab is the tab Start opened to land in the Inspector.
    inspectorTab: null,
    // workspace is the overlay that holds the Inspector inside Portal.
    workspace: null,
    // tab is Record (the device and the step at hand) or Library (what the
    // workspace keeps: scripts, recordings, older segments, replays).
    tab: "record",
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

  function parseSegments(text) {
    return String(text || "")
      .split(/[\n,]+/)
      .map((item) => item.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+/, ""))
      .filter(Boolean)
      .slice(0, 30);
  }

  // The chat message for a saved recording this computer did not compile: a
  // name the member listed, or a script the scenario plan lists, is that
  // scenario's recording, which the assistant compiles; any other is a
  // recording whose scenario the assistant finds out first.
  function savedMessage(name, path, planned) {
    const names = Array.isArray(planned) ? planned : view.planned.concat(planScriptNames());
    return names.indexOf(name) >= 0 ? `Script ${name} recorded: ${path}` : `Recording ${name} saved: ${path}`;
  }

  // The scripts the scenario plan lists for the platform at hand.
  function planScriptNames() {
    const plan = view.flow && view.flow.plan;
    return plan ? plan.scripts.map((item) => item.name) : [];
  }

  // A plan's scenario scripts. A plan from the older flow lists reusable
  // segments under `segments` instead: those are not scripts, and the
  // assistant rewrites such a plan before anything is recorded from it.
  function planEntries(plan) {
    return plan && Array.isArray(plan.scripts) ? plan.scripts : [];
  }

  // The message that asks the assistant for a copy of a script with other
  // values: another scenario that does the same with them. The script's path
  // says which platform's file to copy.
  function copyMessage(name, changes, path) {
    const line = `Copy script ${name} as a variant: ${String(changes || "").replace(/\s+/g, " ").trim()}`;
    return path ? `${line}\nScript: ${path}` : line;
  }

  // The message that hands a script this computer compiled to the assistant
  // for review, with the compiler's report.
  function compiledMessage(name, path, reportPath) {
    return `Script ${name} compiled: ${path}\nReport: ${reportPath}`;
  }

  // The message that asks the assistant what to record next and how. The
  // panel shows the plan's next scenario itself, so it goes on request.
  function startMessage(rec) {
    return [
      "/record-mobile-segment",
      "Recording on my computer through the Mobile testing panel.",
      `App: ${rec.app || ""} (${rec.platform || recordingPlatform()})`,
      rec.device ? `Device: ${rec.device}${rec.os_version ? ` ${rec.os_version}` : ""}` : "",
      view.planned.length ? `Scripts: ${view.planned.join(", ")}` : "",
    ].filter(Boolean).join("\n");
  }

  // A plan as the panel reads it: its issue, its scripts (name, scenario,
  // and which script a copy comes from), and its scenarios' words.
  function planView(doc) {
    const scenarios = {};
    (Array.isArray(doc && doc.scenarios) ? doc.scenarios : []).forEach((sc) => {
      if (!sc || !sc.id) return;
      const list = (v) => (Array.isArray(v) ? v.map(String) : []);
      scenarios[String(sc.id)] = { title: String(sc.title || ""), given: list(sc.given), when: list(sc.when), then: list(sc.then) };
    });
    return {
      key: doc ? String(doc.__key || "") : "",
      issue: doc ? String(doc.issue || "") : "",
      scripts: planEntries(doc).map((seg) => ({ name: String(seg.name || ""), scenario: String(seg.scenario || ""), copyOf: String(seg.copy_of || ""), status: "to_record" })).filter((seg) => seg.name),
      scenarios,
      pipeline: doc && doc.pipeline && typeof doc.pipeline === "object" ? doc.pipeline : {},
    };
  }

  // The scenario a recording saved under a name is: the plan's script of
  // that name, else <KEY>-<id> read from the name itself; null when neither.
  function scriptScenario(name, plan) {
    const entry = plan && Array.isArray(plan.scripts) ? plan.scripts.find((item) => item.name === name) : null;
    if (entry && entry.scenario && plan.issue) {
      const scenario = plan.scenarios && plan.scenarios[entry.scenario];
      return { issue: plan.issue, scenario: entry.scenario, title: scenario ? scenario.title : "" };
    }
    const m = /^([A-Za-z][A-Za-z0-9_]*-\d+)-([A-Za-z0-9][A-Za-z0-9._-]*)$/.exec(String(name || ""));
    return m ? { issue: m[1], scenario: m[2], title: "" } : null;
  }

  // A script's status, from the files rather than a word in the plan: to
  // record without a file; replayed when the latest replay of the file as it
  // is now (the report item's script_sha256 is the file's) passed; else it
  // exists. `replays` come newest first. mobile-auto plan status derives the
  // same for the assistant.
  function scriptStatus(file, replays, platform, name) {
    if (!file || !file.exists) return "to_record";
    const row = { name, kind: "script" };
    for (const rp of Array.isArray(replays) ? replays : []) {
      if (rp.platform !== platform) continue;
      const seg = rp.segments.find((item) => sameReplayRow(item, row) && item.sha256 && item.sha256 === file.hash);
      if (seg) return seg.status === "passed" ? "replayed" : "exists";
    }
    return "exists";
  }

  // SHA-256 of a text, as the bridge hashes the YAML it replays. Without
  // Web Crypto (an origin that is not secure) it is "", and no script counts
  // as replayed here.
  async function sha256Hex(text) {
    const subtle = globalThis.crypto && globalThis.crypto.subtle;
    if (!subtle) return "";
    const digest = await subtle.digest("SHA-256", new TextEncoder().encode(String(text)));
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  // Whether a name is one of the platform's scenario scripts (else an older
  // segment).
  function itemKind(name) {
    return view.flow && Array.isArray(view.flow.scripts) && view.flow.scripts.indexOf(name) >= 0 ? "script" : "segment";
  }

  // What a replay ran, in words: scripts when every file was one.
  function replayNoun(items) {
    const list = Array.isArray(items) ? items : [];
    const kinds = list.map((item) => item.kind || itemKind(item.name));
    const one = list.length === 1;
    if (list.length > 0 && kinds.every((kind) => kind === "script")) return one ? "script" : "scripts";
    if (list.length > 0 && kinds.every((kind) => kind === "recording")) return one ? "recording" : "recordings";
    return one ? "segment" : "segments";
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
        <div class="portal-inline-state" data-recording-status role="status"></div>
        <section class="efp-mobile-section hidden" data-recording-record></section>
        <section class="efp-mobile-section hidden" data-recording-library></section>
        <div data-recording-dialog-host></div>
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
    renderIcons();
  }

  function renderTabs() {
    const target = panelRoot()?.querySelector("[data-recording-tabs]");
    if (!target) return;
    const tab = (key, label) => `<button type="button" role="tab" data-recording-tab="${key}" aria-selected="${view.tab === key ? "true" : "false"}">${label}</button>`;
    target.innerHTML = tab("record", "Record") + tab("library", "Library");
  }

  function switchTab(tab) {
    view.tab = tab === "library" ? "library" : "record";
    renderTabs();
    renderDevice();
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

  // The bar names the bridge, or what is wrong with it.
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
    bar.innerHTML = `<div class="efp-mobile-bar-main">${main}</div>`;
  }

  function startLabel() {
    return inspectorAvailable() ? "Start and open Inspector" : "Start recording device";
  }

  function startFormHtml() {
    return `
      <div class="efp-card efp-device">
        <div class="efp-card-head"><strong class="efp-card-title">Start a device</strong></div>
        <p class="efp-card-meta">The local bridge starts a BrowserStack device with the build and holds it while you record and replay.</p>
        <div class="efp-field">
          <div class="efp-field-head"><span class="portal-form-label">Build</span><button type="button" class="composer-pill-btn" data-recording-action="upload-toggle" aria-expanded="${view.uploadOpen ? "true" : "false"}" title="Upload an .apk, .aab or .ipa to BrowserStack"><i data-lucide="upload" class="w-3 h-3"></i>Upload a build</button></div>
          <select class="portal-form-select" data-recording-app aria-label="Build"><option value="">Looking for your builds…</option></select>
        </div>
        <div class="efp-upload${view.uploadOpen ? "" : " hidden"}" data-recording-upload>
          <input type="file" accept="${BUILD_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-build />
          <label class="portal-form-label"><span class="portal-form-label">Custom id (optional)</span>
            <input class="portal-form-input" data-recording-custom-id placeholder="fxapp-android-uat" autocomplete="off" spellcheck="false" />
          </label>
          <p class="portal-inline-note">A custom id names the latest build uploaded with it, so recordings and the pipeline keep using the same name. BrowserStack keeps builds for 30 days.</p>
          <div class="portal-progress hidden" data-recording-upload-progress><i></i></div>
          <div class="efp-card-actions">
            <button type="button" class="portal-btn is-secondary" data-recording-action="upload-build"><i data-lucide="upload" class="w-4 h-4"></i>Upload to BrowserStack</button>
            <button type="button" class="portal-btn" data-recording-action="upload-toggle">Cancel</button>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <label class="portal-form-label"><span class="portal-form-label">Platform</span>
            <select class="portal-form-select" data-recording-platform><option value="android">Android</option><option value="ios">iOS</option></select>
          </label>
          <label class="portal-form-label"><span class="portal-form-label">Device (optional)</span>
            <input class="portal-form-input" data-recording-device-name placeholder="Google Pixel 8" />
          </label>
        </div>
        <details class="portal-collapsible">
          <summary class="portal-collapsible-summary"><span>Script names (optional)</span></summary>
          <div class="portal-panel-stack">
            <textarea class="portal-form-textarea" rows="2" data-recording-segments placeholder="FX-12-buy-100-usd&#10;FX-12-over-daily-limit-usd"></textarea>
            <p class="portal-inline-note">One recording is one scenario, from the app's first screen to its result. List the scenario scripts you are going to record, in order, as the assistant names them (the issue key and the scenario id): each save goes under the next name. Leave it empty to type the name when you save.</p>
          </div>
        </details>
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-primary" data-recording-action="start"><i data-lucide="play" class="w-4 h-4"></i>${esc(startLabel())}</button>
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
          <button type="button" class="portal-btn is-secondary" data-recording-action="finish-other" data-id="${esc(rec.id)}"${view.releasingOther === rec.id ? " disabled" : ""}>${view.releasingOther === rec.id ? `<i data-lucide="loader-circle" class="w-4 h-4 animate-spin"></i>Releasing…` : "Release the device"}</button>
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

  // The card while the device is being released: nothing to do but wait.
  function releasingCardHtml(rec) {
    const since = view.releasing.since;
    return `
      <div class="efp-card efp-device">
        <div class="efp-card-head">
          <span class="portal-status-badge is-warning">Releasing</span>
          <strong class="efp-card-title">${esc(rec.device || "BrowserStack device")}</strong>
          <span class="efp-card-meta">${esc(platformLabel(rec))}${rec.os_version ? ` ${esc(rec.os_version)}` : ""}</span>
        </div>
        <div class="efp-card-meta efp-device-replay"><i data-lucide="loader-circle" class="w-3 h-3 animate-spin"></i><span>Ending the BrowserStack session and releasing the device… <span data-recording-countdown data-since="${esc(String(since))}">${esc(elapsedText(since))}</span></span></div>
        <p class="portal-inline-note">${view.releasing.replaying ? "The running replay finishes its step and hands the device back first." : "Usually under a minute."} Recordings saved in the assistant's workspace stay.</p>
      </div>`;
  }

  function deviceCardHtml(rec) {
    if (view.releasing) return releasingCardHtml(rec);
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
        ${replayLineHtml()}
        ${view.connectionOpen ? connectionHtml(rec) : ""}
      </div>`;
  }

  function renderDevice() {
    const target = panelRoot()?.querySelector("[data-recording-device]");
    if (!target) return;
    const rec = view.recording;
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
    if (rec.status === "starting") updateInspectorTab(rec.progress || "Starting the device on BrowserStack");
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
      const [recordings, segments, scripts, scenarios, replays] = await Promise.all([
        listWorkspace(RECORDINGS_DIR),
        listWorkspace(`${SEGMENTS_DIR}/${platform}`),
        listWorkspace(`${SCRIPTS_DIR}/${platform}`),
        listWorkspace(SCENARIOS_DIR),
        loadReplayReports(),
      ]);
      const files = (items) => items.filter((item) => item.is_file !== false);
      const yamlNames = (items) => files(items).filter((item) => /\.ya?ml$/i.test(item.name || "")).map((item) => String(item.name).replace(/\.ya?ml$/i, ""));
      const segmentNames = yamlNames(segments);
      const scriptNames = yamlNames(scripts);
      // Each script's status comes from its file and the replays of it.
      const statuses = {};
      const reportPaths = {};
      (await scriptRows(scripts, SCRIPTS_DIR, platform, "script", replays)).forEach((row) => {
        statuses[row.name] = row.status;
        if (row.reportPath) reportPaths[row.name] = row.reportPath;
      });
      const splits = files(recordings).filter((item) => /\.split\.json$/.test(item.name || ""));
      splits.sort((a, b) => String(b.modified_at || "").localeCompare(String(a.modified_at || "")));
      let split = null;
      for (const item of splits) {
        try {
          const doc = JSON.parse(await readWorkspaceText(`${RECORDINGS_DIR}/${item.name}`));
          if (doc.platform && doc.platform !== platform) continue;
          const parts = (Array.isArray(doc.parts) ? doc.parts : []).map((part) => String(part.segment || "")).filter(Boolean);
          split = { name: item.name, parts, compiled: parts.filter((name) => scriptNames.includes(name) || segmentNames.includes(name)) };
          break;
        } catch (_error) {
          /* a split the assistant is still writing */
        }
      }
      const plans = await readPlans(scenarios);
      const app = view.recording ? view.recording.app : "";
      const chosen = plans.find((doc) => app && doc.apps && doc.apps[platform] === app) || plans[0] || null;
      const plan = chosen ? planView(chosen) : null;
      if (plan) plan.scripts.forEach((seg) => { seg.status = statuses[seg.name] || "to_record"; });
      view.flow = {
        key,
        platform,
        recordings: files(recordings).filter((item) => /\.wdlog\.json$/.test(item.name || "")).length,
        scripts: scriptNames,
        segments: segmentNames,
        statuses,
        reportPaths,
        replays,
        split,
        plan,
      };
    } catch (_error) {
      view.flow = { key, platform, error: true, recordings: 0, scripts: [], segments: [], statuses: {}, reportPaths: {}, replays: [], split: null, plan: null };
    }
    renderFlow();
    renderRecord();
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
        if (doc && (Array.isArray(doc.scripts) || Array.isArray(doc.segments))) plans.push(Object.assign({ __key: dir.name }, doc));
      } catch (_error) {
        /* not a plan */
      }
    }
    plans.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    return plans;
  }

  // Whether a plan lists a script.
  function planLists(plan, name) {
    return planEntries(plan).some((item) => String(item.name || "") === name);
  }

  // The replays in the workspace, newest first, with what each one ran and
  // the hash of the file it ran.
  async function loadReplayReports() {
    const replayItems = await listWorkspace(REPLAYS_DIR);
    const dirs = replayItems.filter((item) => item.is_dir || item.type === "directory").sort((a, b) => String(b.name).localeCompare(String(a.name))).slice(0, LIBRARY_MAX_REPLAYS);
    const replays = (await Promise.all(dirs.map(async (dir) => {
      const path = `${REPLAYS_DIR}/${dir.name}/report.json`;
      try {
        const report = JSON.parse(await cachedText(path, dir.modified_at));
        return {
          id: String(dir.name), path, dir: `${REPLAYS_DIR}/${dir.name}`, status: String(report.status || ""), platform: String(report.platform || ""),
          device: String(report.device || ""), finishedAt: String(report.finished_at || ""), start: String(report.start || ""),
          segments: (Array.isArray(report.segments) ? report.segments : []).map((seg) => ({ name: String(seg.name || ""), kind: String(seg.kind || ""), status: String(seg.status || ""), sha256: String(seg.script_sha256 || "") })),
          failure: report.failure && typeof report.failure === "object" ? { segment: String(report.failure.segment || ""), step: Number(report.failure.step) || 0 } : null,
        };
      } catch (_error) {
        return { id: String(dir.name), path, dir: `${REPLAYS_DIR}/${dir.name}`, status: "", platform: "", segments: [], broken: true };
      }
    }))).filter(Boolean);
    // Newest first by the report's clock; the folder name only breaks ties.
    replays.sort((a, b) => String(b.finishedAt || "").localeCompare(String(a.finishedAt || "")) || String(b.id).localeCompare(String(a.id)));
    return replays;
  }

  // The scenario scripts (or older segments) of one platform, each with what
  // its file says, its latest replay, and its status derived from them.
  async function scriptRows(list, dir, platform, kind, replays) {
    const files = (items) => items.filter((item) => item.is_file !== false && !(item.is_dir || item.type === "directory"));
    const names = new Set(files(list).map((item) => String(item.name || "")));
    const items = files(list).filter((item) => /\.ya?ml$/i.test(item.name || "")).slice(0, LIBRARY_MAX_FILES);
    return Promise.all(items.map(async (item) => {
      const name = String(item.name).replace(/\.ya?ml$/i, "");
      const path = `${dir}/${platform}/${item.name}`;
      let parsed = { steps: 0, grade: "", needsReview: 0, compiledAt: "", scenario: "", description: "", copiedFrom: "", sourceFile: "" };
      let hash = "";
      try {
        const text = await cachedText(path, item.modified_at);
        parsed = parseSegmentYaml(text);
        hash = await sha256Hex(text);
      } catch (_error) {
        /* listed without its details */
      }
      const row = { name, kind };
      const latest = replays.find((rp) => rp.platform === platform && rp.segments.some((seg) => sameReplayRow(seg, row) && seg.status !== "not_run")) || null;
      const latestSeg = latest ? latest.segments.find((seg) => sameReplayRow(seg, row)) : null;
      const reportName = `${name}.report.json`;
      return {
        name, path, platform, kind, hash, modifiedAt: String(item.modified_at || ""), steps: parsed.steps, grade: parsed.grade, needsReview: parsed.needsReview,
        compiledAt: parsed.compiledAt || String(item.modified_at || ""),
        status: kind === "script" ? scriptStatus({ exists: true, hash }, replays, platform, name) : "",
        reportPath: names.has(reportName) ? `${dir}/${platform}/${reportName}` : "",
        scenario: parsed.scenario, description: parsed.description, copiedFrom: parsed.copiedFrom, sourceFile: parsed.sourceFile,
        lastReplay: latestSeg ? { status: latestSeg.status, at: latest.finishedAt, current: Boolean(hash) && latestSeg.sha256 === hash, step: latest.failure && latest.failure.segment === name ? latest.failure.step : 0 } : null,
      };
    }));
  }

  // The four steps after a device is up, each with where it stands.
  function flowSteps() {
    const flow = view.flow || { recordings: 0, scripts: [], segments: [], split: null, plan: null };
    const rec = view.recording;
    const unsaved = rec ? Number(rec.summary && rec.summary.actions) || 0 : 0;
    const savedNow = view.done.length;
    const record = {};
    if (!rec) record.text = flow.recordings ? `${flow.recordings} saved` : "start a device";
    else if (savedNow) record.text = `${savedNow} saved${unsaved ? `, ${unsaved} not saved` : ""}`;
    else record.text = unsaved ? `${unsaved} ${unsaved === 1 ? "action" : "actions"} not saved` : "record in the Inspector";
    record.done = savedNow > 0 || (!rec && flow.recordings > 0);

    const compile = {};
    const planned = flow.plan ? flow.plan.scripts.filter((seg) => seg.status !== "to_record") : [];
    if (flow.split && flow.split.parts.length && flow.split.compiled.length < flow.split.parts.length) {
      compile.text = "approve the cut in the chat";
    } else if (flow.scripts.length) {
      compile.text = `${flow.scripts.length} ${flow.scripts.length === 1 ? "script" : "scripts"}`;
      compile.done = true;
    } else if (flow.segments.length) {
      compile.text = `${flow.segments.length} ${flow.segments.length === 1 ? "segment" : "segments"}`;
      compile.done = true;
    } else if (record.done) {
      compile.text = "compile each recording as its scenario in the Library";
    } else {
      compile.text = "after recording";
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
    } else if (compile.done) {
      replay.text = flow.scripts.length ? "replay the scripts" : "replay the segments";
    } else {
      replay.text = "after compiling";
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
      { label: "Compile", ...compile },
      { label: "Replay", ...replay },
      { label: "Export", ...scripts },
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
    if (view.tab !== "record" || (!view.recording && !(view.flow && (view.flow.recordings || view.flow.scripts.length || view.flow.segments.length)))) {
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
      ? `<ul class="efp-recording-done">${view.done.map((item) => `<li><i data-lucide="check" class="w-3 h-3"></i> ${esc(item.name)} <span class="efp-card-meta">${esc(item.detail)}</span>${doneActionsHtml(item)}</li>`).join("")}</ul>`
      : "";
    return `
      <div class="efp-mobile-section-head">
        <h5>Record</h5>
        <span class="efp-card-meta" data-recording-summary>${esc(summaryText(rec.summary))}</span>
      </div>
      <p class="portal-inline-note">${planned
        ? "Save when the scenario reaches its result; the name moves on to the next script you listed."
        : "Everything you do through the Inspector is recorded; there is nothing to start there. Record one scenario from the app's first screen to its result, then save it. Replay the recording as it is to check it, compile it as its scenario from the Library, and press Review once the script passes. Saved under its scenario's name, it is compiled at once."}</p>
      ${nextToRecordHtml()}
      <div class="efp-record-name">
        <label class="portal-form-label"><span class="portal-form-label">${planned ? "Script" : "Save as"}</span>
          <input class="portal-form-input" data-recording-name value="${esc(name)}" list="efp-script-names" autocomplete="off" spellcheck="false" />
          <datalist id="efp-script-names">${recordNameSuggestions().map((item) => `<option value="${esc(item)}"></option>`).join("")}</datalist>
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
          <p class="portal-inline-note">Code from Appium Inspector's recorder keeps no screens, so it cannot become a scenario script; it compiles as an older segment under the name above.</p>
          <div class="efp-card-actions">
            <input type="file" accept="${CODE_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-code />
            <button type="button" class="portal-btn is-secondary" data-recording-action="upload-code"><i data-lucide="upload" class="w-4 h-4"></i>Upload recorded code</button>
          </div>
        </div>
      </details>`;
  }

  // What a saved recording's line offers once it is compiled: replay the
  // script, and ask the assistant for the review.
  function doneActionsHtml(item) {
    if (item.code) return "";
    if (!item.compiled || !item.scriptPath) {
      return ` <button type="button" class="composer-pill-btn" data-recording-action="replay-recording" data-name="${esc(item.name)}" title="Tick it under Replay, to replay it as it is">Replay</button> <button type="button" class="composer-pill-btn" data-recording-action="compile-recording" data-name="${esc(item.name)}" title="Compile it as one of the plan's scenarios, in the Library">Compile</button>`;
    }
    const review = item.reviewed
      ? `<span class="efp-card-meta">review asked</span>`
      : `<button type="button" class="composer-pill-btn" data-recording-action="review-script" data-name="${esc(item.name)}" data-path="${esc(item.scriptPath)}" data-report="${esc(item.reportPath || "")}" title="Ask the assistant to review the script: label its steps with the plan's and add the expected-result checks">Review</button>`;
    return ` <button type="button" class="composer-pill-btn" data-recording-action="replay-script" data-name="${esc(item.name)}" title="Tick it under Replay">Replay</button> ${review}`;
  }

  // Hands a compiled script to the assistant for review, with the
  // compiler's report next to it.
  function requestReview(name, scriptPath, reportPath) {
    sendChat(compiledMessage(name, scriptPath, reportPath || `${scriptPath.replace(/\.ya?ml$/i, "")}.report.json`));
    view.done.forEach((item) => {
      if (item.name === name) item.reviewed = true;
    });
    remember();
    setStatus(`Asked the assistant to review ${name}. Its edits need one more replay to pass.`, "success");
    renderRecord();
    renderIcons();
  }

  // The plan's scripts still to record on this platform, in the plan's
  // order, after the names listed at the start: what Save as offers.
  function recordNameSuggestions() {
    const plan = view.flow && view.flow.plan;
    const planned = plan ? plan.scripts.filter((item) => item.status === "to_record" && !item.copyOf).map((item) => item.name) : [];
    return [...new Set(view.planned.concat(planned))];
  }

  // The next scenario the plan wants recorded on this platform, with its
  // steps in the plan's words; a copy is not recorded.
  function nextToRecord() {
    const plan = view.flow && view.flow.plan;
    if (!plan) return null;
    const entry = plan.scripts.find((item) => item.status === "to_record" && !item.copyOf && !view.done.some((done) => done.name === item.name));
    if (!entry) return null;
    const scenario = plan.scenarios[entry.scenario] || { title: "", given: [], when: [], then: [] };
    const steps = [].concat(scenario.given.map((t) => `Given ${t}`), scenario.when.map((t) => `When ${t}`), scenario.then.map((t) => `Then ${t}`));
    return { name: entry.name, title: scenario.title, steps };
  }

  function nextToRecordHtml() {
    const ask = `<button type="button" class="composer-pill-btn" data-recording-action="ask-assistant" title="Ask the assistant what to record and how">Ask the assistant</button>`;
    const plan = view.flow && view.flow.plan;
    if (!plan) return `<p class="portal-inline-note" data-recording-next>No scenario plan in the workspace yet; the assistant writes one from the Jira story. ${ask}</p>`;
    const next = nextToRecord();
    if (!next) return `<p class="portal-inline-note" data-recording-next>Every scenario of the plan has a script on this platform. ${ask}</p>`;
    return `<div class="efp-lib-card" data-recording-next>
        <div class="efp-lib-main"><strong>Next: ${esc(next.name)}</strong>${next.title ? `<span class="efp-card-meta">${esc(next.title)}</span>` : ""}</div>
        ${next.steps.length ? `<ol class="efp-next-steps">${next.steps.map((step) => `<li>${esc(step)}</li>`).join("")}</ol>` : ""}
        <div class="efp-card-actions">
          <button type="button" class="composer-pill-btn" data-recording-action="use-name" data-name="${esc(next.name)}" title="Save the recording under this name">Save as ${esc(next.name)}</button>
          ${ask}
        </div>
      </div>`;
  }

  // Opens the Library on a recording's scenario choice.
  async function openCompile(name) {
    const lib = view.library;
    lib.open[`compile:${RECORDINGS_DIR}/${name}.wdlog.json`] = true;
    switchTab("library");
    if (!lib.loaded) await loadLibrary();
    else {
      renderLibrary();
      renderIcons();
    }
  }

  function askAssistant() {
    const rec = view.recording;
    if (!rec) {
      setStatus("Start a device first.", "error");
      return;
    }
    sendChat(startMessage(rec));
    setStatus("Asked the assistant what to record next.", "success");
  }

  function useName(root, name) {
    const input = root.querySelector("[data-recording-name]");
    if (!input || !name) return;
    input.value = name;
    input.focus();
  }

  function renderRecord() {
    const target = panelRoot()?.querySelector("[data-recording-record]");
    if (!target) return;
    const rec = view.recording;
    if (!rec || rec.status !== "active" || view.tab !== "record" || view.releasing) {
      target.innerHTML = "";
      target.classList.add("hidden");
      view.sessionKey = "";
      return;
    }
    target.classList.remove("hidden");
    const summary = target.querySelector("[data-recording-summary]");
    if (summary) summary.textContent = summaryText(rec.summary);
    // Re-render only when the recording changes, so typing in the name field
    // is not interrupted by the poll. The plan's next scenario is part of the
    // card, so a change of the plan's statuses re-renders it too, keeping
    // the name typed so far.
    const doneKey = view.done.map((item) => `${item.name}:${item.compiled ? "c" : ""}${item.reviewed ? "r" : ""}`).join(",");
    const recKey = [rec.id, rec.session_id, rec.status, rec.segment, doneKey, view.unsaved ? view.unsaved.name : "", view.planned.length, inspectorAvailable(), rec.replay ? rec.replay.status : ""].join("|");
    const flow = view.flow;
    const flowKey = flow ? [flow.key, flow.plan ? flow.plan.scripts.map((item) => `${item.name}:${item.status}`).join(",") : "none"].join("|") : "";
    const key = `${recKey}#${flowKey}`;
    if (key === view.sessionKey) return;
    const focused = document.activeElement && document.activeElement.matches && document.activeElement.matches("[data-recording-name]");
    const current = target.querySelector("[data-recording-name]");
    const sameRecording = view.sessionKey.split("#")[0] === recKey;
    const typedName = focused ? document.activeElement.value : (sameRecording && current ? current.value : null);
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

  // ---- loading -----------------------------------------------------------------------

  async function loadConfig() {
    try {
      const response = await fetch(`/api/mobile/recording-config?agent_id=${encodeURIComponent(view.agentId)}`, { credentials: "same-origin", cache: "no-store" });
      if (response.ok) {
        view.config = await response.json();
        setBridgeProxy(view.config.bridge_proxy);
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
        recording: null, others: [], planned: [], done: [], unsaved: null, busy: "", releasing: null, releasingOther: "", sessionKey: "", formKey: "", polls: 0,
        replay: freshReplay(), flow: null, connectionOpen: false, uploadOpen: false, inspectorTab: null,
        tab: "record", library: freshLibrary(),
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
    setStatus("Starting the local bridge… allow the efp-bridge link if Chrome asks.", "");
    try {
      view.bridge = await launchAndWait();
    } finally {
      view.busy = "";
    }
    if (bridgeProblem(view.bridge)) {
      setStatus("The bridge did not start. Install it from Connectors > Local bridge, or start it from there and check again.", "error");
      renderBar();
      return;
    }
    setStatus("", "");
    await refreshAll();
  }

  // ---- starting a device ------------------------------------------------------------------

  // A tab opened while the member's click is still being handled is not
  // blocked as a pop-up; it waits for the device and then lands in the
  // Inspector.
  // What the pre-opened tab shows while the device starts (the Inspector
  // replaces it); updateInspectorTab keeps its status line current.
  const INSPECTOR_TAB_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>Appium Inspector</title><style>
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f6f7f9;color:#1f2933;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{max-width:440px;padding:32px;text-align:center}
.phone{width:72px;height:128px;margin:0 auto 20px;border:3px solid #1f2933;border-radius:14px;position:relative;overflow:hidden;background:#fff}
.phone::before{content:"";position:absolute;left:50%;top:6px;width:20px;height:3px;margin-left:-10px;border-radius:2px;background:#1f2933;z-index:1}
.phone .screen{position:absolute;left:0;right:0;bottom:0;height:0;background:linear-gradient(180deg,#4f7cff,#8fb0ff);animation:efp-fill 2.4s ease-in-out infinite}
@keyframes efp-fill{0%{height:0;opacity:1}60%{height:100%;opacity:1}100%{height:100%;opacity:.35}}
.dots span{display:inline-block;width:8px;height:8px;margin:0 3px;border-radius:50%;background:#4f7cff;animation:efp-pulse 1.2s infinite}
.dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}
@keyframes efp-pulse{0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}
h1{font-size:18px;margin:0 0 8px}
p{margin:6px 0;font-size:14px;line-height:1.5;color:#52606d}
.status{font-weight:600;color:#1f2933;min-height:21px}
.hint{margin-top:18px;padding-top:14px;border-top:1px solid #e4e7eb;font-size:13px;text-align:left}
</style></head><body><div class="card">
<div class="phone"><div class="screen"></div></div>
<h1>Starting your BrowserStack device</h1>
<p class="status" id="efp-status">Asking BrowserStack for a device</p>
<p class="dots"><span></span><span></span><span></span></p>
<p>Keep this tab open: it becomes Appium Inspector, attached to the device, as soon as the device is ready. Usually about a minute; longer when all your parallel sessions are busy.</p>
<div class="hint"><strong>While you record</strong><br>Everything you do through the Inspector is recorded by the local bridge; there is nothing to start in the Inspector itself. When you are done, go back to the Portal tab and press Save recording.</div>
</div></body></html>`;

  function updateInspectorTab(text) {
    const tab = view.inspectorTab;
    if (!tab || tab.closed) return;
    try {
      const line = tab.document.getElementById("efp-status");
      if (line) line.textContent = text;
    } catch (_error) {
      /* the tab has moved on to the Inspector */
    }
  }

  function openInspectorTabEarly() {
    if (!inspectorAvailable()) return null;
    let tab = null;
    try {
      tab = window.open("", "_blank");
      if (tab) {
        tab.document.write(INSPECTOR_TAB_HTML);
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
  // assistant exports them. The panel lists the scenario scripts in the
  // assistant's workspace for the recording's platform, then the older
  // segments, asks for the values of the secrets they type (kept in this
  // page's memory only and sent to the bridge alone), and has the bridge
  // replay them: one scenario script on its own, or segments in a chain. It
  // borrows the device from the Inspector and gives it back when the replay
  // ends. The result goes into the workspace
  // under mobile/replays/<id>/, and the chat message tells the assistant,
  // which reviews it and answers with the result card.

  function freshReplay() {
    return {
      start: "restart", secretNames: [], extraSecretNames: [], secrets: {}, yaml: {}, logs: {},
      running: null, polling: false, result: null, saved: [],
      // The replay dialog: the file it was opened on, the older segments
      // ticked in it, and its phase (setup, running, done).
      dialog: null,
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

  // The files a replay dialog runs: the file it was opened on, or for an
  // older segment the chain ticked in it, in the order of the latest cut.
  function dialogItems() {
    const d = view.replay.dialog;
    if (!d) return [];
    if (d.item.kind !== "segment") return [d.item];
    return olderSegments().filter((seg) => d.chain.includes(seg.name));
  }

  // The older segments of the held device's platform, in the order of the
  // latest cut, then by name.
  function olderSegments() {
    const flow = view.flow || { segments: [], split: null };
    const platform = recordingPlatform();
    const order = flow.split ? flow.split.parts : [];
    const rank = (name) => {
      const index = order.indexOf(name);
      return index >= 0 ? index : order.length;
    };
    return (flow.segments || []).slice().sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).map((name) => ({ name, kind: "segment", path: `${SEGMENTS_DIR}/${platform}/${name}.yaml`, platform }));
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
    // A recording has no YAML here; the bridge names what it types.
    const found = await Promise.all(dialogItems().filter((seg) => seg.kind !== "recording").map((seg) => segmentYaml(seg).then(segmentSecrets, () => [])));
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
    let line = `Replay finished: ${passed} of ${segments.length} ${replayNoun(segments)} passed`;
    const failure = report && report.failure;
    if (report && report.status === "failed" && failure && failure.segment) line += `; ${failure.segment} failed at step ${failure.step || "?"}`;
    return `${line}.\nReport: ${path}`;
  }

  // Whether a report's item is this row: the same name, and the same kind
  // unless the report is from a bridge that did not say.
  function sameReplayRow(item, row) {
    return Boolean(item) && item.name === row.name && (!item.kind || item.kind === row.kind);
  }

  // ---- the replay dialog -------------------------------------------------------
  //
  // A replay is asked for on a file: a recording saved in this session, or a
  // script, a recording or an older segment in the Library. A dialog takes
  // where to start and the values the replay types, runs it with its phases
  // in view, and shows the outcome; the device card says what runs meanwhile.

  // The bridge's phases, as its progress text names them.
  const REPLAY_PHASES = [
    ["Take the device back", /taking the device back|^starting$/],
    ["Start the app", /restarting the app|clearing|resetting/],
    ["Replay", /^replaying|step \d+ of \d+/],
    ["Hand the device back", /handing the device back/],
  ];

  function replayPhaseIndex(progress) {
    const text = String(progress || "").trim();
    for (let i = REPLAY_PHASES.length - 1; i >= 0; i -= 1) {
      if (REPLAY_PHASES[i][1].test(text)) return i;
    }
    return 0;
  }

  // The scenario script a replay ran, when the compiler's report is still
  // next to it: what Review in the outcome hands to the assistant.
  function reviewableReplay(report) {
    const items = Array.isArray(report && report.segments) ? report.segments : [];
    const script = items.length === 1 && items[0].kind === "script" ? items[0] : null;
    if (!script || !view.flow || !view.flow.reportPaths) return null;
    const reportPath = view.flow.reportPaths[script.name];
    return reportPath ? { name: script.name, path: script.path || `${SCRIPTS_DIR}/${recordingPlatform()}/${script.name}.yaml`, reportPath } : null;
  }

  // The file the running (or last) replay ran, from what the bridge said.
  function replayedItem() {
    const r = view.replay;
    const running = r.running && r.running.status === "running" ? r.running : null;
    const report = r.result && r.result.result ? r.result.result.report : null;
    const seg = report && Array.isArray(report.segments) && report.segments[0] ? report.segments[0] : null;
    const name = (running && (running.segments || [])[0]) || (seg && seg.name) || "";
    if (!name) return null;
    const kind = seg && seg.kind ? seg.kind : "script";
    const platform = recordingPlatform();
    const path = (seg && seg.path) || (kind === "recording" ? `${RECORDINGS_DIR}/${name}.wdlog.json` : `${kind === "segment" ? SEGMENTS_DIR : SCRIPTS_DIR}/${platform}/${name}.yaml`);
    return { name, kind, path, platform };
  }

  // Opens the dialog on a file, to set a replay up; on the running one, or
  // the last result, when asked to show those.
  async function openReplayDialog(item, phase = "setup") {
    const rec = view.recording;
    const r = view.replay;
    if (!rec || rec.status !== "active") {
      askForDevice("a replay runs on it");
      return;
    }
    if (r.running && r.running.status === "running") phase = "running";
    if (phase !== "setup") {
      r.dialog = { item, chain: [], phase, busy: false, notice: "" };
      renderReplay();
      renderIcons();
      return;
    }
    r.dialog = { item, chain: item.kind === "segment" ? [item.name] : [], phase: "setup", busy: false, notice: "" };
    r.extraSecretNames = [];
    await loadReplaySecrets();
    renderReplay();
    renderIcons();
    focusFirstSecret();
  }

  function closeReplayDialog() {
    view.replay.dialog = null;
    renderReplay();
    renderDevice();
    renderIcons();
  }

  function focusFirstSecret() {
    const inputs = [...(panelRoot()?.querySelectorAll("[data-recording-dialog] [data-replay-secret]") || [])];
    const input = inputs.find((el) => !el.value);
    if (input) input.focus();
  }

  function replayKindLine(kind) {
    if (kind === "recording") return "A recording, as it is: the bridge compiles it on the way and replays it on its own, from the start you choose.";
    if (kind === "segment") return "Older segments run in the order ticked, from the start you choose.";
    return "A scenario script replays on its own, from the start you choose.";
  }

  function replayDialogHtml() {
    const r = view.replay;
    const rec = view.recording;
    const d = r.dialog;
    if (!d || !rec) return "";
    const running = r.running && r.running.status === "running" ? r.running : null;
    let title = "";
    let body = "";
    let actions = "";
    if (d.phase === "setup" && !running) {
      title = `Replay ${d.item.name}`;
      const starts = [["restart", "Restart the app"]];
      if (rec.platform !== "ios") starts.push(["reset", "Clear the app's data first"]);
      starts.push(["current", "The screen the device shows"]);
      const chain = d.item.kind === "segment"
        ? `<div class="efp-replay-list">${olderSegments().map((seg) => `<label class="efp-replay-row"><input type="checkbox" data-replay-chain="${esc(seg.name)}"${d.chain.includes(seg.name) ? " checked" : ""} /><span>${esc(seg.name)}</span></label>`).join("")}</div>`
        : "";
      const secrets = r.secretNames.length
        ? `${r.secretNames.map((name) => `<label class="portal-form-label"><span class="portal-form-label">${esc(name)}</span><input type="password" class="portal-form-input" data-replay-secret="${esc(name)}" placeholder="The value the replay types" autocomplete="off" /></label>`).join("")}
          <p class="portal-inline-note">Kept in this page only and sent to the bridge on this computer, never to the assistant.</p>`
        : "";
      body = `<p class="portal-dialog-msg">${esc(replayKindLine(d.item.kind))} The device is borrowed from the Inspector and handed back when it ends.</p>
        ${chain}
        <label class="portal-form-label"><span class="portal-form-label">Start from</span><select class="portal-form-select" data-replay-start>${starts.map(([value, label]) => `<option value="${value}"${r.start === value ? " selected" : ""}>${label}</option>`).join("")}</select></label>
        ${secrets}
        ${d.notice ? `<div class="portal-inline-state is-visible is-error">${esc(d.notice)}</div>` : ""}`;
      actions = `<button type="button" class="portal-btn" data-replay-action="close">Cancel</button>
        <button type="button" class="portal-btn is-primary" data-replay-action="start"${d.busy ? " disabled" : ""}><i data-lucide="play" class="w-4 h-4"></i>${d.busy ? "Starting…" : "Replay"}</button>`;
    } else if (running) {
      title = `Replaying ${(running.segments || [])[0] || d.item.name}`;
      const current = replayPhaseIndex(running.progress);
      const since = Date.parse(running.started_at || "") || Date.now();
      body = `<ol class="efp-flow">${REPLAY_PHASES.map(([label], i) => `<li class="efp-flow-step is-${i < current ? "done" : (i === current ? "current" : "todo")}"><span class="efp-flow-label">${esc(label)}</span></li>`).join("")}</ol>
        <div class="efp-card-meta"><span data-replay-progress>${esc(progressText(running))}</span> · <span data-recording-countdown data-since="${esc(String(since))}">${esc(elapsedText(since))}</span></div>
        <p class="portal-inline-note">Watch it live on BrowserStack. The Inspector shows nothing until the device is handed back; the result lands in the Library.</p>`;
      actions = `${rec.dashboard_url ? `<a class="portal-btn is-secondary" href="${esc(rec.dashboard_url)}" target="_blank" rel="noopener noreferrer"><i data-lucide="smartphone" class="w-4 h-4"></i>Watch on BrowserStack</a>` : ""}
        <button type="button" class="portal-btn is-secondary" data-replay-action="stop"><i data-lucide="square" class="w-4 h-4"></i>Stop</button>
        <button type="button" class="portal-btn" data-replay-action="close">Hide</button>`;
    } else {
      const res = r.result || {};
      const replay = res.replay || {};
      const report = (res.result && res.result.report) || {};
      const status = replay.status || report.status || "";
      const tone = status === "passed" ? "success" : (status === "cancelled" ? "warning" : "error");
      title = { passed: "Replay passed", failed: "Replay failed", cancelled: "Replay stopped", error: "Replay could not run" }[status] || "Replay";
      const failure = report.failure || {};
      const where = status === "failed" && failure.segment ? ` at ${failure.segment}, step ${failure.step || "?"}` : "";
      const session = report.session_url ? `<a class="portal-link-inline" href="${esc(report.session_url)}" target="_blank" rel="noopener noreferrer">BrowserStack session</a>` : "";
      const recordingReplay = Array.isArray(report.segments) && report.segments.length > 0 && report.segments.every((seg) => seg.kind === "recording");
      const script = status === "passed" ? reviewableReplay(report) : null;
      let saved = "";
      if (res.path && status === "failed" && recordingReplay) saved = "The recording did not replay as it is; the result is in the Library. Record it again, or compile it as its scenario and ask the assistant.";
      else if (res.path && status === "failed") saved = "The result and screenshots went to the chat; the assistant looks into the failure.";
      else if (res.path && recordingReplay) saved = "The recording replayed as it is; the result is in the Library. Compile it as its scenario when it is what you want.";
      else if (res.path) saved = "The result and screenshots are in the Library; the script counts as replayed as it is now.";
      const word = { passed: "Passed", failed: "Failed", cancelled: "Stopped", error: "Not run" }[status] || status || "Over";
      body = `<div><span class="portal-status-badge is-${tone}">${esc(word)}</span>${where ? `<span class="efp-card-meta">${esc(where)}</span>` : ""} ${session}</div>
        ${replay.error ? `<div class="portal-inline-state is-visible is-error">${esc(errorText(replay.error))}</div>` : ""}
        ${saved ? `<p class="portal-dialog-msg">${esc(saved)}</p>` : ""}
        ${res.error ? `<div class="portal-inline-state is-visible is-error">${esc(res.error)}</div>` : ""}`;
      actions = `${res.error ? `<button type="button" class="portal-btn is-secondary" data-replay-action="save-again">Save again</button>` : ""}
        ${script ? `<button type="button" class="portal-btn is-secondary" data-replay-action="review" title="Ask the assistant to label the steps with the plan's and add the expected-result checks">Review with the assistant</button>` : ""}
        ${status !== "passed" ? `<button type="button" class="portal-btn is-secondary" data-replay-action="again"><i data-lucide="rotate-ccw" class="w-4 h-4"></i>Replay again</button>` : ""}
        <button type="button" class="portal-btn is-primary" data-replay-action="close">Close</button>`;
    }
    return `<div class="modal portal-dialog portal-dialog--in" data-recording-dialog role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="modal-card panel portal-dialog-card">
          <div class="portal-modal-titlebar"><h3>${esc(title)}</h3></div>
          ${body}
          <div class="portal-modal-actions">${actions}</div>
        </div>
      </div>`;
  }

  // The bridge's progress, without the file's name it starts with on a step.
  function progressText(running) {
    const name = (running.segments || [])[0] || "";
    const text = running.progress || "starting";
    return name && text.startsWith(`${name}: `) ? text.slice(name.length + 2) : text;
  }

  // What the device card says about replays: the one running, with a way
  // back into the dialog, or the last result.
  function replayLineHtml() {
    const r = view.replay;
    const running = r.running && r.running.status === "running" ? r.running : null;
    if (running) {
      return `<div class="efp-card-meta efp-device-replay"><i data-lucide="play" class="w-3 h-3"></i><span>Replaying ${esc((running.segments || [])[0] || "")}: <span data-replay-progress>${esc(progressText(running))}</span></span><button type="button" class="composer-pill-btn" data-replay-action="show">Show</button></div>`;
    }
    const res = r.result;
    if (res && res.replay && !r.dialog) {
      const status = res.replay.status || "";
      const icon = { passed: "check", failed: "x", cancelled: "square" }[status] || "alert-circle";
      return `<div class="efp-card-meta efp-device-replay"><i data-lucide="${icon}" class="w-3 h-3"></i><span>Last replay: ${esc({ passed: "passed", failed: "failed", cancelled: "stopped" }[status] || "could not run")}</span><button type="button" class="composer-pill-btn" data-replay-action="show">Show</button></div>`;
    }
    return "";
  }

  function renderReplay() {
    const host = panelRoot()?.querySelector("[data-recording-dialog-host]");
    if (!host) return;
    if (view.recording && view.recording.status === "active") syncReplay();
    const r = view.replay;
    if (!r.dialog || !view.recording || view.recording.status !== "active") {
      host.innerHTML = "";
      return;
    }
    const active = document.activeElement;
    const focused = active && active.matches && active.matches("[data-replay-secret]") ? active.dataset.replaySecret : "";
    host.innerHTML = replayDialogHtml();
    host.querySelectorAll("[data-replay-secret]").forEach((input) => {
      input.value = r.secrets[input.dataset.replaySecret] || "";
      if (input.dataset.replaySecret === focused) input.focus();
    });
    if (typeof window.initPasswordToggles === "function") window.initPasswordToggles(host);
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
      const before = view.replay.running;
      const same = before && before.id === replay.id;
      const phaseChanged = !same || replayPhaseIndex(before.progress) !== replayPhaseIndex(replay.progress);
      view.replay.running = replay;
      if (same && !phaseChanged) {
        panelRoot()?.querySelectorAll("[data-replay-progress]").forEach((el) => {
          el.textContent = progressText(replay);
        });
      } else {
        renderDevice();
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
    if (r.dialog) {
      r.dialog.phase = "done";
      r.dialog.busy = false;
    }
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
      // A replay that passed shows in the Library and the plan's status; the
      // assistant is asked only to look into a failure.
      const doc = res.result.report || {};
      const recordingReplay = Array.isArray(doc.segments) && doc.segments.length > 0 && doc.segments.every((seg) => seg.kind === "recording");
      if (doc.status === "failed" && !recordingReplay) sendChat(replayMessage(doc, path));
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
    const d = r.dialog;
    if (!rec || !d || (r.running && r.running.status === "running")) return;
    const items = dialogItems();
    if (!items.length) {
      d.notice = "Tick the segments to replay.";
      renderReplay();
      return;
    }
    d.busy = true;
    d.notice = "";
    renderReplay();
    try {
      await loadReplaySecrets();
      const missing = r.secretNames.filter((name) => !r.secrets[name]);
      if (missing.length) {
        d.busy = false;
        d.notice = `Fill in ${missing.join(", ")} first: the replay types ${missing.length === 1 ? "it" : "them"}.`;
        renderReplay();
        focusFirstSecret();
        return;
      }
      const secrets = {};
      r.secretNames.forEach((name) => {
        secrets[name] = r.secrets[name];
      });
      let data;
      if (items.length === 1 && items[0].kind === "recording") {
        // A recording replays as it is: the bridge compiles it on the way.
        const log = r.logs[items[0].path] || JSON.parse(await readWorkspaceText(items[0].path));
        r.logs[items[0].path] = log;
        data = await call("recording.replay", { id: rec.id, name: items[0].name, path: items[0].path, log, secrets, start: r.start }, { timeoutMs: 120000 });
      } else {
        const segments = [];
        for (const seg of items) segments.push({ name: seg.name, path: seg.path, yaml: await segmentYaml(seg) });
        data = await call("segment.replay", { id: rec.id, segments, secrets, start: r.start }, { timeoutMs: 30000 });
      }
      r.result = null;
      r.running = data.replay || null;
      d.busy = false;
      d.phase = "running";
      // The card disables saving and holding at once, not at the next poll.
      if (view.recording && r.running) view.recording = Object.assign({}, view.recording, { replay: r.running });
      setStatus(`Replaying ${items.map((seg) => seg.name).join(", ")}.`, "");
      renderSession();
      scheduleReplayPoll();
    } catch (error) {
      d.busy = false;
      if (error.code === "missing_secrets" && error.data && Array.isArray(error.data.missing)) {
        r.extraSecretNames = error.data.missing.map(String);
        await loadReplaySecrets();
        d.notice = `Fill in ${error.data.missing.join(", ")} first: the replay types ${error.data.missing.length === 1 ? "it" : "them"}.`;
        renderReplay();
        focusFirstSecret();
      } else {
        d.notice = `Could not start the replay: ${errorText(error)}`;
        renderReplay();
      }
    }
    renderIcons();
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
  // scenario scripts (with the grade and the review count the compiler writes
  // into the file, and whether a replay passed; a copy for another scenario
  // asked of the assistant), recordings (and the scripts compiled from them),
  // older segments, and replays (their reports as cards). A deletion is done
  // here and told to the assistant, which keeps the scenario plan in step.

  const LIBRARY_PLATFORMS = ["android", "ios"];
  const LIBRARY_MAX_FILES = 40;
  const LIBRARY_MAX_REPLAYS = 20;

  function freshLibrary() {
    return { loading: false, loaded: false, error: "", scripts: {}, segments: {}, recordings: [], replays: [], plans: [], texts: {}, open: {} };
  }

  // The little a list needs from a script or segment file, without a YAML
  // parser: its name and scenario, how many steps, and what the compiler (or
  // a copy) wrote into source.
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
      issue: first(/^issue:\s*(.+)$/m),
      scenario: first(/^scenario:\s*(.+)$/m),
      description: first(/^description:\s*(.+)$/m),
      copiedFrom: first(/^\s+copied_from:\s*(.+)$/m),
      sourceFile: first(/^\s+file:\s*(.+)$/m),
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
      const [recordingItems, scenarioItems, ...folderLists] = await Promise.all([
        listWorkspace(RECORDINGS_DIR),
        listWorkspace(SCENARIOS_DIR),
        ...LIBRARY_PLATFORMS.map((platform) => listWorkspace(`${SCRIPTS_DIR}/${platform}`)),
        ...LIBRARY_PLATFORMS.map((platform) => listWorkspace(`${SEGMENTS_DIR}/${platform}`)),
      ]);
      const scriptLists = folderLists.slice(0, LIBRARY_PLATFORMS.length);
      const segmentLists = folderLists.slice(LIBRARY_PLATFORMS.length);
      lib.plans = await readPlans(scenarioItems);
      lib.replays = await loadReplayReports();

      // Scripts and older segments per platform, each with its status from
      // its file and its replays.
      lib.scripts = {};
      lib.segments = {};
      for (let i = 0; i < LIBRARY_PLATFORMS.length; i += 1) {
        const platform = LIBRARY_PLATFORMS[i];
        lib.scripts[platform] = await scriptRows(scriptLists[i], SCRIPTS_DIR, platform, "script", lib.replays);
        lib.segments[platform] = await scriptRows(segmentLists[i], SEGMENTS_DIR, platform, "segment", lib.replays);
      }

      // Recordings with the scripts compiled from them, and the cut of one
      // that holds several scenarios.
      const recordingFiles = files(recordingItems);
      const splits = {};
      for (const item of recordingFiles.filter((it) => /\.split\.json$/.test(it.name || "")).slice(0, LIBRARY_MAX_FILES)) {
        try {
          const doc = JSON.parse(await cachedText(`${RECORDINGS_DIR}/${item.name}`, item.modified_at));
          const platform = String(doc.platform || "");
          const parts = (Array.isArray(doc.parts) ? doc.parts : []).map((part) => String(part.segment || "")).filter(Boolean);
          const compiled = parts.filter((name) => (lib.scripts[platform] || []).concat(lib.segments[platform] || []).some((seg) => seg.name === name));
          splits[String(item.name).replace(/\.split\.json$/, "")] = { path: `${RECORDINGS_DIR}/${item.name}`, platform, parts, compiled };
        } catch (_error) {
          /* a split still being written */
        }
      }
      lib.recordings = recordingFiles.filter((it) => /\.wdlog\.json$/.test(it.name || "")).sort((a, b) => String(b.modified_at || "").localeCompare(String(a.modified_at || ""))).slice(0, LIBRARY_MAX_FILES).map((item) => {
        const name = String(item.name).replace(/\.wdlog\.json$/, "");
        // The scripts (or older segments) whose source is this recording.
        const compiledInto = [...new Set(LIBRARY_PLATFORMS.flatMap((platform) => (lib.scripts[platform] || []).concat(lib.segments[platform] || []))
          .filter((seg) => seg.sourceFile === String(item.name)).map((seg) => seg.name))];
        const row = { name, kind: "recording" };
        const latest = lib.replays.find((rp) => rp.segments.some((seg) => sameReplayRow(seg, row) && seg.status !== "not_run")) || null;
        const latestSeg = latest ? latest.segments.find((seg) => sameReplayRow(seg, row)) : null;
        return {
          name, path: `${RECORDINGS_DIR}/${item.name}`, savedAt: String(item.modified_at || ""), size: Number(item.size) || 0, split: splits[name] || null, compiledInto,
          lastReplay: latestSeg ? { status: latestSeg.status, at: latest.finishedAt, step: latest.failure && latest.failure.segment === name ? latest.failure.step : 0 } : null,
        };
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

  // The scenarios a recording can be compiled as: every plan's scripts, the
  // recording's own name preselected when it is one of them. The cut of a
  // recording that holds several scenarios is the assistant's.
  function compileChoicesHtml(item) {
    const lib = view.library;
    const plans = lib.plans.map(planView).filter((plan) => plan.scripts.length);
    const guess = scriptScenario(item.name, plans.find((plan) => plan.scripts.some((s) => s.name === item.name)) || plans[0] || null);
    const existing = (name) => LIBRARY_PLATFORMS.filter((p) => (lib.scripts[p] || []).some((row) => row.name === name));
    let chosen = guess ? `${guess.issue}|${guess.scenario}` : "";
    if (!chosen) {
      const next = plans.flatMap((plan) => plan.scripts.filter((s) => !s.copyOf && !existing(s.name).length).map((s) => `${plan.issue}|${s.scenario}`))[0];
      chosen = next || "";
    }
    const options = plans.map((plan) => `<optgroup label="${esc(plan.issue || plan.key)}">${plan.scripts.map((s) => {
      const title = plan.scenarios[s.scenario] ? plan.scenarios[s.scenario].title : "";
      const exists = existing(s.name);
      const label = `${s.name}${title ? ` · ${title}` : ""}${exists.length ? ` (script on ${exists.join(", ")})` : ""}`;
      const selected = chosen === `${plan.issue}|${s.scenario}`;
      return `<option value="${esc(`${plan.key}|${s.name}`)}"${selected ? " selected" : ""}>${esc(label)}</option>`;
    }).join("")}</optgroup>`).join("");
    return `<div class="efp-lib-card" data-library-compile="${esc(item.path)}">
        <label class="portal-form-label"><span class="portal-form-label">Compile as the scenario</span>
          <select class="portal-form-select" data-library-scenario>${options || `<option value="">No scenario plan in the workspace yet</option>`}</select>
        </label>
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-primary" data-recording-action="library-compile-as" data-name="${esc(item.name)}" data-path="${esc(item.path)}"${options ? "" : " disabled"}>Compile</button>
          <button type="button" class="composer-pill-btn" data-recording-action="library-cut" data-name="${esc(item.name)}" data-path="${esc(item.path)}" title="Several scenarios in one recording: the assistant proposes where to cut it">Several scenarios: ask the assistant to cut it</button>
        </div>
      </div>`;
  }

  // Where a recording stands in the Library: a cut with parts still to
  // compile first, then what it was compiled into.
  function recordingState(item) {
    const split = item.split && item.split.parts.length ? item.split : null;
    const missing = split ? split.parts.length - split.compiled.length : 0;
    if (missing > 0) return `cut proposed, ${missing} of ${split.parts.length} parts not compiled`;
    if (item.compiledInto && item.compiledInto.length) return `compiled into ${item.compiledInto.join(", ")}`;
    if (split) return `cut into ${split.parts.length} ${split.parts.length === 1 ? "part" : "parts"}`;
    return "not compiled yet";
  }

  function segmentLine(seg) {
    const parts = [];
    if (seg.description) parts.push(seg.description);
    parts.push(`${seg.steps} ${seg.steps === 1 ? "step" : "steps"}`);
    if (seg.grade) parts.push(`grade ${seg.grade}`);
    if (seg.needsReview) parts.push(`${seg.needsReview} to review`);
    if (seg.copiedFrom) parts.push(`copy of ${seg.copiedFrom}`);
    else if (seg.compiledAt) parts.push(`compiled ${dateText(seg.compiledAt)}`);
    return parts.join(" · ");
  }

  // What a row says about its replays: the latest replay of the file as it
  // is now, or that the file changed since its last replay.
  function segmentMarkHtml(seg) {
    const last = seg.lastReplay;
    if (!last) return `<span class="efp-replay-mark">not replayed</span>`;
    if (last.current && last.status === "passed") return `<span class="efp-replay-mark is-success"><i data-lucide="check" class="w-3 h-3"></i>passed ${esc(dateText(last.at))}</span>`;
    if (last.current && last.status === "failed") return `<span class="efp-replay-mark is-error"><i data-lucide="x" class="w-3 h-3"></i>failed${last.step ? ` at step ${esc(String(last.step))}` : ""}</span>`;
    return `<span class="efp-replay-mark is-warning"><i data-lucide="pencil" class="w-3 h-3"></i>edited since it ${last.status === "passed" ? "passed" : "failed"} ${esc(dateText(last.at))}</span>`;
  }

  function libraryHtml() {
    const lib = view.library;
    const rec = view.recording;
    const held = rec && rec.status === "active";
    const platformName = (p) => (p === "ios" ? "iOS" : "Android");
    const groups = (byPlatform, kind) => LIBRARY_PLATFORMS.map((platform) => {
      const list = byPlatform[platform] || [];
      if (!list.length) return "";
      const rows = list.map((seg) => {
        const open = Boolean(lib.open[`file:${seg.path}`]);
        const copying = kind === "script" && Boolean(lib.open[`copy:${seg.path}`]);
        const copyButton = kind === "script"
          ? `<button type="button" class="composer-pill-btn" data-recording-action="library-copy" data-path="${esc(seg.path)}" aria-expanded="${copying ? "true" : "false"}" title="Ask the assistant for a copy of this script for a scenario with other values">Copy</button>`
          : "";
        const reviewButton = kind === "script" && seg.reportPath
          ? `<button type="button" class="composer-pill-btn" data-recording-action="library-review" data-name="${esc(seg.name)}" data-path="${esc(seg.path)}" data-report="${esc(seg.reportPath)}" title="Ask the assistant to review this script: label its steps with the plan's and add the expected-result checks">Review</button>`
          : "";
        const copyForm = copying
          ? `<div class="efp-lib-card" data-library-copy="${esc(seg.path)}">
              <label class="portal-form-label"><span class="portal-form-label">What the copy changes</span>
                <input class="portal-form-input" data-library-copy-input placeholder="EUR instead of USD, 250 instead of 100" autocomplete="off" />
              </label>
              <div class="efp-card-actions"><button type="button" class="portal-btn is-primary" data-recording-action="library-copy-send" data-name="${esc(seg.name)}" data-path="${esc(seg.path)}">Ask the assistant</button></div>
            </div>`
          : "";
        return `
        <div class="efp-lib-row">
          <div class="efp-lib-main"><strong>${esc(seg.name)}</strong> ${segmentMarkHtml(seg)}<span class="efp-card-meta">${esc(segmentLine(seg))}</span></div>
          <div class="efp-lib-actions">
            <button type="button" class="composer-pill-btn" data-recording-action="library-replay" data-name="${esc(seg.name)}" data-platform="${esc(platform)}" data-kind="${kind}" title="${held ? "Replay on the held device" : "Tick it for the next replay"}">Replay</button>
            <button type="button" class="composer-pill-btn" data-recording-action="library-view" data-path="${esc(seg.path)}" aria-expanded="${open ? "true" : "false"}">${open ? "Hide" : "View"}</button>
            ${reviewButton}
            ${copyButton}
            <button type="button" class="composer-pill-btn is-danger" data-recording-action="library-delete-${kind}" data-name="${esc(seg.name)}" data-path="${esc(seg.path)}">Delete</button>
          </div>
        </div>
        ${copyForm}
        ${open ? `<div class="efp-lib-card" data-library-file="${esc(seg.path)}"><pre class="efp-lib-pre">Loading…</pre></div>` : ""}`;
      }).join("");
      return `<div class="efp-lib-group"><span class="efp-card-meta">${esc(platformName(platform))}</span>${rows}</div>`;
    }).join("");
    const scriptGroups = groups(lib.scripts, "script");
    const segmentGroups = groups(lib.segments, "segment");
    const recordingRows = lib.recordings.map((item) => {
      const compiled = recordingState(item);
      const picking = Boolean(lib.open[`compile:${item.path}`]);
      const mark = item.lastReplay
        ? (item.lastReplay.status === "passed"
          ? `<span class="efp-replay-mark is-success"><i data-lucide="check" class="w-3 h-3"></i>replayed ${esc(dateText(item.lastReplay.at))}</span>`
          : `<span class="efp-replay-mark is-error"><i data-lucide="x" class="w-3 h-3"></i>failed${item.lastReplay.step ? ` at step ${esc(String(item.lastReplay.step))}` : ""}</span>`)
        : "";
      return `
        <div class="efp-lib-row">
          <div class="efp-lib-main"><strong>${esc(item.name)}</strong> ${mark}<span class="efp-card-meta">saved ${esc(dateText(item.savedAt))} · ${esc(sizeText(item.size))} · ${esc(compiled)}</span></div>
          <div class="efp-lib-actions">
            <button type="button" class="composer-pill-btn" data-recording-action="library-replay-recording" data-name="${esc(item.name)}" data-path="${esc(item.path)}" title="${held ? "Replay it as it is on the held device" : "Tick it for the next replay"}">Replay</button>
            <button type="button" class="composer-pill-btn" data-recording-action="library-split" data-name="${esc(item.name)}" data-path="${esc(item.path)}" aria-expanded="${picking ? "true" : "false"}" title="Compile it as one of the plan's scenarios on this computer">Compile</button>
            <button type="button" class="composer-pill-btn is-danger" data-recording-action="library-delete-recording" data-name="${esc(item.name)}" data-path="${esc(item.path)}">Delete</button>
          </div>
        </div>
        ${picking ? compileChoicesHtml(item) : ""}`;
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
      <p class="portal-inline-note">What this assistant keeps for mobile testing. A scenario's status follows these files: its script exists once the file is here, and is replayed once the latest replay of the file as it is passed.</p>
      ${lib.error ? `<div class="portal-inline-state is-visible is-error">${esc(lib.error)}</div>` : ""}
      ${lib.loading && !lib.loaded ? `<p class="portal-inline-note">Reading the workspace…</p>` : ""}
      <h6>Scenario scripts</h6>
      ${scriptGroups || (lib.loaded ? empty("No scenario scripts yet. Save the recording of a scenario under its script's name; it is compiled here.") : "")}
      <h6>Recordings</h6>
      ${recordingRows || (lib.loaded ? empty("No recordings saved yet.") : "")}
      ${segmentGroups ? `<h6>Older segments</h6>${segmentGroups}` : ""}
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
      const seg = Object.values(view.library.scripts).flat().concat(Object.values(view.library.segments).flat()).find((item) => item.path === path);
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
      const kind = button.dataset.kind === "script" ? "script" : "segment";
      const rec = view.recording;
      if (!(rec && rec.status === "active")) {
        askForDevice("a replay runs on it");
        return;
      }
      if (recordingPlatform() !== platform) {
        say(`${name} is a ${platform === "ios" ? "iOS" : "Android"} ${kind}; the held device runs ${platformLabel(rec)}.`, "error");
        return;
      }
      // A script's or segment's row names the file by its kind.
      await openReplayDialog({ name, kind, path: path || `${kind === "script" ? SCRIPTS_DIR : SEGMENTS_DIR}/${platform}/${name}.yaml`, platform });
      return;
    }
    if (action === "library-split") {
      if (lib.open[`compile:${path}`]) delete lib.open[`compile:${path}`];
      else lib.open[`compile:${path}`] = true;
      renderLibrary();
      renderIcons();
      return;
    }
    if (action === "library-compile-as") {
      const select = button.closest("[data-library-compile]")?.querySelector("[data-library-scenario]");
      const [planKey, scriptName] = String(select ? select.value : "").split("|");
      const plan = lib.plans.map(planView).find((p) => p.key === planKey) || null;
      const entry = plan ? plan.scripts.find((s) => s.name === scriptName) : null;
      if (!plan || !entry) {
        say("Pick the scenario this recording is.", "error");
        return;
      }
      const what = { issue: plan.issue, scenario: entry.scenario, title: (plan.scenarios[entry.scenario] || {}).title || "" };
      let log;
      try {
        log = JSON.parse(await readWorkspaceText(path));
      } catch (error) {
        say(`Could not read ${name}: ${errorText(error)}`, "error");
        return;
      }
      const platform = String(log.platform || "").toLowerCase() === "ios" ? "ios" : "android";
      const existing = (lib.scripts[platform] || []).find((row) => row.name === entry.name);
      if (existing && !(await confirmAction(`Compile ${name} as ${entry.name} again? The script in the workspace, with the assistant's review, is replaced.`, "Compile", "Compile again?"))) return;
      button.disabled = true;
      try {
        const compiled = await compileRecording(name, log, what);
        delete lib.open[`compile:${path}`];
        say(`Compiled ${name} as ${compiled.name} (grade ${compiled.report.grade || "?"}). Replay it, then press Review when it passes.`, "success");
      } catch (error) {
        // This computer could not compile it (an older bridge): the assistant does.
        sendChat(`Script ${entry.name} recorded: ${path}`);
        say(`Could not compile ${name} on this computer (${errorText(error)}); the assistant compiles it as ${entry.name}.`, "warning");
      }
      lib.loaded = false;
      await Promise.all([loadLibrary(), loadFlow()]);
      return;
    }
    if (action === "library-cut") {
      sendChat(savedMessage(name, path, []));
      delete lib.open[`compile:${path}`];
      renderLibrary();
      renderIcons();
      say(`Asked the assistant to cut ${name} into its scenarios.`, "success");
      return;
    }
    if (action === "library-replay-recording") {
      const rec = view.recording;
      const r = view.replay;
      if (!(rec && rec.status === "active")) {
        askForDevice("a recording replays on it");
        return;
      }
      let log;
      try {
        log = JSON.parse(await readWorkspaceText(path));
      } catch (error) {
        say(`Could not read ${name}: ${errorText(error)}`, "error");
        return;
      }
      const platform = String(log.platform || "").toLowerCase() === "ios" ? "ios" : "android";
      if (platform !== recordingPlatform()) {
        say(`${name} was recorded on ${platform === "ios" ? "iOS" : "Android"}; the held device runs ${platformLabel(rec)}.`, "error");
        return;
      }
      r.logs[path] = log;
      await openReplayDialog({ name, kind: "recording", path, platform });
      return;
    }
    if (action === "library-review") {
      requestReview(name, path, button.dataset.report || "");
      return;
    }
    if (action === "library-copy") {
      if (lib.open[`copy:${path}`]) delete lib.open[`copy:${path}`];
      else lib.open[`copy:${path}`] = true;
      renderLibrary();
      renderIcons();
      panelRoot()?.querySelector(`[data-library-copy="${CSS.escape(path)}"] [data-library-copy-input]`)?.focus();
      return;
    }
    if (action === "library-copy-send") {
      const input = button.closest("[data-library-copy]")?.querySelector("[data-library-copy-input]");
      const changes = input ? input.value.trim() : "";
      if (!changes) {
        say("Say what the copy changes: the values its scenario uses instead.", "error");
        input?.focus();
        return;
      }
      sendChat(copyMessage(name, changes, path));
      delete lib.open[`copy:${path}`];
      renderLibrary();
      renderIcons();
      say(`Asked the assistant for a copy of ${name}.`, "success");
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
    if (action === "library-delete-script" || action === "library-delete-segment" || action === "library-delete-recording" || action === "library-delete-replay") {
      const kind = { "library-delete-script": "Script", "library-delete-segment": "Segment", "library-delete-recording": "Recording" }[action] || "Replay";
      const what = kind === "Replay" ? `the replay of ${dateText((lib.replays.find((rp) => rp.id === button.dataset.id) || {}).finishedAt) || button.dataset.id}` : `${kind.toLowerCase()} ${name}`;
      const hint = { Script: " Its scenario is to record or copy again.", Segment: " Scenarios that use it will need it recorded again.", Recording: " Its cut goes with it; scripts already compiled from it stay." }[kind] || "";
      if (!(await confirmAction(`Delete ${what} from the assistant's workspace?${hint}`, "Delete", "Delete?"))) return;
      const paths = [path];
      if (kind === "Recording") {
        const item = lib.recordings.find((it) => it.name === name);
        if (item && item.split) paths.push(item.split.path);
      }
      if (kind === "Script") {
        const row = LIBRARY_PLATFORMS.flatMap((platform) => lib.scripts[platform] || []).find((it) => it.path === path);
        if (row && row.reportPath) paths.push(row.reportPath);
      }
      button.disabled = true;
      try {
        await deleteWorkspace(paths);
      } catch (error) {
        button.disabled = false;
        say(`Could not delete ${what}: ${errorText(error)}`, "error");
        return;
      }
      say(`Deleted ${what}.`, "success");
      lib.loaded = false;
      view.replay.yaml = {};
      await Promise.all([loadLibrary(), loadFlow()]);
    }
  }

  // ---- saving a recording -----------------------------------------------------------

  // Writes a saved log into the assistant's workspace, then compiles it into
  // its scenario script here when its name says which scenario it is. The
  // bridge has already moved on to the next log, so a failed write keeps
  // this one here to save again or download.
  async function saveLog(name, log, summary) {
    const fileName = `${name}.wdlog.json`;
    const blob = new Blob([JSON.stringify(log, null, 2)], { type: "application/json" });
    let path;
    try {
      path = await writeToWorkspace(fileName, blob);
    } catch (error) {
      view.unsaved = { name, log, summary, error: errorText(error) };
      return "";
    }
    view.unsaved = null;
    const s = summary || {};
    const done = { name, detail: `${Number(s.actions) || 0} actions${s.secrets ? `, ${s.secrets} secret` : ""}`, compiled: false };
    view.done.push(done);
    remember();
    const compiled = await compileSaved(name, log, path);
    if (compiled) {
      done.compiled = true;
      done.detail += `, compiled (grade ${compiled.report.grade || "?"})`;
      done.scriptPath = compiled.path;
      done.reportPath = compiled.reportPath;
      remember();
    }
    return path;
  }

  // Compiles a recording saved under a scenario's name into its script on
  // this computer. Any other recording, and one this computer could not
  // compile, goes to the assistant by message instead.
  async function compileSaved(name, log, path) {
    const listed = view.planned.indexOf(name) >= 0 || planScriptNames().indexOf(name) >= 0;
    const what = listed ? scriptScenario(name, view.flow && view.flow.plan) : null;
    // A recording saved under no scenario's name stays a recording: replay
    // it as it is, and compile it as its scenario from the Library.
    if (!what) return null;
    try {
      return await compileRecording(name, log, what);
    } catch (error) {
      sendChat(savedMessage(name, path));
      setStatus(`Could not compile ${name} on this computer (${errorText(error)}); the assistant compiles it in the chat.`, "warning");
      return null;
    }
  }

  // Runs the compiler on this computer through the bridge and writes the
  // script and the compiler's report into the workspace next to each other.
  // The review is asked for apart (requestReview), once the member wants it.
  async function compileRecording(name, log, what) {
    const platform = String((log && log.platform) || recordingPlatform()).toLowerCase() === "ios" ? "ios" : "android";
    const data = await call("script.compile", { log, file: `${name}.wdlog.json`, issue: what.issue, scenario: what.scenario, title: what.title || "", platform }, { timeoutMs: 120000 });
    const fileName = String(data.name || name);
    const folder = `${SCRIPTS_DIR}/${platform}`;
    const scriptPath = await writeToWorkspace(`${fileName}.yaml`, new Blob([String(data.script || "")], { type: "application/yaml" }), folder);
    const reportPath = await writeToWorkspace(`${fileName}.report.json`, new Blob([JSON.stringify(data.report || {}, null, 2)], { type: "application/json" }), folder);
    view.library.loaded = false;
    view.replay.yaml = {};
    return { name: fileName, path: scriptPath, reportPath, report: data.report || {} };
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
      const last = view.done[view.done.length - 1];
      const compiled = Boolean(path && last && last.name === data.segment && last.compiled);
      if (!path) setStatus(`Could not save ${data.segment} into the assistant's workspace.`, "error");
      else if (compiled) setStatus(`Saved and compiled ${data.segment}. Replay it, then press Review when it passes; or restart the app and record the next scenario.`, "success");
      else setStatus(`Saved ${data.segment}. Replay it as it is to check it, then compile it as its scenario from the Library. Anything you record from here goes into ${data.next_segment}.`, "success");
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
      view.done.push({ name, detail: file.name, code: true });
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
      toggleUpload(root, false);
    } catch (error) {
      setStatus(errorText(error), "error");
    } finally {
      view.busy = "";
      if (button) button.disabled = false;
      if (progress) progress.classList.add("hidden");
    }
  }

  // Shows or hides the start form's upload area in place, so what the
  // member typed elsewhere in the form stays.
  function toggleUpload(root, open) {
    const block = root.querySelector("[data-recording-upload]");
    if (!block) return;
    view.uploadOpen = typeof open === "boolean" ? open : block.classList.contains("hidden");
    block.classList.toggle("hidden", !view.uploadOpen);
    root.querySelectorAll('[data-recording-action="upload-toggle"]').forEach((button) => button.setAttribute("aria-expanded", view.uploadOpen ? "true" : "false"));
    if (view.uploadOpen) block.querySelector("[data-recording-build]")?.focus();
  }

  // Without a held device, points at the start form: a replay runs on the
  // device this panel holds, whichever tab is open.
  function askForDevice(what) {
    if (view.recording && view.recording.status === "starting") {
      setStatus(`The device is still starting; ${what} once it is ready.`, "warning");
      return;
    }
    setStatus(`Start a device first; ${what}.`, "warning");
    const root = panelRoot();
    root?.querySelector("[data-recording-device]")?.scrollIntoView({ block: "start", behavior: "smooth" });
    const start = root?.querySelector('[data-recording-action="start"]');
    if (start) start.focus({ preventScroll: true });
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
    } else if (!(await confirmAction(`Release ${rec.device || "the device"}? The BrowserStack session ends and the Inspector closes; recordings saved in the assistant's workspace stay.`, "Finish", "Finish recording?"))) {
      return;
    }
    if (button) button.disabled = true;
    view.releasing = { since: Date.now(), replaying };
    view.replay.dialog = null;
    renderSession();
    try {
      // The bridge lets a running replay finish its step and hand the
      // device back first.
      await call("session.finish", { id: rec.id }, { credentials: credentials(), timeoutMs: replaying ? 300000 : 120000 });
    } catch (error) {
      if (error.code !== "not_found") {
        view.releasing = null;
        renderSession();
        setStatus(`Could not release the device: ${errorText(error)}. Try Finish recording again.`, "error");
        if (button) button.disabled = false;
        return;
      }
    }
    view.releasing = null;
    closeInspectorTab();
    view.recording = null;
    view.unsaved = null;
    stopReplayPolling();
    view.replay = freshReplay();
    view.connectionOpen = false;
    remember();
    closeWorkspace({ rerender: false });
    view.done = [];
    await loadRecordings();
    renderAll();
    setStatus("Recording finished; the device is released.", "success");
    loadFlow();
  }

  async function finishOther(id, button) {
    if (!(await confirmAction("Release this device? Its BrowserStack session ends; recordings saved in the assistant's workspace stay.", "Release", "Release the device?"))) return;
    view.releasingOther = id;
    renderOthers();
    renderIcons();
    try {
      await call("session.finish", { id }, { credentials: credentials(), timeoutMs: 120000 });
      setStatus("Released the device.", "success");
    } catch (error) {
      if (error.code !== "not_found") setStatus(`Could not release the device: ${errorText(error)}`, "error");
    }
    view.releasingOther = "";
    await loadRecordings();
    renderOthers();
    renderIcons();
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
  const LOCAL_BRIDGE_CONNECTOR = "local_bridge";

  // A Portal connectors API call: the JSON body, or an error with the
  // detail the API gave.
  async function connectorJson(url, options) {
    const response = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" } }, options || {}));
    let body = null;
    try {
      body = await response.json();
    } catch (_error) {
      body = null;
    }
    if (!response.ok) {
      const detail = body && (body.detail || body.error);
      throw new Error(typeof detail === "string" && detail ? detail : `HTTP ${response.status}`);
    }
    return body && typeof body === "object" ? body : {};
  }

  // Saves the proxy typed on the BrowserStack connector page into the
  // member's Local bridge connector, keeping the rest of its settings; the
  // test on that page and the panel's calls use it from then on.
  async function saveProxy(container, button) {
    if (!container) return false;
    const say = (text, tone) => setInline(container.querySelector("[data-bridge-proxy-result]"), text, tone);
    const field = (name) => container.querySelector(`[data-bridge-proxy="${name}"]`);
    const urlInput = field("url");
    const userInput = field("username");
    const passwordInput = field("password");
    const typed = splitProxyLogin(urlInput ? urlInput.value : "");
    let username = userInput ? userInput.value.trim() : "";
    let password = passwordInput ? passwordInput.value : "";
    if (typed.login) {
      // A login typed into the address moves to its own fields.
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
    if (button) button.disabled = true;
    say("Saving…", "");
    try {
      const current = await connectorJson(`/api/connectors/${LOCAL_BRIDGE_CONNECTOR}`, { method: "GET" });
      const config = Object.assign({}, current.config && typeof current.config === "object" ? current.config : {});
      if (typed.url) config.mobile_proxy = { url: typed.url, username, password };
      else delete config.mobile_proxy;
      const saved = await connectorJson(`/api/connectors/${LOCAL_BRIDGE_CONNECTOR}`, { method: "PUT", body: JSON.stringify({ enabled: Boolean(current.enabled), config }) });
      setBridgeProxy(saved.config ? saved.config.mobile_proxy : null);
      if (typed.url) say(username || password ? "Proxy saved for this computer, with its login." : "Proxy saved for this computer.", "success");
      else say("Using this computer's own proxy settings; a user name and password are kept only with a proxy address.", "success");
      return true;
    } catch (error) {
      say(`Could not save the proxy: ${errorText(error)}`, "error");
      return false;
    } finally {
      if (button) button.disabled = false;
    }
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
    const proxySave = target.closest("[data-bridge-proxy-save]");
    if (proxySave) {
      event.preventDefault();
      saveProxy(proxySave.closest("[data-mobile-overview]"), proxySave);
      return;
    }
    const copy = target.closest("[data-recording-copy]");
    if (copy) {
      copyText(copy, copy.dataset.recordingCopy || "");
      return;
    }
    if (target.matches("[data-recording-dialog]") && view.replay.dialog && !view.replay.dialog.busy) {
      // A click on the backdrop closes (or hides) the dialog.
      closeReplayDialog();
      return;
    }
    const replayButton = target.closest("[data-replay-action]");
    if (replayButton && replayButton.closest("[data-recording-root]")) {
      const action = replayButton.dataset.replayAction;
      const r = view.replay;
      if (action === "start") startReplay(replayButton);
      else if (action === "stop") stopReplay(replayButton);
      else if (action === "close") closeReplayDialog();
      else if (action === "show") {
        const item = (r.dialog && r.dialog.item) || replayedItem();
        if (item) openReplayDialog(item, r.running && r.running.status === "running" ? "running" : (r.result ? "done" : "setup"));
      } else if (action === "again") {
        const item = r.dialog && r.dialog.item;
        if (item) {
          r.result = null;
          openReplayDialog(item);
        }
      } else if (action === "save-again") saveReplay();
      else if (action === "review") {
        const res = r.result;
        const script = reviewableReplay(res && res.result ? res.result.report : null);
        if (script) requestReview(script.name, script.path, script.reportPath);
      }
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
    else if (action === "upload-toggle") toggleUpload(root);
    else if (action === "upload-build") uploadBuildFromPanel(root, button);
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
    else if (action === "ask-assistant") askAssistant();
    else if (action === "use-name") useName(root, button.dataset.name || "");
    else if (action === "review-script") requestReview(button.dataset.name || "", button.dataset.path || "", button.dataset.report || "");
    else if (action === "replay-script") openReplayDialog({ name: button.dataset.name || "", kind: "script", path: `${SCRIPTS_DIR}/${recordingPlatform()}/${button.dataset.name || ""}.yaml`, platform: recordingPlatform() });
    else if (action === "replay-recording") openReplayDialog({ name: button.dataset.name || "", kind: "recording", path: `${RECORDINGS_DIR}/${button.dataset.name || ""}.wdlog.json`, platform: recordingPlatform() });
    else if (action === "compile-recording") openCompile(button.dataset.name || "");
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
    } else if (target.matches("[data-replay-chain]")) {
      const d = view.replay.dialog;
      if (d) {
        const name = target.dataset.replayChain;
        d.chain = target.checked ? d.chain.concat(name) : d.chain.filter((item) => item !== name);
        loadReplaySecrets().then(() => renderReplay());
      }
    } else if (target.matches("[data-replay-start]")) {
      view.replay.start = target.value;
    }
  });

  // Escape closes (or hides) the replay dialog.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const d = view.replay && view.replay.dialog;
    if (d && !d.busy && panelRoot()?.querySelector("[data-recording-dialog]")) closeReplayDialog();
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
    copyMessage,
    compiledMessage,
    startMessage,
    planView,
    scriptScenario,
    scriptStatus,
    nextToRecord,
    replayMessage,
    replayNoun,
    replayPhaseIndex,
    sameReplayRow,
    recordingState,
    segmentSecrets,
    flowSteps,
    parseSegmentYaml,
    proxyForBridge,
    proxySettings,
    setBridgeProxy,
    splitProxyLogin,
    suggestCustomId,
    probeBridge: probe,
    callBridge: call,
    waitForDevice,
    localBinaryHtml,
  });
})();
