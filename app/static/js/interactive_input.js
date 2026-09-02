/**
 * Interactive input: the answer surface for questions and permission requests
 * the assistant raises mid-run.
 *
 * The runtime has always been able to pause and ask -- it ships a `question`
 * builtin tool, holds the pending request in session metadata, and resumes once
 * an answer arrives. Portal received the event and drew a passive "Question
 * requested" chip with no way to reply, so the run simply stalled. This renders
 * a real card and posts the answer back.
 *
 * Pending state is read from the session rather than the live event stream, so
 * a card survives a page refresh or a dropped socket instead of stranding the
 * run.
 */
(function () {
  "use strict";

  const CARD_ID = "portal-interactive-input";
  // Long enough for the run to have finished writing its pending request to
  // session metadata, short enough that a genuinely finished run does not leave
  // a dead card on screen.
  const RECHECK_DELAY_MS = 250;
  const state = {
    pending: null,
    kind: null,
    submitting: false,
    checking: false,
    recheckTimer: 0,
    draft: null,
  };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function agentId() {
    return typeof window.currentPortalAgentId === "function" ? window.currentPortalAgentId() : null;
  }

  function sessionId() {
    return typeof window.currentPortalSessionId === "function" ? window.currentPortalSessionId() : null;
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

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = null;
      }
    }
    if (!response.ok) {
      const detail = payload && (payload.error || payload.detail || payload.message);
      throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
    }
    return payload;
  }

  // ------------------------------------------------------------- rendering

  function questionRequestId(request) {
    return request && (request.request_id || request.id) ? String(request.request_id || request.id) : "";
  }

  function normalizeQuestions(request) {
    const raw = request && Array.isArray(request.questions) ? request.questions : [];
    return raw
      .map((item) => ({
        question: String(item && item.question ? item.question : "").trim(),
        header: String(item && item.header ? item.header : "").trim(),
        options: Array.isArray(item && item.options) ? item.options : [],
        // The runtime defaults `custom` to true; only an explicit false closes
        // off free text, so a malformed entry stays answerable.
        custom: !(item && item.custom === false),
      }))
      .filter((item) => item.question);
  }

  function hasLabel(option) {
    return Boolean(String(option && option.label ? option.label : "").trim());
  }

  function optionMarkup(question, questionIndex) {
    const options = question.options
      .map((option, optionIndex) => {
        const label = String(option && option.label ? option.label : "").trim();
        if (!label) return "";
        const description = String(option && option.description ? option.description : "").trim();
        const id = `pii-q${questionIndex}-o${optionIndex}`;
        return `
        <label class="portal-question-option" for="${id}">
          <input type="radio" id="${id}" name="q${questionIndex}" value="${esc(label)}" />
          <span class="portal-question-option-body">
            <strong>${esc(label)}</strong>
            ${description ? `<small>${esc(description)}</small>` : ""}
          </span>
        </label>`;
      })
      .join("");
    if (!options) return "";
    return `<div class="portal-question-options" role="radiogroup" aria-label="${esc(question.question)}">${options}</div>`;
  }

  function questionMarkup(question, index) {
    const options = optionMarkup(question, index);
    // One option is not a choice. Models reach for it to label a free-text
    // answer -- "Provide ticket details" with the real instructions in the
    // description -- and a radio group of one turns that into a control that
    // does nothing while the actual answer hides behind "Something else...".
    // Fewer than two options means the box is the answer, so show it.
    const customAlways = !options || question.options.filter(hasLabel).length < 2;
    return `
    <fieldset class="portal-question-item" data-question-index="${index}">
      ${question.header ? `<legend class="portal-question-header">${esc(question.header)}</legend>` : ""}
      <p class="portal-question-text">${esc(question.question)}</p>
      ${options}
      ${
        question.custom
          ? `
      <div class="portal-question-custom${customAlways ? "" : " hidden"}" data-question-custom>
        <input type="text" class="portal-form-input" data-question-custom-input
               placeholder="Type your answer" ${customAlways ? "" : "disabled"} />
      </div>
      ${
        customAlways
          ? ""
          : `<button type="button" class="portal-question-other-btn" data-question-other-toggle>Something else…</button>`
      }`
          : ""
      }
    </fieldset>`;
  }

  function questionCardMarkup(request) {
    const questions = normalizeQuestions(request);
    if (!questions.length) return "";
    return `
    <form class="portal-interactive-card" data-interactive-kind="question"
          data-request-id="${esc(questionRequestId(request))}">
      <div class="portal-interactive-head">
        <i data-lucide="message-circle-question" class="w-4 h-4"></i>
        <span>${questions.length > 1 ? "The assistant needs a few details" : "The assistant needs a detail"}</span>
      </div>
      <div class="portal-interactive-body">
        ${questions.map(questionMarkup).join("")}
      </div>
      <div class="portal-interactive-actions">
        <span class="portal-interactive-hint">Your answer continues the run.</span>
        <button type="submit" class="portal-btn is-primary" data-interactive-submit>Send answer</button>
      </div>
      <p class="portal-interactive-msg" data-interactive-msg></p>
    </form>`;
  }

  function permissionCardMarkup(request) {
    const toolName = request && (request.tool_id || request.tool_name || request.tool);
    const args = request && request.args;
    let preview = "";
    if (args !== undefined && args !== null) {
      try {
        preview = typeof args === "string" ? args : JSON.stringify(args, null, 2);
      } catch (error) {
        preview = String(args);
      }
    }
    return `
    <form class="portal-interactive-card is-permission" data-interactive-kind="permission"
          data-request-id="${esc(questionRequestId(request))}">
      <div class="portal-interactive-head">
        <i data-lucide="shield-question" class="w-4 h-4"></i>
        <span>Approval needed</span>
      </div>
      <div class="portal-interactive-body">
        <p class="portal-question-text">
          The assistant wants to run <strong>${esc(toolName || "a tool")}</strong>.
        </p>
        ${preview ? `<pre class="portal-panel-pre portal-interactive-preview">${esc(preview.slice(0, 2000))}</pre>` : ""}
        <label class="portal-checkbox-row">
          <input type="checkbox" data-permission-always />
          <span>Don't ask again for this tool in this assistant</span>
        </label>
      </div>
      <div class="portal-interactive-actions">
        <button type="button" class="portal-btn" data-permission-decision="deny">Reject</button>
        <button type="button" class="portal-btn is-primary" data-permission-decision="approve">Approve</button>
      </div>
      <p class="portal-interactive-msg" data-interactive-msg></p>
    </form>`;
  }

  function clearCard() {
    document.getElementById(CARD_ID)?.remove();
    state.pending = null;
    state.kind = null;
    state.draft = null;
  }

  function mountCard(html) {
    const list = document.getElementById("message-list");
    if (!list || !html) return;
    document.getElementById(CARD_ID)?.remove();

    const row = document.createElement("div");
    row.id = CARD_ID;
    row.className = "message-row message-row-assistant portal-interactive-row";
    row.innerHTML = html;
    list.append(row);
    renderIcons();

    const scroll = document.getElementById("message-scroll");
    if (scroll) {
      scroll.scrollTop = scroll.scrollHeight;
      // A card with several questions is taller than the space above the
      // composer, so landing at the very bottom puts the question itself off
      // the top of the screen and leaves the member looking at buttons with
      // nothing to read. Give back whatever the jump cut off.
      const cutOff = scroll.getBoundingClientRect().top - row.getBoundingClientRect().top;
      if (cutOff > 0) scroll.scrollTop -= cutOff;
    }

    const firstInput = row.querySelector("input");
    if (firstInput) window.setTimeout(() => firstInput.focus(), 40);
  }

  /**
   * True when this exact request is already on screen.
   *
   * Re-mounting rebuilds the form, which throws away a half-typed answer and
   * blinks the card. The recovery check runs on a timer and on every rebuild,
   * so it asks this often.
   */
  function alreadyShowing(request, kind) {
    if (state.kind !== kind) return false;
    const card = document.getElementById(CARD_ID);
    if (!card) return false;
    const shown = card.querySelector("[data-request-id]")?.dataset.requestId || "";
    const incoming = questionRequestId(request);
    return Boolean(incoming) && shown === incoming;
  }

  /**
   * Remember what has been answered so far, so a rebuild cannot eat it.
   *
   * Reconnecting rebuilds the transcript, which destroys the form -- and it
   * tends to happen exactly while somebody is typing the answer that would
   * unblock the run.
   */
  function snapshotAnswers() {
    const card = document.getElementById(CARD_ID);
    if (!card || state.kind !== "question") return;
    const draft = {};
    card.querySelectorAll("[data-question-index]").forEach((fieldset) => {
      const index = fieldset.dataset.questionIndex;
      const typed = fieldset.querySelector("[data-question-custom-input]");
      const picked = fieldset.querySelector('input[type="radio"]:checked');
      draft[index] = {
        custom: typed && !typed.disabled ? String(typed.value || "") : "",
        option: picked ? picked.value : "",
      };
    });
    state.draft = { requestId: questionRequestId(state.pending), answers: draft };
  }

  function restoreAnswers() {
    const card = document.getElementById(CARD_ID);
    const draft = state.draft;
    if (!card || !draft || draft.requestId !== questionRequestId(state.pending)) return;
    card.querySelectorAll("[data-question-index]").forEach((fieldset) => {
      const saved = draft.answers[fieldset.dataset.questionIndex];
      if (!saved) return;
      if (saved.option) {
        const radio = fieldset.querySelector(`input[type="radio"][value="${CSS.escape(saved.option)}"]`);
        if (radio) radio.checked = true;
      }
      if (!saved.custom) return;
      const wrap = fieldset.querySelector("[data-question-custom]");
      const typed = fieldset.querySelector("[data-question-custom-input]");
      if (!typed) return;
      // A typed answer means the free-text box had been opened.
      wrap?.classList.remove("hidden");
      fieldset.querySelector("[data-question-other-toggle]")?.remove();
      typed.disabled = false;
      typed.value = saved.custom;
    });
  }

  function showQuestion(request) {
    if (alreadyShowing(request, "question")) return;
    const markup = questionCardMarkup(request);
    if (!markup) return;
    state.pending = request;
    state.kind = "question";
    mountCard(markup);
    restoreAnswers();
  }

  function showPermission(request) {
    if (alreadyShowing(request, "permission")) return;
    state.pending = request;
    state.kind = "permission";
    mountCard(permissionCardMarkup(request));
  }

  // ------------------------------------------------------------ submission

  function collectAnswers(form) {
    const answers = [];
    const fieldsets = form.querySelectorAll("[data-question-index]");
    for (const fieldset of fieldsets) {
      const custom = fieldset.querySelector("[data-question-custom-input]");
      const customValue = custom && !custom.disabled ? String(custom.value || "").trim() : "";
      if (customValue) {
        answers.push(customValue);
        continue;
      }
      const checked = fieldset.querySelector('input[type="radio"]:checked');
      if (checked) {
        answers.push(checked.value);
        continue;
      }
      return { error: "Answer every question before sending." };
    }
    return { answers };
  }

  function setMessage(form, variant, text) {
    const target = form.querySelector("[data-interactive-msg]");
    if (!target) return;
    target.textContent = text || "";
    target.classList.remove("is-error", "is-success");
    if (variant) target.classList.add(`is-${variant}`);
  }

  function setBusy(form, busy) {
    state.submitting = busy;
    form.querySelectorAll("button").forEach((button) => {
      button.disabled = busy;
    });
  }

  async function submitQuestion(form) {
    const agent = agentId();
    const session = sessionId();
    if (!agent || !session) return setMessage(form, "error", "No active conversation to answer.");

    const collected = collectAnswers(form);
    if (collected.error) return setMessage(form, "error", collected.error);

    setBusy(form, true);
    setMessage(form, "", "Sending…");
    try {
      await requestJson(
        `/a/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(session)}/question/respond`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: form.dataset.requestId, answers: collected.answers }),
        }
      );
      clearCard();
      if (typeof window.showToast === "function") window.showToast("Answer sent. Continuing…");
    } catch (error) {
      setBusy(form, false);
      setMessage(form, "error", error.message);
    }
  }

  async function submitPermission(form, decision) {
    const agent = agentId();
    const session = sessionId();
    if (!agent || !session) return setMessage(form, "error", "No active conversation to answer.");

    const always = Boolean(form.querySelector("[data-permission-always]")?.checked);
    setBusy(form, true);
    setMessage(form, "", decision === "approve" ? "Approving…" : "Rejecting…");
    try {
      await requestJson(
        `/a/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(session)}/permission/respond`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: form.dataset.requestId, decision, always }),
        }
      );
      clearCard();
      if (typeof window.showToast === "function") {
        window.showToast(decision === "approve" ? "Approved. Continuing…" : "Rejected.");
      }
    } catch (error) {
      setBusy(form, false);
      setMessage(form, "error", error.message);
    }
  }

  // -------------------------------------------------------------- recovery

  /**
   * Ask the runtime what, if anything, the session is blocked on.
   *
   * This is what makes the card survive a refresh: the pending request lives in
   * session metadata, so it can be recovered long after the event that
   * announced it has gone.
   */
  async function checkPendingInput() {
    if (state.checking || state.submitting) return;
    const agent = agentId();
    const session = sessionId();
    if (!agent || !session) return;

    state.checking = true;
    try {
      const payload = await requestJson(
        `/a/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(session)}/pending-input`
      );
      if (payload && payload.question_request) {
        showQuestion(payload.question_request);
      } else if (payload && payload.permission_request) {
        showPermission(payload.permission_request);
      } else {
        clearCard();
      }
    } catch (error) {
      /* the session may not exist yet; nothing to show either way */
    } finally {
      state.checking = false;
    }
  }

  /**
   * Redraw the card the list was just rebuilt without, from what we already
   * know. The runtime is still asked afterwards; this only removes the gap.
   */
  function remountFromMemory() {
    if (state.submitting || !state.pending) return;
    if (document.getElementById(CARD_ID)) return;
    if (state.kind === "question") {
      showQuestion(state.pending);
    } else if (state.kind === "permission") {
      showPermission(state.pending);
    }
  }

  /**
   * Ask the runtime what is pending, once, shortly after things settle.
   *
   * Deferred rather than immediate because the run writes its pending request
   * into session metadata around the same moment it reports being finished;
   * asking too early can read the state from before the question landed and
   * clear a card that should stay. Coalesced because these events arrive in
   * bursts and the answer only needs to be right at the end of one.
   */
  function scheduleRecheck() {
    if (state.recheckTimer) window.clearTimeout(state.recheckTimer);
    state.recheckTimer = window.setTimeout(() => {
      state.recheckTimer = 0;
      checkPendingInput();
    }, RECHECK_DELAY_MS);
  }

  // -------------------------------------------------------------- listeners

  function extractRequest(detail, keys) {
    const data = detail && detail.event ? detail.event.data || detail.event : {};
    for (const key of keys) {
      const value = data && data[key];
      if (value && typeof value === "object") return value;
    }
    return null;
  }

  function bind() {
    document.addEventListener("portal:runtime-event", (browserEvent) => {
      const detail = browserEvent.detail || {};
      const type = String(detail.event?.type || "");

      if (type === "question.requested" || type === "tool.question_requested") {
        const request = extractRequest(detail, ["question_request", "questionRequest"]);
        if (request) showQuestion(request);
        return;
      }
      if (type === "permission.requested" || type === "permission_request" || type === "tool.permission_requested") {
        const request = extractRequest(detail, ["permission_request", "permissionRequest"]);
        if (request) showPermission(request);
        return;
      }
      // A resolution names the thing it resolved, so it is safe to act on
      // directly and worth doing without a round trip.
      if (
        type === "permission.resolved"
        || type === "permission.allowed"
        || type === "permission.denied"
      ) {
        clearCard();
        return;
      }
      // A run that stops to ask a question *also* completes: the loop returns
      // with the question pending and the request finishes, so the very event
      // that announces the end arrives moments after the card that the end was
      // caused by. Tearing the card down here removed the only way to unblock
      // the run. Ask the runtime what is actually outstanding instead.
      if (type === "chat.completed" || type === "chat.failed") {
        scheduleRecheck();
      }
    });

    document.addEventListener("portal:agent-selected", () => {
      clearCard();
      // Give session state a moment to settle before asking about it.
      window.setTimeout(checkPendingInput, 400);
    });

    // Loading a session rebuilds the whole message list, which takes the card
    // with it. Put back what was already on screen straight away and confirm
    // with the runtime after: clearing and waiting for the round trip made the
    // card blink out and back on every render, and a reconnecting session
    // renders several times in a row.
    document.addEventListener("portal:history-rendered", () => {
      remountFromMemory();
      scheduleRecheck();
    });

    const list = document.getElementById("message-list");
    if (!list) return;

    list.addEventListener("submit", (browserEvent) => {
      const form = browserEvent.target.closest('[data-interactive-kind="question"]');
      if (!form) return;
      browserEvent.preventDefault();
      submitQuestion(form);
    });

    list.addEventListener("click", (browserEvent) => {
      const otherButton = browserEvent.target.closest("[data-question-other-toggle]");
      if (otherButton) {
        const fieldset = otherButton.closest("[data-question-index]");
        const wrap = fieldset?.querySelector("[data-question-custom]");
        const input = fieldset?.querySelector("[data-question-custom-input]");
        wrap?.classList.remove("hidden");
        otherButton.remove();
        if (input) {
          input.disabled = false;
          input.focus();
        }
        // A typed answer wins over a selected option, so clear the radios to
        // keep the card honest about what will be sent.
        fieldset?.querySelectorAll('input[type="radio"]').forEach((radio) => {
          radio.checked = false;
        });
        return;
      }

      const decisionButton = browserEvent.target.closest("[data-permission-decision]");
      if (decisionButton) {
        const form = decisionButton.closest('[data-interactive-kind="permission"]');
        if (form) submitPermission(form, decisionButton.dataset.permissionDecision);
      }
    });

    list.addEventListener("input", (browserEvent) => {
      if (browserEvent.target.closest(`#${CARD_ID}`)) snapshotAnswers();
    });

    // Selecting an option should retire a half-typed free-text answer.
    list.addEventListener("change", (browserEvent) => {
      const radio = browserEvent.target.closest('.portal-question-options input[type="radio"]');
      if (!radio) return;
      const fieldset = radio.closest("[data-question-index]");
      const input = fieldset?.querySelector("[data-question-custom-input]");
      if (input && !input.disabled) input.value = "";
      snapshotAnswers();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
