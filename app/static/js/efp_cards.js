/**
 * Result cards for mobile scenario testing: review checklists, evidence, and
 * scenario matrices.
 *
 * An assistant reply (or a task's final response) carries a fenced block
 * whose language names the card and whose body is JSON:
 *
 *   ```efp-review    {"title", "summary", "items": [{"id", "title", "type", "examples", "warnings", "detail"}]}
 *   ```efp-evidence  {"path": "mobile/runs/.../evidence.json"} or {"paths": [...]} or inline evidence objects
 *   ```efp-matrix    {"path": "mobile/runs/.../matrix.json"} or an inline efp-matrix/v1 document
 *
 * Paths are workspace paths of the assistant; files are read through the
 * Portal proxy (/a/{agent}/api/server-files/...). chat_ui.js hands fenced
 * blocks to buildFromCode; server-rendered pages (the task detail) place
 * <div data-efp-card="..."> placeholders that hydrate() fills in.
 */
(function () {
  "use strict";

  const LANGS = new Set(["efp-review", "efp-evidence", "efp-matrix"]);
  const MATRIX_POLL_MS = 10000;
  const FETCH_CACHE_MS = 4000;
  const fetchCache = new Map();

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function isCardLanguage(lang) {
    return LANGS.has(String(lang || "").trim().toLowerCase());
  }

  function parsePayload(text) {
    try {
      const value = JSON.parse(String(text || ""));
      return value && typeof value === "object" ? value : null;
    } catch (error) {
      return null;
    }
  }

  // ----- workspace paths -----------------------------------------------------

  function normalizePath(path) {
    const parts = [];
    for (const segment of String(path || "").replace(/\\/g, "/").split("/")) {
      if (!segment || segment === ".") continue;
      if (segment === "..") {
        if (!parts.length) return "";
        parts.pop();
        continue;
      }
      parts.push(segment);
    }
    return parts.join("/");
  }

  function dirname(path) {
    const normalized = normalizePath(path);
    const index = normalized.lastIndexOf("/");
    return index > 0 ? normalized.slice(0, index) : "";
  }

  function resolvePath(baseDir, relative) {
    const value = String(relative || "").trim();
    if (!value) return "";
    if (/^[a-z]+:\/\//i.test(value)) return "";
    if (value.startsWith("/")) return normalizePath(value);
    return normalizePath(baseDir ? `${baseDir}/${value}` : value);
  }

  function contentUrl(agentId, path) {
    if (!agentId || !path) return "";
    return `/a/${encodeURIComponent(agentId)}/api/server-files/content?path=${encodeURIComponent(path)}`;
  }

  function streamUrl(agentId, path) {
    if (!agentId || !path) return "";
    return `/a/${encodeURIComponent(agentId)}/api/server-files/download?paths=${encodeURIComponent(path)}`;
  }

  function safeHttpUrl(url) {
    const value = String(url || "").trim();
    return /^https?:\/\//i.test(value) ? value : "";
  }

  async function fetchWorkspaceJson(agentId, path, { fresh = false } = {}) {
    const url = contentUrl(agentId, path);
    if (!url) throw new Error("No assistant to read the file from.");
    const cached = fetchCache.get(url);
    if (!fresh && cached && Date.now() - cached.at < FETCH_CACHE_MS) return cached.promise;
    const promise = fetch(url, { credentials: "same-origin", cache: "no-store" }).then(async (resp) => {
      if (resp.status === 404) throw new Error(`${path} is not in the assistant's workspace yet.`);
      if (!resp.ok) throw new Error(`Could not read ${path} (HTTP ${resp.status}). Is the assistant running?`);
      return resp.json();
    });
    fetchCache.set(url, { promise, at: Date.now() });
    promise.catch(() => fetchCache.delete(url));
    return promise;
  }

  // ----- shared bits -----------------------------------------------------------

  function statusTone(status) {
    const key = String(status || "").toLowerCase();
    if (key === "passed") return "success";
    if (key === "failed") return "error";
    if (key === "running" || key === "queued") return "warning";
    return "neutral";
  }

  function formatDuration(ms) {
    const n = Number(ms);
    if (!Number.isFinite(n) || n <= 0) return "";
    const seconds = Math.round(n / 1000);
    if (seconds < 60) return `${seconds}s`;
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }

  const CLASSIFICATION_LABELS = {
    drift: "Script drift",
    product_bug: "Product defect",
    infra: "Infrastructure",
    data: "Test data / environment",
    flaky: "Flaky",
    passed_on_rerun: "Passed on rerun",
  };

  function classificationBadge(value) {
    const key = String(value || "").trim();
    if (!key) return "";
    const tone = key === "product_bug" ? "error" : (key === "drift" ? "warning" : "info");
    return `<span class="portal-status-badge is-${tone}">${esc(CLASSIFICATION_LABELS[key] || key)}</span>`;
  }

  function linksHtml(links) {
    if (!links || typeof links !== "object") return "";
    return Object.entries(links)
      .map(([label, url]) => {
        const href = safeHttpUrl(url);
        return href ? `<a class="portal-link-inline" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : "";
      })
      .filter(Boolean)
      .join(" ");
  }

  function errorText(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (typeof value === "object") {
      if (value.primary) return errorText(value.primary);
      const code = value.code ? `${value.code}: ` : "";
      if (value.message) return `${code}${value.message}`;
    }
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
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

  function setMessage(container, text, tone) {
    container.innerHTML = `<div class="efp-card efp-card-message"><div class="portal-inline-state is-visible${tone ? ` is-${tone}` : ""}">${esc(text)}</div></div>`;
  }

  // ----- evidence -------------------------------------------------------------

  // evidence.json names its files relative to its own directory; inline
  // evidence in a fence uses workspace paths.
  function normalizeEvidence(doc, baseDir) {
    const ev = doc && typeof doc === "object" ? doc : {};
    const resolve = (value) => (baseDir === null ? normalizePath(value) : resolvePath(baseDir, value));
    const screenshots = (Array.isArray(ev.screenshots) ? ev.screenshots : [])
      .map((shot) => {
        if (typeof shot === "string") return { label: "", path: resolve(shot) };
        return { label: shot.label || "", path: resolve(shot.file || shot.path || "") };
      })
      .filter((shot) => shot.path);
    const failureShot = ev.failure_screenshot ? resolve(ev.failure_screenshot) : "";
    if (failureShot && !screenshots.some((shot) => shot.path === failureShot)) {
      screenshots.unshift({ label: "At the failure", path: failureShot });
    }
    return {
      id: ev.id || [ev.scenario, ev.example].filter(Boolean).join("#"),
      scenario: ev.scenario || ev.id || "Scenario",
      example: ev.example || "",
      status: ev.status || "",
      platform: ev.platform || "",
      device: ev.device || "",
      osVersion: ev.os_version || "",
      durationMs: ev.duration_ms,
      sessionUrl: safeHttpUrl(ev.session_url),
      video: ev.video ? resolve(ev.video) : "",
      screenshots,
      error: errorText(ev.error),
      classification: ev.classification || "",
      links: ev.links || null,
      fallbackHits: Array.isArray(ev.fallback_hits) ? ev.fallback_hits.length : 0,
      source: ev.__source || "",
    };
  }

  function evidenceCardHtml(ev, agentId) {
    const title = ev.example ? `${ev.scenario} · ${ev.example}` : ev.scenario;
    const device = [ev.platform === "ios" ? "iOS" : (ev.platform === "android" ? "Android" : ev.platform), ev.device, ev.osVersion]
      .filter(Boolean).join(" · ");
    const meta = [device, formatDuration(ev.durationMs)].filter(Boolean).join(" · ");
    const shots = ev.screenshots.map((shot) => {
      const url = contentUrl(agentId, shot.path);
      return url
        ? `<a class="efp-evidence-shot" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(shot.label || shot.path)}"><img src="${esc(url)}" alt="${esc(shot.label || "Screenshot")}" loading="lazy" /><span>${esc(shot.label)}</span></a>`
        : "";
    }).join("");
    const video = ev.video && agentId
      ? `<video class="efp-evidence-video" controls preload="metadata" src="${esc(streamUrl(agentId, ev.video))}"></video>`
      : "";
    const actions = [
      ev.sessionUrl ? `<a class="portal-link-inline" href="${esc(ev.sessionUrl)}" target="_blank" rel="noopener noreferrer">BrowserStack session</a>` : "",
      ev.source && agentId ? `<a class="portal-link-inline" href="${esc(contentUrl(agentId, ev.source))}" target="_blank" rel="noopener">evidence.json</a>` : "",
      linksHtml(ev.links),
    ].filter(Boolean).join(" ");
    const drift = ev.fallbackHits
      ? `<div class="portal-inline-state is-visible is-warning">${ev.fallbackHits} step(s) matched a fallback locator; the recorded locator has drifted.</div>`
      : "";
    const error = ev.error ? `<pre class="efp-evidence-error">${esc(ev.error)}</pre>` : "";
    return `
      <article class="efp-card efp-evidence is-${statusTone(ev.status)}">
        <header class="efp-card-head">
          <span class="portal-status-badge is-${statusTone(ev.status)}">${esc(ev.status || "unknown")}</span>
          <strong class="efp-card-title">${esc(title)}</strong>
          ${classificationBadge(ev.classification)}
        </header>
        ${meta ? `<div class="efp-card-meta">${esc(meta)}</div>` : ""}
        ${error}
        ${drift}
        ${video}
        ${shots ? `<div class="efp-evidence-shots">${shots}</div>` : ""}
        ${actions ? `<div class="efp-card-actions">${actions}</div>` : ""}
      </article>`;
  }

  async function renderEvidence(container, payload, ctx) {
    const agentId = ctx.agentId;
    const entries = [];
    const add = (item) => {
      if (!item) return;
      if (typeof item === "string") entries.push({ path: item });
      else if (item.path && !item.scenario && !item.status) entries.push({ path: item.path });
      else entries.push({ inline: item });
    };
    if (Array.isArray(payload)) payload.forEach(add);
    else if (Array.isArray(payload.paths)) payload.paths.forEach(add);
    else if (Array.isArray(payload.items)) payload.items.forEach(add);
    else add(payload);
    if (!entries.length) {
      setMessage(container, "No evidence in this block.", "warning");
      return;
    }
    container.innerHTML = `<div class="efp-evidence-list">${entries.map(() => `<div class="efp-card efp-card-loading"><div class="portal-inline-state is-visible">Loading evidence…</div></div>`).join("")}</div>`;
    const slots = container.querySelectorAll(".efp-evidence-list > div");
    await Promise.all(entries.map(async (entry, index) => {
      const slot = slots[index];
      try {
        let ev;
        if (entry.inline) {
          ev = normalizeEvidence(entry.inline, null);
        } else {
          const path = normalizePath(entry.path);
          const doc = await fetchWorkspaceJson(agentId, path);
          ev = normalizeEvidence(Object.assign({}, doc, { __source: path }), dirname(path));
        }
        slot.outerHTML = evidenceCardHtml(ev, agentId);
      } catch (error) {
        slot.innerHTML = `<div class="portal-inline-state is-visible is-error">${esc(error.message || error)}</div>`;
      }
    }));
    renderIcons();
  }

  // ----- matrix ---------------------------------------------------------------

  function matrixSummary(rows) {
    const summary = { total: rows.length, passed: 0, failed: 0, running: 0, queued: 0 };
    rows.forEach((row) => {
      const key = String(row.status || "").toLowerCase();
      if (key in summary) summary[key] += 1;
    });
    return summary;
  }

  function matrixHtml(doc, ctx, baseDir) {
    const rows = Array.isArray(doc.rows) ? doc.rows : [];
    const summary = matrixSummary(rows);
    const pct = (n) => (summary.total ? (n / summary.total) * 100 : 0);
    const headline = [
      `${summary.total} scenario runs`,
      summary.passed ? `${summary.passed} passed` : "",
      summary.failed ? `${summary.failed} failed` : "",
      summary.running ? `${summary.running} running` : "",
      summary.queued ? `${summary.queued} queued` : "",
    ].filter(Boolean).join(" · ");
    const body = rows.map((row, index) => {
      const evidencePath = row.evidence ? resolvePath(baseDir, row.evidence) : "";
      const videoPath = row.video ? resolvePath(baseDir, row.video) : "";
      const session = safeHttpUrl(row.session_url);
      const actions = [
        evidencePath ? `<button type="button" class="composer-pill-btn" data-efp-evidence-toggle="${esc(evidencePath)}" data-row="${index}">Evidence</button>` : "",
        videoPath && ctx.agentId ? `<a class="portal-link-inline" href="${esc(streamUrl(ctx.agentId, videoPath))}" target="_blank" rel="noopener">Video</a>` : "",
        session ? `<a class="portal-link-inline" href="${esc(session)}" target="_blank" rel="noopener noreferrer">Session</a>` : "",
        linksHtml(row.links),
      ].filter(Boolean).join(" ");
      const note = [row.error, row.note].filter(Boolean).map((text) => `<div class="efp-matrix-note">${esc(text)}</div>`).join("");
      return `
        <tr class="is-${statusTone(row.status)}">
          <td><strong>${esc(row.case || row.id)}</strong>${row.example ? `<div class="efp-card-meta">${esc(row.example)}</div>` : ""}${note}</td>
          <td><span class="portal-status-badge is-${statusTone(row.status)}">${esc(row.status || "")}</span></td>
          <td>${esc([row.device, row.platform].filter(Boolean).join(" · "))}</td>
          <td>${esc(formatDuration(row.duration_ms))}</td>
          <td>${classificationBadge(row.classification)}${row.fallback_hits ? ` <span class="portal-status-badge is-warning" title="Steps that matched a fallback locator">drift ${esc(row.fallback_hits)}</span>` : ""}</td>
          <td class="efp-matrix-actions">${actions}</td>
        </tr>
        <tr class="efp-matrix-evidence-row hidden" data-efp-evidence-row="${index}"><td colspan="6"><div data-efp-evidence-slot></div></td></tr>`;
    }).join("");
    return `
      <article class="efp-card efp-matrix">
        <header class="efp-card-head">
          <strong class="efp-card-title">${esc(doc.suite || "Scenario matrix")}${doc.run ? ` · ${esc(doc.run)}` : ""}</strong>
          <span class="efp-card-meta">${esc(headline)}</span>
        </header>
        <div class="efp-matrix-bar" aria-hidden="true">
          <i class="is-success" style="width:${pct(summary.passed)}%"></i><i class="is-error" style="width:${pct(summary.failed)}%"></i><i class="is-warning" style="width:${pct(summary.running + summary.queued)}%"></i>
        </div>
        <div class="message-table-wrap">
          <table class="efp-matrix-table">
            <thead><tr><th>Scenario</th><th>Result</th><th>Device</th><th>Time</th><th>Triage</th><th></th></tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </article>`;
  }

  async function renderMatrix(container, payload, ctx) {
    const path = payload && typeof payload.path === "string" ? normalizePath(payload.path) : "";
    let doc = payload;
    if (path) {
      try {
        doc = await fetchWorkspaceJson(ctx.agentId, path, { fresh: true });
      } catch (error) {
        if (ctx.quietMissing) {
          container.innerHTML = "";
          return;
        }
        setMessage(container, error.message || String(error), "error");
        return;
      }
    }
    if (!doc || !Array.isArray(doc.rows)) {
      setMessage(container, "This block is not a scenario matrix.", "warning");
      return;
    }
    container.innerHTML = matrixHtml(doc, ctx, path ? dirname(path) : "");
    container.dataset.matrixPath = path;
    renderIcons();
    const live = doc.rows.some((row) => ["running", "queued"].includes(String(row.status || "").toLowerCase()));
    window.clearTimeout(Number(container.dataset.pollTimer || 0));
    if (path && live) {
      const timer = window.setTimeout(() => {
        if (container.isConnected) renderMatrix(container, payload, ctx);
      }, MATRIX_POLL_MS);
      container.dataset.pollTimer = String(timer);
    }
  }

  // ----- review ---------------------------------------------------------------

  function reviewHtml(review, ctx) {
    const items = Array.isArray(review.items) ? review.items : [];
    const list = items.map((item, index) => {
      const id = String(item.id || `#${index + 1}`);
      const warnings = (Array.isArray(item.warnings) ? item.warnings : []).map((w) => `<li>${esc(w)}</li>`).join("");
      const type = String(item.type || "").toLowerCase();
      const typeBadge = type ? `<span class="portal-status-badge is-${type === "negative" ? "warning" : (type === "positive" ? "success" : "info")}">${esc(type)}</span>` : "";
      const examples = item.examples ? `<span class="efp-card-meta">${esc(item.examples)} example rows</span>` : "";
      const detail = item.detail ? `<details class="portal-collapsible"><summary class="portal-collapsible-summary"><span>Details</span></summary><pre class="efp-review-detail">${esc(item.detail)}</pre></details>` : "";
      return `
        <li class="efp-review-item">
          <label class="efp-review-check">
            <input type="checkbox" data-efp-review-item="${esc(id)}" ${item.selected === false ? "" : "checked"} />
            <span><strong>${esc(id)}</strong> ${esc(item.title || "")}</span>
          </label>
          <div class="efp-review-tags">${typeBadge} ${examples}</div>
          ${warnings ? `<ul class="efp-review-warnings">${warnings}</ul>` : ""}
          ${detail}
        </li>`;
    }).join("");
    return `
      <article class="efp-card efp-review" data-efp-review-kind="${esc(review.kind || "review")}">
        <header class="efp-card-head">
          <span class="portal-status-badge is-info">Review</span>
          <strong class="efp-card-title">${esc(review.title || "Review")}</strong>
        </header>
        ${review.summary ? `<p class="efp-card-meta">${esc(review.summary)}</p>` : ""}
        <ul class="efp-review-list">${list}</ul>
        <label class="portal-form-label"><span class="portal-form-label">Notes for the assistant</span>
          <textarea class="portal-form-textarea" rows="3" data-efp-review-notes placeholder="What to change, or the missing expected values"></textarea>
        </label>
        <div class="efp-card-actions">
          <button type="button" class="portal-btn is-primary" data-efp-review-decision="approve">${esc(review.approve_label || "Approve selected")}</button>
          <button type="button" class="portal-btn is-secondary" data-efp-review-decision="changes">Request changes</button>
          <span class="portal-inline-state" data-efp-review-status role="status"></span>
        </div>
      </article>`;
  }

  // The text the assistant receives, in chat or as a task follow-up. The
  // first line is fixed so skills can recognise a review decision.
  function reviewDecisionText(review, decision, approvedIds, declinedIds, notes) {
    const lines = [
      `REVIEW DECISION: ${decision === "approve" ? "approved" : "changes requested"} (${review.kind || "review"}${review.title ? `: ${review.title}` : ""})`,
      `Approved: ${approvedIds.length ? approvedIds.join(", ") : "none"}`,
      `Not approved: ${declinedIds.length ? declinedIds.join(", ") : "none"}`,
    ];
    if (notes) lines.push(`Notes: ${notes}`);
    return lines.join("\n");
  }

  function sendChatText(text) {
    const input = document.getElementById("chat-input");
    const form = document.getElementById("chat-form");
    if (!input || !form) return false;
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    return true;
  }

  async function submitReview(card, decision) {
    const host = card.closest("[data-efp-card-host]");
    const review = host && host.__efpPayload ? host.__efpPayload : {};
    const ctx = host && host.__efpCtx ? host.__efpCtx : {};
    const status = card.querySelector("[data-efp-review-status]");
    const approved = [];
    const declined = [];
    card.querySelectorAll("[data-efp-review-item]").forEach((box) => {
      (box.checked ? approved : declined).push(box.dataset.efpReviewItem);
    });
    const notes = (card.querySelector("[data-efp-review-notes]")?.value || "").trim();
    if (decision === "changes" && !notes && !declined.length) {
      if (status) status.textContent = "Say what to change, or untick the items to rework.";
      return;
    }
    card.querySelectorAll("button[data-efp-review-decision]").forEach((button) => { button.disabled = true; });
    try {
      if (ctx.taskId) {
        const resp = await fetch(`/api/agent-tasks/${encodeURIComponent(ctx.taskId)}/review`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, kind: review.kind || "review", title: review.title || "", approved, declined, notes }),
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`);
        if (status) status.textContent = "Sent. The assistant continues the task.";
        const panel = card.closest("#task-detail-panel-root");
        if (panel && window.htmx && panel.parentElement) {
          window.htmx.ajax("GET", `/app/tasks/${encodeURIComponent(ctx.taskId)}/panel`, { target: panel.parentElement, swap: "innerHTML" });
        }
      } else if (sendChatText(reviewDecisionText(review, decision, approved, declined, notes))) {
        if (status) status.textContent = "Sent to the assistant.";
      } else {
        throw new Error("Open the assistant's chat to answer this review.");
      }
    } catch (error) {
      card.querySelectorAll("button[data-efp-review-decision]").forEach((button) => { button.disabled = false; });
      if (status) status.textContent = error.message || String(error);
    }
  }

  // ----- mounting -------------------------------------------------------------

  function renderCard(container, kind, payload, ctx) {
    container.__efpPayload = payload;
    container.__efpCtx = ctx;
    container.setAttribute("data-efp-card-host", kind);
    if (kind === "efp-review") {
      container.innerHTML = reviewHtml(payload, ctx);
      if (ctx.readonly) {
        // A decided review (or one this viewer cannot act on) is shown, not answered.
        container.querySelectorAll("input, textarea, button").forEach((el) => { el.disabled = true; });
        const actions = container.querySelector(".efp-card-actions");
        if (actions) actions.remove();
      }
      return Promise.resolve();
    }
    if (kind === "efp-evidence") return renderEvidence(container, payload, ctx);
    if (kind === "efp-matrix") return renderMatrix(container, payload, ctx);
    return Promise.resolve();
  }

  function chatAgentId() {
    return typeof window.currentPortalAgentId === "function" ? window.currentPortalAgentId() : "";
  }

  // Replaces a <pre><code class="language-efp-*"> block with its card. A body
  // that is not (yet) valid JSON, as while a reply is still streaming, stays a
  // code block.
  function buildFromCode(code, ctx) {
    if (!code || !code.classList) return false;
    const langClass = Array.from(code.classList).find((name) => name.startsWith("language-")) || "";
    const kind = langClass.slice("language-".length).toLowerCase();
    if (!isCardLanguage(kind)) return false;
    const payload = parsePayload(code.textContent);
    if (!payload) return false;
    const pre = code.parentElement;
    if (!pre || !pre.parentNode) return false;
    const container = document.createElement("div");
    container.className = "efp-card-host";
    pre.parentNode.replaceChild(container, pre);
    renderCard(container, kind, payload, Object.assign({ agentId: chatAgentId(), surface: "chat" }, ctx || {}));
    return true;
  }

  function hydrate(scope) {
    const root = scope && scope.querySelectorAll ? scope : document;
    root.querySelectorAll("[data-efp-card]:not([data-efp-ready])").forEach((el) => {
      el.setAttribute("data-efp-ready", "1");
      const kind = `efp-${String(el.dataset.efpCard || "").replace(/^efp-/, "")}`;
      if (!isCardLanguage(kind)) return;
      const payload = el.dataset.payload ? parsePayload(el.dataset.payload) : (el.dataset.path ? { path: el.dataset.path } : null);
      if (!payload) return;
      renderCard(el, kind, payload, {
        agentId: el.dataset.agentId || chatAgentId(),
        taskId: el.dataset.taskId || "",
        surface: el.dataset.surface || "page",
        quietMissing: el.dataset.quietMissing === "1",
        readonly: el.dataset.readonly === "1",
      });
    });
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const decision = target.closest("[data-efp-review-decision]");
    if (decision) {
      const card = decision.closest(".efp-review");
      if (card) submitReview(card, decision.dataset.efpReviewDecision);
      return;
    }
    const toggle = target.closest("[data-efp-evidence-toggle]");
    if (toggle) {
      const host = toggle.closest("[data-efp-card-host]");
      const row = host && host.querySelector(`[data-efp-evidence-row="${toggle.dataset.row}"]`);
      if (!row) return;
      row.classList.toggle("hidden");
      const slot = row.querySelector("[data-efp-evidence-slot]");
      if (!row.classList.contains("hidden") && slot && !slot.dataset.loaded) {
        slot.dataset.loaded = "1";
        renderEvidence(slot, { path: toggle.dataset.efpEvidenceToggle }, host.__efpCtx || { agentId: chatAgentId() });
      }
    }
  });

  document.addEventListener("htmx:afterSettle", (event) => hydrate(event.target));
  document.addEventListener("DOMContentLoaded", () => hydrate(document));

  window.EfpCards = {
    isCardLanguage,
    parsePayload,
    normalizePath,
    resolvePath,
    dirname,
    contentUrl,
    streamUrl,
    normalizeEvidence,
    evidenceCardHtml,
    matrixSummary,
    matrixHtml,
    reviewHtml,
    reviewDecisionText,
    buildFromCode,
    hydrate,
  };
})();
