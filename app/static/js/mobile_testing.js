/**
 * Mobile testing: app packages (Connectors > BrowserStack) and the Recording
 * panel of the assistant chat.
 *
 * App packages are builds uploaded to BrowserStack through /api/app-packages;
 * the body is the raw file so Portal can stream it on without buffering.
 * The Recording panel is in the second half of this file.
 */
(function () {
  "use strict";

  const API = "/api/app-packages";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try {
        window.lucide.createIcons();
      } catch (error) {
        /* icons are cosmetic */
      }
    }
  }

  function formatSize(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return "";
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(n / 1024))} KB`;
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  // Mirrors default_custom_id in app_package_service.py so the field shows
  // what the server would pick: every build of an app shares one custom id.
  function suggestCustomId(fileName) {
    const name = String(fileName || "");
    const dot = name.lastIndexOf(".");
    const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
    const platform = ext === "ipa" ? "ios" : (ext === "apk" || ext === "aab" ? "android" : "");
    if (!platform) return "";
    let stem = (dot >= 0 ? name.slice(0, dot) : name);
    stem = stem.replace(/[-_.]v?\d+(?:[._]\d+)*(?=$|[-_])/g, "");
    const base = stem.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "").slice(0, 100);
    if (!base) return "";
    return base.endsWith(platform) ? base : `${base}-${platform}`.slice(0, 100);
  }

  function packagesRoot() {
    return document.querySelector("[data-app-packages]");
  }

  function setStatus(root, text, tone) {
    const el = root && root.querySelector("[data-app-package-status]");
    if (!el) return;
    el.textContent = text || "";
    el.className = `portal-inline-state${text ? " is-visible" : ""}${tone ? ` is-${tone}` : ""}`;
  }

  function setProgress(root, fraction) {
    const bar = root && root.querySelector("[data-app-package-progress]");
    if (!bar) return;
    if (fraction == null) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    const fill = bar.querySelector("i");
    if (fill) fill.style.width = `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%`;
  }

  function errorDetail(status, body) {
    if (body && typeof body.detail === "string") return body.detail;
    return `Request failed (HTTP ${status}).`;
  }

  function packageRowHtml(pkg) {
    const platform = pkg.platform === "ios" ? "iOS" : "Android";
    const expiry = pkg.expired
      ? `<span class="portal-status-badge is-error">Expired</span>`
      : `<span class="portal-status-badge ${pkg.expiring_soon ? "is-warning" : "is-neutral"}" title="BrowserStack deletes uploads after 30 days">${esc(pkg.days_left)} days left</span>`;
    const meta = [platform, formatSize(pkg.size_bytes), formatDate(pkg.uploaded_at)].filter(Boolean).join(" · ");
    return `
      <div class="portal-app-package-row" data-app-package-id="${esc(pkg.id)}">
        <div class="portal-app-package-main">
          <strong>${esc(pkg.note || pkg.file_name)}</strong>
          <span class="portal-inline-note">${esc(meta)}${pkg.note ? ` · ${esc(pkg.file_name)}` : ""}</span>
          <span class="portal-app-package-ids">
            ${pkg.custom_id ? `<code title="Custom id">${esc(pkg.custom_id)}</code>` : ""}
            <code title="BrowserStack app URL">${esc(pkg.app_url)}</code>
          </span>
        </div>
        <div class="portal-app-package-actions">
          ${expiry}
          <button type="button" class="composer-pill-btn" data-app-package-action="copy" data-value="${esc(pkg.custom_id || pkg.app_url)}" title="Copy ${pkg.custom_id ? "custom id" : "app URL"}"><i data-lucide="copy" class="w-4 h-4"></i></button>
          <button type="button" class="composer-pill-btn" data-app-package-action="delete" data-id="${esc(pkg.id)}" title="Delete from BrowserStack"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        </div>
      </div>`;
  }

  function renderPackages(root, packages) {
    const list = root.querySelector("[data-app-package-list]");
    if (!list) return;
    if (!packages.length) {
      list.innerHTML = `<div class="portal-panel-empty">No app packages yet. Upload a build to record or run tests on it.</div>`;
      return;
    }
    list.innerHTML = packages.map(packageRowHtml).join("");
    renderIcons();
  }

  async function loadPackages(root) {
    const list = root.querySelector("[data-app-package-list]");
    try {
      const resp = await fetch(API, { credentials: "same-origin" });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(errorDetail(resp.status, body));
      root.dataset.maxMb = String(body.max_mb || "");
      renderPackages(root, Array.isArray(body.packages) ? body.packages : []);
    } catch (error) {
      if (list) list.innerHTML = `<div class="portal-inline-state is-visible is-error">${esc(error.message || error)}</div>`;
    }
  }

  async function loadRemote(root) {
    const target = root.querySelector("[data-app-package-remote-list]");
    if (!target) return;
    target.innerHTML = `<div class="portal-inline-state is-visible">Asking BrowserStack…</div>`;
    try {
      const resp = await fetch(`${API}?remote=1`, { credentials: "same-origin" });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(errorDetail(resp.status, body));
      if (body.remote_error) throw new Error(body.remote_error);
      const remote = Array.isArray(body.remote) ? body.remote : [];
      if (!remote.length) {
        target.innerHTML = `<div class="portal-panel-empty">No other builds on BrowserStack.</div>`;
        return;
      }
      target.innerHTML = remote.map((app) => `
        <div class="portal-app-package-row">
          <div class="portal-app-package-main">
            <strong>${esc(app.file_name || app.app_url)}</strong>
            <span class="portal-inline-note">${esc([app.app_version && `version ${app.app_version}`, app.uploaded_at && formatDate(app.uploaded_at)].filter(Boolean).join(" · "))}</span>
            <span class="portal-app-package-ids">${app.custom_id ? `<code>${esc(app.custom_id)}</code>` : ""}<code>${esc(app.app_url)}</code></span>
          </div>
          <div class="portal-app-package-actions">
            <button type="button" class="composer-pill-btn" data-app-package-action="copy" data-value="${esc(app.custom_id || app.app_url)}" title="Copy"><i data-lucide="copy" class="w-4 h-4"></i></button>
          </div>
        </div>`).join("");
      renderIcons();
    } catch (error) {
      target.innerHTML = `<div class="portal-inline-state is-visible is-error">${esc(error.message || error)}</div>`;
    }
  }

  function upload(root) {
    const input = root.querySelector("[data-app-package-file]");
    const file = input && input.files && input.files[0];
    if (!file) {
      setStatus(root, "Choose a build file first.", "error");
      return;
    }
    const maxMb = Number(root.dataset.maxMb || 0);
    if (maxMb && file.size > maxMb * 1024 * 1024) {
      setStatus(root, `The file is larger than ${maxMb} MB.`, "error");
      return;
    }
    const params = new URLSearchParams();
    const customId = (root.querySelector("[data-app-package-custom-id]")?.value || "").trim();
    const note = (root.querySelector("[data-app-package-note]")?.value || "").trim();
    if (customId) params.set("custom_id", customId);
    if (note) params.set("note", note);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}${params.toString() ? `?${params}` : ""}`);
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) setProgress(root, event.loaded / event.total);
    };
    xhr.onload = () => {
      setProgress(root, null);
      let body = {};
      try {
        body = JSON.parse(xhr.responseText || "{}");
      } catch (error) {
        body = {};
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        setStatus(root, `Uploaded ${body.file_name || file.name} as ${body.custom_id || body.app_url}.`, "success");
        if (input) input.value = "";
        loadPackages(root);
      } else {
        setStatus(root, errorDetail(xhr.status, body), "error");
      }
    };
    xhr.onerror = () => {
      setProgress(root, null);
      setStatus(root, "The upload did not reach Portal. Check your connection and the file size.", "error");
    };
    setStatus(root, `Uploading ${file.name}… BrowserStack processes the build after the transfer, which can take a minute.`, "");
    setProgress(root, 0);
    xhr.send(file);
  }

  async function fromUrl(root) {
    const url = (root.querySelector("[data-app-package-url]")?.value || "").trim();
    if (!url) {
      setStatus(root, "Paste the build URL first.", "error");
      return;
    }
    const payload = {
      url,
      custom_id: (root.querySelector("[data-app-package-custom-id]")?.value || "").trim() || null,
      note: (root.querySelector("[data-app-package-note]")?.value || "").trim() || null,
    };
    setStatus(root, "Fetching the build…", "");
    try {
      const resp = await fetch(`${API}/from-url`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(errorDetail(resp.status, body));
      setStatus(root, `Added ${body.file_name} as ${body.custom_id || body.app_url}.`, "success");
      loadPackages(root);
    } catch (error) {
      setStatus(root, error.message || String(error), "error");
    }
  }

  async function removePackage(root, id) {
    const confirmed = typeof window.showConfirm === "function"
      ? await window.showConfirm({ title: "Delete app package?", message: "The build is deleted from BrowserStack too. Scripts that name its custom id pick the next newest build with that id.", confirmText: "Delete", danger: true })
      : window.confirm("Delete this build from BrowserStack?");
    if (!confirmed) return;
    try {
      const resp = await fetch(`${API}/${encodeURIComponent(id)}`, { method: "DELETE", credentials: "same-origin" });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(errorDetail(resp.status, body));
      setStatus(root, "Deleted.", "success");
      loadPackages(root);
    } catch (error) {
      setStatus(root, error.message || String(error), "error");
    }
  }

  async function copyValue(button) {
    const value = button.dataset.value || "";
    try {
      await navigator.clipboard.writeText(value);
      button.classList.add("is-copied");
      window.setTimeout(() => button.classList.remove("is-copied"), 1200);
    } catch (error) {
      window.prompt("Copy:", value);
    }
  }

  function initPackages() {
    const root = packagesRoot();
    if (!root || root.dataset.ready === "1") return;
    root.dataset.ready = "1";
    loadPackages(root);
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const button = target && target.closest("[data-app-package-action]");
    if (!button) return;
    const root = button.closest("[data-app-packages]") || packagesRoot();
    const action = button.dataset.appPackageAction;
    if (action === "copy") {
      copyValue(button);
      return;
    }
    if (!root) return;
    if (action === "upload") upload(root);
    else if (action === "from-url") fromUrl(root);
    else if (action === "delete") removePackage(root, button.dataset.id);
  });

  document.addEventListener("change", (event) => {
    const input = event.target instanceof Element ? event.target.closest("[data-app-package-file]") : null;
    if (!input) return;
    const root = input.closest("[data-app-packages]");
    const customInput = root && root.querySelector("[data-app-package-custom-id]");
    const file = input.files && input.files[0];
    if (customInput && file && !customInput.value.trim()) customInput.value = suggestCustomId(file.name);
  });

  // A details element's toggle event does not bubble; listen in the capture phase.
  document.addEventListener("toggle", (event) => {
    const details = event.target instanceof Element ? event.target.closest("[data-app-package-remote]") : null;
    if (!details || !details.open || details.dataset.loaded === "1") return;
    details.dataset.loaded = "1";
    const root = details.closest("[data-app-packages]");
    if (root) loadRemote(root);
  }, true);

  document.addEventListener("htmx:afterSettle", initPackages);
  document.addEventListener("DOMContentLoaded", initPackages);

  window.EfpMobileTesting = Object.assign(window.EfpMobileTesting || {}, {
    suggestCustomId,
    formatSize,
    initPackages,
  });
})();

/**
 * Test secrets rows of the BrowserStack connector: add, remove, and keep
 * the posted names numbered in page order (mobile_test_secrets_<i>_name and
 * _secret), the shape the server reads.
 */
(function () {
  "use strict";

  function renumber(list) {
    list.querySelectorAll("[data-test-secret-row]").forEach((row, index) => {
      row.querySelectorAll("[data-test-secret-field]").forEach((input) => {
        input.name = `mobile_test_secrets_${index}_${input.dataset.testSecretField}`;
      });
    });
  }

  function markTouched(from) {
    const section = from.closest("[data-managed-section]");
    const form = from.closest("form") || document;
    const flag = section && form.querySelector(`[data-touch-flag="${section.dataset.managedSection}"]`);
    if (flag) flag.value = "1";
  }

  function addRow(button) {
    const box = button.closest("[data-test-secrets]");
    const list = box && box.querySelector("[data-test-secret-rows]");
    if (!list) return;
    const row = document.createElement("div");
    row.className = "portal-test-secret-row";
    row.dataset.testSecretRow = "";
    row.innerHTML = [
      '<input type="text" placeholder="MOBILE_SECRET_PASSWORD" class="portal-form-input" aria-label="Test secret name" autocomplete="off" spellcheck="false" data-test-secret-field="name" />',
      '<input type="password" placeholder="Value" class="portal-form-input" aria-label="Test secret value" autocomplete="new-password" data-test-secret-field="secret" />',
      '<button type="button" class="portal-btn is-secondary" data-action="remove-test-secret" aria-label="Remove this test secret">Remove</button>',
    ].join("");
    list.appendChild(row);
    renumber(list);
    markTouched(button);
    row.querySelector("input").focus();
  }

  function removeRow(button) {
    const row = button.closest("[data-test-secret-row]");
    const list = row && row.parentElement;
    if (!row || !list) return;
    markTouched(button);
    row.remove();
    renumber(list);
  }

  document.addEventListener("click", (event) => {
    const add = event.target.closest('[data-action="add-test-secret"]');
    if (add) {
      event.preventDefault();
      addRow(add);
      return;
    }
    const remove = event.target.closest('[data-action="remove-test-secret"]');
    if (remove) {
      event.preventDefault();
      removeRow(remove);
    }
  });

  // Names are environment variable names; typing them in capitals saves a
  // round trip through the validation message.
  document.addEventListener("input", (event) => {
    const input = event.target;
    if (!input || !input.matches || !input.matches('[data-test-secret-field="name"]')) return;
    const upper = input.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
    if (upper !== input.value) input.value = upper;
  });
})();

/**
 * Recording panel (assistant chat tool bar > Recording).
 *
 * 1. Start: asks the assistant (/record-mobile-segment) to start a BrowserStack
 *    device with an app package and hold it.
 * 2. The assistant publishes the held session at mobile/recording/active.json;
 *    the panel polls it and shows the device, the hold, and how to attach.
 * 3. Record: the hosted Inspector opens already attached (Portal proxies and
 *    logs its commands; "Segment done" writes the log into the workspace), or
 *    the member records in the desktop Inspector and uploads its code.
 * Each finished segment is announced in the chat so the assistant compiles it.
 */
(function () {
  "use strict";

  const API = "/api/mobile-recordings";
  const HANDSHAKE_PATH = "mobile/recording/active.json";
  const RECORDINGS_DIR = "mobile/recordings";
  const POLL_MS = 4000;
  const CODE_EXTENSIONS = [".py", ".java", ".js", ".rb", ".robot", ".cs"];
  const view = { agentId: "", status: null, handshake: null, recording: null, planned: [], done: [], sessionKey: "" };
  let pollTimer = 0;
  let tickTimer = 0;

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

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
    const el = panelRoot()?.querySelector("[data-recording-status]");
    if (!el) return;
    el.textContent = text || "";
    el.className = `portal-inline-state${text ? " is-visible" : ""}${tone ? ` is-${tone}` : ""}`;
  }

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try {
        window.lucide.createIcons();
      } catch (error) {
        /* icons are cosmetic */
      }
    }
  }

  function parseSegments(text) {
    return String(text || "")
      .split(/[\n,]+/)
      .map((item) => item.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+/, ""))
      .filter(Boolean)
      .slice(0, 30);
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

  function shellHtml() {
    return `
      <div class="efp-recording" data-recording-root>
        <section class="efp-recording-section">
          <h5>1 · Start a recording session</h5>
          <p class="portal-panel-note">The assistant starts a BrowserStack device with the build and holds it while you record short segments.</p>
          <label class="portal-form-label"><span class="portal-form-label">App package</span>
            <select class="portal-form-select" data-recording-package><option value="">Loading app packages…</option></select>
          </label>
          <label class="portal-form-label"><span class="portal-form-label">Device (optional)</span>
            <input class="portal-form-input" data-recording-device placeholder="Google Pixel 8" />
          </label>
          <label class="portal-form-label"><span class="portal-form-label">Segments to record, in order</span>
            <textarea class="portal-form-textarea" rows="3" data-recording-segments placeholder="seg-login&#10;seg-skip-intro&#10;seg-select-currency"></textarea>
          </label>
          <button type="button" class="portal-btn is-primary" data-recording-action="start"><i data-lucide="play" class="w-4 h-4"></i>Start recording session</button>
        </section>
        <section class="efp-recording-section">
          <h5>2 · Record</h5>
          <div data-recording-session><div class="portal-inline-state is-visible">No recording session yet.</div></div>
        </section>
        <div class="portal-inline-state" data-recording-status role="status"></div>
      </div>`;
  }

  function sessionCardHtml() {
    const hs = view.handshake;
    const st = view.status || {};
    const held = hs.hold_deadline && remaining(hs.hold_deadline) !== "expired";
    const owner = hs.control_owner === "human" ? (held ? "Held for you" : "Hold expired") : "Assistant has control";
    const tone = hs.control_owner === "human" && held ? "success" : "warning";
    const platform = hs.platform === "ios" ? "iOS" : "Android";
    const hub = st.hub_url || "";
    const segment = (view.recording && view.recording.segment) || view.planned[view.done.length] || view.planned[0] || "seg-1";
    const inspectorButton = st.inspector_available
      ? `<button type="button" class="portal-btn is-primary" data-recording-action="open-inspector"><i data-lucide="external-link" class="w-4 h-4"></i>Open Inspector</button>`
      : "";
    const webDone = st.inspector_available
      ? `<button type="button" class="portal-btn is-secondary" data-recording-action="segment-done"><i data-lucide="check" class="w-4 h-4"></i>Segment done</button>`
      : "";
    const doneList = view.done.length
      ? `<ul class="efp-recording-done">${view.done.map((item) => `<li><i data-lucide="check" class="w-3 h-3"></i> ${esc(item.segment)} <span class="efp-card-meta">${esc(item.detail)}</span></li>`).join("")}</ul>`
      : "";
    return `
      <div class="efp-card efp-recording-card">
        <div class="efp-card-head">
          <span class="portal-status-badge is-${tone}">${esc(owner)}</span>
          <strong class="efp-card-title">${esc(hs.device || "BrowserStack device")}</strong>
          <span class="efp-card-meta">${esc(platform)}</span>
        </div>
        ${hs.hold_deadline ? `<div class="efp-card-meta">Session held for another <span data-recording-countdown data-deadline="${esc(hs.hold_deadline)}">${esc(remaining(hs.hold_deadline))}</span></div>` : ""}
        <div class="efp-recording-kv"><span>Session id</span><code>${esc(hs.session_id)}</code><button type="button" class="composer-pill-btn" data-recording-copy="${esc(hs.session_id)}" title="Copy"><i data-lucide="copy" class="w-4 h-4"></i></button></div>
        ${hub ? `<div class="efp-recording-kv"><span>Appium hub</span><code>${esc(hub)}</code><button type="button" class="composer-pill-btn" data-recording-copy="${esc(hub)}" title="Copy"><i data-lucide="copy" class="w-4 h-4"></i></button></div>` : ""}
      </div>
      <label class="portal-form-label"><span class="portal-form-label">Current segment</span>
        <input class="portal-form-input" data-recording-segment value="${esc(segment)}" />
      </label>
      <div class="efp-card-actions">${inspectorButton}${webDone}</div>
      <details class="portal-collapsible"${st.inspector_available ? "" : " open"}>
        <summary class="portal-collapsible-summary"><span>Record with the desktop Appium Inspector</span></summary>
        <ol class="portal-setup-guide-steps">
          <li>In Appium Inspector 2026.5.1 or later, choose the BrowserStack tab and sign in with your BrowserStack username and access key.</li>
          <li>Open <strong>Attach to Session</strong>, paste the session id above, and attach.</li>
          <li>Start the recorder, tap and type in the Inspector (not on the screenshot), then copy the generated code (Python is easiest).</li>
          <li>Save it as a file and upload it here; it is named after the current segment.</li>
        </ol>
        <div class="efp-card-actions">
          <input type="file" accept="${CODE_EXTENSIONS.join(",")}" class="portal-form-input" data-recording-code />
          <button type="button" class="portal-btn is-secondary" data-recording-action="upload-code"><i data-lucide="upload" class="w-4 h-4"></i>Upload recorded code</button>
        </div>
      </details>
      ${doneList}
      <div class="efp-card-actions">
        <button type="button" class="portal-btn is-secondary" data-recording-action="extend"><i data-lucide="timer" class="w-4 h-4"></i>Hold 15 more minutes</button>
        <button type="button" class="portal-btn is-secondary" data-recording-action="finish"><i data-lucide="square" class="w-4 h-4"></i>Finish recording</button>
      </div>`;
  }

  function renderSession() {
    const target = panelRoot()?.querySelector("[data-recording-session]");
    if (!target) return;
    if (view.status && view.status.account_error) {
      target.innerHTML = `<div class="portal-inline-state is-visible is-warning">${esc(view.status.account_error)}</div>`;
      return;
    }
    if (!view.handshake) {
      target.innerHTML = `<div class="portal-inline-state is-visible">No recording session yet. Start one above; the device shows up here when the assistant has it ready.</div>`;
      view.sessionKey = "";
      return;
    }
    // Re-render only when the session changes, so typing in the segment field
    // is not interrupted by the poll.
    const key = [view.handshake.session_id, view.handshake.control_owner, view.handshake.hold_deadline, view.recording ? view.recording.id : "", view.done.length, (view.status || {}).inspector_available].join("|");
    if (key === view.sessionKey) return;
    const focused = document.activeElement && document.activeElement.matches && document.activeElement.matches("[data-recording-segment]");
    const segmentValue = focused ? document.activeElement.value : null;
    view.sessionKey = key;
    target.innerHTML = sessionCardHtml();
    if (segmentValue !== null) {
      const input = target.querySelector("[data-recording-segment]");
      if (input) {
        input.value = segmentValue;
        input.focus();
      }
    }
    renderIcons();
  }

  async function loadPackages() {
    const select = panelRoot()?.querySelector("[data-recording-package]");
    if (!select) return;
    try {
      const resp = await fetch("/api/app-packages", { credentials: "same-origin" });
      const body = await resp.json().catch(() => ({}));
      const packages = (Array.isArray(body.packages) ? body.packages : []).filter((pkg) => !pkg.expired);
      if (!packages.length) {
        select.innerHTML = `<option value="">No app packages. Upload one in Connectors > BrowserStack.</option>`;
        return;
      }
      select.innerHTML = packages.map((pkg) => {
        const value = pkg.custom_id || pkg.app_url;
        const label = `${pkg.note || pkg.file_name} · ${pkg.platform === "ios" ? "iOS" : "Android"}`;
        return `<option value="${esc(value)}" data-platform="${esc(pkg.platform)}">${esc(label)}</option>`;
      }).join("");
    } catch (error) {
      select.innerHTML = `<option value="">Could not load app packages</option>`;
    }
  }

  async function refresh() {
    const agentId = view.agentId;
    if (!agentId) return;
    try {
      const [statusResp, handshakeResp] = await Promise.all([
        fetch(`${API}/status?agent_id=${encodeURIComponent(agentId)}`, { credentials: "same-origin" }),
        fetch(`/a/${encodeURIComponent(agentId)}/api/server-files/content?path=${encodeURIComponent(HANDSHAKE_PATH)}`, { credentials: "same-origin", cache: "no-store" }),
      ]);
      view.status = statusResp.ok ? await statusResp.json() : { account_error: statusResp.status === 403 ? "Only the assistant's owner can record on it." : "" };
      view.recording = view.status && view.status.recording ? view.status.recording : null;
      view.handshake = handshakeResp.ok ? await handshakeResp.json().catch(() => null) : null;
      if (view.handshake && view.handshake.format !== "efp-mobile-recording/v1") view.handshake = null;
    } catch (error) {
      /* keep the last known state; the next poll tries again */
    }
    renderSession();
  }

  function schedule() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(async () => {
      if (!panelRoot() || currentAgentId() !== view.agentId) return;
      if (document.visibilityState !== "hidden") await refresh();
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
          el.textContent = remaining(el.dataset.deadline);
        });
      }, 1000);
    }
  }

  async function openRecordingPanel() {
    const agentId = currentAgentId();
    if (typeof window.setToolPanel !== "function" && typeof setToolPanel !== "function") return;
    const show = typeof window.setToolPanel === "function" ? window.setToolPanel : setToolPanel;
    if (!agentId) {
      show("Recording", "<div class='portal-inline-state is-visible'>Select an assistant first.</div>", "recording");
      return;
    }
    if (view.agentId !== agentId) {
      Object.assign(view, { agentId, status: null, handshake: null, recording: null, planned: [], done: [], sessionKey: "" });
    } else {
      view.sessionKey = "";
    }
    show("Recording", shellHtml(), "recording");
    renderIcons();
    const segments = panelRoot()?.querySelector("[data-recording-segments]");
    if (segments && view.planned.length) segments.value = view.planned.join("\n");
    await Promise.all([loadPackages(), refresh()]);
    schedule();
  }

  function startSession(root) {
    const select = root.querySelector("[data-recording-package]");
    const option = select && select.selectedOptions && select.selectedOptions[0];
    const app = select ? select.value : "";
    if (!app) {
      setStatus("Pick an app package first.", "error");
      return;
    }
    const platform = (option && option.dataset.platform) || "";
    const device = (root.querySelector("[data-recording-device]")?.value || "").trim();
    view.planned = parseSegments(root.querySelector("[data-recording-segments]")?.value);
    view.done = [];
    const lines = [
      "/record-mobile-segment",
      `App: ${app}${platform ? ` (${platform})` : ""}`,
      device ? `Device: ${device}` : "",
      view.planned.length ? `Segments: ${view.planned.join(", ")}` : "",
    ].filter(Boolean);
    if (sendChat(lines.join("\n"))) {
      setStatus("Asked the assistant to start the device. It shows up here when it is ready (about a minute).", "");
    } else {
      setStatus("Open the assistant's chat to start a recording session.", "error");
    }
  }

  async function openInspector(root) {
    const segment = (root.querySelector("[data-recording-segment]")?.value || "").trim();
    // Open the tab inside the click so pop-up blockers allow it, then point
    // it at the Inspector once Portal has bound the session.
    const tab = window.open("about:blank", "_blank");
    try {
      const resp = await fetch(API, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: view.agentId, segment: segment || null }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`);
      view.recording = body;
      view.sessionKey = "";
      if (body.inspector_url && tab) {
        tab.location.href = body.inspector_url;
      } else if (tab) {
        tab.close();
      }
      setStatus("Inspector opened. Tap and type there, then press Segment done.", "success");
      renderSession();
    } catch (error) {
      if (tab) tab.close();
      setStatus(error.message || String(error), "error");
    }
  }

  async function segmentDone(root) {
    if (!view.recording) {
      setStatus("Open the Inspector from this panel first; its commands are what gets recorded.", "error");
      return;
    }
    const segment = (root.querySelector("[data-recording-segment]")?.value || "").trim();
    try {
      const resp = await fetch(`${API}/${encodeURIComponent(view.recording.id)}/segments`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segment: segment || null, next_segment: nextPlanned(segment) || null }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`);
      view.done.push({ segment: body.segment, detail: `${body.actions} actions${body.secrets ? `, ${body.secrets} secret` : ""}` });
      view.recording.segment = body.next_segment;
      view.sessionKey = "";
      sendChat(`Segment ${body.segment} recorded: ${body.path}`);
      setStatus(`Saved ${body.segment}. The assistant compiles it; carry on with ${body.next_segment} in the Inspector.`, "success");
      renderSession();
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
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
    if (!CODE_EXTENSIONS.includes(ext)) {
      setStatus(`Upload the recorder's code (${CODE_EXTENSIONS.join(", ")}).`, "error");
      return;
    }
    const segment = ((root.querySelector("[data-recording-segment]")?.value || "").trim() || "segment").replace(/[^A-Za-z0-9._-]+/g, "-");
    const form = new FormData();
    form.append("file", new File([file], `${segment}${ext}`, { type: file.type || "text/plain" }));
    form.append("path", RECORDINGS_DIR);
    try {
      const resp = await fetch(`/a/${encodeURIComponent(view.agentId)}/api/server-files/upload`, { method: "POST", credentials: "same-origin", body: form });
      if (!resp.ok) throw new Error(`Upload failed (HTTP ${resp.status}).`);
      const path = `${RECORDINGS_DIR}/${segment}${ext}`;
      view.done.push({ segment, detail: file.name });
      view.sessionKey = "";
      const next = nextPlanned(segment);
      sendChat(`Segment ${segment} recorded: ${path}`);
      setStatus(`Uploaded ${path}.${next ? ` Next: ${next}.` : ""}`, "success");
      renderSession();
      const segmentInput = panelRoot()?.querySelector("[data-recording-segment]");
      if (segmentInput && next) segmentInput.value = next;
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
  }

  async function finish() {
    if (view.recording) {
      await fetch(`${API}/${encodeURIComponent(view.recording.id)}/close`, { method: "POST", credentials: "same-origin" }).catch(() => null);
      view.recording = null;
    }
    sendChat("Recording finished. Compile any segment not imported yet, then end the recording session.");
    setStatus("Asked the assistant to wrap up the recording.", "success");
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const copy = target.closest("[data-recording-copy]");
    if (copy) {
      const value = copy.dataset.recordingCopy || "";
      navigator.clipboard?.writeText(value).then(() => {
        copy.classList.add("is-copied");
        window.setTimeout(() => copy.classList.remove("is-copied"), 1200);
      }).catch(() => window.prompt("Copy:", value));
      return;
    }
    const button = target.closest("[data-recording-action]");
    const root = button && button.closest("[data-recording-root]");
    if (!button || !root) return;
    const action = button.dataset.recordingAction;
    if (action === "start") startSession(root);
    else if (action === "open-inspector") openInspector(root);
    else if (action === "segment-done") segmentDone(root);
    else if (action === "upload-code") uploadCode(root);
    else if (action === "extend") {
      if (sendChat("Keep the recording device held for another 15 minutes.")) setStatus("Asked the assistant to extend the hold.", "success");
    } else if (action === "finish") finish();
  });

  window.EfpMobileTesting = Object.assign(window.EfpMobileTesting || {}, {
    openRecordingPanel,
    parseSegments,
  });
})();
