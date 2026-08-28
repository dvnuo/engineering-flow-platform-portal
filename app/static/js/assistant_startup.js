/**
 * What an assistant is doing before it can answer.
 *
 * "creating" and a Kubernetes error string are accurate and useless. This
 * renders the same state as a phase with an expected duration while starting,
 * and as a cause plus a next step when it fails, so a member is never left
 * watching a spinner or reading CreateContainerConfigError.
 *
 * The reading itself is computed server-side (app/services/agent_startup_status)
 * and arrives on the status payload as `startup`; this only draws it.
 */
(function () {
  "use strict";

  const CARD_ID = "portal-startup-card";
  const POLL_MS = 3000;

  let activeAgentId = null;
  let timer = null;
  let startedAt = 0;

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

  function stopPolling() {
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function clearCard() {
    document.getElementById(CARD_ID)?.remove();
  }

  function elapsedLabel() {
    if (!startedAt) return "";
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    if (seconds < 60) return `${seconds}s so far`;
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s so far`;
  }

  function phasesMarkup(startup) {
    const phases = Array.isArray(startup.phases) ? startup.phases : [];
    if (!startup.is_starting || !phases.length) return "";
    const currentIndex = phases.findIndex((phase) => phase.key === startup.phase);
    return `<ul class="portal-startup-phases">${phases
      .map((phase, index) => {
        // "ready" is the destination, not a step to show as pending.
        if (phase.key === "ready") return "";
        let cls = "";
        if (currentIndex >= 0 && index < currentIndex) cls = " class=\"is-done\"";
        else if (index === currentIndex) cls = " class=\"is-active\"";
        return `<li${cls}>${esc(phase.label)}</li>`;
      })
      .join("")}</ul>`;
  }

  function actionMarkup(startup) {
    if (!startup.action) return "";
    return `<div class="portal-startup-actions">
      <button type="button" class="portal-btn is-primary" data-startup-action="${esc(startup.action)}">
        ${esc(startup.action_label || "Continue")}
      </button>
    </div>`;
  }

  function cardMarkup(startup) {
    const icon = startup.is_failed ? "triangle-alert" : startup.is_starting ? "loader" : "pause";
    const detail = startup.is_starting
      ? `${esc(startup.detail)} ${esc(elapsedLabel())}`.trim()
      : esc(startup.detail);
    return `
    <div class="portal-startup-progress${startup.is_failed ? " is-failed" : ""}">
      <div class="portal-startup-progress-head">
        <i data-lucide="${icon}" class="w-4 h-4"></i>
        <span>${esc(startup.headline)}</span>
      </div>
      ${detail ? `<p class="portal-startup-progress-note">${detail}</p>` : ""}
      ${phasesMarkup(startup)}
      ${actionMarkup(startup)}
      ${
        startup.technical_detail
          ? `<details class="portal-collapsible">
               <summary class="portal-collapsible-summary">Technical details</summary>
               <pre class="portal-panel-pre">${esc(startup.technical_detail)}</pre>
             </details>`
          : ""
      }
    </div>`;
  }

  function mountCard(startup) {
    const list = document.getElementById("message-list");
    if (!list) return;
    const existing = document.getElementById(CARD_ID);
    const html = cardMarkup(startup);
    if (existing) {
      existing.innerHTML = html;
      renderIcons();
      return;
    }
    const row = document.createElement("div");
    row.id = CARD_ID;
    row.className = "message-row message-row-assistant portal-interactive-row";
    row.innerHTML = html;
    list.prepend(row);
    renderIcons();
  }

  async function poll(agentId) {
    if (activeAgentId !== agentId) return;
    let payload = null;
    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(agentId)}/status`);
      if (!response.ok) throw new Error(String(response.status));
      payload = await response.json();
    } catch (error) {
      // Status is unavailable, not wrong. Leave whatever is on screen and try
      // again rather than replacing it with an error the member cannot act on.
      timer = window.setTimeout(() => poll(agentId), POLL_MS);
      return;
    }
    if (activeAgentId !== agentId) return;

    const startup = payload && payload.startup;
    if (!startup || (!startup.is_starting && !startup.is_failed && startup.phase !== "stopped")) {
      clearCard();
      stopPolling();
      return;
    }

    mountCard(startup);
    // A failed or paused assistant is a settled state; only an in-progress
    // start is worth polling for.
    if (startup.is_starting) {
      timer = window.setTimeout(() => poll(agentId), POLL_MS);
    } else {
      stopPolling();
    }
  }

  async function runAction(action, agentId) {
    if (action === "open_connections") {
      document.getElementById("runtime-profiles-menu-btn")?.click();
      return;
    }
    if (action === "contact_support") {
      document.getElementById("help-btn")?.click();
      return;
    }
    if (action !== "retry" || !agentId) return;
    try {
      await fetch(`/api/agents/${encodeURIComponent(agentId)}/start`, { method: "POST" });
      if (typeof window.showToast === "function") window.showToast("Starting the assistant again…");
      startedAt = Date.now();
      stopPolling();
      poll(agentId);
    } catch (error) {
      if (typeof window.showToast === "function") {
        window.showToast("Could not start the assistant.", { variant: "error" });
      }
    }
  }

  function bind() {
    document.addEventListener("portal:agent-selected", (browserEvent) => {
      stopPolling();
      clearCard();
      activeAgentId = browserEvent.detail?.agentId || null;
      startedAt = Date.now();
      if (activeAgentId) poll(activeAgentId);
    });

    document.getElementById("message-list")?.addEventListener("click", (browserEvent) => {
      const button = browserEvent.target.closest("[data-startup-action]");
      if (button) runAction(button.dataset.startupAction, activeAgentId);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
