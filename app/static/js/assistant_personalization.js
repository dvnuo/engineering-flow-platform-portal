/**
 * Per-assistant greeting and starter cards.
 *
 * Content comes from the behavior-pack branch the assistant actually booted
 * with, served by the runtime at /api/personalization. Portal does not clone
 * that repository itself, so what a member sees can never drift from what the
 * assistant is running.
 *
 * Clicking a card sends. The card names what it does, a card that needs a value
 * asks for it in a dialog the member confirms, and the composed prompt lands in
 * the transcript where it can be read and edited -- so a second trip to the
 * Send button only added a step between deciding and starting.
 *
 * The dialog is a picker when card_picker.js is on the page: a card names a
 * source (input.source) and the member picks from their own recent work
 * instead of typing, or the card lists several fields and gets a form. Without
 * that script, or for a source this Portal does not have, it is the one-line
 * prompt it always was.
 */
(function () {
  "use strict";

  const cache = new Map();
  let activeAgentId = null;

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

  async function loadPersonalization(agentId) {
    if (cache.has(agentId)) return cache.get(agentId);
    let payload = { welcome: null, cards: [] };
    // Only a definite answer is remembered. The proxy answers 409 for an
    // assistant that is paused or still starting, and caching that as "no
    // personalization" kept the generic greeting for the life of the page --
    // including after the assistant had been woken and could have answered.
    let settled = false;
    try {
      // v=2 asks for cards with several fields too; a runtime that predates
      // them ignores the parameter and answers with the cards it knows.
      const response = await fetch(`/a/${encodeURIComponent(agentId)}/api/personalization?v=2`);
      if (response.ok) {
        const parsed = await response.json();
        if (parsed && typeof parsed === "object") {
          payload = {
            welcome: typeof parsed.welcome === "string" ? parsed.welcome : null,
            cards: Array.isArray(parsed.cards) ? parsed.cards : [],
          };
        }
        settled = true;
      } else if (response.status === 404) {
        // A runtime without the endpoint has nothing to offer, now or later.
        settled = true;
      }
    } catch (error) {
      // Network trouble. Keep the generic welcome rather than showing an
      // error where a greeting belongs, and ask again on the next rebuild.
    }
    if (settled) cache.set(agentId, payload);
    return payload;
  }

  function cardsMarkup(cards) {
    return cards
      .map(
        (card, index) => `
      <button type="button" class="portal-starter-card" data-starter-card="${index}">
        <span class="portal-starter-card-icon"><i data-lucide="${esc(card.icon || "sparkles")}" class="w-4 h-4"></i></span>
        <span class="portal-starter-card-copy">
          <strong>${esc(card.title)}</strong>
          ${card.description ? `<small>${esc(card.description)}</small>` : ""}
        </span>
      </button>`
      )
      .join("");
  }

  function applyToWelcome(payload) {
    const list = document.getElementById("message-list");
    const welcome = list ? list.querySelector('[data-welcome="1"]') : null;
    if (!welcome) return;

    if (payload.welcome) {
      const markdown = welcome.querySelector("[data-md]");
      if (markdown) {
        markdown.setAttribute("data-md", payload.welcome);
        // Re-run the shared markdown pass so the greeting renders the same way
        // an assistant message would.
        if (typeof window.renderPortalMarkdown === "function") {
          window.renderPortalMarkdown(welcome);
        } else {
          markdown.textContent = payload.welcome;
        }
      }
    }

    welcome.querySelector(".portal-starter-cards")?.remove();
    if (!payload.cards.length) return;

    const container = document.createElement("div");
    container.className = "portal-starter-cards";
    container.innerHTML = cardsMarkup(payload.cards);
    welcome.querySelector(".message-surface")?.append(container);
    renderIcons();
  }

  function composer() {
    return document.getElementById("chat-input");
  }

  function fillComposer(text) {
    const input = composer();
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    // Land the caret at the end so the member types where they expect to.
    try {
      input.setSelectionRange(input.value.length, input.value.length);
    } catch (error) {
      /* not all inputs support selection ranges */
    }
  }

  /**
   * Send whatever is in the composer, through the form the Send button uses.
   *
   * Going through submit rather than calling the send function keeps every
   * guard that lives on that path -- an upload still in flight, a run already
   * going -- so a card click is refused for the same reasons a click on Send
   * would be, and the text stays put for the member to retry.
   */
  function sendComposer() {
    const form = document.getElementById("chat-form");
    if (!form) return;
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return;
    }
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  }

  function promptFromCard(card, value) {
    const prompt = String(card.prompt || "");
    if (!card.input) return prompt.trim();
    return prompt.split("{{input}}").join(value).trim();
  }

  /**
   * Ask for the one value a card needs.
   *
   * Uses the app's own dialog rather than window.prompt: the native one is
   * unstyled, ignores the theme, and its cramped single line has nowhere to put
   * the card's title, so the question arrives without the context that makes it
   * answerable. Falls back to the native prompt only if dialogs.js is missing.
   */
  async function askForCardInput(card) {
    const label = (card.input && card.input.label) || t("Details");
    const placeholder = (card.input && card.input.placeholder) || "";

    if (typeof window.showPrompt === "function") {
      const answer = await window.showPrompt({
        title: card.title || t("Start this"),
        message: label,
        placeholder,
        confirmText: t("Continue"),
        // The prompt is only shown for cards that need the value, so an empty
        // answer would compose a prompt with a hole in it.
        required: true,
      });
      return answer === null ? null : String(answer).trim();
    }

    const answer = window.prompt(placeholder ? t("{label} (e.g. {placeholder})", { label, placeholder }) : label, "");
    // Blank is a cancel, not an answer. showPrompt refuses to close on an empty
    // required field, but the native prompt has no such notion -- and since the
    // click now sends, a blank here would fire a prompt with a hole in it
    // instead of leaving one on screen to notice.
    return answer === null || !String(answer).trim() ? null : String(answer).trim();
  }

  function isFormCard(card) {
    return Boolean(card && Array.isArray(card.fields) && card.fields.length && card.template);
  }

  /**
   * What a clicked card sends: `{ prompt, files }`, or null when the member
   * cancelled (or the card cannot be answered on this page).
   */
  async function answerCard(card, agentId) {
    if (isFormCard(card) && typeof window.showCardForm === "function") {
      const answer = await window.showCardForm(card, { agentId });
      return answer ? { prompt: answer.prompt, files: answer.files || [] } : null;
    }
    if (!card.input) {
      // A card that only has fields cannot be asked here; one with a plain
      // prompt needs nothing asked.
      const prompt = promptFromCard(card, "");
      return prompt ? { prompt, files: [] } : null;
    }
    if (typeof window.showPicker === "function") {
      const picker = window.portalCardPicker;
      const answer = await window.showPicker({
        title: card.title || t("Start this"),
        label: card.input.label || t("Details"),
        placeholder: card.input.placeholder || "",
        source: card.input.source || "",
        params: card.input.params || {},
        multiline: card.input.multiline === true,
        agentId,
        historyKey: picker && typeof picker.historyKeyFor === "function" ? picker.historyKeyFor(card, "input") : "",
      });
      return answer ? { prompt: promptFromCard(card, answer.value), files: [] } : null;
    }
    const value = await askForCardInput(card);
    // Cancelled: leave the composer alone rather than filling it with a
    // half-formed prompt the member did not ask for.
    return value === null ? null : { prompt: promptFromCard(card, value), files: [] };
  }

  /** Fetch if needed, then paint -- unless the member moved on meanwhile. */
  async function applyForAgent(agentId) {
    if (!agentId) return;
    const payload = await loadPersonalization(agentId);
    if (activeAgentId !== agentId) return;
    applyToWelcome(payload);
  }

  // Fetched ahead of the paint so the greeting and the cards are already in
  // hand when the transcript is drawn, rather than arriving after it and
  // rewriting the welcome row a second time.
  window.portalPrefetchPersonalization = (agentId) =>
    (agentId ? loadPersonalization(agentId) : Promise.resolve(null));

  function bind() {
    document.addEventListener("portal:agent-selected", (browserEvent) => {
      const agentId = browserEvent.detail?.agentId;
      activeAgentId = agentId || null;
      applyForAgent(activeAgentId);
    });

    // Starting a new chat, switching sessions, or loading an empty one rebuilds
    // the welcome row from a hardcoded default without changing the selected
    // assistant, so the greeting and cards have to be painted back on.
    document.addEventListener("portal:welcome-rendered", (browserEvent) => {
      const agentId = browserEvent.detail?.agentId || activeAgentId;
      if (!agentId) return;
      activeAgentId = agentId;
      applyForAgent(agentId);
    });

    document.getElementById("message-list")?.addEventListener("click", async (browserEvent) => {
      const button = browserEvent.target.closest("[data-starter-card]");
      if (!button || !activeAgentId) return;
      const cards = (cache.get(activeAgentId) || {}).cards || [];
      const card = cards[Number(button.dataset.starterCard)];
      if (!card) return;

      const agentId = activeAgentId;
      const answer = await answerCard(card, agentId);
      // Cancelled, or the member moved to another assistant while the dialog
      // was open: nothing is sent.
      if (!answer || !answer.prompt || activeAgentId !== agentId) return;
      if (answer.files.length && typeof window.addPendingFilesAndUpload === "function") {
        // A file a card asked for travels as an attachment of the message,
        // the same way a file dropped on the composer does.
        await window.addPendingFilesAndUpload(answer.files);
      }
      fillComposer(answer.prompt);
      sendComposer();
    });

    // Warm a card's list while the pointer is on its way to the click, so
    // the picker usually opens with the rows already there.
    const warm = (browserEvent) => {
      const target = browserEvent.target;
      const button = target && typeof target.closest === "function" ? target.closest("[data-starter-card]") : null;
      const picker = window.portalCardPicker;
      if (!button || !activeAgentId || !picker || typeof picker.prefetchCard !== "function") return;
      const cards = (cache.get(activeAgentId) || {}).cards || [];
      const card = cards[Number(button.dataset.starterCard)];
      if (card) picker.prefetchCard(card, activeAgentId);
    };
    const list = document.getElementById("message-list");
    list?.addEventListener("mouseover", warm);
    list?.addEventListener("focusin", warm);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
