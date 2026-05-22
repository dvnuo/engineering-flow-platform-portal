/**
 * Portal-native chat UI.
 * Runtime remains source-of-truth for chat/session/file APIs under /a/{agent_id}/api/...
 * Portal is responsible for browser-side state + HTMX partial rendering.
 */

function chatApp() {
  // Alpine.js is kept lightweight; main behavior stays in small explicit functions below.
  return { initialized: true };
}

// ===== DOM refs =====
const dom = {
  appRoot: document.getElementById("app-root"),
  mineList: document.getElementById("mine-list"),
  embedTitle: document.getElementById("embed-title"),
  selectedStatus: document.getElementById("selected-status"),
  centerPlaceholder: document.getElementById("center-placeholder"),
  agentChatApp: document.getElementById("agent-chat-app"),
  workspaceDetailView: document.getElementById("workspace-detail-view"),
  workspaceDetailContent: document.getElementById("workspace-detail-content"),
  messageList: document.getElementById("message-list"),
  messageScroll: document.getElementById("message-scroll"),
  chatInput: document.getElementById("chat-input"),
  chatSuggest: document.getElementById("chat-suggest"),
  chatAgentId: document.getElementById("chat-agent-id"),
  chatSessionId: document.getElementById("chat-session-id"),
  chatStatus: document.getElementById("chat-status"),
  sendChatBtn: document.getElementById("send-chat-btn"),
  abortChatRunBtn: document.getElementById("abort-chat-run-btn"),
  uploadInput: document.getElementById("upload-input"),
  detailToggle: document.getElementById("detail-toggle"),
  toolPanel: document.getElementById("tool-panel"),
  toolPanelTitle: document.getElementById("tool-panel-title"),
  toolPanelBody: document.getElementById("tool-panel-body"),
  toolBackdrop: document.getElementById("tool-backdrop"),
  closeToolPanel: document.getElementById("close-tool-panel"),
  pinToolPanel: document.getElementById("pin-tool-panel"),
  agentMeta: document.getElementById("agent-meta"),
  agentActions: document.getElementById("agent-actions"),
  logoutBtn: document.getElementById("logout-btn"),
  themeToggle: document.getElementById("theme-toggle"),
  railAssistantsBtn: document.getElementById("rail-assistants-btn"),
  usersMenuBtn: document.getElementById("users-menu-btn"),
  tasksMenuBtn: document.getElementById("tasks-menu-btn"),
  automationsMenuBtn: document.getElementById("automations-menu-btn"),
  bundlesMenuBtn: document.getElementById("bundles-menu-btn"),
  runtimeProfilesMenuBtn: document.getElementById("runtime-profiles-menu-btn"),
  portalShell: document.querySelector(".portal-shell"),
  portalSecondaryPane: document.getElementById("portal-secondary-pane"),
  secondaryPaneToggle: document.getElementById("secondary-pane-toggle"),
  secondaryPaneRestore: document.getElementById("secondary-pane-restore"),
  secondaryPaneEyebrow: document.getElementById("secondary-pane-eyebrow"),
  secondaryPaneTitle: document.getElementById("secondary-pane-title"),
  secondaryPaneActions: document.getElementById("secondary-pane-actions"),
  assistantsNavSection: document.getElementById("assistants-nav-section"),
  bundlesNavSection: document.getElementById("bundles-nav-section"),
  tasksNavSection: document.getElementById("tasks-nav-section"),
  runtimeProfilesNavSection: document.getElementById("runtime-profiles-nav-section"),
  automationsNavSection: document.getElementById("automations-nav-section"),
  bundleNavList: document.getElementById("bundle-nav-list"),
  taskNavList: document.getElementById("task-nav-list"),
  runtimeProfileNavList: document.getElementById("runtime-profile-nav-list"),
  automationRuleNavList: document.getElementById("automation-rule-nav-list"),
  refreshBundlesBtn: document.getElementById("refresh-bundles-btn"),
  addBundleBtn: document.getElementById("add-bundle-btn"),
  addTaskBtn: document.getElementById("add-task-btn"),
  addRuntimeProfileBtn: document.getElementById("add-runtime-profile-btn"),
  addAutomationBtn: document.getElementById("add-automation-btn"),
  headerNewChatBtn: document.getElementById("header-new-chat-btn"),
  composerAttachBtn: document.getElementById("composer-attach-btn"),
  chatModelWrap: document.getElementById("composer-model-wrap"),
  chatModelSelect: document.getElementById("composer-model-select"),
  homeTitle: document.getElementById("home-title"),
  homeSubtitle: document.getElementById("home-subtitle"),
  homeAgentSummary: document.getElementById("home-agent-summary"),
  homeStartChatBtn: document.getElementById("home-start-chat-btn"),
  homeOpenBundlesBtn: document.getElementById("home-open-bundles-btn"),
  homeOpenTasksBtn: document.getElementById("home-open-tasks-btn"),
  createBundleModal: document.getElementById("create-bundle-modal"),
  createBundleForm: document.getElementById("create-bundle-form"),
  createBundleMsg: document.getElementById("create-bundle-msg"),
  closeCreateBundleModal: document.getElementById("close-create-bundle-modal"),
  createRuntimeProfileModal: document.getElementById("create-runtime-profile-modal"),
  createRuntimeProfileForm: document.getElementById("create-runtime-profile-form"),
  createRuntimeProfileMsg: document.getElementById("create-runtime-profile-msg"),
  closeCreateRuntimeProfileModal: document.getElementById("close-create-runtime-profile-modal"),
  addAgentBtn: document.getElementById("add-agent-btn"),
  editForm: document.getElementById("edit-form"),
};


// ===== Paste to upload handler =====
if (dom.chatInput) {
  dom.chatInput.addEventListener('paste', async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const files = [];

    for (const item of items) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (!file) continue;
      files.push(file);
    }

    if (!files.length) return;
    e.preventDefault();

    if (!state.selectedAgentId) {
      showToast('Please select an assistant first');
      return;
    }

    await addPendingFilesAndUpload(files);
  });
}

const LAST_AGENT_STORAGE_KEY = "portal-last-agent-id";
const REQUIREMENT_BUNDLES_CACHE_KEY = "portal-requirement-bundles-cache-v1";
const UI_LAYOUT_PREFS_STORAGE_KEY = "portal-ui-layout-prefs-v1";
const ALLOWED_UTILITY_PANEL_KEYS = new Set([
  "details",
  "sessions",
  "thinking",
  "server-files",
  "skills",
  "usage",
  "users",
]);

const PORTAL_ROUTE_SECTIONS = new Set([
  "assistants",
  "bundles",
  "tasks",
  "runtime-profiles",
  "automations",
]);

function normalizeUtilityPanelKey(panelKey) {
  if (typeof panelKey !== "string") return null;
  const normalized = panelKey.trim();
  return ALLOWED_UTILITY_PANEL_KEYS.has(normalized) ? normalized : null;
}

function readUiLayoutPreferences() {
  const fallback = {
    version: 1,
    secondaryPaneCollapsed: false,
    toolPanelPinned: false,
    activeUtilityPanel: null,
    toolPanelWidth: null,
  };
  try {
    const raw = localStorage.getItem(UI_LAYOUT_PREFS_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return fallback;
    const normalized = {
      version: 1,
      secondaryPaneCollapsed: typeof parsed.secondaryPaneCollapsed === "boolean" ? parsed.secondaryPaneCollapsed : false,
      toolPanelPinned: typeof parsed.toolPanelPinned === "boolean" ? parsed.toolPanelPinned : false,
      activeUtilityPanel: normalizeUtilityPanelKey(parsed.activeUtilityPanel),
      toolPanelWidth: null,
    };
    if (typeof parsed.toolPanelWidth === "number" && Number.isFinite(parsed.toolPanelWidth)) {
      const rounded = Math.round(parsed.toolPanelWidth);
      if (rounded >= 300 && rounded <= 1200) {
        normalized.toolPanelWidth = rounded;
      }
    }
    return normalized;
  } catch {
    return fallback;
  }
}

function getInitialUiLayoutPreferences() {
  return readUiLayoutPreferences();
}

function persistUiLayoutPreferences({
  includeSecondaryPane = true,
  includeToolPanel = true,
  clearToolPanelPreference = false,
} = {}) {
  try {
    const existing = readUiLayoutPreferences();
    const payload = {
      version: 1,
      secondaryPaneCollapsed: includeSecondaryPane
        ? !!state.secondaryPaneCollapsed
        : !!existing.secondaryPaneCollapsed,
      toolPanelPinned: !!existing.toolPanelPinned,
      activeUtilityPanel: normalizeUtilityPanelKey(existing.activeUtilityPanel),
      toolPanelWidth: typeof existing.toolPanelWidth === "number" ? existing.toolPanelWidth : null,
    };

    if (includeToolPanel) {
      if (clearToolPanelPreference) {
        payload.toolPanelPinned = false;
        payload.activeUtilityPanel = null;
      } else if (state.toolPanelOpen && state.toolPanelPinned) {
        payload.toolPanelPinned = true;
        payload.activeUtilityPanel = normalizeUtilityPanelKey(state.activeUtilityPanel);
      }

      if ((state.toolPanelOpen && state.toolPanelPinned) || clearToolPanelPreference) {
        if (typeof getCurrentToolPanelWidth === "function") {
          const width = getCurrentToolPanelWidth();
          if (typeof width === "number" && Number.isFinite(width)) {
            const rounded = Math.round(width);
            if (rounded >= 300 && rounded <= 1200) {
              payload.toolPanelWidth = rounded;
            }
          }
        }
      }
    }

    localStorage.setItem(UI_LAYOUT_PREFS_STORAGE_KEY, JSON.stringify(payload));
  } catch {}
}

const initialUiLayoutPrefs = getInitialUiLayoutPreferences();

// Global mapping from blob URL to file ID
const blobUrlToFileId = {};

function getFileIdFromBlobUrl(blobUrl) {
  const fileId = blobUrlToFileId[blobUrl] || null;
  return fileId;
}

function setBlobUrlMapping(blobUrl, fileId) {
  blobUrlToFileId[blobUrl] = fileId;
}


function getLastSessionKey(agentId) {
  return `portal-last-session-${agentId}`;
}

function getLastSessionId(agentId) {
  if (!agentId) return null;
  return localStorage.getItem(getLastSessionKey(agentId));
}

function setLastSessionId(agentId, sessionId) {
  if (!agentId) return;
  if (sessionId) {
    localStorage.setItem(getLastSessionKey(agentId), sessionId);
  } else {
    localStorage.removeItem(getLastSessionKey(agentId));
  }
}

// ===== app state =====
const state = {
  selectedAgentId: null,
  selectedAgentName: null,
  mineAgents: [],
  agentStatus: new Map(),
  detailOpen: initialUiLayoutPrefs.activeUtilityPanel === "details",
  activeUtilityPanel: normalizeUtilityPanelKey(initialUiLayoutPrefs.activeUtilityPanel),
  cachedSkills: [],
  cachedSkillsByAgent: new Map(),
  selectedSuggestionIndex: -1,
  // UI-only state: portal stores current selected session id per agent.
  // Runtime remains source-of-truth for full session history/messages.
  agentSessionIds: new Map(),
  chatStatesByAgent: new Map(),
  pendingMessage: "",
  currentUserId: Number(dom.appRoot?.dataset.userId || 0),
  currentUserName: dom.appRoot?.dataset.nickname || dom.appRoot?.dataset.username || "You",
  currentUserRole: String(dom.appRoot?.dataset.role || "user"),
  eventWs: null,
  eventWsAgentId: null,
  eventWsSessionId: null,
  eventWsRequestId: null,
  eventWsReconnectTimer: null,
  eventWsReconnectAttempt: 0,
  isComposingInput: false,
  suggestRequestSeq: 0,
  suggestBlurHideTimer: null,
  requirementBundles: [],
  hasRequirementBundlesCache: false,
  selectedBundleKey: null,
  activeNavSection: "assistants",
  secondaryPaneCollapsed: !!initialUiLayoutPrefs.secondaryPaneCollapsed,
  toolPanelOpen: !!initialUiLayoutPrefs.toolPanelPinned,
  toolPanelPinned: !!initialUiLayoutPrefs.toolPanelPinned,
  pendingToolPanelRestoreKey: normalizeUtilityPanelKey(initialUiLayoutPrefs.activeUtilityPanel),
  myTasks: [],
  selectedTaskId: null,
  serverFilesRootPath: null,
  serverFilesCurrentPath: null,
  runtimeProfiles: [],
  selectedRuntimeProfileId: null,
  automations: [],
  selectedAutomationRuleId: null,
  agentDefaults: null,
};
let thinkingPanelRefreshRaf = null;
let hasRestoredPinnedToolPanel = false;
let isApplyingPortalRoute = false;
let portalRouteApplySeq = 0;

function safeDecodeRouteComponent(value) {
  try {
    return decodeURIComponent(String(value || ""));
  } catch (_error) {
    return null;
  }
}

function parsePortalHashRoute(hash = window.location.hash) {
  const raw = typeof hash === "string" ? hash : "";
  const hadHash = raw.length > 0 && raw !== "#";
  const fallback = {
    valid: false,
    section: "assistants",
    agentId: "",
    taskId: "",
    runtimeProfileId: "",
    automationRuleId: "",
    bundleRef: null,
    hadHash,
    raw,
  };
  if (!hadHash) return fallback;

  const withoutHash = raw.startsWith("#") ? raw.slice(1) : raw;
  const routeText = withoutHash.startsWith("/") ? withoutHash.slice(1) : withoutHash;
  if (!routeText) return fallback;

  const queryIndex = routeText.indexOf("?");
  const pathPart = queryIndex >= 0 ? routeText.slice(0, queryIndex) : routeText;
  const queryString = queryIndex >= 0 ? routeText.slice(queryIndex + 1) : "";
  const encodedParts = pathPart.split("/");
  const section = safeDecodeRouteComponent(encodedParts[0]);
  if (!section || !PORTAL_ROUTE_SECTIONS.has(section)) return fallback;

  const parsed = {
    ...fallback,
    valid: true,
    section,
  };

  if (section === "bundles") {
    if (encodedParts.slice(1).some((part) => part !== "")) return fallback;
    const params = new URLSearchParams(queryString);
    const repo = params.get("repo") || "";
    const path = params.get("path") || "";
    const branch = params.get("branch") || "";
    if (repo || path || branch) {
      parsed.bundleRef = { repo, path, branch };
    }
    return parsed;
  }

  if (queryString) return fallback;
  if (encodedParts.length > 2) return fallback;

  const decodedId = safeDecodeRouteComponent(encodedParts[1] || "");
  if (decodedId === null) return fallback;

  if (section === "assistants") {
    parsed.agentId = decodedId;
  } else if (section === "tasks") {
    parsed.taskId = decodedId;
  } else if (section === "runtime-profiles") {
    parsed.runtimeProfileId = decodedId;
  } else if (section === "automations") {
    parsed.automationRuleId = decodedId;
  }
  return parsed;
}

function portalHashForRoute(route = {}) {
  const section = PORTAL_ROUTE_SECTIONS.has(route?.section) ? route.section : "assistants";

  if (section === "assistants") {
    const agentId = route.agentId ? String(route.agentId) : "";
    return agentId ? `#/assistants/${encodeURIComponent(agentId)}` : "#/assistants";
  }

  if (section === "bundles") {
    const bundleRef = route.bundleRef || null;
    if (bundleRef && (bundleRef.repo || bundleRef.path || bundleRef.branch)) {
      const params = new URLSearchParams();
      params.set("repo", bundleRef.repo || "");
      params.set("path", bundleRef.path || "");
      params.set("branch", bundleRef.branch || "");
      return `#/bundles?${params.toString()}`;
    }
    return "#/bundles";
  }

  if (section === "tasks") {
    const taskId = route.taskId ? String(route.taskId) : "";
    return taskId ? `#/tasks/${encodeURIComponent(taskId)}` : "#/tasks";
  }

  if (section === "runtime-profiles") {
    const runtimeProfileId = route.runtimeProfileId ? String(route.runtimeProfileId) : "";
    return runtimeProfileId ? `#/runtime-profiles/${encodeURIComponent(runtimeProfileId)}` : "#/runtime-profiles";
  }

  if (section === "automations") {
    const automationRuleId = route.automationRuleId ? String(route.automationRuleId) : "";
    return automationRuleId ? `#/automations/${encodeURIComponent(automationRuleId)}` : "#/automations";
  }

  return "#/assistants";
}

function currentPortalRouteFromState() {
  const section = PORTAL_ROUTE_SECTIONS.has(state.activeNavSection) ? state.activeNavSection : "assistants";

  if (section === "assistants") {
    return { section, agentId: state.selectedAgentId || "" };
  }

  if (section === "bundles") {
    const selectedBundle = (state.requirementBundles || []).find((item) => bundleKey(item) === state.selectedBundleKey);
    if (selectedBundle?.bundle_ref) {
      return { section, bundleRef: selectedBundle.bundle_ref };
    }
    return { section };
  }

  if (section === "tasks") {
    return { section, taskId: state.selectedTaskId || "" };
  }

  if (section === "runtime-profiles") {
    return { section, runtimeProfileId: state.selectedRuntimeProfileId || "" };
  }

  if (section === "automations") {
    return { section, automationRuleId: state.selectedAutomationRuleId || "" };
  }

  return { section: "assistants", agentId: state.selectedAgentId || "" };
}

function commitPortalRoute(route = currentPortalRouteFromState(), { replace = false } = {}) {
  if (isApplyingPortalRoute) return;
  if (typeof window === "undefined" || !window.location || !window.history) return;
  const nextHash = portalHashForRoute(route);
  if (window.location.hash === nextHash) return;
  const nextUrl = `${window.location.pathname}${window.location.search}${nextHash}`;
  if (replace) {
    window.history.replaceState(null, "", nextUrl);
  } else {
    window.history.pushState(null, "", nextUrl);
  }
}

function replacePortalRouteFromState() {
  commitPortalRoute(currentPortalRouteFromState(), { replace: true });
}

function clearPortalSectionDetailSelection(section) {
  if (section === "bundles") {
    state.selectedBundleKey = null;
  } else if (section === "tasks") {
    state.selectedTaskId = null;
  } else if (section === "runtime-profiles") {
    state.selectedRuntimeProfileId = null;
  } else if (section === "automations") {
    state.selectedAutomationRuleId = null;
  }
}

function portalSectionRoute(section) {
  const normalized = PORTAL_ROUTE_SECTIONS.has(section) ? section : "assistants";
  if (normalized === "assistants") {
    return { section: "assistants", agentId: state.selectedAgentId || "" };
  }
  return { section: normalized };
}

async function openPortalSection(section, {
  toggleIfSame = true,
  replace = false,
} = {}) {
  if (!PORTAL_ROUTE_SECTIONS.has(section)) return;

  const hadDetailSelection = (
    (section === "bundles" && !!state.selectedBundleKey) ||
    (section === "tasks" && !!state.selectedTaskId) ||
    (section === "runtime-profiles" && !!state.selectedRuntimeProfileId) ||
    (section === "automations" && !!state.selectedAutomationRuleId)
  );

  // Section-only navigation means the user clicked the rail/menu to open the column page,
  // not a specific detail item.
  clearPortalSectionDetailSelection(section);

  if (!isApplyingPortalRoute) {
    commitPortalRoute(portalSectionRoute(section), { replace });
  }

  await setActiveNavSection(section, {
    toggleIfSame: toggleIfSame && !hadDetailSelection,
    updateRoute: false,
    preferSectionLanding: true,
  });
}

async function applyPortalRouteFromHash({ replaceInvalid = false } = {}) {
  const seq = ++portalRouteApplySeq;
  const parsed = parsePortalHashRoute(window.location.hash);

  let route = parsed;
  if (!route.valid || !route.hadHash) {
    route = {
      valid: true,
      section: "assistants",
      agentId: state.selectedAgentId || "",
      hadHash: false,
    };
  }

  isApplyingPortalRoute = true;
  try {
    await applyPortalRoute(route, { replaceInvalid });
  } finally {
    if (seq === portalRouteApplySeq) {
      isApplyingPortalRoute = false;
    }
  }

  if (!parsed.hadHash || (replaceInvalid && !parsed.valid)) {
    commitPortalRoute(currentPortalRouteFromState(), { replace: true });
  }
}

async function applyPortalRoute(route, { replaceInvalid = false } = {}) {
  if (!route || !PORTAL_ROUTE_SECTIONS.has(route.section)) {
    await setActiveNavSection("assistants", { toggleIfSame: false, updateRoute: false });
    return;
  }

  if (route.section === "assistants") {
    await setActiveNavSection("assistants", { toggleIfSame: false, updateRoute: false });

    if (route.agentId) {
      const exists = (state.mineAgents || []).some((agent) => agent.id === route.agentId);
      if (exists) {
        await selectAgentById(route.agentId, { updateRoute: false });
      } else {
        state.selectedAgentId = null;
        state.selectedAgentName = null;
        window.selectedAgentId = "";
        renderAgentList();
        await syncSelectedAgentState();
        showToast("Assistant from URL was not found or is not accessible.");
      }
    } else {
      renderAgentList();
      await syncSelectedAgentState();
    }
    return;
  }

  if (route.section === "bundles") {
    if (route.bundleRef && route.bundleRef.repo && route.bundleRef.path) {
      await setActiveNavSection("bundles", { toggleIfSame: false, updateRoute: false });
      loadRequirementBundlesFromCache();
      renderRequirementBundleList();
      await openRequirementBundleInMain(route.bundleRef, { updateRoute: false });
    } else {
      await setActiveNavSection("bundles", {
        toggleIfSame: false,
        updateRoute: false,
        preferSectionLanding: true,
      });
    }
    return;
  }

  if (route.section === "tasks") {
    if (route.taskId) {
      await setActiveNavSection("tasks", { toggleIfSame: false, updateRoute: false });
      await refreshMyTasks();
      await openTaskDetailInMain(route.taskId, { updateRoute: false });
    } else {
      await setActiveNavSection("tasks", {
        toggleIfSame: false,
        updateRoute: false,
        preferSectionLanding: true,
      });
    }
    return;
  }

  if (route.section === "runtime-profiles") {
    if (route.runtimeProfileId) {
      await setActiveNavSection("runtime-profiles", { toggleIfSame: false, updateRoute: false });
      await refreshRuntimeProfileList({ preserveSelection: true });
      await openRuntimeProfileInMain(route.runtimeProfileId, { ensureSection: false, updateRoute: false });
    } else {
      await setActiveNavSection("runtime-profiles", {
        toggleIfSame: false,
        updateRoute: false,
        preferSectionLanding: true,
      });
    }
    return;
  }

  if (route.section === "automations") {
    if (route.automationRuleId) {
      await setActiveNavSection("automations", { toggleIfSame: false, updateRoute: false });
      await loadAutomationRules();
      await openAutomationRulePanel(route.automationRuleId, { updateRoute: false });
    } else {
      await setActiveNavSection("automations", {
        toggleIfSame: false,
        updateRoute: false,
        preferSectionLanding: true,
      });
    }
  }
}

function createDefaultChatState() {
  return {
    sessionId: "",
    isSubmitting: false,
    pendingFiles: [],
    inflightThinking: null,
    lastThinkingSnapshot: null,
    pendingThinkingEvents: null,
    draftText: "",
    needsReload: false,
    unreadCount: 0,
    profileProvider: "",
    profileDefaultModel: "",
    modelOverride: "",
  };
}

function ensureChatState(agentId) {
  if (!agentId) return null;
  if (!state.chatStatesByAgent.has(agentId)) {
    const next = createDefaultChatState();
    next.sessionId = state.agentSessionIds.get(agentId) || "";
    state.chatStatesByAgent.set(agentId, next);
  }
  return state.chatStatesByAgent.get(agentId);
}

function getChatState(agentId = state.selectedAgentId) {
  return ensureChatState(agentId);
}

function currentSessionIdForAgent(agentId) {
  return ensureChatState(agentId)?.sessionId || "";
}

function isActiveRequestBlocking(chatState) {
  return Boolean(chatState?.isSubmitting && chatState?.currentRequest);
}

function hasActiveChatRequestForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  return Boolean(chatState?.isSubmitting);
}

function shouldShowAbortChatRunButton(agentId) {
  const chatState = ensureChatState(agentId);
  return Boolean(chatState?.isSubmitting);
}

function activeChatRequestMessage(agentId, actionLabel = "perform this action") {
  if (!hasActiveChatRequestForAgent(agentId)) return "";
  return "This assistant is responding. Please wait for the current response to finish.";
}

function guardNoActiveChatRequestForAgent(agentId, actionLabel = "perform this action") {
  if (!hasActiveChatRequestForAgent(agentId)) return true;
  const message = activeChatRequestMessage(agentId, actionLabel);
  if (message) showToast(message);
  if (message) setChatStatus(message, true);
  return false;
}

function syncSelectedAgentChatActionControls() {
  const agentId = state.selectedAgentId;
  const sessionsBtn = document.getElementById("btn-sessions");
  if (!agentId) {
    setButtonDisabled(dom.headerNewChatBtn, true, "Select an assistant first");
    setButtonDisabled(sessionsBtn, true, "Select an assistant first");
    setButtonDisabled(dom.homeStartChatBtn, true, "Select an assistant first");
    if (dom.sendChatBtn) dom.sendChatBtn.disabled = true;
    if (dom.abortChatRunBtn) {
      dom.abortChatRunBtn.classList.add("hidden");
      dom.abortChatRunBtn.disabled = true;
      dom.abortChatRunBtn.setAttribute("aria-hidden", "true");
    }
    return;
  }

  const chatState = ensureChatState(agentId);
  if (chatState?.clearingStaleActiveRequest === true) return;

  const busy = hasActiveChatRequestForAgent(agentId);
  if (dom.abortChatRunBtn) {
    const showAbort = shouldShowAbortChatRunButton(agentId);
    dom.abortChatRunBtn.classList.toggle("hidden", !showAbort);
    dom.abortChatRunBtn.disabled = !showAbort;
    dom.abortChatRunBtn.setAttribute("aria-hidden", showAbort ? "false" : "true");
  }
  const status = getSelectedAgentStatus();
  const needsStart = status !== "running";
  const disabled = busy || needsStart;
  const title = busy
    ? "This assistant is still working in the current session."
    : "Start the assistant from Assistant details first";

  setButtonDisabled(dom.headerNewChatBtn, disabled, disabled ? title : "");
  setButtonDisabled(sessionsBtn, disabled, disabled ? title : "");
  setButtonDisabled(dom.homeStartChatBtn, disabled, disabled ? title : "");
  if (dom.sendChatBtn) dom.sendChatBtn.disabled = busy || needsStart;
}


const md = window.markdownit({
  html: false,
  linkify: true,
  breaks: true,
  typographer: true,
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      const highlighted = hljs.highlight(str, { language: lang }).value;
      return `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>`;
    }
    return `<pre><code class="hljs">${md.utils.escapeHtml(str)}</code></pre>`;
  },
});

// Only allow http/https URLs to be converted to links
md.validateLink = function(text) {
  return /^https?:\/\//i.test(text);
};

// ===== generic helpers =====
function safe(value) {
  return String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function cssEscapeForSelector(value) {
  if (typeof window !== "undefined" && window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ===== File Preview Functions =====
function generateFileId() {
  return 'file_' + Math.random().toString(36).substr(2, 9);
}

function generateClientWebchatSessionId() {
  // Client-side fallback session id used before first send/upload so both routes share one stable id.
  const now = new Date();
  const pad2 = (value) => String(value).padStart(2, "0");
  const timestamp = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}_${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`;
  const randomPart = Math.random().toString(36).slice(2, 10).padEnd(8, "0").slice(0, 8);
  return `webchat_${timestamp}_${randomPart}`;
}

function ensureChatSessionId(agentId = state.selectedAgentId) {
  // Reuse the same per-agent session id once generated; never rotate it implicitly during an active chat.
  if (!agentId) return "";
  const existing = currentSessionIdForAgent(agentId);
  if (existing) {
    if (agentId === state.selectedAgentId) syncHiddenSessionInputFromState();
    return existing;
  }
  const sessionId = generateClientWebchatSessionId();
  updateAgentSession(agentId, sessionId);
  if (agentId === state.selectedAgentId) {
    const hiddenSessionInput = document.getElementById("chat-session-id");
    if (hiddenSessionInput) hiddenSessionInput.value = sessionId;
  }
  return sessionId;
}

const SUPPORTED_UPLOAD_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/gif",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
  "text/plain",
]);

const SUPPORTED_UPLOAD_EXTENSIONS = new Set([
  "jpg", "jpeg", "png", "webp", "gif",
  "pdf", "docx", "xlsx", "csv", "txt",
]);

const AUTO_PARSE_EXTENSIONS = new Set(["pdf", "docx", "xlsx", "csv", "txt"]);
const AUTO_PARSE_MIME_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
  "text/plain",
]);

function fileExtensionFromName(name) {
  const normalized = String(name || "").trim().toLowerCase();
  const index = normalized.lastIndexOf(".");
  return index >= 0 ? normalized.slice(index + 1) : "";
}

function isRuntimeSupportedUpload(file) {
  // Upload allowlist accepts either explicit MIME from browser or filename extension fallback.
  if (!file) return false;
  const mime = String(file.type || "").toLowerCase();
  const ext = fileExtensionFromName(file.name);
  if (SUPPORTED_UPLOAD_MIME_TYPES.has(mime)) return true;
  return SUPPORTED_UPLOAD_EXTENSIONS.has(ext);
}

function shouldAutoParseUploadedFile(pf, uploadedData) {
  // Auto-parse is intentionally document-only (never image/*), and allows MIME-or-extension matching.
  const fromData = String(uploadedData?.content_type || "").toLowerCase();
  const fromPf = String(pf?.file?.type || "").toLowerCase();
  const mime = fromData || fromPf;
  if (mime.startsWith("image/")) return false;
  const ext = fileExtensionFromName(uploadedData?.filename || pf?.name || pf?.file?.name || "");
  return AUTO_PARSE_MIME_TYPES.has(mime) || AUTO_PARSE_EXTENSIONS.has(ext);
}

async function parseUploadedPendingFile(pf, agentId, sessionId) {
  const response = await fetch(
    `/a/${agentId}/api/files/parse?session_id=${encodeURIComponent(sessionId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: pf.file_id }),
    },
  );
  if (!response.ok) {
    throw new Error(await handleErrorResponse(response));
  }
  const data = await response.json();
  if (data && typeof data === "object" && data.success === false) {
    throw new Error(data.error || "Parse failed");
  }
  return data;
}

function filterRuntimeSupportedUploads(files) {
  const accepted = [];
  for (const file of Array.from(files || [])) {
    if (isRuntimeSupportedUpload(file)) {
      accepted.push(file);
    } else {
      showToast("Unsupported file type. Supported: images, pdf, docx, xlsx, csv, txt.");
    }
  }
  return accepted;
}

function removePendingFile(id) {
  const chatState = getChatState();
  if (!chatState) return;
  const pf = chatState.pendingFiles.find(f => f.id === id);
  if (pf) {
    // Abort upload if in progress
    if (pf.xhr && pf.xhr.abort) {
      pf.xhr.abort();
      pf.cancelled = true;
    }
  }
  const idx = chatState.pendingFiles.findIndex(f => f.id === id);
  if (idx !== -1) {
    // Don't revoke immediately - wait for message to be sent
    // The blob URLs are needed for the optimistic UI message
    chatState.pendingFiles.splice(idx, 1);
    renderInputPreview();
  }
}

function clearPendingFiles() {
  const chatState = getChatState();
  if (!chatState) return;
  // Abort any in-progress uploads and revoke blob URLs to prevent memory leaks
  chatState.pendingFiles.forEach(pf => {
    if (pf.xhr && pf.xhr.abort) {
      pf.xhr.abort();
    }
    // Revoke blob URL to free memory
    if (pf.previewUrl && pf.previewUrl.startsWith('blob:')) {
      URL.revokeObjectURL(pf.previewUrl);
    }
  });
  chatState.pendingFiles = [];
  renderInputPreview();

  // Clear attachments field
  const attachmentsInput = document.getElementById('chat-attachments');
  if (attachmentsInput) {
    attachmentsInput.value = '';
  }
}

// Add files and upload immediately
async function addPendingFilesAndUpload(files) {
  const chatState = getChatState();
  if (!chatState) return;
  const agentId = state.selectedAgentId;
  const normalizedFiles = filterRuntimeSupportedUploads(files);
  if (!normalizedFiles.length) return;
  const sessionId = ensureChatSessionId(agentId);
  for (const file of normalizedFiles) {
    const isImage = file.type.startsWith('image/');
    const pf = {
      id: generateFileId(),
      file: file,
      previewUrl: null,
      name: file.name,
      isImage: isImage,
      status: 'uploading'
    };
    chatState.pendingFiles.push(pf);
    renderInputPreview();

    // Generate preview
    if (isImage) {
      // Use URL.createObjectURL for better memory efficiency
      pf.previewUrl = URL.createObjectURL(file);
      renderInputPreview();  // Re-render to show preview
    }

    // Upload immediately
    try {
      const data = await uploadPendingFile(pf, agentId);
      pf.uploadedData = data;
      pf.file_id = data.file_id || data.id;
      let uploadToastMessage = "File uploaded: " + file.name;

      // Store blob URL to file ID mapping
      if (pf.previewUrl && pf.file_id) {
        setBlobUrlMapping(pf.previewUrl, pf.file_id);
      }

      if (shouldAutoParseUploadedFile(pf, data)) {
        pf.status = "parsing";
        renderInputPreview();
        try {
          pf.parseData = await parseUploadedPendingFile(pf, agentId, sessionId);
          pf.status = "uploaded";
          pf.error = "";
          pf.parseError = "";
          if (!pf.uploadedData) {
            pf.uploadedData = data;
          }
          renderInputPreview();
        } catch (parseError) {
          pf.status = "uploaded";
          pf.parseError = parseError?.message || "Parse failed";
          pf.error = "";
          pf.parseData = null;
          uploadToastMessage = "File uploaded; text extraction unavailable: " + pf.parseError;
          renderInputPreview();
        }
      } else {
        pf.status = "uploaded";
        pf.error = "";
        pf.parseError = "";
        pf.parseData = null;
        renderInputPreview();
      }
      showToast(uploadToastMessage);

      // Note: Do NOT add to attachments here - will be built from pendingFiles when sending
      // Image will be shown in input-preview-area via renderInputPreview()

      // Connect WebSocket after upload completes
      ensureEventSocketForSelectedAgent();
    } catch (error) {
      pf.status = 'failed';
      pf.error = error?.message || "Upload failed";
      pf.parseError = "";
      pf.parseData = null;
      renderInputPreview();
      showToast('Upload failed: ' + error.message);
    }
  }
}

function renderInputPreview() {
  const chatState = getChatState();
  const container = document.getElementById('input-preview-area');
  if (!container) return;
  if (!chatState) {
    container.classList.add('hidden');
    container.innerHTML = '';
    return;
  }

  if (chatState.pendingFiles.length === 0) {
    container.classList.add('hidden');
    container.innerHTML = '';
    return;
  }

  container.classList.remove('hidden');
  container.innerHTML = chatState.pendingFiles.map(pf => {
    let content = '';
    let statusBadge = '';

    // Status badge
    if (pf.status === 'uploading') {
      statusBadge = '<span class="input-preview-badge is-uploading" aria-hidden="true">⏳</span>';
    } else if (pf.status === 'parsing') {
      const safeParseError = escapeHtmlAttr(pf.parseError || '');
      statusBadge = `<span class="input-preview-badge is-uploading" aria-hidden="true" title="${safeParseError || "Processing file"}">🧠</span>`;
    } else if (pf.status === 'uploaded') {
      statusBadge = '<span class="input-preview-badge is-success" aria-hidden="true">✓</span>';
    } else if (pf.status === 'failed') {
      statusBadge = '<span class="input-preview-badge is-error" aria-hidden="true">✗</span>';
    }

    if (pf.isImage && pf.previewUrl) {
      const safeAlt = ((pf.name || pf.file?.name || '')).replace(/[<>"'&]/g, '');
      content = `<img src="${pf.previewUrl}" alt="${safeAlt}" class="w-full h-full object-cover" />`;
    } else if (pf.isImage) {
      content = `<div class="file-icon"><span>...</span></div>`;
    } else {
      const safeName = (pf.name || '').replace(/[<>"'&]/g, '');
      content = `<div class="file-icon"><span>📄</span><span style="font-size:10px">${safeName}</span></div>`;
    }
    const safeId = (pf.id || '').replace(/[<>"'&]/g, '');
    const safePreviewUrl = escapeHtmlAttr(pf.previewUrl || '');
    const safePreviewName = escapeHtmlAttr(pf.name || '');
    return `<div class="input-preview-card" data-id="${safeId}" data-preview-url="${safePreviewUrl}" data-preview-name="${safePreviewName}" data-is-image="${pf.isImage ? 'true' : 'false'}">${statusBadge}${content}<button type="button" class="remove-btn" aria-label="Remove attachment" data-remove-id="${safeId}">×</button></div>`;
  }).join('');
}

async function uploadPendingFile(pf, agentId = state.selectedAgentId) {
  return new Promise((resolve, reject) => {
    // Check if already cancelled
    if (pf.cancelled) {
      reject(new Error('Upload cancelled'));
      return;
    }

    const formData = new FormData();
    formData.append('file', pf.file);

    const xhr = new XMLHttpRequest();
    pf.xhr = xhr;  // Store reference for cancellation

    xhr.addEventListener('load', () => {
      // Check if cancelled during upload
      if (pf.cancelled) {
        reject(new Error('Upload cancelled'));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          pf.uploadedData = data;
          // Store blob URL to file ID mapping
          const fileId = data.file_id || data.id;
          if (pf.previewUrl && fileId) {
            setBlobUrlMapping(pf.previewUrl, fileId);
          }
          resolve(data);
        } catch { reject(new Error('Invalid response')); }
      } else { reject(new Error('HTTP ' + xhr.status)); }
    });
    xhr.addEventListener('error', () => { reject(new Error('Network error')); });
    xhr.addEventListener('abort', () => { reject(new Error('Upload cancelled')); });
    const sessionId = ensureChatSessionId(agentId);
    const url = `/a/${agentId}/api/files/upload?session_id=${encodeURIComponent(sessionId)}`;
    xhr.open('POST', url);
    xhr.send(formData);
  });
}

window.removePendingFile = removePendingFile;

function normalizeSkillCommand(raw) {
  const skillName = String(raw || "").trim().replace(/^\/+/, "");
  return skillName ? `/${skillName}` : "";
}

function parseSkillSlashInput(text) {
  const raw = String(text || "").trim();
  const match = raw.match(/^\/([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+(.*))?$/);
  if (!match) return null;
  const rawName = match[1];
  const normalizedName = rawName.trim().toLowerCase().replaceAll("_", "-");
  return {
    rawName,
    name: normalizedName,
    arguments: match[2] || "",
    command: `/${normalizedName}`,
  };
}

function toSkillSuggestion(item) {
  const skill = (item && typeof item === "object") ? item : null;
  const name = typeof item === "string" ? item : (skill?.name || skill?.opencode_name || skill?.efp_name || "");
  const normalizedName = String(name || "").trim().toLowerCase().replaceAll("_", "-");
  const command = normalizeSkillCommand(normalizedName);
  const blockedReason = skill?.blocked_reason || "";
  const compatibilityWarnings = Array.isArray(skill?.compatibility_warnings) ? skill.compatibility_warnings.filter(Boolean).join(" · ") : "";
  const isCallable = skill?.callable !== false;
  const status = isCallable ? "" : "Not callable";
  const descBase = typeof item === "string" ? "Skill" : (skill?.description || "Skill");
  const desc = [descBase, status, blockedReason || compatibilityWarnings].filter(Boolean).join(" · ");
  const missingTools = Array.isArray(skill?.missing_tools) ? skill.missing_tools.join(", ") : "";
  const missingOpencodeTools = Array.isArray(skill?.missing_opencode_tools) ? skill.missing_opencode_tools.join(", ") : "";
  const titleText = [blockedReason, compatibilityWarnings].filter(Boolean).join(" · ");
  return {
    label: command,
    command,
    desc,
    title: titleText,
    callable: isCallable,
    blocked_reason: blockedReason,
    opencode_compatibility: skill?.opencode_compatibility || "",
    runtime_equivalence: skill?.runtime_equivalence ?? "",
    programmatic: skill?.programmatic,
    permission_state: skill?.permission_state || "",
    missing_tools: missingTools,
    missing_opencode_tools: missingOpencodeTools,
    opencode_name: skill?.opencode_name || "",
    efp_name: skill?.efp_name || "",
    name: skill?.name || normalizedName,
  };
}

function getCachedSkillsForAgent(agentId = state.selectedAgentId) {
  if (!agentId) return [];
  const cached = state.cachedSkillsByAgent.get(agentId);
  if (Array.isArray(cached)) return cached;
  return Array.isArray(state.cachedSkills) ? state.cachedSkills : [];
}

function findCachedSkillForSlash(invocation, agentId = state.selectedAgentId) {
  if (!invocation) return null;
  const target = invocation.name;
  return getCachedSkillsForAgent(agentId).find((skill) => {
    const names = [
      skill?.name,
      skill?.opencode_name,
      skill?.efp_name,
    ].filter(Boolean).map((x) => String(x).trim().toLowerCase().replaceAll("_", "-"));
    return names.includes(target);
  }) || null;
}

function canWriteAgent(agent) {
  if (!agent) return false;
  return state.currentUserRole === "admin" || Number(agent.owner_user_id) === state.currentUserId;
}

function getSelectedAgent() {
  return state.mineAgents.find((item) => item.id === state.selectedAgentId) || null;
}

function getSelectedAgentStatus() {
  const agent = (typeof getSelectedAgent === "function") ? getSelectedAgent() : {};
  if (!agent) return "idle";
  return (state.agentStatus.get(agent.id)?.status || agent.status || "stopped").toLowerCase();
}

function ensureRunningSelectedAssistant(actionLabel = "perform this action") {
  const agent = getSelectedAgent();
  if (!agent) {
    showToast("Please select an assistant first");
    return false;
  }
  const status = getSelectedAgentStatus();
  if (status !== "running") {
    showToast(`${agent.name} is ${status}. Start it from Assistant details first.`);
    return false;
  }
  return true;
}

function setButtonDisabled(button, disabled, disabledTitle = "") {
  if (!button) return;
  const hasDefaultTitle = Object.prototype.hasOwnProperty.call(button.dataset, "defaultTitle");
  if (!hasDefaultTitle) {
    button.dataset.defaultTitle = button.getAttribute("title") || "";
  }
  button.disabled = !!disabled;
  button.setAttribute("aria-disabled", disabled ? "true" : "false");
  if (disabled) {
    if (disabledTitle) button.setAttribute("title", disabledTitle);
  } else {
    const original = button.dataset.defaultTitle || "";
    if (original) button.setAttribute("title", original);
    else button.removeAttribute("title");
  }
}

function getFormSubmitButton(form) {
  if (!form) return null;
  return form.querySelector('button[type="submit"]');
}

function setModalFormBusyState(form, busy, { pendingText = "", closeButton = null } = {}) {
  if (!form) return;
  const submitButton = getFormSubmitButton(form);

  if (busy) {
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    if (submitButton) {
      if (!submitButton.dataset.originalText) {
        submitButton.dataset.originalText = submitButton.textContent || "";
      }
      if (pendingText) submitButton.textContent = pendingText;
      setButtonDisabled(submitButton, true);
    }
    if (closeButton) setButtonDisabled(closeButton, true);
    return;
  }

  delete form.dataset.submitting;
  form.removeAttribute("aria-busy");
  if (submitButton) {
    if (submitButton.dataset.originalText) {
      submitButton.textContent = submitButton.dataset.originalText;
    }
    setButtonDisabled(submitButton, false);
  }
  if (closeButton) setButtonDisabled(closeButton, false);
}

function beginSingleSubmit(form, options = {}) {
  if (!form) return false;
  if (form.dataset.submitting === "true") return false;
  setModalFormBusyState(form, true, options);
  return true;
}

function endSingleSubmit(form, options = {}) {
  setModalFormBusyState(form, false, options);
}

function updateChatInputPlaceholder() {
  if (!dom.chatInput) return;
  const maxPlaceholderAgentLength = 24;
  const assistantName = String(state.selectedAgentName || "").trim();
  if (!assistantName) {
    dom.chatInput.placeholder = "Ask anything...";
    return;
  }
  const displayName = assistantName.length > maxPlaceholderAgentLength
    ? `${assistantName.slice(0, maxPlaceholderAgentLength - 1)}…`
    : assistantName;
  dom.chatInput.placeholder = `Ask ${displayName}`;
}

function syncChatInputHeight() {
  if (!dom.chatInput) return;
  dom.chatInput.style.height = 'auto';
  dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 220) + 'px';
}

function resetChatInputHeight() {
  if (!dom.chatInput) return;
  dom.chatInput.style.height = 'auto';
}

function getCurrentUserDisplayName() {
  const name = String(state.currentUserName || "").trim();
  return name || "You";
}

function getSelectedAssistantDisplayName(fallback = "Assistant") {
  const name = String(state.selectedAgentName || "").trim();
  return name || fallback;
}

function getNonBlankAuthorName(value) {
  const name = String(value || "").trim();
  return name || "";
}

function getHistoryMessageDisplayName(message, isUser) {
  const persistedName = getNonBlankAuthorName(message?.author_name);
  if (persistedName) return persistedName;
  if (isUser) return getCurrentUserDisplayName();
  return getSelectedAssistantDisplayName();
}

function getHistoryUserVisibleContent(message) {
  if (!message || typeof message !== "object") return "";
  const candidates = [
    message.display_content,
    message.displayContent,
    message.metadata?.original_user_message,
    message.metadata?.originalUserMessage,
    message.content,
  ];
  for (const value of candidates) {
    if (typeof value === "string") return value;
  }
  return "";
}

function getCanonicalMessagesFromSessionPayload(payload = {}) {
  const canonical = Array.isArray(payload?.canonical_messages)
    ? payload.canonical_messages
    : Array.isArray(payload?.metadata?.canonical_messages)
      ? payload.metadata.canonical_messages
      : [];

  return canonical
    .filter((message) => message && typeof message === "object")
    .map((message) => {
      const info = message.info && typeof message.info === "object" ? message.info : {};
      const parts = Array.isArray(message.parts) ? message.parts.filter((part) => part && typeof part === "object") : [];
      const messageId = String(message.message_id || info.id || "");
      const role = String(message.role || info.role || "").toLowerCase();

      return {
        info,
        parts,
        message_id: messageId,
        role,
        source_of_truth: "opencode",
      };
    })
    .filter((message) => message.message_id || message.role || message.parts.length);
}

function canonicalPartText(part = {}) {
  if (!part || typeof part !== "object") return "";
  if (typeof part.text === "string") return part.text;
  if (typeof part.content === "string") return part.content;
  if (typeof part.delta === "string") return part.delta;
  if (typeof part.markdown === "string") return part.markdown;
  return "";
}

function canonicalMessageVisibleText(message = {}) {
  const parts = Array.isArray(message.parts) ? message.parts : [];
  return parts
    .filter((part) => {
      const type = String(part.type || "").toLowerCase();
      return type === "text" || type === "markdown" || (!type && canonicalPartText(part));
    })
    .map(canonicalPartText)
    .filter(Boolean)
    .join("");
}

function canonicalMessageToLegacyDisplayMessage(message = {}, index = 0) {
  const info = message.info || {};
  const role = String(message.role || info.role || "").toLowerCase() || "assistant";
  const id = String(message.message_id || info.id || `canonical-${index}`);
  const content = canonicalMessageVisibleText(message);

  return {
    id,
    role,
    content,
    display_content: content,
    request_id: String(info.requestID || info.request_id || ""),
    metadata: {
      source_of_truth: "opencode",
      opencode_message_id: id,
      opencode_role: role,
      canonical_parts: message.parts || [],
    },
  };
}

function canonicalMessagesToLegacyDisplayMessages(canonicalMessages = []) {
  return canonicalMessages
    .map((message, index) => canonicalMessageToLegacyDisplayMessage(message, index))
    .filter((message) => message.role !== "assistant" || String(message.display_content || message.content || "").trim());
}

function canonicalPartToThinkingItem(message = {}, part = {}, index = 0) {
  const partType = String(part.type || "").toLowerCase();
  const messageId = String(message.message_id || message.info?.id || part.messageID || part.messageId || part.message_id || "");
  const partId = String(part.id || `${messageId}:part:${index}`);

  if (partType === "reasoning") {
    return {
      kind: "reasoning",
      message_id: messageId,
      part_id: partId,
      text: canonicalPartText(part),
      status: part.finished === true || part.endTime ? "completed" : "running",
    };
  }

  if (partType === "tool") {
    const state = part.state && typeof part.state === "object" ? part.state : {};
    return {
      kind: "tool",
      message_id: messageId,
      part_id: partId,
      tool: String(part.tool || part.name || state.tool || ""),
      status: String(state.status || part.status || "running"),
      input: part.input || state.input || null,
      output: part.output || state.output || null,
      error: part.error || state.error || "",
    };
  }

  if (partType === "step-start") {
    return { kind: "step_start", message_id: messageId, part_id: partId };
  }

  if (partType === "step-finish") {
    return {
      kind: "step_finish",
      message_id: messageId,
      part_id: partId,
      reason: String(part.reason || part.finish_reason || ""),
      cost: part.cost || null,
      tokens: part.tokens || null,
    };
  }

  if (partType === "permission") {
    return {
      kind: "permission",
      message_id: messageId,
      part_id: partId,
      permission_id: String(part.permissionID || part.permission_id || part.id || ""),
      status: String(part.status || "pending"),
    };
  }

  return null;
}

function canonicalMessagesToThinkingItems(canonicalMessages = []) {
  const out = [];
  canonicalMessages.forEach((message) => {
    const parts = Array.isArray(message.parts) ? message.parts : [];
    parts.forEach((part, index) => {
      const item = canonicalPartToThinkingItem(message, part, index);
      if (item) out.push(item);
    });
  });
  return out;
}

function canonicalThinkingItemToRuntimeEvent(item = {}, sessionId = "") {
  const baseData = {
    ...item,
    message_id: item.message_id || "",
    part_id: item.part_id || "",
    session_id: sessionId || "",
    source_of_truth: "opencode",
  };
  const baseEvent = (type, data = baseData, summary = "") => ({
    type,
    raw_type: type,
    event_type: type,
    data,
    session_id: sessionId || "",
    request_id: "",
    event_id: `canonical:${item.message_id || ""}:${item.part_id || ""}:${type}`,
    summary: summary || data.message || data.text || data.tool || type,
    ts: Date.now() / 1000,
    source_of_truth: "opencode",
  });

  if (item.kind === "reasoning") {
    return baseEvent("opencode.reasoning", {
      ...baseData,
      message: item.text || "Reasoning update",
      status: item.status || "running",
    }, item.text || "Reasoning update");
  }
  if (item.kind === "tool") {
    return baseEvent("opencode.tool", {
      ...baseData,
      message: item.tool ? `${item.tool} ${item.status || "running"}` : "Tool update",
    }, item.tool || "Tool update");
  }
  if (item.kind === "step_start") {
    return baseEvent("opencode.step.started", {
      ...baseData,
      message: "Step started",
    }, "Step started");
  }
  if (item.kind === "step_finish") {
    return baseEvent("opencode.step.finished", {
      ...baseData,
      message: item.reason || "Step finished",
    }, item.reason || "Step finished");
  }
  if (item.kind === "permission") {
    return baseEvent("permission_request", {
      ...baseData,
      message: item.status ? `Permission ${item.status}` : "Permission requested",
    }, item.permission_id || "Permission requested");
  }
  return null;
}

function canonicalMessagesToThinkingEvents(canonicalMessages = [], sessionId = "") {
  return canonicalMessagesToThinkingItems(canonicalMessages)
    .map((item) => canonicalThinkingItemToRuntimeEvent(item, sessionId))
    .filter(Boolean);
}

function applyCanonicalMessagesToChatState(agentId, sessionId, chatState, canonicalMessages = [], metadata = {}) {
  if (!chatState || !Array.isArray(canonicalMessages) || !canonicalMessages.length) return;
  const canonicalEvents = canonicalMessagesToThinkingEvents(canonicalMessages, sessionId);
  if (!canonicalEvents.length) return;
  const target = chatState.inflightThinking && chatState.inflightThinking.completed !== true
    ? chatState.inflightThinking
    : null;
  const existing = target || chatState.lastThinkingSnapshot || {};
  const requestId = String(metadata.request_id || metadata.latest_request_id || existing.requestId || existing.id || "");
  const merged = {
    ...existing,
    id: existing.id || requestId || `canonical-${sessionId || Date.now()}`,
    requestId,
    sessionId: sessionId || existing.sessionId || "",
    events: mergeThinkingEvents(existing.events || [], canonicalEvents),
    canonicalThinkingItems: canonicalMessagesToThinkingItems(canonicalMessages),
    contextSource: "opencode_canonical",
    completed: target ? false : (existing.completed ?? true),
    status: target ? (existing.status || "connected") : (existing.status || "snapshot"),
    lastEventAt: Date.now(),
  };
  if (target) Object.assign(target, merged);
  else chatState.lastThinkingSnapshot = merged;
  if (agentId === state.selectedAgentId && isThinkingPanelActiveForAgent(agentId)) {
    scheduleThinkingPanelRefresh(agentId);
  }
}

function buildUserMessageArticle(text, attachments = [], options = {}) {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const clientRequestAttr = options.clientRequestId ? ` data-client-request-id="${escapeHtmlAttr(options.clientRequestId)}"` : "";
  let attachmentHtml = "";
  if (attachments.length > 0) {
    attachmentHtml = `<div class="message-attachments">${attachments.map(a => {
      const safeName = (a.name || '').replace(/[<>"'&]/g, '');
      const safeUrl = escapeHtmlAttr(a.previewUrl || a.url || '');
      const safeNameAttr = escapeHtmlAttr(safeName);
      if (a.type === 'image') {
        return `<img src="${safeUrl}" class="message-attachment-thumb" alt="${safeNameAttr}" data-preview-url="${safeUrl}" data-preview-name="${safeNameAttr}" data-is-image="true" />`;
      }
      return `<div class="message-attachment-file" data-preview-url="${safeUrl}" data-preview-name="${safeNameAttr}" data-is-image="false">📄 ${safeName}</div>`;
    }).join('')}</div>`;
  }

  return `<div class="message-row message-row-user"><div class="message-meta message-meta-user"><span class="message-author">${escapeHtml(getCurrentUserDisplayName())}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-user user-message" data-local-user="1" data-optimistic-user="1"${clientRequestAttr}><div class="message-body whitespace-pre-wrap text-sm">${safe(text)}</div>${attachmentHtml}</article></div>`;
}

function getAssistantDisplayGroupKey(message, lastUserMessageId, index) {
  const metadata = message?.metadata && typeof message.metadata === "object" ? message.metadata : {};
  return (
    message?.request_id ||
    message?.client_request_id ||
    metadata.request_id ||
    metadata.client_request_id ||
    metadata.turn_id ||
    metadata.run_id ||
    (lastUserMessageId ? `after-user:${lastUserMessageId}` : `assistant-run:${index}`)
  );
}

function groupSessionMessagesForDisplay(messages = []) {
  const entries = [];
  let currentAssistantGroup = null;
  let lastUserMessageId = "";

  messages.forEach((message, index) => {
    if (!message || typeof message !== "object") return;
    if (message.role === "user") {
      currentAssistantGroup = null;
      lastUserMessageId = message.id || lastUserMessageId || "";
      entries.push({ type: "message", message });
      return;
    }
    if (message.role !== "assistant") return;
    const groupKey = getAssistantDisplayGroupKey(message, lastUserMessageId, index);
    if (!currentAssistantGroup || currentAssistantGroup.key !== groupKey) {
      currentAssistantGroup = { type: "assistant_group", key: groupKey, userMessageId: lastUserMessageId || "", messages: [] };
      entries.push(currentAssistantGroup);
    }
    currentAssistantGroup.messages.push(message);
  });

  return entries;
}

function getAssistantGroupMessageIds(group) {
  return (group?.messages || []).map((m) => m?.id || m?.message_id || m?.metadata?.opencode_message_id || "").filter(Boolean);
}

function getAssistantGroupMarkdown(group) {
  return (group?.messages || []).map((m) => String(m?.content || "")).filter((text) => text.trim().length > 0).join("\n\n");
}

function getAssistantGroupDisplayBlocks(group) {
  return (group?.messages || []).flatMap((m) => Array.isArray(m?.display_blocks) ? m.display_blocks : []);
}

function buildAssistantGroupMessageArticle(group, authorName = "Assistant") {
  const messageIds = getAssistantGroupMessageIds(group);
  const primaryMessageId = messageIds[messageIds.length - 1] || "";
  const markdown = getAssistantGroupMarkdown(group);
  const displayBlocks = getAssistantGroupDisplayBlocks(group);
  return buildAssistantMessageArticle(markdown, displayBlocks, authorName, primaryMessageId, {
    messageIds,
    primaryMessageId,
    userMessageId: group?.userMessageId || "",
    assistantGroupKey: group?.key || "",
    copyText: markdown,
  });
}

function buildPendingAssistantArticle(clientRequestId = "", pendingText = "Thinking") {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const pendingAgentName = getSelectedAssistantDisplayName();
  const clientRequestAttr = clientRequestId ? ` data-client-request-id="${escapeHtmlAttr(clientRequestId)}"` : "";
  return `<div class="message-row message-row-assistant" data-temporary-assistant="1"${clientRequestAttr}><div class="message-meta"><span class="message-author">${escapeHtml(pendingAgentName)}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-assistant assistant-message is-pending pending-assistant" data-pending-assistant="1"${clientRequestAttr}><div class="assistant-waiting-indicator">${escapeHtml(pendingText)}<span class="assistant-waiting-dots"></span></div><div class="message-markdown md-render max-w-none text-sm" data-md="" data-display-blocks="[]"></div></article></div>`;
}

function buildAssistantMessageArticle(content, displayBlocks = [], authorName = "Assistant", messageId = "", options = {}) {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const encodedMd = escapeHtmlAttr(content || "");
  const encodedBlocks = escapeHtmlAttr(JSON.stringify(displayBlocks || []));
  const messageIds = Array.isArray(options.messageIds) ? options.messageIds.filter(Boolean) : (messageId ? [messageId] : []);
  const primaryMessageId = options.primaryMessageId || messageId || messageIds[messageIds.length - 1] || "";
  const messageIdAttr = primaryMessageId ? ` data-message-id="${escapeHtmlAttr(primaryMessageId)}"` : "";
  const primaryMessageAttr = primaryMessageId ? ` data-primary-message-id="${escapeHtmlAttr(primaryMessageId)}"` : "";
  const messageIdsAttr = ` data-message-ids="${escapeHtmlAttr(JSON.stringify(messageIds))}"`;
  const userMessageIdAttr = options.userMessageId ? ` data-user-message-id="${escapeHtmlAttr(options.userMessageId)}"` : "";
  const groupKeyAttr = options.assistantGroupKey ? ` data-assistant-group-key="${escapeHtmlAttr(options.assistantGroupKey)}"` : "";
  const requestIdAttr = options.requestId ? ` data-request-id="${escapeHtmlAttr(options.requestId)}"` : "";
  const clientRequestIdAttr = options.clientRequestId ? ` data-client-request-id="${escapeHtmlAttr(options.clientRequestId)}"` : "";
  const copyTextAttr = typeof options.copyText === "string" ? ` data-copy-text="${escapeHtmlAttr(options.copyText)}"` : "";
  const streamingClass = options.isStreaming ? " is-streaming" : "";
  const hasVisibleContent = String(content || "").trim() || (displayBlocks || []).length ? ` data-has-visible-content="1"` : "";
  return `<div class="message-row message-row-assistant"${hasVisibleContent}><div class="message-meta"><span class="message-author">${escapeHtml(authorName)}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-assistant assistant-message${streamingClass}"${messageIdAttr}${primaryMessageAttr}${messageIdsAttr}${userMessageIdAttr}${groupKeyAttr}${requestIdAttr}${clientRequestIdAttr}${copyTextAttr}${hasVisibleContent}><div class="message-markdown md-render max-w-none text-sm" data-md="${encodedMd}" data-display-blocks="${encodedBlocks}"></div></article></div>`;
}

function parseAssistantDisplayBlocksFromDataset(article) {
  const markdownEl = article?.querySelector?.(".message-markdown");
  const raw = markdownEl?.dataset?.displayBlocks || article?.dataset?.displayBlocks || "[]";
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function assistantArticleHasVisibleContent(article) {
  if (!article) return false;
  if (article.dataset.hasVisibleContent === "1") return true;
  if (article.dataset.messageId || article.dataset.primaryMessageId) return true;
  const markdownEl = article.querySelector(".message-markdown");
  if (String(markdownEl?.dataset?.md || "").trim()) return true;
  if (String(markdownEl?.textContent || "").trim()) return true;
  const blocks = parseAssistantDisplayBlocksFromDataset(article);
  return blocks.some((block) => (typeof hasRenderableDisplayBlock === "function")
    ? hasRenderableDisplayBlock(block)
    : !!(block && typeof block === "object" && String(block.text || block.content || block.value || "").trim()));
}

function assistantRowMatchesRequest(row, requestId) {
  if (!row || !requestId) return false;
  const normalized = String(requestId || "");
  const articles = Array.from(row.querySelectorAll("article.assistant-message, article[data-pending-assistant='1']"));
  if (row.dataset.clientRequestId === normalized || row.dataset.requestId === normalized) return true;
  return articles.some((article) => (
    article.dataset.clientRequestId === normalized
    || article.dataset.requestId === normalized
  ));
}

function removeTemporaryAssistantRows(options = {}) {
  if (!dom.messageList) return;
  const requestId = String(options.requestId || options.clientRequestId || "");
  const forceAll = options.forceAll === true;
  const onlyEmpty = options.onlyEmpty !== false;
  const tempRows = new Set();
  dom.messageList
    .querySelectorAll('article[data-pending-assistant="1"]')
    .forEach((article) => {
      const row = article.closest('.message-row');
      if (row) tempRows.add(row);
    });
  dom.messageList
    .querySelectorAll('.message-row[data-thinking-fallback="1"], .message-row[data-temporary-assistant="1"]')
    .forEach((row) => tempRows.add(row));
  tempRows.forEach((row) => {
    if (forceAll) {
      row.remove();
      return;
    }
    if (requestId && !assistantRowMatchesRequest(row, requestId)) return;
    if (onlyEmpty) {
      const article = row.querySelector('article[data-pending-assistant="1"], article.assistant-message');
      if (assistantArticleHasVisibleContent(article)) return;
    }
    row.remove();
  });
}

function removeLatestOptimisticUserRow(options = {}) {
  const latest = getLatestOptimisticUserArticle();
  if (!latest) return false;
  if (options.onlyLocal && latest.dataset.localUser !== "1") return false;
  const requestId = String(options.requestCtx?.clientRequestId || options.requestCtx?.requestId || "");
  if (requestId && latest.dataset.clientRequestId && latest.dataset.clientRequestId !== requestId) return false;
  const persisted =
    latest.dataset.persisted === "1"
    || latest.dataset.messageId
    || latest.dataset.opencodeMessageId;
  if (persisted) return false;
  const row = latest.closest?.(".message-row");
  (row || latest).remove();
  return true;
}

function removeOptimisticUserRowForRequest(requestCtx = {}) {
  const requestId = String(requestCtx.clientRequestId || requestCtx.requestId || "");
  if (!dom.messageList || !requestId) return false;

  const exact = dom.messageList.querySelector(
    `article.user-message[data-client-request-id="${cssEscapeForSelector(requestId)}"]`
  );
  const exactPersisted =
    exact?.dataset.persisted === "1"
    || exact?.dataset.messageId
    || exact?.dataset.opencodeMessageId;
  if (exact && !exactPersisted) {
    const row = exact.closest?.(".message-row");
    (row || exact).remove();
    return true;
  }

  const candidates = Array.from(dom.messageList.querySelectorAll("article.user-message[data-local-user='1']"));
  const last = candidates[candidates.length - 1];
  if (!last) return false;

  const persisted =
    last.dataset.persisted === "1"
    || last.dataset.messageId
    || last.dataset.opencodeMessageId;

  if (persisted) return false;

  const row = last.closest?.(".message-row");
  (row || last).remove();
  return true;
}

function getLatestOptimisticUserArticle() {
  const optimistic = Array.from(
    dom.messageList?.querySelectorAll('article[data-local-user="1"][data-optimistic-user="1"]') || []
  );
  return optimistic[optimistic.length - 1] || null;
}

function disconnectEventSocket({ clearReconnect = true } = {}) {
  if (clearReconnect && state.eventWsReconnectTimer) {
    clearTimeout(state.eventWsReconnectTimer);
    state.eventWsReconnectTimer = null;
  }
  if (state.eventWs) {
    try { state.eventWs.close(); } catch {}
  }
  state.eventWs = null;
  state.eventWsAgentId = null;
  state.eventWsSessionId = null;
  state.eventWsRequestId = null;
}

function normalizeRuntimeEventTypeAlias(type) {
  const normalized = String(type || "").trim();
  return normalized;
}

function isTrackableThinkingEvent(type) {
  const localNormalizeRuntimeEventTypeAlias = (value) => {
    if (typeof normalizeRuntimeEventTypeAlias === "function") {
      return normalizeRuntimeEventTypeAlias(value);
    }
    return String(value || "").trim();
  };
  const normalizedType = localNormalizeRuntimeEventTypeAlias(type);
  return [
    "stream.started",
    "chat.started", "heartbeat", "status",
    "execution.started", "execution.completed", "execution.failed",
    "execution.incomplete", "execution.blocked",
    "opencode.reasoning", "opencode.tool", "opencode.step.started", "opencode.step.finished",
    "chat.completed", "chat.incomplete", "chat.blocked", "chat.empty_final", "chat.failed", "chat.error", "final",
    "edit.failed",
    "iteration_start", "llm_thinking", "tool_call", "tool_result",
    "skill_matched", "complete",
    "context_snapshot", "context_compaction_planned", "context_compaction_applied",
    // Skill mode events
    "skill_mode_start", "skill_step", "skill_session_start",
    "skill_compaction", "skill_complete",
    // Active skill contract events
    "skill_runtime_applied", "skill_contract_active",
    "skill_tool_denied", "skill_contract_cleared",
    "message.started", "message.delta", "message.completed", "message.failed",
    "tool.started", "tool.completed", "tool.failed",
    "tool.error",
    "permission.requested", "permission.resolved", "permission_request", "permission_resolved",
    "permission.denied", "permission.allowed",
    "provider.retry", "provider.status", "provider.rate_limit", "model.retry",
    "event_bridge.connected", "event_bridge.disconnected", "event_bridge.reconnected", "opencode.raw",
    "opencode.status.validated",
    "assistant.message.started", "assistant.message.updated", "assistant.message.completed",
    "portal.waiting_for_runtime_events", "portal.stream_disconnected",
    "skill.loaded", "task.started", "task.completed", "usage.updated"
  ].includes(normalizedType);
}

// RUNTIME_EVENT_HELPER_START: normalizeRuntimeEvent
function normalizeRuntimeEvent(payload) {
  if (!payload || typeof payload !== "object") return null;

  // Runtime may wrap the event or send the event at top-level.
  const candidate = payload.event || payload.payload || payload;
  const localNormalizeRuntimeEventTypeAlias = (value) => {
    if (typeof normalizeRuntimeEventTypeAlias === "function") {
      return normalizeRuntimeEventTypeAlias(value);
    }
    return String(value || "").trim();
  };
  const wrapperTypes = new Set(["runtime_event", "event", "progress"]);
  const outerType = String(candidate?.event_type || candidate?.type || "").toLowerCase();
  const baseData = (candidate?.data && typeof candidate.data === "object") ? candidate.data : {};
  const embeddedType = String(baseData.event_type || baseData.type || baseData.event || "").toLowerCase();
  const rawTypeValue = (wrapperTypes.has(outerType) && embeddedType)
    ? embeddedType
    : (candidate?.event_type || candidate?.type || "");
  const rawType = (typeof normalizeRuntimeEventTypeAlias === "function")
    ? normalizeRuntimeEventTypeAlias(rawTypeValue)
    : localNormalizeRuntimeEventTypeAlias(rawTypeValue);
  if (!rawType) return null;

  const detailPayload = (candidate?.detail_payload && typeof candidate.detail_payload === "object")
    ? candidate.detail_payload
    : {};

  const mergedData = {
    ...baseData,
    ...detailPayload,
  };

  if (candidate?.summary && !mergedData.message) mergedData.message = candidate.summary;
  if (candidate?.state && !mergedData.state) mergedData.state = candidate.state;
  if (candidate?.request_id && !mergedData.request_id) mergedData.request_id = candidate.request_id;
  if (candidate?.session_id && !mergedData.session_id) mergedData.session_id = candidate.session_id;
  if (candidate?.agent_id && !mergedData.agent_id) mergedData.agent_id = candidate.agent_id;
  if (outerType && !mergedData.outer_event_type) mergedData.outer_event_type = outerType;
  if (rawTypeValue && rawTypeValue !== rawType && !mergedData.raw_event_type) mergedData.raw_event_type = rawTypeValue;
  const metadata = (candidate?.metadata && typeof candidate.metadata === "object")
    ? candidate.metadata
    : ((mergedData.metadata && typeof mergedData.metadata === "object") ? mergedData.metadata : {});
  const replayed = Boolean(candidate?.replayed || mergedData.replayed || metadata.replayed);

  let ts = candidate?.ts;
  if (ts == null && candidate?.created_at) {
    const parsed = Date.parse(candidate.created_at);
    if (!Number.isNaN(parsed)) ts = parsed / 1000;
  }
  if (ts == null) ts = Date.now() / 1000;

  const stateValue = String(candidate?.state || mergedData.state || "").toLowerCase();
  const failedByState = ["failed", "failure", "error"].includes(stateValue);
  const failedByType = rawType === "execution.failed";
  const failedByResult = rawType === "tool_result" && mergedData.success === false;
  const completionByType = rawType === "complete" || rawType === "execution.completed";
  const completionByState = isCompletionRuntimeState(stateValue);

  let lifecycleType = "";
  let normalizedType = rawType;
  if (rawType === "provider.retry" || (rawType === "session.status" && String(candidate?.status?.type || "").toLowerCase() === "retry")) {
    normalizedType = "provider.retry";
    mergedData.attempt = mergedData.attempt ?? candidate?.status?.attempt;
    mergedData.message = mergedData.message || candidate?.status?.message || "Provider API retrying";
  }
  if (failedByState || failedByType || failedByResult) {
    lifecycleType = "execution.failed";
    if (failedByType) normalizedType = "execution.failed";
  } else if (completionByType || completionByState) {
    lifecycleType = "execution.completed";
    if (rawType === "complete") normalizedType = "execution.completed";
  } else if (rawType === "execution.started" || stateValue === "started") {
    lifecycleType = "execution.started";
    normalizedType = "execution.started";
  }

  return {
    type: normalizedType,
    raw_type: rawTypeValue || rawType,
    lifecycle_type: lifecycleType,
    data: mergedData,
    outer_event_type: outerType,
    session_id: candidate?.session_id || mergedData.session_id || "",
    request_id: candidate?.request_id || mergedData.request_id || "",
    agent_id: candidate?.agent_id || mergedData.agent_id || "",
    ts,
    state: candidate?.state || mergedData.state || "",
    event_id: candidate?.runtime_event_id || candidate?.event_id || candidate?.id || mergedData.runtime_event_id || mergedData.event_id || mergedData.id || "",
    runtime_event_id: candidate?.runtime_event_id || mergedData.runtime_event_id || "",
    created_at: candidate?.created_at || mergedData.created_at || "",
    summary: candidate?.summary || mergedData.summary || mergedData.message || "",
    replayed,
  };
}
// RUNTIME_EVENT_HELPER_END: normalizeRuntimeEvent

// RUNTIME_EVENT_HELPER_START: completionRuntimeState
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
function isCompletionRuntimeState(state) {
  return COMPLETION_RUNTIME_STATES.has(String(state || "").toLowerCase());
}
// RUNTIME_EVENT_HELPER_END: completionRuntimeState

function getThinkingEventDisplay(event) {
  const type = normalizeRuntimeEventTypeAlias(event?.type || "event");
  const data = event?.data || {};
  const contextBudget = (data.budget && typeof data.budget === "object")
    ? data.budget
    : ((data.context_state?.budget && typeof data.context_state.budget === "object")
      ? data.context_state.budget
      : {});
  const contextPct = contextBudget.prepared_usage_percent ?? contextBudget.usage_percent;
  const contextStage = data.stage || "";
  const formatContextDetail = (fallback) => {
    const pieces = [];
    if (contextPct != null) pieces.push(`${contextPct}% used`);
    if (contextStage) pieces.push(contextStage);
    if (contextBudget.tokens_until_soft_threshold != null) {
      pieces.push(`${contextBudget.tokens_until_soft_threshold} tokens until soft threshold`);
    }
    if (contextBudget.next_compaction_action) {
      pieces.push(`next: ${contextBudget.next_compaction_action}`);
    }
    if (data.next_pruning_policy || contextBudget.next_pruning_policy) {
      pieces.push(data.next_pruning_policy || contextBudget.next_pruning_policy);
    }
    return pieces.join(" · ") || fallback;
  };
  const byType = {
    "execution.started": { icon: "play-circle", title: "Execution Started", detail: data.message || "Execution started" },
    "execution.completed": { icon: "flag", title: "Execution Completed", detail: data.message || "Execution complete", response: data.response, total_iterations: data.total_iterations },
    "execution.failed": { icon: "x-circle", title: "Execution Failed", detail: data.error || data.message || "Execution failed" },
    iteration_start: { icon: "rotate-cw", title: "Iteration Start", detail: `Iteration ${data.iteration || 1}${data.total ? `/${data.total}` : ""}` },
    llm_thinking: { icon: "brain", title: "LLM Thinking", detail: data.message || data.thinking || "Model is reasoning" },
    tool_call: { icon: "wrench", title: "Tool Call", detail: data.tool ? `Calling ${data.tool}` : "Calling tool", args: data.args },
    tool_result: { icon: data.success === false ? "x-circle" : "check-circle-2", title: "Tool Result", detail: data.success === false ? (data.error || "Tool failed") : (data.tool ? `${data.tool} completed` : "Tool completed"), result: data.result, output: data.output },
    "opencode.reasoning": { icon: "brain", title: "OpenCode Reasoning", detail: data.text || data.message || "Reasoning update", kind: data.status === "completed" ? "success" : "running" },
    "opencode.tool": { icon: data.error ? "x-circle" : "wrench", title: "OpenCode Tool", detail: data.tool ? `${data.tool} ${data.status || "running"}` : (data.message || "Tool update"), kind: data.error ? "error" : (data.status === "completed" ? "success" : "running"), args: data.input, output: data.output },
    "opencode.step.started": { icon: "list-start", title: "OpenCode Step Started", detail: data.message || "Step started" },
    "opencode.step.finished": { icon: "list-checks", title: "OpenCode Step Finished", detail: data.reason || data.message || "Step finished", kind: "success" },
    skill_matched: { icon: "zap", title: "Skill Matched", detail: normalizeSkillCommand(data.skill) || "Skill matched", skill: data.skill },
    complete: { icon: "flag", title: "Complete", detail: "Execution complete", response: data.response, total_iterations: data.total_iterations },
    context_snapshot: {
      icon: "gauge",
      title: "Context Snapshot",
      detail: formatContextDetail("Context updated"),
    },
    context_compaction_planned: {
      icon: "scissors",
      title: "Compaction Planned",
      detail: formatContextDetail("Compaction planned"),
    },
    context_compaction_applied: {
      icon: "archive",
      title: "Context Compaction Applied",
      detail: formatContextDetail("Context compaction applied"),
    },
    // Skill mode events
    skill_mode_start: { icon: "play-circle", title: "Skill Mode", detail: `Starting: ${data.skill || "Skill"}` },
    skill_step: { icon: "list-checks", title: `Step: ${data.step || "Step"}`, detail: data.detail || "", status: data.status },
    skill_session_start: { icon: "clipboard-list", title: "Skill Session", detail: `Goal: ${data.goal || ""}` },
    skill_compaction: { icon: data.status === "completed" ? "archive" : "scissors", title: "Compaction", detail: data.status === "completed" ? `Steps: ${data.remaining_steps}` : `Tokens: ${data.current_tokens}` },
    skill_complete: { icon: "check-square", title: data.reason === "finish" ? "Skill Finished" : "Skill Awaiting Input", detail: data.result || data.question || "" },
    skill_runtime_applied: {
      icon: "layers",
      title: "Skill Runtime Applied",
      detail: data.skill ? `Using ${data.skill}` : "Skill runtime applied",
      skill: data.skill
    },
    skill_contract_active: {
      icon: "pin",
      title: "Active Skill",
      detail: data.skill ? `${data.skill}${data.reason ? ` · ${data.reason}` : ""}` : "Active skill",
      skill: data.skill
    },
    skill_tool_denied: {
      icon: "shield-alert",
      title: "Skill Tool Denied",
      detail: data.tool ? `${data.tool} denied by ${data.skill || "active skill"}` : "Tool denied by active skill",
    },
    "tool.started": { icon: "wrench", title: "Tool Started", detail: data.message || "Tool started" },
    "tool.completed": { icon: "check-circle-2", title: "Tool Completed", detail: data.message || "Tool completed" },
    "tool.failed": { icon: "x-circle", title: "Tool Failed", detail: data.error || data.message || "Tool failed" },
    "chat.started": { icon: "play-circle", title: "Chat Started", detail: data.message || "Chat request started" },
    heartbeat: { icon: "activity", title: "Heartbeat", detail: data.message || "Runtime heartbeat" },
    status: { icon: "activity", title: "Status", detail: data.message || data.status || "Runtime status" },
    "permission.requested": { icon: "shield", title: "Permission Requested", detail: data.message || "Permission requested" },
    permission_request: { icon: "shield", title: "Permission Requested", detail: data.message || "Permission requested" },
    "permission.resolved": { icon: "shield-check", title: "Permission Resolved", detail: data.message || "Permission resolved" },
    permission_resolved: { icon: "shield-check", title: "Permission Resolved", detail: data.message || "Permission resolved" },
    "permission.denied": { icon: "shield-alert", title: "Permission Denied", detail: data.message || data.reason || "Permission denied" },
    "permission.allowed": { icon: "shield-check", title: "Permission Allowed", detail: data.message || "Permission allowed" },
    "stream.started": { icon: "activity", title: "Stream Started", detail: data.message || "Streaming response started" },
    "chat.incomplete": { icon: "alert-triangle", title: "Incomplete", detail: data.incomplete_reason || data.message || "Incomplete after auto-continue" },
    "chat.blocked": { icon: "shield-alert", title: "Blocked", detail: data.message || "Blocked waiting for permission" },
    "chat.empty_final": { icon: "alert-triangle", title: "Empty Final", detail: data.message || "Empty final response" },
    "chat.failed": { icon: "x-circle", title: "Chat Failed", detail: data.error || data.message || "Chat failed" },
    "chat.completed": { icon: "check-circle-2", title: "Chat Completed", detail: data.message || "Chat completed" },
    final: { icon: "flag", title: "Final", detail: data.incomplete_reason || data.message || data.completion_state || "Final response received" },
    "provider.retry": { icon: "refresh-cw", title: "Provider Retry", detail: data.message || "Provider API retrying" },
    "provider.rate_limit": { icon: "clock-alert", title: "Provider Rate Limit", detail: data.message || "Provider rate limit" },
    "model.retry": { icon: "refresh-cw", title: "Model Retry", detail: data.message || "Model retrying" },
    "event_bridge.connected": { icon: "plug", title: "Event Bridge Connected", detail: data.message || "Runtime event bridge connected" },
    "event_bridge.disconnected": { icon: "unplug", title: "Event Bridge Disconnected", detail: data.message || "Runtime event bridge disconnected" },
    "event_bridge.reconnected": { icon: "plug-zap", title: "Event Bridge Reconnected", detail: data.message || "Runtime event bridge reconnected" },
    "opencode.raw": { icon: "terminal", title: "OpenCode Event", detail: data.summary || data.message || "OpenCode runtime event" },
    "opencode.status.validated": { icon: "shield-check", title: "OpenCode Status Validated", detail: data.message || "OpenCode active status validated" },
    "assistant.message.started": { icon: "message-square", title: "Assistant Message Started", detail: data.message || "Assistant message started" },
    "assistant.message.updated": { icon: "message-square", title: "Assistant Message Updated", detail: data.message || data.delta || "Assistant message updated" },
    "assistant.message.completed": { icon: "message-square-check", title: "Assistant Message Completed", detail: data.message || "Assistant message completed" },
    "provider.status": { icon: "activity", title: "Provider Status", detail: data.message || data.status || "Provider status update" },
    "skill.loaded": { icon: "zap", title: "Skill Loaded", detail: data.skill || data.message || "Skill loaded" },
    "skill.detected": { icon: "zap", title: "Skill Detected", detail: data.skill || data.message || "Skill detected" },
    "skill.blocked": { icon: "shield-alert", title: "Skill Blocked", detail: data.reason || data.blocked_reason || data.message || "Skill blocked" },
    "skill.command.executed": { icon: "terminal", title: "Skill Command Executed", detail: data.command || data.message || "Skill command executed" },
    "skill.prompt_applied": { icon: "layers", title: "Skill Prompt Applied", detail: data.skill || data.message || "Skill prompt applied" },
    "skill.completed": { icon: "check-square", title: "Skill Completed", detail: data.message || "Skill completed" },
    "task.started": { icon: "list-start", title: "Task Started", detail: data.message || "Task started" },
    "task.completed": { icon: "list-checks", title: "Task Completed", detail: data.message || "Task completed" },
    "usage.updated": { icon: "gauge", title: "Usage Updated", detail: data.message || "Usage updated" },
    "message.delta": { icon: "message-square", title: "Message Streaming", detail: data.message || "Streaming" },
    "message.completed": { icon: "message-square-check", title: "Message Completed", detail: data.message || "Completed" },
    skill_contract_cleared: {
      icon: "x-circle",
      title: "Active Skill Cleared",
      detail: data.skill ? `${data.skill} cleared` : "Active skill cleared",
    },
  };
  return byType[type] || { icon: "circle", title: type.replaceAll("_", " "), detail: "" };
}

function isThinkingPanelActiveForAgent(agentId) {
  return (
    agentId === state.selectedAgentId &&
    state.toolPanelOpen &&
    state.activeUtilityPanel === "thinking"
  );
}

function scheduleThinkingPanelRefresh(agentId) {
  if (thinkingPanelRefreshRaf) return;
  thinkingPanelRefreshRaf = requestAnimationFrame(() => {
    thinkingPanelRefreshRaf = null;
    if (!isThinkingPanelActiveForAgent(agentId)) return;
    const chatState = ensureChatState(agentId);
    renderThinkingPanelFromClientState(chatState);
  });
}

function extractContextBudget(contextState) {
  if (!contextState || typeof contextState !== "object") return null;
  const budget = contextState.budget;
  return budget && typeof budget === "object" ? budget : null;
}

function hasMeaningfulContextState(value) {
  if (!value || typeof value !== "object") return false;

  const scalarKeys = [
    "objective",
    "summary",
    "current_state",
    "next_step",
    "recovery_context_message",
    "compaction_level",
  ];
  if (scalarKeys.some((key) => String(value[key] || "").trim())) return true;

  const listKeys = ["constraints", "decisions", "open_loops"];
  if (listKeys.some((key) => Array.isArray(value[key]) && value[key].some((item) => String(item || "").trim()))) {
    return true;
  }

  const budget = value.budget;
  if (budget && typeof budget === "object") {
    return Object.values(budget).some((item) => {
      if (item == null || item === "") return false;
      if (Array.isArray(item) && item.length === 0) return false;
      if (typeof item === "object" && !Array.isArray(item) && Object.keys(item).length === 0) return false;
      return true;
    });
  }

  return false;
}

function hasMeaningfulContextContents(value) {
  if (!value || typeof value !== "object") return false;

  const scalarKeys = [
    "objective",
    "summary",
    "current_state",
    "next_step",
    "recovery_context_message",
  ];
  if (scalarKeys.some((key) => String(value[key] || "").trim())) return true;

  const listKeys = ["constraints", "decisions", "open_loops"];
  if (listKeys.some((key) => Array.isArray(value[key]) && value[key].some((item) => String(item || "").trim()))) {
    return true;
  }

  return false;
}

function pickMeaningfulContextState(...candidates) {
  for (const candidate of candidates) {
    if (hasMeaningfulContextState(candidate)) return candidate;
  }
  return null;
}

function getRuntimeEventData(event) {
  const data = event?.data && typeof event.data === "object" ? event.data : {};
  const detailPayload = event?.detail_payload && typeof event.detail_payload === "object" ? event.detail_payload : {};
  return { ...data, ...detailPayload };
}

function extractLatestContextStateFromEvents(events) {
  if (!Array.isArray(events)) return null;

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const data = getRuntimeEventData(event);
    const contextState = data?.context_state;
    if (hasMeaningfulContextContents(contextState)) return contextState;
  }

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const data = getRuntimeEventData(event);
    const contextState = data?.context_state;
    if (hasMeaningfulContextState(contextState)) return contextState;
  }
  return null;
}

function pickContextStateWithContentsFirst(...candidates) {
  for (const candidate of candidates) {
    if (hasMeaningfulContextContents(candidate)) return candidate;
  }
  for (const candidate of candidates) {
    if (hasMeaningfulContextState(candidate)) return candidate;
  }
  return null;
}

function pickContextBudget(...candidates) {
  for (const candidate of candidates) {
    const budget = extractContextBudget(candidate);
    if (budget && typeof budget === "object" && Object.keys(budget).length) return budget;
  }
  return null;
}

function hasMeaningfulThinkingSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return false;
  if (hasMeaningfulContextState(snapshot.contextState)) return true;
  if (snapshot.contextBudget && typeof snapshot.contextBudget === "object" && Object.keys(snapshot.contextBudget).length) return true;
  if (Array.isArray(snapshot.events) && snapshot.events.length) return true;
  return false;
}

function updateThinkingContextFromEvent(thinking, entry) {
  if (!thinking || !entry) return;
  const data = entry.data || {};
  if (!["context_snapshot", "context_compaction_planned", "context_compaction_applied"].includes(entry.type)) return;

  const contextState = (
    data.context_state && typeof data.context_state === "object"
      ? data.context_state
      : null
  );
  const hasIncomingContents = hasMeaningfulContextContents(contextState);
  const hasExistingContents = hasMeaningfulContextContents(thinking.contextState);
  const meaningfulContextState = hasMeaningfulContextState(contextState) ? contextState : null;
  const budget = (
    data.budget && typeof data.budget === "object"
      ? data.budget
      : extractContextBudget(meaningfulContextState)
  );

  if (hasIncomingContents || (meaningfulContextState && !hasExistingContents && !hasMeaningfulContextState(thinking.contextState))) {
    thinking.contextState = meaningfulContextState;
  }
  if (budget) thinking.contextBudget = budget;
}

function getActiveThinkingSnapshot(chatState) {
  return chatState?.inflightThinking || chatState?.lastThinkingSnapshot || null;
}

function truncateThinkingText(value, max = 700) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function formatElapsedDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}

function formatThinkingTimestamp(value) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function isNonSuccessCompletionState(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return Boolean(normalized && !["success", "completed", "complete", "done"].includes(normalized));
}

function isSecretEventFieldName(key) {
  const normalized = String(key || "").trim().toLowerCase().replaceAll("-", "_");
  return (
    normalized === "token"
    || normalized === "password"
    || normalized === "api_key"
    || normalized === "apikey"
    || normalized === "authorization"
    || normalized.includes("password")
    || normalized.includes("api_key")
    || normalized.includes("access_token")
  );
}

function sanitizeEventDetailPayload(value, depth = 0) {
  if (depth > 4) return "[truncated]";
  if (Array.isArray(value)) {
    const items = value.slice(0, 30).map((item) => sanitizeEventDetailPayload(item, depth + 1));
    if (value.length > 30) items.push(`... ${value.length - 30} more items`);
    return items;
  }
  if (value && typeof value === "object") {
    const result = {};
    Object.entries(value).slice(0, 40).forEach(([key, item]) => {
      result[key] = isSecretEventFieldName(key) ? "[redacted]" : sanitizeEventDetailPayload(item, depth + 1);
    });
    if (Object.keys(value).length > 40) result["..."] = `${Object.keys(value).length - 40} more fields`;
    return result;
  }
  if (typeof value === "string") return truncateThinkingText(value, 1200);
  return value;
}

function nonSuccessHintForPayload(payload = {}) {
  const reason = String(
    payload.incomplete_reason ||
    payload.incompleteReason ||
    payload.error ||
    payload.detail ||
    payload.reason ||
    ""
  ).toLowerCase();

  if (reason.includes("opencode_abort_still_active")) {
    return "Stop was requested, but OpenCode still reports this session is running. Reset the session or start a new chat.";
  }

  const status = String(payload.status || payload.completion_state || "").toLowerCase();
  if (["busy", "running", "streaming", "retry"].includes(status)) {
    return "The assistant is still working. Wait for it to finish before sending another message.";
  }

  return 'You can send "continue" to ask the runtime to resume, or inspect the event details here.';
}

function getThinkingSnapshotStatus(snapshot) {
  const completionState = String(snapshot?.completion_state || "").trim().toLowerCase();
  if (snapshot?.completed) {
    if (completionState === "failed" || completionState === "error") return "Failed";
    if (isNonSuccessCompletionState(completionState) || snapshot?.incomplete_reason) return "Incomplete";
    return "Completed";
  }
  if (snapshot?.connectionStatus === "reconnecting" || snapshot?.status === "reconnecting") return "Syncing";
  if (snapshot?.connectionStatus === "disconnected") return "Disconnected";
  return "Connected";
}

function renderThinkingPanelFromClientState(chatState) {
  if (!dom.toolPanelBody) return;
  const uiState = computeOpenCodeRuntimeUiState(
    (typeof getSelectedAgent === "function") ? (getSelectedAgent() || {}) : {},
    chatState || {}
  );
  const runtimeStateNotes = renderOpenCodeRuntimeStateNotes(uiState);
  const snapshot = getActiveThinkingSnapshot(chatState);
  if (!snapshot) {
    dom.toolPanelBody.innerHTML = `<div class="portal-panel-section"><div class="portal-panel-title">Runtime State</div>${runtimeStateNotes}<div class="portal-panel-note">${safe(openCodeRuntimeUiStatusText(uiState))}</div></div><div class="portal-inline-state">Waiting for runtime events…</div>`;
    return;
  }
  const events = Array.isArray(snapshot.events) ? snapshot.events : [];
  const contextState = hasMeaningfulContextState(snapshot.contextState) ? snapshot.contextState : null;
  const hasContextContents = hasMeaningfulContextContents(contextState);
  const budget = (snapshot.contextBudget && typeof snapshot.contextBudget === "object")
    ? snapshot.contextBudget
    : extractContextBudget(contextState);
  const usagePercentRaw = budget ? (budget.prepared_usage_percent ?? budget.usage_percent) : null;
  const usagePercent = Number(usagePercentRaw);
  const clampedPercent = Number.isFinite(usagePercent) ? Math.max(0, Math.min(100, usagePercent)) : 0;
  const preparedTokens = budget?.prepared_tokens ?? budget?.estimated_tokens;
  const contextWindowTokens = budget?.context_window_tokens;
  const untilSoft = budget?.tokens_until_soft_threshold;
  const untilHard = budget?.tokens_until_hard_threshold;
  const nextPruningPolicy = budget?.next_pruning_policy;
  const latestSkillEvent = [...events].reverse().find((event) => ["skill_contract_active", "skill_runtime_applied", "skill_matched"].includes(event?.type));
  const skillData = latestSkillEvent?.data || {};
  const visibleEvents = events.slice(-100);
  const capNote = events.length > 100 ? `<div class="portal-panel-note">showing latest 100 of ${events.length} events</div>` : "";
  const now = Date.now();
  const startedAt = Number(snapshot.startedAt || snapshot.createdAt || now);
  const elapsedMs = snapshot.completed && snapshot.completedAt
    ? Number(snapshot.completedAt) - startedAt
    : now - startedAt;
  const lastEventAt = Number(snapshot.lastEventAt || 0);
  const lastEventLabel = lastEventAt ? formatThinkingTimestamp(lastEventAt) : "—";
  const liveStatus = getThinkingSnapshotStatus(snapshot);
  const completionState = String(snapshot.completion_state || snapshot.completionState || "").trim();
  const incompleteReason = String(snapshot.incomplete_reason || snapshot.incompleteReason || "").trim();
  const showNonSuccess = isNonSuccessCompletionState(completionState) || Boolean(incompleteReason);
  const nonSuccessHint = nonSuccessHintForPayload({
    ...snapshot,
    completion_state: completionState,
    incomplete_reason: incompleteReason,
  });
  const stillRunning = !snapshot.completed && elapsedMs >= 10 * 60 * 1000;
  const contextSourceLabel = snapshot.completed
    ? (snapshot.contextSource === "last_observed_live"
        ? "Last observed live snapshot — persisted snapshot pending"
        : snapshot.contextSource === "context_window_only"
          ? "Context window only — no context contents captured"
          : "Final Context Snapshot")
    : "Live Context Snapshot";

  const renderArray = (value) => {
    if (!Array.isArray(value) || !value.length) return '<div class="portal-panel-note">—</div>';
    return `<ul>${value.slice(0, 10).map((item) => `<li>${safe(truncateThinkingText(item, 220))}</li>`).join("")}</ul>`;
  };

  const timeline = visibleEvents.map((event) => {
    const view = getThinkingEventDisplay(event);
    const payload = view.args ?? view.result ?? view.output ?? event.safe_detail_payload ?? event.data ?? null;
    const safePayload = sanitizeEventDetailPayload(payload);
    const detailText = (safePayload && (typeof safePayload === "string" || typeof safePayload === "object"))
      ? truncateThinkingText(typeof safePayload === "string" ? safePayload : JSON.stringify(safePayload, null, 2), 1200)
      : "";
    const detailJson = detailText
      ? `<details class="portal-event-detail"><summary>Details</summary><pre class="portal-panel-pre">${safe(detailText)}</pre></details>`
      : "";
    const kind = String(event.kind || view.kind || "").trim().toLowerCase();
    const running = kind === "running" || ["tool.started", "heartbeat"].includes(event.type);
    const replayed = Boolean(event.replayed);
    const badges = [
      running ? '<span class="portal-live-chip is-running">Running</span>' : "",
      replayed ? '<span class="portal-live-chip is-replay">Historical</span>' : "",
    ].filter(Boolean).join("");
    return `<div class="portal-timeline-event ${kind ? `is-${safe(kind)}` : ""} ${replayed ? "is-replayed" : ""}"><span class="portal-timeline-event-icon"><i data-lucide="${safe(view.icon)}"></i></span><div class="portal-timeline-event-body"><div class="portal-panel-title">${safe(view.title)}${badges ? ` ${badges}` : ""}</div><div class="portal-panel-note">${safe(view.detail || "")}</div>${detailJson}</div></div>`;
  }).join("");

  dom.toolPanelBody.innerHTML = `
    <div class="portal-panel-stack portal-live-thinking" data-live-thinking-panel="1">
      <div class="portal-panel-section">
        <div class="portal-live-header">
          <div class="portal-panel-title">Thinking Process Live</div>
          <span class="portal-live-status is-${safe(liveStatus.toLowerCase())}">${safe(liveStatus)}</span>
        </div>
        <div class="portal-panel-note">Elapsed: ${safe(formatElapsedDuration(elapsedMs))}</div>
        <div class="portal-panel-note">Last event: ${safe(lastEventLabel)}</div>
        <div class="portal-panel-note">Request ID: ${safe(snapshot.requestId || snapshot.id || "—")}</div>
        <div class="portal-panel-note">Session ID: ${safe(snapshot.sessionId || "—")}</div>
        <div class="portal-panel-note">Events: ${events.length}</div>
        ${runtimeStateNotes}
        <div class="portal-panel-note">${safe(openCodeRuntimeUiStatusText(uiState))}</div>
      </div>
      ${showNonSuccess ? `<div class="portal-completion-banner is-warning"><strong>${safe(completionState || "non-success")}</strong>${incompleteReason ? `<div>${safe(incompleteReason)}</div>` : ""}<div>${safe(nonSuccessHint)}</div></div>` : ""}
      ${stillRunning ? '<div class="portal-inline-state">Still running. Live events will continue to appear here.</div>' : ""}
      ${budget ? `<div class="portal-panel-section"><div class="portal-panel-title">Context Window</div><div class="portal-panel-note">${safe(String(usagePercentRaw ?? "—"))}% used</div><div class="portal-context-meter"><div class="portal-context-meter-fill" style="width: ${clampedPercent}%"></div></div><div class="portal-panel-note">${safe(String(preparedTokens ?? "—"))} / ${safe(String(contextWindowTokens ?? "—"))} estimated tokens</div><div class="portal-panel-note">Micro threshold: ${safe(String(budget?.soft_threshold_percent ?? "—"))}%</div><div class="portal-panel-note">Hard threshold: ${safe(String(budget?.hard_threshold_percent ?? "—"))}%</div><div class="portal-panel-note">Next: ${safe(String(budget?.next_compaction_action || "—"))}</div>${untilSoft != null ? `<div class="portal-panel-note">Until soft threshold: ${safe(String(untilSoft))} tokens</div>` : ""}${untilHard != null ? `<div class="portal-panel-note">Until hard threshold: ${safe(String(untilHard))} tokens</div>` : ""}${nextPruningPolicy ? `<div class="portal-panel-note">Pruning policy: ${safe(truncateThinkingText(nextPruningPolicy, 500))}</div>` : ""}</div>` : ""}
      <div class="portal-panel-section">
        <div class="portal-panel-title">Context Contents</div>
        <div class="portal-panel-note">${safe(contextSourceLabel)}</div>
        ${hasContextContents ? `<div class="portal-context-grid">
          <div class="portal-context-kv"><strong>objective</strong><div>${safe(truncateThinkingText(contextState?.objective || "", 700) || "—")}</div></div>
          <div class="portal-context-kv"><strong>summary</strong><div>${safe(truncateThinkingText(contextState?.summary || "", 700) || "—")}</div></div>
          <div class="portal-context-kv"><strong>current_state</strong><div>${safe(truncateThinkingText(contextState?.current_state || "", 700) || "—")}</div></div>
          <div class="portal-context-kv"><strong>next_step</strong><div>${safe(truncateThinkingText(contextState?.next_step || "", 700) || "—")}</div></div>
          <div class="portal-context-kv"><strong>constraints</strong>${renderArray(contextState?.constraints)}</div>
          <div class="portal-context-kv"><strong>decisions</strong>${renderArray(contextState?.decisions)}</div>
          <div class="portal-context-kv"><strong>open_loops</strong>${renderArray(contextState?.open_loops)}</div>
        </div>` : '<div class="portal-inline-state">No context snapshot was captured for this run.</div>'}
      </div>
      ${(skillData.skill || skillData.skill_name) ? `<div class="portal-panel-section"><div class="portal-panel-title">Active Skill</div><div class="portal-panel-note">${safe(skillData.skill || skillData.skill_name)}</div>${skillData.goal ? `<div class="portal-panel-note">Goal: ${safe(truncateThinkingText(skillData.goal, 300))}</div>` : ""}${skillData.turn_count != null ? `<div class="portal-panel-note">Turn: ${safe(String(skillData.turn_count))}</div>` : ""}${skillData.reason ? `<div class="portal-panel-note">Reason: ${safe(truncateThinkingText(skillData.reason, 180))}</div>` : ""}${Array.isArray(skillData.allowed_tools) && skillData.allowed_tools.length ? `<div class="portal-panel-note">Allowed tools: ${safe(skillData.allowed_tools.slice(0, 10).join(", "))}</div>` : ""}</div>` : ""}
      <div class="portal-panel-section">
        <div class="portal-panel-title">Execution Timeline</div>
        ${capNote}
        ${timeline || '<div class="portal-inline-state">Waiting for runtime events…</div>'}
      </div>
    </div>
  `;
  renderIcons();
}

async function loadPersistedThinkingPanel(
  sessionId,
  {
    preserveLiveOnFailure = false,
    preserveLiveIfEmpty = false,
    preserveLiveIfNoContext = false,
    expectedRequestId = "",
  } = {},
) {
  if (!state.selectedAgentId || !sessionId) return false;
  try {
    const response = await fetch(`/app/agents/${state.selectedAgentId}/thinking/panel?session_id=${encodeURIComponent(sessionId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const html = await response.text();
    const template = document.createElement("template");
    template.innerHTML = html;
    const root = template.content.querySelector("[data-thinking-panel-root]");
    const hasData = root?.dataset?.thinkingHasData === "1";
    const hasContext = root?.dataset?.thinkingHasContext === "1";
    const persistedRequestId = root?.dataset?.thinkingRequestId || "";
    const requestMatches = !expectedRequestId || !persistedRequestId || persistedRequestId === expectedRequestId;
    const selectedChatState = state.selectedAgentId ? ensureChatState(state.selectedAgentId) : null;
    const localSnapshot = getActiveThinkingSnapshot(selectedChatState);
    const localHasContext = hasMeaningfulContextContents(localSnapshot?.contextState);
    if (preserveLiveIfEmpty && (!hasData || !requestMatches)) return false;
    if (preserveLiveIfNoContext && localHasContext && !hasContext) return false;
    if (dom.toolPanelBody) dom.toolPanelBody.innerHTML = html;
    renderIcons();
    return true;
  } catch (err) {
    if (preserveLiveOnFailure) return false;
    setToolPanel("Thinking Process", `<div class="portal-inline-state is-error">Error: ${safe(err.message)}</div>`, "thinking");
    return false;
  }
}

async function openThinkingProcessPanel() {
  if (!state.selectedAgentId) {
    showToast("Please select an assistant first");
    return;
  }

  const chatState = ensureChatState(state.selectedAgentId);
  const liveSnapshot = getActiveThinkingSnapshot(chatState);
  let currentSessionId = currentSessionIdForSelectedAgent()
    || liveSnapshot?.sessionId
    || "";
  const hiddenSessionInput = document.getElementById("chat-session-id");
  if (!currentSessionId && hiddenSessionInput) {
    currentSessionId = (hiddenSessionInput.value || "").trim();
  }
  const localSnapshot = getActiveThinkingSnapshot(chatState);
  const isLiveRun = !!(localSnapshot && (chatState.currentRequest || !localSnapshot.completed));
  const localMatchesSession = !currentSessionId || !localSnapshot?.sessionId || localSnapshot.sessionId === currentSessionId;
  const canUseLocalSnapshot = localMatchesSession && (isLiveRun || hasMeaningfulThinkingSnapshot(localSnapshot));
  if (canUseLocalSnapshot) {
    setToolPanel("Thinking Process", "", "thinking");
    renderThinkingPanelFromClientState(chatState);
    ensureEventSocketForSelectedAgent();
    if (localSnapshot.completed && currentSessionId) {
      await loadPersistedThinkingPanel(currentSessionId, {
        preserveLiveOnFailure: true,
        preserveLiveIfEmpty: true,
        preserveLiveIfNoContext: true,
        expectedRequestId: localSnapshot.requestId || localSnapshot.id || "",
      });
    }
    return;
  }

  if (!currentSessionId) {
    setToolPanel("Thinking Process", '<div class="portal-inline-state">No session selected. Start a conversation first.</div>', "thinking");
    return;
  }

  setToolPanel("Thinking Process", '<div class="portal-inline-state">Loading…</div>', "thinking");
  await loadPersistedThinkingPanel(currentSessionId);
}

function runtimeEventSummaryHash(value) {
  const text = String(value || "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash) + text.charCodeAt(index);
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}

function runtimeEventUniqueId(event) {
  const data = (event?.data && typeof event.data === "object") ? event.data : {};
  return String(
    event?.runtime_event_id
    || event?.event_id
    || event?.id
    || data.runtime_event_id
    || data.event_id
    || data.id
    || ""
  );
}

function runtimeEventDedupKey(event) {
  if (!event || typeof event !== "object") return "";
  const data = (event.data && typeof event.data === "object") ? event.data : {};
  const eventId = runtimeEventUniqueId(event);
  if (eventId) return `id:${eventId}`;
  const eventType = normalizeRuntimeEventTypeAlias(event.type || event.event_type || data.type || data.event_type || "");
  const createdAt = event.created_at || data.created_at || event.ts || "";
  const summary = event.summary || data.summary || data.message || "";
  return `${eventType}|${createdAt}|${runtimeEventSummaryHash(summary)}`;
}

function mergeThinkingEvents(primaryEvents, secondaryEvents) {
  const first = Array.isArray(primaryEvents) ? primaryEvents : [];
  const second = Array.isArray(secondaryEvents) ? secondaryEvents : [];
  const merged = [];
  const seen = new Set();
  const localRuntimeEventDedupKey = (typeof runtimeEventDedupKey === "function")
    ? runtimeEventDedupKey
    : (event) => {
      const data = (event?.data && typeof event.data === "object") ? event.data : {};
      const eventId = event?.runtime_event_id || event?.event_id || event?.id || data.runtime_event_id || data.event_id || data.id || "";
      if (eventId) return `id:${eventId}`;
      const createdAt = event?.created_at || data.created_at || event?.ts || "";
      const summary = event?.summary || data.summary || data.message || "";
      return `${event?.type || event?.event_type || ""}|${createdAt}|${summary}`;
    };
  const add = (event) => {
    if (!event || typeof event !== "object") return;
    const key = localRuntimeEventDedupKey(event);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(event);
  };
  first.forEach(add);
  second.forEach(add);
  return merged;
}

function normalizeThinkingEvents(events) {
  if (!Array.isArray(events)) return [];
  return events
    .map((event) => normalizeRuntimeEvent(event) || event)
    .filter((event) => event && typeof event === "object");
}

function normalizePayloadThinkingEvents(events) {
  return normalizeThinkingEvents(events);
}

// Render thinking events from chat response (non-WebSocket)
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Note: Event rendering is handled by thinking tool panel live renderer.

function isAssistantMessageRuntimeEvent(type) {
  return [
    "assistant.message.started",
    "assistant.message.updated",
    "assistant.message.completed",
    "message.delta",
    "message.completed",
  ].includes(String(type || ""));
}

function handleAssistantMessageRuntimeEvent(agentId, chatState, entry, eventMatchesActiveRequest, eventMatchesSocketRequest) {
  if (!chatState?.currentRequest || !isAssistantMessageRuntimeEvent(entry?.type)) return "ignored";
  const requestCtx = chatState.currentRequest;
  const canUseEvent = eventMatchesActiveRequest || eventMatchesSocketRequest || !entry.request_id;
  if (!canUseEvent) return "ignored";
  const data = entry.data && typeof entry.data === "object" ? entry.data : {};
  const type = entry.type;
  if ((type === "message.delta" || type === "assistant.message.updated") && shouldIgnoreAssistantStreamDelta(data, requestCtx)) {
    return "ignored";
  }
  const deltaText = getChatStreamTextPayload(data);
  let visibleText = extractAssistantVisibleText(data);
  if (type === "message.delta" && deltaText) {
    requestCtx.streamedText = `${requestCtx.streamedText || ""}${deltaText}`;
    visibleText = requestCtx.streamedText;
  } else if (visibleText) {
    requestCtx.streamedText = visibleText;
  } else {
    visibleText = requestCtx.streamedText || "";
  }
  const displayBlocks = extractAssistantDisplayBlocks(data);
  const rowPayload = {
    ...data,
    request_id: entry.request_id || data.request_id || requestCtx.requestId || requestCtx.clientRequestId,
    session_id: entry.session_id || data.session_id || requestCtx.sessionIdAtSend || chatState.sessionId || "",
    response: visibleText,
    display_blocks: displayBlocks,
  };
  if ((type === "assistant.message.updated" || type === "message.delta") && (visibleText || displayBlocks.length)) {
    updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, { partial: true });
  }
  if (type === "assistant.message.completed" || type === "message.completed") {
    if (visibleText || displayBlocks.length) {
      updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, {
        partial: true,
        completed: false,
      });
    }

    requestCtx.sawAssistantMessageCompleted = true;
    requestCtx.awaitingAuthoritativeFinal = true;
    requestCtx.assistantCompletedPreview = {
      response: visibleText,
      display_blocks: displayBlocks,
      assistant_message_id: rowPayload.assistant_message_id || "",
      assistant_message_ids: normalizeAssistantMessageIds(rowPayload),
      request_id: rowPayload.request_id,
      session_id: rowPayload.session_id,
    };

    return "completed";
  }
  return "updated";
}

function markThinkingTerminalFromEvent(chatState, entry = {}) {
  if (!chatState) return false;

  if (chatState.inflightThinking) {
    chatState.inflightThinking.completed = true;
    chatState.inflightThinking.status = chatState.inflightThinking.status || "completed";
    chatState.inflightThinking.completedAt = chatState.inflightThinking.completedAt || Date.now();
    updateThinkingContextFromEvent(chatState.inflightThinking, entry);
    chatState.lastThinkingSnapshot = {
      ...chatState.inflightThinking,
      events: mergeThinkingEvents(chatState.inflightThinking.events || [], [entry]),
      completed: true,
      completedAt: chatState.inflightThinking.completedAt,
    };
    return true;
  }

  if (chatState.lastThinkingSnapshot) {
    chatState.lastThinkingSnapshot = {
      ...chatState.lastThinkingSnapshot,
      events: mergeThinkingEvents(chatState.lastThinkingSnapshot.events || [], [entry]),
      completed: true,
      completedAt: chatState.lastThinkingSnapshot.completedAt || Date.now(),
    };
    updateThinkingContextFromEvent(chatState.lastThinkingSnapshot, entry);
    return true;
  }

  return false;
}

function handleAgentEventMessage(raw, socketCtx = {}) {
  let payload = null;
  try { payload = JSON.parse(raw); } catch { return; }

  const entry = normalizeRuntimeEvent(payload);
  if (!entry) return;
  const currentAgentId = socketCtx.agentId || state.selectedAgentId;
  const chatState = ensureChatState(currentAgentId);
  if (!chatState) return;
  const currentSessionId = chatState.sessionId || socketCtx.sessionId || "";
  if (entry.agent_id && currentAgentId && entry.agent_id !== currentAgentId) return;
  if (entry.session_id && currentSessionId && entry.session_id !== currentSessionId) return;
  const currentRequestIds = new Set([
    chatState.currentRequest?.clientRequestId,
    chatState.currentRequest?.requestId,
    chatState.inflightThinking?.requestId,
    chatState.inflightThinking?.id,
  ].map((value) => String(value || "")).filter(Boolean));
  const localRequestId = chatState.currentRequest?.requestId
    || chatState.currentRequest?.clientRequestId
    || "";
  const socketRequestId = socketCtx.requestId || "";
  const currentRequestId = socketRequestId || localRequestId;
  const type = entry.type;
  const eventMatchesCurrentRequest = Boolean(entry.request_id && currentRequestIds.has(entry.request_id));
  const eventMatchesSocketRequest = Boolean(
    entry.request_id && socketRequestId && entry.request_id === socketRequestId
  );
  const canAdoptRequestId = Boolean(
    (type === "chat.started" || type === "assistant.message.started")
    && entry.request_id
    && chatState.currentRequest
    && !eventMatchesCurrentRequest
    && (!entry.session_id || !currentSessionId || entry.session_id === currentSessionId)
  );
  if (
    entry.request_id
    && currentRequestId
    && !eventMatchesCurrentRequest
    && !eventMatchesSocketRequest
    && !canAdoptRequestId
  ) return;
  if (canAdoptRequestId) {
    chatState.currentRequest.requestId = entry.request_id;
    currentRequestIds.add(entry.request_id);
  }
  const eventMatchesLastCompletedRequest = Boolean(
    entry.request_id
    && chatState.lastThinkingSnapshot?.requestId
    && entry.request_id === chatState.lastThinkingSnapshot.requestId
  );
  if (
    entry.session_id
    && !currentSessionId
    && !(eventMatchesCurrentRequest || eventMatchesSocketRequest || eventMatchesLastCompletedRequest || canAdoptRequestId)
  ) {
    // Drop unmatched stale events when no current session is bound.
    // Otherwise they can recreate inflightThinking and cause false busy/session pollution.
    return;
  }

  // Handle additive runtime state fields while keeping existing event semantics.
  const isCompletion = isCompletionRuntimeState(entry.state);
  const lifecycleType = entry.lifecycle_type;
  if (!isTrackableThinkingEvent(type) && !lifecycleType && !isCompletion) return;

  const isLateEventForCompletedRequest = Boolean(
    !chatState.currentRequest
    && eventMatchesLastCompletedRequest
    && chatState.lastThinkingSnapshot
  );

  if (isLateEventForCompletedRequest) {
    chatState.lastThinkingSnapshot = {
      ...chatState.lastThinkingSnapshot,
      events: mergeThinkingEvents(chatState.lastThinkingSnapshot.events || [], [entry]),
      completed: true,
    };
    updateThinkingContextFromEvent(chatState.lastThinkingSnapshot, entry);
    if (isThinkingPanelActiveForAgent(currentAgentId)) {
      scheduleThinkingPanelRefresh(currentAgentId);
    }
    return;
  }

  if (!chatState.inflightThinking) {
    chatState.inflightThinking = {
      id: entry.request_id || `event-${Date.now()}`,
      requestId: entry.request_id || "",
      sessionId: entry.session_id || currentSessionId || "",
      events: [],
      completed: false,
      started: false,
      contextState: null,
      contextBudget: null,
      startedAt: Date.now(),
      lastEventAt: Date.now(),
    };
  }
  if (entry.request_id && (!chatState.inflightThinking.requestId || type === "chat.started")) {
    chatState.inflightThinking.requestId = entry.request_id;
    chatState.inflightThinking.id = chatState.inflightThinking.id || entry.request_id;
  }
  if (entry.session_id && !chatState.inflightThinking.sessionId) {
    chatState.inflightThinking.sessionId = entry.session_id;
  }
  if (
    entry.session_id
    && !chatState.sessionId
    && (eventMatchesCurrentRequest || eventMatchesSocketRequest || canAdoptRequestId)
  ) {
    chatState.sessionId = entry.session_id;
    state.agentSessionIds.set(currentAgentId, entry.session_id);
    if (currentAgentId === state.selectedAgentId && dom.chatSessionId) {
      dom.chatSessionId.value = entry.session_id;
    }
  }

  if (!chatState.inflightThinking) return;
  chatState.inflightThinking.lastEventAt = Date.now();
  chatState.inflightThinking.lastEventTs = entry.ts || null;
  chatState.inflightThinking.lastEventCreatedAt = entry.created_at || "";
  chatState.inflightThinking.status = "connected";
  const runtimeEventDedupKey = (typeof globalThis !== "undefined" && typeof globalThis.runtimeEventDedupKey === "function")
    ? globalThis.runtimeEventDedupKey
    : (event) => {
      const data = (event?.data && typeof event.data === "object") ? event.data : {};
      const eventId = event?.runtime_event_id || event?.event_id || event?.id || data.runtime_event_id || data.event_id || data.id || "";
      if (eventId) return `id:${eventId}`;
      const eventType = event?.type || event?.event_type || data.type || data.event_type || "";
      const createdAt = event?.created_at || data.created_at || event?.ts || "";
      const summary = event?.summary || data.summary || data.message || "";
      return `${eventType}|${createdAt}|${summary}`;
    };
  const entryDedupKey = runtimeEventDedupKey(entry);
  const alreadySeen = (chatState.inflightThinking.events || []).some((event) => {
    const key = runtimeEventDedupKey(event);
    return key === entryDedupKey;
  });
  if (alreadySeen) {
    if (isThinkingPanelActiveForAgent(currentAgentId)) scheduleThinkingPanelRefresh(currentAgentId);
    return;
  }

  if (!chatState.inflightThinking.started && type !== "execution.started") {
    chatState.inflightThinking.events.push({
      type: "execution.started",
      raw_type: "execution.started",
      lifecycle_type: "execution.started",
      data: { message: "Execution started" },
      ts: entry.ts,
      state: "started",
    });
    chatState.inflightThinking.started = true;
  }

  chatState.inflightThinking.events.push(entry);
  if (type === "execution.started") chatState.inflightThinking.started = true;

  if (lifecycleType && lifecycleType !== type) {
    const terminalDetail = lifecycleType === "execution.failed"
      ? (entry?.data?.error || entry?.data?.message || "Execution failed")
      : (entry?.data?.message || "Execution complete");
    chatState.inflightThinking.events.push({
      type: lifecycleType,
      raw_type: lifecycleType,
      lifecycle_type: lifecycleType,
      data: { ...entry.data, message: terminalDetail },
      ts: entry.ts,
      state: entry.state,
    });
  }

  updateThinkingContextFromEvent(chatState.inflightThinking, entry);
  if (entry.request_id && canAdoptRequestId && state.eventWsRequestId !== entry.request_id) {
    ensureEventSocketForAgent(currentAgentId, entry.session_id || currentSessionId, entry.request_id);
  }
  if (isThinkingPanelActiveForAgent(currentAgentId)) {
    scheduleThinkingPanelRefresh(currentAgentId);
  }

  let assistantRuntimeEventResult = "ignored";
  if (typeof handleAssistantMessageRuntimeEvent === "function") {
    assistantRuntimeEventResult = handleAssistantMessageRuntimeEvent(
      currentAgentId,
      chatState,
      entry,
      eventMatchesCurrentRequest,
      eventMatchesSocketRequest
    ) || "ignored";
  }

  if (
    assistantRuntimeEventResult === "finalized"
    && !chatState.inflightThinking
  ) {
    if (isThinkingPanelActiveForAgent(currentAgentId)) {
      scheduleThinkingPanelRefresh(currentAgentId);
    }
    syncSelectedAgentChatActionControls();
    return;
  }

  if (type === "edit.failed" && chatState.currentRequest?.edit && eventMatchesCurrentRequest) {
    const failureMessage = String(
      entry?.data?.error
      || entry?.data?.detail
      || entry?.data?.incomplete_reason
      || entry?.data?.message
      || "regeneration failed"
    );
    handleEditedRegenerationFailure(currentAgentId, chatState.currentRequest, failureMessage);
    return;
  }

  const isTerminalRuntimeEvent = (
    type === "execution.completed"
    || type === "execution.failed"
    || type === "skill_complete"
    || isCompletion
    || lifecycleType === "execution.completed"
    || lifecycleType === "execution.failed"
  );

  if (isTerminalRuntimeEvent) {
    markThinkingTerminalFromEvent(chatState, entry);
    if (isThinkingPanelActiveForAgent(currentAgentId)) {
      scheduleThinkingPanelRefresh(currentAgentId);
    }
    syncSelectedAgentChatActionControls();
  }
}

function hasLiveEventSocketWork(agentId, requestId = "") {
  const chatState = ensureChatState(agentId);
  if (!chatState) return false;
  if (isActiveRequestBlocking(chatState)) {
    if (!requestId) return true;
    return [
      chatState.currentRequest.clientRequestId,
      chatState.currentRequest.requestId,
    ].map((value) => String(value || "")).includes(String(requestId));
  }
  return Boolean(chatState.inflightThinking && chatState.inflightThinking.completed === false);
}

function updateEventSocketStatus(agentId, status) {
  const chatState = ensureChatState(agentId);
  const snapshot = chatState?.inflightThinking || chatState?.lastThinkingSnapshot;
  if (!snapshot) return;
  snapshot.connectionStatus = status;
  snapshot.status = status === "reconnecting" ? "reconnecting" : snapshot.status;
  if (status === "connected") {
    snapshot.connectedAt = Date.now();
  } else if (status === "reconnecting") {
    snapshot.reconnectingAt = Date.now();
  } else if (status === "disconnected") {
    snapshot.disconnectedAt = Date.now();
  }
  if (isThinkingPanelActiveForAgent(agentId)) scheduleThinkingPanelRefresh(agentId);
}

function scheduleEventSocketReconnect(agentId, sessionId, requestId = "") {
  if (!hasLiveEventSocketWork(agentId, requestId)) return;
  if (state.eventWsReconnectTimer) clearTimeout(state.eventWsReconnectTimer);
  const attempt = Math.min(state.eventWsReconnectAttempt || 0, 6);
  const baseDelay = Math.min(30000, 1000 * (2 ** attempt));
  const jitter = Math.round(baseDelay * 0.2 * Math.random());
  state.eventWsReconnectAttempt = attempt + 1;
  updateEventSocketStatus(agentId, "reconnecting");
  state.eventWsReconnectTimer = setTimeout(() => {
    state.eventWsReconnectTimer = null;
    ensureEventSocketForAgent(agentId, sessionId, requestId || null);
  }, baseDelay + jitter);
}

function ensureEventSocketForAgent(agentId, sessionId, requestId = null) {
  if (!agentId) return;
  const chatState = ensureChatState(agentId);
  const session = sessionId || chatState?.sessionId || "";
  if (agentId !== state.selectedAgentId) return;
  if (state.eventWs) {
    const sameAgent = state.eventWsAgentId === agentId;
    const sameSession = (state.eventWsSessionId || "") === (session || "");
    const sameRequest = (state.eventWsRequestId || "") === (requestId || "");
    const readyState = state.eventWs.readyState;
    if (sameAgent && sameSession && sameRequest && (readyState === WebSocket.OPEN || readyState === WebSocket.CONNECTING)) return;
    disconnectEventSocket();
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams();
  if (session) params.set("session_id", session);
  if (requestId) params.set("request_id", requestId);
  params.set("replay", "1");
  const lastEventAt = chatState?.inflightThinking?.lastEventCreatedAt
    || chatState?.lastThinkingSnapshot?.lastEventCreatedAt
    || "";
  if (lastEventAt) params.set("last_event_at", lastEventAt);
  const query = params.toString();
  const wsUrl = `${protocol}//${window.location.host}/a/${agentId}/api/events${query ? `?${query}` : ""}`;
  if (state.eventWsReconnectTimer) {
    clearTimeout(state.eventWsReconnectTimer);
    state.eventWsReconnectTimer = null;
  }
  const ws = new WebSocket(wsUrl);
  state.eventWs = ws;
  state.eventWsAgentId = agentId;
  state.eventWsSessionId = session || "";
  state.eventWsRequestId = requestId || "";
  ws.onopen = () => {
    state.eventWsReconnectAttempt = 0;
    updateEventSocketStatus(agentId, "connected");
  };
  ws.onmessage = (event) => {
    try {
      handleAgentEventMessage(event.data, { agentId, sessionId: session, requestId });
    } catch (error) {
      console.error("Portal chat event handler failed", error);
      const latestChatState = ensureChatState(agentId);
      if (latestChatState?.currentRequest) {
        setChatStatus("Live event update failed. Waiting for the final response...", true);
      }

      syncSelectedAgentChatActionControls();
    }
  };
  ws.onclose = () => {
    if (state.eventWs === ws) {
      state.eventWs = null;
      state.eventWsAgentId = null;
      state.eventWsSessionId = null;
      state.eventWsRequestId = null;
      if (hasLiveEventSocketWork(agentId, requestId || "")) {
        scheduleEventSocketReconnect(agentId, session, requestId || "");
      } else {
        updateEventSocketStatus(agentId, "disconnected");
      }
    }
  };
  ws.onerror = () => {
    updateEventSocketStatus(agentId, "reconnecting");
  };
}

function ensureEventSocketForSelectedAgent() {
  if (typeof ensureEventSocketForAgent !== "function") return;
  const agentId = state.selectedAgentId;
  if (!agentId) return;
  const chatState = ensureChatState(agentId);
  const requestId = chatState?.currentRequest?.clientRequestId || "";
  ensureEventSocketForAgent(agentId, currentSessionIdForSelectedAgent(), requestId || null);
}

function normalizeRuntimeHealthStatus(value) {
  const normalized = String(value || "unknown").trim().toLowerCase();
  if (["running", "online", "ready", "healthy", "started"].includes(normalized)) return "online";
  if (["stopped", "offline", "unavailable", "failed", "error"].includes(normalized)) return "offline";
  return normalized || "unknown";
}

function computeOpenCodeRuntimeUiState(agent = {}, chatState = {}) {
  const runtimeHealth = agent.runtime_status || agent.status || "unknown";
  const normalizedRuntimeHealth = normalizeRuntimeHealthStatus(runtimeHealth);
  const submitting = Boolean(chatState?.isSubmitting);
  const sessionStatus = normalizedRuntimeHealth === "offline"
    ? "unknown"
    : (submitting ? "busy" : "idle");
  const messageProgress = normalizedRuntimeHealth === "offline"
    ? "unknown"
    : (submitting ? "running" : "idle");

  return {
    runtimeHealth,
    normalizedRuntimeHealth,
    sessionStatus,
    messageProgress,
  };
}

function openCodeRuntimeUiStatusText(uiState = {}) {
  const runtimeHealth = uiState.normalizedRuntimeHealth || normalizeRuntimeHealthStatus(uiState.runtimeHealth);
  const sessionStatus = String(uiState.sessionStatus || "unknown").toLowerCase();
  if (runtimeHealth === "offline") return "Runtime offline. Session status unknown.";
  if (sessionStatus === "idle") return "Assistant online. Ready.";
  if (sessionStatus === "busy") return "Assistant online. Response in progress.";
  return "Assistant online. Session status unknown.";
}

function renderOpenCodeRuntimeStateNotes(uiState = {}) {
  return `
    <div class="portal-panel-note">Runtime: ${safe(uiState.normalizedRuntimeHealth || uiState.runtimeHealth || "unknown")}</div>
    <div class="portal-panel-note">Session: ${safe(uiState.sessionStatus || "unknown")}</div>
    <div class="portal-panel-note">Message: ${safe(uiState.messageProgress || "idle")}</div>
  `;
}

function applyTheme(theme) {
  const normalized = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", normalized);
  document.documentElement.classList.toggle("dark", normalized === "dark");
  localStorage.setItem("portal-theme", normalized);
  if (dom.themeToggle) dom.themeToggle.innerHTML = normalized === "light"
    ? '<i data-lucide="sun"></i>'
    : '<i data-lucide="moon"></i>';
  renderIcons();
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(current === "dark" ? "light" : "dark");
}

function setChatStatus(text, isError = false) {
  if (!dom.chatStatus) return;
  const agent = getSelectedAgent();
  const chatState = state.selectedAgentId ? ensureChatState(state.selectedAgentId) : {};
  const uiState = computeOpenCodeRuntimeUiState(agent || {}, chatState || {});
  const runtimeSummary = openCodeRuntimeUiStatusText(uiState);
  const visibleRuntimeSummary = (
    uiState.normalizedRuntimeHealth === "offline"
    || ["busy", "retry"].includes(String(uiState.sessionStatus || "").toLowerCase())
  );
  const visibleStatusText = visibleRuntimeSummary && !String(text || "").includes(runtimeSummary)
    ? [runtimeSummary, text].filter(Boolean).join(" ")
    : text;
  dom.chatStatus.textContent = visibleStatusText;
  dom.chatStatus.className = `portal-statusline${isError ? " is-error" : ""}`;
  dom.chatStatus.dataset.runtimeHealth = uiState.normalizedRuntimeHealth || uiState.runtimeHealth || "unknown";
  dom.chatStatus.dataset.sessionStatus = uiState.sessionStatus || "unknown";
  dom.chatStatus.dataset.messageProgress = uiState.messageProgress || "idle";
  const statusDetail = [
    runtimeSummary,
    `Runtime: ${uiState.normalizedRuntimeHealth || uiState.runtimeHealth || "unknown"}`,
    `Session: ${uiState.sessionStatus || "unknown"}`,
    `Message: ${uiState.messageProgress || "idle"}`,
  ];
  dom.chatStatus.title = statusDetail.join("\n");
  dom.chatStatus.setAttribute("aria-label", `${visibleStatusText}. ${statusDetail.join(". ")}`);
}

function scrollToBottom() {
  const scrollContainer = dom.messageScroll || dom.messageList;
  if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
}

function normalizeMarkdownText(text) {
  return decodeHtml(String(text || "")).replace(/\r\n?/g, "\n");
}

function isMeaningfulText(value) {
  return value !== null && value !== undefined && String(value).trim().length > 0;
}

function pickFirstMeaningfulBlockValue(block, keys) {
  if (!block || typeof block !== "object") return "";
  for (const key of keys) {
    if (isMeaningfulText(block[key])) {
      return String(block[key]);
    }
  }
  return "";
}

function hasRenderableDisplayBlock(block) {
  if (!block || typeof block !== "object") return false;
  const type = String(block.type || "").trim().toLowerCase();
  if (!type) return false;
  if (["markdown", "callout", "tool_result"].includes(type)) {
    return !!getDisplayBlockText(block);
  }
  if (type === "code") {
    return !!pickFirstMeaningfulBlockValue(block, ["code", "content", "text", "message", "output", "result", "value"]);
  }
  if (type === "table") {
    const headers = Array.isArray(block.headers) ? block.headers : (Array.isArray(block.columns) ? block.columns : []);
    const rows = Array.isArray(block.rows) ? block.rows : [];
    return headers.length > 0 || rows.length > 0 || !!getDisplayBlockText(block);
  }
  return !!getDisplayBlockText(block);
}

function parseDisplayBlocks(raw) {
  const normalizeBlocks = (blocks) => {
    if (!Array.isArray(blocks)) return [];
    return blocks
      .filter((block) => block && typeof block === "object" && typeof block.type === "string")
      .map((block) => ({ ...block, type: String(block.type).trim() }))
      .filter((block) => block.type.length > 0)
      .filter((block) => hasRenderableDisplayBlock(block));
  };

  if (Array.isArray(raw)) return normalizeBlocks(raw);
  if (typeof raw !== "string" || !raw) return [];

  try {
    return normalizeBlocks(JSON.parse(raw));
  } catch (error) {
    return [];
  }
}

function getDisplayBlockText(block) {
  if (!block || typeof block !== "object") return "";
  const textCandidates = [block.content, block.text, block.message, block.output, block.result, block.value];
  for (const value of textCandidates) {
    if (isMeaningfulText(value)) return String(value);
  }
  return "";
}

function renderCodeBlock(block) {
  const language = String(block?.lang || block?.language || "").trim().toLowerCase();
  const codeCandidates = [block?.code, block?.content, block?.text, block?.message, block?.output, block?.result, block?.value];
  const code = codeCandidates.find((value) => isMeaningfulText(value));
  const className = language ? `language-${language}` : "";
  return `
    <section class="message-block message-block-code">
      <div class="message-codeblock">
        <div class="message-codeblock-toolbar">
          <span class="message-codeblock-lang">${safe(language || "text")}</span>
          <button type="button" class="message-codeblock-copy" data-copy-text="${escapeHtmlAttr(code || "")}">Copy</button>
        </div>
        <pre><code class="${className}">${safe(code || "")}</code></pre>
      </div>
    </section>
  `;
}

function renderTableBlock(block) {
  const headers = Array.isArray(block?.headers)
    ? block.headers
    : (Array.isArray(block?.columns) ? block.columns : []);
  const rows = Array.isArray(block?.rows) ? block.rows : [];
  if (!headers.length && !rows.length) {
    return `<section class="message-block message-block-markdown">${md.render(normalizeMarkdownText(getDisplayBlockText(block)))}</section>`;
  }
  const headHtml = headers.length
    ? `<thead><tr>${headers.map((header) => `<th>${safe(header)}</th>`).join("")}</tr></thead>`
    : "";
  const bodyHtml = rows.length
    ? `<tbody>${rows.map((row) => `<tr>${(Array.isArray(row) ? row : []).map((cell) => `<td>${safe(cell)}</td>`).join("")}</tr>`).join("")}</tbody>`
    : "";
  return `
    <section class="message-block message-block-table">
      <div class="message-table-wrap">
        <table>${headHtml}${bodyHtml}</table>
      </div>
    </section>
  `;
}

function renderSingleDisplayBlock(block) {
  if (!block || typeof block !== "object") return "";
  const type = String(block.type || "").toLowerCase();
  const blockText = getDisplayBlockText(block);
  if (type === "markdown") {
    return `<section class="message-block message-block-markdown">${md.render(normalizeMarkdownText(blockText))}</section>`;
  }
  if (type === "code") return renderCodeBlock(block);
  if (type === "table") return renderTableBlock(block);
  if (type === "callout") {
    const tone = String(block.tone || "info").toLowerCase();
    const title = String(block.title || "").trim();
    return `
      <section class="message-block">
        <div class="message-callout is-${safe(tone)}">
          ${title ? `<div class="message-callout-title">${safe(title)}</div>` : ""}
          <div class="message-callout-content">${md.render(normalizeMarkdownText(blockText))}</div>
        </div>
      </section>
    `;
  }
  if (type === "tool_result") {
    const title = String(block.title || "Tool result");
    const status = String(block.status || "").toLowerCase();
    return `
      <section class="message-block">
        <div class="message-tool-result${status ? ` is-${safe(status)}` : ""}">
          <div class="message-tool-result-title">${safe(title)}</div>
          <div class="message-tool-result-content">${md.render(normalizeMarkdownText(blockText))}</div>
        </div>
      </section>
    `;
  }
  return `<section class="message-block message-block-markdown">${md.render(normalizeMarkdownText(blockText))}</section>`;
}

function renderDisplayBlocksToHtml(blocks, fallbackMarkdown = "") {
  const parsedBlocks = Array.isArray(blocks) ? blocks.filter((block) => hasRenderableDisplayBlock(block)) : [];
  if (!parsedBlocks.length) {
    if (isMeaningfulText(fallbackMarkdown)) {
      return md.render(normalizeMarkdownText(fallbackMarkdown));
    }
    return "";
  }
  const html = parsedBlocks.map((block) => renderSingleDisplayBlock(block)).join("");
  if (html) return html;
  if (isMeaningfulText(fallbackMarkdown)) {
    return md.render(normalizeMarkdownText(fallbackMarkdown));
  }
  return "";
}

async function copyText(text) {
  const value = String(text || "");
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (error) {}
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  return copied;
}


function setDebugCopyButtonCopied(button) {
  const label = button?.dataset?.copyLabel || "text";
  const token = String(Date.now());
  button.dataset.copyStateToken = token;
  button.classList.add("is-copied");
  button.title = `Copied ${label}`;
  button.setAttribute("aria-label", `Copied ${label}`);
  button.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i>';
  renderIcons();

  window.setTimeout(() => {
    if (button.dataset.copyStateToken !== token) return;
    button.classList.remove("is-copied");
    button.title = `Copy ${label}`;
    button.setAttribute("aria-label", `Copy ${label}`);
    button.innerHTML = '<i data-lucide="copy" class="w-4 h-4"></i>';
    delete button.dataset.copyStateToken;
    renderIcons();
  }, 1400);
}

document.addEventListener("click", async (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const button = target?.closest("[data-copy-debug-text]");
  if (!button) return;

  const block = button.closest("[data-copyable-text-block]");
  const source = block?.querySelector("[data-copy-source]");
  const text = source?.textContent || "";

  if (!text.trim()) {
    showToast("Nothing to copy");
    return;
  }

  button.disabled = true;
  try {
    const copied = await copyText(text);
    if (copied) {
      setDebugCopyButtonCopied(button);
      showToast("Copied to clipboard");
    } else {
      showToast("Copy failed");
    }
  } catch (error) {
    showToast("Copy failed");
  } finally {
    button.disabled = false;
  }
});

function enhanceMarkdownBlock(root) {
  if (!root) return;
  root.querySelectorAll("a").forEach((anchor) => {
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.classList.add("message-link");
  });

  root.querySelectorAll("table").forEach((table) => {
    if (table.closest(".message-table-wrap")) return;
    const wrapper = document.createElement("div");
    wrapper.className = "message-table-wrap";
    table.parentNode.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });

  root.querySelectorAll("pre > code").forEach((code) => {
    if (code.closest(".message-codeblock")) return;
    const pre = code.parentElement;
    if (!pre) return;
    const wrapper = document.createElement("div");
    wrapper.className = "message-codeblock";
    const toolbar = document.createElement("div");
    toolbar.className = "message-codeblock-toolbar";
    const lang = document.createElement("span");
    lang.className = "message-codeblock-lang";
    const rawClass = Array.from(code.classList).find((item) => item.startsWith("language-")) || "";
    lang.textContent = rawClass.replace("language-", "") || "text";
    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "message-codeblock-copy";
    copyButton.textContent = "Copy";
    copyButton.addEventListener("click", async () => {
      const copied = await copyText(code.textContent || "");
      if (!copied) return;
      copyButton.textContent = "Copied";
      copyButton.classList.add("is-copied");
      window.setTimeout(() => {
        copyButton.textContent = "Copy";
        copyButton.classList.remove("is-copied");
      }, 1400);
    });
    toolbar.append(lang, copyButton);
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.append(toolbar, pre);
  });

  root.querySelectorAll(".message-codeblock-copy[data-copy-text]").forEach((button) => {
    if (button.dataset.boundCopy === "1") return;
    button.dataset.boundCopy = "1";
    button.addEventListener("click", async () => {
      const copied = await copyText(button.dataset.copyText || "");
      if (!copied) return;
      button.textContent = "Copied";
      button.classList.add("is-copied");
      window.setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("is-copied");
      }, 1400);
    });
  });
}

function renderMarkdown(scope = document) {
  scope.querySelectorAll(".md-render").forEach((el) => {
    const markdown = normalizeMarkdownText(el.dataset.md || "");
    const blocks = parseDisplayBlocks(el.dataset.displayBlocks || "");
    el.innerHTML = renderDisplayBlocksToHtml(blocks, markdown);
    enhanceMarkdownBlock(el);
    el.querySelectorAll("pre code").forEach((code) => {
      if (code.dataset.highlighted === "1" || code.classList.contains("hljs")) return;
      hljs.highlightElement(code);
      code.dataset.highlighted = "1";
    });
  });
}

function decorateToolMessages(scope = document) {
  scope.querySelectorAll(".assistant-message .md-render").forEach((el) => {
    const text = (el.dataset.md || el.textContent || "").trim();
    const article = el.closest("article");
    if (!article) return;
    if (text.startsWith("[Tool") || text.startsWith("Tool ")) article.classList.add("tool-message");
    else article.classList.remove("tool-message");
  });
}

function renderIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function setDetailOpen(open) {
  state.detailOpen = open;
}

const TOOL_PANEL_DEFAULT_WIDTH = 580;
const TOOL_PANEL_MIN_PINNED_WIDTH = 340;
const TOOL_PANEL_MIN_OVERLAY_WIDTH = 300;
const TOOL_PANEL_PIN_BREAKPOINT_PX = 901;

function isViewportEligibleToPinToolPanel() {
  return window.matchMedia(`(min-width: ${TOOL_PANEL_PIN_BREAKPOINT_PX}px)`).matches;
}

function getSecondaryColumnWidthForPin() {
  if (state.secondaryPaneCollapsed) return 0;
  return window.matchMedia("(max-width: 1024px)").matches ? 260 : 296;
}

function getMainMinWidthForPin() {
  return window.matchMedia("(max-width: 1024px)").matches ? 300 : 360;
}

function getCurrentToolPanelWidth() {
  const cssTarget = dom.portalShell || document.documentElement;
  const fromVar = Number.parseFloat(getComputedStyle(cssTarget).getPropertyValue("--portal-tool-panel-width"));
  if (Number.isFinite(fromVar) && fromVar > 0) return fromVar;
  const fromPanel = dom.toolPanel?.getBoundingClientRect?.().width || 0;
  if (fromPanel > 0) return fromPanel;
  return TOOL_PANEL_DEFAULT_WIDTH;
}

function getPinnedToolPanelWidthBounds() {
  const min = TOOL_PANEL_MIN_PINNED_WIDTH;
  if (!isViewportEligibleToPinToolPanel()) {
    return {
      min,
      max: min,
      canPin: false,
    };
  }

  const shellRect = dom.portalShell?.getBoundingClientRect?.();
  const shellWidth = shellRect?.width || Math.max(0, window.innerWidth - 16);
  const available =
    shellWidth
    - 68
    - getSecondaryColumnWidthForPin()
    - getMainMinWidthForPin();

  const canPin = available >= min;
  const max = Math.max(
    min,
    Math.min(TOOL_PANEL_DEFAULT_WIDTH, available)
  );

  return {
    min,
    max,
    canPin,
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function setToolPanelWidth(widthPx) {
  const next = Math.round(widthPx);
  dom.portalShell?.style.setProperty("--portal-tool-panel-width", `${next}px`);
  if (dom.toolPanel) dom.toolPanel.style.width = "";
}

function clampToolPanelWidthForPinned() {
  const bounds = getPinnedToolPanelWidthBounds();
  const current = getCurrentToolPanelWidth();
  setToolPanelWidth(clamp(current, bounds.min, bounds.max));
  return bounds.canPin;
}

function isWideEnoughToPinToolPanel() {
  return getPinnedToolPanelWidthBounds().canPin;
}

function applyToolPanelState() {
  const open = !!state.toolPanelOpen;
  let pinned = open && !!state.toolPanelPinned;

  if (pinned && !isWideEnoughToPinToolPanel()) {
    state.toolPanelPinned = false;
    pinned = false;
  }

  if (pinned) {
    clampToolPanelWidthForPinned();
  }

  if (dom.toolPanel) dom.toolPanel.style.width = "";
  dom.toolPanel?.classList.toggle("is-open", open);
  dom.toolPanel?.classList.toggle("is-pinned", pinned);
  dom.portalShell?.classList.toggle("is-tool-panel-pinned", pinned);
  dom.toolBackdrop?.classList.toggle("hidden", !open || pinned);

  if (dom.pinToolPanel) {
    dom.pinToolPanel.classList.toggle("is-active", pinned);
    dom.pinToolPanel.setAttribute("aria-pressed", pinned ? "true" : "false");
    dom.pinToolPanel.setAttribute("title", pinned ? "Unpin panel" : "Pin panel");
    dom.pinToolPanel.setAttribute("aria-label", pinned ? "Unpin panel" : "Pin panel");
    dom.pinToolPanel.innerHTML = `<i data-lucide="${pinned ? "pin-off" : "pin"}" class="w-5 h-5"></i>`;
  }

  renderIcons();
}

function reconcilePinnedToolPanelForLayoutChange() {
  if (!state.toolPanelOpen || !state.toolPanelPinned) return;

  if (!isWideEnoughToPinToolPanel()) {
    state.toolPanelPinned = false;
    applyToolPanelState();
    return;
  }

  clampToolPanelWidthForPinned();
  applyToolPanelState();
}

function openToolPanel() {
  state.toolPanelOpen = true;
  applyToolPanelState();
}

function toggleToolPanelPinned() {
  if (!state.toolPanelOpen) return;
  const wantsToPin = !state.toolPanelPinned;
  if (wantsToPin) {
    if (!isWideEnoughToPinToolPanel()) {
      showToast("Pinning is available on wider screens.");
      return;
    }
    clampToolPanelWidthForPinned();
    state.toolPanelPinned = true;
  } else {
    state.toolPanelPinned = false;
  }
  applyToolPanelState();
  persistUiLayoutPreferences({
    includeSecondaryPane: false,
    includeToolPanel: true,
    clearToolPanelPreference: !state.toolPanelPinned,
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) throw new Error(await response.text());
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

async function handleErrorResponse(resp) {
  const fallbackMessage = `Request failed (HTTP ${resp.status})`;
  const contentType = resp.headers.get("content-type") || "";
  try {
    const rawText = await resp.text();
    const trimmedText = rawText?.trim() || "";
    if (contentType.includes("application/json")) {
      if (!trimmedText) return fallbackMessage;
      try {
        const err = JSON.parse(rawText);
        const detail = err?.detail;
        if (typeof detail === "string" && detail.trim()) return detail;
        if (Array.isArray(detail)) {
          const items = detail
            .map((e) => {
              if (typeof e === "string") return e;
              if (typeof e?.msg === "string") return e.msg;
              return JSON.stringify(e);
            })
            .filter((item) => typeof item === "string" && item.trim());
          if (items.length) return items.join(", ");
        }

        if (typeof err?.error?.message === "string" && err.error.message.trim()) return err.error.message;
        if (typeof err?.error === "string" && err.error.trim()) return err.error;
        if (typeof err?.message === "string" && err.message.trim()) return err.message;

        if (err?.error_type || err?.code) {
          const suffix = [err?.error_type, err?.code].filter(Boolean).join(" / ");
          return suffix ? `${fallbackMessage}: ${suffix}` : fallbackMessage;
        }
        return fallbackMessage;
      } catch (_jsonParseError) {
        return trimmedText || fallbackMessage;
      }
    }
    return trimmedText || fallbackMessage;
  } catch (_textReadError) {
    return fallbackMessage;
  }
}

async function agentApi(path, options = {}) {
  if (!state.selectedAgentId) throw new Error("No selected assistant");
  return api(`/a/${state.selectedAgentId}${path}`, options);
}

async function agentApiFor(agentId, path, options = {}) {
  if (!agentId) throw new Error("No selected assistant");
  return api(`/a/${agentId}${path}`, options);
}

function defaultWelcomeMessage() {
  const welcomeAgentName = getSelectedAssistantDisplayName();
  return `<div class="message-row message-row-assistant" data-welcome="1"><div class="message-meta"><span class="message-author">${escapeHtml(welcomeAgentName)}</span><span class="message-timestamp">Ready</span></div><article class="message-surface message-surface-assistant assistant-message"><div class="message-markdown md-render max-w-none text-sm" data-md="👋 Welcome! Ask me anything."></div></article></div>`;
}

function clearMessageListToWelcome() {
  if (dom.messageList) dom.messageList.innerHTML = defaultWelcomeMessage();
  renderMarkdown(dom.messageList);
  decorateToolMessages(dom.messageList);
  scrollToBottom();
}


function removeWelcomeMessageIfPresent() {
  if (!dom.messageList) return;
  const welcome = dom.messageList.querySelector('[data-welcome="1"]');
  if (!welcome) return;

  const onlyWelcome = dom.messageList.children.length === 1;
  if (onlyWelcome) welcome.remove();
}

function setChatSubmittingForAgent(agentId, active, options = {}) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.isSubmitting = !!active;
  if (options.suppressSync === true) return;
  if (agentId !== state.selectedAgentId) return;
  if (typeof syncSelectedAgentChatActionControls === "function") {
    syncSelectedAgentChatActionControls();
    return;
  }
  if (dom.sendChatBtn) dom.sendChatBtn.disabled = !!chatState.isSubmitting;
}

function setChatSubmitting(active) {
  setChatSubmittingForAgent(state.selectedAgentId, active);
}

function currentSessionIdForSelectedAgent() {
  return currentSessionIdForAgent(state.selectedAgentId);
}

function syncHiddenSessionInputFromState() {
  // Re-query the element each time since OOB swap replaces the DOM element
  const hiddenInput = document.getElementById("chat-session-id");
  if (hiddenInput) hiddenInput.value = currentSessionIdForSelectedAgent();
}

function updateAgentSession(agentId, sessionId) {
  if (!agentId) return;
  const chatState = ensureChatState(agentId);
  const value = (sessionId || "").trim();
  if (value) {
    state.agentSessionIds.set(agentId, value);
    if (chatState) chatState.sessionId = value;
    setLastSessionId(agentId, value);
  } else {
    state.agentSessionIds.delete(agentId);
    if (chatState) chatState.sessionId = "";
    setLastSessionId(agentId, null);
  }
  if (agentId === state.selectedAgentId) {
    syncHiddenSessionInputFromState();
    ensureEventSocketForSelectedAgent();
  }
}

function updateSelectedAgentSession(sessionId) {
  updateAgentSession(state.selectedAgentId, sessionId);
}

function persistComposerForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.draftText = dom.chatInput?.value || "";
}

function restoreComposerForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  if (dom.chatInput) dom.chatInput.value = chatState.draftText || "";
  const attachmentsInput = document.getElementById("chat-attachments");
  if (attachmentsInput) attachmentsInput.value = "";
  syncChatInputHeight();
  renderInputPreview();
  renderComposerModelSelectorForAgent(agentId);
  if (agentId === state.selectedAgentId) syncSelectedAgentChatActionControls();
}

function markAgentUnread(agentId, status) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.unreadCount += 1;
}

function clearAgentUnread(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.unreadCount = 0;
}

// Helper to update owner-only button visibility
function updateOwnerOnlyButtons(agentId) {
  const agent = state.mineAgents?.find(a => a.id === agentId);
  const isOwner = canWriteAgent(agent);
  document.querySelectorAll('[data-owner-only]').forEach(btn => {
    btn.style.display = isOwner ? '' : 'none';
  });
}

// ===== selected agent state sync =====
function renderAgentList() {
  if (!dom.mineList) return;

  dom.mineList.innerHTML = "";
  if (!state.mineAgents.length) {
    dom.mineList.innerHTML = '<div class="portal-empty-note">No assistants</div>';
    return;
  }

  const mine = state.mineAgents.filter((agent) => Number(agent.owner_user_id) === state.currentUserId && agent.visibility !== "public");
  const shared = state.mineAgents.filter((agent) => Number(agent.owner_user_id) !== state.currentUserId && agent.visibility !== "public");
  const publicAgents = state.mineAgents.filter((agent) => agent.visibility === "public");

  const renderSection = (title, agents, { showTitle = true } = {}) => {
    if (!agents.length) return;
    const section = document.createElement("section");
    section.className = "portal-agent-section";
    if (showTitle && title) {
      const heading = document.createElement("div");
      heading.className = "portal-eyebrow";
      heading.textContent = title;
      section.append(heading);
    }

    agents.forEach((agent) => {
      const status = (state.agentStatus.get(agent.id)?.status || agent.status || "stopped").toLowerCase();
      const chatState = ensureChatState(agent.id);
      const isActive = state.selectedAgentId === agent.id;
      const row = document.createElement("button");
      row.type = "button";
      row.className = `portal-agent-row${isActive ? " is-active" : ""}`;
      const sharedBadge = Number(agent.owner_user_id) === state.currentUserId ? "" : '<span class="portal-agent-shared">shared</span>';
      const unreadBadge = chatState?.unreadCount ? `<span class="portal-agent-unread">${chatState.unreadCount}</span>` : "";
      let runtimeBadge = "";
      if (hasActiveChatRequestForAgent(agent.id)) runtimeBadge = '<span class="portal-agent-chat-badge is-running">running</span>';
      const runtimeType = String(agent.runtime_type || "native").trim().toLowerCase() || "native";
      const runtimeTypeBadge = `<span class="portal-agent-chat-badge">${safe(runtimeType)}</span>`;
      row.innerHTML = `
        <div class="portal-agent-row-head">
          <span class="portal-agent-name">${safe(agent.name)}</span>
          <span class="portal-agent-row-badges">${runtimeTypeBadge}${runtimeBadge}${unreadBadge}${sharedBadge}</span>
        </div>
        <div class="portal-agent-row-foot">
          <span class="portal-agent-status-dot status-${safe(status)}" aria-hidden="true"></span>
          <span class="portal-agent-status-text">${safe(status)}</span>
        </div>
      `;
      row.addEventListener("click", () => selectAgentById(agent.id));
      section.append(row);
    });

    dom.mineList.append(section);
  };

  renderSection("My Space", mine, { showTitle: false });
  renderSection("Shared", shared);
  renderSection("Public", publicAgents);
}

function renderAgentMeta(agent) {
  if (!dom.agentMeta) return;

  // Format date nicely
  const created = new Date(agent.created_at);
  const dateStr = created.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

  // Format resources as pills
  const cpu = agent.cpu || 'N/A';
  const mem = agent.memory || 'N/A';
  const disk = agent.disk_size_gi;

  const effectiveSkillRepoUrl = agent.effective_skill_repo_url || agent.skill_repo_url || state.agentDefaults?.default_skill_repo_url || "";
  const effectiveSkillBranch = agent.effective_skill_branch || agent.skill_branch || state.agentDefaults?.default_skill_branch || "";
  const isDefaultSkillRepo = !agent.skill_repo_url && !!effectiveSkillRepoUrl;
  const runtimeType = String(agent.runtime_type || "native").trim().toLowerCase() || "native";

  // Build Skills Repository section if present.
  // Tool repo/branch configuration was intentionally removed from Portal agent flows in #318;
  // do not reintroduce tool repo/branch UI or provisioning here.
  let repoSection = "";
  if (effectiveSkillRepoUrl) {
    const branchLine = effectiveSkillBranch
      ? `
        <div class="portal-detail-subtle">Branch: ${safe(effectiveSkillBranch)}</div>
      `
      : "";
    const defaultIndicator = isDefaultSkillRepo
      ? `
        <div class="portal-detail-subtle">Using configured default</div>
      `
      : "";
    repoSection = `
      <div class="portal-detail-row">
        <div class="portal-detail-label">Skills Repository</div>
        <div class="portal-detail-value"><code>${safe(effectiveSkillRepoUrl)}</code></div>
        ${branchLine}
        ${defaultIndicator}
        <div id="agent-skill-git-commit" class="portal-detail-subtle">Loading skill commit...</div>
      </div>
    `;
  }

  dom.agentMeta.innerHTML = `
    <div class="portal-detail-stack">
      <div class="portal-detail-section">
        <div class="portal-detail-label">Assistant ID</div>
        <code class="portal-detail-code">${safe(agent.id)}</code>
      </div>
      <div class="portal-detail-section">
        <div class="portal-detail-label">Runtime Type</div>
        <div class="portal-detail-value">${safe(runtimeType)}</div>
      </div>
      <div class="portal-detail-section">
        <div class="portal-detail-label">Image</div>
        <code class="portal-detail-code">${safe(agent.image)}</code>
      </div>
      ${repoSection}
      <div class="portal-detail-section">
        <div class="portal-detail-label">Created</div>
        <div class="portal-detail-value">${dateStr}</div>
      </div>
      <div class="portal-detail-section">
        <div class="portal-detail-label">Resources</div>
        <div class="portal-resource-pills">
          <span class="portal-resource-pill is-cpu">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
            ${cpu}
          </span>
          <span class="portal-resource-pill is-memory">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
            ${mem}
          </span>
          <span class="portal-resource-pill is-disk">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path></svg>
            ${disk}Gi
          </span>
        </div>
      </div>
      <div id="agent-usage" class="portal-detail-subtle">Loading usage...</div>
    </div>
  `;

  // Fetch usage data
  fetchUsageForAgent(agent.id);
  
  // Fetch and render system prompt config
  renderSystemPromptSection(agent);

  // Fetch git info if repo is configured
  if (effectiveSkillRepoUrl) {
    fetchSkillGitInfo(agent.id);
  }
}

async function fetchSkillGitInfo(agentId) {
  const commitEl = document.getElementById("agent-skill-git-commit");
  if (!commitEl) return;

  // Check if still viewing same agent (prevent stale response overwriting wrong agent)
  if (state.selectedAgentId !== agentId) return;

  try {
    const data = await api(`/a/${agentId}/api/skill-git-info`);
    if (data.commit_id) {
      const shortCommit = data.commit_id.substring(0, 7);
      commitEl.textContent = 'Skill commit: ';

      // Validate URL to prevent XSS
      let safeUrl = null;
      if (data.repo_url) {
        try {
          const parsed = new URL(data.repo_url);
          if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
            safeUrl = data.repo_url.replace(/\.git$/, "");
          }
        } catch (e) {
          // Invalid URL, use plain text
        }
      }

      if (safeUrl) {
        const commitLink = document.createElement('a');
        commitLink.href = `${safeUrl}/commit/${data.commit_id}`;
        commitLink.target = '_blank';
        commitLink.rel = 'noopener noreferrer';
        commitLink.className = 'portal-link-inline';
        commitLink.textContent = shortCommit;
        commitEl.appendChild(commitLink);
      } else {
        const commitText = document.createElement('span');
        commitText.className = 'portal-detail-value';
        commitText.textContent = shortCommit;
        commitEl.appendChild(commitText);
      }
    } else if (data.status === 'running') {
      commitEl.className = "portal-detail-subtle";
      commitEl.textContent = "Skill commit: unavailable";
    } else if (data.status === 'error') {
      commitEl.className = "portal-detail-subtle";
      commitEl.textContent = "Skill commit: unavailable";
    } else {
      commitEl.className = "portal-detail-subtle";
      commitEl.textContent = "Skill commit: unavailable";
    }
  } catch (e) {
    commitEl.className = "portal-detail-subtle";
    commitEl.textContent = "Skill commit: unavailable";
  }
}

async function fetchUsageForAgent(agentId) {
  const usageEl = document.getElementById("agent-usage");
  if (!usageEl) return;
  try {
    const data = await api(`/a/${agentId}/api/usage`);
    if (!data) {
      usageEl.className = "portal-detail-subtle";
      usageEl.textContent = "No usage data";
      return;
    }
    const global = data.global || {};
    const reqCount = global.request_count || 0;
    const cost = global.total_cost_usd || global.total_cost || 0;
    const inputTokens = global.total_input_tokens || global.total_input || 0;
    const outputTokens = global.total_output_tokens || global.total_output || 0;
    usageEl.innerHTML = `
      <div class="portal-detail-label">Usage (30 days)</div>
      <div class="portal-usage-grid">
        <div class="portal-usage-card">
          <span class="portal-usage-k">Requests</span>
          <strong class="portal-usage-v">${reqCount}</strong>
        </div>
        <div class="portal-usage-card">
          <span class="portal-usage-k">Cost</span>
          <strong class="portal-usage-v">$${cost.toFixed(4)}</strong>
        </div>
        <div class="portal-usage-card">
          <span class="portal-usage-k">Input</span>
          <strong class="portal-usage-v">${inputTokens.toLocaleString()}</strong>
        </div>
        <div class="portal-usage-card">
          <span class="portal-usage-k">Output</span>
          <strong class="portal-usage-v">${outputTokens.toLocaleString()}</strong>
        </div>
      </div>
    `;
  } catch (e) {
    usageEl.className = "portal-inline-error";
    usageEl.textContent = "No usage data";
  }
}

function renderAgentActions(agent, status) {
  if (!dom.agentActions) return;

  dom.agentActions.innerHTML = "";
  const writable = canWriteAgent(agent);

  const container = document.createElement("div");
  container.className = "portal-detail-action-grid";

  const buildIconBtn = ({ label, icon, variantClass, onClick, disabled = false }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.title = label;
    button.className = `portal-detail-action-btn ${variantClass}`;
    button.innerHTML = `
      <i data-lucide="${icon}" class="w-4 h-4"></i>
      <span class="text-[10px] font-medium">${label}</span>
    `;
    button.disabled = disabled;
    button.setAttribute("aria-disabled", disabled ? "true" : "false");
    button.addEventListener("click", onClick);
    return button;
  };

  const actions = [
    { label: "Start", icon: "play", variantClass: "is-success", disabled: !writable || !(status === "stopped" || status === "failed"), onClick: () => action(`/api/agents/${agent.id}/start`) },
    { label: "Stop", icon: "square", variantClass: "is-warning", disabled: !writable || status !== "running", onClick: () => action(`/api/agents/${agent.id}/stop`) },
    { label: "Restart", icon: "rotate-cw", variantClass: "is-info", disabled: !writable || status !== "running", onClick: () => action(`/api/agents/${agent.id}/restart`) },
    { label: "Edit", icon: "pencil", variantClass: "is-neutral", disabled: !writable, onClick: () => openEditDialog(agent) },
    { label: agent.visibility === "public" ? "Unshare" : "Share", icon: agent.visibility === "public" ? "lock" : "share-2", variantClass: "is-info", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`) },
    { label: "Delete", icon: "trash-2", variantClass: "is-danger", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/delete-runtime`, "DELETE", true) },
    { label: "Destroy", icon: "flame", variantClass: "is-danger", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/destroy`, "POST", true) },
  ];
  actions.forEach((cfg) => container.append(buildIconBtn(cfg)));

  if (!writable) {
    const note = document.createElement("div");
    note.className = "portal-detail-note";
    note.textContent = "Read-only assistant.";
    container.append(note);
  }

  dom.agentActions.append(container);
  renderIcons();
}

async function selectAgentById(agentId, { updateRoute = true } = {}) {
  const previousAgentId = state.selectedAgentId;
  if (previousAgentId) persistComposerForAgent(previousAgentId);
  state.selectedAgentId = agentId;
  const allAgents = state.mineAgents || [];
  const selectedAgent = allAgents.find(a => a.id === agentId);
  state.selectedAgentName = selectedAgent?.name || null;
  updateChatInputPlaceholder();
  resetChatInputHeight();
  closeSessionsDrawer();

  updateOwnerOnlyButtons(agentId);

  window.selectedAgentId = agentId;
  if (agentId) localStorage.setItem(LAST_AGENT_STORAGE_KEY, agentId);
  state.cachedSkills = state.cachedSkillsByAgent.get(agentId) || [];
  state.selectedSuggestionIndex = -1;
  disconnectEventSocket();

  if (dom.chatAgentId) dom.chatAgentId.value = agentId || "";
  syncHiddenSessionInputFromState();
  restoreComposerForAgent(agentId);
  clearAgentUnread(agentId);
  clearMessageListToWelcome();

  await setActiveNavSection("assistants", { toggleIfSame: false, updateRoute: false });
  renderAgentList();
  await syncSelectedAgentState();
  if (updateRoute && !isApplyingPortalRoute) {
    commitPortalRoute({ section: "assistants", agentId });
  }
}

async function syncSelectedAgentState() {
  const agent = getSelectedAgent();
  const sessionsBtn = document.getElementById("btn-sessions");

  if (!agent) {
    dom.embedTitle.textContent = "Select an assistant";
    dom.selectedStatus.textContent = "idle";
    setChatStatus("Ready");
    setButtonDisabled(dom.headerNewChatBtn, true, "Select an assistant first");
    setButtonDisabled(sessionsBtn, true, "Select an assistant first");
    setButtonDisabled(dom.homeStartChatBtn, true, "Select an assistant first");
    dom.homeTitle && (dom.homeTitle.textContent = "Select an assistant");
    dom.homeSubtitle && (dom.homeSubtitle.textContent = "Choose an assistant from the left to start chatting, inspect tasks, or browse bundles.");
    dom.homeAgentSummary && (dom.homeAgentSummary.textContent = "No assistant selected.");
    setMainView("home");
    state.selectedAgentName = null;
    updateChatInputPlaceholder();
    syncMainHeader();
    if (dom.chatModelSelect) dom.chatModelSelect.innerHTML = "";
    dom.chatModelWrap?.classList.add("hidden");
    return;
  }

  const status = getSelectedAgentStatus();
  state.selectedAgentName = agent.name || null;
  updateChatInputPlaceholder();
  dom.embedTitle.textContent = agent.name;
  dom.selectedStatus.textContent = status;
  dom.selectedStatus.className = `toolbar-status-badge status-${status}`;
  setChatStatus("Ready");
  syncSelectedAgentChatActionControls();
  dom.homeTitle && (dom.homeTitle.textContent = `${agent.name}`);
  dom.homeSubtitle && (dom.homeSubtitle.textContent = "Choose an assistant from the left to start chatting, inspect tasks, or browse bundles.");
  if (dom.homeAgentSummary) {
    if (status !== "running") dom.homeAgentSummary.textContent = `${agent.name} is ${status}. Start it to open chat.`;
    else dom.homeAgentSummary.textContent = `${agent.name} is running. Open a session or start a new chat.`;
  }

  if (dom.chatAgentId) dom.chatAgentId.value = agent.id;
  syncHiddenSessionInputFromState();
  await refreshComposerModelProfile(agent.id);

  renderAgentMeta(agent);
  renderAgentActions(agent, status);

  const running = status === "running";
  setMainView(running ? "chat" : "home");
  syncMainHeader();

  if (running) {
    const chatState = ensureChatState(agent.id);
    if (chatState?.needsReload && chatState.sessionId) {
      await loadSessionForAgent(agent.id, chatState.sessionId, { render: true });
      ensureEventSocketForSelectedAgent();
      return;
    }
    const lastSessionId = getLastSessionId(agent.id);
    if (lastSessionId) {
      try {
        await loadSession(lastSessionId);
      } catch (e) {
        await loadLastSessionFromRemote(agent.id);
      }
    } else {
      await loadLastSessionFromRemote(agent.id);
    }
  }
}

// Load last session from remote agent runtime
async function loadLastSessionFromRemote(agentId) {
  try {
    const data = await agentApiFor(agentId, "/api/sessions?limit=1");
    const sessions = data.sessions || [];
    if (sessions.length > 0) {
      // Use session_id (not id) for session objects
      const lastSessionId = sessions[0].session_id;
      if (lastSessionId) {
        setLastSessionId(agentId, lastSessionId);
        await loadSessionForAgent(agentId, lastSessionId, { render: agentId === state.selectedAgentId });
      }
    }
  } catch (e) {
  }
}

async function refreshAll({ preserveLayout = false, skipRouteApply = false } = {}) {
  const [mine, publicAgents] = await Promise.all([
    api("/api/agents/mine"),
    api("/api/agents/public"),
  ]);

  const allById = new Map();
  [...mine, ...publicAgents].forEach((agent) => allById.set(agent.id, agent));
  state.mineAgents = Array.from(allById.values());

  const pairs = await Promise.all(state.mineAgents.map(async (agent) => {
    try {
      return [agent.id, await api(`/api/agents/${agent.id}/status`)];
    } catch {
      return [agent.id, { status: agent.status }];
    }
  }));

  state.agentStatus = new Map(pairs);

  const route = parsePortalHashRoute(window.location.hash);
  const available = new Set(state.mineAgents.map((agent) => agent.id));
  const stored = localStorage.getItem(LAST_AGENT_STORAGE_KEY) || "";
  if (route.valid && route.section === "assistants" && route.agentId) {
    if (available.has(route.agentId)) {
      state.selectedAgentId = route.agentId;
    } else {
      state.selectedAgentId = null;
    }
  } else {
    if (state.selectedAgentId && !available.has(state.selectedAgentId)) state.selectedAgentId = null;
    if (!state.selectedAgentId && stored && available.has(stored)) state.selectedAgentId = stored;
    if (!state.selectedAgentId && state.mineAgents.length) state.selectedAgentId = state.mineAgents[0].id;
  }
  if (state.selectedAgentId) {
    localStorage.setItem(LAST_AGENT_STORAGE_KEY, state.selectedAgentId);
    window.selectedAgentId = state.selectedAgentId;
  } else {
    window.selectedAgentId = "";
  }

  // Update owner-only button visibility after restoring last agent
  updateOwnerOnlyButtons(state.selectedAgentId);

  renderAgentList();
  await syncSelectedAgentState();

  if (!skipRouteApply) {
    await applyPortalRouteFromHash({ replaceInvalid: true });
  }
}

// ===== chat submit lifecycle =====

function maybeRequestNotificationPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

function notifyAgentCompletion(agentId, agentName, status, summary = "") {
  const text = `${agentName || agentId}: ${status}${summary ? ` - ${summary}` : ""}`;
  if (document.hidden && "Notification" in window && Notification.permission === "granted") {
    try {
      new Notification(text);
      return;
    } catch {}
  }
  showToast(text);
}

function formatAttachmentMetaText(attachment) {
  if (!attachment || typeof attachment !== "object" || Array.isArray(attachment)) return "";
  const contentType = String(attachment.content_type || attachment.contentType || "").trim();
  const rawSize = attachment.size;
  let sizeText = "";
  if (typeof rawSize === "number" && Number.isFinite(rawSize) && rawSize >= 0) {
    if (rawSize >= 1024) {
      sizeText = `${Math.max(1, Math.round(rawSize / 102.4) / 10)} KB`;
    } else {
      sizeText = `${Math.round(rawSize)} B`;
    }
  }
  return [contentType, sizeText].filter(Boolean).join(" · ");
}

function buildAttachmentsFromChatState(agentId, chatState) {
  // Runtime consumes uploaded attachment metadata here and resolves file_id server-side.
  // Do not include previewUrl/blob/base64/browser File objects in the chat payload.
  const uploadedAttachments = chatState.pendingFiles
    .filter((pf) => pf.file_id && pf.status === "uploaded")
    .map((pf) => {
      const fileId = String(pf.file_id);
      const rawName =
        pf.uploadedData?.name ||
        pf.uploadedData?.filename ||
        pf.file?.name ||
        pf.name ||
        fileId;
      const name = String(rawName || fileId);
      const rawContentType =
        pf.uploadedData?.content_type ??
        pf.uploadedData?.mime ??
        pf.file?.type ??
        pf.content_type ??
        "";
      const contentType = String(rawContentType || "");
      const rawSize = pf.uploadedData?.size ?? pf.file?.size ?? pf.size;
      const size = typeof rawSize === "number" && Number.isFinite(rawSize) ? rawSize : null;
      const parsed =
        (pf.parseData && pf.parseData.success !== false) ||
        pf.uploadedData?.parsed === true;
      const parseError =
        pf.parseError ||
        pf.parseData?.error ||
        pf.uploadedData?.parse_error ||
        "";
      return {
        file_id: fileId,
        id: fileId,
        name,
        filename: name,
        content_type: contentType,
        mime: contentType,
        size,
        type: pf.isImage === true || contentType.toLowerCase().startsWith("image/") ? "image" : "file",
        parsed: !!parsed,
        parse_error: parseError,
      };
    });
  return uploadedAttachments;
}

async function submitChatForSelectedAgent() {
  const agentIdAtSend = state.selectedAgentId;
  const chatState = ensureChatState(agentIdAtSend);
  if (!agentIdAtSend || !chatState) return;
  if (!guardNoActiveChatRequestForAgent(agentIdAtSend, "send another message")) return;
  const localNormalizeAssistantMessageIds = (typeof normalizeAssistantMessageIds === "function")
    ? normalizeAssistantMessageIds
    : (candidate = {}) => {
      const rawIds = Array.isArray(candidate?.assistant_message_ids) ? candidate.assistant_message_ids : [];
      const ids = rawIds.map((id) => String(id || "")).filter(Boolean);
      const primary = String(candidate?.assistant_message_id || ids[ids.length - 1] || "");
      if (primary && !ids.includes(primary)) ids.push(primary);
      return ids;
    };
  const localPrimaryAssistantMessageId = (typeof getPrimaryAssistantMessageId === "function")
    ? getPrimaryAssistantMessageId
    : (candidate = {}) => {
      const ids = localNormalizeAssistantMessageIds(candidate);
      return String(candidate?.assistant_message_id || ids[ids.length - 1] || "");
    };
  const localStartWaitingForRuntimeEventsTimer = (typeof startWaitingForRuntimeEventsTimer === "function")
    ? startWaitingForRuntimeEventsTimer
    : () => {};
  const localClearWaitingForRuntimeEventsTimer = (typeof clearWaitingForRuntimeEventsTimer === "function")
    ? clearWaitingForRuntimeEventsTimer
    : () => {};
  const uploadingFiles = chatState.pendingFiles.filter((pf) => pf.status === "uploading");
  const parsingFiles = chatState.pendingFiles.filter((pf) => pf.status === "parsing");
  if (uploadingFiles.length) {
    showToast(`Waiting for ${uploadingFiles.length} file(s) to upload...`);
    return;
  }
  if (parsingFiles.length) {
    showToast(`Waiting for ${parsingFiles.length} file(s) to finish processing...`);
    return;
  }
  const rawMessageAtSend = dom.chatInput?.value || "";
  const messageAtSend = rawMessageAtSend.trim();
  const attachmentsAtSend = buildAttachmentsFromChatState(agentIdAtSend, chatState);
  if (!messageAtSend && attachmentsAtSend.length === 0) return;
  const requestMessage = messageAtSend || "[attachment]";
  const displayMessage = messageAtSend || "📎 Attachment";
  const sessionIdAtSend = ensureChatSessionId(agentIdAtSend);

  const clientRequestId = (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function")
    ? globalThis.crypto.randomUUID()
    : `req_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const requestCtx = {
    requestId: clientRequestId,
    agentId: agentIdAtSend,
    sessionIdAtSend,
    message: requestMessage,
    attachments: attachmentsAtSend,
    clientRequestId,
    startedAt: Date.now(),
    streamStartedAt: Date.now(),
    sawRuntimeEvent: false,
    sawDelta: false,
    sawFinal: false,
    streamCompleted: false,
    streamFailed: false,
    streamIncomplete: false,
    backupMessage: messageAtSend,
    backupPendingFiles: chatState.pendingFiles.slice(),
    typewriter: { targetText: "", visibleText: "", timerId: null, finalizing: false, cancelled: false },
    usedStream: false,
  };

  maybeRequestNotificationPermission();
  const modelOverride = (chatState.modelOverride || dom.chatModelSelect?.value || "").trim();
  const defaultModel = (chatState.profileDefaultModel || "").trim();
  const requestBody = {
    message: requestMessage,
    session_id: sessionIdAtSend || undefined,
    attachments: attachmentsAtSend,
    request_id: clientRequestId,
    ...(modelOverride && modelOverride !== defaultModel ? { model_override: modelOverride } : {}),
  };
  const slashInvocation = parseSkillSlashInput(messageAtSend);
  const matchedSkill = findCachedSkillForSlash(slashInvocation, agentIdAtSend);
  if (slashInvocation && matchedSkill && matchedSkill.callable === false) {
    showToast(matchedSkill.blocked_reason || "This skill is not callable in the current runtime/profile.");
    setChatStatus(matchedSkill.blocked_reason || "This skill is not callable in the current runtime/profile.", true);
    chatState.currentRequest = null;
    cancelAssistantTypewriter(requestCtx);
  setChatSubmittingForAgent(agentIdAtSend, false);
    return;
  }
  if (slashInvocation) {
    const existingMetadata = requestBody.metadata;
    requestBody.metadata = {
      ...(existingMetadata || {}),
      slash_command: matchedSkill ? {
        type: "skill",
        raw_name: slashInvocation.rawName,
        name: matchedSkill.name || matchedSkill.opencode_name || slashInvocation.name,
        opencode_name: matchedSkill.opencode_name || matchedSkill.name || slashInvocation.name,
        efp_name: matchedSkill.efp_name || "",
        arguments: slashInvocation.arguments,
        source: "portal-chat-ui",
      } : {
        type: "unknown",
        raw_name: slashInvocation.rawName,
        name: slashInvocation.name,
        arguments: slashInvocation.arguments,
        source: "portal-chat-ui",
      },
    };
  }
  removeWelcomeMessageIfPresent();
  removeTemporaryAssistantRows({ requestId: clientRequestId, onlyEmpty: true });
  hideSuggest();
  if (agentIdAtSend === state.selectedAgentId && dom.messageList) {
    const displayAttachments = chatState.pendingFiles.map((pf) => ({
      name: pf.file?.name || pf.name || "",
      type: pf.isImage ? "image" : "file",
      previewUrl: pf.previewUrl,
      url: pf.uploadedData?.url,
    }));
    dom.messageList.insertAdjacentHTML(
      "beforeend",
      buildUserMessageArticle(displayMessage, displayAttachments, { clientRequestId })
    );
    dom.messageList.insertAdjacentHTML("beforeend", buildPendingAssistantArticle(clientRequestId));
    chatState.inflightThinking = {
      id: clientRequestId,
      requestId: clientRequestId,
      sessionId: sessionIdAtSend || "",
      events: [],
      completed: false,
      started: false,
      contextState: null,
      contextBudget: null,
      startedAt: Date.now(),
    };
    ensureEventSocketForAgent(agentIdAtSend, sessionIdAtSend, clientRequestId);
    if (isThinkingPanelActiveForAgent(agentIdAtSend)) {
      renderThinkingPanelFromClientState(chatState);
    }
    scrollToBottom();
  }
  chatState.pendingFiles = [];
  renderInputPreview();
  if (dom.chatInput) dom.chatInput.value = "";
  resetChatInputHeight();
  const attachmentsInput = document.getElementById("chat-attachments");
  if (attachmentsInput) attachmentsInput.value = "";
  setChatStatus("Sending...");
  chatState.currentRequest = requestCtx;
  setChatSubmittingForAgent(agentIdAtSend, true);
  localStartWaitingForRuntimeEventsTimer(agentIdAtSend, requestCtx);

  try {
    const streamResult = await trySubmitChatStreamForSelectedAgent(agentIdAtSend, requestCtx, requestBody);
    if (streamResult === "handled") return;
    const resp = await fetch(`/a/${agentIdAtSend}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    if (!resp.ok) {
      throw new Error(await handleErrorResponse(resp));
    }
    const payload = await resp.json();
    const responseText = finalResponseText(payload);
    if (payload?.ok === false || isNonSuccessFinalPayload(payload)) {
      if (typeof handleIncompleteChatStream === "function") {
        await handleIncompleteChatStream(agentIdAtSend, requestCtx, "runtime_error_or_incomplete", payload);
      } else if (typeof finalizeNonSuccessChatResponse === "function") {
        finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, payload, "fallback");
      }
      return;
    }
    if (!isCompletedFinalPayload(payload) || !responseText) {
      await handleIncompleteChatStream(agentIdAtSend, requestCtx, "runtime_incomplete", payload);
      return;
    }
    await handleAgentChatSuccess(agentIdAtSend, requestCtx, {
      ...payload,
      response: responseText,
      session_id: payload?.session_id || requestCtx.sessionIdAtSend || "",
      request_id: payload?.request_id || requestCtx.clientRequestId,
      events: payload?.events || [],
      runtime_events: payload?.runtime_events || [],
    });
  } catch (error) {
    handleAgentChatFailure(agentIdAtSend, requestCtx, error);
  } finally {
    localClearWaitingForRuntimeEventsTimer(requestCtx);
  }
}


function parseSseEventsFromChunk(buffer, chunkText) {
  const merged = `${String(buffer || "")}${String(chunkText || "")}`.replace(/\r\n/g, "\n");
  const events = [];
  let remaining = merged;
  let splitIndex = remaining.indexOf("\n\n");
  while (splitIndex >= 0) {
    const parsed = parseSseEvent(remaining.slice(0, splitIndex));
    if (parsed) events.push(parsed);
    remaining = remaining.slice(splitIndex + 2);
    splitIndex = remaining.indexOf("\n\n");
  }
  return { events, buffer: remaining };
}

function parseSseEvent(rawEvent) {
  const lines = String(rawEvent || '').replace(/\r\n/g, '\n').split('\n');
  let eventName = 'message';
  let hasExplicitEvent = false;
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('event:')) { hasExplicitEvent = true; eventName = line.slice(6).trim() || 'message'; }
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length && !hasExplicitEvent) return null;
  const rawData = dataLines.join('\n');
  let data = rawData;
  try { data = JSON.parse(rawData); } catch {}
  return { eventName: eventName || 'message', data };
}
function normalizeChatStreamEventName(name) {
  return String(name || "").trim().toLowerCase();
}
function isChatStreamWrapperEventName(name) {
  const normalized = normalizeChatStreamEventName(name);
  return ["progress", "runtime_event", "event"].includes(normalized);
}
function isChatStreamFinalEventName(name) {
  return normalizeChatStreamEventName(name) === "final";
}
function isDirectCompletionEventName(name) {
  return ["message.completed", "execution.completed", "complete"].includes(normalizeChatStreamEventName(name));
}
function isChatStreamDeltaEventName(name) {
  return ["delta", "message.delta"].includes(normalizeChatStreamEventName(name));
}
function getChatStreamEventType(eventName, data) {
  const explicitEventName = normalizeChatStreamEventName(eventName);
  const dataType = data && typeof data === "object" ? normalizeChatStreamEventName(data.type || data.event_type || data.event || "") : "";

  if (
    (!explicitEventName || explicitEventName === "message")
    && (!dataType || dataType === "message")
    && isChatStreamDeltaPayload(data)
  ) {
    return "message.delta";
  }

  if (!explicitEventName || explicitEventName === "message") return dataType || explicitEventName || "message";
  return explicitEventName;
}
function isChatStreamDeltaPayload(data) {
  return Boolean(
    data
    && typeof data === "object"
    && (
      Object.prototype.hasOwnProperty.call(data, "delta")
      || Object.prototype.hasOwnProperty.call(data, "response_delta")
    )
  );
}
function getChatStreamTextPayload(data) {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return "";
  return data.response || data.content || data.text || data.delta || data.response_delta || "";
}

function getChatStreamRoleMarker(eventData) {
  return String(
    eventData?.role
    || eventData?.message_role
    || eventData?.messageRole
    || eventData?.source_role
    || eventData?.sourceRole
    || eventData?.author_role
    || eventData?.authorRole
    || ""
  ).trim().toLowerCase();
}
function getChatStreamRawType(eventData) {
  return String(eventData?.raw_type || eventData?.rawType || "").trim().toLowerCase();
}
function isChatStreamSnapshotPayload(eventData) {
  return Boolean(
    eventData?.snapshot === true
    || eventData?.is_snapshot === true
    || eventData?.isSnapshot === true
  );
}
function rememberAssociatedRuntimeDeltaEvent(requestCtx, eventData, embeddedType) {
  const eventType = normalizeChatStreamEventName(
    embeddedType
    || eventData?.type
    || eventData?.event_type
    || eventData?.event
    || ""
  );
  const deltaText = getChatStreamTextPayload(eventData);
  const rawType = getChatStreamRawType(eventData);
  const role = getChatStreamRoleMarker(eventData);

  if (
    eventType === "message.delta"
    || eventType === "assistant_delta"
    || rawType === "message.part.updated"
    || rawType === "message.part.delta"
    || deltaText
  ) {
    requestCtx.lastRuntimeDeltaEvent = {
      ...eventData,
      observedAt: Date.now(),
      deltaText: deltaText || "",
      raw_type: eventData?.raw_type || eventData?.rawType || rawType || "",
      message_role: eventData?.message_role || eventData?.messageRole || role || "",
    };
  }
}
function getAssociatedRuntimeDeltaEvent(requestCtx, deltaText) {
  const candidate = requestCtx?.lastRuntimeDeltaEvent;
  if (!candidate || typeof candidate !== "object") return null;
  const observedAt = Number(candidate.observedAt || 0);
  if (observedAt && Date.now() - observedAt > 3000) return null;

  const candidateText = String(
    candidate.deltaText
    || candidate.delta
    || candidate.response_delta
    || candidate.message
    || candidate.text
    || candidate.content
    || ""
  );

  if (candidateText && String(deltaText || "") && candidateText !== String(deltaText || "")) {
    return null;
  }
  return candidate;
}
function isSyntheticFinalDeltaEvent(eventData = {}, associatedEvent = null) {
  const associatedData = associatedEvent?.data && typeof associatedEvent.data === "object"
    ? associatedEvent.data
    : {};
  return Boolean(
    eventData?.synthetic_final_delta === true
    || eventData?.syntheticFinalDelta === true
    || associatedEvent?.synthetic_final_delta === true
    || associatedEvent?.syntheticFinalDelta === true
    || associatedData?.synthetic_final_delta === true
    || associatedData?.syntheticFinalDelta === true
  );
}
function isLikelySyntheticFinalPreviewDelta(eventData = {}, requestCtx = {}, associatedEvent = null) {
  if (isSyntheticFinalDeltaEvent(eventData, associatedEvent)) return true;

  const deltaText = getChatStreamTextPayload(eventData);
  if (!deltaText) return false;

  const hasSyntheticPreview = Boolean(requestCtx?.syntheticFinalDeltaPreview);
  const awaitingFinal = Boolean(
    requestCtx?.awaitingAuthoritativeFinal
    || requestCtx?.sawAssistantMessageCompleted
    || requestCtx?.sawExecutionCompleted
  );

  if (!hasSyntheticPreview || !awaitingFinal) return false;

  const observedAt = Number(requestCtx.syntheticFinalDeltaPreview?.observedAt || 0);
  if (observedAt && Date.now() - observedAt > 5000) return false;

  const rawType = String(eventData?.raw_type || eventData?.rawType || "").trim();
  const role = String(eventData?.message_role || eventData?.messageRole || "").trim();
  const partType = String(eventData?.part_type || eventData?.partType || "").trim();
  const messageId = String(eventData?.message_id || eventData?.messageId || "").trim();
  const partId = String(eventData?.part_id || eventData?.partId || "").trim();

  const lacksCanonicalMarkers = !rawType && !role && !partType && !messageId && !partId;

  if (!lacksCanonicalMarkers) return false;

  const existingPreview = String(requestCtx.syntheticFinalDeltaPreview?.response || "");
  const containsEllipsis = deltaText.includes("…") || existingPreview.includes("…");

  return containsEllipsis || deltaText.length >= 80;
}
function buildAssistantStreamDeltaGuardSource(eventData, associatedEvent = null) {
  const hasAssociated = associatedEvent && typeof associatedEvent === "object";
  if (!hasAssociated) return eventData || {};

  const source = {
    ...associatedEvent,
    ...eventData,
  };

  const currentRole = getChatStreamRoleMarker(eventData);
  const associatedRole = getChatStreamRoleMarker(associatedEvent);
  const currentRawType = getChatStreamRawType(eventData);
  const associatedRawType = getChatStreamRawType(associatedEvent);

  source.message_role = currentRole || associatedRole || "";
  source.raw_type = currentRawType || associatedRawType || "";

  const currentOrigin = String(eventData?.source || eventData?.origin || "").trim().toLowerCase();
  const associatedOrigin = String(associatedEvent?.source || associatedEvent?.origin || "").trim().toLowerCase();
  if (!currentOrigin && associatedOrigin) {
    source.source = associatedOrigin;
  }

  if (isChatStreamSnapshotPayload(eventData) || isChatStreamSnapshotPayload(associatedEvent)) {
    source.snapshot = true;
  }

  return source;
}
function shouldIgnoreAssistantStreamDelta(eventData, requestCtx, associatedEvent = null) {
  const source = buildAssistantStreamDeltaGuardSource(eventData, associatedEvent);

  const role = getChatStreamRoleMarker(source);
  if (role === "user") return true;

  const rawType = getChatStreamRawType(source);
  if (rawType === "message.part.updated" && !getChatStreamTextPayload(source)) return true;

  const origin = String(source?.source || source?.origin || "").trim().toLowerCase();
  if (origin === "user" || origin === "client_user") return true;

  if (isChatStreamSnapshotPayload(source)) {
    return true;
  }

  return false;
}
function normalizeChatStreamEventData(data) {
  if (!data || typeof data !== "object") return { message: String(data || "") };
  const normalized = { ...data };
  if (normalized.data && typeof normalized.data === "object") Object.assign(normalized, normalized.data);
  delete normalized.data;
  return normalized;
}
function hasChatStreamFinalPayload(data, streamedText = "") {
  const eventData = normalizeChatStreamEventData(data);
  if (getChatStreamTextPayload(eventData)) return true;
  if (String(streamedText || "").trim()) return true;
  if (Array.isArray(eventData.display_blocks) && eventData.display_blocks.length) return true;
  return false;
}

function getCompletionState(payload) {
  const state = String(payload?.completion_state || payload?.completionState || "").trim().toLowerCase();
  return state || "";
}

function isCompletedFinalPayload(payload) {
  const state = getCompletionState(payload);
  if (state) return state === "completed" || state === "success";
  return typeof payload?.response === "string" && payload.response.length > 0;
}

function isNonSuccessFinalPayload(payload) {
  const state = getCompletionState(payload);
  return ["blocked", "error", "failed", "incomplete", "pending", "empty_final"].includes(state) || payload?.ok === false;
}

function finalResponseText(payload) {
  if (typeof payload?.response === "string") return payload.response;
  if (typeof payload?.message === "string") return payload.message;
  if (typeof payload?.text === "string") return payload.text;
  return "";
}



function getAssistantTypewriterState(requestCtx) {
  if (!requestCtx) return null;
  if (!requestCtx.typewriter || typeof requestCtx.typewriter !== "object") {
    requestCtx.typewriter = { targetText: "", visibleText: "", timerId: null, finalizing: false, cancelled: false };
  }
  return requestCtx.typewriter;
}

function cancelAssistantTypewriter(requestCtx) {
  const tw = getAssistantTypewriterState(requestCtx);
  if (!tw) return;
  tw.cancelled = true;
  if (tw.timerId) {
    clearInterval(tw.timerId);
    tw.timerId = null;
  }
}

function queueAssistantTypewriter(agentId, requestCtx, targetText) {
  const tw = getAssistantTypewriterState(requestCtx);
  if (!tw || tw.cancelled) return;
  tw.targetText = String(targetText || "");
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    tw.visibleText = tw.targetText;
    updatePendingAssistantStreamContent(agentId, tw.visibleText, { streaming: true, requestCtx });
    return;
  }
  if (tw.timerId) return;
  tw.timerId = setInterval(() => {
    if (tw.cancelled) {
      clearInterval(tw.timerId);
      tw.timerId = null;
      return;
    }
    const remaining = Math.max(0, tw.targetText.length - tw.visibleText.length);
    if (!remaining) {
      if (!tw.finalizing) {
        clearInterval(tw.timerId);
        tw.timerId = null;
      }
      return;
    }
    const step = Math.max(1, Math.min(8, Math.ceil(remaining / 24)));
    tw.visibleText = tw.targetText.slice(0, tw.visibleText.length + step);
    updatePendingAssistantStreamContent(agentId, tw.visibleText, { streaming: true, requestCtx });
  }, 24);
}

async function flushAssistantTypewriter(agentId, requestCtx, finalText, { maxWaitMs = 1200 } = {}) {
  const tw = getAssistantTypewriterState(requestCtx);
  if (!tw) return;
  tw.finalizing = true;
  queueAssistantTypewriter(agentId, requestCtx, finalText || "");
  const deadline = Date.now() + maxWaitMs;
  while (!tw.cancelled && tw.visibleText.length < tw.targetText.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  tw.visibleText = tw.targetText;
  updatePendingAssistantStreamContent(agentId, tw.visibleText, { streaming: false, requestCtx });
  if (tw.timerId) {
    clearInterval(tw.timerId);
    tw.timerId = null;
  }
  tw.finalizing = false;
}
function normalizeAssistantMessageIds(payload = {}) {
  const rawIds = Array.isArray(payload?.assistant_message_ids) ? payload.assistant_message_ids : [];
  const ids = rawIds.map((id) => String(id || "")).filter(Boolean);
  const primary = String(payload?.assistant_message_id || payload?.message_id || payload?.id || payload?.assistant_projection?.assistant_message_id || payload?.final_payload?.assistant_message_id || ids[ids.length - 1] || "");
  if (primary && !ids.includes(primary)) ids.push(primary);
  return ids;
}

function getPrimaryAssistantMessageId(payload = {}) {
  const ids = normalizeAssistantMessageIds(payload);
  return String(payload?.assistant_message_id || payload?.message_id || payload?.id || payload?.assistant_projection?.assistant_message_id || payload?.final_payload?.assistant_message_id || ids[ids.length - 1] || "");
}

function hasRenderableAssistantPayload(payload) {
  const text = String(payload?.response || "").trim();
  if (text) return true;
  const blocks = Array.isArray(payload?.display_blocks) ? payload.display_blocks : [];
  return blocks.some((block) => hasRenderableDisplayBlock(block));
}

function extractAssistantVisibleText(payload = {}) {
  if (!payload || typeof payload !== "object") return "";
  const role = getChatStreamRoleMarker(payload);
  if (role === "user") return "";
  const candidates = [
    payload.response,
    payload.text,
    payload.content,
    payload.message,
    payload.delta,
    payload.response_delta,
    payload.last_response_text,
    payload.assistant_text,
    payload.assistant_message,
    payload.assistant_projection?.text,
    payload.assistant_projection?.response,
    payload.assistant_projection?.content,
    payload.assistant_projection?.message,
    payload.final_payload?.response,
    payload.final_payload?.text,
    payload.final_payload?.message,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.length) return value;
  }
  const nestedMessage = payload.message && typeof payload.message === "object" ? payload.message : null;
  if (nestedMessage && getChatStreamRoleMarker(nestedMessage) !== "user") {
    return extractAssistantVisibleText(nestedMessage);
  }
  return "";
}

function extractAssistantDisplayBlocks(payload = {}) {
  const candidates = [
    payload?.display_blocks,
    payload?.displayBlocks,
    payload?.assistant_projection?.display_blocks,
    payload?.assistant_projection?.displayBlocks,
    payload?.final_payload?.display_blocks,
    payload?.final_payload?.displayBlocks,
  ];
  for (const blocks of candidates) {
    if (Array.isArray(blocks)) return blocks;
  }
  return [];
}

function getRequestIdCandidatesForAssistantRow(requestCtx = {}, payload = {}) {
  return [
    requestCtx.clientRequestId,
    requestCtx.requestId,
    payload.client_request_id,
    payload.clientRequestId,
    payload.request_id,
    payload.requestId,
    payload.run_id,
    payload.runId,
    payload.assistant_projection?.request_id,
    payload.final_payload?.request_id,
  ].map((value) => String(value || "")).filter(Boolean);
}

function getAssistantMessageIdCandidates(payload = {}) {
  return [
    payload.assistant_message_id,
    payload.assistantMessageId,
    payload.message_id,
    payload.messageId,
    payload.id,
    payload.assistant_projection?.assistant_message_id,
    payload.assistant_projection?.message_id,
    payload.final_payload?.assistant_message_id,
  ].map((value) => String(value || "")).filter(Boolean);
}

function articleContainsAssistantMessageId(article, messageId) {
  if (!article || !messageId) return false;
  if (article.dataset.messageId === messageId || article.dataset.primaryMessageId === messageId) return true;
  try {
    const ids = JSON.parse(article.dataset.messageIds || "[]");
    return Array.isArray(ids) && ids.map((id) => String(id || "")).includes(messageId);
  } catch {
    return String(article.dataset.messageIds || "").includes(messageId);
  }
}

function isAssistantArticle(article) {
  return Boolean(
    article
    && (
      article.classList?.contains?.("assistant-message")
      || article.dataset?.pendingAssistant === "1"
    )
  );
}

function findAssistantArticleForRequest(requestCtx = {}, payload = {}) {
  if (!dom.messageList) return null;
  const requestIds = getRequestIdCandidatesForAssistantRow(requestCtx, payload);
  for (const requestId of requestIds) {
    const escaped = CSS.escape(requestId);
    const byClient = dom.messageList.querySelector(
      [
        `article.assistant-message[data-client-request-id="${escaped}"]`,
        `article[data-pending-assistant="1"][data-client-request-id="${escaped}"]`,
      ].join(",")
    );
    if (isAssistantArticle(byClient)) return byClient;
    const byRequest = dom.messageList.querySelector(
      [
        `article.assistant-message[data-request-id="${escaped}"]`,
        `article[data-pending-assistant="1"][data-request-id="${escaped}"]`,
      ].join(",")
    );
    if (isAssistantArticle(byRequest)) return byRequest;
  }
  const messageIds = getAssistantMessageIdCandidates(payload);
  for (const messageId of messageIds) {
    const escaped = CSS.escape(messageId);
    const byMessageId = dom.messageList.querySelector(
      [
        `article.assistant-message[data-message-id="${escaped}"]`,
        `article.assistant-message[data-primary-message-id="${escaped}"]`,
        `article[data-pending-assistant="1"][data-message-id="${escaped}"]`,
        `article[data-pending-assistant="1"][data-primary-message-id="${escaped}"]`,
      ].join(",")
    );
    if (isAssistantArticle(byMessageId)) return byMessageId;
    const containing = Array.from(
      dom.messageList.querySelectorAll("article.assistant-message[data-message-ids], article[data-pending-assistant='1'][data-message-ids]")
    )
      .find((article) => articleContainsAssistantMessageId(article, messageId));
    if (isAssistantArticle(containing)) return containing;
  }
  const selectedChatState = ensureChatState(state.selectedAgentId);
  if (requestCtx?.clientRequestId && selectedChatState?.currentRequest?.clientRequestId === requestCtx.clientRequestId) {
    const pending = dom.messageList.querySelector(
      [
        `article.assistant-message[data-pending-assistant="1"][data-client-request-id="${CSS.escape(requestCtx.clientRequestId)}"]`,
        `article[data-pending-assistant="1"][data-client-request-id="${CSS.escape(requestCtx.clientRequestId)}"]`,
      ].join(",")
    );
    if (isAssistantArticle(pending)) return pending;
  }
  return null;
}

function findUserRowForAssistantRequest(requestCtx = {}, payload = {}) {
  if (!dom.messageList) return null;
  const userIds = [
    payload.user_message_id,
    payload.userMessageId,
    payload.assistant_projection?.user_message_id,
    payload.final_payload?.user_message_id,
    requestCtx.userMessageId,
  ].map((value) => String(value || "")).filter(Boolean);
  for (const userId of userIds) {
    const article = dom.messageList.querySelector(`article[data-local-user="1"][data-message-id="${CSS.escape(userId)}"]`);
    if (article) return article.closest(".message-row");
  }
  const optimistic = getLatestOptimisticUserArticle();
  return optimistic?.closest(".message-row") || null;
}

function updateOrCreateAssistantRowForRequest(agentId, requestCtx, payload, options = {}) {
  if (state.selectedAgentId !== agentId || !dom.messageList || !requestCtx) return null;
  const text = String(options.text ?? extractAssistantVisibleText(payload) ?? "");
  const displayBlocks = Array.isArray(options.displayBlocks) ? options.displayBlocks : extractAssistantDisplayBlocks(payload);
  const hasVisible = text.trim() || displayBlocks.some((block) => (typeof hasRenderableDisplayBlock === "function")
    ? hasRenderableDisplayBlock(block)
    : !!(block && typeof block === "object" && String(block.text || block.content || block.value || "").trim()));
  let article = findAssistantArticleForRequest(requestCtx, payload);
  if (article && !isAssistantArticle(article)) {
    article = null;
  }
  if (!article) {
    const requestId = payload?.request_id || requestCtx.requestId || requestCtx.clientRequestId || "";
    const clientRequestId = requestCtx.clientRequestId || payload?.client_request_id || "";
    const messageIds = normalizeAssistantMessageIds(payload);
    const primaryMessageId = getPrimaryAssistantMessageId(payload);
    const assistantHtml = buildAssistantMessageArticle(
      text,
      displayBlocks,
      getSelectedAssistantDisplayName(payload?.author_name || "Assistant"),
      primaryMessageId,
      {
        messageIds,
        primaryMessageId,
        requestId,
        clientRequestId,
        userMessageId: payload?.user_message_id || requestCtx.userMessageId || "",
        isStreaming: options.partial === true && options.completed !== true,
        copyText: text,
      },
    );
    const userRow = findUserRowForAssistantRequest(requestCtx, payload);
    if (userRow) userRow.insertAdjacentHTML("afterend", assistantHtml);
    else dom.messageList.insertAdjacentHTML("beforeend", assistantHtml);
    article = findAssistantArticleForRequest(requestCtx, payload)
      || dom.messageList.querySelector("article.assistant-message:last-of-type")
      || Array.from(dom.messageList.querySelectorAll("article.assistant-message")).pop();
    if (article && options.partial === true && options.completed !== true) {
      article.dataset.pendingAssistant = "1";
      article.classList.add("is-pending", "pending-assistant", "is-streaming");
      const row = article.closest(".message-row");
      if (row) {
        row.dataset.temporaryAssistant = "1";
        row.classList.add("is-streaming");
      }
    }
  }
  if (!article) return null;

  const row = article.closest(".message-row");
  const markdownEl = article.querySelector(".message-markdown") || (() => {
    const created = document.createElement("div");
    created.className = "message-markdown md-render max-w-none text-sm";
    article.appendChild(created);
    return created;
  })();
  if (hasVisible) {
    article.querySelector(".assistant-waiting-indicator")?.remove();
    article.dataset.hasVisibleContent = "1";
    if (row) row.dataset.hasVisibleContent = "1";
  }
  markdownEl.className = "message-markdown md-render max-w-none text-sm";
  markdownEl.dataset.md = text;
  markdownEl.dataset.displayBlocks = JSON.stringify(displayBlocks || []);
  article.dataset.copyText = text;

  const messageIds = normalizeAssistantMessageIds(payload);
  const primaryMessageId = getPrimaryAssistantMessageId(payload);
  if (primaryMessageId) {
    article.dataset.messageId = primaryMessageId;
    article.dataset.primaryMessageId = primaryMessageId;
  }
  if (messageIds.length) article.dataset.messageIds = JSON.stringify(messageIds);
  const requestId = payload?.request_id || requestCtx.requestId || requestCtx.clientRequestId || "";
  if (requestId) article.dataset.requestId = requestId;
  if (requestCtx.clientRequestId) article.dataset.clientRequestId = requestCtx.clientRequestId;
  if (payload?.user_message_id || requestCtx.userMessageId) {
    article.dataset.userMessageId = payload?.user_message_id || requestCtx.userMessageId;
  }

  if (options.completed === true) {
    article.removeAttribute("data-pending-assistant");
    article.dataset.pendingAssistant = "0";
    article.classList.remove("is-streaming", "is-pending", "pending-assistant");
    article.classList.add("is-complete");
    article.querySelector(".assistant-stream-cursor")?.remove();
    article.querySelector(".assistant-waiting-indicator")?.remove();
    row?.classList.remove("is-streaming", "is-pending");
    row?.removeAttribute("data-temporary-assistant");
  } else if (options.partial === true) {
    article.dataset.pendingAssistant = "1";
    article.classList.add("is-streaming");
    row?.classList.add("is-streaming");
    let cursor = article.querySelector(".assistant-stream-cursor");
    if (!cursor) {
      cursor = document.createElement("span");
      cursor.className = "assistant-stream-cursor";
      cursor.setAttribute("aria-hidden", "true");
      cursor.textContent = "▌";
      markdownEl.insertAdjacentElement("afterend", cursor);
    }
  }

  renderMarkdown(article);
  decorateToolMessages(article);
  renderIcons();
  addEditButtonsToMessages();
  scrollToBottom();
  return article;
}

function updatePendingAssistantStreamContent(agentId, markdownText, options = {}) {
  if (state.selectedAgentId !== agentId || !dom.messageList) return;
  const reqId = options?.requestCtx?.clientRequestId || "";
  const article = (reqId
    ? dom.messageList.querySelector(`article[data-pending-assistant="1"][data-client-request-id="${CSS.escape(reqId)}"]`)
    : null) || dom.messageList.querySelector('article[data-pending-assistant="1"]');
  if (!article) return;
  const row = article.closest('.message-row');
  const waiting = article.querySelector('.assistant-waiting-indicator');
  if (waiting) waiting.remove();
  article.classList.add('is-streaming');
  row?.classList.add('is-streaming');
  if (String(markdownText || "").trim()) {
    article.dataset.hasVisibleContent = "1";
    if (row) row.dataset.hasVisibleContent = "1";
  }
  const markdownEl = article.querySelector('.message-markdown') || (() => {
    const created = document.createElement('div');
    created.className = 'message-markdown md-render max-w-none text-sm';
    article.appendChild(created);
    return created;
  })();
  markdownEl.dataset.md = String(markdownText || '');
  if (!markdownEl.dataset.displayBlocks) markdownEl.dataset.displayBlocks = '[]';
  let cursor = article.querySelector('.assistant-stream-cursor');
  if (options.streaming !== false) {
    if (!cursor) {
      cursor = document.createElement('span');
      cursor.className = 'assistant-stream-cursor';
      cursor.setAttribute('aria-hidden', 'true');
      cursor.textContent = '▌';
      markdownEl.insertAdjacentElement('afterend', cursor);
    }
  } else if (cursor) {
    cursor.remove();
  }
  renderMarkdown(article); decorateToolMessages(article); renderIcons(); scrollToBottom();
}

function finalizePendingAssistantRow(agentId, requestCtx, payload) {
  if (state.selectedAgentId !== agentId || !dom.messageList) return false;
  const reqId = requestCtx?.clientRequestId || '';
  const article = (reqId
    ? dom.messageList.querySelector(`article[data-pending-assistant="1"][data-client-request-id="${CSS.escape(reqId)}"]`)
    : null) || dom.messageList.querySelector('article[data-pending-assistant="1"]');
  if (!article) return false;
  const row = article.closest('.message-row');
  const messageIds = normalizeAssistantMessageIds(payload);
  const primary = getPrimaryAssistantMessageId(payload);
  const nearestUser = row ? findPrecedingUserArticle(row)?.dataset?.messageId || '' : '';
  article.removeAttribute('data-pending-assistant');
  article.dataset.messageId = primary;
  article.dataset.primaryMessageId = primary;
  article.dataset.messageIds = JSON.stringify(messageIds);
  article.dataset.userMessageId = payload?.user_message_id || nearestUser || '';
  article.dataset.requestId = payload?.request_id || requestCtx?.clientRequestId || '';
  article.dataset.clientRequestId = requestCtx?.clientRequestId || requestCtx?.requestId || reqId;
  article.dataset.hasVisibleContent = "1";
  if (row) {
    row.dataset.hasVisibleContent = "1";
    row.removeAttribute("data-temporary-assistant");
  }
  article.classList.remove('is-pending', 'is-streaming');
  row?.classList.remove('is-streaming');
  article.classList.add('is-complete');
  const md = article.querySelector('.message-markdown') || article.appendChild(document.createElement('div'));
  md.className = 'message-markdown md-render max-w-none text-sm';
  md.dataset.md = String(payload?.response || '');
  md.dataset.displayBlocks = JSON.stringify(payload?.display_blocks || []);
  article.querySelector('.assistant-stream-cursor')?.remove();
  article.querySelector('.assistant-waiting-indicator')?.remove();
  renderMarkdown(article); decorateToolMessages(article); renderIcons();
  return true;
}
function renderCompletionStateWarning(payload = {}) {
  const completionState = escapeHtml(String(payload?.completion_state || payload?.completionState || "unknown"));
  const incompleteReason = escapeHtml(String(payload?.incomplete_reason || payload?.incompleteReason || ""));
  const progressPreview = escapeHtml(String(
    payload?.progress_preview
    || payload?._llm_debug?.completion_probe?.diagnostics?.progress_preview
    || ""
  ));
  return `<div class="chat-completion-warning"><strong>completion_state:</strong> ${completionState}`
    + `${incompleteReason ? `<div><strong>incomplete_reason:</strong> ${incompleteReason}</div>` : ""}`
    + `${progressPreview ? `<div><strong>progress_preview:</strong> ${progressPreview}</div>` : ""}</div>`;
}
function renderCompletionDiagnosticFields(finalPayload = {}) {
  const contextState = finalPayload?.context_state && typeof finalPayload.context_state === "object"
    ? finalPayload.context_state
    : {};
  const contextSummary = [contextState.summary, contextState.current_state, contextState.next_step]
    .map((v) => String(v || "").trim()).filter(Boolean).join(" • ");
  const progressPreview = String(
    finalPayload?.progress_preview
    || finalPayload?._llm_debug?.completion_probe?.diagnostics?.progress_preview
    || ""
  );
  const fields = [
    ["completion_state", finalPayload?.completion_state || finalPayload?.completionState || "unknown"],
    ["incomplete_reason", finalPayload?.incomplete_reason || finalPayload?.incompleteReason || ""],
    ["progress_preview", progressPreview],
    ["context_state", contextSummary],
  ];
  return fields
    .filter(([, value]) => String(value ?? "").trim())
    .map(([label, value]) => `<div><strong>${escapeHtml(label)}:</strong> ${escapeHtml(String(value))}</div>`)
    .join("");
}
function finalizeIncompleteAssistantRow(agentId, requestCtx, finalPayload = {}) {
  if (state.selectedAgentId !== agentId || !dom.messageList) return false;
  const reqId = requestCtx?.clientRequestId || requestCtx?.requestId || "";
  const article = (reqId
    ? dom.messageList.querySelector(`article[data-pending-assistant="1"][data-client-request-id="${CSS.escape(reqId)}"]`)
    : null) || dom.messageList.querySelector('article[data-pending-assistant="1"]');
  if (!article) return false;
  const responseText = String(finalPayload?.response || "").trim();
  article.removeAttribute("data-pending-assistant");
  article.dataset.finalizedIncomplete = "1";
  article.dataset.clientRequestId = requestCtx?.requestId || requestCtx?.clientRequestId || reqId;
  article.dataset.pendingAssistant = "0";
  article.classList.remove("is-pending", "is-streaming");
  article.classList.add("is-incomplete");
  const markdownEl = article.querySelector(".message-markdown") || article.appendChild(document.createElement("div"));
  markdownEl.className = "message-markdown max-w-none text-sm";
  markdownEl.innerHTML = "";
  const warningBlock = document.createElement("div");
  warningBlock.className = "chat-completion-warning-block";
  warningBlock.innerHTML = renderCompletionDiagnosticFields(finalPayload);
  markdownEl.appendChild(warningBlock);
  const responseEl = document.createElement("div");
  responseEl.className = "chat-incomplete-response md-render";
  responseEl.dataset.md = responseText || "No final assistant response was returned. See Thinking Process for runtime events.";
  responseEl.dataset.displayBlocks = "[]";
  markdownEl.appendChild(responseEl);
  article.querySelector('.assistant-stream-cursor')?.remove();
  article.querySelector('.assistant-waiting-indicator')?.remove();
  renderMarkdown(responseEl.parentElement); decorateToolMessages(article); renderIcons();
  return true;
}
function mergeFinalThinkingSnapshot(agentId, requestCtx, finalPayload = {}) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  const completionState = getCompletionState(finalPayload) || (finalPayload?.ok === false ? "error" : "");
  const status = ["blocked", "incomplete", "error", "failed", "empty_final"].includes(completionState)
    ? (completionState === "failed" ? "error" : completionState)
    : "completed";
  const existing = chatState.lastThinkingSnapshot || chatState.inflightThinking || { events: [] };
  const finalPayloadEvents = [
    ...normalizePayloadThinkingEvents(finalPayload?.events || []),
    ...normalizePayloadThinkingEvents(finalPayload?.runtime_events || []),
  ];
  const mergedEvents = mergeThinkingEvents(existing.events || [], finalPayloadEvents).slice(-100);
  const finalContextState =
    finalPayload?.context_state ||
    finalPayload?.contextState ||
    existing.contextState ||
    existing.context_state ||
    null;
  chatState.lastThinkingSnapshot = {
    ...existing,
    completed: true,
    status,
    completion_state: completionState || "completed",
    incomplete_reason: finalPayload?.incomplete_reason || "",
    contextState: finalContextState,
    context_state: finalContextState,
    requestId: finalPayload?.request_id || requestCtx?.requestId || requestCtx?.clientRequestId || "",
    sessionId: finalPayload?.session_id || requestCtx?.sessionIdAtSend || "",
    latestEventAt: Date.now(),
    event_count: mergedEvents.length,
    events: mergedEvents,
  };
  if (isThinkingPanelActiveForAgent(agentId)) renderThinkingPanelFromClientState(chatState);
}
function terminalStatusFromCompletionState(completionState) {
  if (completionState === "failed") return "error";
  if (["blocked", "incomplete", "error", "empty_final"].includes(completionState)) return completionState;
  return completionState || "completed";
}
function finalizeTerminalThinkingState(agentId, requestCtx, finalPayload = {}) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  const requestId = finalPayload?.request_id || requestCtx?.requestId || requestCtx?.clientRequestId || "";
  const sessionId = finalPayload?.session_id || requestCtx?.sessionIdAtSend || chatState.sessionId || "";
  const completionState = getCompletionState(finalPayload) || (finalPayload?.ok === false ? "error" : "");
  const status = terminalStatusFromCompletionState(completionState);
  if (chatState.inflightThinking && (!requestId || !chatState.inflightThinking.requestId || chatState.inflightThinking.requestId === requestId || chatState.inflightThinking.id === requestId)) {
    chatState.inflightThinking.completed = true;
    chatState.inflightThinking.status = status;
    chatState.inflightThinking.completion_state = completionState;
    chatState.inflightThinking.incomplete_reason = finalPayload?.incomplete_reason || "";
  }
  const existing = chatState.lastThinkingSnapshot || chatState.inflightThinking || { events: [] };
  chatState.lastThinkingSnapshot = {
    ...existing,
    completed: true,
    status,
    completion_state: completionState,
    incomplete_reason: finalPayload?.incomplete_reason || "",
    context_state: finalPayload?.context_state || existing.context_state || null,
    requestId,
    sessionId,
    completedAt: Date.now(),
  };
  chatState.inflightThinking = null;
  chatState.pendingThinkingEvents = null;
  if (chatState.currentRequest?.clientRequestId === requestCtx?.clientRequestId) chatState.currentRequest = null;
  chatState.isSubmitting = false;
  clearWaitingForRuntimeEventsTimer(requestCtx);
  if (isThinkingPanelActiveForAgent(agentId)) renderThinkingPanelFromClientState(chatState);
  syncSelectedAgentChatActionControls();
}
function setTerminalCompletionStatus(finalPayload = {}) {
  const completionState = getCompletionState(finalPayload) || (finalPayload?.ok === false ? "error" : "unknown");
  const reason = String(finalPayload?.incomplete_reason || finalPayload?.error || finalPayload?.detail || "").trim();
  const suffix = reason ? `: ${reason}` : "";
  if (completionState === "blocked") setChatStatus(`Blocked${suffix}`, true);
  else if (completionState === "incomplete") setChatStatus(`Incomplete${suffix}`, true);
  else if (completionState === "error" || completionState === "failed") setChatStatus(`Error${suffix}`, true);
  else if (completionState === "empty_final") setChatStatus("Empty final response", true);
  else setChatStatus(`Finished with non-success state: ${completionState}`, true);
}
function finalizeNonSuccessChatResponse(agentId, requestCtx, finalPayload = {}, source = "final") {
  const failureSources = new Set(["error", "stream_error", "runtime_error"]);
  const completionState = getCompletionState(finalPayload);
  if (failureSources.has(source) || completionState === "error" || completionState === "failed") {
    requestCtx.streamFailed = true;
  } else {
    requestCtx.streamIncomplete = true;
  }
  requestCtx.terminalPayload = finalPayload;
  finalizeIncompleteAssistantRow(agentId, requestCtx, finalPayload);
  mergeFinalThinkingSnapshot(agentId, requestCtx, finalPayload);
  finalizeTerminalThinkingState(agentId, requestCtx, finalPayload);
  if (state.selectedAgentId === agentId) setTerminalCompletionStatus(finalPayload);
  cleanupChatStreamRequest(agentId, requestCtx, { keepStatus: true });
}
function cleanupChatStreamRequest(agentIdAtSend, requestCtx, { keepStatus = false } = {}) {
  const chatState = ensureChatState(agentIdAtSend);
  clearWaitingForRuntimeEventsTimer(requestCtx);
  if (requestCtx?.streamIncomplete || requestCtx?.streamFailed) {
    finalizeTerminalThinkingState(agentIdAtSend, requestCtx, requestCtx?.terminalPayload || requestCtx?.streamFinalPayload || {});
  }
  setChatSubmittingForAgent(agentIdAtSend, false);
  if (chatState?.currentRequest?.clientRequestId === requestCtx?.clientRequestId) chatState.currentRequest = null;
  if (!keepStatus && state.selectedAgentId === agentIdAtSend && !requestCtx?.streamIncomplete && !requestCtx?.streamFailed) setChatStatus("Ready");
}
function startWaitingForRuntimeEventsTimer(agentIdAtSend, requestCtx) {
  clearWaitingForRuntimeEventsTimer(requestCtx);
  requestCtx.waitingEventTimerId = setTimeout(() => {
    const chatState = ensureChatState(agentIdAtSend);
    if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestCtx.clientRequestId) return;
    if (requestCtx.sawRuntimeEvent || requestCtx.waitingEventEmitted) return;
    requestCtx.waitingEventEmitted = true;
    const waitingEvent = {
      type: "portal.waiting_for_runtime_events",
      event_type: "portal.waiting_for_runtime_events",
      session_id: requestCtx.sessionIdAtSend || "",
      request_id: requestCtx.clientRequestId,
      created_at: new Date().toISOString(),
      data: { message: "Portal is connected and waiting for runtime events." },
    };
    handleAgentEventMessage(JSON.stringify(waitingEvent), { agentId: agentIdAtSend, sessionId: requestCtx.sessionIdAtSend || "", requestId: requestCtx.clientRequestId });
  }, 15000);
}
function clearWaitingForRuntimeEventsTimer(requestCtx) {
  if (requestCtx?.waitingEventTimerId) {
    clearTimeout(requestCtx.waitingEventTimerId);
    requestCtx.waitingEventTimerId = null;
  }
}

async function abortActiveChatRequestForSelectedAgent() {
  const agentId = state.selectedAgentId;
  const chatState = ensureChatState(agentId);
  const req = chatState?.currentRequest;
  if (!agentId || !chatState || !req) return;

  clearWaitingForRuntimeEventsTimer(req);
  cancelAssistantTypewriter(req);
  if (req.abortController && typeof req.abortController.abort === "function") {
    req.abortController.abort();
  }
  req.aborted = true;
  req.streamFailed = true;
  chatState.currentRequest = null;
  chatState.inflightThinking = null;
  chatState.pendingThinkingEvents = null;
  setChatSubmittingForAgent(agentId, false);
  setChatStatus("Stopped current response.");
  showToast("Stopped current response.");
  syncSelectedAgentChatActionControls();
}

function normalizeChatRunStatus(status) {
  return String(status || "").trim().toLowerCase();
}

async function handleChatStreamEvent(agentIdAtSend, requestCtx, eventName, data) {
  const outerType = normalizeChatStreamEventName(eventName);
  const eventData = normalizeChatStreamEventData(data);
  const embeddedType = normalizeChatStreamEventName(eventData.type || eventData.event_type || eventData?.data?.type || eventData?.data?.event_type || eventData.event || "");
  const localGetCompletionState = (typeof getCompletionState === "function")
    ? getCompletionState
    : (payload) => String(payload?.completion_state || payload?.completionState || "").trim().toLowerCase();
  const localIsCompletedFinalPayload = (typeof isCompletedFinalPayload === "function")
    ? isCompletedFinalPayload
    : (payload) => {
      const state = localGetCompletionState(payload);
      if (state) return state === "completed" || state === "success";
      return typeof payload?.response === "string" && payload.response.length > 0;
    };
  const localIsNonSuccessFinalPayload = (typeof isNonSuccessFinalPayload === "function")
    ? isNonSuccessFinalPayload
    : (payload) => {
      const state = localGetCompletionState(payload);
      return ["blocked", "error", "failed", "incomplete", "pending", "empty_final"].includes(state) || payload?.ok === false;
    };
  const localFinalResponseText = (typeof finalResponseText === "function")
    ? finalResponseText
    : (payload) => {
      if (typeof payload?.response === "string") return payload.response;
      if (typeof payload?.message === "string") return payload.message;
      if (typeof payload?.text === "string") return payload.text;
      return "";
    };
  const localHandleIncompleteChatStream = (typeof handleIncompleteChatStream === "function")
    ? handleIncompleteChatStream
    : async () => {};
  const localFinalizeNonSuccessChatResponse = (typeof finalizeNonSuccessChatResponse === "function")
    ? finalizeNonSuccessChatResponse
    : (agentId, ctx, payload, source) => localHandleIncompleteChatStream(agentId, ctx, source || "non_success", payload);
  const localClearWaitingForRuntimeEventsTimer = (typeof clearWaitingForRuntimeEventsTimer === "function")
    ? clearWaitingForRuntimeEventsTimer
    : () => {};
  const localIsSyntheticFinalDeltaEvent = (typeof isSyntheticFinalDeltaEvent === "function")
    ? isSyntheticFinalDeltaEvent
    : () => false;
  const localNormalizeAssistantMessageIds = (typeof normalizeAssistantMessageIds === "function")
    ? normalizeAssistantMessageIds
    : (payload = {}) => {
      const rawIds = Array.isArray(payload?.assistant_message_ids) ? payload.assistant_message_ids : [];
      const ids = rawIds.map((id) => String(id || "")).filter(Boolean);
      const primary = String(payload?.assistant_message_id || ids[ids.length - 1] || "");
      if (primary && !ids.includes(primary)) ids.push(primary);
      return ids;
    };
  const responseText = getChatStreamTextPayload(eventData) || requestCtx.streamedText || "";

  if (isChatStreamWrapperEventName(outerType)) {
    requestCtx.sawRuntimeEvent = true;
    localClearWaitingForRuntimeEventsTimer(requestCtx);
    const injectedRequestId = eventData.request_id || requestCtx.requestId || requestCtx.clientRequestId || "";
    const injectedSessionId = eventData.session_id || requestCtx.sessionIdAtSend || "";
    const streamEventPayload = {
      type: embeddedType || "runtime_event",
      event_type: embeddedType || "runtime_event",
      stream_event: outerType || "runtime_event",
      request_id: injectedRequestId,
      session_id: injectedSessionId,
      agent_id: eventData.agent_id || agentIdAtSend,
      data: {
        ...eventData,
        stream_event: outerType || "runtime_event",
        request_id: injectedRequestId,
        session_id: injectedSessionId,
        agent_id: eventData.agent_id || agentIdAtSend,
      },
    };
    handleAgentEventMessage(JSON.stringify(streamEventPayload), {agentId: agentIdAtSend, sessionId: requestCtx.sessionIdAtSend || eventData.session_id || "", requestId: requestCtx.clientRequestId});
    rememberAssociatedRuntimeDeltaEvent(requestCtx, eventData, embeddedType);
    const wrapperDeltaText = getChatStreamTextPayload(eventData);
    if (localIsSyntheticFinalDeltaEvent(eventData, null)) {
      requestCtx.awaitingAuthoritativeFinal = true;
      requestCtx.syntheticFinalDeltaPreview = {
        response: wrapperDeltaText || requestCtx.streamedText || "",
        request_id: eventData.request_id || requestCtx.clientRequestId,
        session_id: eventData.session_id || requestCtx.sessionIdAtSend || "",
        observedAt: Date.now(),
      };
    }
    if (["complete", "execution.completed"].includes(embeddedType)) {
      requestCtx.sawExecutionCompleted = true;
      requestCtx.awaitingAuthoritativeFinal = true;
    }
    if (isDirectCompletionEventName(embeddedType)) {
      const candidateText = getChatStreamTextPayload(eventData);
      if (candidateText && !requestCtx.streamFinalCandidate) {
        requestCtx.streamFinalCandidate = {
          response: candidateText,
          display_blocks: eventData?.display_blocks || [],
          session_id: eventData?.session_id || requestCtx.sessionIdAtSend || "",
          user_message_id: eventData?.user_message_id || "",
          assistant_message_id: eventData?.assistant_message_id || "",
          request_id: eventData?.request_id || requestCtx.clientRequestId,
          assistant_message_ids: localNormalizeAssistantMessageIds(eventData),
          events: eventData?.events || [],
          runtime_events: eventData?.runtime_events || [],
        };
      }
      return "candidate_final";
    }
    return "event";
  }

  if (isChatStreamDeltaEventName(outerType)) {
    requestCtx.sawDelta = true;
    const deltaText = getChatStreamTextPayload(eventData);
    const associatedEvent = getAssociatedRuntimeDeltaEvent(requestCtx, deltaText);
    const isSyntheticPreviewDelta = (typeof isLikelySyntheticFinalPreviewDelta === "function")
      ? isLikelySyntheticFinalPreviewDelta(eventData, requestCtx, associatedEvent)
      : localIsSyntheticFinalDeltaEvent(eventData, associatedEvent);

    if (isSyntheticPreviewDelta) {
      const existingPreview = String(
        requestCtx.syntheticFinalDeltaPreview?.response
        || requestCtx.streamedText
        || ""
      );
      const nextPreview = String(deltaText || "");
      const previewText = nextPreview.length > existingPreview.length
        ? nextPreview
        : existingPreview;

      requestCtx.streamedText = previewText;
      requestCtx.awaitingAuthoritativeFinal = true;
      requestCtx.syntheticFinalDeltaPreview = {
        ...(requestCtx.syntheticFinalDeltaPreview || {}),
        response: previewText,
        request_id: eventData.request_id || requestCtx.clientRequestId,
        session_id: eventData.session_id || requestCtx.sessionIdAtSend || "",
        observedAt: requestCtx.syntheticFinalDeltaPreview?.observedAt || Date.now(),
      };

      if (previewText) {
        updatePendingAssistantStreamContent(agentIdAtSend, previewText, {
          streaming: true,
          requestCtx,
        });
      }

      return "event";
    }

    if (shouldIgnoreAssistantStreamDelta(eventData, requestCtx, associatedEvent)) {
      return "event";
    }

    requestCtx.streamedText = (requestCtx.streamedText || "") + (deltaText || "");
    if (typeof queueAssistantTypewriter === "function") queueAssistantTypewriter(agentIdAtSend, requestCtx, requestCtx.streamedText);
    else updatePendingAssistantStreamContent(agentIdAtSend, requestCtx.streamedText);
    return 'delta';
  }

  if (outerType === "error") {
    requestCtx.sawError = true;
    requestCtx.streamFailed = true;
    localClearWaitingForRuntimeEventsTimer(requestCtx);

    const normalizedCompletionState = localGetCompletionState(eventData);
    const finalPayload = {
      ...eventData,
      ok: false,
      completion_state: ["blocked", "incomplete", "empty_final"].includes(normalizedCompletionState) ? normalizedCompletionState : "error",
      incomplete_reason:
        eventData?.incomplete_reason ||
        eventData?.error ||
        eventData?.detail ||
        "stream_error",
      response:
        eventData?.response ||
        eventData?.detail ||
        eventData?.error ||
        "Chat stream failed before a final assistant response was produced.",
      request_id: eventData?.request_id || requestCtx.clientRequestId,
      session_id: eventData?.session_id || requestCtx.sessionIdAtSend || "",
      runtime_events: eventData?.runtime_events || [],
      events: eventData?.events || [],
    };

    if (typeof finalizeNonSuccessChatResponse === "function") {
      finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, finalPayload, "stream_error");
    } else {
      await localFinalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, finalPayload, "stream_error");
    }
    return "error";
  }

  if (isChatStreamFinalEventName(outerType) || isDirectCompletionEventName(outerType)) {
    requestCtx.sawFinal = true;
    localClearWaitingForRuntimeEventsTimer(requestCtx);
    requestCtx.streamSawFinal = true;
    requestCtx.authoritativeFinalReceived = true;
    requestCtx.streamFinalPayload = eventData;
    requestCtx.streamFinalCompletionState = localGetCompletionState(eventData);
    if (requestCtx.streamCompleted) return "final";
    if (localIsNonSuccessFinalPayload(eventData)) {
      if (typeof finalizeNonSuccessChatResponse === "function") {
        finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, eventData, "stream_final");
      } else {
        await localFinalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, eventData, "stream_final");
      }
      return "final_non_success";
    }
    const finalText = localFinalResponseText(eventData);
    if (!localIsCompletedFinalPayload(eventData) || !finalText) {
      await localHandleIncompleteChatStream(agentIdAtSend, requestCtx, "runtime_incomplete", eventData);
      return "final_incomplete";
    }
    requestCtx.streamCompleted = true;
    const finalPayload = {
      ...eventData,
      response: finalText,
      display_blocks: eventData?.display_blocks || [],
      session_id: eventData?.session_id || requestCtx.sessionIdAtSend || "",
      request_id: eventData?.request_id || requestCtx.clientRequestId,
      user_message_id: eventData?.user_message_id || "",
      assistant_message_id: eventData?.assistant_message_id || "",
      assistant_message_ids: localNormalizeAssistantMessageIds(eventData),
      events: eventData?.events || [],
      runtime_events: eventData?.runtime_events || [],
      completion_state: localGetCompletionState(eventData) || "completed",
    };
    await handleAgentChatSuccess(agentIdAtSend, requestCtx, finalPayload, {
      source: "stream_final",
      allowFinalWithoutActiveRequest: true,
    });
    return 'final';
  }

  if (outerType === "done") {
    localClearWaitingForRuntimeEventsTimer(requestCtx);
    if (requestCtx.streamCompleted) return "done";
    return "done";
  }
  if (outerType === "heartbeat") {
    requestCtx.lastHeartbeatAt = Date.now();
    const heartbeatPayload = {
      event_type: "heartbeat",
      request_id: eventData.request_id || requestCtx.requestId || requestCtx.clientRequestId,
      session_id: eventData.session_id || requestCtx.sessionIdAtSend || "",
      agent_id: eventData.agent_id || agentIdAtSend,
      data: {
        ...eventData,
        request_id: eventData.request_id || requestCtx.requestId || requestCtx.clientRequestId,
        session_id: eventData.session_id || requestCtx.sessionIdAtSend || "",
        agent_id: eventData.agent_id || agentIdAtSend,
      },
    };
    handleAgentEventMessage(JSON.stringify(heartbeatPayload), {
      agentId: agentIdAtSend,
      sessionId: requestCtx.sessionIdAtSend || eventData.session_id || "",
      requestId: requestCtx.requestId || requestCtx.clientRequestId,
    });
    if (state.selectedAgentId === agentIdAtSend) setChatStatus("Thinking…");
    return "heartbeat";
  }

  const streamEventPayload = {
    event_type: outerType || getChatStreamEventType(eventName, data),
    request_id: eventData.request_id || requestCtx.clientRequestId,
    session_id: eventData.session_id || requestCtx.sessionIdAtSend || "",
    agent_id: eventData.agent_id || agentIdAtSend,
    data: {
      ...eventData,
      request_id: eventData.request_id || requestCtx.clientRequestId,
      session_id: eventData.session_id || requestCtx.sessionIdAtSend || "",
      agent_id: eventData.agent_id || agentIdAtSend,
    },
  };
  handleAgentEventMessage(JSON.stringify(streamEventPayload), {agentId: agentIdAtSend, sessionId: requestCtx.sessionIdAtSend || eventData.session_id || "", requestId: requestCtx.clientRequestId});
  return 'event';
}

async function handleIncompleteChatStream(agentIdAtSend, requestCtx, reason, payload = {}) {
  const chatState = ensureChatState(agentIdAtSend);
  if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestCtx.clientRequestId || requestCtx.streamCompleted || requestCtx.streamFailed) return;
  clearWaitingForRuntimeEventsTimer(requestCtx);
  cancelAssistantTypewriter(requestCtx);
  const fallbackCompletionState = reason === "runtime_error" ? "error" : "incomplete";
  const finalPayload = {
    ...payload,
    completion_state: getCompletionState(payload) || fallbackCompletionState,
    incomplete_reason: payload?.incomplete_reason || payload?.error || payload?.detail || reason,
    request_id: payload?.request_id || requestCtx.clientRequestId,
    session_id: payload?.session_id || requestCtx.sessionIdAtSend || chatState.sessionId || "",
  };
  if (state.selectedAgentId === agentIdAtSend) {
    finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, finalPayload, reason);
    showToast(String(finalPayload.incomplete_reason || "Assistant response stream ended in an incomplete state."));
    addEditButtonsToMessages();
    renderIcons();
    scrollToBottom();
  } else {
    finalizeTerminalThinkingState(agentIdAtSend, requestCtx, finalPayload);
    chatState.needsReload = true;
    markAgentUnread(agentIdAtSend, "completed");
    renderAgentList();
  }
}

async function handleChatStreamMissingFinal(agentIdAtSend, requestCtx) {
  if (requestCtx?.streamFailed || requestCtx?.streamIncomplete || requestCtx?.streamCompleted || requestCtx?.sawError) {
    return "handled";
  }
  await handleIncompleteChatStream(agentIdAtSend, requestCtx, "missing_final", {
    streamedTextPreview: requestCtx?.streamedText || "",
  });
  return "handled";
}

async function trySubmitChatStreamForSelectedAgent(agentIdAtSend, requestCtx, requestBody) {
  const resp = await fetch(`/a/${agentIdAtSend}/api/chat/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(requestBody) });
  if ([404,405,501].includes(resp.status) || !resp.body) return 'unsupported';
  if (!resp.ok) {
    throw new Error(await handleErrorResponse(resp));
  }
  requestCtx.usedStream = true;
  const localFinalizeNonSuccessChatResponse = (typeof finalizeNonSuccessChatResponse === "function")
    ? finalizeNonSuccessChatResponse
    : (agentId, ctx, payload, source) => handleIncompleteChatStream(agentId, ctx, source || "non_success", payload);
  const parseSseEventsFromChunk = (
    typeof globalThis !== "undefined"
    && typeof globalThis.parseSseEventsFromChunk === "function"
  )
    ? globalThis.parseSseEventsFromChunk
    : (currentBuffer, chunkText) => {
      const combined = `${currentBuffer || ""}${chunkText || ""}`;
      const parts = combined.split(/\r?\n\r?\n/);
      const nextBuffer = parts.pop() || "";
      const events = parts.map((part) => parseSseEvent(part)).filter(Boolean);
      return { events, buffer: nextBuffer };
    };
  const reader = resp.body.getReader(); const decoder = new TextDecoder();
  let buffer=''; let sawEvent=false; let sawFinal=false; let sawError=false;
  const flushChunk = async (chunkText) => {
    const parsedBatch = parseSseEventsFromChunk(buffer, chunkText);
    buffer = parsedBatch.buffer;
    for (const parsed of parsedBatch.events) {
      sawEvent = true;
      const r = await handleChatStreamEvent(agentIdAtSend, requestCtx, parsed.eventName, parsed.data);
      if (r === "final") sawFinal = true;
      if (r === "error" || r === "final_non_success" || r === "final_incomplete") sawError = true;
    }
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      await flushChunk(decoder.decode(value, { stream: true }));
    }
    await flushChunk(decoder.decode());
    if (buffer.trim()) {
      const parsed = parseSseEvent(buffer);
      if (parsed) {
        sawEvent = true;
        const r = await handleChatStreamEvent(agentIdAtSend, requestCtx, parsed.eventName, parsed.data);
        if (r === "final") sawFinal = true;
        if (r === "error" || r === "final_non_success" || r === "final_incomplete") sawError = true;
      }
    }
  } catch (e) { if (sawEvent) throw e; return 'unsupported'; }
  if (
    requestCtx.streamCompleted ||
    requestCtx.streamIncomplete ||
    requestCtx.streamFailed ||
    sawFinal ||
    sawError
  ) return "handled";
  if (requestCtx.streamFinalCandidate && getChatStreamTextPayload(requestCtx.streamFinalCandidate)) {
    const candidate = requestCtx.streamFinalCandidate;
    const candidateText = finalResponseText(candidate) || getChatStreamTextPayload(candidate);
    if (isNonSuccessFinalPayload(candidate)) {
      await localFinalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, candidate, "candidate_final");
      return "handled";
    }
    if (isCompletedFinalPayload(candidate) && candidateText) {
      requestCtx.streamCompleted = true;
      await handleAgentChatSuccess(agentIdAtSend, requestCtx, {
        ...candidate,
        response: candidateText,
        session_id: candidate.session_id || requestCtx.sessionIdAtSend || "",
        request_id: candidate.request_id || requestCtx.clientRequestId,
        events: candidate.events || requestCtx.streamEvents || [],
        runtime_events: candidate.runtime_events || requestCtx.runtimeEvents || [],
      });
      return "handled";
    }
    if (candidateText) {
      await handleIncompleteChatStream(agentIdAtSend, requestCtx, "runtime_incomplete", candidate);
      return "handled";
    }
  }
  if (requestCtx.streamSawFinal && requestCtx.streamFinalPayload) {
    await handleIncompleteChatStream(
      agentIdAtSend,
      requestCtx,
      sawError ? "runtime_error" : "runtime_incomplete",
      requestCtx.streamFinalPayload,
    );
    return "handled";
  }
  if (sawEvent) {
    await handleChatStreamMissingFinal(agentIdAtSend, requestCtx);
    return "handled";
  }
  return 'unsupported';
}

async function handleAgentChatSuccess(agentIdAtSend, requestCtx, payload, options = {}) {
  const localHasRenderableAssistantPayload = (typeof hasRenderableAssistantPayload === "function")
    ? hasRenderableAssistantPayload
    : (candidate) => {
      const text = String(candidate?.response || "").trim();
      if (text) return true;
      const blocks = Array.isArray(candidate?.display_blocks) ? candidate.display_blocks : [];
      return blocks.some((block) => (typeof hasRenderableDisplayBlock === "function")
        ? hasRenderableDisplayBlock(block)
        : !!(block && typeof block === "object" && String(block.text || block.content || block.value || "").trim()));
    };
  const localNormalizeAssistantMessageIds = (typeof normalizeAssistantMessageIds === "function")
    ? normalizeAssistantMessageIds
    : (candidate = {}) => {
      const rawIds = Array.isArray(candidate?.assistant_message_ids) ? candidate.assistant_message_ids : [];
      const ids = rawIds.map((id) => String(id || "")).filter(Boolean);
      const primary = String(candidate?.assistant_message_id || ids[ids.length - 1] || "");
      if (primary && !ids.includes(primary)) ids.push(primary);
      return ids;
    };
  const localPrimaryAssistantMessageId = (typeof getPrimaryAssistantMessageId === "function")
    ? getPrimaryAssistantMessageId
    : (candidate = {}) => {
      const ids = localNormalizeAssistantMessageIds(candidate);
      return String(candidate?.assistant_message_id || ids[ids.length - 1] || "");
    };
  const localNormalizePayloadThinkingEvents = (typeof normalizePayloadThinkingEvents === "function")
    ? normalizePayloadThinkingEvents
    : (events) => Array.isArray(events) ? events : [];
  const chatState = ensureChatState(agentIdAtSend);
  const activeMatches = Boolean(
    chatState?.currentRequest
    && chatState.currentRequest.clientRequestId === requestCtx.clientRequestId
  );

  const finalForSameRequest = Boolean(
    options?.allowFinalWithoutActiveRequest === true
    && (
      requestCtx.streamSawFinal === true
      || requestCtx.authoritativeFinalReceived === true
      || payload?.request_id === requestCtx.clientRequestId
      || payload?.request_id === requestCtx.requestId
      || payload?.request_id === requestCtx.requestId
    )
  );

  if (!activeMatches && !finalForSameRequest) return;

  if (!localHasRenderableAssistantPayload(payload)) {
    const finalSessionId = payload?.session_id || requestCtx?.sessionIdAtSend || chatState?.sessionId || "";
    removeTemporaryAssistantRows({ requestId: requestCtx.clientRequestId, onlyEmpty: true });
    chatState.currentRequest = null;
    chatState.inflightThinking = null;
    chatState.pendingThinkingEvents = null;
    chatState.needsReload = true;
    setChatSubmittingForAgent(agentIdAtSend, false);
    setChatStatus("Completed without a visible assistant response. Reloading session...");
    if (finalSessionId) {
      await loadSessionForAgent(agentIdAtSend, finalSessionId, { render: true });
    }
    if (typeof syncSelectedAgentChatActionControls === "function") syncSelectedAgentChatActionControls();
    return;
  }
  const payloadThinkingEvents = [
    ...localNormalizePayloadThinkingEvents(payload?.events || []),
    ...localNormalizePayloadThinkingEvents(payload?.runtime_events || []),
  ];
  const mergedThinkingEvents = mergeThinkingEvents(
    chatState.inflightThinking?.events || [],
    payloadThinkingEvents,
  );
  const hasMeaningfulContext = (typeof hasMeaningfulContextState === "function")
    ? hasMeaningfulContextState
    : (value) => !!(value && typeof value === "object" && Object.keys(value).length);
  const hasContextContents = (typeof hasMeaningfulContextContents === "function")
    ? hasMeaningfulContextContents
    : (value) => !!(value && typeof value === "object" && (
      String(value.summary || "").trim() || String(value.next_step || "").trim()
    ));
  const pickContextState = (typeof pickContextStateWithContentsFirst === "function")
    ? pickContextStateWithContentsFirst
    : (...candidates) => candidates.find((candidate) => hasMeaningfulContext(candidate)) || null;
  const contextFromEvents = (typeof extractLatestContextStateFromEvents === "function")
    ? extractLatestContextStateFromEvents
    : () => null;
  const getContextBudget = (typeof pickContextBudget === "function")
    ? pickContextBudget
    : (...candidates) => {
      for (const candidate of candidates) {
        const budget = (candidate && typeof candidate === "object" && candidate.budget && typeof candidate.budget === "object")
          ? candidate.budget
          : null;
        if (budget && Object.keys(budget).length) return budget;
      }
      return null;
    };
  updateAgentSession(agentIdAtSend, payload.session_id || requestCtx.sessionIdAtSend || "");
  const finalSessionId = payload.session_id || requestCtx.sessionIdAtSend || "";
  const payloadContextState = payload?.context_state;
  const mergedEventContextState = contextFromEvents(mergedThinkingEvents);
  const eventContextState = contextFromEvents(payloadThinkingEvents);
  const liveContextState = chatState.inflightThinking?.contextState;
  const priorContextState = chatState.lastThinkingSnapshot?.contextState;
  const finalContextState = pickContextState(
    payloadContextState,
    mergedEventContextState,
    liveContextState,
    priorContextState,
  );
  const contextSource =
    hasContextContents(payloadContextState)
      ? "final_response"
      : hasContextContents(eventContextState) || hasContextContents(mergedEventContextState)
        ? "final_response"
      : hasContextContents(liveContextState)
        ? "last_observed_live"
        : hasContextContents(priorContextState)
          ? "previous_snapshot"
          : hasMeaningfulContext(finalContextState)
            ? "context_window_only"
            : "none";
  const finalThinkingSnapshot = {
    ...(chatState.inflightThinking || {}),
    id: payload.request_id || requestCtx.clientRequestId,
    requestId: payload.request_id || requestCtx.clientRequestId,
    sessionId: finalSessionId,
    events: mergedThinkingEvents,
    completed: true,
    contextState: finalContextState,
    contextSource,
    contextBudget: (
      getContextBudget(
        finalContextState,
        payloadContextState,
        mergedEventContextState,
        eventContextState,
        liveContextState,
        priorContextState,
      )
      || chatState.inflightThinking?.contextBudget
      || chatState.lastThinkingSnapshot?.contextBudget
      || null
    ),
    completedAt: Date.now(),
  };
  chatState.lastThinkingSnapshot = finalThinkingSnapshot;
  const canRenderThinkingPanel = typeof isThinkingPanelActiveForAgent === "function" && isThinkingPanelActiveForAgent(agentIdAtSend);
  chatState.currentRequest = null;
  chatState.inflightThinking = null;
  chatState.pendingThinkingEvents = null;
  setChatSubmittingForAgent(agentIdAtSend, false);
  if (typeof syncSelectedAgentChatActionControls === "function") syncSelectedAgentChatActionControls();
  if (state.selectedAgentId !== agentIdAtSend) {
    if (canRenderThinkingPanel) {
      if (typeof renderThinkingPanelFromClientState === "function") renderThinkingPanelFromClientState(chatState);
      if (finalSessionId) {
        if (typeof loadPersistedThinkingPanel === "function") loadPersistedThinkingPanel(finalSessionId, {
          preserveLiveOnFailure: true,
          preserveLiveIfEmpty: true,
          preserveLiveIfNoContext: true,
          expectedRequestId: finalThinkingSnapshot.requestId,
        });
      }
    }
    chatState.needsReload = true;
    markAgentUnread(agentIdAtSend, "completed");
    renderAgentList();
    const agentName = state.mineAgents.find((a) => a.id === agentIdAtSend)?.name || agentIdAtSend;
    notifyAgentCompletion(agentIdAtSend, agentName, "completed", (payload.response || "").slice(0, 80));
    return;
  }

  const optimisticUserArticle = getLatestOptimisticUserArticle();
  if (!optimisticUserArticle) {
    if (finalSessionId) {
      await loadSessionForAgent(agentIdAtSend, finalSessionId, { render: true });
    }
    if (canRenderThinkingPanel) {
      if (typeof renderThinkingPanelFromClientState === "function") renderThinkingPanelFromClientState(chatState);
      if (finalSessionId) {
        if (typeof loadPersistedThinkingPanel === "function") loadPersistedThinkingPanel(finalSessionId, {
          preserveLiveOnFailure: true,
          preserveLiveIfEmpty: true,
          preserveLiveIfNoContext: true,
          expectedRequestId: finalThinkingSnapshot.requestId,
        });
      }
    }
    addEditButtonsToMessages();
    setChatStatus("Ready");
    if (document.hidden) {
      const agentName = state.mineAgents.find((a) => a.id === agentIdAtSend)?.name || agentIdAtSend;
      notifyAgentCompletion(agentIdAtSend, agentName, "completed", (payload.response || "").slice(0, 80));
    }
    return;
  }
  if (optimisticUserArticle && payload.user_message_id) {
    optimisticUserArticle.dataset.messageId = payload.user_message_id;
    delete optimisticUserArticle.dataset.optimisticUser;
  }
  if (requestCtx.usedStream === true) {
    await flushAssistantTypewriter(agentIdAtSend, requestCtx, payload.response || "");
    const finalized = finalizePendingAssistantRow(agentIdAtSend, requestCtx, payload);
    if (!finalized) {
      const updated = updateOrCreateAssistantRowForRequest(agentIdAtSend, requestCtx, payload, { completed: true });
      if (!updated) {
        const assistantHtml = buildAssistantMessageArticle(
          payload.response || "",
          payload.display_blocks || [],
          getSelectedAssistantDisplayName(payload.author_name || "Assistant"),
          payload.assistant_message_id || "",
        );
        dom.messageList?.insertAdjacentHTML("beforeend", assistantHtml);
      }
    }
  } else {
    removeTemporaryAssistantRows({ requestId: requestCtx.clientRequestId, onlyEmpty: true });
    const assistantHtml = buildAssistantMessageArticle(
      payload.response || "",
      payload.display_blocks || [],
      getSelectedAssistantDisplayName(payload.author_name || "Assistant"),
      payload.assistant_message_id || "",
      { userMessageId: payload.user_message_id || optimisticUserArticle?.dataset?.messageId || "", messageIds: localNormalizeAssistantMessageIds(payload), primaryMessageId: localPrimaryAssistantMessageId(payload), requestId: payload.request_id || "", copyText: payload.response || "" }
    );
    dom.messageList?.insertAdjacentHTML("beforeend", assistantHtml);
  }
  if (canRenderThinkingPanel) {
    if (typeof renderThinkingPanelFromClientState === "function") renderThinkingPanelFromClientState(chatState);
    if (finalSessionId) {
      if (typeof loadPersistedThinkingPanel === "function") loadPersistedThinkingPanel(finalSessionId, {
        preserveLiveOnFailure: true,
        preserveLiveIfEmpty: true,
        preserveLiveIfNoContext: true,
        expectedRequestId: finalThinkingSnapshot.requestId,
      });
    }
  }
  setChatStatus("Ready");
  renderMarkdown(dom.messageList);
  decorateToolMessages(dom.messageList);
  renderIcons();
  addEditButtonsToMessages();
  scrollToBottom();
  if (document.hidden) {
    const agentName = state.mineAgents.find((a) => a.id === agentIdAtSend)?.name || agentIdAtSend;
    notifyAgentCompletion(agentIdAtSend, agentName, "completed", (payload.response || "").slice(0, 80));
  }
}

function handleAgentChatFailure(agentIdAtSend, requestCtx, error) {
  const chatState = ensureChatState(agentIdAtSend);
  if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestCtx.clientRequestId) return;
  const restoredMessage = requestCtx.backupMessage || "";
  const errorMsg = error?.message || "Send failed";
  const finalPayload = {
    completion_state: "error",
    incomplete_reason: errorMsg,
    request_id: requestCtx.clientRequestId,
    session_id: requestCtx.sessionIdAtSend || chatState.sessionId || "",
  };
  if (chatState.inflightThinking) {
    const failedEvent = {
      type: "execution.failed",
      raw_type: "execution.failed",
      lifecycle_type: "execution.failed",
      data: { message: errorMsg, error: errorMsg },
      state: "failed",
      ts: Date.now() / 1000,
      request_id: requestCtx.clientRequestId,
      session_id: requestCtx.sessionIdAtSend || "",
    };
    chatState.inflightThinking.events.push(failedEvent);
    chatState.inflightThinking.completed = true;
    chatState.lastThinkingSnapshot = { ...chatState.inflightThinking };
  }
  requestCtx.streamFailed = true;
  requestCtx.terminalPayload = finalPayload;
  if (typeof finalizeTerminalThinkingState === "function") finalizeTerminalThinkingState(agentIdAtSend, requestCtx, finalPayload);
  else {
    chatState.lastThinkingSnapshot = chatState.lastThinkingSnapshot || (chatState.inflightThinking ? { ...chatState.inflightThinking } : null);
    chatState.inflightThinking = null;
    chatState.pendingThinkingEvents = null;
  }
  chatState.currentRequest = null;
  setChatSubmittingForAgent(agentIdAtSend, false);
  if (state.selectedAgentId !== agentIdAtSend) {
    chatState.draftText = restoredMessage;
    chatState.pendingFiles = [];
    chatState.pendingThinkingEvents = null;
    chatState.needsReload = false;
    markAgentUnread(agentIdAtSend, "error");
    renderAgentList();
    const agentName = state.mineAgents.find((a) => a.id === agentIdAtSend)?.name || agentIdAtSend;
    notifyAgentCompletion(agentIdAtSend, agentName, "failed", errorMsg);
    return;
  }
  removeTemporaryAssistantRows({ requestId: requestCtx.clientRequestId, onlyEmpty: false });
  removeLatestOptimisticUserRow();
  chatState.pendingFiles = [];
  if (dom.chatInput) dom.chatInput.value = restoredMessage;
  const attachmentsInput = document.getElementById("chat-attachments");
  if (attachmentsInput) attachmentsInput.value = "";
  showToast("Send failed. Please re-attach files before retrying.");
  renderInputPreview();
  syncChatInputHeight();
  if (typeof isThinkingPanelActiveForAgent === "function" && isThinkingPanelActiveForAgent(agentIdAtSend)) {
    if (typeof renderThinkingPanelFromClientState === "function") renderThinkingPanelFromClientState(chatState);
  }
  if (typeof setTerminalCompletionStatus === "function") setTerminalCompletionStatus(finalPayload);
  else setChatStatus(errorMsg, true);
  if (dom.messageList) {
    const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    dom.messageList.insertAdjacentHTML("beforeend", `
      <div class="message-row message-row-assistant message-row-error">
        <div class="message-meta">
          <span class="message-author">System</span>
          <span class="message-timestamp">${now}</span>
        </div>
        <article class="message-surface message-surface-assistant message-surface-error">
          <div class="message-body whitespace-pre-wrap text-sm">${safe(errorMsg)}</div>
        </article>
      </div>
    `);
    scrollToBottom();
    renderIcons();
  }
  if (document.hidden) {
    const agentName = state.mineAgents.find((a) => a.id === agentIdAtSend)?.name || agentIdAtSend;
    notifyAgentCompletion(agentIdAtSend, agentName, "failed", errorMsg);
  }
}

// ===== HTML decode helper =====
function decodeHtml(text) {
  if (!text) return '';
  const textarea = document.createElement('textarea');
  textarea.innerHTML = text;
  return textarea.value || '';
}

// ===== markdown + icons lifecycle =====
function shouldDecorateChatSwapTarget(target) {
  if (!target || !(target instanceof Element)) return false;
  if (target.id === "message-list") return true;
  if (target.closest?.("#message-list")) return true;
  return !!target.querySelector?.(".message-row-assistant, .message-row-user");
}

function decorateChatMessageRegion(target) {
  const isMessageListTarget = target?.id === "message-list";
  const scope = isMessageListTarget ? target : (dom.messageList || target);
  if (!scope) return;
  renderMarkdown(scope);
  decorateToolMessages(scope);
  addEditButtonsToMessages();
  renderIcons();
}

function initializeRenderLifecycle() {
  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.target;
    if (target?.id === "tool-panel-body" || target?.id === "workspace-detail-content") {
      initializeManagedSettingsPanels();
    }
    if (shouldDecorateChatSwapTarget(target)) {
      decorateChatMessageRegion(target);
      return;
    }
    renderIcons();
  });
}

// ===== suggestion popup hooks =====
function hideSuggest() {
  if (!dom.chatSuggest) return;
  dom.chatSuggest.classList.add("hidden");
  dom.chatSuggest.innerHTML = "";
  state.selectedSuggestionIndex = -1;
  state.suggestRequestSeq += 1;
}

function showSuggest(items, onPick) {
  if (!dom.chatSuggest) return;
  if (!items.length) {
    hideSuggest();
    return;
  }

  dom.chatSuggest.innerHTML = items.map((item, index) => (
    `<button type="button" data-i="${index}" class="portal-suggest-item${item.callable === false ? " is-blocked" : ""}" title="${escapeHtmlAttr(item.title || "")}"><div class="portal-suggest-title">${safe(item.label || item.title || "")}${item.callable === false ? ' <span class="portal-badge">blocked</span>' : ""}</div><div class="portal-suggest-desc">${safe(item.desc || "")}</div></button>`
  )).join("");
  dom.chatSuggest.classList.remove("hidden");
  state.selectedSuggestionIndex = 0;

  const buttons = Array.from(dom.chatSuggest.querySelectorAll("button"));
  buttons.forEach((button) => {
    button.addEventListener("click", () => onPick(items[Number(button.dataset.i)]));
  });
  buttons[0]?.classList.add("is-active");
}

function moveSuggestionSelection(direction) {
  if (!dom.chatSuggest || dom.chatSuggest.classList.contains("hidden")) return;
  const buttons = Array.from(dom.chatSuggest.querySelectorAll("button"));
  if (!buttons.length) return;

  buttons.forEach((b) => b.classList.remove("is-active"));
  state.selectedSuggestionIndex = (state.selectedSuggestionIndex + direction + buttons.length) % buttons.length;
  const selected = buttons[state.selectedSuggestionIndex];
  selected.classList.add("is-active");
  selected.scrollIntoView({ block: "nearest" });
}

function pickCurrentSuggestion() {
  if (!dom.chatSuggest || dom.chatSuggest.classList.contains("hidden")) return false;
  const buttons = Array.from(dom.chatSuggest.querySelectorAll("button"));
  if (!buttons.length) return false;
  const idx = Math.max(0, Math.min(state.selectedSuggestionIndex, buttons.length - 1));
  buttons[idx].click();
  return true;
}

// Fetch file preview and update pendingFile
async function maybeShowSuggest() {
  if (!dom.chatInput) return;
  const requestSeq = ++state.suggestRequestSeq;

  const text = dom.chatInput.value;
  const cursor = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
  const before = text.slice(0, cursor);
  const slash = before.match(/(^|\s)\/([^\s/]*)$/);

  if (slash) {
    if (!state.cachedSkills.length) {
      try {
        const data = await agentApi("/api/skills");
        const skills = (data.skills || []).map(toSkillSuggestion).filter((item) => item.command);
        state.cachedSkills = skills;
        if (state.selectedAgentId) state.cachedSkillsByAgent.set(state.selectedAgentId, skills);
      } catch {
        state.cachedSkills = [];
      }
    }
    if (requestSeq !== state.suggestRequestSeq) return;
    const nowCursor = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
    const nowBefore = dom.chatInput.value.slice(0, nowCursor);
    const nowSlash = nowBefore.match(/(^|\s)\/([^\s/]*)$/);
    if (!nowSlash) {
      hideSuggest();
      return;
    }

    const query = (nowSlash[2] || "").toLowerCase();
    const filteredSkills = !query
      ? state.cachedSkills
      : state.cachedSkills.filter((item) => {
        const haystack = [item.command, item.label, item.title, item.desc]
          .map((v) => String(v || "").toLowerCase());
        return haystack.some((v) => v.includes(query));
      });
    if (!filteredSkills.length) {
      hideSuggest();
      return;
    }

    showSuggest(filteredSkills, (item) => {
      const command = normalizeSkillCommand(item.command || item.label || item.title);
      if (!command) return;
      const pickCursor = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
      const pickBefore = dom.chatInput.value.slice(0, pickCursor);
      const pickSlash = pickBefore.match(/(^|\s)\/([^\s/]*)$/);
      if (!pickSlash) return;
      // Replace from the start of "/" token to cursor while preserving preceding whitespace.
      const start = pickSlash.index + pickSlash[1].length;
      dom.chatInput.setRangeText(`${command} `, start, pickCursor, "end");
      hideSuggest();
    });
    return;
  }

  hideSuggest();
}

function openAssistantDetailsPanel() {
  if (!state.selectedAgentId) return false;
  const agent = state.mineAgents.find((candidate) => candidate.id === state.selectedAgentId);
  if (!agent) return false;
  setToolPanel("Assistant details", `
    <div id="agent-meta" class="portal-detail-card"></div>
    <div id="agent-actions" class="portal-detail-actions"></div>
  `, "details");
  dom.agentMeta = document.getElementById("agent-meta");
  dom.agentActions = document.getElementById("agent-actions");
  renderAgentMeta(agent);
  renderAgentActions(agent, agent.status || "stopped");
  return true;
}

function toggleAssistantDetailsPanel() {
  if (!state.selectedAgentId) {
    showToast("Please select an assistant first");
    return;
  }
  if (state.activeUtilityPanel === "details" && state.toolPanelOpen) {
    closeToolPanel();
    return;
  }
  openAssistantDetailsPanel();
}

async function openUsersPanel() {
  setToolPanel("Users", '<div class="portal-inline-state">Loading users…</div>', "users");
  try {
    await htmx.ajax("GET", "/app/users/panel", {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("Users", `Failed: ${safe(error.message)}`, "users");
  }
}

function showPinnedPanelRestorePlaceholder(message = "Select a utility from top toolbar.") {
  setToolPanel("Panel", `<div class="portal-inline-state">${safe(message)}</div>`, null, {
    persistPreference: false,
  });
  state.toolPanelOpen = true;
  state.toolPanelPinned = true;
  applyToolPanelState();
}

async function restorePinnedToolPanelFromPreferencesOnce() {
  if (hasRestoredPinnedToolPanel) return;
  hasRestoredPinnedToolPanel = true;

  try {
    const prefs = readUiLayoutPreferences();
    if (!prefs.toolPanelPinned) return;
    if (!isWideEnoughToPinToolPanel()) return;

    state.toolPanelOpen = true;
    state.toolPanelPinned = true;
    state.activeUtilityPanel = normalizeUtilityPanelKey(prefs.activeUtilityPanel);
    state.pendingToolPanelRestoreKey = state.activeUtilityPanel;
    applyToolPanelState();

    const panelKey = state.pendingToolPanelRestoreKey;
    const requiresSelectedAssistant = new Set([
      "details",
      "sessions",
      "thinking",
      "server-files",
      "skills",
      "usage",
        ]);

    if (!panelKey) {
      showPinnedPanelRestorePlaceholder();
      return;
    }

    if (requiresSelectedAssistant.has(panelKey) && !state.selectedAgentId) {
      showPinnedPanelRestorePlaceholder("Select an assistant first to restore this panel.");
      return;
    }

    if (panelKey === "details") {
      if (!openAssistantDetailsPanel()) {
        showPinnedPanelRestorePlaceholder();
      }
      return;
    }

    if (panelKey === "sessions") {
      if (getSelectedAgentStatus() === "running") {
        await openSessionsPanel();
      } else {
        setToolPanel("Sessions", "<div class='portal-inline-state'>Start the assistant first to browse sessions.</div>", "sessions", {
          persistPreference: false,
        });
      }
      return;
    }

    if (panelKey === "thinking") {
      await openThinkingProcessPanel();
      return;
    }
    if (panelKey === "server-files") {
      await openServerFiles();
      return;
    }
    if (panelKey === "skills") {
      await openSkillsPanel();
      return;
    }
    if (panelKey === "usage") {
      await openUsagePanel();
      return;
    }
    if (panelKey === "users") {
      await openUsersPanel();
      return;
    }

    showPinnedPanelRestorePlaceholder();
  } catch (_error) {
    showPinnedPanelRestorePlaceholder("Unable to restore the saved panel.");
  }
}

// ===== toolbar actions =====
function setToolPanel(title, contentHtml, panelKey = null, { persistPreference = true } = {}) {
  if (!dom.toolPanel) return;
  state.detailOpen = panelKey === "details";
  state.activeUtilityPanel = normalizeUtilityPanelKey(panelKey);
  dom.toolPanelTitle.textContent = title;
  if (typeof contentHtml === 'string' && contentHtml.startsWith('Failed:')) {
    dom.toolPanelBody.textContent = contentHtml.replace('Failed: ', '');
  } else {
    dom.toolPanelBody.innerHTML = contentHtml;
  }
  openToolPanel();
  if (persistPreference) {
    persistUiLayoutPreferences({ includeSecondaryPane: false, includeToolPanel: true });
  }
}

function closeToolPanel() {
  state.detailOpen = false;
  state.activeUtilityPanel = null;
  state.toolPanelOpen = false;
  state.toolPanelPinned = false;
  applyToolPanelState();
  persistUiLayoutPreferences({
    includeSecondaryPane: false,
    includeToolPanel: true,
    clearToolPanelPreference: true,
  });
}

async function openSessionsPanel() {
  if (!ensureRunningSelectedAssistant("browse sessions")) return;

  setToolPanel("Sessions", '<div class="portal-inline-state">Loading sessions…</div>', "sessions");

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/sessions/panel?current_session_id=${encodeURIComponent(currentSessionIdForSelectedAgent())}&limit=12`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("Sessions", `Failed: ${safe(error.message)}`, "sessions");
    setChatStatus("Failed to load sessions", true);
  }
}

async function openSessionsDrawer() {
  await openSessionsPanel();
}

function closeSessionsDrawer() {
  if (state.activeUtilityPanel !== "sessions") return;
  if (!state.toolPanelOpen) {
    state.activeUtilityPanel = null;
    return;
  }
  closeToolPanel();
}

async function toggleSessionsDrawer() {
  if (state.activeUtilityPanel === "sessions" && state.toolPanelOpen) {
    closeToolPanel();
    return;
  }
  await openSessionsPanel();
}

function setMainView(view) {
  dom.centerPlaceholder?.classList.toggle("hidden", view !== "home");
  dom.agentChatApp?.classList.toggle("hidden", view !== "chat");
  dom.workspaceDetailView?.classList.toggle("hidden", view !== "detail");
}

function restoreAssistantHeaderState() {
  const agent = getSelectedAgent();
  if (agent) {
    const status = getSelectedAgentStatus();
    dom.embedTitle.textContent = agent.name || "Select an assistant";
    if (dom.selectedStatus) {
      dom.selectedStatus.textContent = status;
      dom.selectedStatus.className = `toolbar-status-badge status-${status}`;
    }
    setChatStatus("Ready");
    return;
  }

  dom.embedTitle.textContent = "Select an assistant";
  if (dom.selectedStatus) {
    dom.selectedStatus.textContent = "idle";
    dom.selectedStatus.className = "toolbar-status-badge";
  }
  setChatStatus("Ready");
}

function renderWorkspaceDetailPlaceholder(message = "Select a bundle or task from the left sidebar.", workspaceState = "workspace-placeholder") {
  if (!dom.workspaceDetailContent) return;
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = workspaceState;
  dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state">${safe(message)}</div>`;
}

function getSecondaryPaneLabel() {
  if (state.activeNavSection === "bundles") return "Bundles";
  if (state.activeNavSection === "tasks") return "Tasks";
  if (state.activeNavSection === "runtime-profiles") return "Runtime Profiles";
  if (state.activeNavSection === "automations") return "Automations";
  return "Assistants";
}

function applySecondaryPaneState() {
  const collapsed = !!state.secondaryPaneCollapsed;
  const label = getSecondaryPaneLabel();

  dom.portalShell?.classList.toggle("is-secondary-collapsed", collapsed);
  dom.portalSecondaryPane?.classList.toggle("is-hidden", collapsed);
  dom.portalSecondaryPane?.setAttribute("aria-hidden", collapsed ? "true" : "false");

  if (dom.secondaryPaneToggle) {
    dom.secondaryPaneToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    dom.secondaryPaneToggle.setAttribute("title", collapsed ? `Expand ${label} list` : "Collapse sidebar");
    dom.secondaryPaneToggle.setAttribute("aria-label", collapsed ? `Expand ${label} list` : "Collapse sidebar");
    dom.secondaryPaneToggle.innerHTML = `<i data-lucide="${collapsed ? "panel-left-open" : "panel-left-close"}" class="w-5 h-5"></i>`;
  }

  if (dom.secondaryPaneRestore) {
    dom.secondaryPaneRestore.classList.toggle("hidden", !collapsed);
    dom.secondaryPaneRestore.setAttribute("aria-expanded", collapsed ? "false" : "true");
    dom.secondaryPaneRestore.setAttribute("title", `Expand ${label} list`);
    dom.secondaryPaneRestore.setAttribute("aria-label", `Expand ${label} list`);
  }

  if (state.toolPanelOpen && state.toolPanelPinned) {
    reconcilePinnedToolPanelForLayoutChange();
  }

  renderIcons();
}

function renderSecondaryPaneHeader() {
  if (!dom.secondaryPaneEyebrow || !dom.secondaryPaneTitle || !dom.secondaryPaneActions) return;
  const addAgentBtn = dom.addAgentBtn;
  const addBundleBtn = dom.addBundleBtn;
  const refreshBundlesBtn = dom.refreshBundlesBtn;
  const addTaskBtn = dom.addTaskBtn;
  const addRuntimeProfileBtn = dom.addRuntimeProfileBtn;
  const addAutomationBtn = dom.addAutomationBtn;
  if (addAgentBtn) addAgentBtn.classList.add("hidden");
  if (addBundleBtn) addBundleBtn.classList.add("hidden");
  if (refreshBundlesBtn) refreshBundlesBtn.classList.add("hidden");
  if (addTaskBtn) addTaskBtn.classList.add("hidden");
  if (addRuntimeProfileBtn) addRuntimeProfileBtn.classList.add("hidden");
  if (addAutomationBtn) addAutomationBtn.classList.add("hidden");

  if (state.activeNavSection === "assistants") {
    dom.secondaryPaneEyebrow.textContent = "My Space";
    dom.secondaryPaneTitle.textContent = "Assistants";
    if (addAgentBtn) addAgentBtn.classList.remove("hidden");
  } else if (state.activeNavSection === "bundles") {
    dom.secondaryPaneEyebrow.textContent = "Workspace";
    dom.secondaryPaneTitle.textContent = "Bundles";
    if (refreshBundlesBtn) refreshBundlesBtn.classList.remove("hidden");
    if (addBundleBtn) addBundleBtn.classList.remove("hidden");
  } else if (state.activeNavSection === "tasks") {
    dom.secondaryPaneEyebrow.textContent = "Workspace";
    dom.secondaryPaneTitle.textContent = "Tasks";
    if (addTaskBtn) addTaskBtn.classList.remove("hidden");
  } else if (state.activeNavSection === "automations") {
    dom.secondaryPaneEyebrow.textContent = "Workspace";
    dom.secondaryPaneTitle.textContent = "Automations";
    if (addAutomationBtn) addAutomationBtn.classList.remove("hidden");
  } else {
    dom.secondaryPaneEyebrow.textContent = "My Space";
    dom.secondaryPaneTitle.textContent = "Runtime Profiles";
    if (addRuntimeProfileBtn) addRuntimeProfileBtn.classList.remove("hidden");
  }
}

function syncMainHeader() {
  const assistantMode = state.activeNavSection === "assistants";

  const sessionsBtn = document.getElementById("btn-sessions");
  const assistantOnlyControls = [dom.selectedStatus, sessionsBtn, dom.headerNewChatBtn, dom.detailToggle, document.getElementById("btn-thinking"), document.getElementById("btn-files")];
  assistantOnlyControls.forEach((el) => {
    if (!el) return;
    el.classList.toggle("hidden", !assistantMode);
  });

  if (assistantMode) {
    restoreAssistantHeaderState();
  } else {
    if (state.activeNavSection === "bundles") {
      dom.embedTitle.textContent = "Bundles";
      setChatStatus("Browse and open bundle detail in the main stage");
    } else if (state.activeNavSection === "tasks") {
      dom.embedTitle.textContent = "My Tasks";
      setChatStatus("Browse tasks and open task detail in the main stage");
    } else if (state.activeNavSection === "automations") {
      dom.embedTitle.textContent = "Automations";
      setChatStatus("Manage automation rules");
    } else {
      dom.embedTitle.textContent = "Runtime Profiles";
      setChatStatus("Browse and manage your runtime profiles");
    }
  }
}

function showAssistantDefaultMainView() {
  const agent = getSelectedAgent();
  const status = getSelectedAgentStatus();
  if (agent && status === "running") {
    setMainView("chat");
  } else {
    setMainView("home");
  }
  syncMainHeader();
}

function showBundlesDefaultMainView() {
  renderWorkspaceDetailPlaceholder("Select a bundle from the left sidebar.", "bundles-placeholder");
  syncMainHeader();
}

function showBundlesEmptyMainView() {
  renderWorkspaceDetailPlaceholder(
    "No bundles found. Click refresh to check again or create a bundle.",
    "bundles-placeholder"
  );
  syncMainHeader();
}

function showTasksDefaultMainView() {
  renderWorkspaceDetailPlaceholder("Select a task from the left sidebar.", "tasks-placeholder");
  syncMainHeader();
}

function showBundlesLoadingMainView() {
  setMainView("detail");
  renderWorkspaceDetailPlaceholder("Loading bundles…", "bundles-loading");
  syncMainHeader();
}

function showTasksLoadingMainView() {
  setMainView("detail");
  renderWorkspaceDetailPlaceholder("Loading tasks…", "tasks-loading");
  syncMainHeader();
}

function syncDefaultMainViewForSection(section) {
  if (section === "assistants") {
    showAssistantDefaultMainView();
    return;
  }
  if (section === "bundles") {
    showBundlesDefaultMainView();
    return;
  }
  if (section === "tasks") {
    showTasksDefaultMainView();
    return;
  }
  if (section === "runtime-profiles") {
    renderWorkspaceDetailPlaceholder("Select a runtime profile from the left sidebar.", "runtime-profiles-placeholder");
    return;
  }
  if (section === "automations") {
    renderWorkspaceDetailPlaceholder("Select an automation rule from the left sidebar.", "automations-placeholder");
  }
}

function bundleKeyFromRef(ref) {
  if (!ref) return null;
  return `${ref.repo || ""}|${ref.path || ""}|${ref.branch || ""}`;
}

function bundleKey(item) {
  return bundleKeyFromRef(item?.bundle_ref);
}

function setRequirementBundles(items, { persist = true, hasCache = true } = {}) {
  state.requirementBundles = Array.isArray(items) ? items : [];
  state.hasRequirementBundlesCache = Boolean(hasCache);
  if (state.selectedBundleKey && !state.requirementBundles.some((item) => bundleKey(item) === state.selectedBundleKey)) {
    state.selectedBundleKey = null;
  }
  if (!persist) return;
  try {
    localStorage.setItem(
      REQUIREMENT_BUNDLES_CACHE_KEY,
      JSON.stringify({
        savedAt: Date.now(),
        items: state.requirementBundles,
      }),
    );
  } catch (_error) {}
}

function loadRequirementBundlesFromCache() {
  try {
    const raw = localStorage.getItem(REQUIREMENT_BUNDLES_CACHE_KEY);
    if (!raw) {
      setRequirementBundles([], { persist: false, hasCache: false });
      return { hasCache: false, hasItems: false };
    }
    const parsed = JSON.parse(raw);
    const items = Array.isArray(parsed) ? parsed : (Array.isArray(parsed?.items) ? parsed.items : null);
    if (!Array.isArray(items)) {
      setRequirementBundles([], { persist: false, hasCache: false });
      return { hasCache: false, hasItems: false };
    }
    setRequirementBundles(items, { persist: false, hasCache: true });
    return { hasCache: true, hasItems: items.length > 0 };
  } catch (_error) {
    setRequirementBundles([], { persist: false, hasCache: false });
    return { hasCache: false, hasItems: false };
  }
}

function bundleListItemFromDetail(detail) {
  const manifest = detail?.manifest || {};
  const scope = manifest?.scope || {};
  return {
    bundle_id: manifest?.bundle_id || "",
    title: manifest?.title || "",
    domain: scope?.domain || "",
    status: manifest?.status || "",
    template_id: detail?.template_id || "",
    template_label: detail?.template_label || "",
    artifacts: detail?.artifacts ?? null,
    bundle_ref: detail?.bundle_ref || null,
    manifest_ref: detail?.manifest_ref || null,
    requirements_exists: detail?.requirements_exists ?? null,
    test_cases_exists: detail?.test_cases_exists ?? null,
    last_commit_sha: detail?.last_commit_sha || null,
  };
}

function upsertRequirementBundleListItem(item, { persist = true } = {}) {
  const itemKey = bundleKey(item);
  if (!itemKey) return;
  const nextItems = [...state.requirementBundles];
  const existingIndex = nextItems.findIndex((candidate) => bundleKey(candidate) === itemKey);
  if (existingIndex >= 0) {
    nextItems[existingIndex] = item;
  } else {
    nextItems.push(item);
  }
  nextItems.sort((left, right) => {
    const leftTuple = [left?.template_id || "", left?.domain || "", left?.title || "", left?.bundle_ref?.path || ""];
    const rightTuple = [right?.template_id || "", right?.domain || "", right?.title || "", right?.bundle_ref?.path || ""];
    for (let i = 0; i < leftTuple.length; i += 1) {
      const cmp = String(leftTuple[i]).localeCompare(String(rightTuple[i]));
      if (cmp !== 0) return cmp;
    }
    return 0;
  });
  setRequirementBundles(nextItems, { persist });
}

async function setActiveNavSection(section, {
  toggleIfSame = true,
  preserveCollapsed = false,
  updateRoute = true,
  preferSectionLanding = false,
} = {}) {
  const previousSection = state.activeNavSection;
  const sidebarWasCollapsed = state.secondaryPaneCollapsed;
  const validSections = typeof PORTAL_ROUTE_SECTIONS !== "undefined"
    ? PORTAL_ROUTE_SECTIONS
    : new Set(["assistants", "bundles", "tasks", "runtime-profiles", "automations"]);
  if (!validSections.has(section)) return;

  if (preferSectionLanding) {
    clearPortalSectionDetailSelection(section);
  }

  if (section === state.activeNavSection && toggleIfSame) {
    state.secondaryPaneCollapsed = !state.secondaryPaneCollapsed;
  } else {
    state.activeNavSection = section;
    if (!preserveCollapsed) {
      state.secondaryPaneCollapsed = false;
    }
  }

  dom.railAssistantsBtn?.classList.toggle("is-active", state.activeNavSection === "assistants");
  dom.bundlesMenuBtn?.classList.toggle("is-active", state.activeNavSection === "bundles");
  dom.tasksMenuBtn?.classList.toggle("is-active", state.activeNavSection === "tasks");
  dom.runtimeProfilesMenuBtn?.classList.toggle("is-active", state.activeNavSection === "runtime-profiles");
  dom.automationsMenuBtn?.classList.toggle("is-active", state.activeNavSection === "automations");

  dom.assistantsNavSection?.classList.toggle("hidden", state.activeNavSection !== "assistants");
  dom.bundlesNavSection?.classList.toggle("hidden", state.activeNavSection !== "bundles");
  dom.tasksNavSection?.classList.toggle("hidden", state.activeNavSection !== "tasks");
  dom.runtimeProfilesNavSection?.classList.toggle("hidden", state.activeNavSection !== "runtime-profiles");
  dom.automationsNavSection?.classList.toggle("hidden", state.activeNavSection !== "automations");

  applySecondaryPaneState();
  renderSecondaryPaneHeader();
  syncMainHeader();
  if (typeof persistUiLayoutPreferences === "function") {
    persistUiLayoutPreferences({ includeToolPanel: false });
  }

  const commitCurrentRoute = () => {
    const routeWriteSuppressed = typeof isApplyingPortalRoute !== "undefined" && isApplyingPortalRoute;
    if (
      updateRoute &&
      !routeWriteSuppressed &&
      typeof commitPortalRoute === "function" &&
      typeof currentPortalRouteFromState === "function"
    ) {
      commitPortalRoute(
        preferSectionLanding ? portalSectionRoute(section) : currentPortalRouteFromState()
      );
    }
  };

  if (state.secondaryPaneCollapsed) {
    commitCurrentRoute();
    return;
  }

  const didSwitchSection = section !== previousSection;
  const didRevealPane = sidebarWasCollapsed && !state.secondaryPaneCollapsed;
  const shouldRefreshVisibleSection = didSwitchSection || didRevealPane || preferSectionLanding;

  if (didSwitchSection) {
    if (section === "assistants") {
      showAssistantDefaultMainView();
    } else if (section === "bundles") {
      showBundlesLoadingMainView();
    } else if (section === "tasks") {
      showTasksLoadingMainView();
    } else if (section === "runtime-profiles") {
      renderWorkspaceDetailPlaceholder("Loading runtime profiles…", "runtime-profiles-loading");
    } else if (section === "automations") {
      renderWorkspaceDetailPlaceholder("Loading automations…", "automations-loading");
    }
  }

  if (state.activeNavSection === "bundles" && shouldRefreshVisibleSection) {
    const cacheState = loadRequirementBundlesFromCache();
    renderRequirementBundleList();
    if (preferSectionLanding) {
      if (!cacheState.hasCache) {
        renderWorkspaceDetailPlaceholder(
          "No cached bundles yet. Click refresh to load the latest bundles.",
          "bundles-placeholder"
        );
        syncMainHeader();
      } else if (cacheState.hasItems || state.requirementBundles.length) {
        showBundlesDefaultMainView();
      } else {
        showBundlesEmptyMainView();
      }
    } else if (
      state.activeNavSection === "bundles" &&
      !state.secondaryPaneCollapsed &&
      !state.selectedBundleKey &&
      dom.workspaceDetailContent?.dataset.workspaceState === "bundles-loading"
    ) {
      if (!cacheState.hasCache) {
        renderWorkspaceDetailPlaceholder(
          "No cached bundles yet. Click refresh to load the latest bundles.",
          "bundles-placeholder"
        );
        syncMainHeader();
      } else if (cacheState.hasItems) {
        showBundlesDefaultMainView();
      } else {
        showBundlesEmptyMainView();
      }
    }
  }

  if (state.activeNavSection === "runtime-profiles" && shouldRefreshVisibleSection) {
    await refreshRuntimeProfileList({ preserveSelection: !preferSectionLanding });
    if (state.activeNavSection === "runtime-profiles" && !state.secondaryPaneCollapsed) {
      if (preferSectionLanding) {
        state.selectedRuntimeProfileId = null;
        renderRuntimeProfileList();
        renderWorkspaceDetailPlaceholder(
          "Select a runtime profile from the left sidebar.",
          "runtime-profiles-placeholder"
        );
      } else {
        const defaultProfile = state.runtimeProfiles.find((item) => item.is_default);
        const preferredProfile = defaultProfile || state.runtimeProfiles[0] || null;
        let targetProfileId = null;
        if (didSwitchSection || didRevealPane) {
          targetProfileId = preferredProfile ? preferredProfile.id : null;
          state.selectedRuntimeProfileId = targetProfileId;
        }

        if (targetProfileId) {
          await loadRuntimeProfilePanelContent(targetProfileId, { updateRoute: false });
        } else {
          renderWorkspaceDetailPlaceholder("No runtime profiles found.", "runtime-profiles-placeholder");
        }
      }
    }
  }

  if (state.activeNavSection === "tasks" && shouldRefreshVisibleSection) {
    await refreshMyTasks();
    if (preferSectionLanding) {
      state.selectedTaskId = null;
      renderTaskNavList();
      showTasksDefaultMainView();
    } else if (
      state.activeNavSection === "tasks" &&
      !state.secondaryPaneCollapsed &&
      !state.selectedTaskId &&
      dom.workspaceDetailContent?.dataset.workspaceState === "tasks-loading"
    ) {
      showTasksDefaultMainView();
    }
  }
  if (state.activeNavSection === "automations" && shouldRefreshVisibleSection) {
    await loadAutomationRules();
    const first = state.automations[0];
    if (preferSectionLanding) {
      state.selectedAutomationRuleId = null;
      renderAutomationRuleNavList(state.automations);
      if (first) {
        renderWorkspaceDetailPlaceholder("Select an automation rule from the left sidebar.", "automations-placeholder");
        syncMainHeader();
      } else {
        renderWorkspaceDetailPlaceholder("No automations found.", "automations-placeholder");
      }
    } else if (!first) {
      renderWorkspaceDetailPlaceholder("No automations found.", "automations-placeholder");
    }
  }

  commitCurrentRoute();
}

function renderRequirementBundleList(errorMessage = "") {
  if (!dom.bundleNavList) return;
  if (errorMessage) {
    dom.bundleNavList.innerHTML = `<div class="portal-inline-state is-error">${safe(errorMessage)}</div>`;
    return;
  }

  if (!state.requirementBundles.length) {
    const emptyMessage = state.hasRequirementBundlesCache ? "No bundles found" : "No cached bundles yet";
    dom.bundleNavList.innerHTML = `<div class="portal-bundle-list-state">${safe(emptyMessage)}</div>`;
    return;
  }

  dom.bundleNavList.innerHTML = "";
  state.requirementBundles.forEach((item) => {
    const key = bundleKey(item);
    const activeClass = state.selectedBundleKey === key ? " is-active" : "";
    const row = document.createElement("button");
    row.type = "button";
    row.className = `portal-bundle-row${activeClass}`;
    row.innerHTML = `
      <div class="portal-bundle-title">${safe(item.title || item.bundle_id || item.bundle_ref?.path || "Bundle")}</div>
      <div class="portal-bundle-meta">${safe(item.template_label || item.template_id || "Bundle")} · ${safe(item.domain || "unknown")} · ${safe(item.status || "unknown")}</div>
    `;
    row.addEventListener("click", async () => {
      await setActiveNavSection("bundles", { toggleIfSame: false, updateRoute: false });
      await openRequirementBundleInMain(item.bundle_ref);
    });
    dom.bundleNavList.append(row);
  });
}

async function refreshRequirementBundles({ showLoadingState = true, force = true, notifyOnSuccess = false } = {}) {
  if (!dom.bundleNavList) return;
  const hadBundleCache = state.hasRequirementBundlesCache;
  if (!hadBundleCache && showLoadingState) {
    dom.bundleNavList.innerHTML = '<div class="portal-bundle-list-state">Loading bundles…</div>';
  }
  setButtonDisabled(dom.refreshBundlesBtn, true, "Refreshing bundles...");
  try {
    const endpoint = force ? "/api/requirement-bundles?refresh=1" : "/api/requirement-bundles";
    const bundles = await api(endpoint);
    setRequirementBundles(Array.isArray(bundles) ? bundles : [], { persist: true, hasCache: true });
    renderRequirementBundleList();
    if (
      state.activeNavSection === "bundles" &&
      !state.secondaryPaneCollapsed &&
      !state.selectedBundleKey
    ) {
      if (state.requirementBundles.length > 0) {
        showBundlesDefaultMainView();
      } else {
        showBundlesEmptyMainView();
      }
    }
    if (notifyOnSuccess) showToast("Bundles refreshed");
  } catch (error) {
    if (!hadBundleCache) {
      renderRequirementBundleList(`Failed to load bundles: ${error.message}`);
    } else {
      showToast(`Failed to refresh bundles: ${error.message}`);
    }
  } finally {
    setButtonDisabled(dom.refreshBundlesBtn, false);
  }
}

async function openRequirementBundleInMain(bundleRef = null, { updateRoute = true } = {}) {
  if (!dom.workspaceDetailContent) return;
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  dom.workspaceDetailContent.innerHTML = '<div class="portal-inline-state">Loading bundles…</div>';
  try {
    let path = "/app/requirement-bundles/panel";
    if (bundleRef) {
      const params = new URLSearchParams({ repo: bundleRef.repo, path: bundleRef.path, skill_branch: bundleRef.branch });
      path = `/app/requirement-bundles/open?${params.toString()}`;
      state.selectedBundleKey = bundleKeyFromRef(bundleRef);
      renderRequirementBundleList();
    }
    await htmx.ajax("GET", path, { target: "#workspace-detail-content", swap: "innerHTML" });
    dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
    syncMainHeader();
    if (updateRoute && bundleRef && !isApplyingPortalRoute) {
      commitPortalRoute({ section: "bundles", bundleRef });
    }
  } catch (error) {
    dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed: ${safe(error.message)}</div>`;
  }
}

function renderTaskNavList(errorMessage = "") {
  if (!dom.taskNavList) return;
  if (errorMessage) {
    dom.taskNavList.innerHTML = `<div class="portal-inline-state is-error">${safe(errorMessage)}</div>`;
    return;
  }
  if (!state.myTasks.length) {
    dom.taskNavList.innerHTML = '<div class="portal-bundle-list-state">No visible tasks yet.</div>';
    return;
  }

  dom.taskNavList.innerHTML = "";
  state.myTasks.forEach((task) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `portal-task-row${state.selectedTaskId === task.id ? " is-active" : ""}`;
    row.innerHTML = `
      <div class="portal-bundle-title">${safe(task.task_type || task.id)}</div>
      <div class="portal-bundle-meta">${safe(task.status || "unknown")} · ${safe(task.id)}</div>
    `;
    row.addEventListener("click", async () => {
      await openTaskDetailInMain(task.id);
    });
    dom.taskNavList.append(row);
  });
}

async function refreshMyTasks() {
  if (!dom.taskNavList) return;
  dom.taskNavList.innerHTML = '<div class="portal-bundle-list-state">Loading tasks…</div>';
  try {
    const tasks = await api("/api/my/tasks");
    state.myTasks = Array.isArray(tasks) ? tasks : [];
    if (state.selectedTaskId && !state.myTasks.some((task) => task.id === state.selectedTaskId)) {
      state.selectedTaskId = null;
    }
    renderTaskNavList();
  } catch (error) {
    renderTaskNavList(`Failed to load tasks: ${error.message}`);
  }
}

async function openTaskDetailInMain(taskId, { updateRoute = true } = {}) {
  if (!dom.workspaceDetailContent) return;
  await setActiveNavSection("tasks", { toggleIfSame: false, updateRoute: false });
  if (!state.myTasks.some((task) => task.id === taskId)) {
    await refreshMyTasks();
  }
  state.selectedTaskId = taskId;
  renderTaskNavList();
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "task-detail";
  dom.workspaceDetailContent.innerHTML = '<div class="portal-inline-state">Loading task detail…</div>';
  try {
    await htmx.ajax("GET", `/app/tasks/${encodeURIComponent(taskId)}/panel`, { target: "#workspace-detail-content", swap: "innerHTML" });
    dom.workspaceDetailContent.dataset.workspaceState = "task-detail";
    syncMainHeader();
    if (updateRoute && !isApplyingPortalRoute) {
      commitPortalRoute({ section: "tasks", taskId });
    }
  } catch (error) {
    dom.workspaceDetailContent.dataset.workspaceState = "task-detail";
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed: ${safe(error.message)}</div>`;
  }
}

async function returnFromTaskDetailToSidebar() {
  state.selectedTaskId = null;
  renderTaskNavList();
  await setActiveNavSection("tasks", {
    toggleIfSame: false,
    updateRoute: false,
    preferSectionLanding: true,
  });
  renderWorkspaceDetailPlaceholder("Select a task from the left sidebar.", "tasks-placeholder");
  syncMainHeader();
  if (!isApplyingPortalRoute) {
    commitPortalRoute({ section: "tasks" });
  }
}

function renderChatHistory(messages, metadata = {}) {
  if (!dom.messageList) return;
  if (!messages.length) { clearMessageListToWelcome(); return; }
  dom.messageList.innerHTML = "";
  const displayEntries = groupSessionMessagesForDisplay(messages);
  displayEntries.forEach((entry) => {
    if (entry.type === "message") {
      const message = entry.message;
      if (message.role !== "user") return;
      let timeStr = "";
      if (message.timestamp || message.created_at) { try { timeStr = new Date(message.timestamp || message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (e) {} }
      const container = document.createElement("div"); container.className = "message-row message-row-user";
      const header = document.createElement("div"); header.className = "message-meta message-meta-user";
      const roleLabel = document.createElement("span"); roleLabel.className = "message-author"; roleLabel.textContent = getHistoryMessageDisplayName(message, true); header.appendChild(roleLabel);
      if (timeStr) { const t = document.createElement("span"); t.className = "message-timestamp"; t.textContent = timeStr; header.appendChild(t); }
      container.appendChild(header);
      const article = document.createElement("article"); article.className = "message-surface message-surface-user"; article.dataset.localUser = "1"; if (message.id) article.dataset.messageId = message.id; if (message.metadata?.internal_model_content_hidden) article.dataset.internalModelContentHidden = "1";
      const content = document.createElement("div"); content.className = "message-body whitespace-pre-wrap text-sm"; content.textContent = getHistoryUserVisibleContent(message); article.appendChild(content);
      const normalizedAttachments = Array.isArray(message.attachments) ? message.attachments : [];
      if (normalizedAttachments.length > 0) {
        const attachmentDiv = document.createElement("div"); attachmentDiv.className = "message-attachments";
        normalizedAttachments.forEach((attachment) => {
          const isObj = !!attachment && typeof attachment === "object" && !Array.isArray(attachment);
          const type = isObj ? String(attachment.type || "").toLowerCase() : "";
          const imageUrl = isObj ? (attachment.url || attachment.previewUrl || "") : "";
          const fileId = isObj ? String(attachment.file_id || attachment.fileId || attachment.id || attachment.filename || "attachment") : String(attachment || "");
          const fileName = isObj ? String(attachment.name || attachment.filename || attachment.file_name || fileId || "attachment") : fileId;
          if (type === "image" && imageUrl) { const img = document.createElement("img"); img.src = imageUrl; img.className = "message-attachment-thumb"; img.alt = fileName; img.dataset.fileId = fileId; attachmentDiv.appendChild(img); return; }
          const fileChip = document.createElement("div"); fileChip.className = "message-attachment-file";
          const metaText = formatAttachmentMetaText(attachment); const baseText = `📄 ${fileName || fileId || "attachment"}`; fileChip.textContent = metaText ? `${baseText} · ${metaText}` : baseText;
          attachmentDiv.appendChild(fileChip);
        });
        article.appendChild(attachmentDiv);
      }
      container.appendChild(article); dom.messageList.appendChild(container);
      return;
    }
    if (entry.type === "assistant_group") {
      const first = entry.messages[0] || {};
      const last = entry.messages[entry.messages.length - 1] || first;
      const row = document.createElement("div"); row.className = "message-row message-row-assistant";
      const header = document.createElement("div"); header.className = "message-meta";
      const author = document.createElement("span"); author.className = "message-author"; author.textContent = getHistoryMessageDisplayName(first, false); header.appendChild(author);
      let timeStr = "";
      if (last?.timestamp || last?.created_at || first?.timestamp || first?.created_at) { try { timeStr = new Date(last.timestamp || last.created_at || first.timestamp || first.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (e) {} }
      if (timeStr) { const t = document.createElement("span"); t.className = "message-timestamp"; t.textContent = timeStr; header.appendChild(t); }
      row.appendChild(header);
      const article = document.createElement("article"); article.className = "message-surface message-surface-assistant assistant-message";
      const ids = getAssistantGroupMessageIds(entry);
      const primary = ids[ids.length - 1] || "";
      if (primary) article.dataset.messageId = primary;
      if (primary) article.dataset.primaryMessageId = primary;
      article.dataset.messageIds = JSON.stringify(ids);
      if (entry.userMessageId) article.dataset.userMessageId = entry.userMessageId;
      if (entry.key) article.dataset.assistantGroupKey = entry.key;
      const markdown = getAssistantGroupMarkdown(entry);
      article.dataset.copyText = markdown;
      const content = document.createElement("div"); content.className = "message-markdown md-render max-w-none text-sm";
      content.dataset.md = markdown;
      content.dataset.displayBlocks = JSON.stringify(getAssistantGroupDisplayBlocks(entry));
      article.appendChild(content);
      row.appendChild(article);
      dom.messageList.appendChild(row);
    }
  });
  renderMarkdown(dom.messageList);
  decorateToolMessages(dom.messageList);
  scrollToBottom();
}

function deriveSessionRecoveryNotice(metadata = {}) {
  const latestEventType = typeof metadata.latest_event_type === "string" ? metadata.latest_event_type : "";
  const latestEventState = typeof metadata.latest_event_state === "string" ? metadata.latest_event_state : "";
  // UX-only hint after refresh; this is not live progress recovery/reattach.
  // Keep wording conservative so users are not told work was resumed.

  if (latestEventType === "chat.failed" || latestEventState === "error") {
    return {
      level: "error",
      message: "The previous request failed. You can review the last system error and retry.",
    };
  }

  if (latestEventType === "chat.started" || latestEventState === "running") {
    return {
      level: "warning",
      message: "The previous request may still be running or was interrupted. Live progress cannot be resumed after refresh yet.",
    };
  }

  return null;
}

async function loadSessionForAgent(agentId, sessionId, { render = agentId === state.selectedAgentId } = {}) {
  const normalized = (sessionId || "").trim();
  if (!normalized) return;

  const chatState = ensureChatState(agentId);
  if (render && hasActiveChatRequestForAgent(agentId)) {
    const currentSessionId = chatState?.sessionId || "";
    if (normalized !== currentSessionId) {
      guardNoActiveChatRequestForAgent(agentId, "switch sessions");
      return;
    }
  }

  let data;
  try {
    data = await agentApiFor(agentId, `/api/sessions/${encodeURIComponent(normalized)}`);
  } catch (error) {
    const message = String(error?.message || "");
    if (message.includes("404") || message.includes("410") || message.toLowerCase().includes("no longer exists")) {
      if (normalized === currentSessionIdForAgent(agentId)) {
        updateAgentSession(agentId, "");
        setLastSessionId(agentId, "");
      }
      showToast("Session no longer exists");
      await openSessionsPanel();
      return;
    }
    throw error;
  }
  updateAgentSession(agentId, normalized);
  const latestChatState = ensureChatState(agentId);
  if (latestChatState) latestChatState.needsReload = false;
  let recoveryNotice = null;
  const canApplyRecoveryNotice = () => (
    agentId === state.selectedAgentId
    && !hasActiveChatRequestForAgent(agentId)
  );
  const applyRecoveryNotice = () => {
    if (recoveryNotice) {
      setChatStatus(recoveryNotice.message, recoveryNotice.level === "error");
    }
  };
  const statusPayload = null;
  const shouldApplyRecoveryNotice = canApplyRecoveryNotice();
  const canonicalMessages = getCanonicalMessagesFromSessionPayload(data);
  const messagesForRender = canonicalMessages.length
    ? canonicalMessagesToLegacyDisplayMessages(canonicalMessages)
    : data.messages || [];
  const normalizedPayload = {
    ...data,
    messages: messagesForRender,
    metadata: {
      ...(data.metadata || {}),
      source_of_truth: canonicalMessages.length ? "opencode" : data.metadata?.source_of_truth,
      canonical_messages: canonicalMessages,
      session_status: statusPayload || data.metadata?.session_status || null,
    },
  };
  recoveryNotice = shouldApplyRecoveryNotice ? deriveSessionRecoveryNotice(normalizedPayload.metadata || {}) : null;
  if (latestChatState && canonicalMessages.length) {
    applyCanonicalMessagesToChatState(agentId, normalized, latestChatState, canonicalMessages, normalizedPayload.metadata || {});
  }
  if (render) {
    // Ensure agent name is set
    if (!state.selectedAgentName && state.selectedAgentId) {
      const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
      state.selectedAgentName = agent?.name || null;
    }
    renderChatHistory(normalizedPayload.messages || [], normalizedPayload.metadata || {});
    addEditButtonsToMessages();
    if (!ensureChatState(agentId)?.currentRequest) setChatStatus(`Loaded session ${normalized}`);
    applyRecoveryNotice();
  }
}

async function loadSession(sessionId) {
  return loadSessionForAgent(state.selectedAgentId, sessionId, { render: true });
}

async function renameSessionForAgent(agentId, sessionId, currentName) {
  const normalizedSessionId = (sessionId || "").trim();
  if (!agentId || !normalizedSessionId) return;

  const defaultName = String(currentName || "").trim();
  const proposedName = prompt("Rename session", defaultName || "");
  if (proposedName === null) return;

  const nextName = proposedName.trim();
  if (!nextName) {
    showToast("Session name cannot be empty");
    return;
  }
  if (nextName === defaultName) return;

  try {
    const resp = await fetch(`/a/${agentId}/api/sessions/${encodeURIComponent(normalizedSessionId)}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nextName }),
    });
    if (!resp.ok) throw new Error(await handleErrorResponse(resp));
    showToast("Session renamed");
    await openSessionsPanel();
    renderIcons();
  } catch (error) {
    showToast(`Rename failed: ${safe(error.message)}`);
  }
}

async function deleteSessionForAgent(agentId, sessionId) {
  const normalizedSessionId = (sessionId || "").trim();
  if (!agentId || !normalizedSessionId) return;
  if (!confirm("Delete this session? This cannot be undone.")) return;

  try {
    const resp = await fetch(`/app/agents/${encodeURIComponent(agentId)}/sessions/${encodeURIComponent(normalizedSessionId)}`, {
      method: "DELETE",
    });
    let payload = {};
    let parsedJson = false;
    try {
      payload = await resp.json();
      parsedJson = !!payload && typeof payload === "object";
    } catch (_ignored) {}
    if (!resp.ok || payload.success === false) {
      const detail =
        payload.detail ||
        payload.error ||
        payload.message ||
        (parsedJson ? JSON.stringify(payload) : "") ||
        resp.statusText ||
        `HTTP ${resp.status}`;
      throw new Error(detail);
    }

    if (normalizedSessionId === currentSessionIdForAgent(agentId)) {
      updateAgentSession(agentId, "");
      setLastSessionId(agentId, "");
      if (agentId === state.selectedAgentId) {
        const chatState = getChatState();
        if (chatState) chatState.inflightThinking = null;
        removeTemporaryAssistantRows({ forceAll: true });
        clearMessageListToWelcome();
        resetChatInputHeight();
        setChatStatus("Session deleted");
      }
    }

    showToast("Session deleted");
    await openSessionsPanel();
    renderIcons();
  } catch (error) {
    showToast(`Delete failed: ${safe(error.message)}`);
  }
}

async function openServerFiles() {
  const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
  if (!canWriteAgent(agent)) {
    setToolPanel("Server Files", `<div class="portal-inline-state is-error">You do not have permission to access this assistant's files.</div>`, "server-files");
    return;
  }
  state.serverFilesRootPath = null;
  state.serverFilesCurrentPath = null;
  await loadServerFiles();
}

function buildServerFilesBreadcrumb(path, rootPath) {
  const normalizedRoot = String(rootPath || '/').replace(/\/+$/, '') || '/';
  const normalizedPath = String(path || normalizedRoot || '').replace(/\/+$/, '');
  const workspaceCrumb = normalizedRoot
    ? `<a href="#" class="portal-link-inline portal-breadcrumb-link" data-server-path="${escapeHtmlAttr(normalizedRoot)}">Workspace</a>`
    : '<a href="#" class="portal-link-inline portal-breadcrumb-link" data-server-path="/">Workspace</a>';
  const breadcrumbParts = [
    workspaceCrumb
  ];

  if (normalizedPath === normalizedRoot) {
    return breadcrumbParts.join(' ');
  }

  const relativePath = normalizedPath.startsWith(`${normalizedRoot}/`)
    ? normalizedPath.slice(normalizedRoot.length)
    : normalizedPath;
  const parts = relativePath.split('/').filter(Boolean);
  let currentPath = normalizedRoot;
  const preserveLeadingSlash = normalizedPath.startsWith('/');
  for (const part of parts) {
    currentPath = currentPath
      ? `${currentPath}/${part}`
      : (preserveLeadingSlash ? `/${part}` : part);
    breadcrumbParts.push(
      '<span class="portal-breadcrumb-sep">/</span>' +
      `<a href="#" class="portal-link-inline portal-breadcrumb-link" data-server-path="${escapeHtmlAttr(currentPath)}">${escapeHtml(part)}</a>`
    );
  }
  return breadcrumbParts.join(' ');
}


function normalizeServerFileItem(item) {
  const source = item && typeof item === "object" && !Array.isArray(item) ? item : {};
  const type = String(source.type || source.kind || "").trim().toLowerCase();

  let isDir;
  if (typeof source.is_dir === "boolean") {
    isDir = source.is_dir;
  } else if (typeof source.isDir === "boolean") {
    isDir = source.isDir;
  } else {
    isDir = type === "directory" || type === "dir";
  }

  let isFile;
  if (typeof source.is_file === "boolean") {
    isFile = source.is_file;
  } else if (typeof source.isFile === "boolean") {
    isFile = source.isFile;
  } else if (type === "file") {
    isFile = true;
  } else {
    isFile = !isDir;
  }

  const rawPath =
    source.path ||
    source.relative_path ||
    source.relativePath ||
    source.name ||
    "";

  const path = String(rawPath || "");
  const name = String(source.name || (path ? path.split("/").filter(Boolean).pop() : "") || "");

  return {
    ...source,
    name,
    path,
    is_dir: !!isDir,
    is_file: !!isFile,
  };
}

async function loadServerFiles(path) {
  setToolPanel("Server Files", '<div class="portal-inline-state">Loading files…</div>', "server-files");

  try {
    const hasPath = typeof path === 'string' && path.length > 0;
    const endpoint = hasPath
      ? `/api/server-files?path=${encodeURIComponent(path)}`
      : '/api/server-files';
    const data = await agentApi(endpoint);
    const items = (Array.isArray(data.items) ? data.items : []).map(normalizeServerFileItem);
    const runtimePath = (typeof data.path === 'string' && data.path.length > 0) ? data.path : '';
    const currentPath = runtimePath || (hasPath ? path : (state.serverFilesRootPath || ''));
    const runtimeRoot = (typeof data.root_path === 'string' && data.root_path.length > 0) ? data.root_path : '';
    const rootPath = runtimeRoot || state.serverFilesRootPath || currentPath || '';
    state.serverFilesRootPath = rootPath;
    state.serverFilesCurrentPath = currentPath || rootPath || '';

    const breadcrumb = buildServerFilesBreadcrumb(currentPath, rootPath);

    // Build file rows with checkboxes in separate cell
    const rows = items.map((item) => {
      const icon = item.is_dir ? '📁' : '📄';
      const itemPath = String(item.path || '');
      const safePath = escapeHtmlAttr(itemPath);
      const safeName = escapeHtml(item.name || '(unknown)');
      const disabledAttr = itemPath ? '' : ' disabled';
      return (
        `<div class="portal-file-row file-item" data-path="${safePath}" data-is-dir="${item.is_dir}">` +
          `<input type="checkbox" class="file-checkbox portal-file-checkbox" data-path="${safePath}" data-is-dir="${item.is_dir}" aria-label="${safeName}"${disabledAttr}>` +
          `<div class="portal-file-name-cell name-cell" data-path="${safePath}" data-is-dir="${item.is_dir}">` +
            `<span class="portal-file-icon">${icon}</span>` +
            `<span class="portal-file-name">${safeName}</span>` +
          `</div>` +
        `</div>`
      );
    }).join("");

    // Set panel content with toolbar
    setToolPanel("Server Files",
      `<div id="server-files-panel" class="portal-file-browser">` +
        `<div class="portal-file-toolbar">` +
          `<div class="portal-file-breadcrumb">${breadcrumb}</div>` +
          `<div class="portal-file-toolbar-actions">` +
            `<button class="portal-btn is-secondary sf-upload-btn">Upload</button>` +
            `<button class="portal-btn is-secondary sf-download-btn" disabled>Download</button>` +
            `<button class="portal-btn is-secondary sf-delete-btn" disabled>Delete</button>` +
          `</div>` +
        `</div>` +
        `<div class="portal-file-select-row">` +
          `<input type="checkbox" id="sf-select-all" class="portal-file-checkbox"><label for="sf-select-all">Select all</label>` +
        `</div>` +
        `<div class="portal-panel-stack">${rows || '<div class="portal-inline-state">Empty directory</div>'}</div>` +
      `</div>`,
      "server-files"
    );

    // Add event delegation and handlers
    const panel = document.getElementById('server-files-panel');
    if (panel) {
      // Select all handler
      const selectAll = document.getElementById('sf-select-all');
      if (selectAll) {
        selectAll.addEventListener('change', (e) => {
          const checkboxes = panel.querySelectorAll('.file-checkbox:not([disabled])');
          checkboxes.forEach(cb => cb.checked = e.target.checked);
          updateDownloadButton(panel);
        });
      }

      // File row click handler (navigate on click)
      panel.querySelectorAll('.file-item').forEach(row => {
        row.addEventListener('click', (e) => {
          // Skip if clicking checkbox or name cell (name cell has dedicated handler)
          if (e.target.type === 'checkbox' || e.target.closest('.name-cell')) {
            if (e.target.type === 'checkbox') {
              updateDownloadButton(panel);
            }
            return;
          }

          const filePath = row.dataset.path;
          const isDir = row.dataset.isDir === 'true';

          if (!filePath) return;
          if (isDir) {
            loadServerFiles(filePath);
          }
        });
      });

      // Name cell click handler - navigate for dirs, preview for files
      panel.querySelectorAll('.name-cell').forEach(cell => {
        cell.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();

          // Skip if clicking checkbox
          if (e.target.type === 'checkbox') {
            updateDownloadButton(panel);
            return;
          }

          const filePath = cell.dataset.path;
          const isDir = cell.dataset.isDir === 'true';
          
          if (!filePath) return;
          if (isDir) {
            loadServerFiles(filePath);
          } else {
            previewServerFile(filePath, currentPath, rootPath);
          }
        });
      });

      // Checkbox change handler
      panel.querySelectorAll('.file-checkbox').forEach(cb => {
        cb.addEventListener('change', () => updateDownloadButton(panel));
      });

      // Upload button handler
      panel.querySelector('.sf-upload-btn')?.addEventListener('click', () => {
        uploadToServerFiles(currentPath);
      });

      // Download button handler
      panel.querySelector('.sf-download-btn')?.addEventListener('click', () => {
        const selected = getSelectedFiles(panel);
        if (selected.length > 0) {
          downloadSelectedFiles(selected);
        }
      });

      // Delete button handler
      panel.querySelector('.sf-delete-btn')?.addEventListener('click', () => {
        const selected = getSelectedFiles(panel);
        if (selected.length > 0) {
          deleteSelectedServerFiles(selected, currentPath);
        }
      });

      // Initialize button state
      updateDownloadButton(panel);
    }
  } catch (error) {
    setToolPanel("Server Files", `<div class="portal-inline-state is-error">Failed: ${safe(error.message)}</div>`, "server-files");
  }
}

function getSelectedFiles(panel) {
  const selected = [];
  panel.querySelectorAll('.file-checkbox:checked').forEach(cb => {
    // Include both files and directories
    selected.push(cb.dataset.path);
  });
  return selected;
}

async function parseRuntimeErrorResponse(resp) {
  try {
    const data = await resp.json();
    if (data && typeof data === 'object') {
      const errorText = (typeof data.error === 'string' && data.error.trim()) ? data.error.trim() : '';
      const detailText = (typeof data.detail === 'string' && data.detail.trim()) ? data.detail.trim() : '';
      if (errorText) {
        const safeDetail = detailText && detailText.length <= 160 && !/[\r\n\t]/.test(detailText);
        return safeDetail ? `${errorText} (${detailText})` : errorText;
      }
    }
  } catch (_err) {
    // Fall through to plain text fallback.
  }

  try {
    const errText = await resp.text();
    return errText || `HTTP ${resp.status}`;
  } catch (_err) {
    return `HTTP ${resp.status}`;
  }
}

function updateDownloadButton(panel) {
  const downloadBtn = panel.querySelector('.sf-download-btn');
  const deleteBtn = panel.querySelector('.sf-delete-btn');
  const selectAll = document.getElementById('sf-select-all');
  const checkboxes = panel.querySelectorAll('.file-checkbox:not([disabled])');
  const checkedBoxes = panel.querySelectorAll('.file-checkbox:not([disabled]):checked');

  const selected = getSelectedFiles(panel);
  if (downloadBtn) {
    downloadBtn.disabled = selected.length === 0;
    downloadBtn.textContent = selected.length > 0 ? `Download (${selected.length})` : 'Download';
  }
  if (deleteBtn) {
    deleteBtn.disabled = selected.length === 0;
    deleteBtn.textContent = selected.length > 0 ? `Delete (${selected.length})` : 'Delete';
  }

  // Update select all checkbox state
  if (selectAll) {
    selectAll.disabled = checkboxes.length === 0;
    if (checkboxes.length === 0) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
    } else if (checkedBoxes.length === 0) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
    } else if (checkedBoxes.length === checkboxes.length) {
      selectAll.checked = true;
      selectAll.indeterminate = false;
    } else {
      selectAll.checked = false;
      selectAll.indeterminate = true;
    }
  }
}

async function uploadToServerFiles(targetPath) {
  const input = document.createElement('input');
  input.type = 'file';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setToolPanel("Server Files", `<div class="portal-inline-state">Uploading ${escapeHtml(file.name)}…</div>`, "server-files");

    try {
      const formData = new FormData();
      formData.append('path', targetPath);
      formData.append('file', file);

      const resp = await fetch(`/a/${state.selectedAgentId}/api/server-files/upload`, {
        method: 'POST',
        body: formData
      });

      if (!resp.ok) {
        const errMsg = await parseRuntimeErrorResponse(resp);
        setToolPanel("Server Files", `<div class="portal-inline-state is-error">Upload failed: ${escapeHtml(errMsg)}</div>`, "server-files");
        return;
      }

      const data = await resp.json();
      if (data.success) {
        const mode = typeof data.mode === 'string' ? data.mode : '';
        const uploadedFilename = String(data.uploaded_filename || file.name || 'file');
        const extractedCount = Number.isFinite(Number(data.extracted_count))
          ? Number(data.extracted_count)
          : null;
        const targetLabel = data.target_path ? ` to ${escapeHtml(data.target_path)}` : '';
        let message;
        if (mode === 'zip_extract') {
          const countLabel = extractedCount !== null ? extractedCount : 0;
          message = `Extracted ${countLabel} files${targetLabel}`;
        } else if (mode === 'file_save') {
          message = `Uploaded ${escapeHtml(uploadedFilename)}${targetLabel}`;
        } else if (extractedCount !== null || String(uploadedFilename).toLowerCase().endsWith('.zip')) {
          const countLabel = extractedCount !== null ? extractedCount : 0;
          message = `Extracted ${countLabel} files${targetLabel}`;
        } else {
          message = `Uploaded ${escapeHtml(uploadedFilename)}${targetLabel}`;
        }
        setToolPanel("Server Files", `<div class="portal-inline-state is-success">${message}</div>`, "server-files");
        loadServerFiles(targetPath);
      } else {
        setToolPanel("Server Files", `<div class="portal-inline-state is-error">Upload failed: ${escapeHtml(data.error)}</div>`, "server-files");
      }
    } catch (err) {
      setToolPanel("Server Files", `<div class="portal-inline-state is-error">Upload failed: ${escapeHtml(err.message)}</div>`, "server-files");
    }
  };
  input.click();
}

async function deleteSelectedServerFiles(paths, currentPath) {
  if (!Array.isArray(paths) || paths.length === 0) return;

  const confirmed = window.confirm(`Delete ${paths.length} selected item(s)? This cannot be undone.`);
  if (!confirmed) return;

  setToolPanel("Server Files", `<div class="portal-inline-state">Deleting ${paths.length} item(s)…</div>`, "server-files");

  try {
    const resp = await fetch(`/a/${state.selectedAgentId}/api/server-files/delete`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ paths }),
    });

    if (!resp.ok) {
      const errMsg = await parseRuntimeErrorResponse(resp);
      setToolPanel("Server Files", `<div class="portal-inline-state is-error">Delete failed: ${escapeHtml(errMsg)}</div>`, "server-files");
      return;
    }

    setToolPanel("Server Files", `<div class="portal-inline-state is-success">Deleted ${paths.length} item(s).</div>`, "server-files");
    loadServerFiles(currentPath);
  } catch (err) {
    setToolPanel("Server Files", `<div class="portal-inline-state is-error">Delete failed: ${escapeHtml(err.message)}</div>`, "server-files");
  }
}

function downloadSelectedFiles(paths) {
  if (paths.length === 0) return;

  // Use repeated query params to avoid comma ambiguity
  const url = new URL(`${window.location.origin}/a/${state.selectedAgentId}/api/server-files/download`);
  paths.forEach(p => url.searchParams.append('paths', p));

  const link = document.createElement('a');
  link.href = url.toString();
  link.download = '';
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function previewServerFile(filePath, currentDir, rootPath) {
  try {
    const encodedPath = encodeURIComponent(filePath);
    const dir = currentDir || filePath.substring(0, filePath.lastIndexOf('/'));
    const activeRootPath = rootPath || state.serverFilesRootPath || currentDir || dir || '';
    const breadcrumb = buildServerFilesBreadcrumb(dir, activeRootPath);
    const ext = (filePath.split('.').pop() || '').toLowerCase();
    const contentUrl = `/a/${state.selectedAgentId}/api/server-files/content?path=${encodedPath}`;
    const fileName = filePath.split('/').pop();
    const binaryPreviewExtensions = {
      image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff'],
      pdf: ['pdf'],
      audio: ['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'],
      video: ['mp4', 'webm', 'mov', 'mkv', 'avi', 'm4v'],
    };

    if (binaryPreviewExtensions.image.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<div class="portal-preview-image-wrap"><img src="${contentUrl}" class="max-w-full rounded" /></div>`,
        "server-files"
      );
      return;
    }
    if (binaryPreviewExtensions.pdf.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<iframe src="${contentUrl}" class="w-full" style="min-height: 70vh;" title="${escapeHtmlAttr(fileName)}"></iframe>`,
        "server-files"
      );
      return;
    }
    if (binaryPreviewExtensions.audio.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<audio controls src="${contentUrl}" class="w-full"></audio>`,
        "server-files"
      );
      return;
    }
    if (binaryPreviewExtensions.video.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<video controls src="${contentUrl}" class="max-w-full rounded"></video>`,
        "server-files"
      );
      return;
    }

    const resp = await agentApi(`/api/server-files/read?path=${encodedPath}`);
    if (resp.error) throw new Error(resp.error);
    const content = resp.content || "(empty file)";
    setToolPanel("File: " + fileName,
      `<div class="portal-file-preview-header">${breadcrumb}</div>` +
      `<pre class="portal-panel-pre">${escapeHtml(content)}</pre>`,
      "server-files"
    );
  } catch (error) {
    setToolPanel("File Preview",
      `<div class="portal-inline-state is-error">Unable to preview this file: ${safe(error.message)}</div>`,
      "server-files"
    );
  }
}

async function openSkillsPanel() {
  if (!state.selectedAgentId) return;


  setToolPanel("Skills", '<div class="portal-inline-state">Loading skills…</div>', "skills");

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/skills/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });

    if (!state.cachedSkillsByAgent.has(state.selectedAgentId)) {
      const data = await agentApi("/api/skills");
      const mapped = (data.skills || []).map(toSkillSuggestion).filter((item) => item.command);
      state.cachedSkillsByAgent.set(state.selectedAgentId, mapped);
      state.cachedSkills = mapped;
    }
  } catch (error) {
    setToolPanel("Skills", `Failed: ${safe(error.message)}`, "skills");
  }
}


async function openUsagePanel() {
  if (!state.selectedAgentId) return;


  setToolPanel("Usage", '<div class="portal-inline-state">Loading usage…</div>', "usage");

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/usage/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("Usage", `Failed: ${safe(error.message)}`, "usage");
  }
}



function normalizeManagedModelIdForCapabilities(value) {
  let model = String(value || "").trim().toLowerCase();
  if (!model) return "";
  for (const sep of ["/", ":"]) {
    if (model.includes(sep)) {
      model = model.split(sep).pop().trim();
    }
  }
  return model;
}

function managedModelSupportsTemperature(value) {
  return normalizeManagedModelIdForCapabilities(value) === "gpt-4";
}

function updateTemperatureInputState(root) {
  if (!root) return;
  const modelSelect = root.querySelector("#llm_model");
  const input = root.querySelector("[data-llm-temperature-input]");
  const note = root.querySelector("[data-llm-temperature-note]");
  if (!modelSelect || !input) return;

  const allowTemperature = managedModelSupportsTemperature(modelSelect.value || modelSelect.dataset.currentValue || "");

  input.disabled = !allowTemperature;
  input.placeholder = allowTemperature ? "0.7" : "Only available for gpt-4";
  input.title = allowTemperature
    ? "Temperature is used only for exact gpt-4."
    : "Temperature is disabled because this model omits the deprecated temperature parameter.";

  if (!allowTemperature) {
    input.value = "";
  }

  if (note) {
    note.textContent = allowTemperature
      ? "Temperature is only sent for exact gpt-4."
      : "Temperature is disabled unless the selected model is exact gpt-4. Other models omit this deprecated parameter.";
  }
}

const managedProviderModels = {
  github_copilot: [
    { value: "gpt-5.4-mini", label: "GPT-5.4 mini" },
    { value: "gpt-5.4", label: "GPT-5.4" },
    { value: "gpt-5.3-codex", label: "GPT-5.3-Codex" },
    { value: "gpt-5-mini", label: "GPT-5 mini" },
    { value: "gpt-4.1", label: "GPT-4.1" },
    { value: "gpt-4o", label: "GPT-4o" },
    { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  ],
  openai: [
    { value: "gpt-5.4-mini", label: "GPT-5.4 mini" },
    { value: "gpt-5", label: "GPT-5" },
    { value: "gpt-5-mini", label: "GPT-5 mini" },
    { value: "gpt-4.1", label: "GPT-4.1" },
    { value: "gpt-4o", label: "GPT-4o" },
    { value: "gpt-4o-mini", label: "GPT-4o Mini" },
    { value: "gpt-4", label: "GPT-4" },
    { value: "gpt-3.5-turbo", label: "GPT-3.5 Turbo" },
  ],
  anthropic: [
    { value: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
    { value: "claude-haiku-4-20250514", label: "Claude Haiku 4" },
    { value: "claude-opus-4-20250514", label: "Claude Opus 4" },
  ],
};

function renderComposerModelSelectorForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  if (!dom.chatModelWrap || !dom.chatModelSelect) return;
  if (!chatState) {
    dom.chatModelSelect.innerHTML = "";
    dom.chatModelWrap.classList.add("hidden");
    return;
  }

  const provider = (chatState.profileProvider || "").trim();
  if (!provider || !Object.prototype.hasOwnProperty.call(managedProviderModels, provider)) {
    dom.chatModelSelect.innerHTML = "";
    dom.chatModelWrap.classList.add("hidden");
    return;
  }

  const currentModel = (chatState.profileDefaultModel || "").trim();
  const models = managedProviderModels[provider] || [];
  dom.chatModelSelect.innerHTML = "";

  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.value;
    option.textContent = model.label;
    dom.chatModelSelect.appendChild(option);
  });

  if (currentModel && !models.some((model) => model.value === currentModel)) {
    const extra = document.createElement("option");
    extra.value = currentModel;
    extra.textContent = `${currentModel} (Current)`;
    dom.chatModelSelect.appendChild(extra);
  }

  const allowedValues = new Set(models.map((model) => model.value));
  if (currentModel) allowedValues.add(currentModel);
  if (chatState.modelOverride && !allowedValues.has(chatState.modelOverride)) {
    chatState.modelOverride = "";
  }

  const selectedValue = chatState.modelOverride || currentModel || models[0]?.value || "";
  if (!selectedValue) {
    dom.chatModelSelect.innerHTML = "";
    dom.chatModelWrap.classList.add("hidden");
    return;
  }

  dom.chatModelSelect.value = selectedValue;
  if (dom.chatModelSelect.value !== selectedValue) {
    const fallbackValue = currentModel || models[0]?.value || "";
    dom.chatModelSelect.value = fallbackValue;
  }
  dom.chatModelWrap.classList.remove("hidden");
}

async function refreshComposerModelProfile(agentId) {
  if (!agentId) {
    if (dom.chatModelSelect) dom.chatModelSelect.innerHTML = "";
    dom.chatModelWrap?.classList.add("hidden");
    return;
  }
  const chatState = ensureChatState(agentId);
  if (!chatState) return;

  try {
    const payload = await api(`/api/agents/${agentId}/chat-model-profile`);
    chatState.profileProvider = (payload?.provider || "").trim();
    chatState.profileDefaultModel = (payload?.current_model || "").trim();
    if (agentId !== state.selectedAgentId) return;
    renderComposerModelSelectorForAgent(agentId);
  } catch (error) {
    console.warn("Failed to refresh composer chat model profile", error);
    chatState.profileProvider = "";
    chatState.profileDefaultModel = "";
    if (agentId !== state.selectedAgentId) return;
    if (dom.chatModelSelect) dom.chatModelSelect.innerHTML = "";
    dom.chatModelWrap?.classList.add("hidden");
  }
}

const managedSettingsActionSelector = "[data-settings-action]";
// keep regression guard text for static test:

function normalizeInstanceInputs(root, group) {
  const container = root?.querySelector(`[data-instance-container="${group}"]`);
  const countInput = root?.querySelector(`[data-instance-count="${group}"]`);
  if (!container || !countInput) return;

  const items = Array.from(container.querySelectorAll(`[data-instance-item="${group}"]`));
  items.forEach((item, idx) => {
    const title = item.querySelector(".portal-settings-instance-title");
    if (title) title.textContent = `Instance ${idx + 1}`;
    item.querySelectorAll("[data-field]").forEach((fieldEl) => {
      const field = fieldEl.dataset.field;
      fieldEl.name = `${group}_instances_${idx}_${field}`;
    });
    item.querySelectorAll("input[data-clear-field]").forEach((input) => {
      const field = input.dataset.clearField;
      input.name = `${group}_instances_${idx}_${field}_clear`;
    });
    item.querySelectorAll("input[data-original-field]").forEach((input) => {
      const field = input.dataset.originalField;
      input.name = `${group}_instances_${idx}_original_${field}`;
    });
  });
  countInput.value = String(items.length);
}

function addInstanceRow(root, group) {
  const container = root?.querySelector(`[data-instance-container="${group}"]`);
  if (!container) return;

  const div = document.createElement("div");
  div.className = "portal-settings-instance-card";
  div.dataset.instanceItem = group;

  // Build fields HTML matching server-rendered format
  const projectHtml = group === "jira"
    ? `<input type="text" data-field="project" value="" placeholder="Project" class="portal-form-input" />`
    : `<input type="text" data-field="space" value="" placeholder="Space Key" class="portal-form-input" />`;
  const apiVersionHtml = group === "jira"
    ? `<div class="portal-panel-grid cols-2"><select data-field="api_version" class="portal-form-select"><option value="" selected>Auto API Version</option><option value="2">REST API v2</option><option value="3">REST API v3</option></select><div></div></div>`
    : "";

  const usernamePasswordHtml = `<input type="text" data-field="username" value="" placeholder="Email" class="portal-form-input" /><input type="password" data-field="password" value="" placeholder="Password" class="portal-form-input" />`;

  div.innerHTML = `
    <input type="hidden" data-original-field="name" value="" />
    <input type="hidden" data-original-field="url" value="" />
    <div class="portal-settings-instance-head">
      <span class="portal-settings-instance-title">Instance</span>
      <label class="portal-checkbox-row"><input type="checkbox" data-field="enabled" value="1" checked /><span>Enabled</span></label>
      <button type="button" class="portal-instance-remove" data-action="remove-instance" data-group="${group}">Remove</button>
    </div>
    <div class="portal-panel-grid cols-2"><input type="text" data-field="name" value="" placeholder="Name" class="portal-form-input" /><input type="text" data-field="url" value="" placeholder="URL (e.g. https://yourcompany.atlassian.net)" class="portal-form-input" /></div>
    <div class="portal-panel-grid cols-2">${usernamePasswordHtml}</div>
    <div class="portal-panel-grid cols-2"><input type="password" data-field="token" value="" placeholder="API token" class="portal-form-input" />${projectHtml}</div>
    ${apiVersionHtml}
  `;
  container.append(div);
  normalizeInstanceInputs(root, group);

  if (window.initPasswordToggles) window.initPasswordToggles(root);
}

function normalizeLlmToolPatternInputs(root) {
  const container = root?.querySelector("[data-llm-tools-container]");
  const countInput = root?.querySelector("[data-llm-tools-count]");
  if (!container || !countInput) return;
  const items = Array.from(container.querySelectorAll("[data-llm-tools-item]"));
  items.forEach((item, idx) => {
    const input = item.querySelector("input");
    if (!input) return;
    input.name = `llm_tools_${idx}_pattern`;
  });
  countInput.value = String(items.length);
}

function addLlmToolPatternRow(root, value = "") {
  const container = root?.querySelector("[data-llm-tools-container]");
  if (!container) return;
  const div = document.createElement("div");
  div.className = "flex items-center gap-2";
  div.dataset.llmToolsItem = "";
  div.innerHTML = `
    <input type="text" value="${safe(value)}" placeholder="e.g. git_clone or jira_*" class="portal-form-input" />
    <button type="button" class="portal-btn is-secondary" data-action="remove-llm-tool-pattern">Remove</button>
  `;
  container.append(div);
  normalizeLlmToolPatternInputs(root);
}

function toggleLlmToolsEditor(root) {
  const mode = root?.querySelector('input[name="llm_tools_mode"]:checked')?.value || "inherit";
  const editor = root?.querySelector("[data-llm-tools-editor]");
  if (!editor) return;
  editor.classList.toggle("hidden", mode !== "custom");
}

function markManagedSectionTouched(root, section) {
  if (!root || !section) return;
  const flag = root.querySelector(`[data-touch-flag="${section}"]`);
  if (flag) flag.value = "1";
}

function sectionNameForElement(element) {
  const section = element?.closest?.("[data-managed-section]");
  return section?.dataset?.managedSection || "";
}

window.initPasswordToggles = function(root = document) {
  root.querySelectorAll('input[type="password"]:not(.password-toggle-initialized)').forEach((input) => {
    input.classList.add("pr-6", "password-toggle-initialized");
    const wrapper = document.createElement("div");
    wrapper.className = "relative";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.tabIndex = 0;
    toggle.setAttribute("aria-label", "Toggle password visibility");
    toggle.setAttribute("aria-pressed", "false");
    toggle.className = "portal-password-toggle";
    toggle.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>';
    toggle.addEventListener("click", () => {
      const visible = input.type === "password";
      input.type = visible ? "text" : "password";
      toggle.setAttribute("aria-pressed", visible ? "true" : "false");
    });
    wrapper.appendChild(toggle);
  });
};

function normalizeCopilotRuntimeType(runtimeType) {
  const raw = String(runtimeType || "").trim().toLowerCase();
  if (raw === "opencode") return "opencode";
  return "native";
}


function copilotRuntimeLabel(runtimeType) {
  return normalizeCopilotRuntimeType(runtimeType) === "opencode" ? "OpenCode Runtime" : "EFP Runtime";
}

function maskCopilotSecret(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.length <= 8) return "••••";
  return `${raw.slice(0, 4)}…${raw.slice(-4)}`;
}

function setCopilotResultSummary(root, _runtimeType, message, kind = "") {
  const summary = root?.querySelector("[data-copilot-result-summary]");
  if (!summary) return;
  summary.textContent = message || "";
  summary.classList.toggle("hidden", !message);
  summary.classList.toggle("is-error", kind === "error");
  summary.classList.toggle("is-success", kind === "success");
}
function getManagedCopilotState(root, runtimeType) {
  if (!root.__managedCopilotState) root.__managedCopilotState = { byRuntime: {} };
  const key = normalizeCopilotRuntimeType(runtimeType);
  if (!root.__managedCopilotState.byRuntime[key]) root.__managedCopilotState.byRuntime[key] = { authInterval: null, timerInterval: null };
  return root.__managedCopilotState.byRuntime[key];
}

function stopCopilotPolling(root, runtimeType = null) {
  const keys = runtimeType ? [normalizeCopilotRuntimeType(runtimeType)] : ["native", "opencode"];
  for (const key of keys) {
    const st = getManagedCopilotState(root, key);
    if (st.authInterval) clearInterval(st.authInterval);
    if (st.timerInterval) clearInterval(st.timerInterval);
    st.authInterval = null;
    st.timerInterval = null;
  }
}

function getManagedCopilotAuthBase(root) {
  return (root?.dataset?.copilotAuthBase || "").trim() || "/api/copilot/auth";
}
function setCopilotApiKeyField(root, token) {
  const apiKeyInput =
    root?.querySelector('input[name="llm_api_key"]') ||
    document.querySelector('input[name="llm_api_key"]');
  if (!apiKeyInput || !token) return false;
  apiKeyInput.value = token;
  apiKeyInput.dispatchEvent(new Event("input", { bubbles: true }));
  apiKeyInput.dispatchEvent(new Event("change", { bubbles: true }));
  const form = apiKeyInput.closest("form");
  const touch = form?.querySelector('input[data-touch-flag="llm"], input[name="__touch_llm"]');
  if (touch) touch.value = "1";
  return true;
}

function getManagedGithubBaseUrl(root) {
  const input = root?.querySelector('input[name="github_base_url"]');
  return (input?.value || "").trim();
}

function finishCopilotAuthWithMessage(root, runtimeType, message, kind = "error") {
  stopCopilotPolling(root, runtimeType);
    const finalMessage = message || "Authorization failed";
  if (typeof setCopilotResultSummary === "function") {
    setCopilotResultSummary(root, runtimeType, finalMessage, kind);
  }
}

function updateCopilotAuthCardsVisibility(root, isCopilot) {
  const authRoot = root?.querySelector("[data-copilot-auth-root]");
  if (!authRoot) return;

  authRoot.classList.toggle("hidden", !isCopilot);
  authRoot.querySelectorAll("[data-copilot-auth-button]").forEach((button) => {
    button.classList.toggle("hidden", !isCopilot);
  });

  if (!isCopilot) {
    stopCopilotPolling(root);
    authRoot.querySelector("[data-copilot-instructions]")?.classList.add("hidden");
    setCopilotResultSummary(root, "", "", "");
  }
}

function updateModelOptions(root) {
  const providerSelect = root.querySelector("#llm_provider");
  const modelSelect = root.querySelector("#llm_model");
  if (!providerSelect || !modelSelect) return;
  const provider = providerSelect.value || "";
  const initialProvider = providerSelect.dataset.initialProvider || provider;
  const initialValue = modelSelect.dataset.initialValue || modelSelect.dataset.currentValue || "";
  const lastProvider = modelSelect.dataset.lastProvider || initialProvider;
  const previousValue = modelSelect.value || "";
  const models = managedProviderModels[provider] || [];
  modelSelect.innerHTML = "";
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Use runtime local default";
  modelSelect.appendChild(defaultOption);
  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.value;
    option.textContent = model.label;
    modelSelect.appendChild(option);
  });
  const hasModel = (value) => !!value && models.some((m) => m.value === value);

  let preferred = "";
  if (provider === initialProvider && lastProvider === initialProvider) {
    preferred = initialValue;
    if (preferred && !hasModel(preferred)) {
      const extra = document.createElement("option");
      extra.value = preferred;
      extra.textContent = `${preferred} (Current)`;
      modelSelect.appendChild(extra);
    }
  } else if (provider !== lastProvider) {
    preferred = hasModel(previousValue) ? previousValue : (models[0]?.value || "");
  } else {
    preferred = hasModel(previousValue) ? previousValue : (models[0]?.value || "");
  }
  if (preferred) modelSelect.value = preferred;
  if (!modelSelect.value) modelSelect.value = "";
  modelSelect.dataset.currentValue = modelSelect.value || "";
  modelSelect.dataset.lastProvider = provider;

  const isCopilot = provider === "github_copilot";
  updateCopilotAuthCardsVisibility(root, isCopilot);
  if (!isCopilot) {
    if (typeof stopCopilotPolling === "function") stopCopilotPolling(root);
  }
  if (typeof updateTemperatureInputState === "function") updateTemperatureInputState(root);
}

async function runManagedSettingsTest(root, target, button) {
  const form = root.querySelector("form");
  if (!form) return;
  const testBase = root.dataset.testBase || "";
  if (!testBase) return;
  const resultEl = root.querySelector(`[data-test-result="${target}"]`);
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Testing...";
  try {
    const resp = await fetch(`${testBase}/${target}`, { method: "POST", body: new FormData(form) });
    const data = await resp.json();
    const ok = !!data.ok;
    if (resultEl) {
      resultEl.className = `portal-inline-state ${ok ? "is-success" : "is-error"}`;
      resultEl.textContent = data.message || "";
    }
    showToast(data.message || `${target} test completed`);
  } catch (error) {
    if (resultEl) {
      resultEl.className = "portal-inline-state is-error";
      resultEl.textContent = safe(error.message);
    }
    showToast(`Test failed: ${safe(error.message)}`);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function startCopilotAuth(root, runtimeType) {
  const key = normalizeCopilotRuntimeType(runtimeType);
  const instructions = root?.querySelector("[data-copilot-instructions]");
  const verifyLink = root?.querySelector("[data-copilot-verify-link]");
  const deviceLink = root?.querySelector("[data-copilot-device-link]");
  const userCode = root?.querySelector("[data-copilot-user-code]");
  const timer = root?.querySelector("[data-copilot-timer]");

  stopCopilotPolling(root);
  const authBase = getManagedCopilotAuthBase(root);
  try {
    const response = await fetch(`${authBase}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ runtime_type: key }),
    });
    let data = null;
    try {
      data = await response.json();
    } catch (_error) {
      throw new Error("Authorization start failed: invalid response");
    }
    if (!response.ok || data.error) throw new Error(data.error || data.details || "Authorization start failed");

    const requiredStartFields = ["auth_id", "device_code", "user_code", "verification_url"];
    const missingStartFields = requiredStartFields.filter((field) => !data[field]);
    if (missingStartFields.length > 0) {
      throw new Error(`Authorization start failed: missing ${missingStartFields.join(", ")}`);
    }

    if (instructions) instructions.classList.remove("hidden");
    if (verifyLink) {
      verifyLink.href = data.verification_url;
      verifyLink.textContent = data.verification_url;
    }
    if (deviceLink) {
      if (data.verification_complete_url) {
        deviceLink.href = data.verification_complete_url;
        deviceLink.classList.remove("hidden");
      } else {
        deviceLink.classList.add("hidden");
      }
    }
    if (userCode) userCode.textContent = data.user_code || "";
    if (typeof setCopilotResultSummary === "function") {
      const runtimeLabel = typeof copilotRuntimeLabel === "function" ? copilotRuntimeLabel(key) : (key === "opencode" ? "OpenCode Runtime" : "EFP Runtime");
      setCopilotResultSummary(root, key, `Device authorization started for ${runtimeLabel}. Complete GitHub verification, then wait for this panel to update.`);
    }

    let remaining = Number(data.expires_in || 600);
    if (timer) timer.textContent = `${remaining}s`;
    const st = getManagedCopilotState(root, key);
    st.timerInterval = setInterval(() => {
      remaining -= 1;
      if (timer) timer.textContent = `${Math.max(remaining, 0)}s`;
      if (remaining <= 0) {
        finishCopilotAuthWithMessage(root, key, "Authorization timed out. Please start again.");
      }
    }, 1000);

    st.authInterval = setInterval(async () => {
      let checkResp;
      try {
        checkResp = await fetch(`${authBase}/check`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auth_id: data.auth_id, device_code: data.device_code }),
        });
      } catch (error) {
        finishCopilotAuthWithMessage(root, key, `Authorization check failed: ${safe(error.message)}`);
        return;
      }

      let check = null;
      try {
        check = await checkResp.json();
      } catch (_error) {
        finishCopilotAuthWithMessage(root, key, "Authorization check failed: invalid response");
        return;
      }

      if (!checkResp.ok) {
        finishCopilotAuthWithMessage(
          root,
          key,
          check?.message || check?.details || check?.error || `Authorization check failed (HTTP ${checkResp.status})`,
        );
        return;
      }

      if (check?.error && !check?.status) {
        finishCopilotAuthWithMessage(root, key, check.message || check.details || check.error);
        return;
      }

      if (!check?.status) {
        finishCopilotAuthWithMessage(root, key, "Authorization check failed: missing status");
        return;
      }

      if (check.status === "pending") return;

      if (check.status === "authorized") {
        stopCopilotPolling(root, key);
        if (instructions) instructions.classList.add("hidden");
        const token = check?.token || check?.oauth?.access || check?.oauth?.refresh || "";
        const updated = setCopilotApiKeyField(root, token);

        if (!token || !updated) {
          finishCopilotAuthWithMessage(
            root,
            key,
            "Authorization completed, but no token was returned. Please try again.",
            "error"
          );
          showToast("GitHub Copilot authorization completed, but no token was returned.");
          return;
        }

        setCopilotResultSummary(root, key, "Authorization complete. API Key field has been filled. Click Save Settings to persist.", "success");
        showToast("Authorization complete. API Key field has been filled. Click Save Settings to persist.");
      } else if (check.status === "expired" || check.status === "declined" || check.status === "failed") {
        const errorMessage = check.message || check.error || "Authorization failed";
        finishCopilotAuthWithMessage(root, key, errorMessage);
        if (typeof setCopilotResultSummary === "function") setCopilotResultSummary(root, key, errorMessage, "error");
      } else {
        const unknownStatusMessage = `Authorization check failed: unknown status ${safe(check.status)}`;
        finishCopilotAuthWithMessage(root, key, unknownStatusMessage);
        if (typeof setCopilotResultSummary === "function") setCopilotResultSummary(root, key, unknownStatusMessage, "error");
      }
    }, (Number(data.interval) || 5) * 1000);
  } catch (error) {
    showToast(`Copilot authorization failed: ${safe(error.message)}`);
    finishCopilotAuthWithMessage(root, key, `Copilot authorization failed: ${safe(error.message)}`);
  }
}

function initializeManagedSettingsRoot(root) {
  if (!root) return;
  normalizeInstanceInputs(root, "jira");
  normalizeInstanceInputs(root, "confluence");
  toggleLlmToolsEditor(root);
  normalizeLlmToolPatternInputs(root);
  window.initPasswordToggles(root);
  const provider = root.querySelector("#llm_provider");
  const modelSelect = root.querySelector("#llm_model");
  if (provider && !provider.dataset.initialProvider) provider.dataset.initialProvider = provider.value || "";
  if (modelSelect && !modelSelect.dataset.initialValue) {
    modelSelect.dataset.initialValue = modelSelect.dataset.currentValue || "";
    modelSelect.dataset.lastProvider = provider?.value || "openai";
  }
  updateModelOptions(root);
  const settingsStatus = root.querySelector("#settings-status");
  if (settingsStatus && settingsStatus.dataset.handled !== "1") {
    settingsStatus.dataset.handled = "1";
    const kind = settingsStatus.dataset.settingsStatus || "";
    const message = (settingsStatus.textContent || "").trim();
    if (kind === "success" && message) {
      showToast(message);
      if (root.id === "settings-panel-root") closeToolPanel();
    }
    if (kind === "error" && typeof settingsStatus.focus === "function") settingsStatus.focus();
  }
  if (root.dataset.actionsBound === "1") return;
  root.dataset.actionsBound = "1";
  root.addEventListener("change", (event) => {
    if (event.target?.id === "llm_provider") updateModelOptions(root);
    if (event.target?.id === "llm_model") updateTemperatureInputState(root);
    if (event.target?.name === "llm_tools_mode") toggleLlmToolsEditor(root);
    const section = sectionNameForElement(event.target);
    if (section) markManagedSectionTouched(root, section);
  });
  root.addEventListener("input", (event) => {
    const section = sectionNameForElement(event.target);
    if (section) markManagedSectionTouched(root, section);
  });
  root.addEventListener("click", async (event) => {
    const addToolBtn = event.target.closest('[data-action="add-llm-tool-pattern"]');
    if (addToolBtn) {
      event.preventDefault();
      addLlmToolPatternRow(root);
      markManagedSectionTouched(root, "llm");
      return;
    }
    const removeToolBtn = event.target.closest('[data-action="remove-llm-tool-pattern"]');
    if (removeToolBtn) {
      event.preventDefault();
      removeToolBtn.closest("[data-llm-tools-item]")?.remove();
      normalizeLlmToolPatternInputs(root);
      markManagedSectionTouched(root, "llm");
      return;
    }
    const addBtn = event.target.closest('[data-action="add-instance"]');
    if (addBtn) {
      event.preventDefault();
      const group = addBtn.dataset.group || "jira";
      addInstanceRow(root, group);
      markManagedSectionTouched(root, group);
      return;
    }
    const removeBtn = event.target.closest('[data-action="remove-instance"]');
    if (removeBtn) {
      event.preventDefault();
      const group = removeBtn.dataset.group || "jira";
      removeBtn.closest(`[data-instance-item="${group}"]`)?.remove();
      normalizeInstanceInputs(root, group);
      markManagedSectionTouched(root, group);
      return;
    }
    const testBtn = event.target.closest("[data-test-target]");
    if (testBtn) {
      event.preventDefault();
      await runManagedSettingsTest(root, testBtn.dataset.testTarget, testBtn);
      return;
    }
    const btn = event.target.closest("[data-copilot-auth-button]");
    if (btn) {
      event.preventDefault();
      await startCopilotAuth(root, btn.dataset.copilotAuthButton);
      return;
    }
    const copyBtn = event.target.closest("[data-copilot-copy-button]");
    if (copyBtn) {
      event.preventDefault();
      const authRoot = copyBtn.closest("[data-copilot-auth-root]") || document;
      const code = authRoot?.querySelector("[data-copilot-user-code]")?.textContent || "";
      if (code && navigator.clipboard) {
        await navigator.clipboard.writeText(code);
        showToast("Code copied!");
      }
    }
  });
}

function initializeManagedSettingsPanels() {
  initializeManagedSettingsRoot(document.getElementById("settings-panel-root"));
  initializeManagedSettingsRoot(document.getElementById("runtime-profile-panel-root"));
}

function initializeSettingsPanel() {
  initializeManagedSettingsRoot(document.getElementById("settings-panel-root"));
}

async function openSettings() {
  if (!state.selectedAgentId) return;
  const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
  if (!canWriteAgent(agent)) {
    setToolPanel("Settings", `<div class="portal-inline-state is-error">You do not have permission to modify this assistant's settings.</div>`);
    return;
  }


  setToolPanel("Settings", '<div class="portal-inline-state">Loading settings…</div>');

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/settings/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
    initializeSettingsPanel();
  } catch (error) {
    setToolPanel("Settings", `Failed: ${safe(error.message)}`);
  }
}

async function setModalFeedback(el, kind, text) {
  if (!el) return;
  el.textContent = text || "";
  el.className = `portal-modal-feedback${kind ? ` is-${kind}` : ""}`;
}

async function clearChat() {
  if (!guardNoActiveChatRequestForAgent(state.selectedAgentId, "clear this chat")) return;
  try {
    const sessionId = (document.getElementById("chat-session-id")?.value || "").trim();
    if (sessionId) {
      await agentApi("/api/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
    }

    updateSelectedAgentSession("");
    const chatState = getChatState();
    if (chatState) chatState.inflightThinking = null;
    removeTemporaryAssistantRows({ forceAll: true });
    clearMessageListToWelcome();
    resetChatInputHeight();
    setChatStatus("Chat cleared");
  } catch (error) {
    setChatStatus(`Clear failed: ${safe(error.message)}`);
  }
}

async function startNewChatForSelectedAgent() {
  if (!ensureRunningSelectedAssistant("start a new chat")) return;
  if (!guardNoActiveChatRequestForAgent(state.selectedAgentId, "start a new chat")) return;

  closeSessionsDrawer();
  updateSelectedAgentSession("");
  const chatState = getChatState();
  if (chatState) {
    chatState.inflightThinking = null;
    chatState.currentRequest = null;
  }
  removeTemporaryAssistantRows({ forceAll: true });
  clearMessageListToWelcome();
  setChatSubmitting(false);
  resetChatInputHeight();
  setChatStatus("New chat started");
  dom.chatInput?.focus();
}

// ===== misc actions =====
function parseAgentLifecycleAction(path = "") {
  const match = String(path || "").match(/^\/api\/agents\/([^/]+)\/(stop|restart)$/);
  if (!match) return null;
  return {
    agentId: decodeURIComponent(match[1]),
    action: match[2],
  };
}

function applyLocalAgentStatus(agentId, status, lastError = "") {
  if (!agentId) return;
  if (!state.agentStatus || typeof state.agentStatus.set !== "function") state.agentStatus = new Map();

  const normalizedStatus = String(status || "").trim().toLowerCase();
  const existing = state.agentStatus.get(agentId) || {};
  const nextStatus = { ...existing };
  if (normalizedStatus) nextStatus.status = normalizedStatus;
  if (lastError !== undefined) nextStatus.last_error = lastError || "";
  state.agentStatus.set(agentId, nextStatus);

  const agent = (state.mineAgents || []).find((item) => item.id === agentId);
  if (agent) {
    if (normalizedStatus) agent.status = normalizedStatus;
    if (lastError !== undefined) agent.last_error = lastError || "";
  }

  if (agentId === state.selectedAgentId && normalizedStatus) {
    if (dom.selectedStatus) {
      dom.selectedStatus.textContent = normalizedStatus;
      dom.selectedStatus.className = `toolbar-status-badge status-${normalizedStatus}`;
    }
    if (agent) renderAgentActions(agent, normalizedStatus);
    syncSelectedAgentChatActionControls();
  }
  renderAgentList();
}

function updateAgentRuntimeStatusCache(agentId, payload = {}) {
  if (!agentId) return;
  const status = payload?.status || "";
  const lastError = payload?.last_error || payload?.message || "";
  applyLocalAgentStatus(agentId, status, lastError);
  if (payload && typeof payload === "object" && state.agentStatus && typeof state.agentStatus.set === "function") {
    const existing = state.agentStatus.get(agentId) || {};
    state.agentStatus.set(agentId, { ...existing, ...payload, status: String(status || existing.status || "").toLowerCase() });
  }
}

async function waitForAgentRuntimeStatus(agentId, options = {}) {
  const targetStatuses = options.targetStatuses || ["running"];
  const failureStatuses = options.failureStatuses || ["failed", "stopped", "deleting"];
  const timeoutMs = Number(options.timeoutMs || 120000);
  const intervalMs = Number(options.intervalMs || 1500);
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const payload = await api(`/api/agents/${encodeURIComponent(agentId)}/status`);
    const status = String(payload?.status || "").toLowerCase();
    updateAgentRuntimeStatusCache(agentId, payload);

    if (targetStatuses.includes(status)) {
      return payload;
    }
    if (failureStatuses.includes(status)) {
      const detail = payload?.last_error || payload?.message || `Agent entered ${status}`;
      throw new Error(detail);
    }

    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  throw new Error("Timed out waiting for agent restart to finish");
}

async function pollAgentUntilRestartComplete(agentId, { intervalMs = 2000, timeoutMs = 120000 } = {}) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    let statusPayload = null;
    try {
      statusPayload = await api(`/api/agents/${encodeURIComponent(agentId)}/status`);
    } catch (error) {
      if (state.selectedAgentId === agentId) {
        setChatStatus(`Restart status check failed: ${safe(error.message)}`, true);
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      continue;
    }

    const status = String(statusPayload?.status || "").toLowerCase();
    applyLocalAgentStatus(agentId, status, statusPayload?.last_error || statusPayload?.message || "");

    if (status === "running") {
      if (state.selectedAgentId === agentId) {
        setChatStatus("Assistant restart completed.");
        showToast("Assistant restart completed.");
        await refreshAll({ preserveLayout: true });
        const chatState = ensureChatState(agentId);
        if (chatState?.sessionId) {
          try {
            await loadSessionForAgent(agentId, chatState.sessionId, { render: true });
          } catch (error) {
            console.warn("Failed to reload session after restart completed", error);
          }
        }
        ensureEventSocketForSelectedAgent();
      }
      return true;
    }

    if (status === "failed" || status === "stopped") {
      const message = statusPayload?.last_error || statusPayload?.message || `Assistant restart ended with status ${status}`;
      if (state.selectedAgentId === agentId) setChatStatus(message, true);
      showToast(message);
      return false;
    }

    if (state.selectedAgentId === agentId) {
      setChatStatus("Restarting assistant… waiting for runtime pod to become ready.");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  if (state.selectedAgentId === agentId) {
    setChatStatus("Restart is still in progress. Check Assistant details or Kubernetes rollout status.", true);
  }
  showToast("Restart is still in progress.");
  return false;
}

function agentRestartErrorMessage(error, fallback = "Assistant restart failed or timed out.") {
  const raw = String(error?.message || error || "").trim();
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
    if (Array.isArray(detail) && detail.length) return detail.map((item) => item?.msg || item).join("; ");
  } catch {}
  return raw;
}

function resetLocalChatSubmissionForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  const requestCtx = chatState.currentRequest;
  if (requestCtx) {
    clearWaitingForRuntimeEventsTimer(requestCtx);
    cancelAssistantTypewriter(requestCtx);
    if (requestCtx.abortController && typeof requestCtx.abortController.abort === "function") {
      requestCtx.abortController.abort();
    }
  }
  chatState.currentRequest = null;
  chatState.inflightThinking = null;
  chatState.pendingThinkingEvents = null;
  setChatSubmittingForAgent(agentId, false, { suppressSync: true });
}

async function action(path, method = "POST", needsConfirm = false) {
  if (needsConfirm && !confirm("Please confirm this action.")) return;
  const lifecycle = parseAgentLifecycleAction(path);
  const normalizedMethod = String(method || "POST").toUpperCase();
  const isRestartAction = Boolean(lifecycle && normalizedMethod === "POST" && lifecycle.action === "restart");

  if (isRestartAction && state.selectedAgentId === lifecycle.agentId) {
    setChatStatus("Restarting assistant…");
  }

  let result = null;
  try {
    result = await api(path, { method });
  } catch (error) {
    if (isRestartAction) {
      const message = agentRestartErrorMessage(error);
      showToast(message);
      if (state.selectedAgentId === lifecycle.agentId) {
        setChatStatus(message, true);
      }
      await refreshAll({ preserveLayout: true });
      if (state.selectedAgentId === lifecycle.agentId) {
        setChatStatus(message, true);
      }
      return;
    }
    throw error;
  }

  if (lifecycle && normalizedMethod === "POST") {
    if (lifecycle.action === "restart") {
      applyLocalAgentStatus(
        lifecycle.agentId,
        result?.status || "restarting",
        result?.last_error || result?.message || "Restart requested"
      );
      resetLocalChatSubmissionForAgent(lifecycle.agentId);
      if (state.eventWsAgentId === lifecycle.agentId) disconnectEventSocket();
      if (state.selectedAgentId === lifecycle.agentId) {
        setChatStatus("Restart requested.\nWaiting for runtime pod to restart…");
        showToast("Restart requested.");
      }

      await refreshAll({ preserveLayout: true });

      const cachedStatus = String(
        state.agentStatus?.get?.(lifecycle.agentId)?.status ||
        (state.mineAgents || []).find((item) => item.id === lifecycle.agentId)?.status ||
        ""
      ).toLowerCase();
      if (!["running", "failed", "stopped", "deleting"].includes(cachedStatus) && state.selectedAgentId === lifecycle.agentId) {
        setChatStatus("Restarting assistant… waiting for runtime pod to become ready.");
      }

      pollAgentUntilRestartComplete(lifecycle.agentId).catch((error) => {
        console.warn("Restart poll failed", error);
        if (state.selectedAgentId === lifecycle.agentId) {
          setChatStatus(`Restart polling failed: ${safe(error.message)}`, true);
        }
      });

      return;
    }

    resetLocalChatSubmissionForAgent(lifecycle.agentId);
    if (state.selectedAgentId === lifecycle.agentId) {
      setChatStatus("Assistant stopped.");
    }
  }
  await refreshAll();
}

async function loadRuntimeProfiles(force = false) {
  if (!force && state.runtimeProfiles && state.runtimeProfiles.length > 0) {
    return state.runtimeProfiles;
  }
  try {
    const profiles = await api('/api/runtime-profiles/options');
    state.runtimeProfiles = Array.isArray(profiles) ? profiles : [];
    return state.runtimeProfiles;
  } catch (_err) {
    state.runtimeProfiles = [];
    return [];
  }
}

async function loadAgentDefaults(force = false) {
  if (!force && state.agentDefaults) {
    return state.agentDefaults;
  }
  const defaults = await api("/api/agents/defaults");
  state.agentDefaults = defaults;
  return defaults;
}

function getRuntimeTypes(defaults) {
  const runtimeTypes = Array.isArray(defaults?.runtime_types) ? defaults.runtime_types : [];
  if (runtimeTypes.length) return runtimeTypes;
  return [{ value: "native", label: "EFP Native Runtime", image_repo: defaults?.image_repo || "", image_tag: defaults?.image_tag || "latest", default_mount_path: defaults?.mount_path || "/root/.efp" }];
}

function normalizeRuntimeTypeValue(value, defaults) {
  const raw = String(value || "").trim().toLowerCase();
  const runtimeTypes = getRuntimeTypes(defaults);
  if (runtimeTypes.some((item) => item.value === raw)) return raw;
  return String(defaults?.default_runtime_type || "native").trim().toLowerCase() || "native";
}

function findRuntimeTypeConfig(defaults, runtimeType) {
  const normalized = normalizeRuntimeTypeValue(runtimeType, defaults);
  return getRuntimeTypes(defaults).find((item) => item.value === normalized) || getRuntimeTypes(defaults)[0] || null;
}

function runtimeImagePreview(config) {
  const repo = String(config?.image_repo || "").trim();
  const tag = String(config?.image_tag || "latest").trim() || "latest";
  return repo ? `${repo}:${tag}` : "";
}

function isRuntimeTypeAvailable(defaults, value) {
  const target = String(value || "").trim().toLowerCase();
  return getRuntimeTypes(defaults).some((item) => String(item?.value || "").trim().toLowerCase() === target);
}

function getCreateRuntimeTypes(defaults) {
  const runtimeTypes = getRuntimeTypes(defaults);
  const opencodeTypes = runtimeTypes.filter((item) => String(item?.value || "").trim().toLowerCase() === "opencode");
  const otherTypes = runtimeTypes.filter((item) => String(item?.value || "").trim().toLowerCase() !== "opencode");
  return [...opencodeTypes, ...otherTypes];
}

function getCreateDefaultRuntimeType(defaults) {
  if (isRuntimeTypeAvailable(defaults, "opencode")) {
    return "opencode";
  }
  const normalized = normalizeRuntimeTypeValue(defaults?.default_runtime_type || "opencode", defaults);
  if (isRuntimeTypeAvailable(defaults, normalized)) {
    return normalized;
  }
  const firstRuntimeType = getCreateRuntimeTypes(defaults)[0]?.value;
  return normalizeRuntimeTypeValue(firstRuntimeType || normalized, defaults);
}

function runtimeTypeDescription(item) {
  const value = String(item?.value || "").trim().toLowerCase();
  if (value === "opencode") return "Recommended default for new assistants.";
  if (value === "native") return "Use the native EFP Python runtime.";
  return "Use this runtime for the assistant.";
}

function populateRuntimeTypeSelect(selectEl, defaults, selectedValue = "") {
  if (!selectEl) return;
  const runtimeTypes = getRuntimeTypes(defaults);
  const selected = normalizeRuntimeTypeValue(selectedValue || defaults?.default_runtime_type || "native", defaults);
  selectEl.innerHTML = runtimeTypes.map((item) => {
    const value = String(item.value || "").trim();
    const label = item.label || value;
    const selectedAttr = value === selected ? " selected" : "";
    return `<option value="${escapeHtmlAttr(value)}"${selectedAttr}>${safe(label)}</option>`;
  }).join("");
  selectEl.value = selected;
}

function populateRuntimeTypeRadioGroup(groupEl, defaults, selectedValue = "") {
  if (!groupEl) return;
  const runtimeTypes = getCreateRuntimeTypes(defaults);
  const selected = normalizeRuntimeTypeValue(selectedValue || getCreateDefaultRuntimeType(defaults), defaults);
  groupEl.innerHTML = runtimeTypes.map((item) => {
    const value = String(item.value || "").trim();
    const label = item.label || value;
    const checkedAttr = value === selected ? " checked" : "";
    const image = runtimeImagePreview(item);
    const mountPath = item.default_mount_path || "";
    const meta = image
      ? `Default image: ${image}${mountPath ? ` · Mount: ${mountPath}` : ""}`
      : runtimeTypeDescription(item);

    return `
      <label class="runtime-type-radio-option">
        <input
          class="runtime-type-radio-input"
          type="radio"
          name="runtime_type"
          value="${escapeHtmlAttr(value)}"${checkedAttr}
        />
        <span class="runtime-type-radio-card">
          <span class="runtime-type-radio-control" aria-hidden="true"></span>
          <span class="runtime-type-radio-copy">
            <span class="runtime-type-radio-title">${safe(label)}</span>
            <span class="runtime-type-radio-description">${safe(meta)}</span>
          </span>
        </span>
      </label>
    `;
  }).join("");
}

function updateCreateRuntimeTypeHint(form, defaults) {
  const runtimeTypeControl = form?.elements?.["runtime_type"];
  const hintEl = document.getElementById("create-runtime-type-hint");
  const runtimeTypeValue = runtimeTypeControl?.value || getCreateDefaultRuntimeType(defaults);
  const config = findRuntimeTypeConfig(defaults, runtimeTypeValue);
  const image = runtimeImagePreview(config);
  const mountPath = config?.default_mount_path || defaults?.mount_path || "";
  if (hintEl) hintEl.textContent = image ? `Default image: ${image}${mountPath ? ` · Mount: ${mountPath}` : ""}` : "";
}

function applyCreateAgentDefaults(form, defaults) {
  if (!form?.elements) return;
  const repoInput = form.elements["skill_repo_url"];
  if (repoInput) {
    const repoDefault = defaults?.default_skill_repo_url || "";
    repoInput.value = repoDefault;
    repoInput.defaultValue = repoDefault;
  }
  const branchInput = form.elements["skill_branch"];
  if (branchInput) {
    const branchDefault = defaults?.default_skill_branch || "";
    branchInput.value = branchDefault;
    branchInput.defaultValue = branchDefault;
    branchInput.placeholder = branchDefault ? `Configured default branch (${branchDefault})` : "Configured default branch";
  }
  const runtimeProfileSelect = form.elements["runtime_profile_id"];
  if (runtimeProfileSelect) {
    const defaultRuntimeProfileId = defaults?.default_runtime_profile_id || "";
    if (defaultRuntimeProfileId) runtimeProfileSelect.value = defaultRuntimeProfileId;
  }
  const runtimeTypeGroup = document.getElementById("create-runtime-type-select");
  populateRuntimeTypeRadioGroup(runtimeTypeGroup, defaults, getCreateDefaultRuntimeType(defaults));
  updateCreateRuntimeTypeHint(form, defaults);
}

function populateRuntimeProfileSelect(selectEl, selectedId = '') {
  if (!selectEl) return;
  const profiles = state.runtimeProfiles || [];
  if (!profiles.length) {
    selectEl.innerHTML = '<option value="" disabled selected>No runtime profiles available</option>';
    return;
  }
  selectEl.innerHTML = profiles.map((profile) => {
    const selected = selectedId && selectedId === profile.id ? ' selected' : '';
    const suffix = profile.is_default ? ' (Default)' : '';
    return `<option value="${escapeHtmlAttr(profile.id)}"${selected}>${safe((profile.name || 'Runtime Profile') + suffix)}</option>`;
  }).join('');
  if (!selectedId) {
    const defaultProfile = profiles.find((item) => item.is_default);
    selectEl.value = (defaultProfile || profiles[0]).id;
  }
}

function renderRuntimeProfileList(errorMessage = "") {
  if (!dom.runtimeProfileNavList) return;
  if (errorMessage) {
    dom.runtimeProfileNavList.innerHTML = `<div class="portal-inline-state is-error">${safe(errorMessage)}</div>`;
    return;
  }
  if (!state.runtimeProfiles.length) {
    dom.runtimeProfileNavList.innerHTML = '<div class="portal-bundle-list-state">No runtime profiles found.</div>';
    return;
  }
  dom.runtimeProfileNavList.innerHTML = "";
  state.runtimeProfiles.forEach((profile) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `portal-bundle-row${state.selectedRuntimeProfileId === profile.id ? " is-active" : ""}`;
    row.innerHTML = `
      <div class="portal-bundle-title">${safe(profile.name || 'Runtime Profile')}</div>
      <div class="portal-bundle-meta">Revision ${safe(String(profile.revision || 1))}${profile.is_default ? ' · Default' : ''}</div>
    `;
    row.addEventListener("click", async () => {
      state.selectedRuntimeProfileId = profile.id;
      renderRuntimeProfileList();
      await openRuntimeProfileInMain(profile.id);
    });
    dom.runtimeProfileNavList.append(row);
  });
}

async function loadRuntimeProfilePanelContent(profileId, { updateRoute = true } = {}) {
  if (!profileId) return;
  state.selectedRuntimeProfileId = profileId;
  renderRuntimeProfileList();
  await htmx.ajax("GET", `/app/runtime-profiles/${encodeURIComponent(profileId)}/panel`, { target: "#workspace-detail-content", swap: "innerHTML" });
  if (typeof initializeManagedSettingsPanels === "function") initializeManagedSettingsPanels();
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "runtime-profile-detail";
  syncMainHeader();
}

async function openRuntimeProfileInMain(profileId, { ensureSection = true, updateRoute = true } = {}) {
  if (!profileId) return;
  if (ensureSection) {
    await setActiveNavSection("runtime-profiles", { toggleIfSame: false, updateRoute: false });
  }
  await loadRuntimeProfilePanelContent(profileId, { updateRoute: false });
  if (updateRoute && !isApplyingPortalRoute) {
    commitPortalRoute({ section: "runtime-profiles", runtimeProfileId: profileId });
  }
}

async function refreshRuntimeProfileList({ preserveSelection = true } = {}) {
  await loadRuntimeProfiles(true);
  const previousSelected = state.selectedRuntimeProfileId;
  if (!preserveSelection || !state.runtimeProfiles.some((item) => item.id === previousSelected)) {
    state.selectedRuntimeProfileId = (state.runtimeProfiles.find((item) => item.is_default) || state.runtimeProfiles[0] || {}).id || null;
  }
  renderRuntimeProfileList();
}

async function loadAutomationRules() {
  try {
    const rules = await api("/api/automation-rules");
    state.automations = Array.isArray(rules) ? rules : [];
  } catch (_err) {
    state.automations = [];
  }
  renderAutomationRuleNavList(state.automations);
  return state.automations;
}

function renderAutomationRuleNavList(rules) {
  if (!dom.automationRuleNavList) return;
  const items = Array.isArray(rules) ? rules : [];
  if (!items.length) {
    dom.automationRuleNavList.innerHTML = '<div class="portal-bundle-list-state">No automations found.</div>';
    return;
  }
  dom.automationRuleNavList.innerHTML = "";
  items.forEach((rule) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `portal-bundle-row${state.selectedAutomationRuleId === rule.id ? " is-active" : ""}`;
    row.innerHTML = `
      <div class="portal-bundle-title">${safe(rule.name || "Automation")}</div>
      <div class="portal-bundle-meta">${rule.enabled ? "Enabled" : "Disabled"}</div>
    `;
    row.addEventListener("click", () => openAutomationRulePanel(rule.id));
    dom.automationRuleNavList.append(row);
  });
}

async function openAutomationRulePanel(ruleId, { updateRoute = true } = {}) {
  if (!ruleId) return;
  try {
    state.selectedAutomationRuleId = ruleId;
    renderAutomationRuleNavList(state.automations);
    const detail = await api(`/api/automation-rules/${encodeURIComponent(ruleId)}`);
    const runs = await loadAutomationRuleRuns(ruleId);
    const events = await loadAutomationRuleEvents(ruleId);
    const scope = _safeJson(detail.scope_json) || {};
    const trigger = _safeJson(detail.trigger_config_json) || {};
    const schedule = _safeJson(detail.schedule_json) || {};
    const taskConfig = _safeJson(detail.task_config_json) || {};
    const targetAgent = (state.mineAgents || []).find((item) => item.id === detail.target_agent_id);
    const runsRows = (runs || []).map((run) => `
      <tr><td>${safe(run.status || "-")}</td><td>${safe(String(run.found_count ?? 0))}</td><td>${safe(String(run.created_task_count ?? 0))}</td><td>${safe(String(run.skipped_count ?? 0))}</td><td>${safe(run.error_message || "-")}</td><td>${safe(run.started_at || "-")}</td><td>${safe(run.finished_at || "-")}</td></tr>
    `).join("");
    const eventRows = (events || []).map((event) => `
      <tr><td>${safe(event.status || "-")}</td><td>${safe(event.dedupe_key || "-")}</td><td>${safe(event.task_id || "-")}</td><td>${safe(event.error_message || "-")}</td><td>${safe(event.created_at || "-")}</td><td>${safe(event.updated_at || "-")}</td></tr>
    `).join("");
    dom.workspaceDetailContent.innerHTML = `
      <div class="portal-panel-stack">
        <h3>${safe(detail.name)}</h3>
        <div class="portal-inline-state">Enabled: <strong>${detail.enabled ? "true" : "false"}</strong></div>
        <div class="portal-inline-state">Task Template: <strong>${safe(detail.task_template_id || "-")}</strong></div>
        <div class="portal-inline-state">Repository: <strong>${safe(`${scope.owner || "-"} / ${scope.repo || "-"}`)}</strong></div>
        ${detail.task_template_id === "github_comment_mention"
          ? `<div class="portal-inline-state">Mention target: <strong>${safe(trigger.mention_target || "-")}</strong></div>
             <div class="portal-inline-state">Surfaces: <strong>${safe((scope.surfaces || []).join(", ") || "-")}</strong></div>
             <div class="portal-inline-state">Reply mode: <strong>${safe(taskConfig.reply_mode || "same_surface")}</strong></div>`
          : `<div class="portal-inline-state">Review target: <strong>${safe(`${trigger.review_target_type || "-"} / ${trigger.review_target || "-"}`)}</strong></div>`}
        <div class="portal-inline-state">Target agent: <strong>${safe(`${targetAgent?.name || "-"} (${detail.target_agent_id})`)}</strong></div>
        <div class="portal-inline-state">Interval seconds: <strong>${safe(String(schedule.interval_seconds || 60))}</strong></div>
        <div class="portal-inline-state">Last run at: <strong>${safe(detail.last_run_at || "-")}</strong></div>
        <div class="portal-inline-state">Next run at: <strong>${safe(detail.next_run_at || "-")}</strong></div>
        <div class="flex gap-2">
          <button class="portal-btn is-secondary" type="button" data-run-automation-once="${safe(detail.id)}">Run once</button>
          <button class="portal-btn is-secondary" type="button" data-toggle-automation-enabled="${safe(detail.id)}" data-next-enabled="${detail.enabled ? "false" : "true"}">${detail.enabled ? "Disable" : "Enable"}</button>
          <button class="portal-btn" type="button" data-delete-automation-rule="${safe(detail.id)}">Delete</button>
        </div>
        <h6>Recent Runs</h6>
        <table class="portal-table"><thead><tr><th>Status</th><th>Found</th><th>Created</th><th>Skipped</th><th>Error</th><th>Started</th><th>Finished</th></tr></thead><tbody>${runsRows || '<tr><td colspan="7">No runs</td></tr>'}</tbody></table>
        <h6>Recent Events</h6>
        <table class="portal-table"><thead><tr><th>Status</th><th>Dedupe key</th><th>Task</th><th>Error</th><th>Created</th><th>Updated At</th></tr></thead><tbody>${eventRows || '<tr><td colspan="6">No events</td></tr>'}</tbody></table>
      </div>
    `;
    setMainView("detail");
    dom.workspaceDetailContent.dataset.workspaceState = "automation-rule-detail";
    if (updateRoute && !isApplyingPortalRoute) {
      commitPortalRoute({ section: "automations", automationRuleId: ruleId });
    }
  } catch (error) {
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed to load automation: ${safe(error.message)}</div>`;
  }
}

function openCreateAutomationRuleModal() {
  const mineAgents = state.mineAgents || [];
  if (!mineAgents.length) {
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">No agents available. Create or enable an agent first.</div>`;
    return;
  }
  const agentOptions = mineAgents
    .map((agent) => `<option value="${escapeHtmlAttr(agent.id)}">${safe(agent.name || agent.id)}</option>`)
    .join("");
  dom.workspaceDetailContent.innerHTML = `
    <div class="portal-panel-stack">
      <h3>Create Automation</h3>
      <form id="create-automation-inline-form" class="portal-panel-stack">
        <label class="portal-form-label"><span class="portal-form-label">Name</span><input class="portal-form-input" name="name" value="Review EFP PRs" required /></label>
        <section class="portal-panel-section">
          <h4>Trigger</h4>
          <label class="portal-form-label"><span class="portal-form-label">Trigger</span><select class="portal-form-select" name="trigger_type"><option value="github_pr_review_requested">GitHub PR review requested</option><option value="github_comment_mention">GitHub comment mention</option></select></label>
          <label class="portal-form-label"><span class="portal-form-label">Owner</span><input class="portal-form-input" name="owner" required /></label>
          <label class="portal-form-label"><span class="portal-form-label">Mode</span><select class="portal-form-select" name="scope_mode"><option value="repo">repo</option><option value="org">org</option><option value="account_notifications">account_notifications</option></select></label>
          <label class="portal-form-label"><span class="portal-form-label">Repo</span><input class="portal-form-input" name="repo" required /></label>
          <div data-pr-only="1">
            <label class="portal-form-label"><span class="portal-form-label">Review target type</span><select class="portal-form-select" name="review_target_type"><option value="user">user</option><option value="team">team</option></select></label>
            <label class="portal-form-label"><span class="portal-form-label">Review target</span><input class="portal-form-input" name="review_target" /></label>
          </div>
          <div data-mention-only="1" style="display:none">
            <label class="portal-form-label"><span class="portal-form-label">Mention target</span><input class="portal-form-input" name="mention_target" /></label>
            <label><input type="checkbox" name="surfaces" value="issue_comment" checked /> issue_comment</label>
            <label><input type="checkbox" name="surfaces" value="pull_request_review_comment" checked /> pull_request_review_comment</label>
            <label><input type="checkbox" name="surfaces" value="commit_comment" /> commit_comment</label>
            <label><input type="checkbox" name="surfaces" value="discussion_comment" /> discussion_comment</label>
            <div data-org-account-only="1" style="display:none">
              <label class="portal-form-label"><span class="portal-form-label">Repo include</span><input class="portal-form-input" name="repo_include" placeholder="api-*,portal" /></label>
              <label class="portal-form-label"><span class="portal-form-label">Repo exclude</span><input class="portal-form-input" name="repo_exclude" placeholder="archived-*" /></label>
              <label><input type="checkbox" name="include_forks" /> include_forks</label>
              <label><input type="checkbox" name="include_archived" /> include_archived</label>
              <label class="portal-form-label"><span class="portal-form-label">Max repos/run</span><input class="portal-form-input" name="max_repos_per_run" type="number" value="20" min="1" max="200" /></label>
            </div>
          </div>
          <label class="portal-form-label"><span class="portal-form-label">Interval seconds</span><input class="portal-form-input" name="interval_seconds" type="number" value="60" min="30" max="3600" /></label>
        </section>
        <section class="portal-panel-section">
          <h4>Task Template</h4>
          <label class="portal-form-label"><span class="portal-form-label">Template</span><select class="portal-form-select" name="task_template_id"><option value="github_pr_review">github_pr_review</option><option value="github_comment_mention">github_comment_mention</option></select></label>
        </section>
        <section class="portal-panel-section">
          <h4>Agent & Task Defaults</h4>
          <label class="portal-form-label"><span class="portal-form-label">Agent</span><select class="portal-form-select" name="target_agent_id" required>${agentOptions}</select></label>
          <div data-pr-only="1">
            <label class="portal-form-label"><span class="portal-form-label">Review event</span><select class="portal-form-select" name="review_event"><option value="COMMENT">COMMENT</option><option value="APPROVE">APPROVE</option><option value="REQUEST_CHANGES">REQUEST_CHANGES</option></select></label>
            <label class="portal-form-label"><span class="portal-form-label">Writeback mode</span><input class="portal-form-input" name="writeback_mode" /></label>
          </div>
          <div data-mention-only="1" style="display:none">
            <label class="portal-form-label"><span class="portal-form-label">Reply mode</span><select class="portal-form-select" name="reply_mode"><option value="same_surface">same_surface</option><option value="timeline">timeline</option></select></label>
          </div>
        </section>
        <button class="portal-btn is-primary" type="submit">Create</button>
      </form>
    </div>
  `;
  const form = document.getElementById("create-automation-inline-form");
  const triggerSel = form?.querySelector('select[name="trigger_type"]');
  const tplSel = form?.querySelector('select[name="task_template_id"]');
  const toggle = () => {
    const isMention = (tplSel?.value || "") === "github_comment_mention";
    form?.querySelectorAll("[data-pr-only='1']").forEach((el) => { el.style.display = isMention ? "none" : ""; });
    form?.querySelectorAll("[data-mention-only='1']").forEach((el) => { el.style.display = isMention ? "" : "none"; });
    if (triggerSel) triggerSel.value = isMention ? "github_comment_mention" : "github_pr_review_requested";
    const mode = String(form?.querySelector('select[name=\"scope_mode\"]')?.value || "repo");
    form?.querySelectorAll("[data-org-account-only='1']").forEach((el) => { el.style.display = isMention && ["org","account_notifications"].includes(mode) ? "" : "none"; });
    const repoInput = form?.querySelector('input[name=\"repo\"]');
    if (repoInput) repoInput.required = !isMention || mode === "repo";
    const ownerInput = form?.querySelector('input[name="owner"]');
    if (ownerInput) ownerInput.required = !isMention || mode !== "account_notifications";
  };
  triggerSel?.addEventListener("change", () => { if (tplSel) tplSel.value = triggerSel.value === "github_comment_mention" ? "github_comment_mention" : "github_pr_review"; toggle(); });
  tplSel?.addEventListener("change", toggle);
  form?.querySelector('select[name="scope_mode"]')?.addEventListener("change", toggle);
  toggle();
}

async function submitCreateAutomationRule(formEl) {
  const fd = new FormData(formEl);
  const taskTemplateId = String(fd.get("task_template_id") || "github_pr_review");
  if (taskTemplateId === "github_comment_mention") {
    const parseCommaList = (raw) => String(raw || "").split(",").map((s) => s.trim()).filter(Boolean);
    const selectedSurfaces = fd.getAll("surfaces").map((s) => String(s));
    const mode = String(fd.get("scope_mode") || "repo");
    const baseSurfaces = selectedSurfaces.length ? selectedSurfaces : ["issue_comment", "pull_request_review_comment"];
    const selector = { include: parseCommaList(fd.get("repo_include")), exclude: parseCommaList(fd.get("repo_exclude")), include_forks: fd.get("include_forks") !== null, include_archived: fd.get("include_archived") !== null };
    const scope = mode === "org"
      ? { mode: "org", owner: String(fd.get("owner") || ""), repo_selector: selector, surfaces: baseSurfaces }
      : mode === "account_notifications"
        ? { mode: "account_notifications", surfaces: baseSurfaces, notification_reasons: ["mention", "team_mention"], repo_selector: selector }
        : { mode: "repo", owner: String(fd.get("owner") || ""), repo: String(fd.get("repo") || ""), surfaces: baseSurfaces };
    const payload = {
      name: String(fd.get("name") || ""), target_agent_id: String(fd.get("target_agent_id") || ""), enabled: true, source_type: "github",
      trigger_type: "github_comment_mention", task_template_id: "github_comment_mention",
      scope,
      trigger_config: { mention_target_type: "user", mention_target: String(fd.get("mention_target") || ""), ignore_self_comments: true, ignore_bot_comments: true, ignore_efp_auto_reply_marker: true, strip_code_blocks_before_matching: true },
      task_input_defaults: { skill_name: "handle-triggered-event", execution_mode: "chat_tool_loop", reply_mode: String(fd.get("reply_mode") || "same_surface") },
      schedule: { interval_seconds: Number(fd.get("interval_seconds") || 60), initial_lookback_seconds: 0, overlap_seconds: 120, max_pages_per_surface: 10, max_repos_per_run: Number(fd.get("max_repos_per_run") || 20), max_notification_pages_per_run: 5, commit_comment_initial_tail_pages: 2, max_discussion_pages_per_run: 5, discussion_comments_tail_count: 100, discussion_replies_tail_count: 50 },
    };
    const created = await api("/api/automation-rules", { method: "POST", body: JSON.stringify(payload) });
    await loadAutomationRules();
    await openAutomationRulePanel(created.id);
    return;
  }
  const payload = {
    name: String(fd.get("name") || ""),
    target_agent_id: String(fd.get("target_agent_id") || ""),
    enabled: true,
    source_type: "github",
    trigger_type: String(fd.get("trigger_type") || "github_pr_review_requested"),
    task_template_id: taskTemplateId,
    scope: { owner: String(fd.get("owner") || ""), repo: String(fd.get("repo") || "") },
    trigger_config: { review_target_type: String(fd.get("review_target_type") || "user"), review_target: String(fd.get("review_target") || "") },
    task_input_defaults: {
      review_event: String(fd.get("review_event") || "COMMENT"),
      skill_name: "review-pull-request",
      writeback_mode: String(fd.get("writeback_mode") || "").trim() || undefined,
    },
    schedule: { interval_seconds: Number(fd.get("interval_seconds") || 60) },
  };
  const created = await api("/api/automation-rules", { method: "POST", body: JSON.stringify(payload) });
  await loadAutomationRules();
  await openAutomationRulePanel(created.id);
}

async function runAutomationRuleOnce(ruleId) {
  const result = await api(`/api/automation-rules/${encodeURIComponent(ruleId)}/run-once`, { method: "POST" });
  showToast(`Run finished: created ${result.created_task_count} task(s)`);
  await openAutomationRulePanel(ruleId);
}

async function toggleAutomationRuleEnabled(ruleId, enabled) {
  await api(`/api/automation-rules/${encodeURIComponent(ruleId)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled: !!enabled }),
  });
  await loadAutomationRules();
  await openAutomationRulePanel(ruleId);
}

async function deleteAutomationRule(ruleId) {
  await api(`/api/automation-rules/${encodeURIComponent(ruleId)}`, { method: "DELETE" });
  await loadAutomationRules();
  if (state.automations.length) {
    await openAutomationRulePanel(state.automations[0].id);
  } else {
    renderWorkspaceDetailPlaceholder("No automations found.", "automations-placeholder");
  }
}

async function loadAutomationRuleRuns(ruleId) {
  return api(`/api/automation-rules/${encodeURIComponent(ruleId)}/runs`);
}

async function loadAutomationRuleEvents(ruleId) {
  return api(`/api/automation-rules/${encodeURIComponent(ruleId)}/events`);
}

function _safeJson(raw) {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (_error) {
    return null;
  }
}



async function openTaskCreatePanelInMain() {
  if (!dom.workspaceDetailContent) return;
  await setActiveNavSection("tasks", { toggleIfSame: false });
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "task-create";
  dom.workspaceDetailContent.innerHTML = '<div class="portal-inline-state">Loading task create form…</div>';
  await htmx.ajax("GET", "/app/tasks/create/panel", { target: "#workspace-detail-content", swap: "innerHTML" });
  const formEl = dom.workspaceDetailContent.querySelector("#create-task-from-template-form");
  if (formEl) updateCreateTaskTemplateFieldVisibility(formEl);
}

async function submitCreateTaskFromTemplate(formEl) {
  const fd = new FormData(formEl);
  const payloadInput = {
    owner: String(fd.get("owner") || "").trim(),
    repo: String(fd.get("repo") || "").trim(),
    pull_number: Number(fd.get("pull_number") || 0) || undefined,
    review_event: String(fd.get("review_event") || "").trim(),
    writeback_mode: String(fd.get("writeback_mode") || "").trim(),
    head_sha: String(fd.get("head_sha") || "").trim(),
    bundle_template_id: String(fd.get("bundle_template_id") || "").trim(),
    bundle_ref: {
      repo: String(fd.get("bundle_repo") || "").trim(),
      path: String(fd.get("bundle_path") || "").trim(),
      skill_branch: String(fd.get("bundle_branch") || "").trim(),
    },
    manifest_ref: {
      repo: String(fd.get("manifest_repo") || "").trim() || String(fd.get("bundle_repo") || "").trim(),
      path: String(fd.get("manifest_path") || "").trim() || String(fd.get("bundle_path") || "").trim(),
      skill_branch: String(fd.get("manifest_branch") || "").trim() || String(fd.get("bundle_branch") || "").trim(),
    },
    sources: {
      jira: splitLines(fd.get("jira_sources")),
      confluence: splitLines(fd.get("confluence_sources")),
      github_docs: splitLines(fd.get("github_doc_sources")),
      figma: splitLines(fd.get("figma_sources")),
    },
  };
  const created = await api("/api/agent-tasks/from-template", {
    method: "POST",
    body: JSON.stringify({
      template_id: String(fd.get("template_id") || "").trim(),
      assignee_agent_id: String(fd.get("assignee_agent_id") || "").trim(),
      dispatch_immediately: fd.get("dispatch_immediately") !== null,
      input: payloadInput,
    }),
  });
  await refreshMyTasks();
  await openTaskDetailInMain(created.id);
}

function splitLines(value) {
  const raw = String(value || "");
  return raw.split(/\n|,/).map((item) => item.trim()).filter(Boolean);
}

function updateCreateTaskTemplateFieldVisibility(formEl) {
  if (!formEl) return;
  const templateId = String(formEl.querySelector('[name="template_id"]')?.value || "").trim();
  const bundleFields = formEl.querySelector("[data-task-bundle-fields]");
  const sourcesFields = formEl.querySelector("[data-task-sources-fields]");
  const githubFields = formEl.querySelector("[data-task-github-fields]");
  const bundleTemplateSelect = formEl.querySelector('[name="bundle_template_id"]');

  const isGithub = templateId === "github_pr_review";
  const bundleTemplateIds = new Set([
    "collect_requirements_to_bundle",
    "design_test_cases_from_bundle",
    "collect_research_notes_to_bundle",
    "generate_implementation_plan_from_bundle",
    "generate_runbook_from_bundle",
  ]);
  const sourcesTemplateIds = new Set(["collect_requirements_to_bundle", "collect_research_notes_to_bundle"]);
  const isBundle = bundleTemplateIds.has(templateId);
  const needsSources = sourcesTemplateIds.has(templateId);

  if (bundleFields) bundleFields.classList.toggle("hidden", !isBundle);
  if (sourcesFields) sourcesFields.classList.toggle("hidden", !needsSources);
  if (githubFields) githubFields.classList.toggle("hidden", !isGithub);

  const bundleDefaults = {
    collect_requirements_to_bundle: "requirement.v1",
    design_test_cases_from_bundle: "requirement.v1",
    collect_research_notes_to_bundle: "research.v1",
    generate_implementation_plan_from_bundle: "development.v1",
    generate_runbook_from_bundle: "operations.v1",
  };
  if (bundleTemplateSelect && bundleDefaults[templateId]) {
    bundleTemplateSelect.value = bundleDefaults[templateId];
  }
}

async function openEditDialog(agent) {
  await Promise.all([loadRuntimeProfiles(true), loadAgentDefaults()]);
  const form = document.getElementById("edit-form");
  if (form && form.elements) {
    if (form.elements["id"]) form.elements["id"].value = agent.id ?? "";
    if (form.elements["name"]) form.elements["name"].value = agent.name || "";
    if (form.elements["runtime_type"]) {
      populateRuntimeTypeSelect(form.elements["runtime_type"], state.agentDefaults || {}, agent.runtime_type || "native");
    }
    if (form.elements["skill_repo_url"]) form.elements["skill_repo_url"].value = agent.skill_repo_url || "";
    if (form.elements["skill_branch"]) {
      form.elements["skill_branch"].value = agent.skill_branch || "";
      form.elements["skill_branch"].placeholder = state.agentDefaults?.default_skill_branch
        ? `Configured default branch (${state.agentDefaults.default_skill_branch})`
        : "Configured default branch";
    }
    if (form.elements["runtime_profile_id"]) populateRuntimeProfileSelect(form.elements["runtime_profile_id"], agent.runtime_profile_id || "");
  }

  // Show the modal
  const editModal = document.getElementById("edit-modal");
  if (editModal) {
    editModal.classList.remove("hidden");
    editModal.setAttribute("aria-hidden", "false");
  }
}

// Open message edit modal
function openEditMessageModal(messageId, currentContent) {
  document.getElementById("edit-message-id").value = messageId;
  document.getElementById("edit-message-content").value = currentContent;
  document.getElementById("message-edit-modal")?.classList.remove("hidden");
  document.getElementById("message-edit-modal")?.setAttribute("aria-hidden", "false");
  document.getElementById("edit-message-content")?.focus();
  
  // Close modal when clicking outside (on backdrop)
  const modal = document.getElementById("message-edit-modal");
  const handleOutsideClick = (e) => {
    if (e.target === modal) {
      closeEditMessageModal();
      modal.removeEventListener("click", handleOutsideClick);
    }
  };
  modal?.addEventListener("click", handleOutsideClick);
  
  // Close modal on ESC key
  const handleEsc = (e) => {
    if (e.key === "Escape") {
      closeEditMessageModal();
      document.removeEventListener("keydown", handleEsc);
    }
  };
  document.addEventListener("keydown", handleEsc);
}

function closeEditMessageModal() {
  document.getElementById("message-edit-modal")?.classList.add("hidden");
  document.getElementById("message-edit-modal")?.setAttribute("aria-hidden", "true");
}

function getUserArticleContent(article) {
  const contentEl = article?.querySelector(".message-body, .whitespace-pre-wrap");
  return contentEl ? contentEl.textContent || "" : "";
}

function findPrecedingUserArticle(row) {
  let current = row?.previousElementSibling || null;
  while (current) {
    const userArticle = current.querySelector('article[data-local-user="1"]');
    if (userArticle) return userArticle;
    current = current.previousElementSibling;
  }
  return null;
}

function getDisplayBlockCopyText(block) {
  if (!block || typeof block !== "object") return "";
  const type = String(block.type || "").trim().toLowerCase();

  if (type === "code") {
    return pickFirstMeaningfulBlockValue(block, ["code", "content", "text", "message", "output", "result", "value"]);
  }

  if (type === "table") {
    const direct = getDisplayBlockText(block);
    if (direct) return direct;

    const headers = Array.isArray(block.headers) ? block.headers : (Array.isArray(block.columns) ? block.columns : []);
    const rows = Array.isArray(block.rows) ? block.rows : [];
    const lines = [];
    if (headers.length) {
      lines.push(headers.map((cell) => String(cell ?? "")).join(" | "));
    }
    rows.forEach((row) => {
      if (Array.isArray(row)) {
        lines.push(row.map((cell) => String(cell ?? "")).join(" | "));
      }
    });
    return lines.join("\n");
  }

  return getDisplayBlockText(block);
}

function getAssistantCopyText(article) {
  if (article?.dataset?.copyText && article.dataset.copyText.trim()) return article.dataset.copyText;
  const markdownEl = article?.querySelector(".message-markdown");
  const rawMarkdown = markdownEl?.dataset?.md || "";
  if (rawMarkdown.trim()) return rawMarkdown;

  const blocks = parseDisplayBlocks(markdownEl?.dataset?.displayBlocks || "");
  const blockText = blocks
    .map(getDisplayBlockCopyText)
    .filter((text) => String(text || "").trim().length > 0)
    .join("\n\n");
  if (blockText) return blockText;

  return article?.textContent || "";
}

function hasFollowingMessageRows(row) {
  let cursor = row?.nextElementSibling || null;
  while (cursor) {
    if (cursor.matches?.(".message-row")) return true;
    cursor = cursor.nextElementSibling;
  }
  return false;
}

function truncateDomFromUserArticle(userArticle) {
  const selectedChatState = getChatState();
  if (!dom.messageList || !userArticle) {
    clearMessageListToWelcome();
    return;
  }

  const rows = Array.from(dom.messageList.querySelectorAll(".message-row"));
  const targetRow = userArticle.closest(".message-row");
  const targetRowIndex = rows.indexOf(targetRow);

  if (!targetRow || targetRowIndex < 0) {
    clearMessageListToWelcome();
    return;
  }

  for (let i = rows.length - 1; i >= targetRowIndex; i -= 1) {
    rows[i].remove();
  }

}

function getRuntimeMutationErrorMessage(response, result, fallbackMessage = "Failed to update message") {
  const error = String(result?.detail || result?.error || "").trim();
  if (response?.status === 501 || error === "unsupported_by_opencode_adapter_mvp") {
    return "This runtime does not support retry/edit yet. Please refresh the session after the runtime is upgraded, or start a new chat.";
  }
  if (error) return error;
  return fallbackMessage;
}

const EDITED_MESSAGE_POLL_INTERVAL_MS = 2000;
const EDITED_MESSAGE_POLL_TIMEOUT_MS = 10 * 60 * 1000;

function getRuntimeMessageId(message = {}) {
  return String(
    message?.id
    || message?.message_id
    || message?.messageId
    || message?.metadata?.opencode_message_id
    || ""
  );
}

function isRenderableAssistantSessionMessage(message = {}) {
  if (message?.role !== "assistant") return false;
  if (String(message?.content || "").trim()) return true;
  if (Array.isArray(message?.display_blocks) && message.display_blocks.length > 0) return true;
  const state = String(message?.completion_state || message?.completionState || "").trim().toLowerCase();
  return state === "completed" || state === "success";
}

function sessionMessageRequestId(message = {}) {
  const metadata = message?.metadata && typeof message.metadata === "object" ? message.metadata : {};
  return String(
    message?.request_id
    || message?.client_request_id
    || metadata.request_id
    || metadata.client_request_id
    || ""
  );
}

function findAssistantAfterEditedUserMessage(messages = [], replacementUserMessageId = "", requestId = "") {
  const normalizedMessages = Array.isArray(messages) ? messages : [];
  const replacementId = String(replacementUserMessageId || "");
  const normalizedRequestId = String(requestId || "");

  if (replacementId) {
    const editedUserIndex = normalizedMessages.findIndex((message) => (
      message?.role === "user" && getRuntimeMessageId(message) === replacementId
    ));
    if (editedUserIndex >= 0) {
      return normalizedMessages
        .slice(editedUserIndex + 1)
        .find((message) => isRenderableAssistantSessionMessage(message)) || null;
    }
  }

  if (normalizedRequestId) {
    return normalizedMessages.find((message) => (
      isRenderableAssistantSessionMessage(message)
      && sessionMessageRequestId(message) === normalizedRequestId
    )) || null;
  }

  return null;
}

function getEditedSessionFailureMessage(data = {}) {
  const metadata = data?.metadata && typeof data.metadata === "object" ? data.metadata : {};
  const runtimeEvents = Array.isArray(metadata.runtime_events)
    ? metadata.runtime_events
    : (Array.isArray(data?.runtime_events) ? data.runtime_events : []);
  const lastEvent = runtimeEvents[runtimeEvents.length - 1] || {};
  const lastEventData = lastEvent?.data && typeof lastEvent.data === "object" ? lastEvent.data : {};
  const completionState = String(
    data?.completion_state
    || data?.completionState
    || metadata.completion_state
    || metadata.latest_completion_state
    || lastEvent?.completion_state
    || lastEvent?.completionState
    || lastEventData.completion_state
    || lastEventData.completionState
    || ""
  ).trim().toLowerCase();
  const eventState = String(
    metadata.latest_event_state
    || metadata.event_state
    || lastEvent?.state
    || lastEventData.state
    || ""
  ).trim().toLowerCase();
  const eventType = String(
    metadata.latest_event_type
    || metadata.event_type
    || lastEvent?.type
    || lastEvent?.event_type
    || lastEventData.type
    || lastEventData.event_type
    || ""
  ).trim().toLowerCase();
  const failed = (
    data?.success === false
    || data?.ok === false
    || ["error", "failed", "blocked"].includes(completionState)
    || ["error", "failed"].includes(eventState)
    || ["edit.failed", "chat.failed", "execution.failed", "error"].includes(eventType)
  );
  if (!failed) return "";
  return String(
    data?.detail
    || data?.error
    || data?.incomplete_reason
    || metadata.error
    || metadata.detail
    || metadata.incomplete_reason
    || metadata.summary
    || lastEventData.error
    || lastEventData.detail
    || lastEventData.incomplete_reason
    || lastEventData.message
    || lastEvent?.error
    || lastEvent?.detail
    || lastEvent?.incomplete_reason
    || "regeneration failed"
  );
}

function shouldRenderEditedSessionForAgent(agentId, sessionId) {
  return (
    state.selectedAgentId === agentId
    && currentSessionIdForAgent(agentId) === String(sessionId || "")
  );
}

function completeEditedMessageRequest(agentId, requestCtx, finalPayload = {}, { status = "completed" } = {}) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  const requestId = requestCtx?.clientRequestId || requestCtx?.requestId || finalPayload?.request_id || "";
  if (chatState.inflightThinking && (!requestId || chatState.inflightThinking.requestId === requestId || chatState.inflightThinking.id === requestId)) {
    chatState.inflightThinking.completed = true;
    chatState.inflightThinking.status = status;
    chatState.inflightThinking.completion_state = finalPayload?.completion_state || (status === "error" ? "error" : "completed");
    chatState.lastThinkingSnapshot = {
      ...chatState.inflightThinking,
      completed: true,
      completedAt: Date.now(),
      requestId,
      sessionId: finalPayload?.session_id || requestCtx?.sessionIdAtSend || chatState.sessionId || "",
    };
    chatState.inflightThinking = null;
  }
  chatState.pendingThinkingEvents = null;
  if (chatState.currentRequest?.clientRequestId === requestId) chatState.currentRequest = null;
  setChatSubmittingForAgent(agentId, false);
  syncSelectedAgentChatActionControls();
}

function handleEditedRegenerationFailure(agentId, requestCtx, message = "regeneration failed") {
  const finalPayload = {
    completion_state: "error",
    incomplete_reason: message,
    response: message,
    request_id: requestCtx?.clientRequestId || requestCtx?.requestId || "",
    session_id: requestCtx?.sessionIdAtSend || "",
  };
  completeEditedMessageRequest(agentId, requestCtx, finalPayload, { status: "error" });
  const fullMessage = `Edited message was saved, but regeneration failed: ${message}`;
  if (state.selectedAgentId === agentId) {
    finalizeIncompleteAssistantRow(agentId, requestCtx, finalPayload);
    setChatStatus(fullMessage, true);
    showToast(fullMessage);
    addEditButtonsToMessages();
    renderIcons();
    scrollToBottom();
  } else {
    const chatState = ensureChatState(agentId);
    if (chatState) {
      chatState.needsReload = true;
    }
    if (typeof markAgentUnread === "function") markAgentUnread(agentId, "error");
    if (typeof renderAgentList === "function") renderAgentList();
  }
}

function finalizeEditedSessionMessages(agentId, sessionId, requestCtx, data = {}) {
  const messages = Array.isArray(data?.messages) ? data.messages : [];
  const chatState = ensureChatState(agentId);
  if (chatState) {
    chatState.needsReload = false;
  }

  if (shouldRenderEditedSessionForAgent(agentId, sessionId)) {
    renderChatHistory(messages, data.metadata || {});
    addEditButtonsToMessages();
    renderIcons();
    scrollToBottom();
    setChatStatus("Ready");
  } else {
    if (chatState) {
      chatState.needsReload = true;
    }
    if (typeof markAgentUnread === "function") markAgentUnread(agentId, "completed");
    if (typeof renderAgentList === "function") renderAgentList();
  }

  completeEditedMessageRequest(agentId, requestCtx, {
    completion_state: "completed",
    request_id: requestCtx?.clientRequestId || requestCtx?.requestId || "",
    session_id: sessionId,
  });
}

async function pollEditedSessionUntilComplete(agentId, finalSessionId, requestId, replacementUserMessageId, options = {}) {
  const intervalMs = Number(options.intervalMs || EDITED_MESSAGE_POLL_INTERVAL_MS);
  const timeoutMs = Number(options.timeoutMs || EDITED_MESSAGE_POLL_TIMEOUT_MS);
  const startedAt = Date.now();
  const requestCtx = options.requestCtx || {
    requestId,
    clientRequestId: requestId,
    sessionIdAtSend: finalSessionId,
    edit: true,
  };
  let lastError = null;

  while (Date.now() - startedAt < timeoutMs) {
    const chatState = ensureChatState(agentId);
    if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestId) return;

    try {
      const data = await agentApiFor(agentId, `/api/sessions/${encodeURIComponent(finalSessionId)}`);
      const messages = Array.isArray(data?.messages) ? data.messages : [];
      const assistant = findAssistantAfterEditedUserMessage(messages, replacementUserMessageId, requestId);
      if (assistant) {
        finalizeEditedSessionMessages(agentId, finalSessionId, requestCtx, data);
        return;
      }

      const failureMessage = getEditedSessionFailureMessage(data);
      if (failureMessage) {
        handleEditedRegenerationFailure(agentId, requestCtx, failureMessage);
        return;
      }
    } catch (error) {
      lastError = error;
    }

    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  const chatState = ensureChatState(agentId);
  if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestId) return;
  const message = "Regeneration is still running or timed out; refresh the session to check the latest result.";
  chatState.needsReload = true;
  const finalPayload = {
    completion_state: "timeout",
    incomplete_reason: message,
    response: message,
    request_id: requestId,
    session_id: finalSessionId,
  };
  completeEditedMessageRequest(agentId, requestCtx, finalPayload, { status: "timeout" });
  if (state.selectedAgentId === agentId) {
    finalizeIncompleteAssistantRow(agentId, requestCtx, finalPayload);
    setChatStatus(message, true);
    addEditButtonsToMessages();
    renderIcons();
    scrollToBottom();
    if (lastError) console.warn("Edit regeneration polling timed out after errors", lastError);
  } else {
    if (typeof markAgentUnread === "function") markAgentUnread(agentId, "error");
    if (typeof renderAgentList === "function") renderAgentList();
  }
}

async function retryAssistantMessage(row) {
  const agentId = state.selectedAgentId;
  const sessionId = document.getElementById("chat-session-id")?.value || currentSessionIdForAgent(agentId);
  const chatState = getChatState(agentId);

  if (!agentId || !sessionId) {
    showToast("No active session");
    return;
  }
  if (chatState?.isSubmitting) {
    showToast("Please wait for the current response to finish");
    return;
  }

  const rowUserMessageId = row?.dataset?.userMessageId || row?.querySelector("article")?.dataset?.userMessageId || "";
  const userArticle = findPrecedingUserArticle(row);
  const userMessageId = rowUserMessageId || userArticle?.dataset?.messageId || "";
  if (!userMessageId || userMessageId.startsWith("local-")) {
    showToast("Cannot retry this message yet");
    return;
  }

  const content = getUserArticleContent(userArticle).trim();
  if (!content) {
    showToast("Original message is empty");
    return;
  }

  if (hasFollowingMessageRows(row)) {
    const shouldContinue = window.confirm("Retrying this response will remove this message and all messages after it. Continue?");
    if (!shouldContinue) return;
  }

  const retryBtn = row?.querySelector(".assistant-retry-btn");
  if (retryBtn) retryBtn.disabled = true;
  try {
    const response = await fetch(`/a/${agentId}/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(userMessageId)}/delete-from-here`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    let result = {};
    try {
      result = await response.json();
    } catch (_error) {
      showToast(getRuntimeMutationErrorMessage(response, {}, "Failed to delete message"));
      return;
    }

    if (!response.ok || !result.success) {
      showToast(getRuntimeMutationErrorMessage(response, result, "Failed to delete message"));
      return;
    }

    truncateDomFromUserArticle(userArticle);
    if (chatState) chatState.pendingFiles = [];
    if (dom.chatInput) dom.chatInput.value = content;
    setChatStatus("Retrying...");
    await submitChatForSelectedAgent();
  } catch (err) {
    showToast("Retry failed: " + (err?.message || String(err)));
    setChatStatus("Ready");
  } finally {
    if (retryBtn) retryBtn.disabled = false;
  }
}

// Add edit buttons to user messages
function addUserEditButtonsToMessages() {
  if (!dom.messageList) return;
  const messages = dom.messageList.querySelectorAll('article[data-local-user="1"]');

  messages.forEach(article => {
    const messageId = article.getAttribute('data-message-id');
    if (!messageId || messageId.startsWith('local-')) return;

    const container = article.closest('.message-row');
    if (!container || container.querySelector('.edit-msg-btn')) return;

    const editBtn = document.createElement("button");
    editBtn.className = "edit-msg-btn";
    editBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>`;
    editBtn.title = "Edit message";
    editBtn.setAttribute("aria-label", "Edit message");
    editBtn.onclick = () => {
      const content = getUserArticleContent(article);
      openEditMessageModal(messageId, content);
    };

    container.appendChild(editBtn);
    container.tabIndex = 0;
    container.setAttribute("aria-label", "User message actions");
  });
}

function addAssistantActionsToMessages() {
  if (!dom.messageList) return;
  const rows = dom.messageList.querySelectorAll(".message-row-assistant");
  rows.forEach((row) => {
    if (row.dataset.welcome === "1") return;
    if (row.dataset.temporaryAssistant === "1") return;
    if (row.classList.contains("message-row-error")) return;
    if (row.querySelector(".assistant-msg-actions")) return;

    const article = row.querySelector("article.assistant-message");
    if (!article) return;
    if (article.dataset.pendingAssistant === "1") return;

    const previousUserArticle = findPrecedingUserArticle(row);
    if (!previousUserArticle) return;

    const actions = document.createElement("div");
    actions.className = "assistant-msg-actions message-actions message-actions-assistant";

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "message-action-btn assistant-copy-btn";
    copyBtn.title = "Copy message";
    copyBtn.setAttribute("aria-label", "Copy assistant message");
    copyBtn.innerHTML = `<i data-lucide="copy" class="w-4 h-4"></i>`;
    copyBtn.addEventListener("click", async () => {
      const copied = await copyText(getAssistantCopyText(article));
      showToast(copied ? "Copied" : "Copy failed");
    });

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "message-action-btn assistant-retry-btn";
    retryBtn.title = "Retry";
    retryBtn.setAttribute("aria-label", "Retry assistant response");
    retryBtn.innerHTML = `<i data-lucide="refresh-ccw" class="w-4 h-4"></i>`;
    retryBtn.addEventListener("click", () => retryAssistantMessage(row));

    actions.appendChild(copyBtn);
    actions.appendChild(retryBtn);
    row.appendChild(actions);
    row.tabIndex = 0;
    row.setAttribute("aria-label", "Assistant message actions");
  });
  renderIcons();
}

function addEditButtonsToMessages() {
  addUserEditButtonsToMessages();
  addAssistantActionsToMessages();
}

// Simple hash function for generating temporary message IDs
function simpleHash(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32bit integer
  }
  return Math.abs(hash).toString(36);
}

// Global toast notification
function showToast(message, duration = 2000) {
  const toast = document.getElementById('global-toast');
  if (!toast) return;
  toast.querySelector('div').textContent = message;
  toast.classList.remove('hidden');
  setTimeout(() => toast.classList.add('hidden'), duration);
}

// ===== wiring =====
function bindEvents() {
  // Edit modal events
  dom.editForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);
    const id = formData.get("id");

    const updates = { name: formData.get("name")?.trim() };
    const repoUrl = formData.get("skill_repo_url")?.trim();
    const branch = formData.get("skill_branch")?.trim();
    const runtimeProfileId = (formData.get("runtime_profile_id") || "").toString().trim();
    const runtimeType = (formData.get("runtime_type") || "").toString().trim().toLowerCase();

    // Always include skill_repo_url and skill_branch; empty values mean "use configured default".
    if (repoUrl !== undefined) updates.skill_repo_url = repoUrl || null;
    if (branch !== undefined) updates.skill_branch = branch || null;
    updates.runtime_profile_id = runtimeProfileId || null;
    if (runtimeType) updates.runtime_type = runtimeType;

    const msgEl = document.getElementById("edit-msg");
    msgEl.textContent = "Saving...";
    setModalFeedback(msgEl, "", msgEl.textContent);

    try {
      await api(`/api/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify(updates),
      });
      msgEl.textContent = "Saved!";
      setModalFeedback(msgEl, "success", msgEl.textContent);
      setTimeout(() => {
        document.getElementById("edit-modal").classList.add("hidden");
        document.getElementById("edit-modal").setAttribute("aria-hidden", "true");
        refreshAll();
      }, 800);
    } catch (err) {
      msgEl.textContent = err.message || "Error saving";
      setModalFeedback(msgEl, "error", msgEl.textContent);
    }
  });

  document.getElementById("close-edit-modal")?.addEventListener("click", () => {
    document.getElementById("edit-modal").classList.add("hidden");
    document.getElementById("edit-modal").setAttribute("aria-hidden", "true");
  });

  // Message edit modal
  document.getElementById("close-message-edit-modal")?.addEventListener("click", () => {
    closeEditMessageModal();
    document.getElementById("message-edit-modal")?.setAttribute("aria-hidden", "true");
  });

  document.getElementById("message-edit-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget || e.target;
    const agentId = state.selectedAgentId;
    const chatState = getChatState(agentId);
    const messageId = document.getElementById("edit-message-id")?.value || "";
    const newContent = (document.getElementById("edit-message-content")?.value || "").trim();
    const sessionId = document.getElementById("chat-session-id")?.value || "";

    if (!agentId) {
      showToast("Please select an assistant first");
      return;
    }
    if (!sessionId || !messageId) {
      showToast("Invalid session");
      return;
    }
    if (!newContent.trim()) {
      showToast("Message content cannot be empty");
      return;
    }
    if (!guardNoActiveChatRequestForAgent(agentId, "edit a message")) return;
    const clientRequestId = (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function")
      ? globalThis.crypto.randomUUID()
      : `req_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    if (!beginSingleSubmit(form, { pendingText: "Saving...", closeButton: document.getElementById("close-message-edit-modal") })) return;

    const editBody = {
      content: newContent,
      request_id: clientRequestId,
    };
    const modelOverride = String(chatState?.modelOverride || dom.chatModelSelect?.value || "").trim();
    const defaultModel = String(chatState?.profileDefaultModel || "").trim();
    if (modelOverride && modelOverride !== defaultModel) editBody.model = modelOverride;
    setChatStatus("Saving edited message...");
    let accepted = false;

    try {
      const response = await fetch(`/a/${agentId}/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/edit/async`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editBody)
      });

      const responseForError = response.clone();
      let result = {};
      try {
        result = await response.json();
      } catch (_error) {
        result = {};
      }

      if (!response.ok || result.success !== true) {
        const hasRuntimeErrorText = String(result?.detail || result?.error || "").trim().length > 0;
        let message = getRuntimeMutationErrorMessage(response, result, "Failed to edit message");
        if (!hasRuntimeErrorText && response.status !== 501 && !response.ok) {
          message = await handleErrorResponse(responseForError);
        }
        showToast(message);
        setChatStatus(message || "Ready", true);
        return;
      }

      if (result.accepted !== true) {
        const message = getRuntimeMutationErrorMessage(response, result, "Failed to edit message");
        showToast(message);
        setChatStatus(message || "Ready", true);
        return;
      }

      accepted = true;
      closeEditMessageModal();
      document.getElementById("message-edit-modal")?.setAttribute("aria-hidden", "true");
      endSingleSubmit(form, { closeButton: document.getElementById("close-message-edit-modal") });

      const finalSessionId = result.session_id || sessionId;
      const requestId = result.request_id || clientRequestId;
      const replacementUserMessageId = result.replacement_user_message_id || "";
      const requestCtx = {
        requestId,
        agentId,
        sessionIdAtSend: finalSessionId,
        message: newContent,
        clientRequestId: requestId,
        originalClientRequestId: clientRequestId,
        startedAt: Date.now(),
        usedStream: false,
        edit: true,
        replacementUserMessageId,
      };
      updateAgentSession(agentId, finalSessionId);
      if (state.selectedAgentId === agentId) {
        const hiddenSessionInput = document.getElementById("chat-session-id");
        if (hiddenSessionInput) hiddenSessionInput.value = finalSessionId;
      }
      setLastSessionId(agentId, finalSessionId);

      if (state.selectedAgentId === agentId) {
        if (Array.isArray(result.messages)) {
          renderChatHistory(result.messages);
        }
        removeWelcomeMessageIfPresent();
        if (dom.messageList) {
          dom.messageList.insertAdjacentHTML("beforeend", buildUserMessageArticle(newContent));
          const optimisticUserArticle = getLatestOptimisticUserArticle();
          if (optimisticUserArticle && replacementUserMessageId) {
            optimisticUserArticle.dataset.messageId = replacementUserMessageId;
          }
          dom.messageList.insertAdjacentHTML("beforeend", buildPendingAssistantArticle(requestId, "Regenerating response..."));
        }
        addEditButtonsToMessages();
        renderIcons();
        scrollToBottom();
      } else if (chatState) {
        chatState.needsReload = true;
      }

      chatState.currentRequest = requestCtx;
      chatState.inflightThinking = {
        id: requestId,
        requestId,
        sessionId: finalSessionId,
        events: [],
        completed: false,
        started: false,
        contextState: null,
        contextBudget: null,
        startedAt: Date.now(),
      };
      ensureEventSocketForAgent(agentId, finalSessionId, requestId);
      setChatSubmittingForAgent(agentId, true);
      setChatStatus("Regenerating response...");
      pollEditedSessionUntilComplete(agentId, finalSessionId, requestId, replacementUserMessageId, { requestCtx });
    } catch (err) {
      const message = err?.message || String(err);
      if (accepted) {
        handleEditedRegenerationFailure(agentId, {
          requestId: clientRequestId,
          clientRequestId,
          sessionIdAtSend: sessionId,
          edit: true,
        }, message);
      } else {
        showToast("Error editing message: " + message);
        setChatStatus(message || "Ready", true);
      }
    } finally {
      if (!accepted) {
        setChatSubmittingForAgent(agentId, false);
        endSingleSubmit(form, { closeButton: document.getElementById("close-message-edit-modal") });
        syncSelectedAgentChatActionControls();
      }
    }
  });

  dom.detailToggle?.addEventListener("click", toggleAssistantDetailsPanel);
  dom.closeToolPanel?.addEventListener("click", closeToolPanel);
  dom.pinToolPanel?.addEventListener("click", toggleToolPanelPinned);
  dom.toolBackdrop?.addEventListener("click", () => {
    if (!state.toolPanelPinned) closeToolPanel();
  });
  dom.secondaryPaneToggle?.addEventListener("click", () => {
    state.secondaryPaneCollapsed = true;
    applySecondaryPaneState();
    persistUiLayoutPreferences({ includeToolPanel: false });
    window.requestAnimationFrame(() => dom.secondaryPaneRestore?.focus?.());
  });
  dom.secondaryPaneRestore?.addEventListener("click", async () => {
    await setActiveNavSection(state.activeNavSection, { toggleIfSame: false });
    window.requestAnimationFrame(() => dom.secondaryPaneToggle?.focus?.());
  });

  dom.chatInput?.addEventListener("input", () => {
    maybeShowSuggest();
    // Auto-expand textarea
    syncChatInputHeight();
  });
  dom.chatInput?.addEventListener("compositionstart", () => {
    state.isComposingInput = true;
  });
  dom.chatInput?.addEventListener("compositionend", () => {
    state.isComposingInput = false;
    maybeShowSuggest();
  });
  dom.chatInput?.addEventListener("blur", () => {
    if (state.suggestBlurHideTimer) clearTimeout(state.suggestBlurHideTimer);
    state.suggestBlurHideTimer = setTimeout(() => {
      hideSuggest();
      state.suggestBlurHideTimer = null;
    }, 120);
  });
  dom.chatInput?.addEventListener("keydown", (event) => {
    const isImeComposing = event.isComposing || state.isComposingInput || event.keyCode === 229;
    const suggestOpen = !!dom.chatSuggest && !dom.chatSuggest.classList.contains("hidden");
    if (event.key === "Escape" && suggestOpen) {
      event.preventDefault();
      hideSuggest();
      return;
    }
    if (event.key === "ArrowDown" && suggestOpen) {
      event.preventDefault();
      moveSuggestionSelection(1);
      return;
    }
    if (event.key === "ArrowUp" && suggestOpen) {
      event.preventDefault();
      moveSuggestionSelection(-1);
      return;
    }
    if (event.key === "Enter" && isImeComposing) {
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && suggestOpen) {
      event.preventDefault();
      if (pickCurrentSuggestion()) return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const chatState = getChatState();
      if (chatState?.isSubmitting) return;
      submitChatForSelectedAgent();
    }
  });
  dom.chatSuggest?.addEventListener("mousedown", () => {
    if (state.suggestBlurHideTimer) {
      clearTimeout(state.suggestBlurHideTimer);
      state.suggestBlurHideTimer = null;
    }
  });
  document.addEventListener("mousedown", (event) => {
    if (dom.chatSuggest?.classList.contains("hidden")) return;
    const target = event.target;
    if (!dom.chatInput?.contains(target) && !dom.chatSuggest?.contains(target)) hideSuggest();
  });

  dom.uploadInput?.addEventListener("change", (e) => {
    if (e.target.files?.length) {
      addPendingFilesAndUpload(e.target.files);
      e.target.value = ''; // Clear to allow re-uploading same file
    }
  });

  dom.composerAttachBtn?.addEventListener("click", () => dom.uploadInput?.click());
  dom.chatModelSelect?.addEventListener("change", () => {
    const selectedAgentId = state.selectedAgentId;
    if (!selectedAgentId) return;
    const chatState = ensureChatState(selectedAgentId);
    if (!chatState) return;
    chatState.modelOverride = (dom.chatModelSelect?.value || "").trim();
  });
  dom.headerNewChatBtn?.addEventListener("click", () => startNewChatForSelectedAgent());
  dom.railAssistantsBtn?.addEventListener("click", () => openPortalSection("assistants"));
  dom.bundlesMenuBtn?.addEventListener("click", () => openPortalSection("bundles"));
  dom.automationsMenuBtn?.addEventListener("click", () => openPortalSection("automations"));
  dom.addAutomationBtn?.addEventListener("click", async () => {
    try {
      if (!state.mineAgents || !state.mineAgents.length) {
        await loadMineAgents();
      }
      openCreateAutomationRuleModal();
    } catch (error) {
      dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed to load agents: ${safe(error.message)}</div>`;
    }
  });
  dom.homeStartChatBtn?.addEventListener("click", () => startNewChatForSelectedAgent());
  dom.homeOpenBundlesBtn?.addEventListener("click", async () => {
    await openPortalSection("bundles");
  });
  dom.homeOpenTasksBtn?.addEventListener("click", async () => {
    await openPortalSection("tasks");
  });
  document.getElementById('btn-sessions')?.addEventListener('click', () => toggleSessionsDrawer());


  dom.toolPanelBody?.addEventListener("click", async (event) => {
    const newChatBtn = event.target.closest("#sessions-new-chat-btn");
    if (newChatBtn) {
      event.preventDefault();
      closeSessionsDrawer();
      await startNewChatForSelectedAgent();
      return;
    }

    const actionBtn = event.target.closest("[data-session-action]");
    if (actionBtn) {
      event.preventDefault();
      const action = actionBtn.dataset.sessionAction || "";
      const sessionId = actionBtn.dataset.sessionId || "";
      const sessionName = actionBtn.dataset.sessionName || "";
      const agentId = state.selectedAgentId;
      if (!agentId || !sessionId) return;
      if (action === "rename") {
        await renameSessionForAgent(agentId, sessionId, sessionName);
      } else if (action === "delete") {
        await deleteSessionForAgent(agentId, sessionId);
      }
      return;
    }

    const sessionBtn = event.target.closest("[data-session-id]");
    if (sessionBtn) {
      event.preventDefault();
      await loadSession(sessionBtn.dataset.sessionId || "");
      closeSessionsDrawer();
      return;
    }

    const serverPathLink = event.target.closest('[data-server-path]');
    if (serverPathLink) {
      event.preventDefault();
      await loadServerFiles(serverPathLink.dataset.serverPath || state.serverFilesRootPath || undefined);
      return;
    }


    const skillBtn = event.target.closest("[data-skill-command]");
    if (skillBtn) {
      event.preventDefault();
      const command = normalizeSkillCommand(skillBtn.dataset.skillCommand);
      if (!command) return;
      const skillStart = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
      const skillEnd = dom.chatInput.selectionEnd ?? dom.chatInput.value.length;
      dom.chatInput.setRangeText(`${command} `, skillStart, skillEnd, "end");
      hideSuggest();
      dom.chatInput.focus();
      setChatStatus(`Inserted ${command}`);
      return;
    }

  });
  dom.workspaceDetailContent?.addEventListener("submit", async (event) => {
    const automationForm = event.target.closest("#create-automation-inline-form");
    if (automationForm) {
      event.preventDefault();
      try {
        await submitCreateAutomationRule(automationForm);
      } catch (error) {
        showToast(`Create automation failed: ${error.message}`);
      }
      return;
    }
    const taskForm = event.target.closest("#create-task-from-template-form");
    if (taskForm) {
      event.preventDefault();
      try {
        await submitCreateTaskFromTemplate(taskForm);
      } catch (error) {
        showToast(`Create task failed: ${error.message}`);
      }
    }
  });
  dom.workspaceDetailContent?.addEventListener("change", (event) => {
    const formEl = event.target.closest("#create-task-from-template-form");
    if (!formEl) return;
    if (event.target.matches('[name="template_id"]')) {
      updateCreateTaskTemplateFieldVisibility(formEl);
    }
  });
  dom.workspaceDetailContent?.addEventListener("click", async (event) => {
    const runBtn = event.target.closest("[data-run-automation-once]");
    if (runBtn) {
      event.preventDefault();
      try {
        await runAutomationRuleOnce(runBtn.dataset.runAutomationOnce || "");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Run once failed: ${safe(error.message)}</div>`;
      }
      return;
    }
    const toggleBtn = event.target.closest("[data-toggle-automation-enabled]");
    if (toggleBtn) {
      event.preventDefault();
      try {
        await toggleAutomationRuleEnabled(toggleBtn.dataset.toggleAutomationEnabled || "", toggleBtn.dataset.nextEnabled === "true");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Update failed: ${safe(error.message)}</div>`;
      }
      return;
    }
    const deleteBtn = event.target.closest("[data-delete-automation-rule]");
    if (deleteBtn) {
      event.preventDefault();
      try {
        await deleteAutomationRule(deleteBtn.dataset.deleteAutomationRule || "");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Delete failed: ${safe(error.message)}</div>`;
      }
    }
  });

  dom.workspaceDetailContent?.addEventListener("click", async (event) => {
    const openCreateTaskBtn = event.target.closest("[data-open-create-task-main]");
    if (openCreateTaskBtn) {
      event.preventDefault();
      await openTaskCreatePanelInMain();
      return;
    }

    const openTaskMainBtn = event.target.closest("[data-open-task-main]");
    if (openTaskMainBtn) {
      event.preventDefault();
      const taskId = openTaskMainBtn.dataset.openTaskMain || "";
      if (!taskId) return;
      await openTaskDetailInMain(taskId);
      return;
    }

    const taskBackBtn = event.target.closest("[data-task-back-sidebar]");
    if (taskBackBtn) {
      event.preventDefault();
      await returnFromTaskDetailToSidebar();
      return;
    }

    const deleteProfileBtn = event.target.closest("[data-delete-runtime-profile]");
    if (deleteProfileBtn) {
      event.preventDefault();
      const profileId = deleteProfileBtn.dataset.deleteRuntimeProfile || "";
      if (!profileId || !confirm("Delete this runtime profile?")) return;
      try {
        const resp = await fetch(`/api/runtime-profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" });
        if (!resp.ok) throw new Error(await handleErrorResponse(resp));
        await refreshRuntimeProfileList({ preserveSelection: false });
        await loadRuntimeProfiles(true);
        const next = state.runtimeProfiles.find((item) => item.is_default) || state.runtimeProfiles[0];
        if (next?.id) {
          await openRuntimeProfileInMain(next.id);
        } else {
          renderWorkspaceDetailPlaceholder("No runtime profiles found.", "runtime-profiles-placeholder");
        }
      } catch (err) {
        showToast(err.message);
      }
    }
  });

  dom.themeToggle?.addEventListener("click", toggleTheme);

  dom.usersMenuBtn?.addEventListener("click", openUsersPanel);

  dom.tasksMenuBtn?.addEventListener("click", () => openPortalSection("tasks"));

  dom.addTaskBtn?.addEventListener("click", async () => {
    try {
      await openTaskCreatePanelInMain();
    } catch (error) {
      showToast(`Open task create failed: ${error.message}`);
    }
  });
  dom.runtimeProfilesMenuBtn?.addEventListener("click", () => openPortalSection("runtime-profiles"));
  window.addEventListener("resize", () => {
    if (!state.toolPanelPinned) return;
    if (!isWideEnoughToPinToolPanel()) {
      state.toolPanelPinned = false;
      applyToolPanelState();
      return;
    }
    clampToolPanelWidthForPinned();
    applyToolPanelState();
  });

  dom.addBundleBtn?.addEventListener("click", () => {
    endSingleSubmit(dom.createBundleForm, { closeButton: dom.closeCreateBundleModal });
    dom.createBundleModal?.classList.remove("hidden");
    dom.createBundleModal?.setAttribute("aria-hidden", "false");
    if (dom.createBundleMsg) {
      dom.createBundleMsg.textContent = "";
      setModalFeedback(dom.createBundleMsg, "", dom.createBundleMsg.textContent);
    }
  });
  dom.refreshBundlesBtn?.addEventListener("click", async () => {
    await refreshRequirementBundles({ showLoadingState: true, force: true, notifyOnSuccess: true });
  });

  dom.addRuntimeProfileBtn?.addEventListener("click", () => {
    endSingleSubmit(dom.createRuntimeProfileForm, { closeButton: dom.closeCreateRuntimeProfileModal });
    dom.createRuntimeProfileModal?.classList.remove("hidden");
    dom.createRuntimeProfileModal?.setAttribute("aria-hidden", "false");
    if (dom.createRuntimeProfileMsg) setModalFeedback(dom.createRuntimeProfileMsg, "", "");
  });

  dom.closeCreateRuntimeProfileModal?.addEventListener("click", () => {
    if (dom.createRuntimeProfileForm?.dataset.submitting === "true") return;
    dom.createRuntimeProfileModal?.classList.add("hidden");
    dom.createRuntimeProfileModal?.setAttribute("aria-hidden", "true");
  });

  dom.createRuntimeProfileForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if (!beginSingleSubmit(form, { pendingText: "Creating...", closeButton: dom.closeCreateRuntimeProfileModal })) return;
    const formData = new FormData(form);
    try {
      const resp = await fetch('/api/runtime-profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: String(formData.get('name') || '').trim(),
          description: String(formData.get('description') || '').trim() || null,
          is_default: String(formData.get('is_default') || '').toLowerCase() === 'on',
        }),
      });
      if (!resp.ok) throw new Error(await handleErrorResponse(resp));
      const created = await resp.json();
      await refreshRuntimeProfileList({ preserveSelection: false });
      await loadRuntimeProfiles(true);
      state.selectedRuntimeProfileId = created.id;
      renderRuntimeProfileList();
      await openRuntimeProfileInMain(created.id);
      form.reset();
      dom.createRuntimeProfileModal?.classList.add('hidden');
      dom.createRuntimeProfileModal?.setAttribute('aria-hidden', 'true');
      endSingleSubmit(form, { closeButton: dom.closeCreateRuntimeProfileModal });
    } catch (err) {
      if (dom.createRuntimeProfileMsg) setModalFeedback(dom.createRuntimeProfileMsg, 'error', err.message);
      endSingleSubmit(form, { closeButton: dom.closeCreateRuntimeProfileModal });
    }
  });

  dom.closeCreateBundleModal?.addEventListener("click", () => {
    if (dom.createBundleForm?.dataset.submitting === "true") return;
    dom.createBundleModal?.classList.add("hidden");
    dom.createBundleModal?.setAttribute("aria-hidden", "true");
  });
  document.getElementById("create-runtime-type-select")?.addEventListener("change", () => {
    updateCreateRuntimeTypeHint(document.getElementById("create-form"), state.agentDefaults || {});
  });

  dom.addAgentBtn?.addEventListener("click", async () => {
    const [, defaults] = await Promise.all([loadRuntimeProfiles(true), loadAgentDefaults(true)]);
    const createForm = document.getElementById("create-form");
    if (createForm) {
      endSingleSubmit(createForm, { closeButton: document.getElementById("close-create-modal") });
      createForm.reset();
      applyCreateAgentDefaults(createForm, defaults);
    }
    const createMsg = document.getElementById("create-msg");
    if (createMsg) {
      createMsg.textContent = "";
      setModalFeedback(createMsg, "", createMsg.textContent);
    }
    const createSelect = document.getElementById("create-runtime-profile-select");
    populateRuntimeProfileSelect(createSelect, "");
    document.getElementById("create-modal")?.classList.remove("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "false");
  });

  document.getElementById("close-create-modal")?.addEventListener("click", () => {
    if (document.getElementById("create-form")?.dataset.submitting === "true") return;
    document.getElementById("create-modal")?.classList.add("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
  });

  document.getElementById("create-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if (!beginSingleSubmit(form, { pendingText: "Creating...", closeButton: document.getElementById("close-create-modal") })) return;
    const formData = new FormData(form);
    const name = formData.get("name");
    const repoUrl = (formData.get("skill_repo_url") || "").toString().trim();
    const branch = (formData.get("skill_branch") || "").toString().trim();
    const runtimeProfileId = (formData.get("runtime_profile_id") || "").toString().trim();
    const runtimeType = normalizeRuntimeTypeValue(formData.get("runtime_type"), state.agentDefaults || {});

    const msgEl = document.getElementById("create-msg");

    try {
      const defaults = await loadAgentDefaults();

      if (!defaults.disk_size_gi) {
        throw new Error("Invalid defaults configuration");
      }
      const runtimeConfig = findRuntimeTypeConfig(defaults, runtimeType);

      // Use form values if provided, or null to skip repo
      const data = {
        name: name,
        runtime_type: runtimeType,
        skill_repo_url: repoUrl || null,
        skill_branch: branch || null,
        disk_size_gi: defaults.disk_size_gi,
        cpu: defaults.cpu,
        memory: defaults.memory,
        mount_path: runtimeConfig?.default_mount_path || defaults.mount_path,
        runtime_profile_id: runtimeProfileId || null,
      };

      msgEl.textContent = "Creating...";
      setModalFeedback(msgEl, "", msgEl.textContent);
      const resp = await fetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!resp.ok) {
        throw new Error(await handleErrorResponse(resp));
      }
      const agent = await resp.json();
      msgEl.textContent = "Assistant created!";
      setModalFeedback(msgEl, "success", msgEl.textContent);
      form.reset();
      applyCreateAgentDefaults(form, defaults);
      setTimeout(() => {
        document.getElementById("create-modal")?.classList.add("hidden");
        document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
        endSingleSubmit(form, { closeButton: document.getElementById("close-create-modal") });
        refreshAll();
      }, 1000);
    } catch (err) {
      msgEl.textContent = err.message;
      setModalFeedback(msgEl, "error", msgEl.textContent);
      endSingleSubmit(form, { closeButton: document.getElementById("close-create-modal") });
    }
  });

  dom.createBundleForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if (!beginSingleSubmit(form, { pendingText: "Creating...", closeButton: dom.closeCreateBundleModal })) return;
    const formData = new FormData(form);
    const payload = {
      template_id: String(formData.get("template_id") || "requirement.v1"),
      title: String(formData.get("title") || ""),
      domain: String(formData.get("domain") || ""),
      slug: String(formData.get("slug") || "").trim() || null,
      base_skill_branch: String(formData.get("base_branch") || ""),
    };

    try {
      if (dom.createBundleMsg) {
        dom.createBundleMsg.textContent = "Creating...";
        setModalFeedback(dom.createBundleMsg, "", dom.createBundleMsg.textContent);
      }
      const resp = await fetch("/api/requirement-bundles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        throw new Error(await handleErrorResponse(resp));
      }
      const detail = await resp.json();
      if (dom.createBundleMsg) {
        dom.createBundleMsg.textContent = "Bundle created!";
        setModalFeedback(dom.createBundleMsg, "success", dom.createBundleMsg.textContent);
      }

      form.reset();
      form.querySelector('[name="base_branch"]').value = payload.base_branch;
      form.querySelector('[name="template_id"]').value = 'requirement.v1';
      dom.createBundleModal?.classList.add("hidden");
      dom.createBundleModal?.setAttribute("aria-hidden", "true");
      endSingleSubmit(form, { closeButton: dom.closeCreateBundleModal });
      upsertRequirementBundleListItem(bundleListItemFromDetail(detail));
      await setActiveNavSection("bundles", { toggleIfSame: false });
      state.selectedBundleKey = bundleKeyFromRef(detail.bundle_ref);
      renderRequirementBundleList();
      await openRequirementBundleInMain(detail.bundle_ref);
    } catch (err) {
      if (dom.createBundleMsg) {
        dom.createBundleMsg.textContent = err.message;
        setModalFeedback(dom.createBundleMsg, "error", dom.createBundleMsg.textContent);
      }
      endSingleSubmit(form, { closeButton: dom.closeCreateBundleModal });
    }
  });

  dom.logoutBtn?.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  const initialTheme = localStorage.getItem("portal-theme") || (document.documentElement.getAttribute("data-theme") || "dark");
  applyTheme(initialTheme);

  // Tool panel resize from left edge
  const resizeHandle = document.getElementById('tool-panel-resize');
  if (resizeHandle) {
    let isResizing = false;
    let didResizeToolPanel = false;
    resizeHandle.addEventListener('mousedown', (e) => {
      isResizing = true;
      didResizeToolPanel = false;
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', (e) => {
      if (!isResizing) return;
      const newWidth = window.innerWidth - e.clientX - 24;
      const pinned = state.toolPanelPinned && state.toolPanelOpen;

      if (pinned) {
        const bounds = getPinnedToolPanelWidthBounds();
        if (!bounds.canPin) {
          state.toolPanelPinned = false;
          applyToolPanelState();
          return;
        }
        setToolPanelWidth(clamp(newWidth, bounds.min, bounds.max));
        didResizeToolPanel = true;
      } else {
        const maxWidth = Math.max(TOOL_PANEL_MIN_OVERLAY_WIDTH, window.innerWidth - 24);
        setToolPanelWidth(clamp(newWidth, TOOL_PANEL_MIN_OVERLAY_WIDTH, maxWidth));
        didResizeToolPanel = true;
      }
    });
    document.addEventListener('mouseup', () => {
      isResizing = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (didResizeToolPanel && state.toolPanelOpen && state.toolPanelPinned) {
        persistUiLayoutPreferences({ includeSecondaryPane: false, includeToolPanel: true });
      }
      didResizeToolPanel = false;
    });
  }

  document.body.addEventListener("runtimeProfilesChanged", async () => {
    await refreshRuntimeProfileList({ preserveSelection: true });
    await loadRuntimeProfiles(true);
  });

  bindEvents();
  if (typeof initialUiLayoutPrefs.toolPanelWidth === "number" && Number.isFinite(initialUiLayoutPrefs.toolPanelWidth)) {
    setToolPanelWidth(initialUiLayoutPrefs.toolPanelWidth);
  }
  if (initialUiLayoutPrefs.toolPanelPinned && !isWideEnoughToPinToolPanel()) {
    state.toolPanelOpen = false;
    state.toolPanelPinned = false;
    state.activeUtilityPanel = null;
  }
  applySecondaryPaneState();
  applyToolPanelState();
  initializeRenderLifecycle();
  updateChatInputPlaceholder();

  // Event delegation for remove buttons (replace inline onclick)
  const previewArea = document.getElementById('input-preview-area');
  if (previewArea) {
    previewArea.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove-id]');
      if (btn) {
        e.preventDefault();
        e.stopPropagation();
        const fileId = btn.dataset.removeId;
        if (fileId) removePendingFile(fileId);
      }
    });
  }

  document.getElementById("chat-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitChatForSelectedAgent();
  });
  dom.abortChatRunBtn?.addEventListener("click", async (event) => {
    event.preventDefault();
    await abortActiveChatRequestForSelectedAgent();
  });

  // Drag and drop file upload
  const messageList = dom.messageList;
  let dragCounter = 0;
  if (messageList) {
    messageList.addEventListener("dragenter", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter++;
      messageList.classList.add("drag-over");
    });
    messageList.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.stopPropagation();
      messageList.classList.add("drag-over");
    });
    messageList.addEventListener("dragleave", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter = Math.max(0, dragCounter - 1);
      if (dragCounter === 0) {
        messageList.classList.remove("drag-over");
      }
    });
    messageList.addEventListener("drop", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter = 0;
      messageList.classList.remove("drag-over");
      const files = e.dataTransfer?.files;
      if (files?.length) {
        // Add files and upload immediately
        await addPendingFilesAndUpload(files);
      }
    });

    // Also support drag & drop on the chat form
    const chatForm = document.getElementById('chat-form');
    if (chatForm) {
      chatForm.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
      chatForm.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const files = e.dataTransfer?.files;
        if (files?.length) {
          // Add files and upload immediately
          await addPendingFilesAndUpload(files);
        }
      });
    }
  }


  // Server Files button in header
  document.getElementById('btn-files')?.addEventListener('click', () => {
    if (!state.selectedAgentId) {
      showToast('Please select an assistant first');
      return;
    }
    openServerFiles();
  });

  // Thinking Process button in header
  document.getElementById('btn-thinking')?.addEventListener('click', () => {
    if (!state.selectedAgentId) {
      showToast('Please select an assistant first');
      return;
    }
    openThinkingProcessPanel();
  });

  await loadAgentDefaults();
  await refreshAll({ preserveLayout: true, skipRouteApply: true });
  await applyPortalRouteFromHash({ replaceInvalid: true });
  await restorePinnedToolPanelFromPreferencesOnce();
  renderMarkdown(document);
  renderIcons();
});

window.addEventListener("hashchange", () => {
  applyPortalRouteFromHash({ replaceInvalid: true }).catch((error) => {
    console.error("Failed to apply portal route from hash", error);
    showToast("Failed to open URL route: " + (error?.message || error));
  });
});

window.addEventListener("popstate", () => {
  applyPortalRouteFromHash({ replaceInvalid: true }).catch((error) => {
    console.error("Failed to apply portal route from history", error);
    showToast("Failed to open URL route: " + (error?.message || error));
  });
});

window.addEventListener("beforeunload", disconnectEventSocket);



// ===== File Preview Modal =====
let filePreviewModal = null;
let filePreviewContent = null;
let filePreviewBackdrop = null;
let previousFocusElement = null;  // Store previously focused element for accessibility

function escapeHtmlAttr(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function initFilePreviewModal() {
  filePreviewModal = document.getElementById('file-preview-modal');
  filePreviewContent = document.getElementById('file-preview-content');
  filePreviewBackdrop = document.getElementById('file-preview-backdrop');
  
  document.getElementById('close-file-preview')?.addEventListener('click', closeFilePreview);
  filePreviewBackdrop?.addEventListener('click', closeFilePreview);
  
  // Focus trap: keep keyboard focus within modal when open
  document.addEventListener('keydown', (e) => {
    if (!filePreviewModal || filePreviewModal.classList.contains('hidden')) return;
    
    if (e.key === 'Escape') {
      closeFilePreview();
      return;
    }
    
    if (e.key === 'Tab') {
      const focusable = filePreviewModal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (focusable.length === 0) return;
      
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });
  
  // Delegated click handler for preview elements (input-preview-area cards and chat attachments)
  document.addEventListener('click', function(e) {
    // Handle input-preview-area cards (exclude remove button)
    const card = e.target.closest('.input-preview-card');
    if (card && !e.target.closest('.remove-btn')) {
      const url = card.getAttribute('data-preview-url');
      // data-preview-name is HTML-escaped, getAttribute returns decoded value directly
      const name = card.getAttribute('data-preview-name') || '';
      const isImage = card.getAttribute('data-is-image') === 'true';
      if (url) openFilePreview(url, name, isImage);
      return;
    }
    
    // Handle chat message attachments with data-preview-url
    const previewEl = e.target.closest('[data-preview-url]');
    if (previewEl && !e.target.closest('.remove-btn')) {
      const url = previewEl.getAttribute('data-preview-url');
      // data-preview-name is HTML-escaped, getAttribute returns decoded value directly
      const name = previewEl.getAttribute('data-preview-name') || '';
      const isImage = previewEl.getAttribute('data-is-image') === 'true';
      if (url) openFilePreview(url, name, isImage);
    }
  });
}

function isSafePreviewUrl(url) {
  if (typeof url !== 'string') return false;
  try {
    const resolved = new URL(url, window.location.origin);
    const allowed = ['http:', 'https:', 'blob:'];
    if (!allowed.includes(resolved.protocol)) return false;
    return true;
  } catch (e) {
    return false;
  }
}

function openFilePreview(url, name, isImage) {
  if (!filePreviewModal || !filePreviewContent) return;
  if (!isSafePreviewUrl(url)) return;
  
  // Clear existing content
  filePreviewContent.textContent = '';
  
  if (isImage) {
    const img = document.createElement('img');
    img.src = url;
    img.alt = name || 'Preview';
    filePreviewContent.appendChild(img);
  } else {
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.className = 'file-link';
    
    const icon = document.createElement('span');
    icon.textContent = '📄';
    const text = document.createElement('span');
    text.textContent = name || 'Open File';
    
    link.appendChild(icon);
    link.appendChild(text);
    filePreviewContent.appendChild(link);
  }
  
  // Accessibility: store previous focus and move focus to modal
  const closeBtn = document.getElementById('close-file-preview');
  previousFocusElement = document.activeElement;
  
  filePreviewModal.classList.remove('hidden');
  filePreviewModal.setAttribute('aria-hidden', 'false');
  
  // Focus the close button when opening
  if (closeBtn) closeBtn.focus();
}

function closeFilePreview() {
  if (!filePreviewModal) return;
  filePreviewModal.classList.add('hidden');
  filePreviewModal.setAttribute('aria-hidden', 'true');
  
  // Restore focus to previously focused element
  if (previousFocusElement && previousFocusElement.focus) {
    previousFocusElement.focus();
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function() { initFilePreviewModal(); initFilePanelPreview(); });
} else {
  initFilePreviewModal();
  initFilePanelPreview();
}


// ===== File Panel Preview Handler =====
function initFilePanelPreview() {
  document.addEventListener('click', function(e) {
    // Ignore clicks inside uploads-list (has its own preview handler)
    if (e.target.closest('#uploads-list')) return;
    
    const previewBtn = e.target.closest('.preview-btn');
    if (!previewBtn) return;
    
    const fileRow = previewBtn.closest('.file-row');
    if (!fileRow) return;
    
    const fileId = fileRow.dataset.fileId;
    const filename = fileRow.querySelector('.font-medium')?.textContent || fileId;
    if (!fileId || !state.selectedAgentId) return;
    
    const url = '/a/' + state.selectedAgentId + '/api/files/' + encodeURIComponent(fileId);
    const isImage = filename && filename.match(/\.(jpg|jpeg|png|gif|webp|svg)$/i);
    
    openFilePreview(url, filename, !!isImage);
  });
}


// ===== System Prompt Configuration =====
function getAgentRuntimeType(agent) {
  var normalized = String((agent && agent.runtime_type) || 'native').trim().toLowerCase();
  if (!normalized) return 'native';
  return normalized === 'opencode' ? 'opencode' : 'native';
}

function isOpenCodeAgent(agent) {
  return getAgentRuntimeType(agent) === 'opencode';
}

function getSystemPromptUiModel(agent, config) {
  var runtimeType = getAgentRuntimeType(agent);
  if (runtimeType === 'opencode') {
    return {
      runtimeType: runtimeType,
      title: 'OpenCode Rules',
      description: 'OpenCode uses the project-root AGENTS.md as its editable instruction file. SOUL, USER, MEMORY and DAILY NOTES are native EFP prompt sections and are not supported for this runtime.',
      sections: ['agents'],
      labels: { agents: (config && config.agents && config.agents.label) || 'AGENTS.md' },
      editable: { agents: true },
      canToggle: { agents: false },
      forcedEnabled: { agents: true }
    };
  }

  return {
    runtimeType: runtimeType,
    title: 'System Prompt',
    description: '',
    sections: ['soul', 'user', 'agents', 'memory', 'daily_notes'],
    labels: { soul: 'SOUL', user: 'USER', agents: 'AGENTS', memory: 'MEMORY', daily_notes: 'DAILY NOTES' },
    editable: { soul: true, user: true, agents: true, memory: true, daily_notes: false },
    canToggle: { soul: true, user: true, agents: true, memory: true, daily_notes: true },
    forcedEnabled: {}
  };
}

function resolveSystemPromptLabel(agent, section, config) {
  var ui = getSystemPromptUiModel(agent, config || {});
  return ui.labels[section] || String(section || '').toUpperCase();
}

function renderSystemPromptSection(agent) {
  var container = null;
  var agentMeta = document.getElementById('agent-meta');
  if (agentMeta) container = agentMeta;
  if (!container) {
    var toolPanelBody = document.getElementById('tool-panel-body');
    if (toolPanelBody) container = toolPanelBody;
  }
  if (!container) {
    var asides = document.querySelectorAll('aside');
    for (var i = 0; i < asides.length; i++) {
      if (asides[i].offsetParent !== null) {
        container = asides[i];
        break;
      }
    }
  }
  if (!container) container = document.body;

  var existing = document.getElementById('system-prompt-section');
  if (existing) existing.remove();

  var ui = getSystemPromptUiModel(agent, {});
  var descriptionHtml = ui.description ? '<div class="portal-inline-state">' + ui.description + '</div>' : '';

  var section = document.createElement('div');
  section.id = 'system-prompt-section';
  section.className = 'portal-panel-section';
  section.innerHTML = '<div class="portal-panel-header"><div class="portal-detail-label">' + ui.title + '</div></div>' + descriptionHtml + '<div id="system-prompt-items" class="portal-panel-stack"></div><div id="system-prompt-loading" class="portal-inline-state">Loading...</div><div id="system-prompt-error" class="portal-inline-state is-error hidden"></div>';
  container.appendChild(section);
  loadSystemPromptConfig(agent.id);
}

function loadSystemPromptConfig(agentId) {
  if (state.selectedAgentId !== agentId) return;
  var loading = document.getElementById('system-prompt-loading');
  var error = document.getElementById('system-prompt-error');
  var items = document.getElementById('system-prompt-items');
  if (!items) return;

  loading.classList.remove('hidden');
  error.classList.add('hidden');
  items.innerHTML = '';

  api('/a/' + agentId + '/api/agent/system-prompt/config').then(function(config) {
    if (state.selectedAgentId !== agentId) return;

    var currentAgent = state.mineAgents?.find(a => a.id === agentId);
    var canWrite = canWriteAgent(currentAgent);
    var ui = getSystemPromptUiModel(currentAgent, config || {});

    for (var i = 0; i < ui.sections.length; i++) {
      var name = ui.sections[i];
      var enabled = ui.forcedEnabled[name] === true ? true : (config[name] && typeof config[name].enabled === 'boolean' ? config[name].enabled : true);
      var isToggleable = ui.canToggle[name] !== false;
      var disabledAttr = canWrite && isToggleable ? '' : ' disabled';
      var label = ui.labels[name] || name;
      var toggleHtml = isToggleable
        ? '<label class="toggle-switch"><input type="checkbox" id="sp-' + name + '-enabled" data-section="' + name + '" ' + (enabled ? 'checked' : '') + ' class="portal-system-prompt-check"' + disabledAttr + '><span class="toggle-slider"></span></label>'
        : '<label class="toggle-switch"><input type="checkbox" id="sp-' + name + '-enabled" data-section="' + name + '" checked class="portal-system-prompt-check" disabled><span class="toggle-slider"></span></label><span class="portal-muted">Always enabled</span>';
      var editAllowed = ui.editable[name] === true;
      var editDisabledAttr = canWrite ? '' : ' disabled';
      var editButton = editAllowed ? '<button data-section="' + name + '" data-action="edit" class="portal-btn is-secondary portal-system-prompt-edit" title="Edit ' + label + '"' + editDisabledAttr + '>Edit</button>' : '';

      var item = document.createElement('div');
      item.className = 'portal-system-prompt-item';
      item.innerHTML = '<div class="portal-checkbox-row">' + toggleHtml + '<span>' + label + '</span></div>' + editButton;
      items.appendChild(item);
    }

    var checkboxes = items.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < checkboxes.length; j++) {
      if (checkboxes[j].disabled) continue;
      checkboxes[j].addEventListener('change', function(e) {
        updateSystemPromptEnabled(state.selectedAgentId, e.target.dataset.section, e.target.checked);
      });
    }

    var editBtns = items.querySelectorAll('button[data-action="edit"]');
    for (var k = 0; k < editBtns.length; k++) {
      editBtns[k].addEventListener('click', (function(btn) {
        return function() {
          editSystemPromptSection(state.selectedAgentId, btn.dataset.section);
        };
      })(editBtns[k]));
    }

    loading.classList.add('hidden');
  }).catch(function(e) {
    error.textContent = 'Failed to load: ' + e.message;
    error.classList.remove('hidden');
    loading.classList.add('hidden');
  });
}

function updateSystemPromptEnabled(agentId, section, enabled) {
  var currentAgent = state.mineAgents?.find(a => a.id === agentId);
  if (isOpenCodeAgent(currentAgent)) {
    if (section !== 'agents') {
      showToast('OpenCode runtime only supports AGENTS.md');
      loadSystemPromptConfig(agentId);
      return;
    }
    if (enabled === false) {
      showToast('AGENTS.md cannot be disabled for OpenCode runtime');
      loadSystemPromptConfig(agentId);
      return;
    }
    return;
  }
  var payload = {};
  payload[section] = { enabled: enabled };
  api('/a/' + agentId + '/api/agent/system-prompt/config', { method: 'PUT', body: JSON.stringify(payload) }).catch(function(e) {
    console.error('Failed to update:', e);
    showToast('Failed to update: ' + e.message);
    loadSystemPromptConfig(agentId);
  });
}

function editSystemPromptSection(agentId, section) {
  var currentAgent = state.mineAgents?.find(a => a.id === agentId);
  if (isOpenCodeAgent(currentAgent) && section !== 'agents') {
    showToast('OpenCode runtime only supports AGENTS.md');
    return;
  }
  api('/a/' + agentId + '/api/agent/system-prompt/' + section).then(function(data) {
    showSystemPromptEditor(agentId, section, data.content || '', data.enabled);
  }).catch(function(e) {
    console.error('Failed to load:', e);
    showToast('Failed to load: ' + e.message);
  });
}

function showSystemPromptEditor(agentId, section, content, enabled) {
  var currentAgent = state.mineAgents?.find(a => a.id === agentId);
  var runtimeIsOpenCode = isOpenCodeAgent(currentAgent);
  var label = resolveSystemPromptLabel(currentAgent, section, {});
  var title = runtimeIsOpenCode && section === 'agents' ? 'AGENTS.md Configuration' : (label + ' Configuration');

  var modal = document.getElementById('system-prompt-editor-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'system-prompt-editor-modal';
    modal.className = 'modal hidden';
    modal.dataset.keyHandlerAttached = '0';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'sp-editor-title');
    modal.innerHTML = '<div class="modal-card panel portal-editor-modal-card"><div class="portal-modal-titlebar"><h3 id="sp-editor-title"></h3><button type="button" id="sp-editor-close" class="portal-modal-close" aria-label="Close">✕</button></div><div class="stack"><div class="portal-checkbox-row"><label class="toggle-switch"><input type="checkbox" id="sp-editor-enabled"><span class="toggle-slider"></span></label><span>Enable custom prompt for this section</span></div><div id="sp-editor-enabled-note" class="portal-inline-state hidden"></div><textarea id="sp-editor-content" class="portal-form-textarea" rows="10" placeholder="Enter content..."></textarea><div class="portal-modal-actions"><button type="button" id="sp-editor-cancel" class="portal-btn is-secondary">Cancel</button><button type="button" id="sp-editor-save" class="portal-btn is-primary">Save</button></div></div></div>';
    document.body.appendChild(modal);
    modal._keyHandler = function(e) { if (e.key === 'Escape') closeSystemPromptEditor(); };
    document.getElementById('sp-editor-close').addEventListener('click', closeSystemPromptEditor);
    modal.addEventListener('click', function(e) { if (e.target === modal) closeSystemPromptEditor(); });
    document.getElementById('sp-editor-cancel').addEventListener('click', closeSystemPromptEditor);
    document.getElementById('sp-editor-save').addEventListener('click', function() {
      var currentAgentId = modal.dataset.agentId;
      var currentSection = modal.dataset.section;
      if (currentAgentId && currentSection) saveSystemPromptSection(currentAgentId, currentSection);
    });
  }

  if (modal.dataset.keyHandlerAttached !== '1') {
    document.addEventListener('keydown', modal._keyHandler);
    modal.dataset.keyHandlerAttached = '1';
  }

  document.getElementById('sp-editor-title').textContent = title;
  var enabledCheckbox = document.getElementById('sp-editor-enabled');
  var enabledNote = document.getElementById('sp-editor-enabled-note');
  if (runtimeIsOpenCode) {
    enabledCheckbox.checked = true;
    enabledCheckbox.disabled = true;
    enabledNote.textContent = 'AGENTS.md is always active for OpenCode.';
    enabledNote.classList.remove('hidden');
  } else {
    enabledCheckbox.checked = enabled;
    enabledCheckbox.disabled = false;
    enabledNote.textContent = '';
    enabledNote.classList.add('hidden');
  }
  document.getElementById('sp-editor-content').value = content;
  modal.dataset.section = section;
  modal.dataset.agentId = agentId;

  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  modal._previousActiveElement = document.activeElement;
  var focusTarget = document.getElementById('sp-editor-content') || enabledCheckbox;
  if (focusTarget && typeof focusTarget.focus === 'function') focusTarget.focus();
}


function closeSystemPromptEditor() {
  var modal = document.getElementById('system-prompt-editor-modal');
  if (!modal) return;

  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');

  if (modal._keyHandler && modal.dataset.keyHandlerAttached === '1') {
    document.removeEventListener('keydown', modal._keyHandler);
    modal.dataset.keyHandlerAttached = '0';
  }

  if (modal._previousActiveElement && typeof modal._previousActiveElement.focus === 'function') {
    modal._previousActiveElement.focus();
    modal._previousActiveElement = null;
  }
}

function saveSystemPromptSection(agentId, section) {
  var currentAgent = state.mineAgents?.find(a => a.id === agentId);
  var runtimeIsOpenCode = isOpenCodeAgent(currentAgent);
  if (runtimeIsOpenCode && section !== 'agents') {
    showToast('OpenCode runtime only supports AGENTS.md');
    return;
  }

  var enabled = document.getElementById('sp-editor-enabled').checked;
  var content = document.getElementById('sp-editor-content').value;
  if (runtimeIsOpenCode) enabled = true;

  api('/a/' + agentId + '/api/agent/system-prompt/' + section, {
    method: 'PUT',
    body: JSON.stringify({ enabled: enabled, content: content })
  }).then(function() {
    closeSystemPromptEditor();
    loadSystemPromptConfig(agentId);
  }).catch(function(e) {
    console.error('Failed to save:', e);
    showToast('Failed to save: ' + e.message);
    loadSystemPromptConfig(agentId);
  });
}

// provider.retry UX copy: Provider API retrying. Check Runtime Profile LLM API key/base URL/proxy.
