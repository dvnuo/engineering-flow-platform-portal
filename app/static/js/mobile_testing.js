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
