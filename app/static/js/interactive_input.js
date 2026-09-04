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
  // A prefetched answer is only good for the paint it was fetched for; past
  // that, ask again rather than show something the run has moved on from.
  const PREFETCH_MAX_AGE_MS = 5000;
  const state = {
    pending: null,
    kind: null,
    // Which conversation the pending request came from. A card belongs to one
    // session; without this it would follow the reader into the next.
    session: "",
    submitting: false,
    checking: false,
    recheckTimer: 0,
    draft: null,
    prefetched: null,
    // Request ids this client has already submitted an answer or decision for.
    // The socket reconnect that follows a submission asks for replay, and the
    // request it is replaying can be the very one just answered -- the server
    // event stream has no notion of "answered", only "already sent". Without
    // this, that redelivery remounted a card for a question already put away.
    resolved: new Set(),
    // Set when this client answers, cleared by the first run-end that follows.
    awaitingResumedRun: false,
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

  function announcePendingChange() {
    try {
      document.dispatchEvent(new CustomEvent("portal:pending-input-changed"));
    } catch (error) {
      /* the card is up either way */
    }
  }

  /**
   * Every identity a request answers to.
   *
   * The request id is derived from what is being asked, so it is stable for as
   * long as the question is -- but only on a runtime carrying that fix. The
   * tool call id is stable regardless: a replay of an unanswered question is a
   * replay of the same pending call, whatever id the run mints for it. Matching
   * on either means a redelivered question is recognised in both cases.
   */
  function requestIdentities(request) {
    if (!request || typeof request !== "object") return [];
    const metadata = request.metadata && typeof request.metadata === "object" ? request.metadata : {};
    const id = questionRequestId(request);
    const call = String(request.tool_call_id || metadata.tool_call_id || "");
    const identities = [];
    if (id) identities.push(`id:${id}`);
    if (call) identities.push(`call:${call}`);
    return identities;
  }

  /**
   * Ask the transcript to catch up once, after a run this client unblocked.
   *
   * Answering starts a run Portal joins rather than sends, and that join can
   * end without the transcript learning anything -- refused before it starts,
   * or accepted and then silent. The run happened either way, so the member is
   * left looking at the state from before their answer until they reload.
   *
   * Only after an answer, only once per answer, and only when nothing is
   * streaming, so a working turn is never interrupted by it.
   */
  function reconcileAfterAnsweredRun() {
    if (!state.awaitingResumedRun) return;
    state.awaitingResumedRun = false;
    if (typeof window.reconcilePortalTranscript !== "function") return;
    const agent = agentId();
    const session = sessionId();
    if (!agent || !session) return;
    try {
      Promise.resolve(window.reconcilePortalTranscript(agent, session)).catch(() => {});
    } catch (error) {
      /* the answer is already sent; catching up is a courtesy */
    }
  }

  /** Never show this request again, however it comes back around. */
  function markResolved(request) {
    requestIdentities(request).forEach((identity) => state.resolved.add(identity));
  }

  function wasResolved(request) {
    return requestIdentities(request).some((identity) => state.resolved.has(identity));
  }

  function clearCard() {
    document.getElementById(CARD_ID)?.remove();
    state.pending = null;
    state.kind = null;
    // The form is gone, so nothing is being submitted through it. Only the
    // failure path used to put this back, which left it stuck true for the
    // life of the page after a successful answer -- and with it the recovery
    // check that is the only thing able to clear a card shown in error.
    state.submitting = false;
    state.session = "";
    state.draft = null;
    announcePendingChange();
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

    announcePendingChange();

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

  function showQuestion(request, forSessionId) {
    if (wasResolved(request)) return;
    if (alreadyShowing(request, "question")) return;
    const markup = questionCardMarkup(request);
    if (!markup) return;
    state.pending = request;
    state.kind = "question";
    state.session = forSessionId || sessionId();
    mountCard(markup);
    restoreAnswers();
  }

  function showPermission(request, forSessionId) {
    if (wasResolved(request)) return;
    if (alreadyShowing(request, "permission")) return;
    state.pending = request;
    state.kind = "permission";
    state.session = forSessionId || sessionId();
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

  /**
   * Follow the run the answer just started.
   *
   * Both respond endpoints reply 202 with the request id of the resumed run.
   * Dropping it left the card gone, a toast saying "Continuing...", and a
   * conversation that did not move again until the page was reloaded -- by
   * which point the work had already happened.
   */
  function followResumedRun(result) {
    state.awaitingResumedRun = true;
    const requestId = result && (result.request_id || result.requestId);
    const agent = agentId();
    const session = sessionId();
    if (!requestId || !agent || !session) return;
    if (typeof window.adoptPortalResumedChatRun !== "function") return;
    try {
      Promise.resolve(window.adoptPortalResumedChatRun(agent, session, String(requestId))).catch(() => {});
    } catch (error) {
      /* the answer is already sent; following it is a courtesy */
    }
  }

  /** Pair each answer with the question it answers, and hand it to the transcript. */
  function showAnswerInTranscript(questions, answers) {
    if (typeof window.renderPortalAnswerNow !== "function") return;
    const pairs = (answers || [])
      .map((answer, index) => {
        const value = String(answer ?? "").trim();
        if (!value) return null;
        const question = questions[index] || {};
        // The question, not its header: "PROJECT" does not tell anyone what
        // they were asked, and this row is the only record left once the card
        // is gone.
        return { label: String(question.question || question.header || "").trim(), value };
      })
      .filter(Boolean);
    if (pairs.length) window.renderPortalAnswerNow(pairs);
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

  async function submitQuestion(form, override = null) {
    const agent = agentId();
    const session = sessionId();
    if (!agent || !session) return setMessage(form, "error", "No active conversation to answer.");

    // The composer supplies its own answer: it is one line for a card that may
    // have several fields, or none it could have typed into.
    const collected = override || collectAnswers(form);
    if (collected.error) return setMessage(form, "error", collected.error);
    const questions = normalizeQuestions(state.pending);

    setBusy(form, true);
    setMessage(form, "", "Sending…");
    try {
      const result = await requestJson(
        `/a/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(session)}/question/respond`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: form.dataset.requestId, answers: collected.answers }),
        }
      );
      // Show it before clearing, or the member watches their reply vanish and
      // waits a whole resumed run for it to come back from history.
      showAnswerInTranscript(questions, collected.answers);
      markResolved(state.pending);
      clearCard();
      followResumedRun(result);
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
      const result = await requestJson(
        `/a/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(session)}/permission/respond`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: form.dataset.requestId, decision, always }),
        }
      );
      markResolved(state.pending);
      clearCard();
      followResumedRun(result);
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
  function applyPendingInput(payload) {
    if (payload && payload.question_request) {
      showQuestion(payload.question_request);
    } else if (payload && payload.permission_request) {
      showPermission(payload.permission_request);
    } else {
      clearCard();
    }
  }

  /**
   * Ask the runtime what this session is blocked on, and keep the answer.
   *
   * The transcript is drawn from several fetches. If this one only started when
   * the others had finished, the card would land a round trip after the
   * conversation it belongs to -- which on a throttled connection is a visible
   * second step. Prefetching lets the caller run it alongside the rest and
   * apply the result in the same frame as the history.
   */
  async function fetchPendingInput(forAgentId, forSessionId) {
    const agent = forAgentId || agentId();
    const session = forSessionId || sessionId();
    if (!agent || !session) return null;
    try {
      const payload = await requestJson(
        `/a/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(session)}/pending-input`
      );
      state.prefetched = { session, payload, at: Date.now() };
      return payload;
    } catch (error) {
      // The session may not exist yet; nothing to show either way.
      return null;
    }
  }

  function takeFreshPrefetch() {
    const held = state.prefetched;
    state.prefetched = null;
    if (!held || held.session !== sessionId()) return null;
    return Date.now() - held.at <= PREFETCH_MAX_AGE_MS ? held : null;
  }

  // The load names the session it is prefetching for; by the time the answer is
  // applied, that session is the one on screen.


  async function checkPendingInput() {
    if (state.checking || state.submitting) return;
    const agent = agentId();
    const session = sessionId();
    if (!agent || !session) return;

    const held = takeFreshPrefetch();
    if (held) {
      applyPendingInput(held.payload);
      return;
    }

    state.checking = true;
    try {
      applyPendingInput(await fetchPendingInput());
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
    if (state.session && state.session !== sessionId()) {
      // The reader has moved to another conversation; this question is not
      // theirs to answer here.
      clearCard();
      return;
    }
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

  // Fetched ahead of the paint, so the card is mounted in the same frame as
  // the transcript instead of appearing a round trip later.
  window.portalPrefetchPendingInput = (agent, session) => fetchPendingInput(agent, session);

  /**
   * What the composer should do while this card is up.
   *
   * A run stops at a question until the tool call it came from is resolved.
   * Sending an ordinary message does not resolve it: the next run replays the
   * pending call, asks again, and stops -- so the message reaches the
   * transcript, never reaches the model, and the question comes back. There was
   * nothing on screen to say so.
   *
   * A single question that accepts free text can be answered from the composer,
   * which is the same thing its own text box does. Anything else has to go
   * through the card, because a typed line cannot say which of several
   * questions it answers, or approve a tool.
   */
  window.portalPendingComposerIntent = () => {
    if (!state.pending || !document.getElementById(CARD_ID)) return null;
    // An approval is the one thing a typed line cannot be. `permission/respond`
    // wants approve or deny, and reading either out of prose is a guess nobody
    // should be making on the member's behalf.
    if (state.kind === "permission") {
      return { acceptsText: false, reason: "Approve or reject the tool above to continue." };
    }
    // Every question can be answered in words. The runtime takes a shorter
    // answers array than there are questions and reports the rest as
    // unanswered, and it does not enforce `custom: false` -- that is how the
    // card chooses to render, not a rule about what the member may say.
    const questions = normalizeQuestions(state.pending);
    // Named, because typing into the composer puts the card away -- and an
    // answer box with the question out of sight is a guessing game.
    const asked = questions.length ? (questions[0].question || questions[0].header) : "";
    if (questions.length > 1) {
      return { acceptsText: true, reason: "", asked, note: `Answering “${asked}”. The other ${questions.length - 1} stay open.` };
    }
    if (questions.length === 1 && !questions[0].custom) {
      return { acceptsText: true, reason: "", asked, note: `Answering “${asked}” in your own words.` };
    }
    return { acceptsText: true, reason: "", asked, note: `Answering “${asked}”.` };
  };

  /**
   * Whether this session is blocked on the member, card mounted or not.
   *
   * `portalPendingComposerIntent` needs the card in the DOM. The run that
   * asked ends in the same burst of events that raises it, so at the moment
   * the transcript decides what that run was, the card may not be up yet --
   * and a parked run judged on that instant reads as one that ended badly.
   * `state.pending` is set as soon as the request arrives, which is early
   * enough.
   */
  window.portalHasPendingInput = () => Boolean(state.pending);

  /** Put the card away while the composer has the floor, without losing it. */
  window.portalCollapsePendingCard = (collapsed) => {
    document.getElementById(CARD_ID)?.classList.toggle("is-collapsed", Boolean(collapsed));
  };

  /** Answer the pending question with what the member typed. */
  window.portalAnswerPendingWithText = async (text) => {
    const form = document.querySelector(`#${CARD_ID} [data-interactive-kind="question"]`);
    if (!form) return false;
    await submitQuestion(form, { answers: [String(text || "")] });
    return true;
  };

  // -------------------------------------------------------------- listeners

  function eventSessionId(detail) {
    const event = detail && detail.event ? detail.event : {};
    const data = event.data && typeof event.data === "object" ? event.data : {};
    return String(event.session_id || event.sessionId || data.session_id || detail?.sessionId || "");
  }

  /**
   * Whether this event describes the conversation currently on screen.
   *
   * An event that names no session is taken at face value: some runtime events
   * carry only a request id, and dropping those would lose live updates.
   */
  function eventBelongsToOpenConversation(detail) {
    const open = sessionId();
    const from = eventSessionId(detail);
    if (!open || !from) return true;
    return open === from;
  }

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
      // The socket asks for `replay=1`, so a question left unanswered in one
      // conversation is redelivered on the next connect. Without this check it
      // was mounted into whatever the reader had moved on to -- starting a new
      // chat brought the old question straight back.
      if (!eventBelongsToOpenConversation(detail)) return;

      if (type === "question.requested" || type === "tool.question_requested") {
        const request = extractRequest(detail, ["question_request", "questionRequest"]);
        if (request) showQuestion(request, eventSessionId(detail));
        return;
      }
      if (type === "permission.requested" || type === "permission_request" || type === "tool.permission_requested") {
        const request = extractRequest(detail, ["permission_request", "permissionRequest"]);
        if (request) showPermission(request, eventSessionId(detail));
        return;
      }
      // A resolution names the thing it resolved, so it is safe to act on
      // directly and worth doing without a round trip.
      if (
        type === "permission.resolved"
        || type === "permission.allowed"
        || type === "permission.denied"
      ) {
        markResolved(state.pending);
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
        reconcileAfterAnsweredRun();
      }
    });

    // A welcome means an empty conversation, and an empty conversation cannot
    // be blocked on anything. New chat clears the list without going near this
    // module, which left the card's state behind -- and with it a switch bar
    // pointing at a card that was no longer on screen.
    document.addEventListener("portal:welcome-rendered", () => clearCard());

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
