/**
 * Pickers and forms for starter cards.
 *
 * A card that needs a value used to ask for it in a one-line text box. This
 * module replaces that box with two dialogs:
 *
 *   showPicker(options)   one value: a text box with a list under it. The list
 *                         is the member's own recent work from a source the
 *                         card names (GET /api/suggestions/{source}) and the
 *                         values they used with this card before. Clicking a
 *                         row answers; typing still works, always.
 *   showCardForm(card)    several values: one control per field (text, long
 *                         text, choices, a file), each text field with its
 *                         own list when it names a source.
 *
 * The list never stands between the member and the text box. While it loads,
 * when the connector is off, when the system does not answer: the box is
 * focused and takes whatever is typed or pasted, exactly as before.
 *
 * What a row puts into the prompt is its `value` only -- an issue key, a pull
 * request link, a branch and its repository. Titles and summaries are shown
 * and never sent: they are other people's text.
 *
 * The pure parts (filtering, the prompt template, history, labels) are
 * exported on window.portalCardPicker so they can be tested without a page.
 */
(function () {
  "use strict";

  const HISTORY_KEY = "efp.cards.recent:v1";
  const HISTORY_PER_FIELD = 5;
  const HISTORY_MAX_FIELDS = 60;
  const HISTORY_MAX_CHARS = 500;
  const SEARCH_DEBOUNCE_MS = 250;
  const MIN_SEARCH_CHARS = 2;
  // A hover warms the list and the click a moment later opens it: one request.
  const CLIENT_MEMO_MS = 30000;
  const PLACEHOLDER = /\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g;

  let dialogSeq = 0;

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ------------------------------------------------------------------ words

  const GROUP_LABELS = {
    history: N_("Used before"),
    viewed: N_("Recently viewed"),
    assigned: N_("Assigned to you"),
    reported: N_("Reported by you"),
    edited: N_("Edited by you"),
    review_requested: N_("Waiting for your review"),
    mine: N_("Opened by you"),
    no_pr: N_("Pushed recently, no pull request yet"),
    has_pr: N_("Already has a pull request"),
    pushed: N_("Pushed recently"),
    workspace: N_("In this assistant's workspace"),
    planned: N_("Planned in this workspace"),
    recent: N_("Recent"),
    all: N_("All"),
    accounts: N_("Accounts"),
    pr_checks: N_("Failed checks on your pull requests"),
    jenkins: N_("Failed builds of jobs you asked about"),
    failed: N_("With failures"),
    results: N_("Results"),
  };

  const STATUS_LABELS = {
    open: N_("Open"),
    draft: N_("Draft"),
    unstable: N_("Unstable"),
    uncommitted: N_("Uncommitted changes"),
    folder: N_("Folder"),
    queued: N_("Queued"),
    running: N_("Running"),
    done: N_("Done"),
    blocked: N_("Blocked"),
    stale: N_("Stale"),
    cancelled: N_("Cancelled"),
    pending_restart: N_("Waiting for a restart"),
  };

  const CONNECTOR_LABELS = {
    github: "GitHub",
    jira: "Jira",
    confluence: "Confluence",
    jenkins: "Jenkins",
    aws: "AWS",
  };

  function groupLabel(id) {
    return GROUP_LABELS[id] ? tv(GROUP_LABELS[id]) : "";
  }

  function connectorLabel(type) {
    return CONNECTOR_LABELS[type] || String(type || "");
  }

  /** The words for a row's state, counted where the state has a count. */
  function statusLabel(item) {
    const status = String((item && item.status) || "");
    const count = item && Number.isFinite(item.count) ? item.count : null;
    if (!status) return "";
    if (status === "failed") return count ? t("{n} failed", { n: count }) : t("Failed");
    if (status === "ahead") return count ? tn(count, "{n} commit ahead", "{n} commits ahead") : t("Ahead of its base");
    if (status === "scenarios") return tn(count || 0, "{n} scenario", "{n} scenarios");
    return STATUS_LABELS[status] ? tv(STATUS_LABELS[status]) : status;
  }

  function statusTone(status) {
    if (status === "failed") return "danger";
    if (status === "unstable" || status === "uncommitted" || status === "blocked") return "warning";
    if (status === "open" || status === "ahead" || status === "running") return "accent";
    return "";
  }

  /** Why there is no list, in words the member can act on. */
  function stateNote(result) {
    if (!result || result.state === "ok") return "";
    const connector = connectorLabel(result.connector);
    if (result.state === "not_configured") {
      return connector
        ? t("Set up {connector} in Connectors to pick from a list here.", { connector })
        : t("Set up the connector to pick from a list here.");
    }
    if (result.reason === "timeout") return t("The list took too long to load. Type or paste the value instead.");
    if (result.reason === "unauthorized" && connector) {
      return t("{connector} refused the connector's credentials. Type or paste the value instead.", { connector });
    }
    if (result.reason === "rate_limited" && connector) {
      return t("{connector} is limiting requests right now. Type or paste the value instead.", { connector });
    }
    return connector
      ? t("Could not reach {connector}. Type or paste the value instead.", { connector })
      : t("Could not load the list. Type or paste the value instead.");
  }

  /** "2 hours ago" in the page's language, or "" for anything unreadable. */
  function relativeTime(value, now) {
    const then = Date.parse(String(value || ""));
    if (!Number.isFinite(then)) return "";
    const seconds = Math.round((then - (now == null ? Date.now() : now)) / 1000);
    const steps = [
      ["year", 31536000],
      ["month", 2592000],
      ["day", 86400],
      ["hour", 3600],
      ["minute", 60],
    ];
    const locale = (typeof window !== "undefined" && window.EFP_I18N && window.EFP_I18N.locale) || "en";
    let formatter = null;
    try {
      formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    } catch (error) {
      formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
    }
    for (const [unit, size] of steps) {
      if (Math.abs(seconds) >= size) return formatter.format(Math.trunc(seconds / size), unit);
    }
    return formatter.format(0, "minute");
  }

  // ----------------------------------------------------------- pure helpers

  function itemMatches(item, needle) {
    if (!needle) return true;
    const haystack = `${item.title || ""} ${item.subtitle || ""} ${item.value || ""} ${item.badge || ""}`.toLowerCase();
    return needle.split(/\s+/).every((word) => haystack.includes(word));
  }

  /** The groups with only the rows that match what is typed; empty groups go. */
  function filterGroups(groups, query) {
    const needle = String(query || "").trim().toLowerCase();
    return (Array.isArray(groups) ? groups : [])
      .map((group) => ({ id: group.id, items: (group.items || []).filter((item) => itemMatches(item, needle)) }))
      .filter((group) => group.items.length);
  }

  /**
   * History as a group, without the values the live groups already list:
   * the live row says more (a title, a state) about the same thing.
   */
  function historyGroup(values, groups) {
    const listed = new Set();
    (groups || []).forEach((group) => (group.items || []).forEach((item) => listed.add(item.value)));
    const items = (values || []).filter((value) => value && !listed.has(value)).map((value) => ({ value, title: value }));
    return items.length ? { id: "history", items } : null;
  }

  function flatItems(groups) {
    const items = [];
    (groups || []).forEach((group) => (group.items || []).forEach((item) => items.push(item)));
    return items;
  }

  /**
   * The prompt for a form: every {{name}} replaced by that field's value, a
   * line whose placeholders all came out empty dropped (an optional field
   * left blank takes its sentence with it), blank runs collapsed.
   */
  function composeTemplate(template, values) {
    const lines = String(template || "").split("\n").map((line) => {
      let named = 0;
      let filled = 0;
      const text = line.replace(PLACEHOLDER, (match, name) => {
        named += 1;
        const value = String((values && values[name]) || "").trim();
        if (value) filled += 1;
        return value;
      });
      return named > 0 && filled === 0 ? null : text;
    });
    return lines
      .filter((line) => line !== null)
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  /** A source's params with {{field}} references filled from the form so far. */
  function resolveParams(params, values) {
    const out = {};
    Object.keys(params || {}).forEach((name) => {
      const raw = params[name];
      const fill = (text) => String(text).replace(PLACEHOLDER, (match, field) => String((values && values[field]) || ""));
      if (Array.isArray(raw)) {
        const list = raw.map(fill).map((text) => text.trim()).filter(Boolean);
        if (list.length) out[name] = list;
      } else if (raw !== null && raw !== undefined && raw !== false) {
        const text = fill(raw === true ? "true" : raw).trim();
        if (text) out[name] = [text];
      }
    });
    return out;
  }

  /** The fields a source's params read from, so their change refreshes it. */
  function paramDependencies(params) {
    const names = new Set();
    Object.keys(params || {}).forEach((name) => {
      const raw = params[name];
      (Array.isArray(raw) ? raw : [raw]).forEach((value) => {
        String(value == null ? "" : value).replace(PLACEHOLDER, (match, field) => {
          names.add(field);
          return match;
        });
      });
    });
    return Array.from(names);
  }

  function suggestionUrl(source, options) {
    const query = new URLSearchParams();
    const opts = options || {};
    if (opts.q) query.append("q", opts.q);
    if (opts.agentId) query.append("agent_id", opts.agentId);
    Object.keys(opts.params || {}).sort().forEach((name) => {
      (opts.params[name] || []).forEach((value) => query.append(`p.${name}`, value));
    });
    (opts.recent || []).forEach((value) => query.append("recent", value));
    const text = query.toString();
    return `/api/suggestions/${encodeURIComponent(source)}${text ? `?${text}` : ""}`;
  }

  /**
   * The values a member used with a card field, newest first, in this
   * browser. A convenience and nothing more: a browser without storage, or a
   * private window, simply has no history.
   */
  function createHistory(storage) {
    function read() {
      try {
        const parsed = JSON.parse((storage && storage.getItem(HISTORY_KEY)) || "{}");
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
      } catch (error) {
        return {};
      }
    }
    function write(data) {
      try {
        if (storage) storage.setItem(HISTORY_KEY, JSON.stringify(data));
      } catch (error) {
        /* storage is a convenience */
      }
    }
    return {
      get(key) {
        const entry = read()[key];
        return entry && Array.isArray(entry.values) ? entry.values.filter((value) => typeof value === "string") : [];
      },
      add(key, value) {
        const text = String(value == null ? "" : value).trim();
        if (!key || !text || text.length > HISTORY_MAX_CHARS) return;
        const data = read();
        const previous = data[key] && Array.isArray(data[key].values) ? data[key].values : [];
        data[key] = { at: Date.now(), values: [text].concat(previous.filter((item) => item !== text)).slice(0, HISTORY_PER_FIELD) };
        const keys = Object.keys(data);
        if (keys.length > HISTORY_MAX_FIELDS) {
          keys
            .sort((left, right) => (data[left].at || 0) - (data[right].at || 0))
            .slice(0, keys.length - HISTORY_MAX_FIELDS)
            .forEach((old) => delete data[old]);
        }
        write(data);
      },
    };
  }

  function historyKeyFor(card, fieldName) {
    return `${String((card && card.title) || "").trim()}::${fieldName || "input"}`;
  }

  // ----------------------------------------------------------------- client

  let sourcesPromise = null;
  const memo = new Map();

  /** The sources this Portal has, by id. A card naming another stays a text box. */
  function loadSources() {
    if (!sourcesPromise) {
      sourcesPromise = fetch("/api/suggestions", { credentials: "same-origin" })
        .then((response) => (response.ok ? response.json() : { sources: [] }))
        .then((body) => {
          const map = new Map();
          (Array.isArray(body && body.sources) ? body.sources : []).forEach((spec) => {
            if (spec && spec.id) map.set(String(spec.id), spec);
          });
          return map;
        })
        .catch(() => {
          // Ask again next time: a failed list must not turn every card
          // into a plain box for the life of the page.
          sourcesPromise = null;
          return new Map();
        });
    }
    return sourcesPromise;
  }

  function fetchSuggestions(source, options) {
    const url = suggestionUrl(source, options);
    const now = Date.now();
    const cached = memo.get(url);
    if (cached && now - cached.at < CLIENT_MEMO_MS) return cached.promise;
    const promise = fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((body) => {
        const result = {
          state: String((body && body.state) || "ok"),
          groups: Array.isArray(body && body.groups) ? body.groups : [],
          connector: String((body && body.connector) || ""),
          reason: String((body && body.reason) || ""),
          identity: String((body && body.identity) || ""),
        };
        // Only a list is worth remembering: a connector set up a moment ago,
        // or a system that is back, should show on the next open.
        if (result.state !== "ok") memo.delete(url);
        return result;
      })
      .catch(() => {
        memo.delete(url);
        return { state: "unavailable", groups: [], connector: "", reason: "error", identity: "" };
      });
    memo.set(url, { at: now, promise });
    if (memo.size > 200) memo.delete(memo.keys().next().value);
    return promise;
  }

  /** Warm a card's lists before it is clicked (called on hover and focus). */
  async function prefetchCard(card, agentId) {
    if (!card) return;
    const sources = await loadSources();
    const wanted = [];
    if (card.input && card.input.source) wanted.push([card.input, "input"]);
    (Array.isArray(card.fields) ? card.fields : []).forEach((field) => {
      // A list that waits on another field cannot be asked for yet.
      if (field && field.source && !paramDependencies(field.params).length) wanted.push([field, field.name]);
    });
    wanted.forEach(([field, name]) => {
      const spec = sources.get(field.source);
      if (!spec) return;
      fetchSuggestions(field.source, {
        params: resolveParams(field.params, {}),
        agentId: spec.uses_agent ? agentId : "",
        recent: field.source === "ci.failed_builds" ? usedBefore.get(historyKeyFor(card, name)) : [],
      });
    });
  }

  // Two lists are worth a glance before the card is even opened: work that is
  // waiting on the member. A card whose input names one of these sources gets
  // a small count on it, short enough to sit beside the card's title on a
  // narrow screen. That is all the "notification" there is: the same
  // request the picker would make on the click, made a moment earlier, and at
  // most these two per new chat.
  const CARD_HINTS = {
    "github.pull_requests": { group: "review_requested", tone: "accent", label: (n) => t("{n} to review", { n }) },
    "ci.failed_builds": { group: "pr_checks", tone: "danger", label: (n) => t("{n} failed", { n }) },
  };

  /** `{ text, tone }` for a card that has work waiting behind it, else null. */
  async function cardHint(card, agentId) {
    const source = card && card.input ? card.input.source : "";
    const rule = CARD_HINTS[source];
    if (!rule) return null;
    const sources = await loadSources();
    const spec = sources.get(source);
    if (!spec) return null;
    // The same request the picker makes when the card is opened, so this is
    // also what warms it.
    const result = await fetchSuggestions(source, {
      params: resolveParams(card.input.params, {}),
      agentId: spec.uses_agent ? agentId : "",
      recent: source === "ci.failed_builds" ? usedBefore.get(historyKeyFor(card, "input")) : [],
    });
    if (result.state !== "ok") return null;
    const group = result.groups.find((entry) => entry.id === rule.group);
    const count = group && Array.isArray(group.items) ? group.items.length : 0;
    return count ? { text: rule.label(count), tone: rule.tone } : null;
  }

  const usedBefore = createHistory(typeof window !== "undefined" ? safeStorage() : null);

  function safeStorage() {
    try {
      return window.localStorage;
    } catch (error) {
      return null;
    }
  }

  // --------------------------------------------------------- list component

  /**
   * A list of suggestions under a text input.
   *
   * It owns the rows and the highlighted one; the input stays the member's.
   * `onPick(item)` fires for a click or for Enter on a highlighted row.
   */
  function createSuggestionList(config) {
    const listEl = config.listEl;
    const noteEl = config.noteEl || null;
    const inputEl = config.inputEl;
    const idPrefix = config.idPrefix;
    const historyValues = config.historyKey ? usedBefore.get(config.historyKey) : [];
    let spec = null;
    let initial = null;
    let shown = [];
    let rows = [];
    let active = -1;
    let query = "";
    let token = 0;
    let timer = null;
    let destroyed = false;
    let busy = false;

    function requestOptions(q) {
      return {
        q: q || "",
        params: typeof config.params === "function" ? config.params() : config.params || {},
        agentId: spec && spec.uses_agent ? config.agentId || "" : "",
        // The values used before tell this source which jobs to look at.
        recent: config.source === "ci.failed_builds" ? historyValues : [],
      };
    }

    function setNote(text) {
      if (!noteEl) return;
      noteEl.textContent = text || "";
      noteEl.classList.toggle("hidden", !text);
    }

    function setActive(index) {
      active = rows.length ? Math.max(-1, Math.min(index, rows.length - 1)) : -1;
      listEl.querySelectorAll("[data-picker-row]").forEach((row) => {
        const on = Number(row.dataset.pickerRow) === active;
        row.classList.toggle("is-active", on);
        row.setAttribute("aria-selected", on ? "true" : "false");
        if (on && typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
      });
      if (inputEl) {
        if (active >= 0) inputEl.setAttribute("aria-activedescendant", `${idPrefix}-row-${active}`);
        else inputEl.removeAttribute("aria-activedescendant");
      }
    }

    function rowMarkup(item, index) {
      const status = statusLabel(item);
      const tone = statusTone(item.status);
      const when = relativeTime(item.meta);
      return `
        <div class="portal-picker-row" role="option" id="${idPrefix}-row-${index}" data-picker-row="${index}" aria-selected="false">
          <span class="portal-picker-row-main">
            <span class="portal-picker-row-title">${esc(item.title || item.value)}</span>
            ${item.subtitle ? `<span class="portal-picker-row-sub">${esc(item.subtitle)}</span>` : ""}
          </span>
          <span class="portal-picker-row-side">
            ${item.badge ? `<span class="portal-picker-badge">${esc(item.badge)}</span>` : ""}
            ${status ? `<span class="portal-picker-badge${tone ? ` is-${tone}` : ""}">${esc(status)}</span>` : ""}
            ${when ? `<span class="portal-picker-row-meta">${esc(when)}</span>` : ""}
          </span>
        </div>`;
    }

    function render(groups, options) {
      if (destroyed) return;
      const withHistory = (groups || []).slice();
      const past = historyGroup(historyValues, groups);
      if (past) {
        const kept = filterGroups([past], query)[0];
        if (kept) withHistory.push(kept);
      }
      shown = withHistory;
      rows = flatItems(shown);
      let index = 0;
      const loading = Boolean(options && options.loading);
      busy = loading;
      listEl.innerHTML =
        shown
          .map((group) => {
            const label = groupLabel(group.id);
            const body = group.items.map((item) => rowMarkup(item, index++)).join("");
            return `<div class="portal-picker-group" role="group"${label ? ` aria-label="${esc(label)}"` : ""}>${
              label ? `<div class="portal-picker-group-label">${esc(label)}</div>` : ""
            }${body}</div>`;
          })
          .join("") +
        (loading ? `<div class="portal-picker-loading" aria-hidden="true"><span></span><span></span><span></span></div>` : "");
      if (!config.inline) listEl.classList.toggle("hidden", !rows.length && !loading);
      listEl.setAttribute("aria-busy", loading ? "true" : "false");
      setActive(-1);
      if (typeof config.onRender === "function") config.onRender(rows.length);
    }

    function show(result, local) {
      const groups = local ? filterGroups(result.groups, query) : result.groups;
      render(groups);
      if (result.state !== "ok") setNote(stateNote(result));
      else if (result.identity === "shared" && rows.length) {
        setNote(t("Listed for your username: this connector signs in with shared credentials."));
      } else if (result.identity === "unknown" && !rows.length && spec && spec.searchable) {
        setNote(t("Type to search."));
      } else setNote("");
      if (typeof config.onState === "function") config.onState(result);
    }

    async function load() {
      const sources = config.source ? await loadSources() : new Map();
      if (destroyed) return;
      spec = config.source ? sources.get(config.source) || null : null;
      if (!spec) {
        // No source, or one this Portal does not have: history is the list.
        initial = { state: "ok", groups: [], identity: "" };
        show(initial, true);
        return;
      }
      const mine = ++token;
      render([], { loading: true });
      const result = await fetchSuggestions(config.source, requestOptions(""));
      if (destroyed || mine !== token) return;
      initial = result;
      show(result, true);
    }

    function search() {
      const mine = ++token;
      const asked = query;
      fetchSuggestions(config.source, requestOptions(asked)).then((result) => {
        if (destroyed || mine !== token || asked !== query) return;
        // An empty search keeps what matched locally: a pasted link or a key
        // from another project finds nothing, and that is not an error.
        if (result.state === "ok" && !flatItems(result.groups).length && initial) show(initial, true);
        else show(result, false);
      });
    }

    function setQuery(text) {
      query = String(text || "").trim();
      if (timer) clearTimeout(timer);
      if (initial) show(initial, true);
      if (!spec || !spec.searchable || initial === null) return;
      if (query.length < MIN_SEARCH_CHARS) {
        token += 1;
        return;
      }
      timer = setTimeout(search, SEARCH_DEBOUNCE_MS);
    }

    function reload() {
      if (timer) clearTimeout(timer);
      initial = null;
      load();
    }

    listEl.addEventListener("mousemove", (event) => {
      const row = event.target.closest ? event.target.closest("[data-picker-row]") : null;
      if (row && Number(row.dataset.pickerRow) !== active) setActive(Number(row.dataset.pickerRow));
    });
    // mousedown, not click: the input must not lose focus to the row first.
    listEl.addEventListener("mousedown", (event) => {
      const row = event.target.closest ? event.target.closest("[data-picker-row]") : null;
      if (!row) return;
      event.preventDefault();
      const item = rows[Number(row.dataset.pickerRow)];
      if (item) config.onPick(item);
    });

    return {
      load,
      reload,
      setQuery,
      move(delta) {
        if (!rows.length) return;
        const next = active + delta;
        setActive(next < 0 ? rows.length - 1 : next >= rows.length ? 0 : next);
      },
      activeItem() {
        return active >= 0 ? rows[active] : null;
      },
      clearActive() {
        setActive(-1);
      },
      count() {
        return rows.length;
      },
      isLoading() {
        return busy;
      },
      destroy() {
        destroyed = true;
        if (timer) clearTimeout(timer);
      },
    };
  }

  // ------------------------------------------------------------ dialog shell

  const FOCUSABLE = 'a[href],button:not([disabled]),textarea,input:not([type="hidden"]),select,[tabindex]:not([tabindex="-1"])';

  function openShell(title, bodyHtml, confirmText, extraClass) {
    dialogSeq += 1;
    const id = `portal-picker-${dialogSeq}`;
    const previouslyFocused = document.activeElement;
    const root = document.createElement("div");
    root.className = "modal portal-dialog portal-picker-root";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", `${id}-title`);
    root.innerHTML =
      `<div class="modal-card panel portal-dialog-card portal-picker-dialog${extraClass ? ` ${extraClass}` : ""}" data-dialog-card>` +
      `<div class="portal-modal-titlebar"><h3 id="${id}-title">${esc(title)}</h3></div>` +
      bodyHtml(id) +
      `<div class="portal-modal-actions portal-picker-actions">` +
      `<button type="button" class="portal-btn" data-dialog-cancel>${esc(t("Cancel"))}</button>` +
      `<button type="button" class="portal-btn is-primary" data-dialog-confirm>${esc(confirmText)}</button>` +
      `</div></div>`;
    document.body.appendChild(root);
    // Force reflow so the enter transition runs, like dialogs.js.
    root.offsetWidth; // eslint-disable-line no-unused-expressions
    root.classList.add("portal-dialog--in");

    let closed = false;
    let onEscape = null;
    function onKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (onEscape) onEscape();
        return;
      }
      if (event.key !== "Tab") return;
      const items = Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), (el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeydown, true);

    return {
      id,
      root,
      confirmEl: root.querySelector("[data-dialog-confirm]"),
      cancelEl: root.querySelector("[data-dialog-cancel]"),
      setEscape(handler) {
        onEscape = handler;
      },
      close() {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKeydown, true);
        root.classList.remove("portal-dialog--in");
        window.setTimeout(() => {
          if (root.parentNode) root.parentNode.removeChild(root);
          if (previouslyFocused && typeof previouslyFocused.focus === "function" && document.contains(previouslyFocused)) {
            try {
              previouslyFocused.focus();
            } catch (error) {
              /* noop */
            }
          }
        }, 200);
      },
    };
  }

  function openConnectors() {
    const button = document.getElementById("connectors-menu-btn");
    if (button) button.click();
  }

  function noteMarkup(id) {
    return (
      `<p class="portal-picker-note hidden" id="${id}-note" data-picker-note></p>` +
      `<button type="button" class="portal-link-inline portal-picker-connect hidden" data-picker-connect>${esc(t("Open Connectors"))}</button>`
    );
  }

  // ----------------------------------------------------------------- picker

  /**
   * Ask for one value.
   *
   * options: title, label, placeholder, source, params, multiline, agentId,
   * historyKey, confirmText. Resolves to { value, item } -- item is the row
   * that was picked, or null for typed text -- or to null on cancel.
   */
  function showPicker(options) {
    const opts = options || {};
    const multiline = Boolean(opts.multiline) && !opts.source;
    const shell = openShell(
      opts.title || t("Start this"),
      (id) =>
        `<label class="portal-picker-label" for="${id}-input">${esc(opts.label || t("Details"))}</label>` +
        (multiline
          ? `<textarea id="${id}-input" class="portal-form-input portal-picker-input portal-picker-textarea" rows="4" ` +
            `placeholder="${esc(opts.placeholder || "")}"></textarea>`
          : `<input id="${id}-input" type="text" class="portal-form-input portal-picker-input" autocomplete="off" ` +
            `spellcheck="false" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="${id}-list" ` +
            `placeholder="${esc(opts.placeholder || "")}" />`) +
        `<div class="portal-picker-list hidden" id="${id}-list" role="listbox" aria-label="${esc(opts.label || t("Details"))}" data-picker-list></div>` +
        noteMarkup(id),
      opts.confirmText || t("Continue")
    );
    const input = shell.root.querySelector(".portal-picker-input");
    const connectEl = shell.root.querySelector("[data-picker-connect]");

    return new Promise((resolve) => {
      let settled = false;
      const list = createSuggestionList({
        listEl: shell.root.querySelector("[data-picker-list]"),
        noteEl: shell.root.querySelector("[data-picker-note]"),
        inputEl: input,
        idPrefix: shell.id,
        source: opts.source || "",
        params: resolveParams(opts.params, {}),
        agentId: opts.agentId || "",
        historyKey: opts.historyKey || "",
        onPick: (item) => finish({ value: String(item.value), item }),
        onState: (result) => connectEl.classList.toggle("hidden", result.state !== "not_configured"),
      });

      function finish(result) {
        if (settled) return;
        settled = true;
        if (result && opts.historyKey) usedBefore.add(opts.historyKey, result.value);
        list.destroy();
        shell.close();
        resolve(result);
      }

      function confirm() {
        const picked = list.activeItem();
        if (picked) {
          finish({ value: String(picked.value), item: picked });
          return;
        }
        const text = input.value.trim();
        if (!text) {
          input.focus();
          input.classList.add("portal-dialog-input--invalid");
          return;
        }
        finish({ value: text, item: null });
      }

      shell.setEscape(() => finish(null));
      shell.confirmEl.addEventListener("click", confirm);
      shell.cancelEl.addEventListener("click", () => finish(null));
      connectEl.addEventListener("click", () => {
        finish(null);
        openConnectors();
      });
      input.addEventListener("input", () => {
        input.classList.remove("portal-dialog-input--invalid");
        list.setQuery(multiline ? "" : input.value);
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" && !multiline) {
          event.preventDefault();
          list.move(1);
        } else if (event.key === "ArrowUp" && !multiline) {
          event.preventDefault();
          list.move(-1);
        } else if (event.key === "Enter" && !event.isComposing) {
          // A long answer keeps Enter for new lines; Ctrl or Cmd sends it.
          if (multiline && !(event.ctrlKey || event.metaKey)) return;
          event.preventDefault();
          confirm();
        }
      });

      list.load();
      window.requestAnimationFrame(() => input.focus());
    });
  }

  // ------------------------------------------------------------------- form

  function optionList(field) {
    return (Array.isArray(field.options) ? field.options : [])
      .map((option) =>
        option && typeof option === "object"
          ? { value: String(option.value == null ? "" : option.value), label: String(option.label || option.value || "") }
          : { value: String(option), label: String(option) }
      )
      .filter((option) => option.value);
  }

  function defaultValues(field) {
    if (Array.isArray(field.default)) return field.default.map(String);
    return field.default == null || field.default === "" ? [] : [String(field.default)];
  }

  function fieldMarkup(field, id) {
    const inputId = `${id}-f-${field.name}`;
    const required = field.required !== false;
    const label =
      `<label class="portal-picker-label" for="${inputId}">${esc(field.label || field.name)}` +
      `${required ? "" : ` <span class="portal-picker-optional">${esc(t("optional"))}</span>`}</label>`;
    let control = "";
    if (field.type === "choice") {
      const chosen = new Set(defaultValues(field));
      const kind = field.multiple ? "checkbox" : "radio";
      control =
        `<div class="portal-picker-choices" role="${field.multiple ? "group" : "radiogroup"}" aria-label="${esc(field.label || field.name)}">` +
        optionList(field)
          .map(
            (option, index) =>
              `<label class="portal-picker-choice"><input type="${kind}" name="${inputId}" value="${esc(option.value)}"` +
              `${index === 0 ? ` id="${inputId}"` : ""}${chosen.has(option.value) ? " checked" : ""} />` +
              `<span>${esc(option.label)}</span></label>`
          )
          .join("") +
        `</div>`;
    } else if (field.type === "file") {
      const accept = Array.isArray(field.accept) ? field.accept.join(",") : "";
      control = `<input id="${inputId}" type="file" class="portal-picker-file"${accept ? ` accept="${esc(accept)}"` : ""} />`;
    } else if (field.type === "textarea") {
      control =
        `<textarea id="${inputId}" class="portal-form-input portal-picker-textarea" rows="3" ` +
        `placeholder="${esc(field.placeholder || "")}">${esc(defaultValues(field)[0] || "")}</textarea>`;
    } else {
      control =
        `<input id="${inputId}" type="text" class="portal-form-input portal-picker-input" autocomplete="off" spellcheck="false" ` +
        `role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="${inputId}-list" ` +
        `placeholder="${esc(field.placeholder || "")}" value="${esc(defaultValues(field)[0] || "")}" />` +
        `<div class="portal-picker-list is-inline hidden" id="${inputId}-list" role="listbox" aria-label="${esc(field.label || field.name)}" data-picker-list></div>` +
        `<p class="portal-picker-note hidden" data-picker-note></p>`;
    }
    return `<div class="portal-picker-field" data-picker-field="${esc(field.name)}">${label}${control}</div>`;
  }

  /**
   * Ask for a card's fields.
   *
   * Resolves to { prompt, values, files } or to null on cancel. `files` are
   * the File objects of the card's file fields, for the caller to attach.
   */
  function showCardForm(card, options) {
    const opts = options || {};
    const fields = (Array.isArray(card && card.fields) ? card.fields : []).filter((field) => field && field.name);
    const shell = openShell(
      card.title || t("Start this"),
      (id) => `<div class="portal-picker-form" data-picker-form>${fields.map((field) => fieldMarkup(field, id)).join("")}</div>`,
      t("Continue"),
      "is-form"
    );
    const controls = new Map();

    return new Promise((resolve) => {
      let settled = false;

      function finish(result) {
        if (settled) return;
        settled = true;
        controls.forEach((control) => control.list && control.list.destroy());
        shell.close();
        resolve(result);
      }

      function valueOf(field) {
        const control = controls.get(field.name);
        if (field.type === "choice") {
          return Array.prototype.filter
            .call(control.el.querySelectorAll("input"), (input) => input.checked)
            .map((input) => input.value)
            .join(", ");
        }
        if (field.type === "file") {
          const file = control.input.files && control.input.files[0];
          return file ? file.name : "";
        }
        return control.input.value.trim();
      }

      function allValues() {
        const values = {};
        fields.forEach((field) => {
          values[field.name] = valueOf(field);
        });
        return values;
      }

      /** A picked row can answer other fields too: the branch knows its base. */
      function spread(item, from) {
        const extra = (item && item.fields) || {};
        fields.forEach((field) => {
          if (field.name === from || field.type === "choice" || field.type === "file") return;
          const control = controls.get(field.name);
          const offered = extra[field.name];
          if (!offered || control.touched || control.input.value.trim()) return;
          control.input.value = String(offered);
        });
      }

      function changed(name) {
        controls.forEach((control, other) => {
          if (other !== name && control.list && control.depends.includes(name)) control.list.reload();
        });
      }

      let openField = "";

      function showList(name) {
        openField = name;
        controls.forEach((control, other) => {
          if (!control.listEl) return;
          // Open while it loads too, so the wait is visible where the rows will be.
          const open = other === name && control.list && (control.list.count() > 0 || control.list.isLoading());
          control.listEl.classList.toggle("hidden", !open);
          control.input.setAttribute("aria-expanded", open ? "true" : "false");
        });
      }

      function submit() {
        const values = allValues();
        const missing = fields.find((field) => field.required !== false && !values[field.name]);
        if (missing) {
          const control = controls.get(missing.name);
          control.el.classList.add("is-invalid");
          const target = control.input || control.el.querySelector("input");
          if (target) target.focus();
          return;
        }
        const files = [];
        fields.forEach((field) => {
          const control = controls.get(field.name);
          if (field.type === "file" && control.input.files && control.input.files[0]) files.push(control.input.files[0]);
          else if (field.type !== "choice" && field.type !== "file") {
            usedBefore.add(historyKeyFor(card, field.name), values[field.name]);
          }
        });
        finish({ prompt: composeTemplate(card.template, values), values, files });
      }

      fields.forEach((field) => {
        const el = shell.root.querySelector(`[data-picker-field="${field.name}"]`);
        const control = { el, input: null, list: null, listEl: null, touched: false, depends: [] };
        controls.set(field.name, control);
        el.addEventListener("input", () => el.classList.remove("is-invalid"));
        el.addEventListener("change", () => {
          el.classList.remove("is-invalid");
          changed(field.name);
        });
        if (field.type === "choice") return;
        control.input = el.querySelector("input, textarea");
        if (field.type === "file" || field.type === "textarea") return;

        control.listEl = el.querySelector("[data-picker-list]");
        control.depends = paramDependencies(field.params);
        control.list = createSuggestionList({
          listEl: control.listEl,
          noteEl: el.querySelector("[data-picker-note]"),
          inputEl: control.input,
          idPrefix: `${shell.id}-${field.name}`,
          source: field.source || "",
          params: () => resolveParams(field.params, allValues()),
          agentId: opts.agentId || "",
          historyKey: historyKeyFor(card, field.name),
          inline: true,
          onPick: (item) => {
            control.input.value = String(item.value);
            control.touched = true;
            el.classList.remove("is-invalid");
            control.list.clearActive();
            spread(item, field.name);
            changed(field.name);
            showList("");
            focusNext(field.name);
          },
          onRender: () => showList(document.activeElement === control.input ? field.name : openField),
        });
        control.input.addEventListener("focus", () => showList(field.name));
        control.input.addEventListener("input", () => {
          control.touched = true;
          control.list.setQuery(control.input.value);
          showList(field.name);
        });
        control.input.addEventListener("keydown", (event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            showList(field.name);
            control.list.move(1);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            control.list.move(-1);
          } else if (event.key === "Enter" && !event.isComposing) {
            event.preventDefault();
            const picked = control.list.activeItem();
            if (picked) {
              control.input.value = String(picked.value);
              control.touched = true;
              control.list.clearActive();
              spread(picked, field.name);
              changed(field.name);
              showList("");
              focusNext(field.name);
            } else submit();
          }
        });
        control.list.load();
      });

      function focusNext(name) {
        const index = fields.findIndex((field) => field.name === name);
        for (let next = index + 1; next < fields.length; next += 1) {
          const control = controls.get(fields[next].name);
          const empty = !valueOf(fields[next]);
          const target = control.input || control.el.querySelector("input");
          if (empty && target) {
            target.focus();
            return;
          }
        }
        shell.confirmEl.focus();
      }

      shell.setEscape(() => finish(null));
      shell.confirmEl.addEventListener("click", submit);
      shell.cancelEl.addEventListener("click", () => finish(null));

      window.requestAnimationFrame(() => {
        const first = fields[0] && controls.get(fields[0].name);
        const target = first && (first.input || first.el.querySelector("input"));
        if (target) target.focus();
      });
    });
  }

  const api = {
    showPicker,
    showCardForm,
    prefetchCard,
    cardHint,
    loadSources,
    fetchSuggestions,
    createSuggestionList,
    history: usedBefore,
    historyKeyFor,
    // Pure helpers, exported for tests.
    composeTemplate,
    resolveParams,
    paramDependencies,
    filterGroups,
    historyGroup,
    flatItems,
    suggestionUrl,
    createHistory,
    statusLabel,
    stateNote,
    groupLabel,
    relativeTime,
  };

  if (typeof window !== "undefined") {
    window.portalCardPicker = api;
    window.showPicker = showPicker;
    window.showCardForm = showCardForm;
  }
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
