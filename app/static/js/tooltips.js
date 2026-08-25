/*
 * Portal tooltips.
 *
 * Replaces the browser's native `title` bubble, which is slow to appear, cannot
 * be themed, never shows for keyboard users, and truncates awkwardly. Hints are
 * read from `data-tooltip`; any legacy `title` is upgraded to one on first
 * contact so the two can never both appear.
 *
 * Everything is delegated from the document, so hints work on markup that htmx
 * swaps in and on rows chat_ui.js builds at runtime, with nothing to re-init.
 *
 * Public API (also on window):
 *   setTooltip(el, text)   -> set or clear an element's hint
 *   hideTooltip()          -> dismiss immediately
 */
(function () {
  "use strict";

  // Anything that can carry a hint: an explicit one, a legacy title, or an
  // interactive control the registry below may have an entry for.
  var SELECTOR = [
    "[data-tooltip]",
    "[title]",
    "button",
    "a[href]",
    'input:not([type="hidden"])',
    "select",
    "textarea",
    '[role="button"]',
  ].join(", ");
  var SHOW_DELAY_MS = 350;
  // Moving between two adjacent targets shouldn't replay the delay.
  var GRACE_MS = 320;
  var GAP_PX = 8;
  var VIEWPORT_MARGIN_PX = 8;

  var bubble = null;
  var arrow = null;
  var currentTarget = null;
  var showTimer = 0;
  var lastHiddenAt = 0;
  var seq = 0;
  // Touch devices fire a synthetic mouseover on tap; a tooltip that then sticks
  // over the thing you just tapped is worse than no tooltip.
  var pointerIsCoarse = false;

  function ensureBubble() {
    if (bubble) return bubble;
    bubble = document.createElement("div");
    bubble.className = "portal-tooltip";
    bubble.setAttribute("role", "tooltip");
    bubble.id = "portal-tooltip-bubble";
    arrow = document.createElement("span");
    arrow.className = "portal-tooltip-arrow";
    var text = document.createElement("span");
    text.className = "portal-tooltip-text";
    bubble.append(text, arrow);
    document.body.appendChild(bubble);
    return bubble;
  }

  // Hints for controls that carry no attribute of their own, resolved lazily by
  // selector. Kept here rather than stamped into the templates because the
  // settings panels are dense single-line markup and because Jira/Confluence
  // instance cards are cloned at runtime — a hint written into the template
  // would not reach the copies. First match wins.
  var HINTS = [
    // --- rail: what each section of the product actually is ---
    ["#rail-assistants-btn", "Assistants — each one is a running workspace you chat with"],
    ["#tasks-menu-btn", "Tasks — work an assistant runs on its own, tracked to completion"],
    ["#delegations-menu-btn", "Delegations — rules that start work automatically from GitHub, Jira, or a timer"],
    ["#users-menu-btn", "Administration — members, roles, and who is allowed to sign in"],
    ["#runtime-profiles-menu-btn", "Runtime Profiles — the credentials and integrations assistants boot with"],
    ["#help-btn", "What the terms mean, and the keyboard shortcuts"],
    ["#logout-btn", "Sign out of Portal"],

    // --- main header ---
    ["#header-new-chat-btn", "Start a fresh conversation (Ctrl/Cmd + Shift + O)"],
    ["#btn-sessions", "Earlier conversations with this assistant"],
    ["#btn-context", "How much of the model's context window this conversation is using"],
    ["#btn-files", "Browse the files in this assistant's workspace"],
    ["#detail-toggle", "Assistant status, configuration, and start/stop actions"],
    ["#header-add-allowlist-btn", "Let more usernames register and sign in"],

    // --- workspace list ---
    ["#add-agent-btn", "Create a new assistant"],
    ["#add-task-btn", "Hand a piece of work to an assistant to run on its own"],
    ["#add-delegation-btn", "Create a rule that starts work automatically"],
    ["#add-runtime-profile-btn", "Create a new set of credentials and integrations"],
    ["#agent-search-input", "Filter by assistant name"],
    ["#task-owner-filter", "Show every task you can see, or only your own"],
    ["#task-status-filter", "Show only tasks in one state"],
    ["#delegation-owner-filter", "Show every delegation you can see, or only your own"],
    ["#delegation-source-filter", "Show only rules triggered by one source"],
    ["#user-management-nav-item", "Members, roles, allowlist access, and per-member usage"],
    ["#runtime-profile-nav-list .portal-list-row", "Open this profile's credentials and integrations"],
    ["#task-nav-list [data-task-id]", "Open this task's detail, output, and run history"],
    ["#delegation-rule-nav-list [data-delegation-rule-id]", "Open this rule's trigger, schedule, and recent runs"],

    // --- home quick actions ---
    ["#home-create-agent-btn", "Create your first assistant and start chatting"],
    ["#home-start-chat-btn", "Start a fresh conversation with the selected assistant"],
    ["#home-open-tasks-btn", "See what assistants are working on right now"],
    ["#home-open-delegations-btn", "See the rules that start work automatically"],

    // --- composer ---
    ["#composer-attach-btn", "Attach images, PDFs, or documents to this message"],
    ["#send-chat-btn", "Send (Enter). Shift + Enter starts a new line."],
    ["#abort-chat-run-btn", "Stop the assistant's current turn (Esc)"],
    ["#composer-model-select", "Model for this message only — the profile default is unchanged"],
    ["#composer-reasoning-select", "How much reasoning the model does before answering. Higher is slower but better on hard problems."],
    ["#composer-context-select", "Largest context window this request may use"],
    ["#chat-input", "Ask anything. Enter sends, Shift + Enter starts a new line."],
    ["#jump-to-latest-btn", "Scroll back to the newest message"],

    // --- tool panel ---
    ["#pin-tool-panel", "Keep this panel open beside the conversation instead of over it"],
    ["#close-tool-panel", "Close this panel"],

    // --- overview screens ---
    ['[data-task-overview-scope="all"]', "Include tasks from every assistant you can see"],
    ['[data-task-overview-scope="mine"]', "Only tasks on assistants you own"],
    ["[data-refresh-task-overview]", "Reload the numbers on this page"],
    ["[data-open-create-task-main]", "Hand a piece of work to an assistant to run on its own"],
    ['[data-delegation-overview-scope="all"]', "Include delegations from every assistant you can see"],
    ['[data-delegation-overview-scope="mine"]', "Only delegations on assistants you own"],
    ["[data-refresh-delegation-overview]", "Reload the numbers on this page"],
    ["[data-open-create-delegation-main]", "Create a rule that starts work automatically from GitHub, Jira, or a timer"],

    // --- user management ---
    // Each pill explains what it grants, replacing the permanent line of helper
    // text that used to sit under the control and break the row's grid.
    ['[data-admin-role-option][value="user"]', "Owns and runs their own assistants"],
    ['[data-admin-role-option][value="admin"]', "Full access to every member's assistants, plus member management"],
    ["[data-admin-role-group]", "What this member can do"],
    ["[data-remove-allowlist]", "Revoke access. They lose sign-in immediately and existing sessions end."],
    ["[data-allow-member]", "Put this member back on the allowlist so they can sign in again"],
    ["[data-admin-member-search]", "Filter by name or username"],
    ["[data-admin-member-access-filter]", "Show everyone, or only members who can/cannot sign in"],

    // --- runtime profile: metadata ---
    ['#runtime-profile-form input[name="name"]', "What this profile is called in the assistant picker"],
    ['#runtime-profile-form input[name="description"]', "Optional note about what this profile is for"],
    ['input[name="is_default"]', "New assistants use this profile unless you pick another"],

    // --- runtime profile: proxy ---
    ['input[name="proxy_enabled"]', "Route the assistant's outbound traffic through a proxy"],
    ['input[name="proxy_url"]', "Proxy address including the port, for example http://proxy.example.com:8080"],
    ['input[name="proxy_username"]', "Only needed if the proxy requires authentication"],
    ['input[name="proxy_password"]', "Only needed if the proxy requires authentication"],
    ['[data-test-target="proxy"]', "Check that the proxy is reachable with these settings"],

    // --- runtime profile: LLM ---
    ["#llm_provider", "Which service answers the assistant's messages"],
    ["#llm_model", "Model used when a message does not override it"],
    ['select[name="llm_reasoning_effort"]', "Default reasoning effort. Higher is slower but better on hard problems."],
    ['select[name="llm_max_context_tokens"]', "Largest context window an assistant on this profile may use"],
    ['input[name="llm_api_key"]', "Filled in automatically after you authorize GitHub Copilot below"],
    ["[data-copilot-auth-button]", "Get a Copilot token through GitHub's device flow — always uses github.com"],
    ["[data-copilot-copy-button]", "Copy this code, then paste it on the GitHub page"],
    ['input[name="llm_ai_platform_username"]', "AI Platform account username"],
    ['input[name="llm_ai_platform_password"]', "AI Platform account password"],
    ['input[name="llm_ai_platform_usercase"]', "AI Platform usercase identifier issued to your team"],

    // --- runtime profile: integrations ---
    ['input[name="jira_enabled"]', "Let assistants on this profile read and update Jira"],
    ['input[name="confluence_enabled"]', "Let assistants on this profile read and write Confluence"],
    ['input[name="github_enabled"]', "Let assistants on this profile use the GitHub API"],
    ['[data-action="add-instance"][data-group="jira"]', "Connect another Jira site"],
    ['[data-action="add-instance"][data-group="confluence"]', "Connect another Confluence site"],
    ['[data-action="remove-instance"]', "Remove this connection from the profile"],
    ['[data-test-target="jira"]', "Check these Jira credentials against the server"],
    ['[data-test-target="confluence"]', "Check these Confluence credentials against the server"],
    ['[data-test-target="github"]', "Check these GitHub credentials against the API"],
    ['[data-instance-item] [data-field="name"]', "A label for this connection, shown to the assistant"],
    ['[data-instance-item="jira"] [data-field="url"]', "Jira site address, for example https://yourcompany.atlassian.net"],
    ['[data-instance-item="confluence"] [data-field="url"]', "Confluence address, usually the site URL followed by /wiki"],
    ['[data-instance-item] [data-field="username"]', "The account email for this site"],
    ['[data-instance-item] [data-field="password"]', "Use an API token instead where the site supports it"],
    ['[data-instance-item] [data-field="token"]', "API token — preferred over a password"],
    ['[data-instance-item] [data-field="project"]', "Default Jira project key, for example ENG"],
    ['[data-instance-item] [data-field="space"]', "Default Confluence space key"],
    ['[data-instance-item] [data-field="api_version"]', "Leave on Auto unless the site needs a specific REST version"],
    ['[data-instance-item] [data-field="enabled"]', "Turn this single connection on or off without deleting it"],
    ['input[name="mobile_enabled"]', "Let assistants on this profile drive mobile test devices"],
    ['input[name="aws_enabled"]', "Let assistants on this profile reach AWS with these credentials"],
    ['input[name="aws_domain"]', "AWS domain or account alias to sign in against"],
    ['input[name="aws_username"]', "AWS account username"],
    ['input[name="aws_password"]', "AWS account password"],
    ['input[name="jenkins_enabled"]', "Let assistants on this profile trigger and read Jenkins jobs"],
    ['[data-action="add-instance"][data-group="jenkins"]', "Connect another Jenkins controller"],
    ['input[name="git_user_name"]', "Name recorded as the author on commits the assistant makes"],
    ['input[name="git_user_email"]', "Email recorded as the author on commits the assistant makes"],
    ['input[name="debug_enabled"]', "Write verbose runtime logs. Useful when chasing a problem, noisy otherwise."],
    ['select[name="debug_log_level"]', "How much detail the runtime writes to its log"],
    ["#runtime-profile-form button[type=\"submit\"]", "Save this profile. Running assistants bound to it restart to pick up the change."],
    ["[data-delete-runtime-profile]", "Delete this profile. Assistants bound to it must be moved first."],

    // --- assistant create/edit wizard ---
    ['[data-load-branches], [data-edit-load-branches]', "Fetch the branch list from this repository"],
    ['select[name="runtime_profile_id"]', "Which credentials and integrations this assistant boots with"],
    ['input[name="agent_settings_repo_url"]', "Repository holding the instructions that shape how this assistant behaves. Leave empty for the configured default."],
    ['input[name="skill_repo_url"]', "Repository of packaged skills to install. Leave empty for the configured default."],
    ['input[name="runtime_type"][value="native"]', "The Python EFP runtime"],
    ['input[name="runtime_type"][value="opencode"]', "The opencode adapter runtime"],
  ];

  var compiledHints = null;

  function hintFromRegistry(el) {
    if (!compiledHints) compiledHints = HINTS;
    for (var i = 0; i < compiledHints.length; i += 1) {
      try {
        if (el.matches(compiledHints[i][0])) return compiledHints[i][1];
      } catch (_error) { /* skip a selector this browser rejects */ }
    }
    return "";
  }

  function tooltipTextFor(el) {
    if (!el || el.nodeType !== 1) return "";

    // A legacy title is retired either way, so the native bubble can never
    // double up. Keep the element's accessible name: an icon-only button whose
    // only name was the title would otherwise go mute.
    var title = el.getAttribute("title");
    if (title && title.trim()) {
      title = title.trim();
      el.removeAttribute("title");
      if (!el.getAttribute("aria-label") && !(el.textContent || "").trim()) {
        el.setAttribute("aria-label", title);
      }
    } else {
      title = "";
    }

    var explicit = el.getAttribute("data-tooltip");
    if (explicit && explicit.trim()) return explicit.trim();

    // Registry beats a legacy title: most of those just repeated the visible
    // label ("Sessions", "Tasks"), which is not a hint. Resolve once and cache
    // on the element so repeated hovers and cloned cards cost nothing.
    if (el.dataset.tooltipResolved !== "1") {
      el.dataset.tooltipResolved = "1";
      var registryHint = hintFromRegistry(el);
      if (registryHint) {
        el.setAttribute("data-tooltip", registryHint);
        return registryHint;
      }
      if (title) {
        el.setAttribute("data-tooltip", title);
        return title;
      }
      return "";
    }

    return title || "";
  }

  function place(el) {
    var target = el.getBoundingClientRect();
    var box = bubble.getBoundingClientRect();
    var preferred = el.getAttribute("data-tooltip-placement") || "top";

    var placement = preferred;
    if (placement === "top" && target.top - box.height - GAP_PX < VIEWPORT_MARGIN_PX) placement = "bottom";
    else if (placement === "bottom" && target.bottom + box.height + GAP_PX > window.innerHeight - VIEWPORT_MARGIN_PX) placement = "top";
    else if (placement === "left" && target.left - box.width - GAP_PX < VIEWPORT_MARGIN_PX) placement = "right";
    else if (placement === "right" && target.right + box.width + GAP_PX > window.innerWidth - VIEWPORT_MARGIN_PX) placement = "left";

    var top;
    var left;
    if (placement === "top" || placement === "bottom") {
      top = placement === "top" ? target.top - box.height - GAP_PX : target.bottom + GAP_PX;
      left = target.left + target.width / 2 - box.width / 2;
    } else {
      top = target.top + target.height / 2 - box.height / 2;
      left = placement === "left" ? target.left - box.width - GAP_PX : target.right + GAP_PX;
    }

    // Keep the bubble on screen, then point the arrow at the target anyway.
    var maxLeft = window.innerWidth - box.width - VIEWPORT_MARGIN_PX;
    var maxTop = window.innerHeight - box.height - VIEWPORT_MARGIN_PX;
    var clampedLeft = Math.max(VIEWPORT_MARGIN_PX, Math.min(left, maxLeft));
    var clampedTop = Math.max(VIEWPORT_MARGIN_PX, Math.min(top, maxTop));

    bubble.dataset.placement = placement;
    bubble.style.top = Math.round(clampedTop) + "px";
    bubble.style.left = Math.round(clampedLeft) + "px";

    if (placement === "top" || placement === "bottom") {
      var centre = target.left + target.width / 2 - clampedLeft;
      arrow.style.left = Math.round(Math.max(10, Math.min(centre, box.width - 10))) + "px";
      arrow.style.top = "";
    } else {
      var middle = target.top + target.height / 2 - clampedTop;
      arrow.style.top = Math.round(Math.max(10, Math.min(middle, box.height - 10))) + "px";
      arrow.style.left = "";
    }
  }

  function show(el, text) {
    ensureBubble();
    currentTarget = el;
    bubble.querySelector(".portal-tooltip-text").textContent = text;

    seq += 1;
    var id = "portal-tooltip-" + seq;
    bubble.id = id;
    // Only describe; the element keeps its own accessible name.
    if (!el.getAttribute("aria-describedby")) {
      el.setAttribute("aria-describedby", id);
      el.dataset.tooltipDescribed = "1";
    }

    bubble.classList.add("is-measuring");
    bubble.classList.add("is-visible");
    place(el);
    bubble.classList.remove("is-measuring");
  }

  function hideTooltip() {
    window.clearTimeout(showTimer);
    showTimer = 0;
    if (currentTarget) {
      if (currentTarget.dataset.tooltipDescribed === "1") {
        currentTarget.removeAttribute("aria-describedby");
        delete currentTarget.dataset.tooltipDescribed;
      }
      currentTarget = null;
    }
    if (bubble && bubble.classList.contains("is-visible")) {
      bubble.classList.remove("is-visible");
      lastHiddenAt = Date.now();
    }
  }

  function scheduleShow(el, immediate) {
    var text = tooltipTextFor(el);
    if (!text) return;
    if (currentTarget === el && bubble && bubble.classList.contains("is-visible")) return;

    window.clearTimeout(showTimer);
    var withinGrace = Date.now() - lastHiddenAt < GRACE_MS;
    var delay = immediate || withinGrace ? 0 : SHOW_DELAY_MS;
    showTimer = window.setTimeout(function () {
      // The pointer may have left, or the element may have been swapped out.
      if (!el.isConnected) return;
      show(el, tooltipTextFor(el) || text);
    }, delay);
  }

  function candidateFromEvent(event) {
    var direct = event.target && event.target.closest ? event.target.closest(SELECTOR) : null;
    if (direct) return direct;
    // Disabled controls never dispatch mouse events, and they carry some of the
    // most useful hints ("Select an assistant first"), so look under the pointer.
    if (typeof event.clientX !== "number") return null;
    var under = document.elementFromPoint(event.clientX, event.clientY);
    return under && under.closest ? under.closest(SELECTOR) : null;
  }

  document.addEventListener("mouseover", function (event) {
    if (pointerIsCoarse) return;
    var el = candidateFromEvent(event);
    if (!el) {
      if (currentTarget) hideTooltip();
      return;
    }
    if (el === currentTarget) return;
    hideTooltip();
    scheduleShow(el, false);
  });

  document.addEventListener("mouseout", function (event) {
    if (!currentTarget && !showTimer) return;
    var to = event.relatedTarget;
    if (to && currentTarget && currentTarget.contains(to)) return;
    var next = to && to.closest ? to.closest(SELECTOR) : null;
    if (next === currentTarget && next) return;
    hideTooltip();
  });

  // Keyboard users get the same hints; native title never gave them any.
  document.addEventListener("focusin", function (event) {
    var el = event.target && event.target.closest ? event.target.closest(SELECTOR) : null;
    if (!el) return;
    // Only for keyboard focus — clicking a button shouldn't pop its own hint.
    if (!el.matches(":focus-visible")) return;
    hideTooltip();
    scheduleShow(el, true);
  });

  document.addEventListener("focusout", function () {
    hideTooltip();
  });

  // A hint must never outlive what it describes or sit stale over the page.
  document.addEventListener("click", hideTooltip, true);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") hideTooltip();
  });
  window.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("resize", hideTooltip);
  window.addEventListener("blur", hideTooltip);

  document.addEventListener("pointerdown", function (event) {
    pointerIsCoarse = event.pointerType === "touch" || event.pointerType === "pen";
    if (pointerIsCoarse) hideTooltip();
  }, true);

  function setTooltip(el, text) {
    if (!el) return;
    if (text) {
      el.setAttribute("data-tooltip", text);
      el.removeAttribute("title");
    } else {
      el.removeAttribute("data-tooltip");
      if (currentTarget === el) hideTooltip();
    }
  }

  window.setTooltip = setTooltip;
  window.hideTooltip = hideTooltip;
})();
