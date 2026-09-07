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
 *
 * It draws into #assistant-status-banner, which sits under the main header and
 * outside both the home and chat views. The chat view is hidden whenever the
 * assistant is not running, so a card inside the transcript would only ever be
 * seen once there was nothing left to say.
 *
 * Two events tie this to chat_ui.js without either reaching into the other:
 *   - portal:agent-selected / portal:agent-lifecycle (from chat_ui) start a
 *     watch on that assistant;
 *   - portal:agent-status (from here) hands every status reading back so the
 *     sidebar, header badge and main view follow along.
 */
(function () {
  "use strict";

  const BANNER_ID = "assistant-status-banner";
  const CARD_ID = "portal-startup-card";
  const POLL_MS = 3000;

  let activeAgentId = null;
  let canWrite = false;
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

  // Start, Retry and Connections change the assistant; a member who can only
  // read it would get a 403 for their trouble.
  const WRITE_ACTIONS = new Set(["start", "retry", "open_connections"]);

  function actionMarkup(startup) {
    if (!startup.action) return "";
    if (WRITE_ACTIONS.has(startup.action) && !canWrite) return "";
    return `<div class="portal-startup-actions">
      <button type="button" class="portal-btn is-primary" data-startup-action="${esc(startup.action)}">
        ${esc(startup.action_label || "Continue")}
      </button>
    </div>`;
  }

  // Past this multiple of the typical start the wait is no longer "usual".
  // Kubernetes only declares the rollout failed after its own progress
  // deadline (minutes), and until then nothing here can restart a start in
  // progress, so this changes what the card says, not what it offers.
  const SLOW_START_FACTOR = 2.5;
  const SUPPORT_AFTER_SECONDS = 600;

  function elapsedSeconds() {
    return startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
  }

  // The server's reading, adjusted for how long this member has been waiting.
  function escalate(startup) {
    if (!startup.is_starting) return startup;
    const typical = Number(startup.typical_seconds) || 40;
    const elapsed = elapsedSeconds();
    if (elapsed < typical * SLOW_START_FACTOR) return startup;
    const view = {
      ...startup,
      is_slow: true,
      headline: "Startup is taking longer than usual",
      detail: "Still waiting for the runtime. The platform marks a start that never finishes as failed, and you can retry from there.",
    };
    if (elapsed >= SUPPORT_AFTER_SECONDS) {
      view.detail = "This has been going on for a while. Retrying later usually works; if it keeps happening, your administrator needs to look at it.";
      view.action_label = "Contact support";
      view.action = "contact_support";
    }
    return view;
  }

  function cardMarkup(rawStartup) {
    const startup = escalate(rawStartup);
    const icon = startup.is_failed ? "triangle-alert" : startup.is_starting ? "loader" : "pause";
    const detail = startup.is_starting
      ? `${esc(startup.detail)} ${esc(elapsedLabel())}`.trim()
      : esc(startup.detail);
    const tone = startup.is_failed ? " is-failed" : startup.is_slow ? " is-slow" : "";
    return `
    <div class="portal-startup-progress${tone}">
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
    const banner = document.getElementById(BANNER_ID);
    if (!banner) return;
    const existing = document.getElementById(CARD_ID);
    const html = cardMarkup(startup);
    if (existing) {
      existing.innerHTML = html;
      renderIcons();
      return;
    }
    const card = document.createElement("div");
    card.id = CARD_ID;
    card.className = "portal-startup-card";
    card.innerHTML = html;
    banner.replaceChildren(card);
    renderIcons();
  }

  function reportStatus(agentId, payload) {
    try {
      document.dispatchEvent(new CustomEvent("portal:agent-status", { detail: { agentId, payload } }));
    } catch (error) {
      /* the card is still correct even if nobody else is listening */
    }
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

    reportStatus(agentId, payload);

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

  function watch(agentId, { keepElapsed = false } = {}) {
    stopPolling();
    if (!keepElapsed) startedAt = Date.now();
    if (agentId) poll(agentId);
  }

  function runAction(action, agentId) {
    if (action === "open_connections") {
      document.getElementById("runtime-profiles-menu-btn")?.click();
      return;
    }
    if (action === "contact_support") {
      document.getElementById("help-btn")?.click();
      return;
    }
    if ((action !== "retry" && action !== "start") || !agentId) return;
    const button = document.querySelector(`#${CARD_ID} [data-startup-action]`);
    if (button) button.disabled = true;
    // chat_ui.js owns lifecycle actions (optimistic status, toasts, restart
    // polling, refresh); it answers with portal:agent-lifecycle, which
    // restarts the watch here.
    document.dispatchEvent(new CustomEvent("portal:agent-action", { detail: { agentId, action: "start" } }));
  }

  function switchTo(agentId) {
    if (agentId === activeAgentId) return;
    clearCard();
    activeAgentId = agentId;
    if (agentId) {
      watch(agentId);
    } else {
      stopPolling();
    }
  }

  function bind() {
    document.addEventListener("portal:agent-selected", (browserEvent) => {
      canWrite = browserEvent.detail?.canWrite === true;
      switchTo(browserEvent.detail?.agentId || null);
    });

    document.addEventListener("portal:agent-lifecycle", (browserEvent) => {
      const agentId = browserEvent.detail?.agentId || null;
      const lifecycleAction = browserEvent.detail?.action;
      // "sync": chat_ui settled on a selected assistant by some path other
      // than a click (initial load, a refresh). Same as a selection.
      if (lifecycleAction === "sync") {
        if (typeof browserEvent.detail?.canWrite === "boolean") canWrite = browserEvent.detail.canWrite;
        switchTo(agentId);
        return;
      }
      if (!agentId || agentId !== activeAgentId) return;
      // "refresh": the periodic poll saw this assistant change while the
      // watch here was settled. Look again, without restarting the clock.
      if (lifecycleAction === "refresh") {
        watch(agentId, { keepElapsed: true });
        return;
      }
      // Start / Stop / Restart from the details panel or the health card. The
      // watch already running (if any) would only notice on its next tick, and
      // for a settled assistant there is no watch running at all.
      watch(agentId);
    });

    document.getElementById(BANNER_ID)?.addEventListener("click", (browserEvent) => {
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
