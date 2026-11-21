/**
 * Apps in the Portal page: the app center, the New app dialog, an app's own
 * page, and Administration > Apps (APPS_PLAN.md, M2 to M5).
 *
 * An app is a small web app a member publishes from a GitHub repository. It
 * runs on the app host, another hostname, and always opens in a new tab:
 * nothing here embeds one. Everything these pages show comes from the owner
 * API (/api/apps, app/api/apps.py); this file draws it and sends the
 * member's choices back. chat_ui.js keeps the section's address, rail and
 * header (window.portalAppsShell) and calls showCenter / showApp below.
 *
 * The script is in the page only while Apps is enabled (APPS_ENABLED). The
 * parts that decide what a member reads (states and their reasons, the order
 * of a list, what an error asks them to do) are plain functions, exported at
 * the bottom for the tests.
 */
(function () {
  "use strict";

  const API = "/api/apps";
  const HAS_DOM = typeof document !== "undefined";

  // ---- small things --------------------------------------------------------

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // An address the server gave, before it becomes a link, an image or a new
  // tab: an http(s) address, or one inside the Portal ("/api/...", "#/help").
  // Anything with a scheme of another kind (javascript:, data:, ssh: ...) is
  // no address to go to: "", which every caller shows as "no link".
  function safeUrl(value) {
    const text = String(value === null || value === undefined ? "" : value).trim();
    if (!text) return "";
    try {
      const here = typeof window !== "undefined" && window.location && window.location.href ? window.location.href : "http://portal.invalid/";
      const scheme = new URL(text, here).protocol;
      return scheme === "http:" || scheme === "https:" ? text : "";
    } catch (_error) {
      return "";
    }
  }

  // An app opens in a tab of its own, and only at an address safeUrl lets through.
  function openInNewTab(value) {
    const url = safeUrl(value);
    if (url) window.open(url, "_blank", "noopener");
  }

  function shell() {
    return (typeof window !== "undefined" && window.portalAppsShell) || null;
  }

  function toast(message, variant) {
    if (typeof window !== "undefined" && typeof window.showToast === "function") {
      window.showToast(message, variant ? { variant: variant } : {});
    }
  }

  // Waiting. Every action that asks the server shows that it is running, where
  // the member pressed: a button turns busy (dialogs.js), a switch or a choice
  // is held while it is saved, and a dialog that asked "are you sure" stays
  // open until the work is done (the `action` of showConfirm / showPrompt).
  function busy(button, label) {
    if (typeof window === "undefined" || typeof window.portalBusyButton !== "function") return function () {};
    return window.portalBusyButton(button, label);
  }

  function savingControl(input) {
    if (!input) return function () {};
    // The row of a switch, or the option of a choice: where "saving" is shown.
    const holder = (input.closest && (input.closest(".portal-app-switch-row") || input.closest("label"))) || input.parentElement;
    input.disabled = true;
    if (holder) {
      holder.classList.add("is-saving");
      holder.setAttribute("aria-busy", "true");
    }
    return function () {
      input.disabled = false;
      if (holder) {
        holder.classList.remove("is-saving");
        holder.removeAttribute("aria-busy");
      }
    };
  }

  // A button drawn from the page's state: busy while its own action runs,
  // disabled while any other does. It keeps its label, and with it its
  // size: the spinner lies over the label, and what is happening is said to
  // assistive technology.
  function stateButtonHtml(action, classes, attrs, label, busyLabel, running) {
    const working = running === action;
    return (
      '<button type="button" class="portal-btn ' + classes + (working ? " is-busy is-busy-over" : "") + '" ' + attrs +
      (working ? ' aria-busy="true" aria-label="' + esc(busyLabel) + '"' : "") + (running ? " disabled" : "") + ">" +
      "<span>" + esc(label) + "</span></button>"
    );
  }

  // The action that holds the page busy; "" when none does.
  function runningAction() {
    return page.busy ? page.busyAction || "other" : "";
  }

  function drawIcons(scope) {
    if (typeof window === "undefined" || !window.lucide || typeof window.lucide.createIcons !== "function") return;
    if (scope) window.lucide.createIcons({ root: scope });
    else window.lucide.createIcons();
  }

  async function copyToClipboard(text) {
    const value = String(text || "");
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch (_error) {
      /* fall through to the selection way */
    }
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (_error) {
      copied = false;
    }
    area.remove();
    return copied;
  }

  // ---- the API ---------------------------------------------------------------

  // An error answer of the owner API in the member's language: the message is
  // an English catalogue key with its placeholders apart
  // (app/services/apps/errors.py), which tv() looks up and fills.
  function errorText(payload, status) {
    if (payload && typeof payload === "object") {
      if (typeof payload.message_key === "string" && payload.message_key) {
        return tv(payload.message_key, payload.params || {});
      }
      if (typeof payload.detail === "string" && payload.detail) return tv(payload.detail);
      if (Array.isArray(payload.detail)) {
        const parts = payload.detail
          .map(function (item) { return item && typeof item.msg === "string" ? item.msg : ""; })
          .filter(Boolean);
        if (parts.length) return parts.join("; ");
      }
      if (payload.error && typeof payload.error.message === "string" && payload.error.message) {
        return payload.error.message;
      }
      if (typeof payload.message === "string" && payload.message) return payload.message;
    }
    return status ? t("Request failed (HTTP {status})", { status: status }) : t("Request failed");
  }

  class ApiError extends Error {
    constructor(message, payload, status) {
      super(message);
      this.name = "ApiError";
      this.payload = payload && typeof payload === "object" ? payload : null;
      this.status = status || 0;
      this.code = (this.payload && this.payload.code) || "";
    }
  }

  async function request(path, options) {
    const init = Object.assign({}, options || {});
    init.headers = Object.assign({ Accept: "application/json" }, init.headers || {});
    if (init.json !== undefined) {
      init.body = JSON.stringify(init.json);
      init.headers["Content-Type"] = "application/json";
      delete init.json;
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      if (error && error.name === "AbortError") throw error;
      throw new ApiError(t("The Portal could not be reached. Check your connection and try again."), null, 0);
    }
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
      throw new ApiError(errorText(payload, response.status), payload, response.status);
    }
    if (response.status === 204) return null;
    const type = response.headers.get("content-type") || "";
    return type.indexOf("application/json") >= 0 ? response.json() : response.text();
  }

  function appPath(appId, suffix) {
    return API + "/" + encodeURIComponent(appId) + (suffix || "");
  }

  // ---- numbers, sizes, times ------------------------------------------------

  // The Portal stores UTC without a zone ("2026-11-15T08:30:00"); said so
  // here, or the browser would read it as the reader's local time.
  function parseTime(value) {
    if (!value) return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    let text = String(value).trim();
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(text) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(text)) {
      text = text.replace(" ", "T") + "Z";
    }
    const parsed = new Date(text);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDateTime(value) {
    const parsed = parseTime(value);
    if (!parsed) return "";
    return parsed.toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  // "3 minutes ago" for what just happened, the date for the rest.
  function formatAgo(value, now) {
    const parsed = parseTime(value);
    if (!parsed) return "";
    const seconds = Math.round(((now || new Date()).getTime() - parsed.getTime()) / 1000);
    if (seconds < 45) return t("just now");
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return tn(minutes, "{n} minute ago", "{n} minutes ago");
    const hours = Math.round(minutes / 60);
    if (hours < 24) return tn(hours, "{n} hour ago", "{n} hours ago");
    const days = Math.round(hours / 24);
    if (days < 14) return tn(days, "{n} day ago", "{n} days ago");
    return formatDateTime(parsed);
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = bytes;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    const digits = unit === 0 || amount >= 100 ? 0 : 1;
    return amount.toFixed(digits) + " " + units[unit];
  }

  function formatCount(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return Math.round(number).toLocaleString();
  }

  function formatCredits(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return number >= 100 ? number.toFixed(0) : number.toFixed(1);
  }

  function formatDuration(startedAt, finishedAt) {
    const start = parseTime(startedAt);
    const end = parseTime(finishedAt);
    if (!start || !end) return "";
    const seconds = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
    if (seconds < 60) return t("{n} s", { n: seconds });
    return t("{minutes} min {seconds} s", { minutes: Math.floor(seconds / 60), seconds: seconds % 60 });
  }

  function shortSha(sha) {
    return String(sha || "").slice(0, 7);
  }

  // A branch or tag as it is named. An app made from a template follows a
  // commit instead, which is shown as a commit and never as a name.
  function refName(ref) {
    const text = String(ref || "");
    return /^[0-9a-f]{40}$/.test(text) ? "" : text;
  }

  // ---- who may open an app --------------------------------------------------

  // The words read right on the owner's own page and on an administrator's
  // list alike, so they speak of "the owner" rather than of "you".
  const AUDIENCES = [
    { value: "private", label: N_("Only the owner"), detail: N_("Nobody else can open it.") },
    { value: "public", label: N_("The owner's group"), detail: N_("Every member of the owner's group.") },
    { value: "platform", label: N_("Every member"), detail: N_("Everyone who can sign in to the Portal.") },
    { value: "anyone", label: N_("Anyone"), detail: N_("No sign-in at all. An administrator approves each app first.") },
  ];
  const AUDIENCE_ORDER = AUDIENCES.map(function (item) { return item.value; });

  function audienceLabel(visibility) {
    const found = AUDIENCES.filter(function (item) { return item.value === visibility; })[0];
    return found ? tv(found.label) : String(visibility || "");
  }

  // Whether a change lets more people in than before.
  function audienceWidens(from, to) {
    return AUDIENCE_ORDER.indexOf(to) > AUDIENCE_ORDER.indexOf(from);
  }

  // ---- what an app is doing, in plain words ---------------------------------

  const STATE_LABELS = {
    running: N_("Running"),
    starting: N_("Starting"),
    stopped: N_("Stopped"),
    crashed: N_("Crashed"),
    killed: N_("Stopped by the host"),
    failed: N_("Failed to start"),
    undeployed: N_("Not deployed"),
    deploying: N_("Deploying"),
    deploy_failed: N_("Deployment failed"),
    disabled: N_("Taken down"),
    unknown: N_("Unknown"),
  };
  const STATE_TONES = {
    running: "success",
    starting: "info",
    stopped: "neutral",
    crashed: "error",
    killed: "error",
    failed: "error",
    undeployed: "neutral",
    deploying: "info",
    deploy_failed: "error",
    disabled: "error",
    unknown: "neutral",
  };
  const ATTENTION_STATES = ["crashed", "killed", "failed", "deploy_failed", "disabled"];

  function disabledReasonText(reason) {
    const text = String(reason || "").trim();
    if (text === "owner_inactive") return t("Its owner no longer has access to the Portal, so the app is switched off.");
    if (text) return t("An administrator took it down: {reason}", { reason: text });
    return t("An administrator took it down.");
  }

  function hostReasonText(state, reason) {
    const text = String(reason || "").trim();
    if (state === "starting") return t("It is starting and will answer in a moment.");
    if (state === "stopped") {
      if (text === "idle") return t("It stopped after a while without visitors. The next visit starts it again.");
      if (text === "manual") return t("It was stopped by hand. The next visit starts it again.");
      return t("It is not running. The next visit starts it again.");
    }
    if (state === "crashed") {
      const exit = /^exit:(-?\d+)$/.exec(text);
      if (exit) return t("It exited with code {code}. The log says why.", { code: exit[1] });
      return t("It stopped unexpectedly. The log says why.");
    }
    if (state === "killed") {
      if (text === "memory") return t("The host stopped it because it used more memory than an app may.");
      return t("The host stopped it.");
    }
    if (state === "failed") return t("It could not be started. The log says why.");
    if (state === "undeployed") return t("The app host holds no release of it. Deploy it again.");
    return "";
  }

  // One state for an app, from its card or page payload and, when the page
  // has one, the fresher status answer: {key, tone, label, reason, attention}.
  function appState(app, status) {
    const source = app || {};
    const manage = source.manage || {};
    const latest = manage.latest_release || null;
    const enabled = status ? status.enabled !== false : source.enabled !== false;
    const disabledReason = (status && status.disabled_reason) || source.disabled_reason || "";
    const deployed = status ? Boolean(status.deployed) : Boolean(source.deployed);
    const deploying = status ? Boolean(status.deploying) : Boolean(manage.active_release);
    const hostState = String((status && status.state) || source.state || "");
    const hostReason = (status ? status.reason : source.state_reason) || "";

    let key = "unknown";
    let reason = "";
    if (!enabled) {
      key = "disabled";
      reason = disabledReasonText(disabledReason);
    } else if (deploying) {
      key = "deploying";
      reason = t("A deployment is in progress.");
    } else if (!deployed) {
      if (latest && latest.status === "failed") {
        key = "deploy_failed";
        reason = t("The first deployment failed. Deployments shows where and why.");
      } else {
        key = "undeployed";
        reason = t("Nothing is deployed yet. Deploy it to bring it to its address.");
      }
    } else if (hostState === "disabled") {
      key = "disabled";
      reason = disabledReasonText(hostReason);
    } else if (Object.prototype.hasOwnProperty.call(STATE_LABELS, hostState)) {
      key = hostState;
      reason = hostReasonText(hostState, hostReason);
    } else {
      key = "unknown";
      reason = t("The app host has not reported on it yet.");
    }
    return {
      key: key,
      tone: STATE_TONES[key],
      label: tv(STATE_LABELS[key]),
      reason: reason,
      attention: ATTENTION_STATES.indexOf(key) >= 0,
      // The app runs, but the newest code did not make it: said beside the state.
      lastDeployFailed: Boolean(deployed && latest && latest.status === "failed" && key !== "deploy_failed"),
    };
  }

  function needsAttention(app) {
    const state = appState(app, null);
    return state.attention || state.lastDeployFailed;
  }

  function byName(left, right) {
    return String(left.name || "").localeCompare(String(right.name || ""), undefined, { sensitivity: "base" });
  }

  // My apps: the ones that need something from me first, the rest by name.
  function sortMine(apps) {
    return (apps || []).slice().sort(function (left, right) {
      const order = Number(needsAttention(right)) - Number(needsAttention(left));
      return order || byName(left, right);
    });
  }

  // Available to me: what I used last comes first, the rest by name.
  function sortAvailable(apps) {
    return (apps || []).slice().sort(function (left, right) {
      const leftTime = parseTime(left.last_visited_at);
      const rightTime = parseTime(right.last_visited_at);
      if (leftTime && rightTime) return rightTime.getTime() - leftTime.getTime() || byName(left, right);
      if (leftTime) return -1;
      if (rightTime) return 1;
      return byName(left, right);
    });
  }

  // Every word typed must be in the name, the address, the description or the owner.
  function matchesQuery(app, query) {
    const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const owner = (app && app.owner) || {};
    const haystack = [app && app.name, app && app.slug, app && app.description, owner.display_name, owner.username]
      .map(function (part) { return String(part || "").toLowerCase(); })
      .join(" ");
    return words.every(function (word) { return haystack.indexOf(word) >= 0; });
  }

  // The first letter of the name, for an app without an icon.
  function appInitial(name) {
    const text = String(name || "").trim();
    return text ? Array.from(text)[0].toUpperCase() : "?";
  }

  // ---- registering: is there enough to ask the server yet? -------------------

  function plausibleRepoUrl(value) {
    const text = String(value || "").trim();
    if (!text || /\s/.test(text)) return false;
    // https://host/owner/repository(.git), or git@host:owner/repository(.git).
    return /^(https?|ssh|git):\/\/[^/\s]+\/[^/\s]+\/[^/\s]+\/?$/i.test(text)
      || /^[\w.-]+@[\w.-]+:[^/\s]+\/[^/\s]+$/.test(text);
  }

  function plausibleRef(value) {
    const text = String(value || "").trim();
    return Boolean(text) && !/[\s~^:?*[\\]/.test(text) && text.indexOf("..") < 0 && !/^[-/]/.test(text);
  }

  function plausibleEntryDir(value) {
    const text = String(value || "").trim();
    if (!text) return true;
    return !/[\s\\]/.test(text) && text.split("/").indexOf("..") < 0;
  }

  // The address name the server makes of an app's name (app/services/apps/names.py).
  function slugify(name) {
    const slug = String(name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return slug.slice(0, 40).replace(/^-+|-+$/g, "");
  }

  // What a failed look at a repository asks of the member: a title, the
  // server's sentence, what to do, and (when it helps) a line to copy.
  function inspectProblem(payload, settings, status) {
    const source = payload && typeof payload === "object" ? payload : {};
    const code = String(source.code || "");
    const params = source.params || {};
    const problem = { code: code, title: t("The repository could not be checked"), text: errorText(source, status), steps: [], list: [], snippet: "", account: "" };
    if (code === "repo_not_readable") {
      const account = String(params.account || (settings && settings.service_account) || "");
      problem.title = t("The Portal cannot read this repository");
      problem.account = account;
      if (account) {
        problem.text = t("Repositories are read as {account}, and that account has no access to {repo}.", { account: account, repo: params.repo || t("this repository") });
        problem.steps = [
          t("On GitHub, open the repository's Settings, then Collaborators and teams."),
          t("Add {account} with the Read role.", { account: account }),
          t("Come back and press Check again."),
        ];
      } else {
        problem.steps = [t("Ask an administrator which account reads app repositories, give it the Read role on the repository, then press Check again.")];
      }
    } else if (code === "not_listed_as_owner") {
      const username = String(params.username || "");
      problem.title = t("efp-app.json does not name you");
      // Only a manifest that lists owners refuses anybody: without the list any member may publish.
      problem.text = t("efp-app.json limits who may publish this directory to the members under owners, and you are not one of them.");
      problem.steps = [t("Add your username to owners in efp-app.json, commit and push, then press Check again:")];
      problem.snippet = username ? '"' + username + '"' : "";
    } else if (code === "manifest_invalid") {
      problem.title = t("efp-app.json needs fixing");
      problem.list = (Array.isArray(source.problems) ? source.problems : []).map(function (item) {
        if (item && typeof item.message_key === "string" && item.message_key) return tv(item.message_key, item.params || {});
        return String((item && item.detail) || "");
      }).filter(Boolean);
      if (problem.list.length) problem.text = "";
      problem.steps = [t("Correct these in efp-app.json, commit and push, then press Check again.")];
    } else if (code === "manifest_missing") {
      problem.title = t("No efp-app.json there");
      problem.steps = [t("The entry directory is the folder that holds efp-app.json. Leave it empty when the file is at the top of the repository.")];
    } else if (code === "ref_not_found") {
      problem.title = t("No such branch or tag");
    } else if (code === "repo_url_invalid") {
      problem.title = t("That is not a repository this Portal reads");
    } else if (code === "entry_dir_invalid" || code === "ref_invalid") {
      problem.title = t("Check what you entered");
    }
    return problem;
  }

  // ---- deployments -------------------------------------------------------------

  const STEP_LABELS = {
    "portal:resolve": N_("Find the commit"),
    "portal:manifest": N_("Read efp-app.json"),
    "portal:fetch": N_("Send the code to the app host"),
    "host:unpack": N_("Unpack the code"),
    "host:manifest": N_("Check the manifest"),
    "host:environment": N_("Create the environment"),
    "host:dependencies": N_("Install the dependencies"),
    "host:start": N_("Start the app"),
    "host:health": N_("Check that it answers"),
    "host:activate": N_("Switch to this release"),
  };
  const STEP_ICONS = { pending: "circle", running: "loader-circle", succeeded: "circle-check", failed: "circle-x", skipped: "circle-minus" };
  const RELEASE_STATUS_LABELS = { queued: N_("Queued"), running: N_("In progress"), succeeded: N_("Succeeded"), failed: N_("Failed") };
  const RELEASE_STATUS_TONES = { queued: "info", running: "info", succeeded: "success", failed: "error" };

  function stepLabel(step) {
    const key = String((step && step.source) || "") + ":" + String((step && step.name) || "");
    return Object.prototype.hasOwnProperty.call(STEP_LABELS, key) ? tv(STEP_LABELS[key]) : String((step && step.name) || "");
  }

  // The words under a step: a note the Portal left, or why the step failed.
  function stepNote(step) {
    if (!step) return "";
    if (step.error && typeof step.error === "object") {
      if (step.error.message_key) return tv(step.error.message_key, step.error.params || {});
      return String(step.error.message || "");
    }
    if (step.note_key) return tv(step.note_key, step.note_params || {});
    return String(step.note || "");
  }

  function releaseIsActive(release) {
    return Boolean(release) && (release.status === "queued" || release.status === "running");
  }

  function releaseKindLabel(release) {
    return release && release.kind === "rollback" ? t("Rollback") : t("Deployment");
  }

  // ---- usage --------------------------------------------------------------------

  // Bar heights in percent of the tallest day; a day with nothing is 0.
  function barHeights(rows, pick) {
    const values = (rows || []).map(function (row) {
      const value = Number(pick(row));
      return Number.isFinite(value) && value > 0 ? value : 0;
    });
    const top = Math.max.apply(null, values.concat([0]));
    return values.map(function (value) { return top > 0 ? Math.round((value / top) * 1000) / 10 : 0; });
  }

  function sumOf(rows, pick) {
    return (rows || []).reduce(function (total, row) {
      const value = Number(pick(row));
      return total + (Number.isFinite(value) ? value : 0);
    }, 0);
  }

  // ---- environment variables ------------------------------------------------------

  const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
  const ENV_RESERVED = ["PORT", "HOME", "TMPDIR", "PATH"];

  // {env} from the rows of the editor, or {error} naming the first row that
  // cannot be saved (the server checks the same rules: docs/CONTRACTS.md, 8).
  function envFromRows(rows) {
    const env = {};
    for (let index = 0; index < (rows || []).length; index += 1) {
      const name = String(rows[index].name || "").trim();
      const value = String(rows[index].value === undefined || rows[index].value === null ? "" : rows[index].value);
      if (!name && !value) continue;
      if (!ENV_NAME.test(name)) {
        return { error: t("{name} is not a valid variable name: use letters, digits and underscores, not starting with a digit.", { name: name || t("(empty)") }) };
      }
      const upper = name.toUpperCase();
      if (upper.indexOf("EFP_") === 0 || ENV_RESERVED.indexOf(upper) >= 0) {
        return { error: t("{name} is set by the platform and cannot be used as a variable name.", { name: name }) };
      }
      if (Object.prototype.hasOwnProperty.call(env, name)) {
        return { error: t("{name} is listed twice.", { name: name }) };
      }
      env[name] = value;
    }
    return { env: env };
  }

  // ---- the live log ------------------------------------------------------------------

  // Feeds text of a text/event-stream and calls onLine for each
  // `data: {"line": "..."}` event; comment lines (keep-alives) are skipped.
  function createSseParser(onLine) {
    let buffer = "";
    return function feed(chunk) {
      buffer += String(chunk || "");
      let at = buffer.indexOf("\n");
      while (at >= 0) {
        const raw = buffer.slice(0, at).replace(/\r$/, "");
        buffer = buffer.slice(at + 1);
        if (raw.indexOf("data:") === 0) {
          const body = raw.slice(5).trim();
          try {
            const parsed = JSON.parse(body);
            if (parsed && typeof parsed.line === "string") onLine(parsed.line);
          } catch (_error) {
            if (body) onLine(body);
          }
        }
        at = buffer.indexOf("\n");
      }
    };
  }

  // ---- activity ------------------------------------------------------------------------

  const FIELD_LABELS = {
    name: N_("the name"),
    description: N_("the description"),
    repo_ref: N_("the branch or tag it follows"),
    llm_enabled: N_("the model provider switch"),
    connectors_enabled: N_("the connectors"),
    connectors_write: N_("which connectors may write"),
    daily_credits: N_("the daily credits limit"),
    daily_tokens: N_("the daily tokens limit"),
  };

  // One line for an entry of the app's history (app/services/apps/audit.py).
  function eventText(event) {
    const action = String((event && event.action) || "");
    const detail = (event && event.detail) || {};
    switch (action) {
      case "app.created": return t("Registered the app");
      case "app.updated": {
        const fields = (Array.isArray(detail.fields) ? detail.fields : []).map(function (field) {
          return Object.prototype.hasOwnProperty.call(FIELD_LABELS, field) ? tv(FIELD_LABELS[field]) : String(field);
        });
        return fields.length ? t("Changed {fields}", { fields: fields.join(", ") }) : t("Changed the app");
      }
      case "app.deleted": return t("Deleted the app");
      case "app.visibility_changed":
        return t("Changed who may open it from {from} to {to}", { from: audienceLabel(detail.from), to: audienceLabel(detail.to) });
      case "app.env_changed": return t("Changed the environment variables");
      case "app.started": return t("Started the app");
      case "app.stopped": return t("Stopped the app");
      case "app.restarted": return t("Restarted the app");
      case "app.data_cleared": return t("Cleared the app's data");
      case "app.disabled": return detail.reason ? t("Took the app down: {reason}", { reason: detail.reason }) : t("Took the app down");
      case "app.enabled": return t("Restored the app");
      case "app.anyone_approved": return t("Approved opening it to anyone");
      case "app.anyone_revoked": return t("Withdrew the approval to open it to anyone");
      case "app.owner_changed": return t("Transferred the app from {from} to {to}", { from: detail.from || "?", to: detail.to || "?" });
      case "app.owner_inactive": return t("Switched off: its owner lost access to the Portal");
      case "app.owner_active": return t("Switched on again: its owner has access again");
      case "deploy.requested": return detail.ref ? t("Asked for a deployment of {ref}", { ref: detail.ref }) : t("Asked for a deployment");
      case "deploy.succeeded": return t("Deployed {sha}", { sha: shortSha(detail.sha) || "?" });
      case "deploy.failed": return detail.error ? t("A deployment failed: {error}", { error: detail.error }) : t("A deployment failed");
      case "rollback.succeeded": return t("Rolled back to {sha}", { sha: shortSha(detail.sha) || "?" });
      case "rollback.failed": return detail.error ? t("A rollback failed: {error}", { error: detail.error }) : t("A rollback failed");
      case "token.viewed": return t("Looked at the app token");
      case "token.reset": return t("Reset the app token");
      case "app.data_purged": return t("Removed the data the deleted app had left on the host");
      case "template.changed":
        return detail.is_template ? t("Offered the app as a template") : t("Stopped offering the app as a template");
      case "template.version_published": return t("Published version {tag}", { tag: detail.tag || "?" });
      case "template.version_removed": return t("Removed version {tag}", { tag: detail.tag || "?" });
      case "host.crashed": return t("The app crashed");
      case "host.killed": return t("The host stopped the app");
      case "host.idle_stopped": return t("Stopped after a while without visitors");
      case "host.started": return t("Started for a visitor");
      case "host.quota_exceeded": return t("The app's data went over its quota");
      default: return action;
    }
  }

  // ==========================================================================
  // The pages. Everything from here on needs the document.
  // ==========================================================================

  // /api/apps/settings: the public address, the audiences on offer, and the
  // account that reads repositories. Asked once per page load.
  const settingsState = { value: null, loading: null };

  function loadSettings() {
    if (settingsState.value) return Promise.resolve(settingsState.value);
    if (!settingsState.loading) {
      settingsState.loading = request(API + "/settings").then(function (value) {
        settingsState.value = value;
        settingsState.loading = null;
        return value;
      }, function (error) {
        settingsState.loading = null;
        throw error;
      });
    }
    return settingsState.loading;
  }

  function appIconHtml(app, extraClass) {
    const cls = "portal-app-icon" + (extraClass ? " " + extraClass : "");
    const initial = esc(appInitial(app && app.name));
    const icon = safeUrl(app && app.icon_url);
    if (icon) {
      // The letter is the fallback when the image does not load (see the error listener).
      return '<span class="' + cls + '" data-app-initial="' + initial + '" aria-hidden="true"><img src="' + esc(icon) + '" alt="" loading="lazy" /></span>';
    }
    return '<span class="' + cls + '" aria-hidden="true">' + initial + "</span>";
  }

  function stateBadgeHtml(state) {
    return '<span class="portal-status-badge is-' + esc(state.tone) + '">' + esc(state.label) + "</span>";
  }

  function loadingHtml(text) {
    return '<div class="portal-inline-state is-visible">' + esc(text) + "</div>";
  }

  function errorHtml(message, retryAttribute) {
    const retry = retryAttribute
      ? ' <button type="button" class="portal-btn portal-apps-inline-btn" ' + retryAttribute + ">" + esc(t("Try again")) + "</button>"
      : "";
    return '<div class="portal-inline-state is-error portal-apps-error"><span>' + esc(message) + "</span>" + retry + "</div>";
  }

  function emptyHtml(icon, title, text, actionsHtml) {
    return (
      '<div class="portal-apps-empty">' +
      '<div class="portal-apps-empty-icon" aria-hidden="true"><i data-lucide="' + esc(icon) + '" class="w-6 h-6"></i></div>' +
      "<h3>" + esc(title) + "</h3>" +
      "<p>" + esc(text) + "</p>" +
      (actionsHtml ? '<div class="portal-apps-empty-actions">' + actionsHtml + "</div>" : "") +
      "</div>"
    );
  }

  function ownerName(app) {
    const owner = (app && app.owner) || {};
    return owner.display_name || owner.username || "";
  }

  // ---- the app center -------------------------------------------------------

  const VIEW_TITLES = { mine: N_("My apps"), available: N_("Available to me"), templates: N_("Templates") };
  const CENTER_REFRESH_MS = 30000;

  const center = {
    root: null,
    visible: false,
    requested: "",
    view: "mine",
    query: "",
    lists: { mine: null, available: null, templates: null },
    errors: { mine: "", available: "", templates: "" },
    seq: 0,
    // Counts the times the center was opened: an opening that is no longer
    // the newest one stops where it is (showCenter).
    shown: 0,
    timer: 0,
    drawn: "",
  };

  // With no list named in the address: my apps when I have any, else what is
  // shared with me, else my (empty) apps and how to make the first one.
  function chooseView(lists) {
    if (lists.mine && lists.mine.length) return "mine";
    if (lists.available && lists.available.length) return "available";
    return "mine";
  }

  function appCardHtml(app) {
    const state = app.can_manage ? appState(app, null) : null;
    const url = safeUrl(app.url);
    const launchable = Boolean(url) && Boolean(app.deployed) && app.enabled !== false;
    const copy =
      appIconHtml(app) +
      '<span class="portal-app-card-copy"><strong>' + esc(app.name) + "</strong>" +
      "<small>" + esc(app.description || t("No description.")) + "</small></span>";
    let main;
    if (launchable) {
      main = '<a class="portal-app-card-main" href="' + esc(url) + '" target="_blank" rel="noopener" data-app-launch="' + esc(app.id) +
        '" title="' + esc(t("Open {name} in a new tab", { name: app.name })) + '">' + copy + "</a>";
    } else if (app.can_manage) {
      main = '<button type="button" class="portal-app-card-main" data-app-manage="' + esc(app.id) +
        '" title="' + esc(t("Open the page of {name}", { name: app.name })) + '">' + copy + "</button>";
    } else {
      main = '<div class="portal-app-card-main">' + copy + "</div>";
    }
    let stateLine = "";
    if (state) {
      const words = state.lastDeployFailed
        ? t("The last deployment failed; the release before it keeps running.")
        : state.reason;
      stateLine = '<div class="portal-app-card-state">' + stateBadgeHtml(state) + "<span>" + esc(words) + "</span></div>";
    }
    const meta = [];
    if (!app.is_owner && ownerName(app)) {
      meta.push('<span title="' + esc(t("Owner")) + '"><i data-lucide="user-round" class="w-4 h-4"></i>' + esc(ownerName(app)) + "</span>");
    }
    meta.push(
      '<span title="' + esc(t("Who may open it")) + '"><i data-lucide="users" class="w-4 h-4"></i>' +
      esc(audienceLabel(app.effective_visibility || app.visibility)) + "</span>"
    );
    const actions = [];
    if (app.can_manage) {
      actions.push('<button type="button" class="portal-btn" data-app-manage="' + esc(app.id) + '">' + esc(t("Manage")) + "</button>");
    }
    if (url) {
      actions.push(
        '<button type="button" class="portal-btn portal-app-icon-btn" data-app-copy-link="' + esc(url) + '" title="' + esc(t("Copy link")) +
        '" aria-label="' + esc(t("Copy the link of {name}", { name: app.name })) + '"><i data-lucide="link" class="w-4 h-4"></i></button>'
      );
    }
    const attention = state && (state.attention || state.lastDeployFailed);
    return (
      '<article class="portal-app-card' + (attention ? " needs-attention" : "") + '" data-app-card="' + esc(app.id) + '">' +
      main + stateLine +
      '<div class="portal-app-card-foot"><span class="portal-app-card-meta">' + meta.join("") + "</span>" +
      '<span class="portal-app-card-actions">' + actions.join("") + "</span></div>" +
      "</article>"
    );
  }

  function templateCardHtml(item) {
    const latest = item.latest_version || null;
    const copy =
      appIconHtml(item) +
      '<span class="portal-app-card-copy"><strong>' + esc(item.name) + "</strong>" +
      "<small>" + esc(item.description || t("No description.")) + "</small></span>";
    const meta = [];
    if (ownerName(item)) {
      meta.push('<span title="' + esc(t("Owner")) + '"><i data-lucide="user-round" class="w-4 h-4"></i>' + esc(ownerName(item)) + "</span>");
    }
    if (latest && latest.tag) {
      meta.push('<span title="' + esc(t("Latest version")) + '"><i data-lucide="tag" class="w-4 h-4"></i>' + esc(latest.tag) + "</span>");
    }
    return (
      '<article class="portal-app-card" data-template-card="' + esc(item.id) + '">' +
      '<button type="button" class="portal-app-card-main" data-template-use="' + esc(item.id) + '" title="' +
      esc(t("Start an app of your own from {name}", { name: item.name })) + '">' + copy + "</button>" +
      '<div class="portal-app-card-foot"><span class="portal-app-card-meta">' + meta.join("") + "</span>" +
      '<span class="portal-app-card-actions"><button type="button" class="portal-btn" data-template-use="' + esc(item.id) + '">' +
      esc(t("Use template")) + "</button></span></div>" +
      "</article>"
    );
  }

  function centerEmptyHtml(view) {
    if (view === "available") {
      return emptyHtml(
        "users",
        t("Nothing is shared with you yet"),
        t("Apps that other members open to your group or to every member appear here, the ones you used last first."),
        ""
      );
    }
    if (view === "templates") {
      return emptyHtml(
        "layout-template",
        t("No templates yet"),
        t("An owner can offer an app as a template from the app's Settings. Other members then start their own app from it."),
        ""
      );
    }
    return emptyHtml(
      "blocks",
      t("You have no apps yet"),
      t("An app is a small web app kept in a GitHub repository. Publish your own, or start from a template another member published."),
      '<button type="button" class="portal-btn is-primary" data-app-create-open>' + esc(t("New app")) + "</button>" +
      '<button type="button" class="portal-btn" data-apps-view-open="templates">' + esc(t("Browse templates")) + "</button>" +
      '<a class="portal-btn" href="#/help/building-apps">' + esc(t("How to build one")) + "</a>"
    );
  }

  function drawCenterFrame() {
    if (!center.root) return;
    center.root.innerHTML =
      '<div class="portal-panel-stack portal-apps-center" data-apps-center>' +
      '<div class="portal-apps-toolbar">' +
      '<h2 class="portal-apps-heading" data-apps-heading></h2>' +
      '<label class="portal-search-field portal-apps-search"><i data-lucide="search" class="w-4 h-4"></i>' +
      '<input type="search" data-apps-search placeholder="' + esc(t("Search apps")) + '" aria-label="' + esc(t("Search apps")) +
      '" autocomplete="off" value="' + esc(center.query) + '" /></label>' +
      '<span class="portal-apps-count" data-apps-count aria-live="polite"></span>' +
      "</div>" +
      '<div class="portal-apps-body" data-apps-body></div>' +
      "</div>";
    center.drawn = "";
    drawIcons(center.root);
  }

  function updateNavCounts() {
    document.querySelectorAll("[data-apps-view-count]").forEach(function (node) {
      const list = center.lists[node.dataset.appsViewCount];
      node.textContent = list ? String(list.length) : "";
    });
  }

  function drawCenterBody(force) {
    const root = center.root;
    const body = root && root.querySelector("[data-apps-body]");
    if (!body) return;
    updateNavCounts();
    const all = center.lists[center.view];
    const signature = JSON.stringify([center.view, center.query, all, center.errors[center.view]]);
    if (!force && signature === center.drawn) return;
    center.drawn = signature;

    const heading = root.querySelector("[data-apps-heading]");
    if (heading) heading.textContent = tv(VIEW_TITLES[center.view]);
    const count = root.querySelector("[data-apps-count]");
    if (all === null) {
      if (count) count.textContent = "";
      body.innerHTML = center.errors[center.view]
        ? errorHtml(center.errors[center.view], "data-apps-retry")
        : loadingHtml(t("Loading apps\u2026"));
      return;
    }
    const items = all.filter(function (item) { return matchesQuery(item, center.query); });
    if (count) {
      if (center.query.trim()) count.textContent = t("{shown} of {total}", { shown: items.length, total: all.length });
      else if (center.view === "templates") count.textContent = tn(all.length, "{n} template", "{n} templates");
      else count.textContent = tn(all.length, "{n} app", "{n} apps");
    }
    if (!all.length) {
      body.innerHTML = centerEmptyHtml(center.view);
    } else if (!items.length) {
      body.innerHTML = '<div class="portal-panel-empty">' + esc(t('Nothing here matches "{query}".', { query: center.query.trim() })) + "</div>";
    } else {
      const card = center.view === "templates" ? templateCardHtml : appCardHtml;
      body.innerHTML = '<div class="portal-app-grid">' + items.map(card).join("") + "</div>";
    }
    drawIcons(body);
  }

  async function refreshCenter() {
    const seq = ++center.seq;
    const results = await Promise.allSettled([
      request(API + "?scope=mine"),
      request(API + "?scope=available"),
      request(API + "/templates"),
    ]);
    if (seq !== center.seq) return;
    const names = ["mine", "available", "templates"];
    results.forEach(function (result, index) {
      const name = names[index];
      if (result.status === "fulfilled") {
        const value = result.value || {};
        const rows = name === "templates" ? value.templates : value.apps;
        const list = Array.isArray(rows) ? rows : [];
        center.lists[name] = name === "mine" ? sortMine(list) : name === "available" ? sortAvailable(list) : list.slice().sort(byName);
        center.errors[name] = "";
      } else if (center.lists[name] === null) {
        const error = result.reason || {};
        // A Portal without the templates routes yet simply has none to show.
        if (name === "templates" && error.status === 404) center.lists[name] = [];
        else center.errors[name] = error.message || t("Request failed");
      }
    });
  }

  // The counts beside the three lists, kept right while an app's page is open
  // (an app was just created or deleted, say).
  async function refreshNavCounts() {
    await refreshCenter();
    updateNavCounts();
  }

  function stopCenter() {
    center.visible = false;
    center.seq += 1;
    if (center.timer) window.clearInterval(center.timer);
    center.timer = 0;
  }

  async function showCenter(container, options) {
    stopPage();
    stopCenter();
    // One address can reach here twice at once (Back fires popstate and
    // hashchange, and the shell applies both): only the newest opening goes
    // on after the lists are read, so only one refresh timer is ever started.
    const shown = ++center.shown;
    center.root = container;
    center.visible = true;
    center.requested = options && VIEW_TITLES[options.view] ? options.view : "";
    if (center.requested) center.view = center.requested;
    const firstLoad = center.lists.mine === null && center.lists.available === null;
    drawCenterFrame();
    drawCenterBody(true);
    await refreshCenter();
    if (shown !== center.shown || !center.visible || center.root !== container) return;
    // Chosen once the lists are known; a later refresh never moves the member.
    if (!center.requested && (firstLoad || !VIEW_TITLES[center.view])) center.view = chooseView(center.lists);
    if (shell()) shell().markView(center.view);
    drawCenterBody(true);
    if (center.timer) window.clearInterval(center.timer);
    center.timer = window.setInterval(async function () {
      if (!center.visible || document.hidden) return;
      await refreshCenter();
      if (center.visible) drawCenterBody(false);
    }, CENTER_REFRESH_MS);
  }

  // ---- New app (partials/app_create_dialog.html) ----------------------------

  const INSPECT_DELAY_MS = 500;
  const APP_TYPE_LABELS = {
    fastapi: "FastAPI",
    flask: "Flask",
    streamlit: "Streamlit",
    gradio: "Gradio",
    command: N_("Custom command"),
    static: N_("Static site"),
  };
  // Errors of POST /api/apps that are about the repository, not about the form.
  const REPOSITORY_ERROR_CODES = [
    "repo_not_readable", "not_listed_as_owner", "manifest_invalid", "manifest_missing",
    "ref_not_found", "repo_url_invalid", "entry_dir_invalid", "ref_invalid",
    "github_unreachable", "github_auth_failed", "source_error", "file_not_found", "bundle_too_large",
  ];

  const dialog = {
    seq: 0,
    timer: 0,
    inspection: null,
    problem: null,
    checkedKey: "",
    refsFor: "",
    repoSeen: "",
    refAuto: "",
    nameAuto: "",
    templateNameAuto: "",
    submitting: false,
    trigger: null,
    templates: null,
    versions: {},
    versionSeq: 0,
  };

  function appTypeLabel(type) {
    return Object.prototype.hasOwnProperty.call(APP_TYPE_LABELS, type) ? tv(APP_TYPE_LABELS[type]) : String(type || "");
  }

  function createModal() {
    return document.getElementById("app-create-modal");
  }

  function createField(name) {
    const form = document.getElementById("app-create-form");
    return form ? form.elements[name] : null;
  }

  function createValue(name) {
    const field = createField(name);
    return field ? String(field.value || "").trim() : "";
  }

  function createMode() {
    return createValue("app_source") === "template" ? "template" : "repository";
  }

  function inspectKey() {
    return [createValue("repo_url"), createValue("ref"), createValue("entry_dir")].join("\n");
  }

  function setCreateMessage(text, isError) {
    const node = document.getElementById("app-create-msg");
    if (!node) return;
    node.textContent = text || "";
    node.classList.toggle("is-error", Boolean(isError));
  }

  function syncCreateSubmit() {
    const modal = createModal();
    const button = modal && modal.querySelector("[data-app-create-submit]");
    if (!button) return;
    let ready;
    if (createMode() === "repository") {
      ready = Boolean(dialog.inspection) && dialog.checkedKey === inspectKey();
    } else {
      ready = Boolean(createValue("template_app_id") && createValue("version_id") && createValue("template_name"));
    }
    button.disabled = !ready || dialog.submitting;
    // While the app is being created nothing closes the dialog
    // (closeCreateDialog), and its two ways out say so.
    Array.prototype.forEach.call(modal.querySelectorAll("[data-app-create-close]"), function (closer) {
      closer.disabled = dialog.submitting;
    });
  }

  function inspectBox() {
    const modal = createModal();
    return modal ? modal.querySelector("[data-app-inspect]") : null;
  }

  function drawInspect(html) {
    const box = inspectBox();
    if (!box) return;
    box.innerHTML = html;
    drawIcons(box);
  }

  function inspectNoteHtml(icon, lines) {
    return (
      '<div class="portal-app-inspect-note"><i data-lucide="' + esc(icon) + '" class="w-4 h-4"></i><div>' +
      lines.map(function (line) { return "<p>" + esc(line) + "</p>"; }).join("") +
      "</div></div>"
    );
  }

  function drawInspectIdle() {
    const settings = settingsState.value;
    const lines = [t("Paste the repository's address. The Portal reads efp-app.json there and shows what it found.")];
    if (settings && settings.service_account) {
      lines.push(t("Repositories are read as {account} on {host}. A private repository needs that account as a read-only collaborator.", {
        account: settings.service_account,
        host: settings.github_host || "github.com",
      }));
    }
    drawInspect(inspectNoteHtml("info", lines));
  }

  function drawInspectChecking() {
    drawInspect(
      '<div class="portal-app-inspect-note is-busy"><span class="portal-app-spinner" aria-hidden="true"></span><div><p>' +
      esc(t("Reading the repository\u2026")) + "</p></div></div>"
    );
  }

  // The address the app will get. The server makes it of the name; a name it
  // cannot make an address of gets a generated one, which is not known yet.
  function previewAddress() {
    const settings = settingsState.value || {};
    const base = String(settings.public_base_url || "").replace(/\/+$/, "");
    if (!base) return t("This Portal has no public address for apps yet. Ask an administrator.");
    const inspection = dialog.inspection;
    const typed = createValue("name");
    let slug = inspection ? String(inspection.suggested_slug || "") : "";
    if (typed && inspection && typed !== inspection.suggested_name) {
      const own = slugify(typed);
      const reserved = Array.isArray(settings.reserved_slugs) ? settings.reserved_slugs : [];
      slug = /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(own) && reserved.indexOf(own) < 0 ? own : "";
    }
    return slug ? base + "/" + slug + "/" : t("{base}/ followed by a name the Portal makes for it", { base: base });
  }

  function syncAddressPreview() {
    const box = inspectBox();
    const node = box && box.querySelector("[data-app-address]");
    if (node) node.textContent = previewAddress();
  }

  function drawInspectFound(inspection) {
    const manifest = inspection.manifest || {};
    const message = String(inspection.commit_message || "").split("\n")[0];
    drawInspect(
      '<div class="portal-app-inspect-found">' +
      '<div class="portal-app-inspect-title"><i data-lucide="circle-check" class="w-4 h-4"></i><strong>' + esc(manifest.name || inspection.suggested_name || "") + "</strong>" +
      '<span class="portal-admin-role-badge">' + esc(appTypeLabel(manifest.type)) + "</span></div>" +
      (manifest.description ? "<p>" + esc(manifest.description) + "</p>" : "") +
      '<dl class="portal-app-facts is-compact">' +
      "<dt>" + esc(t("Address")) + '</dt><dd data-app-address></dd>' +
      "<dt>" + esc(t("Commit")) + "</dt><dd><code>" + esc(shortSha(inspection.commit_sha)) + "</code> " + esc(message) + "</dd>" +
      "<dt>" + esc(t("Read from")) + "</dt><dd>" + esc(inspection.repo || "") + " \u00b7 " + esc(inspection.ref || "") + " \u00b7 " +
      esc(inspection.entry_dir || t("top of the repository")) + "</dd>" +
      "</dl></div>"
    );
    syncAddressPreview();
  }

  function drawInspectProblem(problem) {
    const list = problem.list.length
      ? "<ul>" + problem.list.map(function (line) { return "<li>" + esc(line) + "</li>"; }).join("") + "</ul>"
      : "";
    let steps = "";
    if (problem.steps.length > 1) {
      steps = "<ol>" + problem.steps.map(function (line) { return "<li>" + esc(line) + "</li>"; }).join("") + "</ol>";
    } else if (problem.steps.length === 1) {
      steps = "<p>" + esc(problem.steps[0]) + "</p>";
    }
    const snippet = problem.snippet
      ? '<div class="portal-app-snippet"><code>' + esc(problem.snippet) + '</code><button type="button" class="portal-btn portal-apps-inline-btn" data-app-copy-text="' +
        esc(problem.snippet) + '">' + esc(t("Copy")) + "</button></div>"
      : "";
    const account = problem.account
      ? '<div class="portal-app-snippet"><code>' + esc(problem.account) + '</code><button type="button" class="portal-btn portal-apps-inline-btn" data-app-copy-text="' +
        esc(problem.account) + '">' + esc(t("Copy")) + "</button></div>"
      : "";
    drawInspect(
      '<div class="portal-app-inspect-problem">' +
      '<div class="portal-app-inspect-title is-error"><i data-lucide="circle-alert" class="w-4 h-4"></i><strong>' + esc(problem.title) + "</strong></div>" +
      (problem.text ? "<p>" + esc(problem.text) + "</p>" : "") +
      list + steps + snippet + account +
      '<div><button type="button" class="portal-btn portal-apps-inline-btn" data-app-inspect-again>' + esc(t("Check again")) + "</button></div>" +
      "</div>"
    );
  }

  function problemFromError(error) {
    if (error && error.payload) return inspectProblem(error.payload, settingsState.value, error.status);
    return { code: "", title: t("The repository could not be checked"), text: (error && error.message) || t("Request failed"), steps: [], list: [], snippet: "", account: "" };
  }

  function fillRefList(refs) {
    const list = document.getElementById("app-create-refs");
    if (!list) return;
    const names = [];
    (Array.isArray(refs.branches) ? refs.branches : []).concat(Array.isArray(refs.tags) ? refs.tags : []).forEach(function (name) {
      if (names.indexOf(name) < 0) names.push(name);
    });
    list.innerHTML = names.map(function (name) { return '<option value="' + esc(name) + '"></option>'; }).join("");
  }

  async function runInspect() {
    const seq = dialog.seq;
    const repo = createValue("repo_url");
    const entryDir = createValue("entry_dir");
    try {
      if (dialog.refsFor !== repo) {
        // The repository's branches and tags fill the field's list; its
        // default branch fills the field itself while that is still empty.
        const refs = await request(API + "/repo-refs?repo_url=" + encodeURIComponent(repo));
        if (seq !== dialog.seq) return;
        dialog.refsFor = repo;
        fillRefList(refs || {});
        const refField = createField("ref");
        if (refField && !refField.value.trim() && refs && refs.default_branch) {
          refField.value = refs.default_branch;
          dialog.refAuto = refs.default_branch;
        }
      }
      const ref = createValue("ref");
      if (!plausibleRef(ref)) {
        drawInspect(inspectNoteHtml("info", [t("Enter the branch or tag to deploy.")]));
        return;
      }
      const inspection = await request(API + "/inspect", { method: "POST", json: { repo_url: repo, ref: ref, entry_dir: entryDir } });
      if (seq !== dialog.seq) return;
      dialog.inspection = inspection;
      dialog.problem = null;
      dialog.checkedKey = inspectKey();
      const nameField = createField("name");
      if (nameField && (!nameField.value.trim() || nameField.value === dialog.nameAuto)) {
        nameField.value = inspection.suggested_name || "";
        dialog.nameAuto = nameField.value;
      }
      drawInspectFound(inspection);
    } catch (error) {
      if (seq !== dialog.seq) return;
      dialog.inspection = null;
      dialog.problem = problemFromError(error);
      drawInspectProblem(dialog.problem);
    } finally {
      if (seq === dialog.seq) syncCreateSubmit();
    }
  }

  // Called on every change of the first three fields: what was found no
  // longer counts, and a new look starts once they are plausible again.
  function scheduleInspect(immediate) {
    window.clearTimeout(dialog.timer);
    dialog.seq += 1;
    dialog.inspection = null;
    dialog.problem = null;
    dialog.checkedKey = "";
    setCreateMessage("");
    const repo = createValue("repo_url");
    if (repo !== dialog.repoSeen) {
      // Another repository: the branch and the name the Portal filled in for
      // the last one go with it, what the member typed stays.
      dialog.repoSeen = repo;
      const refField = createField("ref");
      if (refField && dialog.refAuto && refField.value === dialog.refAuto) refField.value = "";
      const nameField = createField("name");
      if (nameField && dialog.nameAuto && nameField.value === dialog.nameAuto) nameField.value = "";
      dialog.refAuto = "";
      dialog.nameAuto = "";
    }
    syncCreateSubmit();
    if (!repo) {
      drawInspectIdle();
      return;
    }
    if (!plausibleRepoUrl(repo)) {
      const host = (settingsState.value && settingsState.value.github_host) || "github.com";
      drawInspect(inspectNoteHtml("info", [t("Enter the repository's full address, for example https://{host}/owner/repository.", { host: host })]));
      return;
    }
    if (!plausibleEntryDir(createValue("entry_dir"))) {
      drawInspect(inspectNoteHtml("info", [t("The entry directory is a path inside the repository, for example apps/mindmap.")]));
      return;
    }
    drawInspectChecking();
    dialog.timer = window.setTimeout(runInspect, immediate ? 0 : INSPECT_DELAY_MS);
  }

  // ---- New app: from a template ----

  function templateInfoBox() {
    const modal = createModal();
    return modal ? modal.querySelector("[data-app-template-info]") : null;
  }

  function drawTemplateInfo(html) {
    const box = templateInfoBox();
    if (!box) return;
    box.innerHTML = html;
    drawIcons(box);
  }

  function selectedTemplate() {
    const id = createValue("template_app_id");
    return (dialog.templates || []).filter(function (item) { return item.id === id; })[0] || null;
  }

  function drawSelectedTemplate() {
    const template = selectedTemplate();
    if (!template) {
      const none = dialog.templates && !dialog.templates.length;
      drawTemplateInfo(inspectNoteHtml("info", [
        none
          ? t("No templates have been published yet. An owner offers an app as a template from the app's Settings.")
          : t("Pick a template and one of its versions. Your app starts as a copy of that version, under your name."),
      ]));
      return;
    }
    const versionId = createValue("version_id");
    const version = (dialog.versions[template.id] || []).filter(function (item) { return String(item.id) === versionId; })[0] || null;
    drawTemplateInfo(
      '<div class="portal-app-inspect-found">' +
      '<div class="portal-app-inspect-title"><i data-lucide="layout-template" class="w-4 h-4"></i><strong>' + esc(template.name) + "</strong>" +
      (ownerName(template) ? '<span class="portal-admin-role-badge">' + esc(ownerName(template)) + "</span>" : "") + "</div>" +
      (template.description ? "<p>" + esc(template.description) + "</p>" : "") +
      (version && version.notes ? "<p>" + esc(t("Version {tag}: {notes}", { tag: version.tag, notes: version.notes })) + "</p>" : "") +
      "<p>" + esc(t("Your app stays on the version you pick until you choose to upgrade it. To change its code, fork the repository on GitHub and register your fork as a new app.")) + "</p>" +
      "</div>"
    );
  }

  function fillTemplateSelect(selectedId) {
    const select = createField("template_app_id");
    if (!select) return;
    const options = ['<option value="">' + esc(t("Choose a template")) + "</option>"];
    (dialog.templates || []).forEach(function (item) {
      const label = ownerName(item) ? t("{name} (by {owner})", { name: item.name, owner: ownerName(item) }) : item.name;
      options.push('<option value="' + esc(item.id) + '">' + esc(label) + "</option>");
    });
    select.innerHTML = options.join("");
    select.value = selectedId && (dialog.templates || []).some(function (item) { return item.id === selectedId; }) ? selectedId : "";
  }

  function fillVersionSelect(template) {
    const select = createField("version_id");
    if (!select) return;
    const versions = template ? dialog.versions[template.id] : null;
    if (!template) {
      select.innerHTML = '<option value="">' + esc(t("Choose a template first")) + "</option>";
      select.disabled = true;
    } else if (!versions) {
      select.innerHTML = '<option value="">' + esc(t("Loading\u2026")) + "</option>";
      select.disabled = true;
    } else if (!versions.length) {
      select.innerHTML = '<option value="">' + esc(t("No version has been published")) + "</option>";
      select.disabled = true;
    } else {
      select.innerHTML = versions.map(function (item) {
        return '<option value="' + esc(item.id) + '">' + esc(item.tag) + "</option>";
      }).join("");
      const latest = template.latest_version && template.latest_version.id;
      select.value = versions.some(function (item) { return item.id === latest; }) ? String(latest) : String(versions[0].id);
      select.disabled = false;
    }
  }

  async function onTemplateChosen() {
    const template = selectedTemplate();
    const nameField = createField("template_name");
    if (nameField && (!nameField.value.trim() || nameField.value === dialog.templateNameAuto)) {
      nameField.value = template ? template.name : "";
      dialog.templateNameAuto = nameField.value;
    }
    fillVersionSelect(template);
    drawSelectedTemplate();
    syncCreateSubmit();
    if (!template || dialog.versions[template.id]) return;
    const seq = ++dialog.versionSeq;
    let versions;
    try {
      const data = await request(appPath(template.id, "/template/versions"));
      versions = Array.isArray(data && data.versions) ? data.versions : [];
    } catch (_error) {
      // The list could not be read: the newest version, which the card carries, is still on offer.
      versions = template.latest_version ? [template.latest_version] : [];
    }
    dialog.versions[template.id] = versions;
    if (seq !== dialog.versionSeq) return;
    fillVersionSelect(selectedTemplate());
    drawSelectedTemplate();
    syncCreateSubmit();
  }

  async function showTemplateSource(templateId) {
    drawTemplateInfo(inspectNoteHtml("info", [t("Loading templates\u2026")]));
    try {
      if (!dialog.templates) {
        const data = await request(API + "/templates");
        dialog.templates = Array.isArray(data && data.templates) ? data.templates.slice().sort(byName) : [];
      }
    } catch (error) {
      dialog.templates = dialog.templates || [];
      if (error.status !== 404) setCreateMessage(error.message, true);
    }
    fillTemplateSelect(templateId || createValue("template_app_id"));
    await onTemplateChosen();
  }

  function setCreateMode(mode, templateId) {
    const modal = createModal();
    if (!modal) return;
    const source = createField("app_source");
    if (source) source.value = mode;
    modal.querySelectorAll("[data-app-create-panel]").forEach(function (panel) {
      panel.classList.toggle("hidden", panel.dataset.appCreatePanel !== mode);
    });
    setCreateMessage("");
    if (mode === "template") showTemplateSource(templateId || "");
    syncCreateSubmit();
  }

  function resetCreateDialog() {
    const form = document.getElementById("app-create-form");
    if (form) form.reset();
    window.clearTimeout(dialog.timer);
    dialog.seq += 1;
    dialog.inspection = null;
    dialog.problem = null;
    dialog.checkedKey = "";
    dialog.refsFor = "";
    dialog.repoSeen = "";
    dialog.refAuto = "";
    dialog.nameAuto = "";
    dialog.templateNameAuto = "";
    dialog.submitting = false;
    // Templates are read again each time: one may have been published since.
    dialog.templates = null;
    dialog.versions = {};
    const refs = document.getElementById("app-create-refs");
    if (refs) refs.innerHTML = "";
    setCreateMessage("");
    drawInspectIdle();
  }

  function openCreateDialog(options) {
    const modal = createModal();
    if (!modal) return;
    // An app is being created: the dialog is still open and is not started
    // over, or the same app could be asked for a second time.
    if (dialog.submitting) return;
    dialog.trigger = document.activeElement;
    resetCreateDialog();
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    // The account that reads repositories is part of the first hint.
    loadSettings().then(function () {
      if (!createValue("repo_url") && createMode() === "repository") drawInspectIdle();
    }, function () { /* the hint just stays shorter */ });
    const templateId = options && options.templateId ? options.templateId : "";
    setCreateMode(templateId ? "template" : "repository", templateId);
    window.setTimeout(function () {
      const first = createField(templateId ? "template_name" : "repo_url");
      if (first && typeof first.focus === "function") first.focus();
    }, 0);
  }

  // False when there was nothing to close, and while an app is being created:
  // Escape, Cancel, the cross and a press beside the dialog would only hide
  // it, and the app would be created all the same.
  function closeCreateDialog() {
    const modal = createModal();
    if (!modal || modal.classList.contains("hidden")) return false;
    if (dialog.submitting) return false;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    window.clearTimeout(dialog.timer);
    dialog.seq += 1;
    if (dialog.trigger && typeof dialog.trigger.focus === "function" && document.contains(dialog.trigger)) dialog.trigger.focus();
    dialog.trigger = null;
    return true;
  }

  async function submitCreate() {
    if (dialog.submitting) return;
    const mode = createMode();
    let path;
    let body;
    if (mode === "repository") {
      if (!dialog.inspection || dialog.checkedKey !== inspectKey()) return;
      path = API;
      body = { repo_url: createValue("repo_url"), ref: createValue("ref"), entry_dir: createValue("entry_dir"), name: createValue("name"), deploy: true };
    } else {
      const versionId = createValue("version_id");
      if (!createValue("template_app_id") || !versionId || !createValue("template_name")) return;
      path = API + "/from-template";
      body = {
        template_app_id: createValue("template_app_id"),
        version_id: /^\d+$/.test(versionId) ? Number(versionId) : versionId,
        name: createValue("template_name"),
        deploy: true,
      };
    }
    dialog.submitting = true;
    syncCreateSubmit();
    setCreateMessage(t("Creating the app\u2026"));
    const modal = createModal();
    const submitted = busy(modal && modal.querySelector("[data-app-create-submit]"), t("Creating\u2026"));
    try {
      const created = await request(path, { method: "POST", json: body });
      const app = (created && created.app) || created || {};
      // Created: the dialog may close now.
      dialog.submitting = false;
      closeCreateDialog();
      center.lists.mine = null;
      if (created && created.deploy_error) {
        toast(t("{name} was created, but its first deployment could not start: {error}", { name: app.name, error: errorText(created.deploy_error, 0) }), "error");
      } else {
        toast(t("{name} was created. Its first deployment is running.", { name: app.name }), "success");
      }
      if (shell() && app.id) await shell().openApp(app.id, "deployments");
    } catch (error) {
      setCreateMessage(error.message, true);
      if (mode === "repository" && REPOSITORY_ERROR_CODES.indexOf(error.code) >= 0) {
        dialog.inspection = null;
        dialog.problem = problemFromError(error);
        drawInspectProblem(dialog.problem);
      }
    } finally {
      submitted();
      dialog.submitting = false;
      syncCreateSubmit();
    }
  }

  // ---- an app's own page --------------------------------------------------------

  const APP_TABS = [
    { key: "overview", label: N_("Overview") },
    { key: "deployments", label: N_("Deployments") },
    { key: "logs", label: N_("Logs") },
    { key: "usage", label: N_("Usage") },
    { key: "settings", label: N_("Settings") },
    { key: "data", label: N_("Data") },
    { key: "activity", label: N_("Activity") },
  ];
  const STATUS_REFRESH_MS = 10000;
  const STATUS_REFRESH_BUSY_MS = 3000;
  const RELEASE_POLL_MS = 2000;
  const LOG_TAIL_LINES = 500;
  const LOG_MAX_LINES = 5000;
  // How far the live log may grow past that before its oldest lines go, all
  // in one cut (appendLogLines).
  const LOG_TRIM_LINES = 500;
  const LOG_RECONNECT_MS = 5000;
  const CONNECTOR_NAMES = { jira: "Jira", confluence: "Confluence", github: "GitHub", jenkins: "Jenkins", nexus: "Nexus", splunk: "Splunk" };

  let pageToken = 0;

  function newPage() {
    pageToken += 1;
    return {
      token: pageToken,
      active: false,
      root: null,
      appId: "",
      tab: "overview",
      app: null,
      status: null,
      statusError: "",
      statusTimer: 0,
      busy: false,
      // Which action holds the page busy, for the button that says so.
      busyAction: "",
      overviewDrawn: "",
      // Deployments
      releases: null,
      releasesError: "",
      rollbackReleaseId: "",
      followReleaseId: "",
      shownReleaseId: "",
      // The release the member asked to see (Details): a deployment that is
      // running does not take its place.
      pickedReleaseId: "",
      // The deployment the status last named as running, so that its end is
      // noticed when nobody follows it.
      activeSeenId: "",
      release: null,
      releaseTimer: 0,
      followSeq: 0,
      following: 0,
      announced: {},
      releaseLog: "",
      releaseLogOffset: 0,
      releaseLogLive: false,
      deployRefsLoaded: false,
      // Logs
      log: { lines: [], pending: [], paused: false, stream: null, timer: 0, state: "", note: "" },
      // Usage, Data, Activity
      usageDays: 30,
      // Settings
      // What the server said to the last save of the variables that failed.
      envFailure: "",
      token_value: "",
      tokenShown: false,
      templateTags: null,
      templateVersions: null,
    };
  }

  let page = newPage();

  // One rule for everything on these pages that waits, for an answer of the
  // server or for the member to answer a dialog: the work belongs to the page
  // it started on. It takes that page first (`const owner = page`), builds
  // its addresses from `owner.appId`, keeps what comes back on `owner`, and
  // touches the screen only while `pageLive(owner.token)`. By the time the
  // wait is over `page` may be another app's page: an answer written to it
  // would put one app's settings under the other's name, and an address
  // built from it would act on the app the member is looking at instead of
  // the one they were asked about.
  function pageLive(token) {
    return page.active && page.token === token;
  }

  function tabBody() {
    return page.root ? page.root.querySelector("[data-app-tab-body]") : null;
  }

  function closeLogStream() {
    if (page.log.stream) {
      try {
        page.log.stream.abort();
      } catch (_error) {
        /* already closed */
      }
    }
    page.log.stream = null;
    if (page.log.timer) window.clearTimeout(page.log.timer);
    page.log.timer = 0;
  }

  function stopReleasePolling() {
    if (page.releaseTimer) window.clearTimeout(page.releaseTimer);
    page.releaseTimer = 0;
    page.following = 0;
  }

  // Ends what the page keeps running: the status refresh, a followed
  // deployment and the live log. The token, if it was shown, is forgotten:
  // it was read for this visit of the page and is asked for again on the next.
  function stopPage() {
    page.active = false;
    if (page.statusTimer) window.clearTimeout(page.statusTimer);
    page.statusTimer = 0;
    stopReleasePolling();
    closeLogStream();
    page.token_value = "";
    page.tokenShown = false;
  }

  function pageState() {
    return appState(page.app, page.status);
  }

  function connectorName(type) {
    return CONNECTOR_NAMES[type] || String(type || "");
  }

  // What the main header says over an open app (chat_ui.js, syncMainHeader).
  function heading() {
    if (!page.active || !page.app) return null;
    if (!page.app.can_manage) return { title: page.app.name, status: page.app.description || "" };
    const state = pageState();
    return { title: page.app.name, status: state.reason ? state.label + " \u00b7 " + state.reason : state.label };
  }

  function syncHeader() {
    if (shell()) shell().syncHeader();
    const app = page.app;
    const open = document.getElementById("header-app-open");
    const copy = document.getElementById("header-app-copy-link");
    const deployed = Boolean(app && (page.status ? page.status.deployed : app.deployed));
    const url = app ? safeUrl(app.url) : "";
    if (open) open.disabled = !(url && deployed);
    if (copy) copy.disabled = !url;
  }

  function drawPageError(error) {
    const missing = error && (error.status === 404 || error.status === 403);
    page.root.innerHTML =
      '<div class="portal-panel-stack portal-app-page">' +
      emptyHtml(
        "circle-alert",
        missing ? t("This app is not here") : t("The app could not be loaded"),
        missing ? t("It may have been deleted, or it is not open to you.") : (error && error.message) || t("Request failed"),
        '<button type="button" class="portal-btn is-primary" data-apps-view-open="">' + esc(t("All apps")) + "</button>"
      ) +
      "</div>";
    drawIcons(page.root);
  }

  // Someone who may open the app but not manage it sees what it is and a way in.
  function drawVisitorPage() {
    const app = page.app;
    const url = safeUrl(app.url);
    const launchable = Boolean(url) && Boolean(app.deployed) && app.enabled !== false;
    page.root.innerHTML =
      '<div class="portal-panel-stack portal-app-page" data-app-page="' + esc(app.id) + '">' +
      '<div class="portal-app-page-bar"><button type="button" class="portal-btn portal-app-back" data-apps-view-open=""><i data-lucide="arrow-left" class="w-4 h-4"></i><span>' +
      esc(t("All apps")) + "</span></button></div>" +
      '<section class="portal-panel-section portal-app-visitor">' +
      '<div class="portal-app-visitor-head">' + appIconHtml(app, "is-large") + "<div><strong>" + esc(app.name) + "</strong><p>" +
      esc(app.description || t("No description.")) + "</p></div></div>" +
      '<dl class="portal-app-facts">' +
      "<dt>" + esc(t("Owner")) + "</dt><dd>" + esc(ownerName(app)) + "</dd>" +
      "<dt>" + esc(t("Who may open it")) + "</dt><dd>" + esc(audienceLabel(app.effective_visibility || app.visibility)) + "</dd>" +
      "</dl>" +
      "<p class=\"portal-panel-note\">" + esc(t("Only its owner and administrators can change this app.")) + "</p>" +
      (launchable
        ? '<div><a class="portal-btn is-primary" href="' + esc(url) + '" target="_blank" rel="noopener"><i data-lucide="external-link" class="w-4 h-4"></i><span>' +
          esc(t("Open in a new tab")) + "</span></a></div>"
        : "") +
      "</section></div>";
    drawIcons(page.root);
  }

  function drawPageFrame() {
    const app = page.app;
    if (!app.can_manage) {
      drawVisitorPage();
      return;
    }
    const tabs = APP_TABS.map(function (tab) {
      return '<button type="button" role="tab" id="app-tab-' + tab.key + '" data-app-tab="' + tab.key + '" aria-selected="false" tabindex="-1">' + esc(tv(tab.label)) + "</button>";
    }).join("");
    page.root.innerHTML =
      '<div class="portal-panel-stack portal-app-page" data-app-page="' + esc(app.id) + '">' +
      '<div class="portal-app-page-bar">' +
      '<button type="button" class="portal-btn portal-app-back" data-apps-view-open=""><i data-lucide="arrow-left" class="w-4 h-4"></i><span>' + esc(t("All apps")) + "</span></button>" +
      '<div class="portal-app-tabs" role="tablist" aria-label="' + esc(t("Parts of this app's page")) + '">' + tabs + "</div>" +
      "</div>" +
      '<div class="portal-app-tab-body" data-app-tab-body role="tabpanel"></div>' +
      "</div>";
    drawIcons(page.root);
  }

  function selectTab(tab) {
    if (!page.root || !page.app || !page.app.can_manage) return;
    // Leaving a part ends what only that part follows.
    closeLogStream();
    stopReleasePolling();
    page.tab = tab;
    page.overviewDrawn = "";
    // The open part is on the page's root for the stylesheet: Logs fills the pane.
    const wrap = page.root.querySelector("[data-app-page]");
    if (wrap) wrap.dataset.appPart = tab;
    page.root.querySelectorAll("[data-app-tab]").forEach(function (button) {
      const selected = button.dataset.appTab === tab;
      button.setAttribute("aria-selected", selected ? "true" : "false");
      button.setAttribute("tabindex", selected ? "0" : "-1");
    });
    const body = tabBody();
    if (body) body.setAttribute("aria-labelledby", "app-tab-" + tab);
    if (tab === "deployments") drawDeployments();
    else if (tab === "logs") drawLogs();
    else if (tab === "usage") drawUsage();
    else if (tab === "settings") drawSettings();
    else if (tab === "data") drawData();
    else if (tab === "activity") drawActivity();
    else drawOverview();
  }

  // The app as an answer gives it, kept on the page that asked. The
  // variables are read from the single-app answer (GET /api/apps/{id}); an
  // answer that leaves them out (a list never has to carry them) leaves the
  // page the ones it has.
  function keepApp(owner, app) {
    const had = owner.app && owner.app.manage ? owner.app.manage.env : undefined;
    if (app && app.manage && (app.manage.env === undefined || app.manage.env === null) && had !== undefined) app.manage.env = had;
    owner.app = app;
  }

  async function reloadApp() {
    const owner = page;
    try {
      const app = await request(appPath(owner.appId));
      if (!pageLive(owner.token)) return;
      owner.app = app;
    } catch (_error) {
      /* the page keeps what it has; the next refresh tries again */
    }
  }

  async function refreshStatus() {
    const owner = page;
    if (!owner.app || !owner.app.can_manage) return;
    try {
      const status = await request(appPath(owner.appId, "/status"));
      if (!pageLive(owner.token)) return;
      owner.status = status;
      owner.statusError = "";
    } catch (error) {
      if (!pageLive(owner.token)) return;
      owner.statusError = error.message;
    }
    onStatusChanged();
  }

  function onStatusChanged() {
    syncHeader();
    syncSettingsHold();
    if (page.tab === "overview") drawOverview();
    if (page.tab === "deployments") {
      syncDeployControls();
      const active = page.status && page.status.deploying ? page.status.active_release_id : "";
      if (active) {
        page.activeSeenId = active;
        // A deployment started somewhere else (another tab, another
        // administrator) is followed too, unless the member is reading a
        // release they picked: that one stays until they pick another.
        if (!page.pickedReleaseId && !(page.following && page.shownReleaseId === active)) followRelease(active);
      } else if (page.activeSeenId) {
        const ended = page.activeSeenId;
        page.activeSeenId = "";
        if (!page.announced[ended] && !(page.following && page.shownReleaseId === ended)) releaseEndedUnfollowed(ended);
      }
    }
  }

  // A deployment that ended while the member was reading another release:
  // said, and the history drawn again, as when it is followed to its end.
  async function releaseEndedUnfollowed(releaseId) {
    const owner = page;
    try {
      const release = await request(appPath(owner.appId, "/releases/" + encodeURIComponent(releaseId)));
      if (!pageLive(owner.token) || releaseIsActive(release)) return;
      onReleaseEnded(release);
    } catch (_error) {
      if (pageLive(owner.token) && owner.tab === "deployments") loadReleases();
    }
  }

  function scheduleStatus() {
    if (page.statusTimer) window.clearTimeout(page.statusTimer);
    const token = page.token;
    const key = page.app && page.app.can_manage ? pageState().key : "";
    const delay = key === "deploying" || key === "starting" ? STATUS_REFRESH_BUSY_MS : STATUS_REFRESH_MS;
    page.statusTimer = window.setTimeout(async function () {
      if (!pageLive(token)) return;
      if (!document.hidden) await refreshStatus();
      if (pageLive(token)) scheduleStatus();
    }, delay);
  }

  async function showApp(container, options) {
    stopCenter();
    const appId = String((options && options.appId) || "");
    const wanted = options && options.tab;
    const tab = APP_TABS.some(function (item) { return item.key === wanted; }) ? wanted : "overview";
    if (page.active && page.appId === appId && page.root === container && container.querySelector("[data-app-page]")) {
      if (page.tab !== tab) selectTab(tab);
      return;
    }
    stopPage();
    page = newPage();
    page.active = true;
    page.root = container;
    page.appId = appId;
    page.tab = tab;
    const token = page.token;
    container.innerHTML = loadingHtml(t("Loading the app\u2026"));
    loadSettings().catch(function () { /* the parts that need it say so */ });
    let app;
    try {
      // The single-app read: the one answer the variables are taken from.
      app = await request(appPath(appId));
    } catch (error) {
      if (!pageLive(token)) return;
      drawPageError(error);
      syncHeader();
      return;
    }
    if (!pageLive(token)) return;
    page.app = app;
    drawPageFrame();
    syncHeader();
    // The side pane marks the list this app is in; an administrator looking
    // after someone else's app is in none of the three.
    if (shell()) shell().markView(page.app.is_owner ? "mine" : page.app.can_manage ? "none" : "available");
    refreshNavCounts();
    if (!page.app.can_manage) return;
    selectTab(tab);
    await refreshStatus();
    if (pageLive(token)) scheduleStatus();
  }

  // ---- Overview ----

  function factHtml(label, valueHtml) {
    return "<dt>" + esc(label) + "</dt><dd>" + valueHtml + "</dd>";
  }

  function lifecycleBusyLabel(verb) {
    if (verb === "start") return t("Starting\u2026");
    if (verb === "stop") return t("Stopping\u2026");
    return t("Restarting\u2026");
  }

  function lifecycleButtonHtml(verb, icon, label, disabled, running) {
    // The one that was pressed turns busy: the spinner takes the icon's place
    // and the label stays, so the three buttons stay where they are.
    if (running === verb) {
      return (
        '<button type="button" class="portal-btn is-busy is-busy-icon" data-app-lifecycle="' + verb + '" aria-busy="true" aria-label="' +
        esc(lifecycleBusyLabel(verb)) + '" disabled><span>' + esc(label) + "</span></button>"
      );
    }
    return (
      '<button type="button" class="portal-btn" data-app-lifecycle="' + verb + '"' + (disabled ? " disabled" : "") + ">" +
      '<i data-lucide="' + icon + '" class="w-4 h-4"></i><span>' + esc(label) + "</span></button>"
    );
  }

  function drawOverview() {
    const body = tabBody();
    if (!body || !page.app) return;
    const app = page.app;
    const status = page.status;
    const manage = app.manage || {};
    const state = pageState();
    const signature = JSON.stringify([app, status, page.statusError, page.busy, page.busyAction]);
    if (signature === page.overviewDrawn) return;
    page.overviewDrawn = signature;

    const deployed = status ? Boolean(status.deployed) : Boolean(app.deployed);
    const deploying = state.key === "deploying";
    const enabled = state.key !== "disabled";
    const idle = !page.busy && deployed && enabled && !deploying;
    const notes = [];
    if (page.statusError) {
      notes.push('<p class="portal-app-note is-warning">' + esc(t("The app's state could not be read: {error}", { error: page.statusError })) + "</p>");
    } else if (status && status.host_reachable === false) {
      const when = formatAgo(status.reported_at);
      notes.push(
        '<p class="portal-app-note is-warning">' +
        esc(when
          ? t("The app host cannot be reached right now. This is what it reported {when}.", { when: when })
          : t("The app host cannot be reached right now, and it has not reported on this app yet.")) +
        "</p>"
      );
    }
    if (state.lastDeployFailed) {
      notes.push(
        '<p class="portal-app-note is-error">' + esc(t("The last deployment failed; the release before it keeps running.")) +
        ' <button type="button" class="portal-app-link" data-app-tab-open="deployments">' + esc(t("See what failed")) + "</button></p>"
      );
    }
    if (deploying || state.key === "deploy_failed") {
      notes.push(
        '<p class="portal-app-note"><button type="button" class="portal-app-link" data-app-tab-open="deployments">' +
        esc(deploying ? t("Follow the deployment") : t("See what failed")) + "</button></p>"
      );
    }

    const callouts = [];
    if (!deployed && !deploying && enabled) {
      callouts.push(
        '<div class="portal-app-callout"><span>' + esc(t("Deploy {ref} to bring the app to its address.", { ref: manage.repo_ref || "" })) + "</span>" +
        stateButtonHtml("deploy", "is-primary", "data-app-deploy-latest", t("Deploy"), t("Starting\u2026"), runningAction()) + "</div>"
      );
    }
    const behind = status && typeof status.commits_behind === "number" ? status.commits_behind : 0;
    if (deployed && behind > 0 && !deploying && enabled) {
      callouts.push(
        '<div class="portal-app-callout"><span>' +
        esc(tn(behind, "{n} commit behind {ref}", "{n} commits behind {ref}", { ref: (status && status.repo_ref) || manage.repo_ref || "" })) + "</span>" +
        stateButtonHtml("deploy", "is-primary", "data-app-deploy-latest", t("Deploy latest"), t("Starting\u2026"), runningAction()) + "</div>"
      );
    }
    const template = app.template || null;
    if (template && template.update_available && !deploying && enabled) {
      callouts.push(
        '<div class="portal-app-callout"><span>' +
        esc(t("Version {tag} of {template} is available. This app is on {current}.", {
          tag: template.update_available.tag,
          template: template.app_name || t("the template"),
          current: (template.version && template.version.tag) || "?",
        })) + "</span>" +
        stateButtonHtml("upgrade", "is-primary", 'data-app-upgrade="' + esc(template.update_available.id) + '"', t("Upgrade"), t("Starting\u2026"), runningAction()) + "</div>"
      );
    }

    const facts = [];
    const url = safeUrl(app.url);
    if (url) {
      facts.push(factHtml(
        t("Address"),
        '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + "</a> " +
        '<button type="button" class="portal-btn portal-app-icon-btn" data-app-copy-link="' + esc(url) + '" title="' + esc(t("Copy link")) + '" aria-label="' + esc(t("Copy link")) +
        '"><i data-lucide="copy" class="w-4 h-4"></i></button>'
      ));
    } else {
      facts.push(factHtml(t("Address"), esc(t("This Portal has no public address for apps yet. Ask an administrator."))));
    }
    let audience = esc(audienceLabel(app.visibility));
    if (app.anyone_pending_approval) {
      audience += ' <span class="portal-admin-status-badge">' + esc(t("Waiting for an administrator's approval")) + "</span>";
    }
    audience += ' <button type="button" class="portal-app-link" data-app-tab-open="settings">' + esc(t("Change")) + "</button>";
    facts.push(factHtml(t("Who may open it"), audience));
    const current = app.current_release;
    if (current && current.commit_sha) {
      facts.push(factHtml(
        t("Running release"),
        "<code>" + esc(shortSha(current.commit_sha)) + "</code> " +
        esc(refName(current.ref || manage.repo_ref)
          ? t("of {ref}, deployed {when}", { ref: refName(current.ref || manage.repo_ref), when: formatAgo(current.finished_at || current.started_at) })
          : t("deployed {when}", { when: formatAgo(current.finished_at || current.started_at) }))
      ));
    } else if (!app.deployed) {
      // An app that runs and names no release: the commit and the branch are
      // for whoever may manage it, so a visitor's page has no such line.
      facts.push(factHtml(t("Running release"), esc(t("Nothing deployed yet"))));
    }
    const repoUrl = manage.repo_web_url || manage.repo_url || "";
    if (repoUrl) {
      // A link when the address is one to go to; the address as words when
      // it is not (the page of a repository cannot be made of every address).
      const repoLink = safeUrl(repoUrl);
      const repoShown = repoUrl.replace(/^https?:\/\//, "");
      // An app made from a template follows no branch: it is pinned to a commit.
      const followed = /^[0-9a-f]{40}$/.test(manage.repo_ref || "") ? shortSha(manage.repo_ref) : manage.repo_ref || "";
      facts.push(factHtml(
        t("Code"),
        (repoLink ? '<a href="' + esc(repoLink) + '" target="_blank" rel="noopener">' + esc(repoShown) + "</a>" : esc(repoShown)) + " \u00b7 " +
        esc(followed) + (manage.entry_dir ? " \u00b7 " + esc(manage.entry_dir) : "")
      ));
    }
    if (template) {
      const from = template.owner && (template.owner.display_name || template.owner.username)
        ? t("{name} by {owner}, version {tag}", { name: template.app_name || "?", owner: template.owner.display_name || template.owner.username, tag: (template.version && template.version.tag) || "?" })
        : t("{name}, version {tag}", { name: template.app_name || "?", tag: (template.version && template.version.tag) || "?" });
      facts.push(factHtml(t("Made from"), esc(from)));
    }
    if (app.type) facts.push(factHtml(t("Kind"), esc(appTypeLabel(app.type))));
    if (!app.is_owner) facts.push(factHtml(t("Owner"), esc(ownerName(app))));

    const running = state.key === "running" || state.key === "starting";
    body.innerHTML =
      '<div class="portal-app-overview">' +
      '<section class="portal-panel-section portal-app-status">' +
      '<div class="portal-app-status-row">' +
      '<div class="portal-app-status-main">' + appIconHtml(app, "is-large") +
      '<div class="portal-app-status-copy"><div class="portal-app-status-title"><span class="portal-row-status-dot is-' + esc(state.tone) + '"></span><strong>' + esc(state.label) + "</strong></div>" +
      (state.reason ? "<p>" + esc(state.reason) + "</p>" : "") + "</div></div>" +
      '<div class="portal-app-status-actions">' +
      lifecycleButtonHtml("start", "play", t("Start"), !idle || running, runningAction()) +
      lifecycleButtonHtml("restart", "rotate-cw", t("Restart"), !idle, runningAction()) +
      lifecycleButtonHtml("stop", "square", t("Stop"), !idle || state.key === "stopped", runningAction()) +
      "</div></div>" +
      notes.join("") +
      "</section>" +
      callouts.join("") +
      '<section class="portal-panel-section"><dl class="portal-app-facts">' + facts.join("") + "</dl></section>" +
      "</div>";
    drawIcons(body);
  }

  async function runLifecycle(verb) {
    if (page.busy || ["start", "stop", "restart"].indexOf(verb) < 0) return;
    const token = page.token;
    page.busy = true;
    page.busyAction = verb;
    drawOverview();
    try {
      const status = await request(appPath(page.appId, "/" + verb), { method: "POST" });
      if (!pageLive(token)) return;
      page.status = status;
      page.statusError = "";
      if (verb === "start") toast(t("{name} is starting.", { name: page.app.name }));
      else if (verb === "stop") toast(t("{name} is stopped.", { name: page.app.name }));
      else toast(t("{name} is restarting.", { name: page.app.name }));
    } catch (error) {
      if (!pageLive(token)) return;
      toast(error.message, "error");
    } finally {
      if (pageLive(token)) {
        page.busy = false;
        page.busyAction = "";
        onStatusChanged();
        scheduleStatus();
      }
    }
  }

  // ---- Deployments ----

  function deployRefValue() {
    const input = page.root ? page.root.querySelector("[data-app-deploy-ref]") : null;
    return input ? input.value.trim() : "";
  }

  function syncDeployControls() {
    if (!page.root || page.tab !== "deployments") return;
    const state = pageState();
    const deploying = state.key === "deploying";
    const disabled = page.busy || deploying || state.key === "disabled";
    const deploy = page.root.querySelector("[data-app-deploy]");
    const rollback = page.root.querySelector("[data-app-rollback]");
    if (deploy) deploy.disabled = disabled;
    if (rollback) rollback.disabled = disabled || !(page.status && page.status.rollback_available);
    const note = page.root.querySelector("[data-app-deploy-note]");
    if (note) {
      const manage = (page.app && page.app.manage) || {};
      const behind = page.status && typeof page.status.commits_behind === "number" ? page.status.commits_behind : null;
      const template = page.app && page.app.template;
      let text = t("Deploys the newest commit of the branch or tag. Another branch or tag becomes the one the app follows.");
      if (state.key === "disabled") text = t("The app is taken down, so nothing can be deployed.");
      else if (deploying) text = t("A deployment is in progress. Another one can start when it has ended.");
      else if (template) {
        text = t("This app runs version {tag} of {template}. Deploy installs that version again; another version comes with an upgrade, offered on Overview when there is one.", {
          tag: (template.version && template.version.tag) || "?",
          template: template.app_name || t("its template"),
        });
      } else if (behind !== null && behind > 0) {
        text = tn(behind, "The running release is {n} commit behind {ref}.", "The running release is {n} commits behind {ref}.", { ref: manage.repo_ref || "" }) + " " + text;
      } else if (behind === 0) {
        text = t("The running release is the newest commit of {ref}.", { ref: manage.repo_ref || "" }) + " " + text;
      }
      note.textContent = text;
    }
  }

  function releaseStatusBadge(release) {
    const tone = RELEASE_STATUS_TONES[release.status] || "neutral";
    const label = Object.prototype.hasOwnProperty.call(RELEASE_STATUS_LABELS, release.status) ? tv(RELEASE_STATUS_LABELS[release.status]) : String(release.status || "");
    return '<span class="portal-status-badge is-' + tone + '">' + esc(label) + "</span>";
  }

  function stepsHtml(release) {
    const steps = Array.isArray(release.steps) ? release.steps : [];
    return steps.map(function (step) {
      const status = String(step.status || "pending");
      const note = stepNote(step);
      const duration = step.started_at && step.finished_at ? formatDuration(step.started_at, step.finished_at) : "";
      return (
        '<li class="portal-app-step is-' + esc(status) + '">' +
        '<i data-lucide="' + (STEP_ICONS[status] || "circle") + '" class="w-4 h-4"></i>' +
        '<span class="portal-app-step-name">' + esc(stepLabel(step)) + "</span>" +
        '<span class="portal-app-step-time">' + esc(duration) + "</span>" +
        (note ? '<small class="portal-app-step-note">' + esc(note) + "</small>" : "") +
        "</li>"
      );
    }).join("");
  }

  function releaseMetaText(release) {
    const parts = [formatAgo(release.started_at)];
    if (release.finished_at) parts.push(formatDuration(release.started_at, release.finished_at));
    const who = release.requested_by && (release.requested_by.display_name || release.requested_by.username);
    if (who) parts.push(t("by {name}", { name: who }));
    return parts.filter(Boolean).join(" \u00b7 ");
  }

  // The followed (or picked) release: its steps and its log. Drawn once, then
  // only its changing parts are replaced, so the log keeps its place.
  function drawRelease() {
    const panel = page.root ? page.root.querySelector("[data-app-release]") : null;
    const release = page.release;
    if (!panel || !release) return;
    panel.hidden = false;
    if (panel.dataset.releaseId !== release.id) {
      panel.dataset.releaseId = release.id;
      panel.innerHTML =
        '<div class="portal-app-release-head"><div><strong data-release-title></strong><small data-release-meta></small></div><span data-release-badge></span></div>' +
        '<p class="portal-app-release-message" data-release-message></p>' +
        '<div class="portal-inline-state is-error" data-release-error hidden></div>' +
        '<ol class="portal-app-steps" data-release-steps></ol>' +
        '<div class="portal-app-log-head"><span>' + esc(t("Log")) + '</span><button type="button" class="portal-btn portal-apps-inline-btn" data-app-release-log-copy>' + esc(t("Copy")) + "</button></div>" +
        '<pre class="portal-app-log is-release" data-release-log tabindex="0"></pre>';
    }
    const title = panel.querySelector("[data-release-title]");
    title.innerHTML = esc(releaseKindLabel(release)) + (refName(release.ref) ? " " + esc(t("of {ref}", { ref: refName(release.ref) })) : "") +
      (release.commit_sha ? " <code>" + esc(shortSha(release.commit_sha)) + "</code>" : "");
    panel.querySelector("[data-release-meta]").textContent = releaseMetaText(release);
    panel.querySelector("[data-release-badge]").innerHTML = releaseStatusBadge(release);
    const message = panel.querySelector("[data-release-message]");
    message.textContent = String(release.commit_message || "").split("\n")[0];
    message.hidden = !message.textContent;
    const error = panel.querySelector("[data-release-error]");
    error.textContent = release.status === "failed" ? String(release.error_summary || t("The deployment failed.")) : "";
    error.hidden = !error.textContent;
    const steps = panel.querySelector("[data-release-steps]");
    const html = stepsHtml(release);
    if (steps.dataset.drawn !== html) {
      steps.dataset.drawn = html;
      steps.innerHTML = html;
      drawIcons(steps);
    }
    const log = panel.querySelector("[data-release-log]");
    const printed = page.releaseLogLive ? page.releaseLog : String(release.log_tail || "");
    const text = printed || (releaseIsActive(release) ? t("Waiting for the first lines\u2026") : t("This release left no log."));
    // A finished release that printed nothing needs one line, not the log's box.
    log.classList.toggle("is-compact", !printed && !releaseIsActive(release));
    if (log.textContent !== text) {
      const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
      log.textContent = text;
      if (atEnd) log.scrollTop = log.scrollHeight;
    }
  }

  // The host's own log of the job, read on from where the last read ended.
  async function pullReleaseLog(release) {
    if (!release.host_job_id) return;
    const owner = page;
    try {
      const data = await request(appPath(owner.appId, "/releases/" + encodeURIComponent(release.id) + "/log?offset=" + owner.releaseLogOffset));
      if (owner.shownReleaseId !== release.id || !data) return;
      if (typeof data.text === "string" && data.text) owner.releaseLog += data.text;
      if (typeof data.size === "number" && data.size >= owner.releaseLogOffset) owner.releaseLogOffset = data.size;
      owner.releaseLogLive = Boolean(owner.releaseLog);
    } catch (_error) {
      // The end of the log the release itself carries is shown instead.
      owner.releaseLogLive = false;
    }
  }

  async function onReleaseEnded(release) {
    const owner = page;
    // Said once, however many reads see the release ended.
    if (owner.announced[release.id]) return;
    owner.announced[release.id] = true;
    if (release.status === "succeeded") {
      toast(release.kind === "rollback" ? t("{name} is rolled back.", { name: owner.app.name }) : t("{name} is deployed.", { name: owner.app.name }), "success");
    } else {
      // The summary is a whole sentence of its own ("The deployment failed at the step ...").
      toast(release.error_summary || t("The deployment failed. Its log says why."), "error");
    }
    await Promise.all([reloadApp(), refreshStatus()]);
    if (!pageLive(owner.token)) return;
    if (owner.tab === "deployments") loadReleases();
    scheduleStatus();
  }

  // One chain of reads per followed release: `follow` is the chain's number,
  // and a chain that is no longer the newest stops at its next step. A step
  // that starts from a timer is handed the page the chain belongs to.
  async function pollRelease(releaseId, failures, follow, from) {
    const owner = from || page;
    const current = function () {
      return pageLive(owner.token) && owner.following === follow && owner.shownReleaseId === releaseId && owner.tab === "deployments";
    };
    owner.releaseTimer = 0;
    let release;
    try {
      release = await request(appPath(owner.appId, "/releases/" + encodeURIComponent(releaseId)));
    } catch (error) {
      if (!current()) return;
      // A read that fails is tried again a few times before the page says so.
      if (failures < 5) {
        owner.releaseTimer = window.setTimeout(function () { pollRelease(releaseId, failures + 1, follow, owner); }, RELEASE_POLL_MS * 2);
      } else {
        toast(t("The deployment can no longer be followed: {error}", { error: error.message }), "error");
      }
      return;
    }
    if (!current()) return;
    owner.release = release;
    await pullReleaseLog(release);
    if (!current()) return;
    drawRelease();
    if (releaseIsActive(release)) {
      owner.releaseTimer = window.setTimeout(function () { pollRelease(releaseId, 0, follow, owner); }, RELEASE_POLL_MS);
    } else {
      owner.following = 0;
      onReleaseEnded(release);
    }
  }

  function followRelease(releaseId) {
    stopReleasePolling();
    page.followSeq += 1;
    page.following = page.followSeq;
    page.shownReleaseId = releaseId;
    page.releaseLog = "";
    page.releaseLogOffset = 0;
    page.releaseLogLive = false;
    pollRelease(releaseId, 0, page.following);
  }

  // The release the member pressed Details on. It stays in the panel until
  // they pick another one or start a deployment themselves: a deployment
  // that runs meanwhile does not take its place (onStatusChanged,
  // loadReleases). Picking the running one follows it.
  function pickRelease(releaseId) {
    page.pickedReleaseId = releaseId;
    return showReleaseOnce(releaseId);
  }

  async function showReleaseOnce(releaseId) {
    const owner = page;
    stopReleasePolling();
    owner.following = 0;
    owner.shownReleaseId = releaseId;
    owner.releaseLog = "";
    owner.releaseLogOffset = 0;
    owner.releaseLogLive = false;
    try {
      const release = await request(appPath(owner.appId, "/releases/" + encodeURIComponent(releaseId)));
      if (!pageLive(owner.token) || owner.shownReleaseId !== releaseId) return;
      owner.release = release;
      drawRelease();
      drawHistory();
      if (releaseIsActive(release)) followRelease(releaseId);
    } catch (error) {
      if (pageLive(owner.token)) toast(error.message, "error");
    }
  }

  function drawHistory() {
    const box = page.root ? page.root.querySelector("[data-app-history]") : null;
    if (!box) return;
    const count = page.root.querySelector("[data-app-history-count]");
    if (page.releases === null) {
      if (count) count.textContent = "";
      box.innerHTML = page.releasesError ? errorHtml(page.releasesError, "data-app-history-retry") : loadingHtml(t("Loading deployments\u2026"));
      return;
    }
    if (count) count.textContent = tn(page.releases.length, "{n} entry", "{n} entries");
    if (!page.releases.length) {
      box.innerHTML = '<div class="portal-panel-empty">' + esc(t("Nothing has been deployed yet. Press Deploy to start the first deployment.")) + "</div>";
      return;
    }
    const rows = page.releases.map(function (release) {
      const badges = [];
      if (release.is_current) badges.push('<span class="portal-admin-status-badge is-success">' + esc(t("Running now")) + "</span>");
      if (release.id === page.rollbackReleaseId) badges.push('<span class="portal-admin-role-badge">' + esc(t("Roll back returns here")) + "</span>");
      const who = release.requested_by && (release.requested_by.display_name || release.requested_by.username);
      return (
        '<tr class="' + (release.id === page.shownReleaseId ? "is-selected" : "") + '">' +
        "<td>" + esc(formatDateTime(release.started_at)) + "</td>" +
        "<td>" + esc(releaseKindLabel(release)) + (refName(release.ref) ? " \u00b7 " + esc(refName(release.ref)) : "") + "</td>" +
        "<td><code>" + esc(shortSha(release.commit_sha) || "-") + "</code> " + esc(String(release.commit_message || "").split("\n")[0].slice(0, 80)) + " " + badges.join(" ") + "</td>" +
        "<td>" + esc(who || "") + "</td>" +
        "<td>" + releaseStatusBadge(release) + "</td>" +
        '<td class="portal-app-row-action"><button type="button" class="portal-btn portal-apps-inline-btn" data-app-release-show="' + esc(release.id) + '">' + esc(t("Details")) + "</button></td>" +
        "</tr>"
      );
    }).join("");
    box.innerHTML =
      '<div class="portal-data-table-wrap"><table class="portal-data-table portal-app-history-table"><thead><tr>' +
      "<th>" + esc(t("Started")) + "</th><th>" + esc(t("What")) + "</th><th>" + esc(t("Commit")) + "</th><th>" + esc(t("By")) + "</th><th>" + esc(t("Result")) + '</th><th><span class="sr-only">' + esc(t("Actions")) + "</span></th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  async function loadReleases() {
    const owner = page;
    try {
      const data = await request(appPath(owner.appId, "/releases"));
      if (!pageLive(owner.token)) return;
      owner.releases = Array.isArray(data && data.releases) ? data.releases : [];
      owner.rollbackReleaseId = (data && data.rollback_release_id) || "";
      owner.releasesError = "";
    } catch (error) {
      if (!pageLive(owner.token)) return;
      owner.releasesError = error.message;
      if (owner.releases === null) {
        drawHistory();
        return;
      }
    }
    if (owner.tab !== "deployments") return;
    drawHistory();
    // What to show above the history: the deployment just asked for, one that
    // is running (unless the member is reading a release they picked), or
    // else the newest entry (and why it failed, if it did).
    const active = owner.releases.filter(releaseIsActive)[0];
    const wanted = owner.followReleaseId || (!owner.pickedReleaseId && active && active.id) || "";
    owner.followReleaseId = "";
    if (wanted) {
      if (!(owner.following && owner.shownReleaseId === wanted)) followRelease(wanted);
    } else if (!owner.shownReleaseId && owner.releases.length) {
      showReleaseOnce(owner.releases[0].id);
    }
  }

  function drawDeployments() {
    const body = tabBody();
    if (!body || !page.app) return;
    const manage = page.app.manage || {};
    // An app made from a template runs the version it is on: there is no
    // branch to choose, only that version to install again (or an upgrade).
    const refField = page.app.template
      ? ""
      : '<div class="portal-app-field"><label class="portal-form-label" for="app-deploy-ref">' + esc(t("Branch or tag")) + "</label>" +
        '<input id="app-deploy-ref" class="portal-form-input" type="text" list="app-deploy-refs" spellcheck="false" autocomplete="off" data-app-deploy-ref value="' + esc(manage.repo_ref || "") + '" />' +
        '<datalist id="app-deploy-refs"></datalist></div>';
    body.innerHTML =
      '<div class="portal-app-deployments">' +
      '<section class="portal-panel-section portal-app-deploy">' +
      '<div class="portal-app-deploy-bar">' +
      refField +
      '<button type="button" class="portal-btn is-primary" data-app-deploy><i data-lucide="rocket" class="w-4 h-4"></i><span>' + esc(t("Deploy")) + "</span></button>" +
      '<button type="button" class="portal-btn" data-app-rollback title="' + esc(t("Return to the release that ran before the current one")) + '"><i data-lucide="undo-2" class="w-4 h-4"></i><span>' + esc(t("Roll back")) + "</span></button>" +
      "</div>" +
      '<p class="portal-panel-note" data-app-deploy-note></p>' +
      "</section>" +
      '<section class="portal-panel-section portal-app-release" data-app-release hidden></section>' +
      '<section class="portal-app-history-section"><div class="portal-overview-panel-head"><h3>' + esc(t("History")) + "</h3><span data-app-history-count></span></div>" +
      "<div data-app-history></div></section>" +
      "</div>";
    drawIcons(body);
    page.shownReleaseId = "";
    page.pickedReleaseId = "";
    page.activeSeenId = "";
    page.release = null;
    page.deployRefsLoaded = false;
    syncDeployControls();
    drawHistory();
    loadReleases();
  }

  // The repository's branches and tags for the Deploy field's list, read the
  // first time the field is used.
  async function loadDeployRefs() {
    if (page.deployRefsLoaded || !page.app || !page.app.manage) return;
    page.deployRefsLoaded = true;
    const token = page.token;
    try {
      const refs = await request(API + "/repo-refs?repo_url=" + encodeURIComponent(page.app.manage.repo_url));
      if (!pageLive(token)) return;
      const list = document.getElementById("app-deploy-refs");
      if (!list) return;
      const names = [];
      (refs.branches || []).concat(refs.tags || []).forEach(function (name) {
        if (names.indexOf(name) < 0) names.push(name);
      });
      list.innerHTML = names.map(function (name) { return '<option value="' + esc(name) + '"></option>'; }).join("");
    } catch (_error) {
      /* the field still takes a typed name */
    }
  }

  // `options.owner` is the page a dialog asked on (confirmUpgrade): the
  // deployment is asked for that page's app, whatever is on the screen by
  // the time the dialog is confirmed.
  async function startDeploy(options) {
    const owner = (options && options.owner) || page;
    if (owner.busy || !owner.app) return;
    const live = function () { return pageLive(owner.token); };
    const manage = owner.app.manage || {};
    const typed = live() ? deployRefValue() : "";
    const latest = Boolean(options && options.latest) || !typed || typed === manage.repo_ref;
    owner.busy = true;
    owner.busyAction = options && options.upgradeTo !== undefined ? "upgrade" : "deploy";
    if (live()) {
      syncDeployControls();
      if (owner.tab === "overview") drawOverview();
    }
    const pressed = busy(live() && owner.tab === "deployments" && owner.root ? owner.root.querySelector("[data-app-deploy]") : null, t("Starting\u2026"));
    try {
      let release;
      if (options && options.upgradeTo !== undefined) {
        release = await request(appPath(owner.appId, "/upgrade"), { method: "POST", json: { version_id: options.upgradeTo } });
      } else if (latest) {
        release = await request(appPath(owner.appId, "/deploy-latest"), { method: "POST" });
      } else {
        release = await request(appPath(owner.appId, "/deploy"), { method: "POST", json: { ref: typed } });
      }
      if (!live()) return;
      toast(t("The deployment has started."));
      owner.status = Object.assign({}, owner.status || {}, { deploying: true, active_release_id: release && release.id });
      owner.busy = false;
      owner.followReleaseId = (release && release.id) || "";
      // Asked for by the member: it is followed, whatever release they were reading.
      owner.pickedReleaseId = "";
      if (owner.tab !== "deployments") {
        if (shell()) await shell().openApp(owner.appId, "deployments");
      } else {
        syncDeployControls();
        loadReleases();
      }
      if (!live()) return;
      syncHeader();
      scheduleStatus();
    } catch (error) {
      // Asked from a dialog: the dialog is still open and shows what went wrong.
      if (options && options.rethrow) throw error;
      if (!live()) return;
      toast(error.message, "error");
    } finally {
      pressed();
      owner.busy = false;
      owner.busyAction = "";
      if (live()) {
        syncDeployControls();
        syncSettingsHold();
        if (owner.tab === "overview") drawOverview();
      }
    }
  }

  // An app made from a template moves to the template's newer version only
  // when its owner says so.
  async function confirmUpgrade(versionId) {
    const owner = page;
    if (owner.busy || !owner.app || !owner.app.template) return;
    const template = owner.app.template;
    await window.showConfirm({
      title: t("Upgrade {name} to version {tag}?", { name: owner.app.name, tag: (template.update_available && template.update_available.tag) || "?" }),
      message: t("The app is deployed again from that version of {template}. If it does not work out, Roll back returns to the version it runs now.", {
        template: template.app_name || t("its template"),
      }),
      confirmText: t("Upgrade"),
      busyText: t("Starting\u2026"),
      action: function () {
        return startDeploy({ upgradeTo: versionId, rethrow: true, owner: owner });
      },
    });
  }

  async function runRollback() {
    const owner = page;
    if (owner.busy || !owner.app) return;
    const target = (owner.releases || []).filter(function (release) { return release.id === owner.rollbackReleaseId; })[0];
    let release = null;
    let failed = false;
    const confirmed = await window.showConfirm({
      title: t("Roll back {name}?", { name: owner.app.name }),
      message: target && target.commit_sha
        ? t("The app switches back to the release that ran before ({sha}) and restarts.", { sha: shortSha(target.commit_sha) })
        : t("The app switches back to the release that ran before and restarts."),
      confirmText: t("Roll back"),
      busyText: t("Rolling back\u2026"),
      busyMessage: t("Switching to the release that ran before and restarting the app."),
      // The dialog stays until the host has switched; what fails is said in it.
      action: async function () {
        owner.busy = true;
        owner.busyAction = "rollback";
        if (pageLive(owner.token)) syncDeployControls();
        try {
          release = await request(appPath(owner.appId, "/rollback"), { method: "POST" });
        } catch (error) {
          failed = true;
          throw error;
        } finally {
          owner.busy = false;
          owner.busyAction = "";
          if (pageLive(owner.token)) {
            syncDeployControls();
            syncSettingsHold();
          }
        }
      },
    });
    if (!pageLive(owner.token)) return;
    if (!confirmed || !release) {
      if (failed) loadReleases();
      return;
    }
    owner.release = release;
    owner.shownReleaseId = release.id;
    owner.pickedReleaseId = "";
    owner.releaseLogLive = false;
    drawRelease();
    await onReleaseEnded(release);
  }

  // ---- Logs ----

  function logBox() {
    return page.root ? page.root.querySelector("[data-app-log]") : null;
  }

  function setLogState(state, note) {
    page.log.state = state;
    page.log.note = note || "";
    const node = page.root ? page.root.querySelector("[data-app-log-state]") : null;
    if (!node) return;
    let text = "";
    if (state === "loading") text = t("Reading the log\u2026");
    else if (state === "live") text = t("Following the live log");
    else if (state === "paused") text = tn(page.log.pending.length, "Paused \u00b7 {n} new line waiting", "Paused \u00b7 {n} new lines waiting");
    else if (state === "ended") text = note ? t("The live log stopped: {reason}. Trying again\u2026", { reason: note }) : t("The live log stopped. Trying again\u2026");
    else if (state === "error") text = note;
    node.textContent = text;
    node.dataset.state = state;
  }

  // The whole log, written anew. It ends at its last line, as when the page
  // first reads it; with `keepPlace` it does so only for a member who was at
  // the end, and one who is reading further up keeps the lines they are on
  // (what was taken off the top is made up for).
  function drawLogLines(keepPlace) {
    const box = logBox();
    if (!box) return;
    const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 32;
    const heightBefore = box.scrollHeight;
    const topBefore = box.scrollTop;
    box.textContent = page.log.lines.length ? page.log.lines.join("\n") + "\n" : "";
    if (!keepPlace || atEnd) box.scrollTop = box.scrollHeight;
    else box.scrollTop = Math.max(0, topBefore - Math.max(0, heightBefore - box.scrollHeight));
    const empty = page.root.querySelector("[data-app-log-empty]");
    if (empty) empty.hidden = page.log.lines.length > 0;
  }

  function appendLogLines(lines) {
    const box = logBox();
    if (!lines.length) return;
    Array.prototype.push.apply(page.log.lines, lines);
    if (page.log.lines.length >= LOG_MAX_LINES + LOG_TRIM_LINES) {
      // The oldest lines go, a batch at a time (Download still gets them
      // from the host): the box is written anew once per batch and not once
      // per line, and it stays where the member is reading.
      page.log.lines = page.log.lines.slice(-LOG_MAX_LINES);
      drawLogLines(true);
      return;
    }
    if (!box) return;
    const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 32;
    box.appendChild(document.createTextNode(lines.join("\n") + "\n"));
    if (atEnd) box.scrollTop = box.scrollHeight;
    const empty = page.root.querySelector("[data-app-log-empty]");
    if (empty) empty.hidden = true;
  }

  function onLogLine(line) {
    if (page.log.paused) {
      page.log.pending.push(line);
      if (page.log.pending.length > LOG_MAX_LINES) page.log.pending.shift();
      setLogState("paused");
      return;
    }
    appendLogLines([line]);
  }

  function scheduleLogReconnect(token) {
    if (page.log.timer) window.clearTimeout(page.log.timer);
    page.log.timer = window.setTimeout(function () {
      page.log.timer = 0;
      if (pageLive(token) && page.tab === "logs") openLogStream();
    }, LOG_RECONNECT_MS);
  }

  // The host's live log (text/event-stream), read with fetch so an error
  // answer can be told from a stream that ended.
  async function openLogStream() {
    closeLogStream();
    const token = page.token;
    const controller = new AbortController();
    page.log.stream = controller;
    const mine = function () { return pageLive(token) && page.log.stream === controller; };
    try {
      const response = await fetch(appPath(page.appId, "/logs/stream"), { headers: { Accept: "text/event-stream" }, signal: controller.signal });
      if (!mine()) return;
      if (!response.ok || !response.body) {
        let payload = null;
        try {
          payload = await response.json();
        } catch (_error) {
          payload = null;
        }
        throw new ApiError(errorText(payload, response.status), payload, response.status);
      }
      setLogState(page.log.paused ? "paused" : "live");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const feed = createSseParser(onLogLine);
      for (;;) {
        const chunk = await reader.read();
        if (!mine()) return;
        if (chunk.done) break;
        feed(decoder.decode(chunk.value, { stream: true }));
      }
      setLogState("ended");
    } catch (error) {
      if (!mine() || (error && error.name === "AbortError")) return;
      setLogState("ended", (error && error.message) || "");
    }
    if (mine()) {
      page.log.stream = null;
      scheduleLogReconnect(token);
    }
  }

  async function startLogs() {
    const token = page.token;
    setLogState("loading");
    try {
      const data = await request(appPath(page.appId, "/logs?tail=" + LOG_TAIL_LINES));
      if (!pageLive(token) || page.tab !== "logs") return;
      page.log.lines = Array.isArray(data && data.lines) ? data.lines.map(String) : [];
      page.log.pending = [];
      drawLogLines();
    } catch (error) {
      if (!pageLive(token) || page.tab !== "logs") return;
      setLogState("error", error.message);
      scheduleLogReconnectAfterError(token);
      return;
    }
    openLogStream();
  }

  // The first read failed (the host is away, say): try the whole thing again later.
  function scheduleLogReconnectAfterError(token) {
    if (page.log.timer) window.clearTimeout(page.log.timer);
    page.log.timer = window.setTimeout(function () {
      page.log.timer = 0;
      if (pageLive(token) && page.tab === "logs") startLogs();
    }, LOG_RECONNECT_MS * 2);
  }

  function drawLogs() {
    const body = tabBody();
    if (!body || !page.app) return;
    const deployed = page.status ? Boolean(page.status.deployed) : Boolean(page.app.deployed);
    page.log = { lines: [], pending: [], paused: false, stream: null, timer: 0, state: "", note: "" };
    if (!deployed) {
      body.innerHTML = emptyHtml(
        "scroll-text",
        t("No log yet"),
        t("The app writes its log once it runs. Deploy it first; what a deployment itself printed is under Deployments."),
        '<button type="button" class="portal-btn" data-app-tab-open="deployments">' + esc(t("Deployments")) + "</button>"
      );
      drawIcons(body);
      return;
    }
    body.innerHTML =
      '<div class="portal-app-logs">' +
      '<div class="portal-app-logs-bar">' +
      '<span class="portal-app-live" data-app-log-state role="status" aria-live="polite"></span>' +
      '<button type="button" class="portal-btn" data-app-log-pause aria-pressed="false" title="' + esc(t("Hold new lines back while you read; press again to carry on")) + '">' +
      '<i data-lucide="pause" class="w-4 h-4"></i><span>' + esc(t("Pause")) + "</span></button>" +
      '<button type="button" class="portal-btn" data-app-log-download title="' + esc(t("Save the last {n} lines as a text file", { n: LOG_MAX_LINES })) + '">' +
      '<i data-lucide="download" class="w-4 h-4"></i><span>' + esc(t("Download")) + "</span></button>" +
      "</div>" +
      '<div class="portal-app-log-wrap">' +
      '<pre class="portal-app-log" data-app-log tabindex="0" aria-label="' + esc(t("The app's log")) + '"></pre>' +
      '<div class="portal-app-log-empty" data-app-log-empty hidden>' + esc(t("The app has not written anything yet.")) + "</div>" +
      "</div></div>";
    drawIcons(body);
    startLogs();
  }

  function toggleLogPause(button) {
    page.log.paused = !page.log.paused;
    button.setAttribute("aria-pressed", page.log.paused ? "true" : "false");
    button.classList.toggle("is-active", page.log.paused);
    if (page.log.paused) {
      setLogState("paused");
      return;
    }
    const waiting = page.log.pending;
    page.log.pending = [];
    appendLogLines(waiting);
    const box = logBox();
    if (box) box.scrollTop = box.scrollHeight;
    setLogState(page.log.stream ? "live" : "ended");
  }

  function saveTextFile(name, text) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    window.setTimeout(function () {
      URL.revokeObjectURL(link.href);
      link.remove();
    }, 0);
  }

  async function downloadLog(button) {
    const owner = page;
    const slug = (owner.app && owner.app.slug) || "app";
    const fetched = busy(button, t("Preparing\u2026"));
    try {
      const data = await request(appPath(owner.appId, "/logs?tail=" + LOG_MAX_LINES));
      // The member has left the page meanwhile: no file arrives on another one.
      if (!pageLive(owner.token)) return;
      const lines = Array.isArray(data && data.lines) ? data.lines : [];
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
      saveTextFile(slug + "-log-" + stamp + ".txt", lines.join("\n") + "\n");
    } catch (error) {
      if (pageLive(owner.token)) toast(error.message, "error");
    } finally {
      fetched();
    }
  }

  // ---- Usage ----

  // The days of the period, oldest first, each with its row or none (UTC days, as the host counts them).
  function daysOfPeriod(rows, days, now) {
    const byDay = {};
    (rows || []).forEach(function (row) { byDay[String(row.day)] = row; });
    const end = now || new Date();
    const out = [];
    for (let back = days - 1; back >= 0; back -= 1) {
      const day = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate() - back)).toISOString().slice(0, 10);
      out.push({ day: day, row: byDay[day] || null });
    }
    return out;
  }

  function chartHtml(title, total, period, pick, format) {
    const heights = barHeights(period, function (item) { return item.row ? pick(item.row) : 0; });
    const bars = period.map(function (item, index) {
      const value = item.row ? Number(pick(item.row)) || 0 : 0;
      return (
        '<span class="portal-usage-bar' + (value > 0 ? "" : " is-empty") + '" style="--h: ' + heights[index] + '%" title="' +
        esc(item.day + ": " + format(value)) + '"><i></i></span>'
      );
    }).join("");
    return (
      '<article class="portal-panel-section portal-usage-chart">' +
      '<div class="portal-overview-panel-head"><h3>' + esc(title) + "</h3><span>" + esc(total) + "</span></div>" +
      '<div class="portal-usage-bars" role="img" aria-label="' + esc(title) + '">' + bars + "</div>" +
      '<div class="portal-usage-axis"><span>' + esc(period.length ? period[0].day : "") + "</span><span>" + esc(period.length ? period[period.length - 1].day : "") + "</span></div>" +
      "</article>"
    );
  }

  function usageCardHtml(label, value, sub) {
    return (
      '<div class="portal-usage-card"><span class="portal-usage-k">' + esc(label) + '</span><strong class="portal-usage-v">' + esc(value) + "</strong>" +
      (sub ? '<small class="portal-usage-sub">' + esc(sub) + "</small>" : "") + "</div>"
    );
  }

  function meterHtml(label, used, limit, format) {
    const share = limit > 0 ? Math.min(100, Math.round((used / limit) * 1000) / 10) : 0;
    return (
      '<div class="portal-app-meter' + (share >= 90 ? " is-high" : "") + '">' +
      '<div class="portal-app-meter-head"><span>' + esc(label) + "</span><strong>" +
      esc(limit > 0 ? t("{used} of {limit}", { used: format(used), limit: format(limit) }) : format(used)) + "</strong></div>" +
      '<div class="portal-quota-meter"><span style="width: ' + share + '%"></span></div></div>'
    );
  }

  function drawUsageData(usage) {
    const body = tabBody();
    if (!body) return;
    const days = Number(usage.days) || page.usageDays;
    const traffic = daysOfPeriod(usage.traffic, days);
    const llm = daysOfPeriod(usage.llm, days);
    const totals = usage.totals || {};
    const budget = usage.budget || {};
    const errors = sumOf(usage.traffic, function (row) { return (Number(row.errors_4xx) || 0) + (Number(row.errors_5xx) || 0); });
    const serverErrors = sumOf(usage.traffic, function (row) { return row.errors_5xx; });
    const failedCalls = sumOf(usage.llm, function (row) { return row.failed; });
    const slowest = Math.max.apply(null, (usage.traffic || []).map(function (row) { return Number(row.p95_ms) || 0; }).concat([0]));
    const count = function (value) { return formatCount(value); };
    const ms = function (value) { return t("{n} ms", { n: formatCount(value) }); };

    const periodOptions = [7, 30, 90].map(function (option) {
      return '<option value="' + option + '"' + (option === days ? " selected" : "") + ">" + esc(t("Last {n} days", { n: option })) + "</option>";
    }).join("");

    const connectors = Array.isArray(usage.connectors) ? usage.connectors : [];
    const connectorTop = Math.max.apply(null, connectors.map(function (row) { return Number(row.calls) || 0; }).concat([0]));
    const connectorRows = connectors.length
      ? connectors.map(function (row) {
          const calls = Number(row.calls) || 0;
          const failed = Number(row.failed) || 0;
          return (
            '<div class="portal-usage-connector-row" title="' + esc(tn(failed, "{n} failed call", "{n} failed calls")) + '"><span>' + esc(connectorName(row.type)) + "</span>" +
            '<div class="portal-quota-meter"><span style="width: ' + (connectorTop > 0 ? Math.round((calls / connectorTop) * 100) : 0) + '%"></span></div>' +
            "<strong>" + esc(count(calls)) + "</strong></div>"
          );
        }).join("")
      : '<div class="portal-panel-empty">' + esc(t("No connector is turned on for this app.")) + "</div>";

    const visitors = Array.isArray(usage.visitors) ? usage.visitors : [];
    const visitorRows = visitors.length
      ? visitors.map(function (row) {
          const user = row.user || {};
          return (
            "<tr><td><strong>" + esc(user.display_name || user.username || t("(unknown)")) + "</strong>" +
            (user.username ? ' <small class="is-muted">@' + esc(user.username) + "</small>" : "") + "</td>" +
            '<td class="is-number">' + esc(count(row.requests)) + "</td>" +
            '<td class="is-muted">' + esc(formatAgo(row.last_visited_at)) + "</td></tr>"
          );
        }).join("")
      : '<tr><td colspan="3" class="is-muted">' + esc(t("No signed-in member has opened the app in this period.")) + "</td></tr>";

    const process = usage.process || null;
    const processHtml = process
      ? '<dl class="portal-app-facts is-compact">' +
        factHtml(t("Memory"), esc(formatBytes(process.rss_bytes) || "-")) +
        factHtml(t("CPU"), esc(typeof process.cpu_percent === "number" ? process.cpu_percent.toFixed(1) + "%" : "-")) +
        factHtml(t("Data"), esc(formatBytes(process.data_bytes) || "-")) +
        factHtml(t("Restarts"), esc(count(process.restarts))) +
        factHtml(t("Reported"), esc(formatAgo(process.reported_at) || "-")) +
        "</dl>"
      : '<div class="portal-panel-empty">' + esc(t("The app host has not reported on the process yet.")) + "</div>";

    const usedCredits = Number(budget.used_credits) || 0;
    const usedTokens = Number(budget.used_tokens) || 0;
    body.innerHTML =
      '<div class="portal-app-usage">' +
      '<div class="portal-admin-usage-toolbar"><select class="portal-form-select" aria-label="' + esc(t("Period")) + '" data-app-usage-days>' + periodOptions + "</select></div>" +
      '<div class="portal-admin-summary-grid portal-admin-usage-summary">' +
      usageCardHtml(t("Requests"), count(totals.requests), tn(errors, "{n} error", "{n} errors")) +
      usageCardHtml(t("Visitors"), count(totals.visitors), t("signed in or not")) +
      usageCardHtml(t("Model calls"), count(totals.llm_calls), t("{failed} failed", { failed: count(failedCalls) })) +
      usageCardHtml(t("Credits"), formatCredits(totals.credits), t("spent on the owner's model provider")) +
      usageCardHtml(t("Connector calls"), count(totals.connector_calls), "") +
      "</div>" +
      '<section class="portal-admin-usage-charts" aria-label="' + esc(t("Visits")) + '">' +
      chartHtml(t("Requests per day"), count(totals.requests), traffic, function (row) { return row.requests; }, count) +
      chartHtml(t("Visitors per day"), "", traffic, function (row) { return row.visitors; }, count) +
      chartHtml(t("Errors per day"), t("{n} from the app itself", { n: count(serverErrors) }), traffic, function (row) { return (Number(row.errors_4xx) || 0) + (Number(row.errors_5xx) || 0); }, count) +
      chartHtml(t("Response time per day (95th percentile)"), slowest ? t("slowest day {n} ms", { n: formatCount(slowest) }) : "", traffic, function (row) { return row.p95_ms; }, ms) +
      "</section>" +
      '<section class="portal-admin-usage-columns">' +
      '<article class="portal-panel-section"><div class="portal-overview-panel-head"><h3>' + esc(t("Model provider today")) + "</h3><span>" + esc(t("against the daily limit")) + "</span></div>" +
      meterHtml(t("Credits"), usedCredits, Number(budget.daily_credits) || 0, formatCredits) +
      meterHtml(t("Tokens"), usedTokens, Number(budget.daily_tokens) || 0, count) +
      "</article>" +
      chartHtml(t("Credits per day"), formatCredits(totals.credits), llm, function (row) { return row.credits; }, formatCredits) +
      "</section>" +
      '<section class="portal-admin-usage-columns">' +
      '<article class="portal-panel-section"><div class="portal-overview-panel-head"><h3>' + esc(t("Connector calls")) + "</h3><span>" + esc(count(totals.connector_calls)) + "</span></div>" +
      '<div class="portal-usage-connectors">' + connectorRows + "</div></article>" +
      '<article class="portal-panel-section"><div class="portal-overview-panel-head"><h3>' + esc(t("Memory and data")) + "</h3></div>" + processHtml + "</article>" +
      "</section>" +
      '<section class="portal-panel-section"><div class="portal-overview-panel-head"><h3>' + esc(t("Top visitors")) + "</h3><span>" + esc(tn(visitors.length, "{n} member", "{n} members")) + "</span></div>" +
      '<div class="portal-data-table-wrap"><table class="portal-data-table portal-admin-usage-table"><thead><tr><th>' + esc(t("Member")) + '</th><th class="is-number" title="' + esc(t("Every request the member ever made to the app")) + '">' + esc(t("Requests, all time")) + "</th><th>" + esc(t("Last visit")) + "</th></tr></thead><tbody>" +
      visitorRows + "</tbody></table></div></section>" +
      "</div>";
  }

  async function drawUsage() {
    const body = tabBody();
    if (!body) return;
    const owner = page;
    // The period asked for: an answer for one the member has since changed is not drawn.
    const days = owner.usageDays;
    const wanted = function () { return pageLive(owner.token) && owner.tab === "usage" && owner.usageDays === days; };
    body.innerHTML = loadingHtml(t("Loading usage\u2026"));
    try {
      const usage = await request(appPath(owner.appId, "/usage?days=" + days));
      if (!wanted()) return;
      drawUsageData(usage || {});
    } catch (error) {
      if (!wanted()) return;
      body.innerHTML = error.status === 404
        ? '<div class="portal-panel-empty">' + esc(t("Usage figures are not available on this Portal yet.")) + "</div>"
        : errorHtml(error.message, "data-app-usage-retry");
    }
  }

  // ---- Settings ----

  function settingsSection(name) {
    return page.root ? page.root.querySelector('[data-app-settings="' + name + '"]') : null;
  }

  // The owner, as the warnings name them: "your" on one's own app, the
  // owner's name when an administrator changes someone else's.
  function llmWarning() {
    if (page.app.is_owner) return t("Visitors' use of this app consumes your Copilot and may slow down your own chats.");
    return t("Visitors' use of this app consumes {owner}'s Copilot and may slow down their own chats.", { owner: ownerName(page.app) });
  }

  function connectorWarning() {
    if (page.app.is_owner) return t("Visitors will read these systems with your connector's account.");
    return t("Visitors will read these systems with the account of {owner}'s connector.", { owner: ownerName(page.app) });
  }

  // Saves changes to the app of `owner`, the page that asked. The answer is
  // that app as it was saved: it is kept on that page and on no other, and
  // the header follows it only while that page is the one on the screen.
  async function patchApp(owner, changes) {
    const app = await request(appPath(owner.appId), { method: "PATCH", json: changes });
    const carriesEnv = Boolean(app && app.manage && app.manage.env !== undefined && app.manage.env !== null);
    keepApp(owner, app);
    // Variables an answer does not repeat are the ones that were just saved.
    if (!carriesEnv && changes && changes.env && owner.app && owner.app.manage) owner.app.manage.env = changes.env;
    if (pageLive(owner.token)) syncHeader();
    return app;
  }

  // While one action holds the page, the choices and switches of Settings
  // wait with it. A second save beside the first would undo it (the list of
  // connectors is sent whole, and the answer that arrives last is the one
  // the page keeps), and a choice that cannot be saved must not look chosen.
  // They are held where they are, so the one being saved keeps its spinner;
  // what was switched off for another reason stays off when the hold ends.
  const SETTINGS_HELD = ["audience", "capabilities", "template"];

  function syncSettingsHold() {
    if (!page.root || page.tab !== "settings" || !page.app || !page.app.can_manage) return;
    SETTINGS_HELD.forEach(function (name) {
      const section = settingsSection(name);
      if (!section) return;
      Array.prototype.forEach.call(section.querySelectorAll("input"), function (input) {
        if (page.busy) {
          if (!input.disabled) {
            input.disabled = true;
            input.dataset.appHeld = "1";
          }
        } else if (input.dataset.appHeld) {
          input.disabled = false;
          delete input.dataset.appHeld;
        }
      });
    });
    syncEnvSave();
  }

  // One save of Settings at a time, with the page held from the request to
  // its answer. `save` is what asks the server; `owner` the page it is for.
  async function saveHeld(owner, save) {
    owner.busy = true;
    if (pageLive(owner.token)) syncSettingsHold();
    try {
      return await save();
    } finally {
      owner.busy = false;
      if (pageLive(owner.token)) syncSettingsHold();
    }
  }

  function audienceSectionHtml() {
    const app = page.app;
    const settings = settingsState.value || {};
    const offered = Array.isArray(settings.visibilities) && settings.visibilities.length ? settings.visibilities : ["private", "public", "platform"];
    const groupName = (app.owner && app.owner.group_name) || "";
    const options = AUDIENCES.filter(function (item) {
      // "Anyone" is offered where the platform allows it; an app that already asks for it keeps showing it.
      return offered.indexOf(item.value) >= 0 || item.value === app.visibility;
    }).map(function (item) {
      let label = tv(item.label);
      if (item.value === "public" && groupName) label = t("{label} ({group})", { label: label, group: groupName });
      return (
        '<label class="portal-share-option"><input type="radio" name="app-audience" value="' + item.value + '"' + (item.value === app.visibility ? " checked" : "") + " />" +
        "<span><strong>" + esc(label) + "</strong><small>" + esc(tv(item.detail)) + "</small></span></label>"
      );
    }).join("");
    const pending = app.anyone_pending_approval
      ? '<p class="portal-app-note is-warning">' + esc(t("Waiting for an administrator's approval. Until then every member can open it, and nobody else.")) + "</p>"
      : "";
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("Who may open it")) + "</h3></div>" +
      '<div class="portal-share-options portal-app-audience" role="radiogroup" aria-label="' + esc(t("Who may open it")) + '">' + options + "</div>" + pending
    );
  }

  function capabilitySectionHtml() {
    const manage = page.app.manage || {};
    const settings = settingsState.value || {};
    const types = Array.isArray(settings.connector_types) && settings.connector_types.length ? settings.connector_types : Object.keys(CONNECTOR_NAMES);
    const enabled = manage.connectors_enabled || [];
    const writable = manage.connectors_write || [];
    const credits = manage.daily_credits === null || manage.daily_credits === undefined ? manage.default_daily_credits : manage.daily_credits;
    const tokens = manage.daily_tokens === null || manage.daily_tokens === undefined ? manage.default_daily_tokens : manage.daily_tokens;
    const rows = types.map(function (type) {
      const on = enabled.indexOf(type) >= 0;
      const name = connectorName(type);
      return (
        '<div class="portal-app-switch-row">' +
        '<div class="portal-app-switch-copy"><strong>' + esc(name) + "</strong></div>" +
        '<label class="portal-checkbox-row" title="' + esc(t("Without this the app can only read: GET and HEAD")) + '"><input type="checkbox" data-app-connector-write="' + esc(type) + '"' +
        (writable.indexOf(type) >= 0 ? " checked" : "") + (on ? "" : " disabled") + " /><span>" + esc(t("May write")) + "</span></label>" +
        '<label class="toggle-switch" aria-label="' + esc(t("Let the app use {name}", { name: name })) + '"><input type="checkbox" data-app-connector="' + esc(type) + '"' + (on ? " checked" : "") +
        ' /><span class="toggle-slider"></span></label>' +
        "</div>"
      );
    }).join("");
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("What the app may use")) + "</h3></div>" +
      '<p class="portal-panel-note">' + esc(t("The app's Python code reaches these through the SDK, with the owner's own settings. Everything is off until it is turned on here.")) + "</p>" +
      '<div class="portal-app-switch-row">' +
      '<div class="portal-app-switch-copy"><strong>' + esc(t("Model provider")) + "</strong><small>" +
      esc(t("Up to {credits} credits and {tokens} tokens a day, whoever the visitor is.", { credits: formatCredits(credits), tokens: formatCount(tokens) })) + "</small></div>" +
      '<label class="toggle-switch" aria-label="' + esc(t("Let the app use the model provider")) + '"><input type="checkbox" data-app-llm' + (manage.llm_enabled ? " checked" : "") +
      ' /><span class="toggle-slider"></span></label>' +
      "</div>" +
      '<div class="portal-app-switch-group"><div class="portal-detail-label">' + esc(t("Connectors")) + "</div>" + rows + "</div>"
    );
  }

  function envRowHtml(name, value) {
    return (
      '<div class="portal-app-env-row" data-env-row>' +
      '<input type="text" class="portal-form-input" data-env-name value="' + esc(name) + '" placeholder="' + esc(t("NAME")) + '" aria-label="' + esc(t("Variable name")) + '" spellcheck="false" autocomplete="off" />' +
      '<input type="text" class="portal-form-input portal-app-masked" data-env-value value="' + esc(value) + '" placeholder="' + esc(t("Value")) + '" aria-label="' + esc(t("Variable value")) + '" spellcheck="false" autocomplete="off" />' +
      '<button type="button" class="portal-btn portal-app-icon-btn" data-env-reveal aria-pressed="false" title="' + esc(t("Show or hide the value")) + '" aria-label="' + esc(t("Show or hide the value")) + '"><i data-lucide="eye" class="w-4 h-4"></i></button>' +
      '<button type="button" class="portal-btn portal-app-icon-btn" data-env-remove title="' + esc(t("Remove this variable")) + '" aria-label="' + esc(t("Remove this variable")) + '"><i data-lucide="trash-2" class="w-4 h-4"></i></button>' +
      "</div>"
    );
  }

  function envSectionHtml() {
    const env = (page.app.manage && page.app.manage.env) || {};
    const names = Object.keys(env).sort();
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("Environment variables")) + "</h3><span>" + esc(tn(names.length, "{n} variable", "{n} variables")) + "</span></div>" +
      '<p class="portal-panel-note">' + esc(t("Settings and secrets the code reads from its environment. Names may not start with EFP_. Saving restarts the app.")) + "</p>" +
      '<div class="portal-app-env-rows" data-app-env-rows>' + names.map(function (name) { return envRowHtml(name, env[name]); }).join("") + "</div>" +
      '<div class="portal-app-env-actions">' +
      '<button type="button" class="portal-btn" data-app-env-add><i data-lucide="plus" class="w-4 h-4"></i><span>' + esc(t("Add variable")) + "</span></button>" +
      '<p class="portal-modal-feedback" data-app-env-msg aria-live="polite"></p>' +
      '<button type="button" class="portal-btn is-primary" data-app-env-save disabled>' + esc(t("Save")) + "</button>" +
      "</div>"
    );
  }

  function readEnvRows() {
    const section = settingsSection("env");
    if (!section) return [];
    return Array.prototype.map.call(section.querySelectorAll("[data-env-row]"), function (row) {
      return { name: row.querySelector("[data-env-name]").value, value: row.querySelector("[data-env-value]").value };
    });
  }

  function syncEnvSave() {
    const section = settingsSection("env");
    if (!section) return;
    const result = envFromRows(readEnvRows());
    const saved = (page.app.manage && page.app.manage.env) || {};
    const message = section.querySelector("[data-app-env-msg]");
    const save = section.querySelector("[data-app-env-save]");
    const changed = !result.error && JSON.stringify(Object.keys(result.env).sort().map(function (key) { return [key, result.env[key]]; })) !==
      JSON.stringify(Object.keys(saved).sort().map(function (key) { return [key, saved[key]]; }));
    // What is wrong with the rows as they are typed, or else what the server
    // said to the last save: that stays until the rows are changed, so a
    // save that failed is not answered with a button that just comes back.
    const said = result.error || page.envFailure || "";
    if (message) {
      message.textContent = said;
      message.classList.toggle("is-error", Boolean(said));
    }
    if (save) save.disabled = Boolean(result.error) || !changed || page.busy;
  }

  // The rows were changed by the member: what the last save was told no longer applies.
  function envRowsChanged() {
    page.envFailure = "";
    syncEnvSave();
  }

  function tokenUsedText() {
    const manage = page.app.manage || {};
    if (!manage.token_last_used_at) return t("The token has not been used yet.");
    const where = manage.token_last_used_source === "cluster" ? t("from inside the cluster (the deployed app)") : t("from outside the cluster (a developer's machine, for example)");
    return t("Last used {when}, {where}.", { when: formatAgo(manage.token_last_used_at), where: where });
  }

  function tokenSectionHtml() {
    const shown = page.tokenShown && page.token_value;
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("App token")) + "</h3></div>" +
      '<p class="portal-panel-note">' + esc(t("The app's code calls the Portal with this token. The deployed app has it by itself; copy it only to run the app on your own machine.")) + "</p>" +
      '<div class="portal-app-token-row">' +
      '<input type="text" class="portal-form-input portal-app-token" readonly data-app-token-field aria-label="' + esc(t("App token")) + '" value="' +
      esc(shown ? page.token_value : "efpa_" + Array(25).join("\u2022")) + '" />' +
      '<button type="button" class="portal-btn' + (shown ? " is-active" : "") + '" data-app-token-reveal aria-pressed="' + (shown ? "true" : "false") + '"><i data-lucide="eye" class="w-4 h-4"></i><span>' + esc(t("Reveal")) + "</span></button>" +
      '<button type="button" class="portal-btn" data-app-token-copy><i data-lucide="copy" class="w-4 h-4"></i><span>' + esc(t("Copy")) + "</span></button>" +
      "</div>" +
      '<p class="portal-panel-note" data-app-token-used>' + esc(tokenUsedText()) + "</p>" +
      '<p class="portal-panel-note">' + esc(t("On your own machine, put it in .env as EFP_APP_TOKEN, with EFP_PORTAL_URL={url}, and start the app with efp-app dev.", { url: window.location.origin })) +
      ' <a href="#/help/building-apps">' + esc(t("How local development works")) + "</a></p>" +
      '<div><button type="button" class="portal-btn is-danger" data-app-token-reset>' + esc(t("Reset token")) + "</button></div>"
    );
  }

  function templateSectionHtml() {
    const app = page.app;
    let body = "";
    if (app.is_template) {
      const tags = page.templateTags;
      let tagOptions;
      if (tags === null) tagOptions = '<option value="">' + esc(t("Loading\u2026")) + "</option>";
      else if (!tags.length) tagOptions = '<option value="">' + esc(t("The repository has no tags")) + "</option>";
      else {
        tagOptions = '<option value="">' + esc(t("Choose a tag")) + "</option>" + tags.map(function (item) {
          return '<option value="' + esc(item.tag) + '"' + (item.published ? " disabled" : "") + ">" +
            esc(item.published ? t("{tag} (published)", { tag: item.tag }) : item.tag) + "</option>";
        }).join("");
      }
      const versions = page.templateVersions;
      let versionRows;
      if (versions === null) versionRows = loadingHtml(t("Loading\u2026"));
      else if (!versions.length) {
        versionRows = '<div class="portal-panel-empty">' + esc(t("No version is published yet, so the template is not listed. Tag a commit on GitHub (v1.0.0, for example), then publish that tag here.")) + "</div>";
      } else {
        versionRows =
          '<div class="portal-data-table-wrap"><table class="portal-data-table portal-app-history-table"><thead><tr><th>' + esc(t("Version")) + "</th><th>" + esc(t("Commit")) + "</th><th>" + esc(t("Notes")) + "</th><th>" + esc(t("Published")) +
          '</th><th><span class="sr-only">' + esc(t("Actions")) + "</span></th></tr></thead><tbody>" +
          versions.map(function (item) {
            return (
              "<tr><td><strong>" + esc(item.tag) + "</strong></td><td><code>" + esc(shortSha(item.commit_sha)) + "</code></td><td>" + esc(item.notes || "") + "</td><td>" + esc(formatAgo(item.published_at)) + "</td>" +
              '<td class="portal-app-row-action"><button type="button" class="portal-btn portal-apps-inline-btn" data-app-template-remove="' + esc(item.id) + '" data-tag="' + esc(item.tag) + '">' + esc(t("Remove")) + "</button></td></tr>"
            );
          }).join("") + "</tbody></table></div>";
      }
      body =
        '<div class="portal-app-template-publish">' +
        '<div class="portal-app-field"><label class="portal-form-label" for="app-template-tag">' + esc(t("Tag to publish")) + '</label><select id="app-template-tag" class="portal-form-select" data-app-template-tag>' + tagOptions + "</select></div>" +
        '<div class="portal-app-field is-wide"><label class="portal-form-label" for="app-template-notes">' + esc(t("What is new (optional)")) + '</label><input id="app-template-notes" type="text" class="portal-form-input" maxlength="500" autocomplete="off" data-app-template-notes /></div>' +
        '<button type="button" class="portal-btn is-primary" data-app-template-publish disabled>' + esc(t("Publish version")) + "</button>" +
        "</div>" + versionRows;
    }
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("Template")) + "</h3></div>" +
      '<div class="portal-app-switch-row">' +
      '<div class="portal-app-switch-copy"><strong>' + esc(t("Offer this app as a template")) + "</strong><small>" +
      esc(t("Other members can start an app of their own from a version you publish. They get that version's code, never your token, variables or data.")) + "</small></div>" +
      '<label class="toggle-switch" aria-label="' + esc(t("Offer this app as a template")) + '"><input type="checkbox" data-app-template-toggle' + (app.is_template ? " checked" : "") +
      ' /><span class="toggle-slider"></span></label>' +
      "</div>" + body
    );
  }

  function codeSectionHtml() {
    const manage = page.app.manage || {};
    const repoUrl = manage.repo_web_url || manage.repo_url || "";
    // Links only for addresses there is a page to go to (safeUrl); the
    // repository's address is shown as words either way.
    const repoLink = safeUrl(repoUrl);
    const forkLink = safeUrl(manage.fork_url || (repoLink ? repoLink.replace(/\/+$/, "") + "/fork" : ""));
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("Code")) + "</h3></div>" +
      '<dl class="portal-app-facts is-compact">' +
      factHtml(t("Repository"), repoLink ? '<a href="' + esc(repoLink) + '" target="_blank" rel="noopener">' + esc(repoUrl) + "</a>" : esc(repoUrl || "-")) +
      (page.app.template
        ? factHtml(t("Version"), esc((page.app.template.version && page.app.template.version.tag) || "?") + " <code>" + esc(shortSha(manage.repo_ref)) + "</code>")
        : factHtml(t("Branch or tag"), esc(manage.repo_ref || "-"))) +
      factHtml(t("Entry directory"), esc(manage.entry_dir || t("top of the repository"))) +
      "</dl>" +
      '<p class="portal-panel-note">' + esc(t("The Portal only reads this repository: the app changes when someone pushes to it and deploys.")) + "<br />" +
      esc(t("Without write access to it, fork it on GitHub with your own account and register your fork here as a new app.")) + "</p>" +
      (forkLink ? '<div><a class="portal-btn" href="' + esc(forkLink) + '" target="_blank" rel="noopener"><i data-lucide="git-fork" class="w-4 h-4"></i><span>' + esc(t("Fork on GitHub")) + "</span></a></div>" : "")
    );
  }

  function deleteSectionHtml() {
    return (
      '<div class="portal-overview-panel-head"><h3>' + esc(t("Delete this app")) + "</h3></div>" +
      '<p class="portal-panel-note">' + esc(t("The app stops and its address stops working for good: no other app can take the address later. What the app saved stays on the host for a while and is then removed.")) + "</p>" +
      '<div><button type="button" class="portal-btn is-danger" data-app-delete>' + esc(t("Delete app")) + "</button></div>"
    );
  }

  function redrawSettingsSection(name, html) {
    const section = settingsSection(name);
    if (!section) return;
    section.innerHTML = html;
    drawIcons(section);
    // Drawn anew while the page is held: what was drawn waits like the rest.
    if (SETTINGS_HELD.indexOf(name) >= 0) syncSettingsHold();
  }

  async function loadTemplateDetails() {
    if (!page.app.is_template) return;
    const token = page.token;
    const results = await Promise.allSettled([
      request(appPath(page.appId, "/template/tags")),
      request(appPath(page.appId, "/template/versions")),
    ]);
    if (!pageLive(token) || page.tab !== "settings") return;
    page.templateTags = results[0].status === "fulfilled" && Array.isArray(results[0].value && results[0].value.tags) ? results[0].value.tags : [];
    page.templateVersions = results[1].status === "fulfilled" && Array.isArray(results[1].value && results[1].value.versions) ? results[1].value.versions : [];
    redrawSettingsSection("template", templateSectionHtml());
  }

  function drawSettings() {
    const body = tabBody();
    if (!body || !page.app) return;
    page.tokenShown = false;
    page.envFailure = "";
    page.templateTags = null;
    page.templateVersions = null;
    body.innerHTML =
      '<div class="portal-app-settings">' +
      '<section class="portal-panel-section" data-app-settings="audience">' + audienceSectionHtml() + "</section>" +
      '<section class="portal-panel-section" data-app-settings="capabilities">' + capabilitySectionHtml() + "</section>" +
      '<section class="portal-panel-section" data-app-settings="env">' + envSectionHtml() + "</section>" +
      '<section class="portal-panel-section" data-app-settings="token">' + tokenSectionHtml() + "</section>" +
      // An app made from a template cannot be offered as one itself.
      (page.app.template ? "" : '<section class="portal-panel-section" data-app-settings="template">' + templateSectionHtml() + "</section>") +
      '<section class="portal-panel-section" data-app-settings="code">' + codeSectionHtml() + "</section>" +
      '<section class="portal-panel-section portal-app-danger" data-app-settings="delete">' + deleteSectionHtml() + "</section>" +
      "</div>";
    drawIcons(body);
    const token = page.token;
    // The audiences and connectors on offer come with the settings; drawn again once they are known.
    if (!settingsState.value) {
      loadSettings().then(function () {
        if (!pageLive(token) || page.tab !== "settings") return;
        redrawSettingsSection("audience", audienceSectionHtml());
        redrawSettingsSection("capabilities", capabilitySectionHtml());
      }, function () { /* the defaults stay */ });
    }
    // Opened while an action runs (a restart, say): the choices wait for it.
    syncSettingsHold();
    loadTemplateDetails();
  }

  async function changeAudience(input) {
    const owner = page;
    const app = owner.app;
    const manage = app.manage || {};
    const previous = app.visibility;
    const next = input.value;
    const restore = function () {
      if (pageLive(owner.token)) redrawSettingsSection("audience", audienceSectionHtml());
    };
    if (next === previous) return;
    // Another action holds the page, so this choice cannot be saved now: it
    // is put back, not left looking chosen.
    if (owner.busy) {
      restore();
      return;
    }
    let updated = null;
    let asked = false;
    const save = function () {
      return saveHeld(owner, function () { return patchApp(owner, { visibility: next }); });
    };
    if (audienceWidens(previous, next)) {
      // More people in: said again what their use costs the owner.
      const warnings = [];
      if (manage.llm_enabled) warnings.push(llmWarning());
      if ((manage.connectors_enabled || []).length) warnings.push(connectorWarning());
      if (warnings.length) {
        // The dialog stays until the audience is saved, and says so when it is not.
        const confirmed = await window.showConfirm({
          title: t("Open {name} to more people?", { name: app.name }),
          message: warnings.join(" "),
          confirmText: t("Change audience"),
          busyText: t("Saving\u2026"),
          action: async function () { updated = await save(); },
        });
        if (!confirmed) {
          restore();
          return;
        }
        asked = true;
      }
    }
    if (!asked) {
      const saved = savingControl(input);
      try {
        updated = await save();
      } catch (error) {
        if (pageLive(owner.token)) toast(error.message, "error");
      } finally {
        saved();
      }
    }
    if (updated && pageLive(owner.token)) {
      if (updated.anyone_pending_approval) toast(t("An administrator has to approve it before anyone can open the app without signing in. Until then every member can open it."));
      else toast(t("{audience} can open {name} now.", { audience: audienceLabel(updated.visibility), name: updated.name }), "success");
    }
    restore();
  }

  async function changeCapability(input) {
    const owner = page;
    const app = owner.app;
    const manage = app.manage || {};
    const turnedOn = input.checked;
    const restore = function () {
      if (pageLive(owner.token)) redrawSettingsSection("capabilities", capabilitySectionHtml());
    };
    // One save at a time. The lists below are made of what the page knows
    // now and are sent whole: made while another save is on its way, they
    // would leave out what that save changes and undo it. The switch is put
    // back; it can be pressed again once the page is free.
    if (owner.busy) {
      restore();
      return;
    }
    let changes;
    let question = null;
    if (input.hasAttribute("data-app-llm")) {
      changes = { llm_enabled: turnedOn };
      if (turnedOn) {
        question = { title: t("Let {name} use the model provider?", { name: app.name }), message: llmWarning(), confirmText: t("Turn on") };
      }
    } else if (input.hasAttribute("data-app-connector")) {
      const type = input.dataset.appConnector;
      const enabled = (manage.connectors_enabled || []).filter(function (item) { return item !== type; });
      if (turnedOn) enabled.push(type);
      changes = { connectors_enabled: enabled };
      if (turnedOn) {
        question = { title: t("Let {name} use {connector}?", { name: app.name, connector: connectorName(type) }), message: connectorWarning(), confirmText: t("Turn on") };
      }
    } else {
      const type = input.dataset.appConnectorWrite;
      const writable = (manage.connectors_write || []).filter(function (item) { return item !== type; });
      if (turnedOn) writable.push(type);
      changes = { connectors_write: writable };
      if (turnedOn) {
        question = {
          title: t("Let {name} write to {connector}?", { name: app.name, connector: connectorName(type) }),
          message: t("What visitors do in the app can then change things in {connector}, with the connector's account.", { connector: connectorName(type) }),
          confirmText: t("Allow writing"),
        };
      }
    }
    // The other switches and choices wait from the request to its answer.
    const save = function () {
      return saveHeld(owner, function () { return patchApp(owner, changes); });
    };
    if (question) {
      // The dialog stays until the change is saved, and says so when it is not.
      await window.showConfirm(Object.assign(question, {
        busyText: t("Saving\u2026"),
        action: function () { return save(); },
      }));
      restore();
      return;
    }
    const saved = savingControl(input);
    try {
      await save();
    } catch (error) {
      if (pageLive(owner.token)) toast(error.message, "error");
    } finally {
      saved();
      restore();
    }
  }

  async function saveEnv() {
    const owner = page;
    const result = envFromRows(readEnvRows());
    if (result.error || owner.busy) return;
    owner.busy = true;
    owner.envFailure = "";
    // The Save button, and the choices and switches above it, wait.
    syncSettingsHold();
    const section = settingsSection("env");
    const saved = busy(section && section.querySelector("[data-app-env-save]"), t("Saving\u2026"));
    try {
      await patchApp(owner, { env: result.env });
      if (!pageLive(owner.token)) return;
      toast(owner.app.deployed ? t("Saved. The app restarts with the new values.") : t("Saved. The values apply from the first deployment."), "success");
      owner.busy = false;
      redrawSettingsSection("env", envSectionHtml());
    } catch (error) {
      // Said in the line beside the button (syncEnvSave), where it stays
      // until the rows are changed.
      owner.envFailure = error.message;
    } finally {
      saved();
      owner.busy = false;
      if (pageLive(owner.token)) syncSettingsHold();
    }
  }

  // Reading the token is recorded in the app's activity, so it is asked for
  // only when the member wants to see or copy it. It is the token of the app
  // of `owner`, the page that asked, and is kept on that page while it is
  // open: one that arrives after the member left is kept nowhere.
  async function fetchToken(owner) {
    const data = await request(appPath(owner.appId, "/token"));
    const value = String((data && data.token) || "");
    if (!pageLive(owner.token)) return value;
    owner.token_value = value;
    if (owner.app && owner.app.manage && data) {
      owner.app.manage.token_last_used_at = data.last_used_at || null;
      owner.app.manage.token_last_used_source = data.last_used_source || null;
    }
    return value;
  }

  async function revealToken(button) {
    const owner = page;
    // The token is asked for the first time it is shown; the button waits with the member.
    const fetched = !page.tokenShown && !page.token_value ? busy(button, t("Loading\u2026")) : function () {};
    try {
      if (!owner.tokenShown && !owner.token_value) await fetchToken(owner);
      if (!pageLive(owner.token)) return;
      owner.tokenShown = !owner.tokenShown;
      redrawSettingsSection("token", tokenSectionHtml());
    } catch (error) {
      if (pageLive(owner.token)) toast(error.message, "error");
    } finally {
      fetched();
    }
  }

  async function copyToken(button) {
    const owner = page;
    const fetched = page.token_value ? function () {} : busy(button, t("Copying\u2026"));
    try {
      const value = owner.token_value || (await fetchToken(owner));
      if (!pageLive(owner.token)) return;
      toast((await copyToClipboard(value)) ? t("Token copied") : t("Copy failed"));
    } catch (error) {
      if (pageLive(owner.token)) toast(error.message, "error");
    } finally {
      fetched();
    }
  }

  async function resetToken() {
    const owner = page;
    let data = null;
    const confirmed = await window.showConfirm({
      title: t("Reset the token of {name}?", { name: owner.app.name }),
      message: t("The old token stops working at once and the app restarts with the new one. A copy in a .env file on someone's machine has to be replaced."),
      confirmText: t("Reset token"),
      busyText: t("Resetting\u2026"),
      danger: true,
      action: async function () {
        data = await request(appPath(owner.appId, "/token/reset"), { method: "POST" });
      },
    });
    if (!confirmed || !pageLive(owner.token)) return;
    owner.token_value = String((data && data.token) || "");
    owner.tokenShown = true;
    if (owner.app.manage) {
      owner.app.manage.token_last_used_at = null;
      owner.app.manage.token_last_used_source = null;
    }
    redrawSettingsSection("token", tokenSectionHtml());
    if (data && data.host_synced === false) {
      toast(t("The token is reset, but the app host could not be told: {error} The app gets the new token the next time it starts.", { error: errorText(data.host_error, 0) }), "error");
    } else {
      toast(t("The token is reset. The app restarts with the new one."), "success");
    }
  }

  async function toggleTemplate(input) {
    const owner = page;
    const offered = input.checked;
    // Held like the other switches of Settings: its answer is the whole app
    // too, and must not land beside another save's.
    if (owner.busy) {
      if (pageLive(owner.token)) redrawSettingsSection("template", templateSectionHtml());
      return;
    }
    const saved = savingControl(input);
    try {
      const answer = await saveHeld(owner, function () {
        return request(appPath(owner.appId, "/template"), { method: "PUT", json: { is_template: offered } });
      });
      if (answer && typeof answer === "object" && answer.id) keepApp(owner, answer);
      else if (owner.app) owner.app.is_template = offered;
      if (pageLive(owner.token)) {
        toast(offered ? t("{name} is offered as a template once a version is published.", { name: owner.app.name }) : t("{name} is no longer offered as a template.", { name: owner.app.name }));
      }
    } catch (error) {
      if (pageLive(owner.token)) toast(error.status === 404 ? t("Templates are not available on this Portal yet.") : error.message, "error");
    }
    saved();
    if (!pageLive(owner.token)) return;
    owner.templateTags = null;
    owner.templateVersions = null;
    redrawSettingsSection("template", templateSectionHtml());
    loadTemplateDetails();
    refreshNavCounts();
  }

  async function publishTemplateVersion(button) {
    const section = settingsSection("template");
    const tag = section.querySelector("[data-app-template-tag]").value;
    const notes = section.querySelector("[data-app-template-notes]").value.trim();
    if (!tag) return;
    const token = page.token;
    const published = busy(button, t("Publishing\u2026"));
    try {
      await request(appPath(page.appId, "/template/versions"), { method: "POST", json: { tag: tag, notes: notes } });
      if (pageLive(token)) toast(t("Version {tag} is published.", { tag: tag }), "success");
    } catch (error) {
      if (pageLive(token)) toast(error.message, "error");
    }
    published();
    if (pageLive(token)) {
      loadTemplateDetails();
      // The first version puts the app on the Templates list.
      refreshNavCounts();
    }
  }

  async function removeTemplateVersion(button) {
    const owner = page;
    const tag = button.dataset.tag || "";
    const versionId = button.dataset.appTemplateRemove;
    const confirmed = await window.showConfirm({
      title: t("Remove version {tag}?", { tag: tag }),
      message: t("Nobody can start a new app from this version any more. Apps already made from it keep running."),
      confirmText: t("Remove"),
      busyText: t("Removing\u2026"),
      danger: true,
      action: function () {
        return request(appPath(owner.appId, "/template/versions/" + encodeURIComponent(versionId)), { method: "DELETE" });
      },
    });
    if (!confirmed) return;
    if (pageLive(owner.token)) {
      loadTemplateDetails();
      refreshNavCounts();
    }
  }

  async function deleteApp() {
    const app = page.app;
    const token = page.token;
    // Deleting takes a while: the host stops the app and removes what it has.
    // The dialog waits with the member, says what is happening, and closes
    // when the list without the app is on the screen.
    await window.showPrompt({
      title: t("Delete {name}?", { name: app.name }),
      message: t("This cannot be undone. Type the app's address name, {slug}, to confirm.", { slug: app.slug }),
      placeholder: app.slug,
      confirmText: t("Delete app"),
      busyText: t("Deleting\u2026"),
      busyMessage: t("Stopping the app and removing what it has on the host. This can take a moment."),
      danger: true,
      required: true,
      validate: function (typed) {
        return String(typed).trim() === app.slug ? "" : t("What you typed is not {slug}. Nothing was deleted.", { slug: app.slug });
      },
      action: async function () {
        await request(appPath(app.id), { method: "DELETE" });
        center.lists.mine = null;
        try {
          if (shell() && pageLive(token)) await shell().openCenter("mine");
        } catch (_error) {
          /* the app is gone; the list is read again when it is opened */
        }
        toast(t("{name} is deleted.", { name: app.name }), "success");
      },
    });
  }

  // ---- Data ----

  async function drawData() {
    const body = tabBody();
    if (!body) return;
    const token = page.token;
    body.innerHTML = loadingHtml(t("Loading\u2026"));
    let data = null;
    let failure = "";
    try {
      data = await request(appPath(page.appId, "/data"));
    } catch (error) {
      failure = error.message;
    }
    if (!pageLive(token) || page.tab !== "data") return;
    if (failure) {
      body.innerHTML = errorHtml(failure, "data-app-data-retry");
      return;
    }
    const known = data && typeof data.data_bytes === "number";
    const when = data && data.reported_at ? formatAgo(data.reported_at) : "";
    let note = "";
    if (data && data.host_reachable === false) note = t("The app host cannot be reached right now; this is what it last reported.");
    else if (!known) note = t("The app host has not reported a size yet. It appears once the app has been deployed.");
    body.innerHTML =
      '<section class="portal-panel-section portal-app-data">' +
      '<div class="portal-overview-panel-head"><h3>' + esc(t("Data")) + "</h3><span>" + esc(when ? t("measured {when}", { when: when }) : "") + "</span></div>" +
      '<div class="portal-app-data-size"><strong>' + esc(known ? formatBytes(data.data_bytes) : "-") + "</strong><span>" + esc(t("kept by the app on the host")) + "</span></div>" +
      (note ? '<p class="portal-app-note is-warning">' + esc(note) + "</p>" : "") +
      '<p class="portal-panel-note">' + esc(t("What the app saved through the SDK's storage or in its data directory. It survives restarts and deployments; the code and the log are not part of it. An app that keeps more than the platform allows is shown here and to administrators.")) + "</p>" +
      '<div><button type="button" class="portal-btn is-danger" data-app-data-clear' + (known && data.data_bytes > 0 ? "" : " disabled") + ">" + esc(t("Clear data")) + "</button></div>" +
      "</section>";
  }

  async function clearData() {
    const owner = page;
    const name = owner.app.name;
    const confirmed = await window.showConfirm({
      title: t("Clear the data of {name}?", { name: name }),
      message: t("Everything the app saved is removed and cannot be brought back. The app itself keeps running."),
      confirmText: t("Clear data"),
      busyText: t("Clearing\u2026"),
      danger: true,
      action: function () {
        return request(appPath(owner.appId, "/data"), { method: "DELETE" });
      },
    });
    // Said wherever the member is by now: the sentence names the app.
    if (confirmed) toast(t("The data of {name} is cleared.", { name: name }), "success");
    if (!pageLive(owner.token)) return;
    if (owner.tab === "data") drawData();
  }

  // ---- Activity ----

  async function drawActivity() {
    const body = tabBody();
    if (!body) return;
    const token = page.token;
    body.innerHTML = loadingHtml(t("Loading\u2026"));
    let events = [];
    try {
      const data = await request(appPath(page.appId, "/events"));
      events = Array.isArray(data && data.events) ? data.events : [];
    } catch (error) {
      if (pageLive(token) && page.tab === "activity") body.innerHTML = errorHtml(error.message, "data-app-activity-retry");
      return;
    }
    if (!pageLive(token) || page.tab !== "activity") return;
    if (!events.length) {
      body.innerHTML = '<div class="portal-panel-empty">' + esc(t("Nothing has happened to this app yet.")) + "</div>";
      return;
    }
    const rows = events.map(function (event) {
      const actor = event.actor
        ? event.actor.display_name || event.actor.username
        : String(event.action || "").indexOf("host.") === 0 ? t("App host") : t("Portal");
      return (
        "<tr><td>" + esc(formatDateTime(event.created_at)) + "</td><td>" + esc(actor) + "</td><td>" + esc(eventText(event)) + "</td></tr>"
      );
    }).join("");
    body.innerHTML =
      '<section class="portal-app-activity"><div class="portal-overview-panel-head"><h3>' + esc(t("Activity")) + "</h3><span>" + esc(tn(events.length, "{n} entry", "{n} entries")) + "</span></div>" +
      '<div class="portal-data-table-wrap"><table class="portal-data-table portal-app-history-table"><thead><tr><th>' + esc(t("When")) + "</th><th>" + esc(t("Who")) + "</th><th>" + esc(t("What")) + "</th></tr></thead><tbody>" +
      rows + "</tbody></table></div></section>";
  }

  // ---- Administration > Apps (partials/admin_apps_panel.html) ----------------

  const ADMIN_FILTERS = [
    { value: "all", label: N_("All apps ({n})"), test: function () { return true; } },
    { value: "attention", label: N_("Needs attention ({n})"), test: function (app) { return needsAttention(app); } },
    { value: "disabled", label: N_("Taken down ({n})"), test: function (app) { return app.enabled === false; } },
    { value: "pending", label: N_("Waiting for approval ({n})"), test: function (app) { return Boolean(app.anyone_pending_approval); } },
    { value: "templates", label: N_("Templates ({n})"), test: function (app) { return Boolean(app.is_template); } },
  ];

  const admin = { root: null, apps: null, host: null, error: "", query: "", filter: "all", sortKey: "name", sortDir: 1, seq: 0 };

  function adminMetric(app, name) {
    const metrics = (app && app.metrics) || {};
    const value = Number(metrics[name]);
    return Number.isFinite(value) ? value : null;
  }

  function adminSortValue(app, key) {
    if (key === "owner") return ownerName(app).toLowerCase();
    if (key === "audience") return AUDIENCE_ORDER.indexOf(app.visibility);
    if (key === "state") return appState(app, null).label.toLowerCase();
    if (key === "requests") return adminMetric(app, "requests_7d") || 0;
    if (key === "credits") return adminMetric(app, "credits_7d") || 0;
    if (key === "memory") return adminMetric(app, "rss_bytes") || 0;
    if (key === "data") return adminMetric(app, "data_bytes") || 0;
    return String(app.name || "").toLowerCase();
  }

  function adminRows() {
    const filter = ADMIN_FILTERS.filter(function (item) { return item.value === admin.filter; })[0] || ADMIN_FILTERS[0];
    const rows = (admin.apps || []).filter(function (app) { return filter.test(app) && matchesQuery(app, admin.query); });
    return rows.sort(function (left, right) {
      const a = adminSortValue(left, admin.sortKey);
      const b = adminSortValue(right, admin.sortKey);
      if (a === b) return byName(left, right);
      if (typeof a === "number" && typeof b === "number") return (a - b) * admin.sortDir;
      return String(a).localeCompare(String(b)) * admin.sortDir;
    });
  }

  function hostCardHtml(label, value, sub, share) {
    return (
      '<div class="portal-usage-card"><span class="portal-usage-k">' + esc(label) + '</span><strong class="portal-usage-v">' + esc(value) + "</strong>" +
      (typeof share === "number" ? '<div class="portal-quota-meter"><span style="width: ' + Math.max(0, Math.min(100, share)) + '%"></span></div>' : "") +
      (sub ? '<small class="portal-usage-sub">' + esc(sub) + "</small>" : "") + "</div>"
    );
  }

  function usedOfTotal(part) {
    const used = Number(part && part.used_bytes);
    const total = Number(part && part.total_bytes);
    if (!Number.isFinite(total) || total <= 0) return { text: "-", share: undefined };
    return { text: t("{used} of {total}", { used: formatBytes(used), total: formatBytes(total) }), share: Math.round((used / total) * 100) };
  }

  function adminHostHtml() {
    const host = admin.host;
    if (!host) return "";
    const info = host.info || null;
    const reported = host.reported_at ? t("reported {when}", { when: formatAgo(host.reported_at) }) : "";
    const cards = [
      hostCardHtml(
        t("App host"),
        host.reachable ? t("Reachable") : t("Not reachable"),
        info && info.version ? t("version {version}", { version: info.version }) + (reported ? " \u00b7 " + reported : "") : reported
      ),
    ];
    if (info) {
      const memory = usedOfTotal(info.memory);
      const store = usedOfTotal(info.disk && info.disk.store);
      const local = usedOfTotal(info.disk && info.disk.local);
      cards.push(hostCardHtml(t("Apps running"), t("{running} of {total}", { running: formatCount(info.apps_running), total: formatCount(info.apps_total) }), ""));
      cards.push(hostCardHtml(t("Memory"), memory.text, "", memory.share));
      cards.push(hostCardHtml(t("CPU"), typeof info.cpu_percent === "number" ? info.cpu_percent.toFixed(1) + "%" : "-", ""));
      cards.push(hostCardHtml(t("Disk: the apps' volume"), store.text, "", store.share));
      cards.push(hostCardHtml(t("Disk: the host's own"), local.text, "", local.share));
      cards.push(hostCardHtml(
        t("Isolation"),
        info.isolation_mode === "hidden" ? t("Hidden volume") : info.isolation_mode === "sandbox" ? t("Sandbox") : String(info.isolation_mode || "-"),
        info.isolation_mode === "hidden" ? t("apps cannot see the volume; they save through the SDK") : info.isolation_mode === "sandbox" ? t("each app sees only its own folder") : ""
      ));
    }
    return '<div class="portal-admin-summary-grid portal-admin-usage-summary portal-admin-host" aria-label="' + esc(t("App host")) + '">' + cards.join("") + "</div>";
  }

  function adminRowHtml(app) {
    const state = appState(app, null);
    const badges = [];
    if (app.is_template) badges.push('<span class="portal-admin-role-badge">' + esc(t("Template")) + "</span>");
    if (app.anyone_pending_approval) badges.push('<span class="portal-admin-status-badge is-danger">' + esc(t("Waiting for approval")) + "</span>");
    const menuId = "admin-app-menu-" + esc(app.id);
    const items = ['<button type="button" role="menuitem" data-admin-app-manage="' + esc(app.id) + '">' + esc(t("Manage")) + "</button>"];
    const url = safeUrl(app.url);
    if (url && app.deployed) {
      items.push('<button type="button" role="menuitem" data-admin-app-open="' + esc(url) + '">' + esc(t("Open in a new tab")) + "</button>");
    }
    if (app.visibility === "anyone") {
      items.push(
        app.anyone_pending_approval
          ? '<button type="button" role="menuitem" data-admin-app-approve="' + esc(app.id) + '">' + esc(t("Approve for anyone")) + "</button>"
          : '<button type="button" role="menuitem" data-admin-app-unapprove="' + esc(app.id) + '">' + esc(t("Withdraw approval")) + "</button>"
      );
    }
    items.push('<button type="button" role="menuitem" data-admin-app-transfer="' + esc(app.id) + '">' + esc(t("Transfer owner")) + "</button>");
    const takenDown = app.enabled === false && app.disabled_reason !== "owner_inactive";
    items.push(
      takenDown
        ? '<button type="button" role="menuitem" data-admin-app-restore="' + esc(app.id) + '">' + esc(t("Restore")) + "</button>"
        : '<button type="button" role="menuitem" class="is-danger" data-admin-app-takedown="' + esc(app.id) + '">' + esc(t("Take down")) + "</button>"
    );
    const number = function (value, format) { return value === null ? "-" : format(value); };
    const owner = app.owner || {};
    return (
      '<tr data-admin-app-row="' + esc(app.id) + '">' +
      '<td><div class="portal-admin-member-identity"><strong><button type="button" class="portal-admin-member-name" data-admin-app-manage="' + esc(app.id) + '">' + esc(app.name) + "</button> " + badges.join(" ") + "</strong>" +
      "<small>/" + esc(app.slug) + "/</small></div></td>" +
      '<td><div class="portal-admin-member-identity"><strong>' + esc(ownerName(app)) + "</strong><small>" + esc(owner.username ? "@" + owner.username : "") + (owner.group_name ? " \u00b7 " + esc(owner.group_name) : "") + "</small></div></td>" +
      "<td>" + esc(audienceLabel(app.visibility)) + "</td>" +
      '<td title="' + esc(state.reason) + '">' + stateBadgeHtml(state) + "</td>" +
      '<td class="is-number">' + esc(number(adminMetric(app, "requests_7d"), formatCount)) + "</td>" +
      '<td class="is-number">' + esc(number(adminMetric(app, "credits_7d"), formatCredits)) + "</td>" +
      '<td class="is-number">' + esc(number(adminMetric(app, "rss_bytes"), formatBytes)) + "</td>" +
      '<td class="is-number">' + esc(number(adminMetric(app, "data_bytes"), formatBytes)) + "</td>" +
      '<td class="portal-admin-actions-cell"><div class="portal-admin-row-actions">' +
      '<button type="button" class="portal-admin-row-menu-button" popovertarget="' + menuId + '" aria-haspopup="menu" aria-label="' + esc(t("Actions for {name}", { name: app.name })) + '" data-admin-row-menu-trigger>' +
      '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg></button>' +
      '<div id="' + menuId + '" class="portal-admin-row-menu" popover role="menu" aria-label="' + esc(t("Actions for {name}", { name: app.name })) + '" data-admin-row-menu>' + items.join("") + "</div>" +
      "</div></td>" +
      "</tr>"
    );
  }

  function drawAdminRows() {
    const root = admin.root;
    if (!root || !root.isConnected) return;
    const body = root.querySelector("[data-admin-apps-rows]");
    const wrap = root.querySelector("[data-admin-apps-table-wrap]");
    const empty = root.querySelector("[data-admin-apps-empty]");
    const count = root.querySelector("[data-admin-apps-count]");
    const state = root.querySelector("[data-admin-apps-state]");
    const host = root.querySelector("[data-admin-apps-host]");
    if (host) host.innerHTML = adminHostHtml();
    if (admin.apps === null) {
      if (wrap) wrap.hidden = true;
      if (empty) empty.hidden = true;
      if (state) {
        state.hidden = false;
        state.className = "portal-inline-state " + (admin.error ? "is-error" : "is-visible");
        state.textContent = admin.error || t("Loading apps\u2026");
      }
      return;
    }
    if (state) {
      // The class alone shows the box (app.css), so it goes with the attribute.
      state.hidden = true;
      state.className = "portal-inline-state";
    }
    const filter = root.querySelector("[data-admin-apps-filter]");
    if (filter) {
      Array.prototype.forEach.call(filter.options, function (option) {
        const item = ADMIN_FILTERS.filter(function (candidate) { return candidate.value === option.value; })[0];
        if (item) option.textContent = tv(item.label, { n: admin.apps.filter(item.test).length });
      });
    }
    root.querySelectorAll("th[data-sort-key]").forEach(function (header) {
      header.setAttribute("aria-sort", header.dataset.sortKey === admin.sortKey ? (admin.sortDir > 0 ? "ascending" : "descending") : "none");
    });
    const rows = adminRows();
    if (count) count.textContent = t("{n} shown", { n: rows.length });
    if (body) body.innerHTML = rows.map(adminRowHtml).join("");
    if (wrap) wrap.hidden = rows.length === 0;
    if (empty) {
      empty.hidden = rows.length !== 0;
      empty.textContent = admin.apps.length ? t("No apps match.") : t("Nobody has registered an app yet.");
    }
  }

  // The list could not be read. Before the first one there is nowhere to say
  // so but in its place; once a list is on the screen it stays (what it
  // shows may be a little old) and the failure is said beside it: a Refresh
  // that changes nothing would look like one that found nothing new.
  function adminLoadFailed(message) {
    const hadList = admin.apps !== null;
    admin.error = message;
    drawAdminRows();
    if (hadList && admin.root && admin.root.isConnected) {
      toast(t("The list of apps could not be refreshed: {error}", { error: message }), "error");
    }
  }

  async function loadAdminApps() {
    const seq = ++admin.seq;
    let data = null;
    try {
      data = await request(API + "/admin/overview");
    } catch (error) {
      if (error.status === 404) {
        // A Portal without the overview yet: the plain list, without figures or the host.
        try {
          const list = await request(API + "?scope=all");
          data = { apps: (list && list.apps) || [], host: null };
        } catch (fallbackError) {
          if (seq === admin.seq) adminLoadFailed(fallbackError.message);
          return;
        }
      } else {
        if (seq === admin.seq) adminLoadFailed(error.message);
        return;
      }
    }
    if (seq !== admin.seq) return;
    admin.apps = Array.isArray(data && data.apps) ? data.apps : [];
    admin.host = (data && data.host) || null;
    admin.error = "";
    drawAdminRows();
  }

  function initAdminPanel(root) {
    admin.root = root;
    admin.apps = null;
    admin.host = null;
    admin.error = "";
    admin.query = "";
    admin.filter = "all";
    drawAdminRows();
    loadAdminApps();
  }

  function adminApp(appId) {
    return (admin.apps || []).filter(function (app) { return app.id === appId; })[0] || null;
  }

  function closeRowMenus() {
    document.querySelectorAll("[data-admin-row-menu]:popover-open").forEach(function (menu) {
      try {
        menu.hidePopover();
      } catch (_error) {
        /* already closed */
      }
    });
  }

  // An administrator's action. The dialog that asked stays open while it runs
  // and shows what went wrong; the table is read again once something was tried.
  async function adminAct(ask, question, run, done) {
    let tried = false;
    const answer = await ask(Object.assign({}, question, {
      action: function (value) {
        tried = true;
        return run(value);
      },
    }));
    if (answer !== null && answer !== false) toast(done(answer), "success");
    if (tried) await loadAdminApps();
  }

  async function adminTakeDown(appId) {
    const app = adminApp(appId);
    if (!app) return;
    await adminAct(window.showPrompt, {
      title: t("Take {name} down?", { name: app.name }),
      message: t("Nobody can open the app until it is restored. Visitors read this reason instead of the app, so write it for them."),
      placeholder: t("Why it is taken down"),
      confirmText: t("Take down"),
      busyText: t("Taking down\u2026"),
      danger: true,
      required: true,
    }, function (reason) {
      return request(appPath(appId, "/disable"), { method: "POST", json: { reason: String(reason).trim() } });
    }, function () {
      return t("{name} is taken down.", { name: app.name });
    });
  }

  async function adminRestore(appId) {
    const app = adminApp(appId);
    if (!app) return;
    await adminAct(window.showConfirm, {
      title: t("Restore {name}?", { name: app.name }),
      message: t("The app can be opened again by everyone it is open to."),
      confirmText: t("Restore"),
      busyText: t("Restoring\u2026"),
    }, function () {
      return request(appPath(appId, "/enable"), { method: "POST" });
    }, function () {
      return t("{name} is restored.", { name: app.name });
    });
  }

  async function adminApprove(appId, approved) {
    const app = adminApp(appId);
    if (!app) return;
    await adminAct(window.showConfirm, approved
      ? {
          title: t("Open {name} to anyone?", { name: app.name }),
          message: t("Nobody has to sign in to use it from then on. Its code runs inside the cluster and may use {owner}'s model provider and connectors, so approve only what you have looked at.", { owner: ownerName(app) }),
          confirmText: t("Approve"),
          busyText: t("Saving\u2026"),
        }
      : {
          title: t("Withdraw the approval of {name}?", { name: app.name }),
          message: t("Only signed-in members can open it from then on."),
          confirmText: t("Withdraw approval"),
          busyText: t("Saving\u2026"),
        }, function () {
      return request(appPath(appId, "/anyone-approval"), { method: "POST", json: { approved: approved } });
    }, function () {
      return approved ? t("{name} is open to anyone.", { name: app.name }) : t("{name} asks for sign-in again.", { name: app.name });
    });
  }

  async function adminTransfer(appId) {
    const app = adminApp(appId);
    if (!app) return;
    await adminAct(window.showPrompt, {
      title: t("Transfer {name}", { name: app.name }),
      message: t("Enter the new owner's Portal username. The app gets a new token, and its model provider and connectors are switched off: from then on it would use the new owner's."),
      placeholder: t("Portal username"),
      confirmText: t("Transfer"),
      busyText: t("Transferring\u2026"),
      required: true,
    }, function (username) {
      return request(appPath(appId, "/transfer"), { method: "POST", json: { username: String(username).trim() } });
    }, function (username) {
      return t("{name} now belongs to {username}.", { name: app.name, username: String(username).trim() });
    });
  }

  // ---- what the shell calls ---------------------------------------------------

  // Leaving the section: nothing keeps asking the server on a page nobody sees.
  function leave() {
    stopCenter();
    stopPage();
    closeCreateDialog();
  }

  // ---- events -------------------------------------------------------------------

  // Every page above arrives as markup, so its controls are bound once, here,
  // by what they carry rather than one by one.
  function bindEvents() {
    document.addEventListener("click", function (event) {
      const target = event.target;
      if (!target || !target.closest) return;
      const hit = function (selector) { return target.closest(selector); };
      let el;

      // New app
      if ((el = hit("[data-app-create-open]"))) { event.preventDefault(); openCreateDialog(); return; }
      if ((el = hit("[data-app-create-close]"))) { event.preventDefault(); closeCreateDialog(); return; }
      if ((el = hit("[data-app-inspect-again]"))) { event.preventDefault(); scheduleInspect(true); return; }
      if ((el = hit("[data-template-use]"))) { event.preventDefault(); openCreateDialog({ templateId: el.dataset.templateUse }); return; }
      if ((el = hit("[data-app-copy-text]"))) {
        event.preventDefault();
        copyToClipboard(el.dataset.appCopyText).then(function (copied) { toast(copied ? t("Copied") : t("Copy failed")); });
        return;
      }

      // The app center
      if ((el = hit("[data-apps-view-open]"))) { event.preventDefault(); if (shell()) shell().openCenter(el.dataset.appsViewOpen || ""); return; }
      if ((el = hit("[data-app-manage]"))) { event.preventDefault(); if (shell()) shell().openApp(el.dataset.appManage, ""); return; }
      if ((el = hit("[data-app-copy-link]"))) {
        event.preventDefault();
        copyToClipboard(el.dataset.appCopyLink).then(function (copied) { toast(copied ? t("Link copied") : t("Copy failed")); });
        return;
      }
      if ((el = hit("[data-apps-retry]"))) {
        event.preventDefault();
        center.errors = { mine: "", available: "", templates: "" };
        drawCenterBody(true);
        refreshCenter().then(function () { if (center.visible) drawCenterBody(true); });
        return;
      }

      // The header over an open app
      if ((el = hit("[data-app-copy-current]"))) {
        const link = page.app ? safeUrl(page.app.url) : "";
        if (link) copyToClipboard(link).then(function (copied) { toast(copied ? t("Link copied") : t("Copy failed")); });
        return;
      }
      if ((el = hit("[data-app-open-current]"))) {
        if (page.app) openInNewTab(page.app.url);
        return;
      }

      // An app's page
      if ((el = hit("[data-app-tab]"))) { if (shell()) shell().openApp(page.appId, el.dataset.appTab); return; }
      if ((el = hit("[data-app-tab-open]"))) { event.preventDefault(); if (shell()) shell().openApp(page.appId, el.dataset.appTabOpen); return; }
      if ((el = hit("[data-app-lifecycle]"))) { runLifecycle(el.dataset.appLifecycle); return; }
      if ((el = hit("[data-app-deploy-latest]"))) { startDeploy({ latest: true }); return; }
      if ((el = hit("[data-app-deploy]"))) { startDeploy({}); return; }
      if ((el = hit("[data-app-upgrade]"))) { confirmUpgrade(el.dataset.appUpgrade); return; }
      if ((el = hit("[data-app-rollback]"))) { runRollback(); return; }
      if ((el = hit("[data-app-release-show]"))) { pickRelease(el.dataset.appReleaseShow); return; }
      if ((el = hit("[data-app-release-log-copy]"))) {
        const log = page.root && page.root.querySelector("[data-release-log]");
        copyToClipboard(log ? log.textContent : "").then(function (copied) { toast(copied ? t("Copied") : t("Copy failed")); });
        return;
      }
      if ((el = hit("[data-app-history-retry]"))) { page.releasesError = ""; drawHistory(); loadReleases(); return; }
      if ((el = hit("[data-app-log-pause]"))) { toggleLogPause(el); return; }
      if ((el = hit("[data-app-log-download]"))) { downloadLog(el); return; }
      if ((el = hit("[data-app-usage-retry]"))) { drawUsage(); return; }
      if ((el = hit("[data-app-data-retry]"))) { drawData(); return; }
      if ((el = hit("[data-app-data-clear]"))) { clearData(); return; }
      if ((el = hit("[data-app-activity-retry]"))) { drawActivity(); return; }

      // Settings
      if ((el = hit("[data-env-reveal]"))) {
        const value = el.closest("[data-env-row]").querySelector("[data-env-value]");
        const shown = value.classList.toggle("portal-app-masked") === false;
        el.setAttribute("aria-pressed", shown ? "true" : "false");
        el.classList.toggle("is-active", shown);
        return;
      }
      if ((el = hit("[data-env-remove]"))) { el.closest("[data-env-row]").remove(); envRowsChanged(); return; }
      if ((el = hit("[data-app-env-add]"))) {
        const rows = settingsSection("env").querySelector("[data-app-env-rows]");
        rows.insertAdjacentHTML("beforeend", envRowHtml("", ""));
        drawIcons(rows.lastElementChild);
        rows.lastElementChild.querySelector("[data-env-name]").focus();
        envRowsChanged();
        return;
      }
      if ((el = hit("[data-app-env-save]"))) { saveEnv(); return; }
      if ((el = hit("[data-app-token-reveal]"))) { revealToken(el); return; }
      if ((el = hit("[data-app-token-copy]"))) { copyToken(el); return; }
      if ((el = hit("[data-app-token-reset]"))) { resetToken(); return; }
      if ((el = hit("[data-app-template-publish]"))) { publishTemplateVersion(el); return; }
      if ((el = hit("[data-app-template-remove]"))) { removeTemplateVersion(el); return; }
      if ((el = hit("[data-app-delete]"))) { deleteApp(); return; }

      // Administration > Apps
      if ((el = hit("[data-admin-app-manage]"))) { closeRowMenus(); if (shell()) shell().openApp(el.dataset.adminAppManage, ""); return; }
      if ((el = hit("[data-admin-app-open]"))) { closeRowMenus(); openInNewTab(el.dataset.adminAppOpen); return; }
      if ((el = hit("[data-admin-app-approve]"))) { closeRowMenus(); adminApprove(el.dataset.adminAppApprove, true); return; }
      if ((el = hit("[data-admin-app-unapprove]"))) { closeRowMenus(); adminApprove(el.dataset.adminAppUnapprove, false); return; }
      if ((el = hit("[data-admin-app-transfer]"))) { closeRowMenus(); adminTransfer(el.dataset.adminAppTransfer); return; }
      if ((el = hit("[data-admin-app-restore]"))) { closeRowMenus(); adminRestore(el.dataset.adminAppRestore); return; }
      if ((el = hit("[data-admin-app-takedown]"))) { closeRowMenus(); adminTakeDown(el.dataset.adminAppTakedown); return; }
      if ((el = hit("[data-admin-apps-refresh]"))) {
        const refreshed = busy(el, t("Refreshing\u2026"));
        Promise.resolve(loadAdminApps()).then(refreshed, refreshed);
        return;
      }
      if ((el = hit("[data-admin-apps-panel] th[data-sort-key]"))) {
        const key = el.dataset.sortKey;
        if (admin.sortKey === key) admin.sortDir = -admin.sortDir;
        else {
          admin.sortKey = key;
          // Numbers start with the largest on top, words from A.
          admin.sortDir = el.dataset.sortType === "number" ? -1 : 1;
        }
        drawAdminRows();
      }
    });

    // A press on the dimmed area around the New app dialog closes it.
    document.addEventListener("mousedown", function (event) {
      const modal = createModal();
      if (modal && event.target === modal) closeCreateDialog();
    });

    document.addEventListener("change", function (event) {
      const target = event.target;
      if (!target || !target.matches) return;
      if (target.matches('#app-create-form input[name="app_source"]')) { setCreateMode(target.value, ""); return; }
      if (target.matches("#app-create-template")) { onTemplateChosen(); return; }
      if (target.matches("#app-create-version")) { drawSelectedTemplate(); syncCreateSubmit(); return; }
      if (target.matches('input[name="app-audience"]')) { changeAudience(target); return; }
      if (target.matches("[data-app-llm], [data-app-connector], [data-app-connector-write]")) { changeCapability(target); return; }
      if (target.matches("[data-app-usage-days]")) { page.usageDays = Number(target.value) || 30; drawUsage(); return; }
      if (target.matches("[data-app-template-toggle]")) { toggleTemplate(target); return; }
      if (target.matches("[data-app-template-tag]")) {
        const publish = settingsSection("template").querySelector("[data-app-template-publish]");
        if (publish) publish.disabled = !target.value;
        return;
      }
      if (target.matches("[data-admin-apps-filter]")) { admin.filter = target.value; drawAdminRows(); }
    });

    document.addEventListener("input", function (event) {
      const target = event.target;
      if (!target || !target.matches) return;
      if (target.matches("[data-apps-search]")) { center.query = target.value; drawCenterBody(false); return; }
      if (target.matches("#app-create-repo, #app-create-ref, #app-create-dir")) { scheduleInspect(false); return; }
      if (target.matches("#app-create-name")) { syncAddressPreview(); return; }
      if (target.matches("#app-create-template-name")) { syncCreateSubmit(); return; }
      if (target.matches("[data-env-name], [data-env-value]")) { envRowsChanged(); return; }
      if (target.matches("[data-admin-apps-search]")) { admin.query = target.value; drawAdminRows(); }
    });

    document.addEventListener("submit", function (event) {
      if (!event.target || event.target.id !== "app-create-form") return;
      event.preventDefault();
      submitCreate();
    });

    // The branches and tags of the Deploy field are read when the field is first used.
    document.addEventListener("focusin", function (event) {
      if (event.target && event.target.matches && event.target.matches("[data-app-deploy-ref]")) loadDeployRefs();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        // A confirm on top of the dialog takes the key first (dialogs.js).
        if (document.querySelector(".portal-dialog")) return;
        if (closeCreateDialog()) event.preventDefault();
        return;
      }
      // Left and right move between the parts of an app's page.
      const tab = event.target && event.target.closest ? event.target.closest("[data-app-tab]") : null;
      if (!tab || (event.key !== "ArrowRight" && event.key !== "ArrowLeft" && event.key !== "Home" && event.key !== "End")) return;
      const tabs = Array.prototype.slice.call(tab.parentNode.querySelectorAll("[data-app-tab]"));
      const index = tabs.indexOf(tab);
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else next = tabs.length - 1;
      event.preventDefault();
      tabs[next].focus();
      tabs[next].click();
    });

    // An icon that does not load gives way to the app's first letter.
    document.addEventListener("error", function (event) {
      const image = event.target;
      if (!image || image.tagName !== "IMG" || !image.parentNode || !image.parentNode.classList || !image.parentNode.classList.contains("portal-app-icon")) return;
      const holder = image.parentNode;
      holder.textContent = holder.dataset.appInitial || "?";
    }, true);

    // Administration > Apps arrives through htmx like the other administration
    // panels (assistant_setup.js, openAdminPanel); its rows are drawn here.
    document.addEventListener("htmx:afterSwap", function () {
      const root = document.querySelector("[data-admin-apps-panel]");
      if (root && root !== admin.root) initAdminPanel(root);
    });
  }

  if (HAS_DOM) {
    bindEvents();
    window.EfpApps = {
      showCenter: showCenter,
      showApp: showApp,
      heading: heading,
      leave: leave,
      openCreateDialog: openCreateDialog,
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      appInitial: appInitial,
      appState: appState,
      audienceLabel: audienceLabel,
      audienceWidens: audienceWidens,
      barHeights: barHeights,
      chooseView: chooseView,
      createSseParser: createSseParser,
      daysOfPeriod: daysOfPeriod,
      envFromRows: envFromRows,
      errorText: errorText,
      eventText: eventText,
      formatBytes: formatBytes,
      inspectProblem: inspectProblem,
      lifecycleButtonHtml: lifecycleButtonHtml,
      matchesQuery: matchesQuery,
      needsAttention: needsAttention,
      parseTime: parseTime,
      plausibleEntryDir: plausibleEntryDir,
      plausibleRef: plausibleRef,
      plausibleRepoUrl: plausibleRepoUrl,
      safeUrl: safeUrl,
      slugify: slugify,
      sortAvailable: sortAvailable,
      sortMine: sortMine,
      stateButtonHtml: stateButtonHtml,
      stepLabel: stepLabel,
      stepNote: stepNote,
    };
  }
})();
