/**
 * The slash command in the composer, shown as the skill it is.
 *
 * Typing `/create-pull-request` looked like any other text, so nothing on
 * screen said whether the name was recognised, what it would do, or that the
 * instructions behind it are a file the member is allowed to read. The chip
 * answers all three: it names the skill, carries its description on hover, and
 * links to the source on the branch this assistant actually booted with.
 *
 * It appears under exactly the rule the send path uses to decide a message is a
 * skill invocation, so it is a statement about what will happen on send rather
 * than a hint that turns out to be wrong.
 */
(function () {
  "use strict";

  const ROW_ID = "composer-skill-chip";
  // Loading is cached per assistant by chat_ui.js; this only stops a burst of
  // keystrokes queuing a fetch each.
  const requestedSkillLoads = new Set();
  let lastRendered = "";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function row() {
    return document.getElementById(ROW_ID);
  }

  function input() {
    return document.getElementById("chat-input");
  }

  function agentId() {
    return typeof window.currentPortalAgentId === "function" ? window.currentPortalAgentId() : null;
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

  function hintFor(skill) {
    const lines = [];
    if (skill.description) lines.push(skill.description);
    if (!skill.callable && skill.blockedReason) {
      lines.push(skill.blockedReason);
    } else if (!skill.callable) {
      lines.push("This skill is not callable on the current runtime.");
    }
    if (skill.url) {
      lines.push(`Open the source on ${skill.branch}`);
    } else if (skill.branch) {
      // Says why the chip is not a link, rather than leaving a dead-looking one.
      lines.push("Source link unavailable for this assistant.");
    }
    return lines.join("\n\n") || skill.command;
  }

  function chipMarkup(skill) {
    const classes = ["portal-skill-chip"];
    if (!skill.callable) classes.push("is-blocked");
    const icon = skill.callable ? "wand-sparkles" : "ban";
    const body = `
      <i data-lucide="${icon}" class="portal-skill-chip-icon"></i>
      <span class="portal-skill-chip-name">${esc(skill.command)}</span>
      ${skill.url ? '<i data-lucide="external-link" class="portal-skill-chip-open"></i>' : ""}`;

    if (!skill.url) {
      return `<span class="${classes.join(" ")}" data-tooltip="${esc(hintFor(skill))}">${body}</span>`;
    }
    classes.push("is-linked");
    // noopener is not optional here: the target is a repository the member is
    // signed in to.
    return `<a class="${classes.join(" ")}" href="${esc(skill.url)}" target="_blank" rel="noopener noreferrer"
               data-tooltip="${esc(hintFor(skill))}">${body}</a>`;
  }

  /** Fetch the skill list once per assistant, then repaint with what arrived. */
  function ensureSkillsLoaded() {
    const agent = agentId();
    if (!agent || requestedSkillLoads.has(agent)) return;
    if (typeof window.ensurePortalSkillsLoaded !== "function") return;
    requestedSkillLoads.add(agent);
    Promise.resolve(window.ensurePortalSkillsLoaded(agent))
      .then(() => {
        if (agentId() === agent) update();
      })
      .catch(() => {
        // An assistant that is still starting has no skills to list yet; a
        // later keystroke retries.
        requestedSkillLoads.delete(agent);
      });
  }

  function update() {
    const container = row();
    if (!container) return;

    const text = input()?.value || "";
    const skill = typeof window.resolvePortalSkillCommand === "function"
      ? window.resolvePortalSkillCommand(text)
      : null;

    if (!skill) {
      // A leading slash with no match usually means the list has not arrived
      // yet, so ask for it -- but only when the text could still become one.
      if (/^\s*\//.test(text)) ensureSkillsLoaded();
      if (lastRendered !== "") {
        container.innerHTML = "";
        container.classList.add("hidden");
        lastRendered = "";
      }
      return;
    }

    const markup = chipMarkup(skill);
    // Rebuilding on every keystroke would restart the hover timer under the
    // pointer and make the tooltip flicker while arguments are typed.
    if (markup === lastRendered) return;
    lastRendered = markup;
    container.innerHTML = markup;
    container.classList.remove("hidden");
    renderIcons();
  }

  function bind() {
    const field = input();
    if (!field || !row()) return;

    field.addEventListener("input", update);
    document.addEventListener("portal:agent-selected", () => {
      // Skills differ per assistant, so a chip drawn for the previous one is a
      // claim about the wrong runtime.
      lastRendered = "";
      row()?.replaceChildren();
      row()?.classList.add("hidden");
      update();
    });
    update();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
