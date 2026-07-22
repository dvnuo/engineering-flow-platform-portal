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
  dashboardMenuBtn: document.getElementById("dashboard-menu-btn"),
  railAssistantsBtn: document.getElementById("rail-assistants-btn"),
  usersMenuBtn: document.getElementById("users-menu-btn"),
  tasksMenuBtn: document.getElementById("tasks-menu-btn"),
  delegationsMenuBtn: document.getElementById("delegations-menu-btn"),
  bundlesMenuBtn: document.getElementById("bundles-menu-btn"),
  runtimeProfilesMenuBtn: document.getElementById("runtime-profiles-menu-btn"),
  portalShell: document.querySelector(".portal-shell"),
  portalSecondaryPane: document.getElementById("portal-secondary-pane"),
  secondaryPaneToggle: document.getElementById("secondary-pane-toggle"),
  secondaryPaneRestore: document.getElementById("secondary-pane-restore"),
  secondaryPaneEyebrow: document.getElementById("secondary-pane-eyebrow"),
  secondaryPaneTitle: document.getElementById("secondary-pane-title"),
  secondaryPaneActions: document.getElementById("secondary-pane-actions"),
  dashboardNavSection: document.getElementById("dashboard-nav-section"),
  assistantsNavSection: document.getElementById("assistants-nav-section"),
  bundlesNavSection: document.getElementById("bundles-nav-section"),
  tasksNavSection: document.getElementById("tasks-nav-section"),
  runtimeProfilesNavSection: document.getElementById("runtime-profiles-nav-section"),
  delegationsNavSection: document.getElementById("delegations-nav-section"),
  agentSearchInput: document.getElementById("agent-search-input"),
  agentFilterSummary: document.getElementById("agent-filter-summary"),
  bundleNavList: document.getElementById("bundle-nav-list"),
  taskOwnerFilter: document.getElementById("task-owner-filter"),
  taskStatusFilter: document.getElementById("task-status-filter"),
  taskFilterSummary: document.getElementById("task-filter-summary"),
  taskNavList: document.getElementById("task-nav-list"),
  runtimeProfileNavList: document.getElementById("runtime-profile-nav-list"),
  delegationOwnerFilter: document.getElementById("delegation-owner-filter"),
  delegationSourceFilter: document.getElementById("delegation-source-filter"),
  delegationFilterSummary: document.getElementById("delegation-filter-summary"),
  delegationRuleNavList: document.getElementById("delegation-rule-nav-list"),
  dashboardScopeFilter: document.getElementById("dashboard-scope-filter"),
  dashboardFilterSummary: document.getElementById("dashboard-filter-summary"),
  refreshBundlesBtn: document.getElementById("refresh-bundles-btn"),
  addBundleBtn: document.getElementById("add-bundle-btn"),
  addTaskBtn: document.getElementById("add-task-btn"),
  addRuntimeProfileBtn: document.getElementById("add-runtime-profile-btn"),
  addDelegationBtn: document.getElementById("add-delegation-btn"),
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
const INFLIGHT_CHAT_RUN_STORAGE_PREFIX = "portal-inflight-chat-run";
const REQUIREMENT_BUNDLES_CACHE_KEY = "portal-requirement-bundles-cache-v1";
const UI_LAYOUT_PREFS_STORAGE_KEY = "portal-ui-layout-prefs-v1";
const ALLOWED_UTILITY_PANEL_KEYS = new Set([
  "details",
  "sessions",
  "server-files",
  "skills",
  "usage",
  "users",
]);

const PORTAL_ROUTE_SECTIONS = new Set([
  "dashboard",
  "assistants",
  "bundles",
  "tasks",
  "runtime-profiles",
  "delegations",
]);
const DEFAULT_PORTAL_ROUTE_SECTION = "dashboard";

function initialPortalRouteSectionFromHash(hash = window.location.hash) {
  const raw = typeof hash === "string" ? hash : "";
  if (!raw || raw === "#") return DEFAULT_PORTAL_ROUTE_SECTION;
  const withoutHash = raw.startsWith("#") ? raw.slice(1) : raw;
  const routeText = withoutHash.startsWith("/") ? withoutHash.slice(1) : withoutHash;
  if (!routeText) return DEFAULT_PORTAL_ROUTE_SECTION;
  const queryIndex = routeText.indexOf("?");
  const pathPart = queryIndex >= 0 ? routeText.slice(0, queryIndex) : routeText;
  const encodedSection = pathPart.split("/")[0] || "";
  try {
    const section = decodeURIComponent(encodedSection);
    return PORTAL_ROUTE_SECTIONS.has(section) ? section : DEFAULT_PORTAL_ROUTE_SECTION;
  } catch (_error) {
    return DEFAULT_PORTAL_ROUTE_SECTION;
  }
}

const INITIAL_PORTAL_ROUTE_SECTION = initialPortalRouteSectionFromHash();

function initialPortalSectionTitle(section) {
  if (section === "dashboard") return "Dashboard";
  if (section === "bundles") return "Bundles";
  if (section === "tasks") return "Tasks";
  if (section === "runtime-profiles") return "Runtime Profiles";
  if (section === "delegations") return "Delegations";
  return "Assistants";
}

function initialPortalStatusText(section) {
  if (section === "dashboard") return "Operational overview across assistants, tasks, and delegations";
  if (section === "bundles") return "Browse and open bundle detail in the main stage";
  if (section === "tasks") return "Browse tasks and open task detail in the main stage";
  if (section === "runtime-profiles") return "Browse and manage your runtime profiles";
  if (section === "delegations") return "Manage delegations";
  return "Ready";
}

function applyInitialPortalRouteShell(section = INITIAL_PORTAL_ROUTE_SECTION) {
  const normalized = PORTAL_ROUTE_SECTIONS.has(section) ? section : DEFAULT_PORTAL_ROUTE_SECTION;
  const railButtons = {
    dashboard: dom.dashboardMenuBtn,
    assistants: dom.railAssistantsBtn,
    bundles: dom.bundlesMenuBtn,
    tasks: dom.tasksMenuBtn,
    "runtime-profiles": dom.runtimeProfilesMenuBtn,
    delegations: dom.delegationsMenuBtn,
  };
  const navSections = {
    dashboard: dom.dashboardNavSection,
    assistants: dom.assistantsNavSection,
    bundles: dom.bundlesNavSection,
    tasks: dom.tasksNavSection,
    "runtime-profiles": dom.runtimeProfilesNavSection,
    delegations: dom.delegationsNavSection,
  };
  Object.entries(railButtons).forEach(([key, element]) => {
    element?.classList.toggle("is-active", key === normalized);
  });
  Object.entries(navSections).forEach(([key, element]) => {
    element?.classList.toggle("hidden", key !== normalized);
  });

  const actionButtons = [
    dom.addAgentBtn,
    dom.refreshBundlesBtn,
    dom.addBundleBtn,
    dom.addTaskBtn,
    dom.addRuntimeProfileBtn,
    dom.addDelegationBtn,
  ];
  actionButtons.forEach((button) => button?.classList.add("hidden"));
  if (normalized === "assistants") dom.addAgentBtn?.classList.remove("hidden");
  if (normalized === "bundles") {
    dom.refreshBundlesBtn?.classList.remove("hidden");
    dom.addBundleBtn?.classList.remove("hidden");
  }
  if (normalized === "tasks") dom.addTaskBtn?.classList.remove("hidden");
  if (normalized === "runtime-profiles") dom.addRuntimeProfileBtn?.classList.remove("hidden");
  if (normalized === "delegations") dom.addDelegationBtn?.classList.remove("hidden");

  const title = initialPortalSectionTitle(normalized);
  if (dom.secondaryPaneEyebrow) {
    dom.secondaryPaneEyebrow.textContent = normalized === "assistants" ? "My Space" : (normalized === "dashboard" ? "Portal" : "Workspace");
  }
  if (dom.secondaryPaneTitle) dom.secondaryPaneTitle.textContent = title;

  const assistantOnlyControls = [
    document.getElementById("btn-sessions"),
    dom.headerNewChatBtn,
    dom.detailToggle,
    document.getElementById("btn-files"),
  ];
  assistantOnlyControls.forEach((element) => {
    element?.classList.toggle("hidden", normalized !== "assistants");
  });

  if (normalized === "assistants") {
    dom.centerPlaceholder?.classList.remove("hidden");
    dom.workspaceDetailView?.classList.add("hidden");
    dom.agentChatApp?.classList.add("hidden");
    return;
  }

  dom.centerPlaceholder?.classList.add("hidden");
  dom.agentChatApp?.classList.add("hidden");
  dom.workspaceDetailView?.classList.remove("hidden");
  if (dom.workspaceDetailContent) {
    dom.workspaceDetailContent.dataset.workspaceState = `${normalized}-initial-loading`;
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state">Loading ${title.toLowerCase()}...</div>`;
  }
  if (dom.embedTitle) dom.embedTitle.textContent = title;
  if (dom.chatStatus) dom.chatStatus.textContent = initialPortalStatusText(normalized);
}

applyInitialPortalRouteShell();

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

function getInflightChatRunKey(agentId) {
  return `${INFLIGHT_CHAT_RUN_STORAGE_PREFIX}-${agentId || "unknown"}`;
}

function persistInflightChatRun(agentId, run = {}) {
  if (!agentId || !run?.session_id || !run?.request_id) return;
  const payload = {
    agent_id: agentId,
    session_id: String(run.session_id || ""),
    request_id: String(run.request_id || ""),
    message_preview: String(run.message_preview || "").slice(0, 240),
    started_at: run.started_at || new Date().toISOString(),
  };
  try {
    localStorage.setItem(getInflightChatRunKey(agentId), JSON.stringify(payload));
  } catch {}
}

function getPersistedInflightChatRun(agentId) {
  if (!agentId) return null;
  try {
    const parsed = JSON.parse(localStorage.getItem(getInflightChatRunKey(agentId)) || "null");
    if (!parsed || typeof parsed !== "object") return null;
    if (String(parsed.agent_id || "") !== String(agentId)) return null;
    if (!parsed.session_id || !parsed.request_id) return null;
    return parsed;
  } catch {
    return null;
  }
}

function clearPersistedInflightChatRun(agentId, requestId = "") {
  if (!agentId) return;
  if (requestId) {
    const existing = getPersistedInflightChatRun(agentId);
    if (existing && String(existing.request_id || "") !== String(requestId)) return;
  }
  try {
    localStorage.removeItem(getInflightChatRunKey(agentId));
  } catch {}
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
  agentFilters: { query: "", scope: "all", status: "all" },
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
  activeNavSection: INITIAL_PORTAL_ROUTE_SECTION,
  dashboardScope: "all",
  secondaryPaneCollapsed: !!initialUiLayoutPrefs.secondaryPaneCollapsed,
  toolPanelOpen: !!initialUiLayoutPrefs.toolPanelPinned,
  toolPanelPinned: !!initialUiLayoutPrefs.toolPanelPinned,
  pendingToolPanelRestoreKey: normalizeUtilityPanelKey(initialUiLayoutPrefs.activeUtilityPanel),
  myTasks: [],
  taskPageSize: 20,
  taskListOffset: 0,
  taskListHasMore: true,
  taskListLoading: false,
  taskFilters: { status: "all", owner: "all" },
  selectedTaskId: null,
  serverFilesRootPath: null,
  serverFilesCurrentPath: null,
  runtimeProfiles: [],
  selectedRuntimeProfileId: null,
  delegations: [],
  delegationFilters: { owner: "all", source: "all" },
  selectedDelegationRuleId: null,
  agentDefaults: null,
  gitRepoBranches: new Map(),
  createAgentStep: "runtime",
};
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
    section: DEFAULT_PORTAL_ROUTE_SECTION,
    agentId: "",
    taskId: "",
    runtimeProfileId: "",
    delegationRuleId: "",
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
  } else if (section === "delegations") {
    parsed.delegationRuleId = decodedId;
  }
  return parsed;
}

function portalHashForRoute(route = {}) {
  const section = PORTAL_ROUTE_SECTIONS.has(route?.section) ? route.section : DEFAULT_PORTAL_ROUTE_SECTION;

  if (section === "assistants") {
    const agentId = route.agentId ? String(route.agentId) : "";
    return agentId ? `#/assistants/${encodeURIComponent(agentId)}` : "#/assistants";
  }

  if (section === "dashboard") {
    return "#/dashboard";
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

  if (section === "delegations") {
    const delegationRuleId = route.delegationRuleId ? String(route.delegationRuleId) : "";
    return delegationRuleId ? `#/delegations/${encodeURIComponent(delegationRuleId)}` : "#/delegations";
  }

  return "#/dashboard";
}

function currentPortalRouteFromState() {
  const section = PORTAL_ROUTE_SECTIONS.has(state.activeNavSection) ? state.activeNavSection : DEFAULT_PORTAL_ROUTE_SECTION;

  if (section === "assistants") {
    return { section, agentId: state.selectedAgentId || "" };
  }

  if (section === "dashboard") {
    return { section };
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

  if (section === "delegations") {
    return { section, delegationRuleId: state.selectedDelegationRuleId || "" };
  }

  return portalSectionRoute(DEFAULT_PORTAL_ROUTE_SECTION);
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
  } else if (section === "delegations") {
    state.selectedDelegationRuleId = null;
  }
}

function portalSectionRoute(section) {
  const normalized = PORTAL_ROUTE_SECTIONS.has(section) ? section : DEFAULT_PORTAL_ROUTE_SECTION;
  if (normalized === "dashboard") {
    return { section: "dashboard" };
  }
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
    (section === "delegations" && !!state.selectedDelegationRuleId)
  );

  // Section-only navigation means the user clicked the rail/menu to open the column page,
  // not a specific detail item.
  clearPortalSectionDetailSelection(section);

  const opensDetailByDefault = section === "runtime-profiles";

  if (!isApplyingPortalRoute && !opensDetailByDefault) {
    commitPortalRoute(portalSectionRoute(section), { replace });
  }

  await setActiveNavSection(section, {
    toggleIfSame: toggleIfSame && !hadDetailSelection,
    updateRoute: opensDetailByDefault,
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
      section: DEFAULT_PORTAL_ROUTE_SECTION,
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

  const shouldNormalizeRuntimeProfileLandingRoute =
    route.valid &&
    route.section === "runtime-profiles" &&
    !route.runtimeProfileId &&
    state.selectedRuntimeProfileId;
  if (shouldNormalizeRuntimeProfileLandingRoute) {
    commitPortalRoute(
      { section: "runtime-profiles", runtimeProfileId: state.selectedRuntimeProfileId },
      { replace: true }
    );
    return;
  }

  if (!parsed.hadHash || (replaceInvalid && !parsed.valid)) {
    commitPortalRoute(currentPortalRouteFromState(), { replace: true });
  }
}

async function applyPortalRoute(route, { replaceInvalid = false } = {}) {
  if (!route || !PORTAL_ROUTE_SECTIONS.has(route.section)) {
    await setActiveNavSection(DEFAULT_PORTAL_ROUTE_SECTION, { toggleIfSame: false, updateRoute: false });
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

  if (route.section === "dashboard") {
    await setActiveNavSection("dashboard", {
      toggleIfSame: false,
      updateRoute: false,
      preferSectionLanding: true,
    });
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

  if (route.section === "delegations") {
    if (route.delegationRuleId) {
      await setActiveNavSection("delegations", { toggleIfSame: false, updateRoute: false });
      await loadDelegationRules();
      await openDelegationRulePanel(route.delegationRuleId, { updateRoute: false });
    } else {
      await setActiveNavSection("delegations", {
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
    inflightEventStream: null,
    inflightAgentTimeline: null,
    lastAgentTimelineSnapshot: null,
    lastEventStreamSnapshot: null,
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
      showToast('Upload failed: ' + error.message, { variant: 'error' });
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
    let progressBar = '';
    if (pf.status === 'uploading') {
      const hasPct = typeof pf.uploadProgress === 'number';
      const pct = hasPct ? Math.max(0, Math.min(100, Math.round(pf.uploadProgress))) : 0;
      const pctLabel = hasPct ? ` (${pct}%)` : '';
      statusBadge = `<span class="input-preview-badge is-uploading" title="Uploading${pctLabel}" aria-hidden="true">⏳</span>`;
      progressBar = `<div class="input-preview-progress" style="position:absolute;left:0;right:0;bottom:0;height:3px;background:rgba(148,163,184,0.35);border-bottom-left-radius:inherit;border-bottom-right-radius:inherit;overflow:hidden;"><div style="height:100%;width:${pct}%;background:#3b82f6;transition:width .15s ease;"></div></div>`;
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
    return `<div class="input-preview-card" data-id="${safeId}" data-preview-url="${safePreviewUrl}" data-preview-name="${safePreviewName}" data-is-image="${pf.isImage ? 'true' : 'false'}">${statusBadge}${content}<button type="button" class="remove-btn" aria-label="Remove attachment" data-remove-id="${safeId}">×</button>${progressBar}</div>`;
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

    pf.uploadProgress = 0;
    xhr.upload.addEventListener('progress', (event) => {
      if (pf.cancelled) return;
      if (event.lengthComputable && event.total > 0) {
        pf.uploadProgress = Math.round((event.loaded / event.total) * 100);
        renderInputPreview();
      }
    });

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
  const name = typeof item === "string" ? item : (skill?.name || skill?.runtime_name || skill?.opencode_name || skill?.efp_name || "");
  const normalizedName = String(name || "").trim().toLowerCase().replaceAll("_", "-");
  const command = normalizeSkillCommand(normalizedName);
  const blockedReason = skill?.blocked_reason || "";
  const compatibilityWarnings = Array.isArray(skill?.compatibility_warnings) ? skill.compatibility_warnings.filter(Boolean).join(" · ") : "";
  const isCallable = skill?.callable !== false;
  const status = isCallable ? "" : "Not callable";
  const descBase = typeof item === "string" ? "Skill" : (skill?.description || "Skill");
  const desc = [descBase, status, blockedReason || compatibilityWarnings].filter(Boolean).join(" · ");
  const missingTools = Array.isArray(skill?.missing_tools) ? skill.missing_tools.join(", ") : "";
  const missingRuntimeTools = Array.isArray(skill?.missing_runtime_tools)
    ? skill.missing_runtime_tools.join(", ")
    : (Array.isArray(skill?.missing_opencode_tools) ? skill.missing_opencode_tools.join(", ") : "");
  const titleText = [blockedReason, compatibilityWarnings].filter(Boolean).join(" · ");
  return {
    label: command,
    command,
    desc,
    title: titleText,
    callable: isCallable,
    blocked_reason: blockedReason,
    runtime_compatibility: skill?.runtime_compatibility || skill?.opencode_compatibility || "",
    runtime_equivalence: skill?.runtime_equivalence ?? "",
    programmatic: skill?.programmatic,
    permission_state: skill?.permission_state || "",
    missing_tools: missingTools,
    missing_runtime_tools: missingRuntimeTools,
    runtime_name: skill?.runtime_name || skill?.opencode_name || "",
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
      skill?.runtime_name,
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
        source_of_truth: "runtime",
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
      source_of_truth: "runtime",
      runtime_message_id: id,
      runtime_role: role,
      canonical_parts: message.parts || [],
    },
  };
}

function canonicalMessagesToLegacyDisplayMessages(canonicalMessages = []) {
  return canonicalMessages
    .map((message, index) => canonicalMessageToLegacyDisplayMessage(message, index))
    .filter((message) => message.role !== "assistant" || String(message.display_content || message.content || "").trim());
}

function canonicalPartToStreamItem(message = {}, part = {}, index = 0) {
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

function canonicalMessagesToStreamItems(canonicalMessages = []) {
  const out = [];
  canonicalMessages.forEach((message) => {
    const parts = Array.isArray(message.parts) ? message.parts : [];
    parts.forEach((part, index) => {
      const item = canonicalPartToStreamItem(message, part, index);
      if (item) out.push(item);
    });
  });
  return out;
}

function canonicalStreamItemToRuntimeEvent(item = {}, sessionId = "") {
  const baseData = {
    ...item,
    message_id: item.message_id || "",
    part_id: item.part_id || "",
    session_id: sessionId || "",
    source_of_truth: "runtime",
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
    source_of_truth: "runtime",
  });

  if (item.kind === "reasoning") {
    return baseEvent("runtime.reasoning", {
      ...baseData,
      message: item.text || "Reasoning update",
      status: item.status || "running",
    }, item.text || "Reasoning update");
  }
  if (item.kind === "tool") {
    return baseEvent("runtime.tool", {
      ...baseData,
      message: item.tool ? `${item.tool} ${item.status || "running"}` : "Tool update",
    }, item.tool || "Tool update");
  }
  if (item.kind === "step_start") {
    return baseEvent("runtime.step.started", {
      ...baseData,
      message: "Step started",
    }, "Step started");
  }
  if (item.kind === "step_finish") {
    return baseEvent("runtime.step.finished", {
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

function canonicalMessagesToStreamEvents(canonicalMessages = [], sessionId = "") {
  return canonicalMessagesToStreamItems(canonicalMessages)
    .map((item) => canonicalStreamItemToRuntimeEvent(item, sessionId))
    .filter(Boolean);
}

function applyCanonicalMessagesToChatState(agentId, sessionId, chatState, canonicalMessages = [], metadata = {}) {
  if (!chatState || !Array.isArray(canonicalMessages) || !canonicalMessages.length) return;
  const canonicalEvents = canonicalMessagesToStreamEvents(canonicalMessages, sessionId);
  if (!canonicalEvents.length) return;
  const target = chatState.inflightEventStream && chatState.inflightEventStream.completed !== true
    ? chatState.inflightEventStream
    : null;
  const existing = target || chatState.lastEventStreamSnapshot || {};
  const requestId = String(metadata.request_id || metadata.latest_request_id || existing.requestId || existing.id || "");
  const merged = {
    ...existing,
    id: existing.id || requestId || `canonical-${sessionId || Date.now()}`,
    requestId,
    sessionId: sessionId || existing.sessionId || "",
    events: mergeRuntimeStreamEvents(existing.events || [], canonicalEvents),
    contextSource: "runtime_canonical",
    completed: target ? false : (existing.completed ?? true),
    status: target ? (existing.status || "connected") : (existing.status || "snapshot"),
    lastEventAt: Date.now(),
  };
  if (target) Object.assign(target, merged);
  else chatState.lastEventStreamSnapshot = merged;
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
  return (group?.messages || []).map((m) => m?.id || m?.message_id || m?.metadata?.runtime_message_id || m?.metadata?.opencode_message_id || "").filter(Boolean);
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
  return `<div class="message-row message-row-assistant" data-temporary-assistant="1"${clientRequestAttr}><div class="message-meta"><span class="message-author">${escapeHtml(pendingAgentName)}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-assistant assistant-message is-pending pending-assistant" data-pending-assistant="1"${clientRequestAttr}><div class="assistant-waiting-indicator">${escapeHtml(pendingText)}<span class="assistant-waiting-dots"></span></div><div class="message-markdown md-render max-w-none text-sm" data-md="" data-display-blocks="[]"></div><div class="agent-timeline" data-agent-timeline="1"><div class="agent-timeline-head"><span class="agent-timeline-status"><span class="portal-running-spinner" aria-hidden="true"></span><span data-agent-timeline-status-text>Working</span></span><span class="agent-timeline-meta" data-agent-timeline-meta></span></div><div class="agent-timeline-list" data-agent-timeline-items></div></div></article></div>`;
}

function findPendingAssistantArticle(requestId = "") {
  if (!dom.messageList) return null;
  const normalizedRequestId = String(requestId || "").trim();
  if (normalizedRequestId) {
    const escaped = CSS.escape(normalizedRequestId);
    const exact = dom.messageList.querySelector(
      [
        `article[data-pending-assistant="1"][data-client-request-id="${escaped}"]`,
        `article[data-pending-assistant="1"][data-request-id="${escaped}"]`,
      ].join(",")
    );
    if (exact) return exact;
  }
  return dom.messageList.querySelector('article[data-pending-assistant="1"]');
}

function removePendingAssistantArticle(requestId = "") {
  const article = findPendingAssistantArticle(requestId);
  if (!article) return;
  const row = article.closest(".message-row");
  (row || article).remove();
}

function renderRecoveredPendingAssistantArticle(agentId, requestId, pendingText = "Reconnecting") {
  if (state.selectedAgentId !== agentId || !dom.messageList || !requestId) return false;
  if (findPendingAssistantArticle(requestId)) return false;
  dom.messageList.insertAdjacentHTML("beforeend", buildPendingAssistantArticle(requestId, pendingText));
  renderIcons();
  scrollToBottom();
  return true;
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
    || latest.dataset.runtimeMessageId
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
    || exact?.dataset.runtimeMessageId
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
    || last.dataset.runtimeMessageId
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
  if (normalized.startsWith("session.next.")) return normalized;
  const aliases = {
    // Transitional parser only for older runtime builds; UI uses runtime.* names.
    "opencode.reasoning": "runtime.reasoning",
    "opencode.tool": "runtime.tool",
    "opencode.step.started": "runtime.step.started",
    "opencode.step.finished": "runtime.step.finished",
    "opencode.raw": "runtime.raw",
    "opencode.status.validated": "runtime.status.validated",
  };
  if (aliases[normalized]) return aliases[normalized];
  if (normalized.startsWith("opencode.")) return "runtime.raw";
  return normalized;
}

function isTrackableStreamEvent(type) {
  const localNormalizeRuntimeEventTypeAlias = (value) => {
    if (typeof normalizeRuntimeEventTypeAlias === "function") {
      return normalizeRuntimeEventTypeAlias(value);
    }
    return String(value || "").trim();
  };
  const normalizedType = localNormalizeRuntimeEventTypeAlias(type);
  if (
    normalizedType === "session.next.prompted"
    || normalizedType.startsWith("session.next.step.")
    || normalizedType.startsWith("session.next.text.")
    || normalizedType.startsWith("session.next.reasoning.")
    || normalizedType.startsWith("session.next.tool.")
    || normalizedType.startsWith("session.next.compaction.")
  ) {
    return true;
  }
  return [
    "stream.started",
    "chat.started", "heartbeat", "status",
    "execution.started", "execution.completed", "execution.failed",
    "execution.incomplete", "execution.blocked",
    "runtime.reasoning", "runtime.tool", "runtime.step.started", "runtime.step.finished",
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
    "message.started", "message.delta", "assistant_delta", "message.completed", "message.failed",
    "tool.started", "tool.completed", "tool.failed",
    "tool.error",
    "permission.requested", "permission.resolved", "permission_request", "permission_resolved",
    "permission.denied", "permission.allowed",
    "question.requested",
    "provider.retry", "provider.status", "provider.rate_limit", "model.retry",
    "event_bridge.connected", "event_bridge.disconnected", "event_bridge.reconnected", "runtime.raw",
    "runtime.status.validated",
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
  const candidateProperties = (candidate?.properties && typeof candidate.properties === "object")
    ? candidate.properties
    : {};
  const baseProperties = (baseData?.properties && typeof baseData.properties === "object")
    ? baseData.properties
    : {};
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
    ...candidateProperties,
    ...baseProperties,
    ...baseData,
    ...detailPayload,
  };
  if (
    !mergedData.properties
    && (Object.keys(candidateProperties).length || Object.keys(baseProperties).length)
  ) {
    mergedData.properties = {
      ...candidateProperties,
      ...baseProperties,
    };
  }

  if (candidate?.summary && !mergedData.message) mergedData.message = candidate.summary;
  if (candidate?.state && !mergedData.state) mergedData.state = candidate.state;
  const pickField = (...keys) => {
    for (const source of [candidate, baseData, candidateProperties, baseProperties, mergedData]) {
      if (!source || typeof source !== "object") continue;
      for (const key of keys) {
        const value = source[key];
        if (value !== undefined && value !== null && String(value).trim()) return value;
      }
    }
    return "";
  };
  const requestId = pickField("request_id", "requestId", "requestID", "client_request_id", "clientRequestId");
  const sessionId = pickField("session_id", "sessionId", "sessionID");
  const agentId = pickField("agent_id", "agentId", "agentID");
  if (requestId && !mergedData.request_id) mergedData.request_id = requestId;
  if (sessionId && !mergedData.session_id) mergedData.session_id = sessionId;
  if (agentId && !mergedData.agent_id) mergedData.agent_id = agentId;
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
  const failedByType = rawType === "execution.failed" || rawType === "session.next.step.failed";
  const failedByResult = rawType === "tool_result" && mergedData.success === false;
  const completionByType = rawType === "complete" || rawType === "execution.completed" || rawType === "session.next.step.ended";
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
  } else if (rawType === "execution.started" || rawType === "session.next.step.started" || stateValue === "started") {
    lifecycleType = "execution.started";
    if (rawType === "execution.started") normalizedType = "execution.started";
  }

  return {
    type: normalizedType,
    raw_type: rawTypeValue || rawType,
    lifecycle_type: lifecycleType,
    data: mergedData,
    outer_event_type: outerType,
    session_id: sessionId || mergedData.session_id || "",
    request_id: requestId || mergedData.request_id || "",
    agent_id: agentId || mergedData.agent_id || "",
    ts,
    state: candidate?.state || mergedData.state || "",
    event_id: pickField("runtime_event_id", "runtimeEventId", "event_id", "eventId", "id"),
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
  const rawEventType = String(event.raw_type || data.raw_event_type || data.raw_type || "");
  const createdAt = event.created_at || data.created_at || event.ts || "";
  const callId = data.callID || data.callId || data.call_id || data.tool_call_id || data.toolCallId || "";
  const partId = data.partID || data.partId || data.part_id || data.message_id || data.messageId || "";
  const deltaIndex = data.delta_index || data.deltaIndex || data.index || data.sequence || data.seq || "";
  const summary = event.summary || data.summary || data.message || data.delta || data.text || data.input || data.output || data.result || "";
  const payloadHash = runtimeEventSummaryHash(typeof summary === "string" ? summary : JSON.stringify(summary));
  return `${eventType}|${rawEventType}|${createdAt}|${callId}|${partId}|${deltaIndex}|${payloadHash}`;
}

function mergeRuntimeStreamEvents(primaryEvents, secondaryEvents) {
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

function normalizeRuntimeStreamEvents(events) {
  if (!Array.isArray(events)) return [];
  return events
    .map((event) => normalizeRuntimeEvent(event) || event)
    .filter((event) => event && typeof event === "object");
}

function normalizePayloadStreamEvents(events) {
  return normalizeRuntimeStreamEvents(events);
}

function createAgentTimelineState({ requestId = "", sessionId = "" } = {}) {
  return {
    requestId: String(requestId || ""),
    sessionId: String(sessionId || ""),
    assistantText: "",
    eventsById: {},
    items: [],
    toolsByCallId: {},
    reasoningById: {},
    textById: {},
    completed: false,
    status: "running",
    model: "",
    agent: "",
    startedAt: Date.now(),
    updatedAt: Date.now(),
    completedAt: null,
    visibleItemCount: 0,
    revealTimer: null,
    revealIntervalMs: 280,
    lastRevealAt: 0,
  };
}

function isAgentTimelinePacedItem(item) {
  if (!item) return false;
  const kind = String(item.kind || "event").toLowerCase();
  return kind !== "step" && kind !== "text";
}

function getAgentTimelineRenderableItems(timeline) {
  if (!timeline) return [];
  return (timeline.items || [])
    .filter((item) => item && String(item.kind || "").toLowerCase() !== "step")
    .slice(-24);
}

function getAgentTimelineVisibleItems(timeline) {
  const items = getAgentTimelineRenderableItems(timeline);
  if (!items.length) return [];
  const pacedTotal = items.filter(isAgentTimelinePacedItem).length;
  const pacedLimit = Math.max(0, Math.min(Number(timeline?.visibleItemCount || 0), pacedTotal));
  let pacedSeen = 0;
  return items.filter((item) => {
    if (!isAgentTimelinePacedItem(item)) return true;
    pacedSeen += 1;
    return pacedSeen <= pacedLimit;
  });
}

function advanceAgentTimelineReveal(timeline, count = 1) {
  if (!timeline) return 0;
  const items = getAgentTimelineRenderableItems(timeline);
  const pacedTotal = items.filter(isAgentTimelinePacedItem).length;
  const current = Math.max(0, Math.min(Number(timeline.visibleItemCount || 0), pacedTotal));
  const increment = count === Infinity
    ? pacedTotal
    : Math.max(0, Number.isFinite(Number(count)) ? Math.floor(Number(count)) : 0);
  timeline.visibleItemCount = Math.min(pacedTotal, current + increment);
  if (increment > 0) timeline.lastRevealAt = Date.now();
  return timeline.visibleItemCount;
}

function clearAgentTimelineRevealTimer(timeline) {
  if (!timeline?.revealTimer) return;
  clearTimeout(timeline.revealTimer);
  timeline.revealTimer = null;
}

function dropInflightAgentTimelineState(chatState) {
  if (!chatState?.inflightAgentTimeline) return;
  clearAgentTimelineRevealTimer(chatState.inflightAgentTimeline);
  chatState.inflightAgentTimeline = null;
}

function scheduleAgentTimelineReveal(agentId, chatState, requestCtx = {}) {
  const timeline = chatState?.inflightAgentTimeline;
  if (!timeline || timeline.completed) {
    clearAgentTimelineRevealTimer(timeline);
    return;
  }
  const pacedTotal = getAgentTimelineRenderableItems(timeline).filter(isAgentTimelinePacedItem).length;
  if (Number(timeline.visibleItemCount || 0) >= pacedTotal) {
    clearAgentTimelineRevealTimer(timeline);
    return;
  }
  if (timeline.revealTimer) return;
  const timelineRequestId = String(timeline.requestId || "");
  const intervalMs = Math.max(220, Math.min(350, Number(timeline.revealIntervalMs || 280) || 280));
  timeline.revealTimer = setTimeout(() => {
    timeline.revealTimer = null;
    const activeTimeline = chatState?.inflightAgentTimeline;
    if (!activeTimeline || activeTimeline !== timeline) return;
    if (timelineRequestId && String(activeTimeline.requestId || "") !== timelineRequestId) return;
    advanceAgentTimelineReveal(activeTimeline, 1);
    if (typeof renderAgentTimelineForCurrentRequest === "function") {
      renderAgentTimelineForCurrentRequest(agentId, chatState, { requestCtx });
    }
    scheduleAgentTimelineReveal(agentId, chatState, requestCtx);
  }, intervalMs);
}

function ensureAgentTimelineState(chatState, event = {}) {
  if (!chatState) return null;
  const requestId = String(
    event?.request_id
    || event?.data?.request_id
    || chatState.currentRequest?.requestId
    || chatState.currentRequest?.clientRequestId
    || ""
  );
  const sessionId = String(
    event?.session_id
    || event?.data?.session_id
    || chatState.currentRequest?.sessionIdAtSend
    || chatState.sessionId
    || ""
  );
  if (
    !chatState.inflightAgentTimeline
    || (requestId && chatState.inflightAgentTimeline.requestId && chatState.inflightAgentTimeline.requestId !== requestId && chatState.inflightAgentTimeline.completed)
  ) {
    clearAgentTimelineRevealTimer(chatState.inflightAgentTimeline);
    chatState.inflightAgentTimeline = createAgentTimelineState({ requestId, sessionId });
  }
  const timeline = chatState.inflightAgentTimeline;
  if (requestId && !timeline.requestId) timeline.requestId = requestId;
  if (sessionId && !timeline.sessionId) timeline.sessionId = sessionId;
  return timeline;
}

function getAgentTimelineEventData(event = {}) {
  const data = event?.data && typeof event.data === "object" ? event.data : {};
  const properties = data?.properties && typeof data.properties === "object" ? data.properties : {};
  return { ...properties, ...data };
}

function getAgentTimelineField(event, keys = []) {
  const data = getAgentTimelineEventData(event);
  for (const source of [data, event]) {
    if (!source || typeof source !== "object") continue;
    for (const key of keys) {
      const value = source[key];
      if (value !== undefined && value !== null && String(value).trim()) return value;
    }
  }
  return "";
}

function normalizeAgentTimelineJsonText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function truncateAgentTimelineText(value, max = 360) {
  const text = normalizeAgentTimelineJsonText(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > max ? `${text.slice(0, Math.max(0, max - 3))}...` : text;
}

function getAgentTimelineCallId(event) {
  return String(getAgentTimelineField(event, [
    "callID", "callId", "call_id", "tool_call_id", "toolCallId",
  ]) || "");
}

function getAgentTimelinePartId(event) {
  return String(getAgentTimelineField(event, [
    "partID", "partId", "part_id", "message_id", "messageId",
  ]) || "");
}

function getAgentTimelineEventKey(event, timeline = null) {
  const id = runtimeEventUniqueId(event);
  if (id) return `id:${id}`;
  const data = getAgentTimelineEventData(event);
  const type = normalizeRuntimeEventTypeAlias(event?.type || event?.event_type || data.type || data.event_type || "");
  const createdAt = event?.created_at || data.created_at || "";
  const callId = getAgentTimelineCallId(event);
  const partId = getAgentTimelinePartId(event);
  const deltaIndex = String(data.delta_index ?? data.deltaIndex ?? data.index ?? data.sequence ?? data.seq ?? "");
  const summary = event?.summary || data.summary || data.message || data.delta || data.text || data.input || data.output || data.result || "";
  const payloadHash = runtimeEventSummaryHash(normalizeAgentTimelineJsonText(summary));
  const stableKey = `${type}|${createdAt}|${callId}|${partId}|${deltaIndex}|${payloadHash}`;
  if (deltaIndex || payloadHash !== "0") return stableKey;
  const fallbackIndex = timeline ? String((timeline.items || []).length) : "";
  return `${stableKey}|${fallbackIndex}`;
}

function findAgentTimelineItem(timeline, itemId) {
  if (!timeline || !itemId) return null;
  return (timeline.items || []).find((item) => item.id === itemId) || null;
}

function upsertAgentTimelineItem(timeline, itemId, defaults = {}, updates = {}) {
  if (!timeline || !itemId) return null;
  let item = findAgentTimelineItem(timeline, itemId);
  if (!item) {
    item = {
      id: itemId,
      kind: defaults.kind || "event",
      status: defaults.status || "running",
      title: defaults.title || "Runtime event",
      summary: "",
      detail: "",
      createdAt: defaults.createdAt || new Date().toISOString(),
      updatedAt: Date.now(),
      order: timeline.items.length,
      icon: defaults.icon || "circle",
      weak: Boolean(defaults.weak),
    };
    timeline.items.push(item);
  }
  Object.assign(item, updates);
  item.updatedAt = Date.now();
  return item;
}

function appendAgentTimelineText(timeline, value, { replace = false } = {}) {
  if (!timeline) return;
  const text = String(value || "");
  if (replace) {
    timeline.assistantText = text;
  } else if (text) {
    timeline.assistantText = `${timeline.assistantText || ""}${text}`;
  }
}

function agentTimelineToolName(event) {
  const value = getAgentTimelineField(event, [
    "tool", "tool_name", "toolName", "name", "command",
  ]);
  return String(value || "Tool");
}

function agentTimelineInputText(event) {
  const data = getAgentTimelineEventData(event);
  return normalizeAgentTimelineJsonText(
    data.input
    ?? data.args
    ?? data.arguments
    ?? data.parameters
    ?? data.command
    ?? data.delta
    ?? ""
  );
}

function agentTimelineOutputText(event) {
  const data = getAgentTimelineEventData(event);
  return normalizeAgentTimelineJsonText(
    data.output
    ?? data.result
    ?? data.response
    ?? data.content
    ?? data.error
    ?? data.message
    ?? ""
  );
}

function agentTimelineReasoningText(event) {
  const data = getAgentTimelineEventData(event);
  if (data.hidden === true || data.is_hidden === true) return "";
  return normalizeAgentTimelineJsonText(
    data.summary
    ?? data.delta
    ?? data.message
    ?? (event?.type === "llm_thinking" ? data.thinking : "")
    ?? ""
  );
}

function reduceAgentTimelineStepEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const stepId = `step:${timeline.requestId || getAgentTimelinePartId(event) || "current"}`;
  if (type === "session.next.step.started" || type === "execution.started" || type === "chat.started" || type === "session.next.prompted") {
    timeline.completed = false;
    timeline.status = "running";
    timeline.model = String(data.model || data.model_id || data.modelID || timeline.model || "");
    timeline.agent = String(data.agent || data.agent_name || data.agentName || timeline.agent || "");
    upsertAgentTimelineItem(timeline, stepId, {
      kind: "step",
      status: "running",
      title: "Agent step",
      icon: "play-circle",
      createdAt: event.created_at,
    }, {
      kind: "step",
      status: "running",
      title: data.title || "Agent step started",
      summary: truncateAgentTimelineText(data.message || data.prompt || data.status || "Working"),
      model: timeline.model,
      agent: timeline.agent,
      icon: "play-circle",
    });
    return true;
  }
  if (type === "session.next.step.failed" || type === "execution.failed" || type === "chat.failed") {
    timeline.completed = true;
    timeline.status = "failed";
    timeline.completedAt = Date.now();
    upsertAgentTimelineItem(timeline, stepId, {
      kind: "step",
      status: "failed",
      title: "Agent step failed",
      icon: "x-circle",
      createdAt: event.created_at,
    }, {
      status: "failed",
      title: "Agent step failed",
      summary: truncateAgentTimelineText(data.error || data.detail || data.message || "Step failed"),
      icon: "x-circle",
    });
    return true;
  }
  timeline.completed = true;
  timeline.status = "completed";
  timeline.completedAt = Date.now();
  upsertAgentTimelineItem(timeline, stepId, {
    kind: "step",
    status: "completed",
    title: "Agent step completed",
    icon: "flag",
    createdAt: event.created_at,
  }, {
    status: "completed",
    title: "Agent step completed",
    summary: truncateAgentTimelineText(data.reason || data.message || data.completion_state || "Completed"),
    icon: "flag",
  });
  return true;
}

function reduceAgentTimelineTextEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const partId = getAgentTimelinePartId(event) || "current";
  const textItemId = `text:${partId}`;
  const item = upsertAgentTimelineItem(timeline, textItemId, {
    kind: "text",
    status: "running",
    title: "Writing response",
    icon: "message-square",
    createdAt: event.created_at,
  }, {
    kind: "text",
    status: type.endsWith(".ended") || type === "message.completed" ? "completed" : "running",
    title: "Writing response",
    icon: type.endsWith(".ended") || type === "message.completed" ? "message-square-check" : "message-square",
  });
  timeline.textById[partId] = textItemId;
  if (type === "session.next.text.delta" || type === "message.delta" || type === "assistant_delta") {
    const delta = data.delta ?? data.response_delta ?? data.text_delta ?? data.content_delta ?? data.content ?? data.text ?? data.message ?? "";
    appendAgentTimelineText(timeline, delta);
    item.summary = truncateAgentTimelineText(timeline.assistantText || "Streaming response");
    return true;
  }
  if (type === "session.next.text.ended" || type === "message.completed") {
    const finalText = data.text ?? data.content ?? data.response ?? data.message ?? data.assistantText ?? "";
    if (finalText) appendAgentTimelineText(timeline, finalText, { replace: true });
    item.status = "completed";
    item.summary = truncateAgentTimelineText(timeline.assistantText || "Response text completed");
    return true;
  }
  item.summary = truncateAgentTimelineText(data.message || "Response text started");
  return true;
}

function reduceAgentTimelineReasoningEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const partId = getAgentTimelinePartId(event) || getAgentTimelineEventKey(event, timeline);
  const itemId = `reasoning:${partId}`;
  const status = type.endsWith(".ended") ? "completed" : "running";
  const item = upsertAgentTimelineItem(timeline, itemId, {
    kind: "reasoning",
    status,
    title: "Reasoning summary",
    icon: "brain",
    weak: true,
    createdAt: event.created_at,
  }, {
    kind: "reasoning",
    status,
    title: data.title || "Reasoning summary",
    icon: status === "completed" ? "check-circle-2" : "brain",
    weak: true,
  });
  timeline.reasoningById[partId] = itemId;
  const reasoningText = agentTimelineReasoningText(event);
  if (reasoningText) {
    item.detail = type.endsWith(".delta")
      ? truncateAgentTimelineText(`${item.detail ? `${item.detail} ` : ""}${reasoningText}`, 520)
      : truncateAgentTimelineText(reasoningText, 520);
  }
  item.summary = item.detail || truncateAgentTimelineText(data.status || "Reasoning update");
  return true;
}

function reduceAgentTimelineToolEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const toolName = agentTimelineToolName(event);
  const callId = getAgentTimelineCallId(event) || getAgentTimelinePartId(event) || toolName || getAgentTimelineEventKey(event, timeline);
  const itemId = timeline.toolsByCallId[callId] || `tool:${callId}`;
  timeline.toolsByCallId[callId] = itemId;
  const status = (
    type === "session.next.tool.success" || type === "tool.completed" || (type === "tool_result" && data.success !== false)
    || (type === "runtime.tool" && String(data.status || "").toLowerCase() === "completed")
  )
    ? "completed"
    : (type === "session.next.tool.failed" || type === "tool.failed" || type === "tool.error" || (type === "tool_result" && data.success === false) || (type === "runtime.tool" && (data.error || String(data.status || "").toLowerCase() === "failed")))
      ? "failed"
      : "running";
  const item = upsertAgentTimelineItem(timeline, itemId, {
    kind: "tool",
    status,
    title: toolName,
    icon: "wrench",
    createdAt: event.created_at,
  }, {
    kind: "tool",
    status,
    title: toolName,
    icon: status === "completed" ? "check-circle-2" : (status === "failed" ? "x-circle" : "wrench"),
  });
  if (type === "session.next.tool.input.started") {
    item.summary = truncateAgentTimelineText(data.message || "Preparing tool input");
    item.input = truncateAgentTimelineText(agentTimelineInputText(event) || item.input || "", 520);
  } else if (type === "session.next.tool.input.delta") {
    const delta = agentTimelineInputText(event);
    item.input = truncateAgentTimelineText(`${item.input || ""}${delta}`, 520);
    item.summary = item.input || "Preparing tool input";
  } else if (type === "session.next.tool.input.ended") {
    const inputText = agentTimelineInputText(event);
    if (inputText) item.input = truncateAgentTimelineText(inputText, 520);
    item.summary = item.input || "Tool input ready";
  } else if (type === "session.next.tool.called" || type === "tool.started" || type === "tool_call") {
    const inputText = agentTimelineInputText(event);
    if (inputText) item.input = truncateAgentTimelineText(inputText, 520);
    item.summary = truncateAgentTimelineText(data.message || item.input || `Running ${toolName}`);
  } else if (type === "session.next.tool.progress") {
    const progress = normalizeAgentTimelineJsonText(data.progress ?? data.message ?? data.delta ?? data.status ?? "");
    item.progress = truncateAgentTimelineText(`${item.progress ? `${item.progress} ` : ""}${progress}`, 520);
    item.summary = item.progress || `Running ${toolName}`;
  } else if (status === "completed") {
    const outputText = agentTimelineOutputText(event);
    if (outputText) item.output = truncateAgentTimelineText(outputText, 520);
    item.summary = truncateAgentTimelineText(data.message || item.output || `${toolName} completed`);
  } else if (status === "failed") {
    const errorText = agentTimelineOutputText(event);
    if (errorText) item.error = truncateAgentTimelineText(errorText, 520);
    item.summary = truncateAgentTimelineText(data.error || data.detail || data.message || item.error || `${toolName} failed`);
  }
  return true;
}

function reduceAgentTimelineCompactionEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const partId = getAgentTimelinePartId(event) || "current";
  const itemId = `compaction:${partId}`;
  const status = type.endsWith(".ended") || type === "context_compaction_applied" ? "completed" : "running";
  const item = upsertAgentTimelineItem(timeline, itemId, {
    kind: "compaction",
    status,
    title: "Context compaction",
    icon: "archive",
    createdAt: event.created_at,
  }, {
    kind: "compaction",
    status,
    title: data.title || "Context compaction",
    icon: status === "completed" ? "archive" : "scissors",
  });
  const delta = normalizeAgentTimelineJsonText(data.summary ?? data.delta ?? data.message ?? data.stage ?? "");
  if (delta) item.summary = truncateAgentTimelineText(type.endsWith(".delta") ? `${item.summary ? `${item.summary} ` : ""}${delta}` : delta, 520);
  else item.summary = status === "completed" ? "Compaction completed" : "Compacting context";
  return true;
}

function reduceAgentTimelinePermissionEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const permissionId = String(getAgentTimelineField(event, [
    "permission_id", "permissionId", "id", "callID", "callId", "tool_call_id",
  ]) || getAgentTimelineEventKey(event, timeline));
  const itemId = `permission:${permissionId}`;
  const resolved = type === "permission.resolved" || type === "permission_resolved" || type === "permission.allowed" || type === "permission.denied";
  const denied = type === "permission.denied" || String(data.status || data.decision || data.result || "").toLowerCase() === "denied";
  upsertAgentTimelineItem(timeline, itemId, {
    kind: "permission",
    status: resolved ? (denied ? "failed" : "completed") : "pending",
    title: "Permission requested",
    icon: "shield",
    createdAt: event.created_at,
  }, {
    kind: "permission",
    status: resolved ? (denied ? "failed" : "completed") : "pending",
    title: resolved ? "Permission resolved" : "Permission requested",
    summary: truncateAgentTimelineText(data.message || data.reason || data.prompt || data.tool || "Permission required"),
    icon: resolved ? (denied ? "shield-alert" : "shield-check") : "shield",
  });
  return true;
}

function reduceAgentTimelineGenericEvent(timeline, event, type) {
  const data = getAgentTimelineEventData(event);
  const itemId = `${type}:${getAgentTimelineEventKey(event, timeline)}`;
  let kind = "event";
  let title = type.replaceAll("_", " ");
  let icon = "activity";
  let status = "running";
  if (type.startsWith("provider.") || type === "model.retry") {
    kind = "provider";
    title = type === "provider.retry" || type === "model.retry" ? "Provider retry" : "Provider status";
    icon = type.includes("retry") ? "refresh-cw" : "activity";
  } else if (type === "usage.updated") {
    kind = "usage";
    title = "Usage updated";
    icon = "gauge";
    status = "completed";
  } else if (type === "question.requested") {
    kind = "permission";
    title = "Question requested";
    icon = "circle-help";
    status = "pending";
  }
  upsertAgentTimelineItem(timeline, itemId, {
    kind,
    status,
    title,
    icon,
    createdAt: event.created_at,
  }, {
    kind,
    status,
    title,
    summary: truncateAgentTimelineText(data.message || data.summary || data.status || data.reason || ""),
    icon,
  });
  return true;
}

function reduceAgentTimelineEvent(chatState, event) {
  if (!chatState || !event || typeof event !== "object") return { changed: false, reason: "invalid" };
  const type = normalizeRuntimeEventTypeAlias(event.type || event.event_type || event?.data?.type || "");
  if (!type) return { changed: false, reason: "missing_type" };
  const timeline = ensureAgentTimelineState(chatState, event);
  if (!timeline) return { changed: false, reason: "missing_timeline" };
  const eventKey = getAgentTimelineEventKey(event, timeline);
  if (eventKey && timeline.eventsById[eventKey]) return { changed: false, duplicate: true, timeline };
  if (eventKey) timeline.eventsById[eventKey] = true;
  timeline.updatedAt = Date.now();
  let changed = false;

  if (
    type === "session.next.prompted"
    || type.startsWith("session.next.step.")
    || ["execution.started", "execution.completed", "execution.failed", "chat.started", "chat.completed", "chat.failed", "complete"].includes(type)
  ) {
    const stepType = type === "complete" ? "execution.completed" : type;
    changed = reduceAgentTimelineStepEvent(timeline, event, stepType);
  } else if (type.startsWith("session.next.text.") || ["message.delta", "assistant_delta", "message.completed"].includes(type)) {
    changed = reduceAgentTimelineTextEvent(timeline, event, type);
  } else if (type.startsWith("session.next.reasoning.") || type === "llm_thinking" || type === "runtime.reasoning") {
    changed = reduceAgentTimelineReasoningEvent(timeline, event, type);
  } else if (type.startsWith("session.next.tool.") || ["tool.started", "tool.completed", "tool.failed", "tool.error", "tool_call", "tool_result", "runtime.tool"].includes(type)) {
    changed = reduceAgentTimelineToolEvent(timeline, event, type);
  } else if (type.startsWith("session.next.compaction.") || ["context_compaction_planned", "context_compaction_applied", "skill_compaction"].includes(type)) {
    changed = reduceAgentTimelineCompactionEvent(timeline, event, type);
  } else if (["permission.requested", "permission.resolved", "permission_request", "permission_resolved", "permission.denied", "permission.allowed"].includes(type)) {
    changed = reduceAgentTimelinePermissionEvent(timeline, event, type);
  } else if (type.startsWith("provider.") || type === "model.retry" || type === "usage.updated" || type === "question.requested") {
    changed = reduceAgentTimelineGenericEvent(timeline, event, type);
  }

  if (timeline.items.length > 80) timeline.items = timeline.items.slice(-80);
  const pacedTotal = getAgentTimelineRenderableItems(timeline).filter(isAgentTimelinePacedItem).length;
  if (Number(timeline.visibleItemCount || 0) > pacedTotal) timeline.visibleItemCount = pacedTotal;
  return { changed, timeline };
}

function agentTimelineMatchesRequest(timeline, requestCtx = {}) {
  if (!timeline) return false;
  const timelineId = String(timeline.requestId || "");
  const ids = [
    requestCtx.clientRequestId,
    requestCtx.requestId,
  ].map((value) => String(value || "")).filter(Boolean);
  if (!timelineId || !ids.length) return true;
  return ids.includes(timelineId);
}

function renderAgentTimelineItemDetail(item) {
  const details = [
    item.input ? ["Input", item.input] : null,
    item.progress ? ["Progress", item.progress] : null,
    item.output ? ["Output", item.output] : null,
    item.error ? ["Error", item.error] : null,
    item.detail && item.detail !== item.summary ? ["Detail", item.detail] : null,
  ].filter(Boolean);
  if (!details.length) return "";
  return details.map(([label, value]) => `<div class="agent-timeline-detail"><span>${safe(label)}</span><code>${safe(truncateAgentTimelineText(value, 260))}</code></div>`).join("");
}

function renderAgentTimelineRowsHtml(timeline) {
  if (!timeline) return "";
  const visibleItems = getAgentTimelineVisibleItems(timeline);
  if (!visibleItems.length) return "";
  return visibleItems.map((item) => {
    const status = String(item.status || "running").toLowerCase();
    const kind = String(item.kind || "event").toLowerCase();
    const icon = item.icon || (kind === "tool" ? "wrench" : "activity");
    const title = item.title || "Runtime event";
    const summary = item.summary || item.detail || "";
    const detail = renderAgentTimelineItemDetail(item);
    return `<div class="agent-timeline-row is-${safe(kind)} is-${safe(status)}${item.weak ? " is-weak" : ""}"><span class="agent-timeline-icon"><i data-lucide="${safe(icon)}"></i></span><div class="agent-timeline-row-main"><div class="agent-timeline-row-head"><span class="agent-timeline-title">${safe(title)}</span><span class="agent-timeline-chip is-${safe(status)}">${safe(status)}</span></div>${summary ? `<div class="agent-timeline-summary">${safe(truncateAgentTimelineText(summary, 280))}</div>` : ""}${detail}</div></div>`;
  }).join("");
}

function findPendingAssistantArticleForTimeline(requestCtx = {}, timeline = null) {
  if (!dom.messageList) return null;
  const ids = [
    requestCtx.clientRequestId,
    requestCtx.requestId,
    timeline?.requestId,
  ].map((value) => String(value || "")).filter(Boolean);
  for (const id of ids) {
    const escaped = CSS.escape(id);
    const article = dom.messageList.querySelector(
      `article[data-pending-assistant="1"][data-client-request-id="${escaped}"], article[data-pending-assistant="1"][data-request-id="${escaped}"]`
    );
    if (article) return article;
  }
  return dom.messageList.querySelector('article[data-pending-assistant="1"]');
}

function renderAgentTimelineForCurrentRequest(agentId, chatState, options = {}) {
  if (state.selectedAgentId !== agentId || !dom.messageList || !chatState?.inflightAgentTimeline) return;
  const timeline = chatState.inflightAgentTimeline;
  const requestCtx = options.requestCtx || chatState.currentRequest || {};
  if (!agentTimelineMatchesRequest(timeline, requestCtx)) return;
  const article = findPendingAssistantArticleForTimeline(requestCtx, timeline);
  if (!article) return;
  if (timeline.assistantText && options.updateAssistantText !== false) {
    updatePendingAssistantStreamContent(agentId, timeline.assistantText, {
      streaming: !timeline.completed,
      requestCtx,
    });
  } else if ((timeline.items || []).length) {
    article.querySelector(".assistant-waiting-indicator")?.remove();
  }
  const timelineEl = article.querySelector("[data-agent-timeline='1']") || (() => {
    const created = document.createElement("div");
    created.className = "agent-timeline";
    created.dataset.agentTimeline = "1";
    created.innerHTML = `<div class="agent-timeline-head"><span class="agent-timeline-status"><span class="portal-running-spinner" aria-hidden="true"></span><span data-agent-timeline-status-text>Working</span></span><span class="agent-timeline-meta" data-agent-timeline-meta></span></div><div class="agent-timeline-list" data-agent-timeline-items></div>`;
    article.appendChild(created);
    return created;
  })();
  const statusText = timeline.status === "failed" ? "Failed" : (timeline.completed ? "Completed" : "Working");
  const statusNode = timelineEl.querySelector("[data-agent-timeline-status-text]");
  if (statusNode) statusNode.textContent = statusText;
  timelineEl.classList.toggle("is-completed", Boolean(timeline.completed));
  timelineEl.classList.toggle("is-failed", timeline.status === "failed");
  const spinner = timelineEl.querySelector(".portal-running-spinner");
  if (spinner) spinner.style.display = timeline.completed ? "none" : "";
  const meta = [timeline.agent, timeline.model].map((value) => String(value || "").trim()).filter(Boolean).join(" / ");
  const metaNode = timelineEl.querySelector("[data-agent-timeline-meta]");
  if (metaNode) metaNode.textContent = meta;
  const listNode = timelineEl.querySelector("[data-agent-timeline-items]");
  if (listNode) {
    const rows = renderAgentTimelineRowsHtml(timeline);
    listNode.innerHTML = rows || `<div class="agent-timeline-empty">Waiting for runtime events...</div>`;
  }
  renderIcons();
  scrollToBottom();
}

function clearAssistantTimelineFromArticle(article) {
  if (!article) return;
  article.querySelector("[data-agent-timeline='1']")?.remove();
  article.querySelector(".assistant-waiting-indicator")?.remove();
}

function finalizeAgentTimelineState(chatState, requestCtx = {}, finalPayload = {}) {
  if (!chatState?.inflightAgentTimeline) return;
  const timeline = chatState.inflightAgentTimeline;
  if (!agentTimelineMatchesRequest(timeline, requestCtx)) return;
  const finalText = String(finalPayload?.response || "");
  if (finalText) timeline.assistantText = finalText;
  timeline.completed = true;
  timeline.status = String(finalPayload?.completion_state || "").toLowerCase() === "failed" ? "failed" : "completed";
  timeline.completedAt = Date.now();
  clearAgentTimelineRevealTimer(timeline);
  advanceAgentTimelineReveal(timeline, Infinity);
  chatState.lastAgentTimelineSnapshot = {
    ...timeline,
    eventsById: { ...(timeline.eventsById || {}) },
    toolsByCallId: { ...(timeline.toolsByCallId || {}) },
    reasoningById: { ...(timeline.reasoningById || {}) },
    textById: { ...(timeline.textById || {}) },
    items: (timeline.items || []).map((item) => ({ ...item })),
  };
  if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
  else chatState.inflightAgentTimeline = null;
}

function syncAgentTimelineAssistantTextFromStream(chatState, requestCtx = {}, text = "") {
  if (!chatState) return null;
  const timeline = ensureAgentTimelineState(chatState, {
    request_id: requestCtx.requestId || requestCtx.clientRequestId || "",
    session_id: requestCtx.sessionIdAtSend || chatState.sessionId || "",
    data: {},
  });
  if (!timeline) return null;
  timeline.assistantText = String(text || "");
  timeline.updatedAt = Date.now();
  const item = upsertAgentTimelineItem(timeline, "text:current", {
    kind: "text",
    status: "running",
    title: "Writing response",
    icon: "message-square",
  }, {
    kind: "text",
    status: "running",
    title: "Writing response",
    summary: truncateAgentTimelineText(timeline.assistantText || "Streaming response"),
    icon: "message-square",
  });
  timeline.textById.current = item?.id || "text:current";
  return timeline;
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}


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

function markStreamTerminalFromEvent(chatState, entry = {}) {
  if (!chatState) return false;

  if (chatState.inflightEventStream) {
    chatState.inflightEventStream.completed = true;
    chatState.inflightEventStream.status = chatState.inflightEventStream.status || "completed";
    chatState.inflightEventStream.completedAt = chatState.inflightEventStream.completedAt || Date.now();
    chatState.lastEventStreamSnapshot = {
      ...chatState.inflightEventStream,
      events: mergeRuntimeStreamEvents(chatState.inflightEventStream.events || [], [entry]),
      completed: true,
      completedAt: chatState.inflightEventStream.completedAt,
    };
    return true;
  }

  if (chatState.lastEventStreamSnapshot) {
    chatState.lastEventStreamSnapshot = {
      ...chatState.lastEventStreamSnapshot,
      events: mergeRuntimeStreamEvents(chatState.lastEventStreamSnapshot.events || [], [entry]),
      completed: true,
      completedAt: chatState.lastEventStreamSnapshot.completedAt || Date.now(),
    };
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
    chatState.inflightEventStream?.requestId,
    chatState.inflightEventStream?.id,
    chatState.inflightAgentTimeline?.requestId,
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
    (type === "chat.started" || type === "assistant.message.started" || type === "session.next.prompted" || type === "session.next.step.started" || type === "session.next.text.started")
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
    && chatState.lastEventStreamSnapshot?.requestId
    && entry.request_id === chatState.lastEventStreamSnapshot.requestId
  );
  if (
    entry.session_id
    && !currentSessionId
    && !(eventMatchesCurrentRequest || eventMatchesSocketRequest || eventMatchesLastCompletedRequest || canAdoptRequestId)
  ) {
    // Drop unmatched stale events when no current session is bound.
    // Otherwise they can recreate inflightEventStream and cause false busy/session pollution.
    return;
  }

  // Handle additive runtime state fields while keeping existing event semantics.
  const isCompletion = isCompletionRuntimeState(entry.state);
  const lifecycleType = entry.lifecycle_type;
  if (!isTrackableStreamEvent(type) && !lifecycleType && !isCompletion) return;

  const isLateEventForCompletedRequest = Boolean(
    !chatState.currentRequest
    && eventMatchesLastCompletedRequest
    && chatState.lastEventStreamSnapshot
  );

  if (isLateEventForCompletedRequest) {
    chatState.lastEventStreamSnapshot = {
      ...chatState.lastEventStreamSnapshot,
      events: mergeRuntimeStreamEvents(chatState.lastEventStreamSnapshot.events || [], [entry]),
      completed: true,
    };
    return;
  }

  if (!chatState.inflightEventStream) {
    chatState.inflightEventStream = {
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
  if (entry.request_id && (!chatState.inflightEventStream.requestId || type === "chat.started")) {
    chatState.inflightEventStream.requestId = entry.request_id;
    chatState.inflightEventStream.id = chatState.inflightEventStream.id || entry.request_id;
  }
  if (entry.session_id && !chatState.inflightEventStream.sessionId) {
    chatState.inflightEventStream.sessionId = entry.session_id;
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

  if (!chatState.inflightEventStream) return;
  chatState.inflightEventStream.lastEventAt = Date.now();
  chatState.inflightEventStream.lastEventTs = entry.ts || null;
  chatState.inflightEventStream.lastEventCreatedAt = entry.created_at || "";
  chatState.inflightEventStream.status = "connected";
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
  const alreadySeen = (chatState.inflightEventStream.events || []).some((event) => {
    const key = runtimeEventDedupKey(event);
    return key === entryDedupKey;
  });
  if (alreadySeen) {
    return;
  }

  if (!chatState.inflightEventStream.started && type !== "execution.started") {
    chatState.inflightEventStream.events.push({
      type: "execution.started",
      raw_type: "execution.started",
      lifecycle_type: "execution.started",
      data: { message: "Execution started" },
      ts: entry.ts,
      state: "started",
    });
    chatState.inflightEventStream.started = true;
  }

  chatState.inflightEventStream.events.push(entry);
  if (type === "execution.started") chatState.inflightEventStream.started = true;

  if (lifecycleType && lifecycleType !== type) {
    const terminalDetail = lifecycleType === "execution.failed"
      ? (entry?.data?.error || entry?.data?.message || "Execution failed")
      : (entry?.data?.message || "Execution complete");
    chatState.inflightEventStream.events.push({
      type: lifecycleType,
      raw_type: lifecycleType,
      lifecycle_type: lifecycleType,
      data: { ...entry.data, message: terminalDetail },
      ts: entry.ts,
      state: entry.state,
    });
  }

  const timelineResult = (typeof reduceAgentTimelineEvent === "function")
    ? reduceAgentTimelineEvent(chatState, entry)
    : { changed: false };
  if (timelineResult.changed) {
    const activeTimeline = chatState.inflightAgentTimeline;
    if (activeTimeline && Number(activeTimeline.visibleItemCount || 0) < 1) {
      advanceAgentTimelineReveal(activeTimeline, 1);
    }
    if (
      chatState.currentRequest
      && ["session.next.text.delta", "session.next.text.ended", "assistant_delta"].includes(type)
      && chatState.inflightAgentTimeline?.assistantText
    ) {
      chatState.currentRequest.streamedText = chatState.inflightAgentTimeline.assistantText;
    }
    if (typeof renderAgentTimelineForCurrentRequest === "function") {
      renderAgentTimelineForCurrentRequest(currentAgentId, chatState, {
        requestCtx: chatState.currentRequest || {},
      });
    }
    if (typeof scheduleAgentTimelineReveal === "function") {
      scheduleAgentTimelineReveal(currentAgentId, chatState, chatState.currentRequest || {});
    }
  }

  if (entry.request_id && canAdoptRequestId && state.eventWsRequestId !== entry.request_id) {
    ensureEventSocketForAgent(currentAgentId, entry.session_id || currentSessionId, entry.request_id);
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
    && !chatState.inflightEventStream
  ) {
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
    || type === "session.next.step.ended"
    || type === "session.next.step.failed"
    || type === "skill_complete"
    || isCompletion
    || lifecycleType === "execution.completed"
    || lifecycleType === "execution.failed"
  );

  if (isTerminalRuntimeEvent) {
    markStreamTerminalFromEvent(chatState, entry);
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
      chatState.inflightAgentTimeline?.requestId,
    ].map((value) => String(value || "")).includes(String(requestId));
  }
  return Boolean(
    (chatState.inflightEventStream && chatState.inflightEventStream.completed === false)
    || (chatState.inflightAgentTimeline && chatState.inflightAgentTimeline.completed === false)
  );
}

function updateEventSocketStatus(agentId, status) {
  const chatState = ensureChatState(agentId);
  const snapshot = chatState?.inflightEventStream || chatState?.lastEventStreamSnapshot;
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
  const lastEventAt = chatState?.inflightEventStream?.lastEventCreatedAt
    || chatState?.lastEventStreamSnapshot?.lastEventCreatedAt
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

function computeRuntimeUiState(agent = {}, chatState = {}) {
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

function runtimeUiStatusText(uiState = {}) {
  const runtimeHealth = uiState.normalizedRuntimeHealth || normalizeRuntimeHealthStatus(uiState.runtimeHealth);
  const sessionStatus = String(uiState.sessionStatus || "unknown").toLowerCase();
  if (runtimeHealth === "offline") return "Runtime offline. Session status unknown.";
  if (sessionStatus === "idle") return "Assistant online. Ready.";
  if (sessionStatus === "busy") return "Assistant online. Response in progress.";
  return "Assistant online. Session status unknown.";
}

function renderRuntimeStateNotes(uiState = {}) {
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
  const uiState = computeRuntimeUiState(agent || {}, chatState || {});
  const runtimeSummary = runtimeUiStatusText(uiState);
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
      showToast("Copy failed", { variant: 'error' });
    }
  } catch (error) {
    showToast("Copy failed", { variant: 'error' });
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

function agentScope(agent) {
  if (agent?.visibility === "public") return "public";
  return Number(agent?.owner_user_id) === state.currentUserId ? "mine" : "shared";
}

function agentRuntimeStatus(agent) {
  return String(state.agentStatus.get(agent?.id)?.status || agent?.status || "stopped").trim().toLowerCase() || "stopped";
}

function compactText(value, maxLength = 160) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 3)).trim()}...`;
}

function agentHealth(agent) {
  const status = agentRuntimeStatus(agent);
  const lastError = String(agent?.last_error || state.agentStatus.get(agent?.id)?.last_error || "").trim();
  const writable = canWriteAgent(agent);
  if (lastError || ["failed", "error"].includes(status)) {
    return {
      key: "attention",
      tone: "error",
      label: "Needs attention",
      detail: lastError ? compactText(lastError, 120) : "The runtime reported an error.",
      action: writable ? (status === "running" ? "Restart" : "Start") : "",
    };
  }
  if (!agent?.runtime_profile_id) {
    return {
      key: "attention",
      tone: "warning",
      label: "Needs setup",
      detail: "Runtime profile is missing.",
      action: writable ? "Edit setup" : "",
    };
  }
  if (["creating", "starting", "restarting"].includes(status)) {
    return {
      key: "starting",
      tone: "warning",
      label: "Starting",
      detail: "Runtime is preparing. Chat will be available soon.",
      action: "",
    };
  }
  if (status === "running") {
    return {
      key: "ready",
      tone: "success",
      label: "Ready",
      detail: "Ready to chat.",
      action: "",
    };
  }
  return {
    key: "stopped",
    tone: "neutral",
    label: "Stopped",
    detail: writable ? "Start this assistant when you need it." : "This assistant is not running.",
    action: writable ? "Start" : "",
  };
}

function agentScopeLabel(agent) {
  const scope = agentScope(agent);
  if (scope === "mine") return "Mine";
  if (scope === "public") return "Public";
  return `Shared by User ${agent?.owner_user_id ?? "-"}`;
}

function agentMatchesFilters(agent) {
  const filters = state.agentFilters || {};
  const health = agentHealth(agent);

  const query = String(filters.query || "").trim().toLowerCase();
  if (!query) return true;
  const haystack = [
    agent?.name,
    agent?.description,
    agent?.status,
    agent?.runtime_type,
    agent?.visibility,
    agent?.last_error,
    agentScopeLabel(agent),
    health.label,
    health.detail,
  ].join(" ").toLowerCase();
  return haystack.includes(query);
}

function visibleAgents() {
  return (state.mineAgents || []).filter(agentMatchesFilters);
}

function hasActiveAgentFilters() {
  const filters = state.agentFilters || {};
  return Boolean(String(filters.query || "").trim());
}

function syncAgentFilterControls(visibleCount = null) {
  const filters = state.agentFilters || { query: "" };
  if (dom.agentSearchInput && dom.agentSearchInput.value !== filters.query) dom.agentSearchInput.value = filters.query;
  if (dom.agentFilterSummary) {
    const total = (state.mineAgents || []).length;
    const shown = visibleCount === null ? visibleAgents().length : visibleCount;
    const parts = [];
    if (filters.query) parts.push(`"${filters.query}"`);
    dom.agentFilterSummary.textContent = parts.length ? `${shown} of ${total} shown - ${parts.join(", ")}` : `${total} assistants`;
  }
}

// ===== selected agent state sync =====
function renderAgentList() {
  if (!dom.mineList) return;

  dom.mineList.innerHTML = "";
  syncAgentFilterControls();
  if (!state.mineAgents.length) {
    dom.mineList.innerHTML = '<div class="portal-empty-note">No assistants</div>';
    return;
  }
  const filteredAgents = visibleAgents();
  syncAgentFilterControls(filteredAgents.length);
  if (!filteredAgents.length) {
    dom.mineList.innerHTML = `<div class="portal-empty-note">${hasActiveAgentFilters() ? "No assistants match these filters." : "No assistants"}</div>`;
    return;
  }

  const mine = filteredAgents.filter((agent) => agentScope(agent) === "mine");
  const shared = filteredAgents.filter((agent) => agentScope(agent) === "shared");
  const publicAgents = filteredAgents.filter((agent) => agentScope(agent) === "public");

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
      const status = agentRuntimeStatus(agent);
      const health = agentHealth(agent);
      const chatState = ensureChatState(agent.id);
      const isActive = state.selectedAgentId === agent.id;
      const row = document.createElement("button");
      row.type = "button";
      row.dataset.agentId = agent.id;
      row.className = `portal-agent-row is-${safe(health.tone)}${isActive ? " is-active" : ""}`;
      if (isActive) row.setAttribute("aria-current", "true");
      row.title = `${agent.name || "Assistant"}\nStatus: ${status}\n${health.detail}`;
      row.setAttribute("aria-label", `${agent.name || "Assistant"}. Status ${status}. ${health.detail}`);
      const sharedBadge = agentScope(agent) === "mine" ? "" : `<span class="portal-agent-shared">${safe(agentScope(agent))}</span>`;
      const unreadBadge = chatState?.unreadCount ? `<span class="portal-agent-unread">${chatState.unreadCount}</span>` : "";
      let runtimeBadge = "";
      if (hasActiveChatRequestForAgent(agent.id)) runtimeBadge = '<span class="portal-agent-chat-badge is-running">running</span>';
      const runtimeType = String(agent.runtime_type || "native").trim().toLowerCase() || "native";
      const runtimeTypeBadge = `<span class="portal-agent-chat-badge">${safe(runtimeType)}</span>`;
      const rowBadges = `${runtimeTypeBadge}${runtimeBadge}${unreadBadge}${sharedBadge}`;
      const statusLabel = `Status: ${status}`;
      row.innerHTML = `
        <div class="portal-agent-row-head">
          <span class="portal-agent-status-dot status-${safe(status)}" title="${escapeHtmlAttr(statusLabel)}" aria-hidden="true"></span>
          <span class="portal-agent-name">${safe(agent.name)}</span>
        </div>
        ${rowBadges ? `<div class="portal-agent-row-badges">${rowBadges}</div>` : ""}
      `;
      row.addEventListener("click", () => selectAgentById(agent.id));
      section.append(row);
    });

    dom.mineList.append(section);
  };

  renderSection("My Space", mine, { showTitle: false });
  renderSection("Shared", shared);
  renderSection("Public", publicAgents);
  renderIcons();
}

function agentRowForId(agentId) {
  if (!dom.mineList || !agentId) return null;
  return dom.mineList.querySelector(`.portal-agent-row[data-agent-id="${cssEscapeForSelector(String(agentId))}"]`);
}

function syncAgentRowUnreadBadge(agentId) {
  const row = agentRowForId(agentId);
  if (!row) return false;

  const chatState = ensureChatState(agentId);
  const count = Number(chatState?.unreadCount || 0);
  const existingBadge = row.querySelector(".portal-agent-unread");
  if (count <= 0) {
    existingBadge?.remove();
    return true;
  }

  if (existingBadge) {
    existingBadge.textContent = String(count);
    return true;
  }

  const badges = row.querySelector(".portal-agent-row-badges");
  if (!badges) return false;
  const unreadBadge = document.createElement("span");
  unreadBadge.className = "portal-agent-unread";
  unreadBadge.textContent = String(count);
  badges.append(unreadBadge);
  return true;
}

function syncAgentListSelection(previousAgentId = null, selectedAgentId = state.selectedAgentId) {
  if (!dom.mineList) return false;
  const rows = dom.mineList.querySelectorAll(".portal-agent-row[data-agent-id]");
  if (!rows.length) return false;

  rows.forEach((row) => {
    const active = row.dataset.agentId === selectedAgentId;
    row.classList.toggle("is-active", active);
    if (active) row.setAttribute("aria-current", "true");
    else row.removeAttribute("aria-current");
  });

  if (previousAgentId) syncAgentRowUnreadBadge(previousAgentId);
  if (selectedAgentId) syncAgentRowUnreadBadge(selectedAgentId);
  return true;
}

function agentHealthActionLabel(health) {
  return String(health?.action || "").trim();
}

function agentHealthCardHtml(agent) {
  const health = agentHealth(agent);
  const scopeLabel = agentScopeLabel(agent);
  const actionLabel = agentHealthActionLabel(health);
  const actionButton = actionLabel ? `
    <button class="portal-btn is-secondary" type="button" data-agent-health-action="${escapeHtmlAttr(actionLabel)}">
      <i data-lucide="${actionLabel === "Edit setup" ? "settings" : (actionLabel === "Restart" ? "rotate-cw" : "play")}" class="w-4 h-4"></i>${safe(actionLabel)}
    </button>
  ` : "";
  return `
    <section class="portal-agent-health-card is-${safe(health.tone)}">
      <div class="portal-agent-health-main">
        <span class="portal-status-badge is-${safe(health.tone)}">${safe(health.label)}</span>
        <div>
          <strong>${safe(health.detail)}</strong>
          <span>${safe(scopeLabel)} · ${safe(agentRuntimeStatus(agent))}</span>
        </div>
      </div>
      ${actionButton}
    </section>
  `;
}

async function handleAgentHealthAction(agent, actionLabel) {
  const label = String(actionLabel || "").trim();
  if (!agent || !label) return;
  if (label === "Edit setup") {
    openEditDialog(agent);
    return;
  }
  if (label === "Restart") {
    await action(`/api/agents/${agent.id}/restart`);
    return;
  }
  if (label === "Start") {
    await action(`/api/agents/${agent.id}/start`);
  }
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

  const effectiveAgentSettingsRepoUrl = agent.effective_agent_settings_repo_url || agent.agent_settings_repo_url || state.agentDefaults?.default_agent_settings_repo_url || "";
  const effectiveAgentSettingsBranch = agent.effective_agent_settings_branch || agent.agent_settings_branch || state.agentDefaults?.default_agent_settings_branch || "";
  const effectiveAgentSettingsSubdir = agent.effective_agent_settings_subdir || agent.agent_settings_subdir || state.agentDefaults?.default_agent_settings_repo_subdir || "";
  const isDefaultAgentSettingsRepo = !agent.agent_settings_repo_url && !!effectiveAgentSettingsRepoUrl;
  const effectiveSkillRepoUrl = agent.effective_skill_repo_url || agent.skill_repo_url || state.agentDefaults?.default_skill_repo_url || "";
  const effectiveSkillBranch = agent.effective_skill_branch || agent.skill_branch || state.agentDefaults?.default_skill_branch || "";
  const isDefaultSkillRepo = !agent.skill_repo_url && !!effectiveSkillRepoUrl;
  const runtimeType = String(agent.runtime_type || "native").trim().toLowerCase() || "native";

  // Build Skills Repository section if present.
  // Tool repo/branch configuration was intentionally removed from Portal agent flows in #318;
  // do not reintroduce tool repo/branch UI or provisioning here.
  let repoSection = "";
  let agentSettingsSection = "";
  if (effectiveAgentSettingsRepoUrl) {
    const branchLine = effectiveAgentSettingsBranch
      ? `
        <div class="portal-detail-subtle">Branch: ${safe(effectiveAgentSettingsBranch)}</div>
      `
      : "";
    const subdirLine = effectiveAgentSettingsSubdir
      ? `
        <div class="portal-detail-subtle">Subdirectory: ${safe(effectiveAgentSettingsSubdir)}</div>
      `
      : "";
    const defaultIndicator = isDefaultAgentSettingsRepo
      ? `
        <div class="portal-detail-subtle">Using configured default</div>
      `
      : "";
    agentSettingsSection = `
      <div class="portal-detail-row">
        <div class="portal-detail-label">Instructions Repository</div>
        <div class="portal-detail-value"><code>${safe(effectiveAgentSettingsRepoUrl)}</code></div>
        ${branchLine}
        ${subdirLine}
        ${defaultIndicator}
      </div>
    `;
  }
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
      ${agentHealthCardHtml(agent)}
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
      ${agentSettingsSection}
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
    </div>
  `;

  dom.agentMeta.querySelector("[data-agent-health-action]")?.addEventListener("click", async (event) => {
    try {
      await handleAgentHealthAction(agent, event.currentTarget.dataset.agentHealthAction);
    } catch (error) {
      showToast(`Action failed: ${error.message}`, { variant: 'error' });
    }
  });

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
  syncAgentListSelection(previousAgentId, agentId);
  await syncSelectedAgentState();
  if (updateRoute && !isApplyingPortalRoute) {
    commitPortalRoute({ section: "assistants", agentId });
  }
}

function setSelectedStatusText(status = "idle") {
  if (!dom.selectedStatus) return;
  dom.selectedStatus.textContent = status || "idle";
}

async function syncSelectedAgentState() {
  const agent = getSelectedAgent();
  const sessionsBtn = document.getElementById("btn-sessions");

  if (!agent) {
    dom.embedTitle.textContent = "Select an assistant";
    setSelectedStatusText("idle");
    setChatStatus("Ready");
    setButtonDisabled(dom.headerNewChatBtn, true, "Select an assistant first");
    setButtonDisabled(sessionsBtn, true, "Select an assistant first");
    setButtonDisabled(dom.homeStartChatBtn, true, "Select an assistant first");
    dom.homeTitle && (dom.homeTitle.textContent = "Select an assistant");
    dom.homeSubtitle && (dom.homeSubtitle.textContent = "Choose an assistant from the left to start chatting, inspect tasks, or browse bundles.");
    dom.homeAgentSummary && (dom.homeAgentSummary.textContent = "No assistant selected.");
    if (state.activeNavSection === "assistants") {
      setMainView("home");
    }
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
  setSelectedStatusText(status);
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

  if (state.activeNavSection !== "assistants") {
    syncMainHeader();
    return;
  }

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
        name: matchedSkill.name || matchedSkill.runtime_name || matchedSkill.opencode_name || slashInvocation.name,
        runtime_name: matchedSkill.runtime_name || matchedSkill.opencode_name || matchedSkill.name || slashInvocation.name,
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
    chatState.inflightEventStream = {
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
    if (typeof createAgentTimelineState === "function") {
      if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
      else chatState.inflightAgentTimeline = null;
      chatState.inflightAgentTimeline = createAgentTimelineState({
        requestId: clientRequestId,
        sessionId: sessionIdAtSend || "",
      });
    }
    ensureEventSocketForAgent(agentIdAtSend, sessionIdAtSend, clientRequestId);
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
  persistInflightChatRun(agentIdAtSend, {
    session_id: sessionIdAtSend,
    request_id: clientRequestId,
    message_preview: requestMessage,
    started_at: new Date(requestCtx.startedAt).toISOString(),
  });
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
    await handleAgentChatFailure(agentIdAtSend, requestCtx, error);
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
    clearAssistantTimelineFromArticle(article);
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
  clearAssistantTimelineFromArticle(article);
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
  clearAssistantTimelineFromArticle(article);
  const markdownEl = article.querySelector(".message-markdown") || article.appendChild(document.createElement("div"));
  markdownEl.className = "message-markdown max-w-none text-sm";
  markdownEl.innerHTML = "";
  const warningBlock = document.createElement("div");
  warningBlock.className = "chat-completion-warning-block";
  warningBlock.innerHTML = renderCompletionDiagnosticFields(finalPayload);
  markdownEl.appendChild(warningBlock);
  const responseEl = document.createElement("div");
  responseEl.className = "chat-incomplete-response md-render";
  responseEl.dataset.md = responseText || "No final assistant response was returned.";
  responseEl.dataset.displayBlocks = "[]";
  markdownEl.appendChild(responseEl);
  article.querySelector('.assistant-stream-cursor')?.remove();
  article.querySelector('.assistant-waiting-indicator')?.remove();
  renderMarkdown(responseEl.parentElement); decorateToolMessages(article); renderIcons();
  return true;
}
function mergeFinalStreamSnapshot(agentId, requestCtx, finalPayload = {}) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  const completionState = getCompletionState(finalPayload) || (finalPayload?.ok === false ? "error" : "");
  const status = ["blocked", "incomplete", "error", "failed", "empty_final"].includes(completionState)
    ? (completionState === "failed" ? "error" : completionState)
    : "completed";
  const existing = chatState.lastEventStreamSnapshot || chatState.inflightEventStream || { events: [] };
  const finalPayloadEvents = [
    ...normalizePayloadStreamEvents(finalPayload?.events || []),
    ...normalizePayloadStreamEvents(finalPayload?.runtime_events || []),
  ];
  const mergedEvents = mergeRuntimeStreamEvents(existing.events || [], finalPayloadEvents).slice(-100);
  const finalContextState =
    finalPayload?.context_state ||
    finalPayload?.contextState ||
    existing.contextState ||
    existing.context_state ||
    null;
  chatState.lastEventStreamSnapshot = {
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
}
function terminalStatusFromCompletionState(completionState) {
  if (completionState === "failed") return "error";
  if (["blocked", "incomplete", "error", "empty_final"].includes(completionState)) return completionState;
  return completionState || "completed";
}
function finalizeTerminalStreamState(agentId, requestCtx, finalPayload = {}) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  stopRecoveredRunPolling(requestCtx);
  clearPersistedInflightChatRun(agentId, requestCtx?.clientRequestId || requestCtx?.requestId || finalPayload?.request_id || "");
  const requestId = finalPayload?.request_id || requestCtx?.requestId || requestCtx?.clientRequestId || "";
  const sessionId = finalPayload?.session_id || requestCtx?.sessionIdAtSend || chatState.sessionId || "";
  const completionState = getCompletionState(finalPayload) || (finalPayload?.ok === false ? "error" : "");
  const status = terminalStatusFromCompletionState(completionState);
  if (chatState.inflightEventStream && (!requestId || !chatState.inflightEventStream.requestId || chatState.inflightEventStream.requestId === requestId || chatState.inflightEventStream.id === requestId)) {
    chatState.inflightEventStream.completed = true;
    chatState.inflightEventStream.status = status;
    chatState.inflightEventStream.completion_state = completionState;
    chatState.inflightEventStream.incomplete_reason = finalPayload?.incomplete_reason || "";
  }
  const existing = chatState.lastEventStreamSnapshot || chatState.inflightEventStream || { events: [] };
  chatState.lastEventStreamSnapshot = {
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
  if (typeof finalizeAgentTimelineState === "function") {
    finalizeAgentTimelineState(chatState, requestCtx, finalPayload);
  }
  chatState.inflightEventStream = null;
  if (chatState.currentRequest?.clientRequestId === requestCtx?.clientRequestId) chatState.currentRequest = null;
  chatState.isSubmitting = false;
  clearWaitingForRuntimeEventsTimer(requestCtx);
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
  mergeFinalStreamSnapshot(agentId, requestCtx, finalPayload);
  finalizeTerminalStreamState(agentId, requestCtx, finalPayload);
  if (state.selectedAgentId === agentId) setTerminalCompletionStatus(finalPayload);
  cleanupChatStreamRequest(agentId, requestCtx, { keepStatus: true });
}
function cleanupChatStreamRequest(agentIdAtSend, requestCtx, { keepStatus = false } = {}) {
  const chatState = ensureChatState(agentIdAtSend);
  clearWaitingForRuntimeEventsTimer(requestCtx);
  stopRecoveredRunPolling(requestCtx);
  clearPersistedInflightChatRun(agentIdAtSend, requestCtx?.clientRequestId || requestCtx?.requestId || "");
  if (requestCtx?.streamIncomplete || requestCtx?.streamFailed) {
    finalizeTerminalStreamState(agentIdAtSend, requestCtx, requestCtx?.terminalPayload || requestCtx?.streamFinalPayload || {});
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
  const requestId = req.clientRequestId || req.requestId || "";
  const sessionId = req.sessionIdAtSend || chatState.sessionId || "";
  if (requestId) {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    void agentApiFor(agentId, `/api/chat/runs/${encodeURIComponent(requestId)}/cancel${query}`, { method: "POST" }).catch(() => {});
  }
  stopRecoveredRunPolling(req);
  clearPersistedInflightChatRun(agentId, requestId);
  req.aborted = true;
  req.streamFailed = true;
  chatState.currentRequest = null;
  chatState.inflightEventStream = null;
  if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
  else chatState.inflightAgentTimeline = null;
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
        const chatState = ensureChatState(agentIdAtSend);
        if (typeof syncAgentTimelineAssistantTextFromStream === "function") {
          syncAgentTimelineAssistantTextFromStream(chatState, requestCtx, previewText);
        }
        if (typeof renderAgentTimelineForCurrentRequest === "function") {
          renderAgentTimelineForCurrentRequest(agentIdAtSend, chatState, {
            requestCtx,
            updateAssistantText: false,
          });
        }
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
    const chatState = ensureChatState(agentIdAtSend);
    if (typeof syncAgentTimelineAssistantTextFromStream === "function") {
      syncAgentTimelineAssistantTextFromStream(chatState, requestCtx, requestCtx.streamedText);
    }
    if (typeof renderAgentTimelineForCurrentRequest === "function") {
      renderAgentTimelineForCurrentRequest(agentIdAtSend, chatState, {
        requestCtx,
        updateAssistantText: false,
      });
    }
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
    finalizeTerminalStreamState(agentIdAtSend, requestCtx, finalPayload);
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
  } catch (e) {
    if (sawEvent && requestCtx?.recovered) return 'unsupported';
    if (sawEvent) throw e;
    return 'unsupported';
  }
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
  if (sawEvent && requestCtx?.recovered) {
    return "unsupported";
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
  const localNormalizePayloadStreamEvents = (typeof normalizePayloadStreamEvents === "function")
    ? normalizePayloadStreamEvents
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

  stopRecoveredRunPolling(requestCtx);
  if (!localHasRenderableAssistantPayload(payload)) {
    const finalSessionId = payload?.session_id || requestCtx?.sessionIdAtSend || chatState?.sessionId || "";
    removeTemporaryAssistantRows({ requestId: requestCtx.clientRequestId, onlyEmpty: true });
    chatState.currentRequest = null;
    chatState.inflightEventStream = null;
    if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
    else chatState.inflightAgentTimeline = null;
    chatState.needsReload = true;
    setChatSubmittingForAgent(agentIdAtSend, false);
    setChatStatus("Completed without a visible assistant response. Reloading session...");
    if (finalSessionId) {
      await loadSessionForAgent(agentIdAtSend, finalSessionId, { render: true });
    }
    if (typeof syncSelectedAgentChatActionControls === "function") syncSelectedAgentChatActionControls();
    return;
  }
  const payloadStreamEvents = [
    ...localNormalizePayloadStreamEvents(payload?.events || []),
    ...localNormalizePayloadStreamEvents(payload?.runtime_events || []),
  ];
  const mergedStreamEvents = mergeRuntimeStreamEvents(
    chatState.inflightEventStream?.events || [],
    payloadStreamEvents,
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
  clearPersistedInflightChatRun(agentIdAtSend, requestCtx.clientRequestId);
  const payloadContextState = payload?.context_state;
  const mergedEventContextState = contextFromEvents(mergedStreamEvents);
  const eventContextState = contextFromEvents(payloadStreamEvents);
  const liveContextState = chatState.inflightEventStream?.contextState;
  const priorContextState = chatState.lastEventStreamSnapshot?.contextState;
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
  const finalStreamSnapshot = {
    ...(chatState.inflightEventStream || {}),
    id: payload.request_id || requestCtx.clientRequestId,
    requestId: payload.request_id || requestCtx.clientRequestId,
    sessionId: finalSessionId,
    events: mergedStreamEvents,
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
      || chatState.inflightEventStream?.contextBudget
      || chatState.lastEventStreamSnapshot?.contextBudget
      || null
    ),
    completedAt: Date.now(),
  };
  chatState.lastEventStreamSnapshot = finalStreamSnapshot;
  if (typeof finalizeAgentTimelineState === "function") {
    finalizeAgentTimelineState(chatState, requestCtx, payload);
  }
  chatState.currentRequest = null;
  chatState.inflightEventStream = null;
  setChatSubmittingForAgent(agentIdAtSend, false);
  if (typeof syncSelectedAgentChatActionControls === "function") syncSelectedAgentChatActionControls();
  if (state.selectedAgentId !== agentIdAtSend) {
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

async function tryRecoverBrokenChatStream(agentIdAtSend, requestCtx) {
  const chatState = ensureChatState(agentIdAtSend);
  const requestId = requestCtx?.clientRequestId || "";
  if (!chatState || !requestId) return false;
  cancelAssistantTypewriter(requestCtx);
  let statusPayload = null;
  try {
    statusPayload = await fetchChatRunStatusForAgent(
      agentIdAtSend,
      requestCtx.sessionIdAtSend || chatState.sessionId || "",
      requestId,
    );
  } catch {
    return false;
  }
  const runState = normalizeChatRunStatus(statusPayload?.state);
  const sessionId = requestCtx.sessionIdAtSend || chatState.sessionId || String(statusPayload?.session_id || "");
  if (!sessionId) return false;
  if (isTerminalChatRunState(runState) || statusPayload?.terminal === true) {
    // The run finished while our stream was broken: deliver it from history.
    await finishRecoveredChatRun(agentIdAtSend, sessionId, requestId, requestCtx, statusPayload);
    return true;
  }
  if (runState !== "running") return false;
  // The run is still executing detached; hand off to the standard recovery
  // flow instead of reporting a false send failure.
  persistInflightChatRun(agentIdAtSend, {
    session_id: sessionId,
    request_id: requestId,
    message_preview: requestCtx.message || "",
    started_at: requestCtx.startedAt ? new Date(requestCtx.startedAt).toISOString() : new Date().toISOString(),
  });
  chatState.currentRequest = null;
  if (state.selectedAgentId === agentIdAtSend) {
    setChatStatus("Connection lost. Reconnecting to running response...");
  }
  const recovered = await recoverInflightChatRunForAgent(
    agentIdAtSend,
    sessionId,
    {},
    { render: state.selectedAgentId === agentIdAtSend },
  );
  if (!recovered) chatState.currentRequest = requestCtx;
  return recovered;
}

async function handleAgentChatFailure(agentIdAtSend, requestCtx, error) {
  const chatState = ensureChatState(agentIdAtSend);
  if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestCtx.clientRequestId) return;
  if (requestCtx.usedStream && !requestCtx.aborted) {
    // A broken stream is not proof of failure: the run usually continues
    // detached on the runtime. Verify before declaring a send failure.
    try {
      if (await tryRecoverBrokenChatStream(agentIdAtSend, requestCtx)) return;
    } catch {
      // Fall through to the regular failure handling.
    }
    if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestCtx.clientRequestId) return;
  }
  const restoredMessage = requestCtx.backupMessage || "";
  const errorMsg = error?.message || "Send failed";
  const finalPayload = {
    completion_state: "error",
    incomplete_reason: errorMsg,
    request_id: requestCtx.clientRequestId,
    session_id: requestCtx.sessionIdAtSend || chatState.sessionId || "",
  };
  stopRecoveredRunPolling(requestCtx);
  clearPersistedInflightChatRun(agentIdAtSend, requestCtx.clientRequestId);
  if (chatState.inflightEventStream) {
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
    chatState.inflightEventStream.events.push(failedEvent);
    chatState.inflightEventStream.completed = true;
    chatState.lastEventStreamSnapshot = { ...chatState.inflightEventStream };
  }
  requestCtx.streamFailed = true;
  requestCtx.terminalPayload = finalPayload;
  if (typeof finalizeTerminalStreamState === "function") finalizeTerminalStreamState(agentIdAtSend, requestCtx, finalPayload);
  else {
    chatState.lastEventStreamSnapshot = chatState.lastEventStreamSnapshot || (chatState.inflightEventStream ? { ...chatState.inflightEventStream } : null);
    chatState.inflightEventStream = null;
    if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
    else chatState.inflightAgentTimeline = null;
  }
  chatState.currentRequest = null;
  setChatSubmittingForAgent(agentIdAtSend, false);
  if (state.selectedAgentId !== agentIdAtSend) {
    chatState.draftText = restoredMessage;
    chatState.pendingFiles = [];
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
  showToast("Send failed. Please re-attach files before retrying.", { variant: 'error' });
  renderInputPreview();
  syncChatInputHeight();
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
    setSelectedStatusText(status);
    setChatStatus("Ready");
    return;
  }

  dom.embedTitle.textContent = "Select an assistant";
  setSelectedStatusText("idle");
  setChatStatus("Ready");
}

function renderWorkspaceDetailPlaceholder(message = "Select a bundle or task from the left sidebar.", workspaceState = "workspace-placeholder") {
  if (!dom.workspaceDetailContent) return;
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = workspaceState;
  dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state">${safe(message)}</div>`;
}

function syncDashboardScopeControls() {
  const scope = state.dashboardScope || "all";
  if (dom.dashboardScopeFilter && dom.dashboardScopeFilter.value !== scope) {
    dom.dashboardScopeFilter.value = scope;
  }
  if (dom.dashboardFilterSummary) {
    dom.dashboardFilterSummary.textContent = scope === "mine" ? "Only your owned work" : "All visible work";
  }
  const root = document.getElementById("dashboard-panel-root");
  if (root) {
    root.querySelectorAll("button[data-dashboard-scope]").forEach((button) => {
      const buttonScope = button.getAttribute("data-dashboard-scope") || "";
      button.classList.toggle("is-active", buttonScope === scope);
    });
  }
}

async function loadDashboardPanel({ scope = state.dashboardScope || "all" } = {}) {
  if (!dom.workspaceDetailContent) return;
  state.dashboardScope = scope === "mine" ? "mine" : "all";
  syncDashboardScopeControls();
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "dashboard-loading";
  dom.workspaceDetailContent.innerHTML = '<div class="portal-inline-state">Loading dashboard...</div>';
  try {
    await htmx.ajax("GET", `/app/dashboard/panel?scope=${encodeURIComponent(state.dashboardScope)}`, {
      target: "#workspace-detail-content",
      swap: "innerHTML",
    });
    dom.workspaceDetailContent.dataset.workspaceState = "dashboard";
    syncDashboardScopeControls();
    syncMainHeader();
    renderIcons();
  } catch (error) {
    dom.workspaceDetailContent.dataset.workspaceState = "dashboard-error";
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed to load dashboard: ${safe(error.message)}</div>`;
  }
}

function scrollDashboardSection(shortcut) {
  const selectorByShortcut = {
    attention: '[data-dashboard-section="attention"]',
    workload: '[data-dashboard-section="workload"]',
    "delegation-health": '[data-dashboard-section="delegation-health"]',
  };
  const selector = selectorByShortcut[shortcut];
  const target = selector ? dom.workspaceDetailContent?.querySelector(selector) : null;
  target?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function openDashboardAgent(agentId) {
  if (!agentId) return;
  await setActiveNavSection("assistants", { toggleIfSame: false, updateRoute: false });
  await selectAgentById(agentId);
}

async function openDashboardDelegation(ruleId) {
  if (!ruleId) return;
  await setActiveNavSection("delegations", { toggleIfSame: false, updateRoute: false });
  await loadDelegationRules();
  await openDelegationRulePanel(ruleId);
}

function getSecondaryPaneLabel() {
  if (state.activeNavSection === "dashboard") return "Dashboard";
  if (state.activeNavSection === "bundles") return "Bundles";
  if (state.activeNavSection === "tasks") return "Tasks";
  if (state.activeNavSection === "runtime-profiles") return "Runtime Profiles";
  if (state.activeNavSection === "delegations") return "Delegations";
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
  const addDelegationBtn = dom.addDelegationBtn;
  if (addAgentBtn) addAgentBtn.classList.add("hidden");
  if (addBundleBtn) addBundleBtn.classList.add("hidden");
  if (refreshBundlesBtn) refreshBundlesBtn.classList.add("hidden");
  if (addTaskBtn) addTaskBtn.classList.add("hidden");
  if (addRuntimeProfileBtn) addRuntimeProfileBtn.classList.add("hidden");
  if (addDelegationBtn) addDelegationBtn.classList.add("hidden");

  if (state.activeNavSection === "dashboard") {
    dom.secondaryPaneEyebrow.textContent = "Portal";
    dom.secondaryPaneTitle.textContent = "Dashboard";
    syncDashboardScopeControls();
  } else if (state.activeNavSection === "assistants") {
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
  } else if (state.activeNavSection === "delegations") {
    dom.secondaryPaneEyebrow.textContent = "Workspace";
    dom.secondaryPaneTitle.textContent = "Delegations";
    if (addDelegationBtn) addDelegationBtn.classList.remove("hidden");
  } else {
    dom.secondaryPaneEyebrow.textContent = "My Space";
    dom.secondaryPaneTitle.textContent = "Runtime Profiles";
    if (addRuntimeProfileBtn) addRuntimeProfileBtn.classList.remove("hidden");
  }
}

function syncMainHeader() {
  const assistantMode = state.activeNavSection === "assistants";

  const sessionsBtn = document.getElementById("btn-sessions");
  const assistantOnlyControls = [sessionsBtn, dom.headerNewChatBtn, dom.detailToggle, document.getElementById("btn-files")];
  assistantOnlyControls.forEach((el) => {
    if (!el) return;
    el.classList.toggle("hidden", !assistantMode);
  });

  if (assistantMode) {
    restoreAssistantHeaderState();
  } else {
    if (state.activeNavSection === "dashboard") {
      dom.embedTitle.textContent = "Dashboard";
      setChatStatus("Operational overview across assistants, tasks, and delegations");
    } else if (state.activeNavSection === "bundles") {
      dom.embedTitle.textContent = "Bundles";
      setChatStatus("Browse and open bundle detail in the main stage");
    } else if (state.activeNavSection === "tasks") {
      dom.embedTitle.textContent = "Tasks";
      setChatStatus("Browse tasks and open task detail in the main stage");
    } else if (state.activeNavSection === "delegations") {
      dom.embedTitle.textContent = "Delegations";
      setChatStatus("Manage delegations");
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
  if (section === "delegations") {
    renderWorkspaceDetailPlaceholder("Select a delegation from the left sidebar.", "delegations-placeholder");
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
    bundle_label: detail?.bundle_label || "Requirement Bundle",
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
    const leftTuple = [left?.domain || "", left?.title || "", left?.bundle_ref?.path || ""];
    const rightTuple = [right?.domain || "", right?.title || "", right?.bundle_ref?.path || ""];
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
    : new Set(["dashboard", "assistants", "bundles", "tasks", "runtime-profiles", "delegations"]);
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

  dom.dashboardMenuBtn?.classList.toggle("is-active", state.activeNavSection === "dashboard");
  dom.railAssistantsBtn?.classList.toggle("is-active", state.activeNavSection === "assistants");
  dom.bundlesMenuBtn?.classList.toggle("is-active", state.activeNavSection === "bundles");
  dom.tasksMenuBtn?.classList.toggle("is-active", state.activeNavSection === "tasks");
  dom.runtimeProfilesMenuBtn?.classList.toggle("is-active", state.activeNavSection === "runtime-profiles");
  dom.delegationsMenuBtn?.classList.toggle("is-active", state.activeNavSection === "delegations");

  dom.dashboardNavSection?.classList.toggle("hidden", state.activeNavSection !== "dashboard");
  dom.assistantsNavSection?.classList.toggle("hidden", state.activeNavSection !== "assistants");
  dom.bundlesNavSection?.classList.toggle("hidden", state.activeNavSection !== "bundles");
  dom.tasksNavSection?.classList.toggle("hidden", state.activeNavSection !== "tasks");
  dom.runtimeProfilesNavSection?.classList.toggle("hidden", state.activeNavSection !== "runtime-profiles");
  dom.delegationsNavSection?.classList.toggle("hidden", state.activeNavSection !== "delegations");

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
    if (section === "dashboard") {
      renderWorkspaceDetailPlaceholder("Loading dashboard...", "dashboard-loading");
    } else if (section === "assistants") {
      showAssistantDefaultMainView();
    } else if (section === "bundles") {
      showBundlesLoadingMainView();
    } else if (section === "tasks") {
      showTasksLoadingMainView();
    } else if (section === "runtime-profiles") {
      renderWorkspaceDetailPlaceholder("Loading runtime profiles…", "runtime-profiles-loading");
    } else if (section === "delegations") {
      renderWorkspaceDetailPlaceholder("Loading delegations…", "delegations-loading");
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

  if (state.activeNavSection === "dashboard" && shouldRefreshVisibleSection) {
    await loadDashboardPanel();
  }

  if (state.activeNavSection === "runtime-profiles" && shouldRefreshVisibleSection) {
    await refreshRuntimeProfileList({ preserveSelection: !preferSectionLanding });
    if (state.activeNavSection === "runtime-profiles" && !state.secondaryPaneCollapsed) {
      if (preferSectionLanding) {
        const targetProfile = state.runtimeProfiles[0] || null;
        const targetProfileId = targetProfile ? targetProfile.id : null;
        state.selectedRuntimeProfileId = targetProfileId;
        renderRuntimeProfileList();
        if (targetProfileId) {
          await loadRuntimeProfilePanelContent(targetProfileId, { updateRoute: false });
          if (updateRoute && !isApplyingPortalRoute) {
            commitPortalRoute({ section: "runtime-profiles", runtimeProfileId: targetProfileId });
          }
        } else {
          renderWorkspaceDetailPlaceholder("No runtime profiles found.", "runtime-profiles-placeholder");
        }
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
  if (state.activeNavSection === "delegations" && shouldRefreshVisibleSection) {
    await loadDelegationRules();
    const first = state.delegations[0];
    if (preferSectionLanding) {
      state.selectedDelegationRuleId = null;
      renderDelegationRuleNavList();
      if (first) {
        renderWorkspaceDetailPlaceholder("Select a delegation from the left sidebar.", "delegations-placeholder");
        syncMainHeader();
      } else {
        renderWorkspaceDetailPlaceholder("No delegations found.", "delegations-placeholder");
      }
    } else if (!first) {
      renderWorkspaceDetailPlaceholder("No delegations found.", "delegations-placeholder");
    }
  }

  // Landing on the Assistants section (rail/menu) keeps the already-selected
  // agent; load its last session so it shows immediately instead of only after
  // re-clicking the agent. The selectAgentById and route-apply paths carry
  // their own syncSelectedAgentState and do not set preferSectionLanding, so
  // gating on it here avoids a double load.
  if (
    state.activeNavSection === "assistants" &&
    shouldRefreshVisibleSection &&
    preferSectionLanding &&
    state.selectedAgentId
  ) {
    await syncSelectedAgentState();
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
      <div class="portal-bundle-meta">${safe(item.bundle_label || "Requirement Bundle")} · ${safe(item.domain || "unknown")} · ${safe(item.status || "unknown")}</div>
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
      showToast(`Failed to refresh bundles: ${error.message}`, { variant: 'error' });
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

function taskOwnerLabel(task) {
  const ownerName = String(task?.owner_display_name || "").trim();
  if (ownerName) return ownerName;
  const ownerId = task?.owner_user_id ?? "";
  return ownerId === "" || ownerId === null || ownerId === undefined ? "-" : `User ${ownerId}`;
}

function hasActiveTaskFilters() {
  const filters = state.taskFilters || {};
  return Boolean(filters.status !== "all" || filters.owner !== "all");
}

function taskStatusFilterLabel(value) {
  const normalized = String(value || "all").trim().toLowerCase();
  if (normalized === "all") return "all";
  return normalized.replace(/_/g, " ");
}

function taskStatusLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  const labels = {
    queued: "Queued",
    running: "Running",
    blocked: "Blocked",
    done: "Done",
    completed: "Done",
    success: "Done",
    failed: "Failed",
    error: "Failed",
    stale: "Stale",
    cancelled: "Cancelled",
    canceled: "Cancelled",
    pending_restart: "Pending restart",
    cancel_failed: "Cancel failed",
  };
  return labels[normalized] || (normalized ? normalized.replace(/_/g, " ") : "Unknown");
}

function taskStatusTone(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["done", "completed", "success"].includes(normalized)) return "success";
  if (["failed", "error", "cancel_failed"].includes(normalized)) return "error";
  if (["blocked", "stale", "pending_restart"].includes(normalized)) return "warning";
  if (normalized === "running") return "info";
  return "neutral";
}

function portalRowStatusDot(label, tone = "neutral") {
  const displayLabel = String(label || "Status unknown").trim();
  const displayTone = String(tone || "neutral").trim();
  return `<span class="portal-row-status-dot is-${safe(displayTone)}" title="${escapeHtmlAttr(displayLabel)}" aria-label="${escapeHtmlAttr(displayLabel)}"></span>`;
}

function syncTaskFilterControls() {
  const filters = state.taskFilters || { status: "all", owner: "all" };
  if (dom.taskOwnerFilter && dom.taskOwnerFilter.value !== filters.owner) dom.taskOwnerFilter.value = filters.owner;
  if (dom.taskStatusFilter && dom.taskStatusFilter.value !== filters.status) dom.taskStatusFilter.value = filters.status;
  if (dom.taskFilterSummary) {
    const parts = [];
    if (filters.status !== "all") parts.push(taskStatusFilterLabel(filters.status));
    if (filters.owner === "mine") parts.push("owned by you");
    const filterLabel = parts.length ? `Filtered by ${parts.join(", ")}` : "All visible tasks";
    const countLabel = state.taskListHasMore ? `${state.myTasks.length}+ loaded` : `${state.myTasks.length} shown`;
    dom.taskFilterSummary.textContent = `${filterLabel} - ${countLabel}`;
  }
}

function renderTaskNavList(errorMessage = "", { preserveScroll = false } = {}) {
  if (!dom.taskNavList) return;
  const previousScrollTop = preserveScroll ? dom.taskNavList.scrollTop : 0;
  syncTaskFilterControls();
  if (errorMessage) {
    dom.taskNavList.innerHTML = `<div class="portal-inline-state is-error">${safe(errorMessage)}</div>`;
    return;
  }
  if (!state.myTasks.length) {
    dom.taskNavList.innerHTML = `<div class="portal-bundle-list-state">${hasActiveTaskFilters() ? "No tasks match these filters." : "No visible tasks yet."}</div>`;
    return;
  }

  dom.taskNavList.innerHTML = "";
  state.myTasks.forEach((task) => {
    const title = String(task.display_title || task.title || task.task_type || task.id || "Task").trim();
    const displayTitle = title.length > 80 ? `${title.slice(0, 77).trim()}...` : title;
    const timeLabel = formatTaskNavTime(task.created_at || task.updated_at);
    const ownerLabel = taskOwnerLabel(task);
    const statusLabel = taskStatusLabel(task.status);
    const statusDot = portalRowStatusDot(`Task status: ${statusLabel}`, taskStatusTone(task.status));
    const row = document.createElement("button");
    row.type = "button";
    row.className = `portal-task-row${state.selectedTaskId === task.id ? " is-active" : ""}`;
    row.innerHTML = `
      <div class="portal-task-row-head">
        <div class="portal-task-row-title">${safe(displayTitle)}</div>
        ${statusDot}
      </div>
      <div class="portal-task-row-meta">
        <span>Owner ${safe(ownerLabel)}</span>
        ${timeLabel ? `<span>${safe(timeLabel)}</span>` : ""}
      </div>
    `;
    row.addEventListener("click", async () => {
      await openTaskDetailInMain(task.id);
    });
    dom.taskNavList.append(row);
  });
  if (state.taskListLoading || state.taskListHasMore) {
    const sentinel = document.createElement("div");
    sentinel.className = "portal-list-load-state";
    sentinel.textContent = state.taskListLoading ? "Loading more tasks..." : "Scroll for more";
    dom.taskNavList.append(sentinel);
  }
  if (preserveScroll) dom.taskNavList.scrollTop = previousScrollTop;
}

function formatTaskNavTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function refreshMyTasks({ reset = true } = {}) {
  if (!dom.taskNavList || state.taskListLoading) return;
  if (reset) {
    dom.taskNavList.innerHTML = '<div class="portal-bundle-list-state">Loading tasks...</div>';
  }
  state.taskListLoading = true;
  if (reset) {
    state.taskListOffset = 0;
    state.taskListHasMore = true;
    state.myTasks = [];
  } else {
    renderTaskNavList("", { preserveScroll: true });
  }
  let loadError = false;
  try {
    const params = new URLSearchParams({
      limit: String(state.taskPageSize),
      offset: String(state.taskListOffset),
    });
    const filters = state.taskFilters || {};
    if (filters.status && filters.status !== "all") params.set("status", filters.status);
    if (filters.owner && filters.owner !== "all") params.set("owner", filters.owner);
    const tasks = await api(`/api/my/tasks?${params.toString()}`);
    const page = Array.isArray(tasks) ? tasks : [];
    if (reset) {
      state.myTasks = page;
    } else {
      const seen = new Set(state.myTasks.map((task) => task.id));
      state.myTasks = state.myTasks.concat(page.filter((task) => task?.id && !seen.has(task.id)));
    }
    state.taskListOffset += page.length;
    state.taskListHasMore = page.length >= state.taskPageSize;
    if (state.selectedTaskId && !state.myTasks.some((task) => task.id === state.selectedTaskId)) {
      state.selectedTaskId = null;
    }
    renderTaskNavList("", { preserveScroll: !reset });
  } catch (error) {
    loadError = true;
    renderTaskNavList(`Failed to load tasks: ${error.message}`);
  } finally {
    state.taskListLoading = false;
    if (!loadError) renderTaskNavList("", { preserveScroll: !reset });
  }
}

async function loadMoreTasksIfNeeded() {
  if (!dom.taskNavList || state.taskListLoading || !state.taskListHasMore) return;
  const remaining = dom.taskNavList.scrollHeight - dom.taskNavList.scrollTop - dom.taskNavList.clientHeight;
  if (remaining > 160) return;
  await refreshMyTasks({ reset: false });
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
    initializeInlineWizard(dom.workspaceDetailContent.querySelector("#continue-agent-task-form"));
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

  if (latestEventType === "chat.failed" || latestEventState === "error") {
    return {
      level: "error",
      message: "The previous request failed. You can review the last system error and retry.",
    };
  }

  if (metadataIndicatesRunningChatRun(metadata)) {
    return {
      level: "warning",
      message: "Reconnecting to the previous request...",
    };
  }

  return null;
}

const RUNNING_CHAT_RUN_STATES = new Set(["running", "accepted", "queued", "in_progress"]);

function normalizeChatRunState(value) {
  return String(value || "").trim().toLowerCase();
}

function chatRunRequestIdFromMetadata(metadata = {}) {
  const sessionStatus = metadata?.session_status && typeof metadata.session_status === "object"
    ? metadata.session_status
    : {};
  return String(
    metadata.last_execution_id
    || metadata.request_id
    || metadata.latest_request_id
    || sessionStatus.last_execution_id
    || sessionStatus.request_id
    || sessionStatus.latest_request_id
    || ""
  ).trim();
}

function metadataIndicatesRunningChatRun(metadata = {}) {
  const sessionStatus = metadata?.session_status && typeof metadata.session_status === "object"
    ? metadata.session_status
    : {};
  const states = [
    metadata.latest_event_state,
    metadata.completion_state,
    metadata.chatlog_status,
    metadata.status,
    sessionStatus.state,
    sessionStatus.completion_state,
    sessionStatus.chatlog_status,
  ].map(normalizeChatRunState);

  if (states.some((value) => RUNNING_CHAT_RUN_STATES.has(value))) return true;
  return normalizeChatRunState(metadata.latest_event_type) === "chat.started"
    && !!chatRunRequestIdFromMetadata(metadata);
}

function isTerminalChatRunState(stateValue) {
  return ["completed", "success", "failed", "error", "cancelled", "canceled", "blocked", "incomplete"].includes(String(stateValue || "").trim().toLowerCase());
}

function inflightChatRunCandidate(agentId, sessionId, metadata = {}) {
  const persisted = getPersistedInflightChatRun(agentId);
  if (persisted && String(persisted.session_id || "") === String(sessionId || "")) {
    return persisted;
  }
  const requestId = chatRunRequestIdFromMetadata(metadata);
  if (requestId && metadataIndicatesRunningChatRun(metadata)) {
    return {
      agent_id: agentId,
      session_id: sessionId,
      request_id: requestId,
      message_preview: "",
      started_at: metadata.latest_chatlog_at || "",
    };
  }
  return null;
}

async function fetchChatRunStatusForAgent(agentId, sessionId, requestId) {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return agentApiFor(agentId, `/api/chat/runs/${encodeURIComponent(requestId)}${query}`);
}

function stopRecoveredRunPolling(requestCtx) {
  if (requestCtx?.recoveryPollTimerId) {
    clearTimeout(requestCtx.recoveryPollTimerId);
    requestCtx.recoveryPollTimerId = null;
  }
}

async function finishRecoveredChatRun(agentId, sessionId, requestId, requestCtx, statusPayload = {}) {
  const chatState = ensureChatState(agentId);
  stopRecoveredRunPolling(requestCtx);
  clearPersistedInflightChatRun(agentId, requestId);
  clearWaitingForRuntimeEventsTimer(requestCtx);
  if (chatState?.currentRequest?.clientRequestId === requestId || chatState?.currentRequest?.requestId === requestId) {
    chatState.currentRequest = null;
  }
  if (chatState) {
    chatState.isSubmitting = false;
    chatState.inflightEventStream = null;
    if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
    else chatState.inflightAgentTimeline = null;
  }
  if (state.selectedAgentId === agentId) {
    setChatSubmittingForAgent(agentId, false);
    setChatStatus(isTerminalChatRunState(statusPayload?.state) ? "Recovered latest response." : "Ready");
    await loadSessionForAgent(agentId, sessionId, { render: true, recoverRunning: false });
  } else if (chatState) {
    chatState.needsReload = true;
  }
}

const RECOVERED_RUN_POLL_MAX_MS = 30 * 60 * 1000;
const RECOVERED_RUN_POLL_MAX_UNKNOWN_STREAK = 8;
const RECOVERED_RUN_POLL_MAX_FALLBACK_FROZEN_STREAK = 8;

function scheduleRecoveredChatRunPoll(agentId, sessionId, requestId, requestCtx) {
  stopRecoveredRunPolling(requestCtx);
  if (!requestCtx.recoveryPollStartedAt) requestCtx.recoveryPollStartedAt = Date.now();
  requestCtx.recoveryPollTimerId = setTimeout(async () => {
    const chatState = ensureChatState(agentId);
    if (!chatState?.currentRequest || chatState.currentRequest.clientRequestId !== requestId) return;
    try {
      const statusPayload = await fetchChatRunStatusForAgent(agentId, sessionId, requestId);
      if (isTerminalChatRunState(statusPayload?.state) || statusPayload?.terminal === true) {
        await finishRecoveredChatRun(agentId, sessionId, requestId, requestCtx, statusPayload);
        return;
      }
      requestCtx.recoveryUnknownStreak = (normalizeChatRunStatus(statusPayload?.state) === "unknown" || statusPayload?.error)
        ? (requestCtx.recoveryUnknownStreak || 0) + 1
        : 0;
      // A "running" state served from a metadata/chatlog fallback with a
      // frozen updated_at is a phantom (the registry record is gone and
      // nothing refreshes the fallback); a live run either has a registry
      // record or keeps advancing the fallback timestamp.
      const sourceOfTruth = String(statusPayload?.source_of_truth || "");
      const runningFromFallback = normalizeChatRunStatus(statusPayload?.state) === "running"
        && !!sourceOfTruth
        && sourceOfTruth !== "run_registry";
      const fallbackUpdatedAt = String(statusPayload?.updated_at || "");
      if (runningFromFallback && fallbackUpdatedAt === String(requestCtx.recoveryLastFallbackUpdatedAt ?? null)) {
        requestCtx.recoveryFallbackFrozenStreak = (requestCtx.recoveryFallbackFrozenStreak || 0) + 1;
      } else {
        requestCtx.recoveryFallbackFrozenStreak = 0;
      }
      requestCtx.recoveryLastFallbackUpdatedAt = runningFromFallback ? fallbackUpdatedAt : null;
    } catch {
      requestCtx.recoveryUnknownStreak = (requestCtx.recoveryUnknownStreak || 0) + 1;
    }
    const elapsedMs = Date.now() - (requestCtx.recoveryPollStartedAt || Date.now());
    if (
      elapsedMs >= RECOVERED_RUN_POLL_MAX_MS
      || (requestCtx.recoveryUnknownStreak || 0) >= RECOVERED_RUN_POLL_MAX_UNKNOWN_STREAK
      || (requestCtx.recoveryFallbackFrozenStreak || 0) >= RECOVERED_RUN_POLL_MAX_FALLBACK_FROZEN_STREAK
    ) {
      // Never poll forever: give up, unlock the composer, show latest state.
      showToast("Stopped waiting for the running response; showing the latest session state.");
      await finishRecoveredChatRun(agentId, sessionId, requestId, requestCtx, { state: "unknown" });
      return;
    }
    scheduleRecoveredChatRunPoll(agentId, sessionId, requestId, requestCtx);
  }, 2500);
}

async function reconnectRecoveredChatStreamForAgent(agentId, requestCtx) {
  if (!agentId || !requestCtx?.clientRequestId || requestCtx.reconnectStreamStarted) return;
  if (requestCtx.reconnectStreamAllowed === false) return;
  requestCtx.reconnectStreamStarted = true;
  try {
    await trySubmitChatStreamForSelectedAgent(agentId, requestCtx, {
      message: requestCtx.message || "[reconnect]",
      session_id: requestCtx.sessionIdAtSend || "",
      request_id: requestCtx.clientRequestId,
      reconnect: true,
    });
  } catch (error) {
    requestCtx.reconnectStreamError = String(error?.message || error || "");
  }
}

async function recoverInflightChatRunForAgent(agentId, sessionId, metadata = {}, { render = agentId === state.selectedAgentId } = {}) {
  const chatState = ensureChatState(agentId);
  if (!agentId || !sessionId || !chatState || chatState.currentRequest) return false;
  const candidate = inflightChatRunCandidate(agentId, sessionId, metadata);
  if (!candidate?.request_id) return false;
  const metadataSaysRunning = metadataIndicatesRunningChatRun(metadata);
  const insertedRecoveryPending = render ? renderRecoveredPendingAssistantArticle(agentId, candidate.request_id, "Reconnecting") : false;
  if (insertedRecoveryPending && state.selectedAgentId === agentId) {
    setChatStatus("Reconnecting to running response...");
  }

  let statusPayload = null;
  try {
    statusPayload = await fetchChatRunStatusForAgent(agentId, sessionId, candidate.request_id);
  } catch {
    statusPayload = null;
  }

  if (!statusPayload || statusPayload.state === "unknown" || statusPayload.error) {
    if (!metadataSaysRunning) {
      clearPersistedInflightChatRun(agentId, candidate.request_id);
      if (insertedRecoveryPending) removePendingAssistantArticle(candidate.request_id);
      return false;
    }
    statusPayload = {
      ok: true,
      state: "running",
      terminal: false,
      session_id: sessionId,
      request_id: candidate.request_id,
      source_of_truth: "session_metadata",
    };
  }

  if (isTerminalChatRunState(statusPayload.state) || statusPayload.terminal === true) {
    if (insertedRecoveryPending) removePendingAssistantArticle(candidate.request_id);
    await finishRecoveredChatRun(agentId, sessionId, candidate.request_id, { clientRequestId: candidate.request_id }, statusPayload);
    return true;
  }

  const requestCtx = {
    requestId: candidate.request_id,
    clientRequestId: candidate.request_id,
    agentId,
    sessionIdAtSend: sessionId,
    message: candidate.message_preview || "",
    startedAt: candidate.started_at ? Date.parse(candidate.started_at) || Date.now() : Date.now(),
    streamStartedAt: Date.now(),
    sawRuntimeEvent: false,
    sawDelta: false,
    sawFinal: false,
    streamCompleted: false,
    streamFailed: false,
    streamIncomplete: false,
    recovered: true,
    usedStream: true,
    typewriter: { targetText: "", visibleText: "", timerId: null, finalizing: false, cancelled: false },
  };
  chatState.currentRequest = requestCtx;
  requestCtx.reconnectStreamAllowed = statusPayload?.source_of_truth === "run_registry";
  chatState.inflightEventStream = {
    id: candidate.request_id,
    requestId: candidate.request_id,
    sessionId,
    events: [],
    completed: false,
    started: false,
    startedAt: Date.now(),
  };
  if (typeof createAgentTimelineState === "function") {
    if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
    else chatState.inflightAgentTimeline = null;
    chatState.inflightAgentTimeline = createAgentTimelineState({ requestId: candidate.request_id, sessionId });
  }
  setChatSubmittingForAgent(agentId, true);
  if (render) renderRecoveredPendingAssistantArticle(agentId, candidate.request_id, "Reconnecting");
  if (state.selectedAgentId === agentId) {
    setChatStatus("Reconnected to running response.");
  }
  ensureEventSocketForAgent(agentId, sessionId, candidate.request_id);
  startWaitingForRuntimeEventsTimer(agentId, requestCtx);
  scheduleRecoveredChatRunPoll(agentId, sessionId, candidate.request_id, requestCtx);
  if (requestCtx.reconnectStreamAllowed) void reconnectRecoveredChatStreamForAgent(agentId, requestCtx);
  persistInflightChatRun(agentId, {
    session_id: sessionId,
    request_id: candidate.request_id,
    message_preview: candidate.message_preview || "",
    started_at: candidate.started_at || new Date().toISOString(),
  });
  return true;
}

async function loadSessionForAgent(agentId, sessionId, { render = agentId === state.selectedAgentId, recoverRunning = true } = {}) {
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
      source_of_truth: canonicalMessages.length ? "runtime" : data.metadata?.source_of_truth,
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
  if (recoverRunning) {
    await recoverInflightChatRunForAgent(agentId, normalized, normalizedPayload.metadata || {}, { render });
  }
}

async function loadSession(sessionId) {
  return loadSessionForAgent(state.selectedAgentId, sessionId, { render: true });
}

async function renameSessionForAgent(agentId, sessionId, currentName) {
  const normalizedSessionId = (sessionId || "").trim();
  if (!agentId || !normalizedSessionId) return;

  const defaultName = String(currentName || "").trim();
  const proposedName = await showPrompt({ title: "Rename session", defaultValue: defaultName, confirmText: "Rename" });
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
    showToast(`Rename failed: ${safe(error.message)}`, { variant: 'error' });
  }
}

async function deleteSessionForAgent(agentId, sessionId) {
  const normalizedSessionId = (sessionId || "").trim();
  if (!agentId || !normalizedSessionId) return;
  if (!(await showConfirm({ title: "Delete session", message: "This can't be undone.", confirmText: "Delete", danger: true }))) return;

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
        if (chatState) {
          chatState.inflightEventStream = null;
          if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
          else chatState.inflightAgentTimeline = null;
        }
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
    showToast(`Delete failed: ${safe(error.message)}`, { variant: 'error' });
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

  const confirmed = await showConfirm({ title: "Delete files", message: `Delete ${paths.length} selected item(s)? This can't be undone.`, confirmText: "Delete", danger: true });
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
    { value: "gpt-5.4", label: "GPT-5.4" },
    { value: "gpt-5.5", label: "GPT-5.5" },
    { value: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
    { value: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
    { value: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
  ],
  ai_platform: [
    { value: "gpt-5.4", label: "GPT-5.4" },
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

  const projectHtml = group === "jira"
    ? `<input type="text" data-field="project" value="" placeholder="Project" class="portal-form-input" />`
    : `<input type="text" data-field="space" value="" placeholder="Space Key" class="portal-form-input" />`;
  const apiVersionHtml = group === "jira"
    ? `<div class="portal-panel-grid cols-2"><select data-field="api_version" class="portal-form-select"><option value="" selected>Auto API Version</option><option value="2">REST API v2</option><option value="3">REST API v3</option></select><div></div></div>`
    : "";
  const urlPlaceholder = group === "confluence"
    ? "URL (e.g. https://yourcompany.atlassian.net/wiki)"
    : "URL (e.g. https://yourcompany.atlassian.net)";

  div.innerHTML = `
    <input type="hidden" data-original-field="name" value="" />
    <input type="hidden" data-original-field="url" value="" />
    <div class="portal-settings-instance-head">
      <span class="portal-settings-instance-title">Instance</span>
      <label class="portal-checkbox-row"><input type="checkbox" data-field="enabled" value="1" checked /><span>Enabled</span></label>
      <button type="button" class="portal-instance-remove" data-action="remove-instance" data-group="${group}">Remove</button>
    </div>
    <div class="portal-panel-grid cols-2"><input type="text" data-field="name" value="" placeholder="Name" class="portal-form-input" /><input type="text" data-field="url" value="" placeholder="${urlPlaceholder}" class="portal-form-input" /></div>
    <div class="portal-panel-grid cols-2"><input type="text" data-field="username" value="" placeholder="Email" class="portal-form-input" /><input type="password" data-field="password" value="" placeholder="Password" class="portal-form-input" /></div>
    <div class="portal-panel-grid cols-2"><input type="password" data-field="token" value="" placeholder="API token" class="portal-form-input" />${projectHtml}</div>
    ${apiVersionHtml}
  `;
  container.append(div);
  normalizeInstanceInputs(root, group);

  if (window.initPasswordToggles) window.initPasswordToggles(root);
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

function maskCopilotSecret(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.length <= 8) return "••••";
  return `${raw.slice(0, 4)}…${raw.slice(-4)}`;
}

function setCopilotResultSummary(root, message, kind = "") {
  const summary = root?.querySelector("[data-copilot-result-summary]");
  if (!summary) return;
  summary.textContent = message || "";
  summary.classList.toggle("hidden", !message);
  summary.classList.toggle("is-error", kind === "error");
  summary.classList.toggle("is-success", kind === "success");
}
function getManagedCopilotState(root) {
  if (!root.__managedCopilotState) root.__managedCopilotState = { authInterval: null, timerInterval: null };
  return root.__managedCopilotState;
}

function stopCopilotPolling(root) {
  const st = getManagedCopilotState(root);
  if (st.authInterval) clearInterval(st.authInterval);
  if (st.timerInterval) clearInterval(st.timerInterval);
  st.authInterval = null;
  st.timerInterval = null;
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

function finishCopilotAuthWithMessage(root, message, kind = "error") {
  stopCopilotPolling(root);
  const finalMessage = message || "Authorization failed";
  if (typeof setCopilotResultSummary === "function") {
    setCopilotResultSummary(root, finalMessage, kind);
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
    setCopilotResultSummary(root, "", "");
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
  // Show only the selected provider's rich-config fields (e.g. AI Platform).
  root.querySelectorAll("[data-provider-fields]").forEach((el) => {
    el.classList.toggle("hidden", el.getAttribute("data-provider-fields") !== provider);
  });
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
    showToast(`Test failed: ${safe(error.message)}`, { variant: 'error' });
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function startCopilotAuth(root) {
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
      body: JSON.stringify({}),
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
      setCopilotResultSummary(root, "Device authorization started. Complete GitHub verification, then wait for this panel to update.");
    }

    let remaining = Number(data.expires_in || 600);
    if (timer) timer.textContent = `${remaining}s`;
    const st = getManagedCopilotState(root);
    st.timerInterval = setInterval(() => {
      remaining -= 1;
      if (timer) timer.textContent = `${Math.max(remaining, 0)}s`;
      if (remaining <= 0) {
        finishCopilotAuthWithMessage(root, "Authorization timed out. Please start again.");
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
        finishCopilotAuthWithMessage(root, `Authorization check failed: ${safe(error.message)}`);
        return;
      }

      let check = null;
      try {
        check = await checkResp.json();
      } catch (_error) {
        finishCopilotAuthWithMessage(root, "Authorization check failed: invalid response");
        return;
      }

      if (!checkResp.ok) {
        finishCopilotAuthWithMessage(
          root,
          check?.message || check?.details || check?.error || `Authorization check failed (HTTP ${checkResp.status})`,
        );
        return;
      }

      if (check?.error && !check?.status) {
        finishCopilotAuthWithMessage(root, check.message || check.details || check.error);
        return;
      }

      if (!check?.status) {
        finishCopilotAuthWithMessage(root, "Authorization check failed: missing status");
        return;
      }

      if (check.status === "pending") return;

      if (check.status === "authorized") {
        stopCopilotPolling(root);
        if (instructions) instructions.classList.add("hidden");
        const token = check?.token || check?.oauth?.access || check?.oauth?.refresh || "";
        const updated = setCopilotApiKeyField(root, token);

        if (!token || !updated) {
          finishCopilotAuthWithMessage(
            root,
            "Authorization completed, but no token was returned. Please try again.",
            "error"
          );
          showToast("GitHub Copilot authorization completed, but no token was returned.");
          return;
        }

        setCopilotResultSummary(root, "Authorization complete. API Key field has been filled. Click Save Settings to persist.", "success");
        showToast("Authorization complete. API Key field has been filled. Click Save Settings to persist.");
      } else if (check.status === "expired" || check.status === "declined" || check.status === "failed") {
        const errorMessage = check.message || check.error || "Authorization failed";
        finishCopilotAuthWithMessage(root, errorMessage);
        if (typeof setCopilotResultSummary === "function") setCopilotResultSummary(root, errorMessage, "error");
      } else {
        const unknownStatusMessage = `Authorization check failed: unknown status ${safe(check.status)}`;
        finishCopilotAuthWithMessage(root, unknownStatusMessage);
        if (typeof setCopilotResultSummary === "function") setCopilotResultSummary(root, unknownStatusMessage, "error");
      }
    }, (Number(data.interval) || 5) * 1000);
  } catch (error) {
    showToast(`Copilot authorization failed: ${safe(error.message)}`, { variant: 'error' });
    finishCopilotAuthWithMessage(root, `Copilot authorization failed: ${safe(error.message)}`);
  }
}

function initializeManagedSettingsRoot(root) {
  if (!root) return;
  normalizeInstanceInputs(root, "jira");
  normalizeInstanceInputs(root, "confluence");
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
    const section = sectionNameForElement(event.target);
    if (section) markManagedSectionTouched(root, section);
  });
  root.addEventListener("input", (event) => {
    const section = sectionNameForElement(event.target);
    if (section) markManagedSectionTouched(root, section);
  });
  root.addEventListener("click", async (event) => {
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
      await startCopilotAuth(root);
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
    if (chatState) {
      chatState.inflightEventStream = null;
      if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
      else chatState.inflightAgentTimeline = null;
    }
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
    chatState.inflightEventStream = null;
    if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
    else chatState.inflightAgentTimeline = null;
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
    setSelectedStatusText(normalizedStatus);
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
  chatState.inflightEventStream = null;
  if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
  else chatState.inflightAgentTimeline = null;
  setChatSubmittingForAgent(agentId, false, { suppressSync: true });
}

async function action(path, method = "POST", needsConfirm = false) {
  if (needsConfirm && !(await showConfirm({ title: "Confirm action", message: "Please confirm this action.", confirmText: "Continue" }))) return;
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

const CREATE_AGENT_STEPS = ["runtime", "profile", "instructions", "skills", "review"];

function createAgentStepIndex(step) {
  const index = CREATE_AGENT_STEPS.indexOf(step);
  return index >= 0 ? index : 0;
}

function createAgentFieldValue(form, name) {
  return (form?.elements?.[name]?.value || "").toString().trim();
}

function createAgentRuntimeType(form, defaults) {
  return normalizeRuntimeTypeValue(createAgentFieldValue(form, "runtime_type"), defaults || state.agentDefaults || {});
}

function createAgentSelectedProfileLabel(form) {
  const select = form?.elements?.["runtime_profile_id"];
  if (!select || !select.value) return "";
  return select.options?.[select.selectedIndex]?.textContent || select.value;
}

function setCreateAgentRepoFeedback(elementId, kind, message) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = message || "";
  setModalFeedback(el, kind || "", el.textContent);
}

function branchSelectPlaceholder(selectEl, defaultBranch = "") {
  const base = selectEl?.dataset?.branchPlaceholder || "Configured default branch";
  const branch = String(defaultBranch || "").trim();
  return branch ? `${base} (${branch})` : base;
}

function populateBranchSelect(selectId, branches, selectedValue = "", defaultBranch = "") {
  const selectEl = document.getElementById(selectId);
  if (!selectEl) return;
  const selected = String(selectedValue || "").trim();
  const uniqueBranches = Array.from(new Set((branches || []).map((branch) => String(branch || "").trim()).filter(Boolean)));
  const options = [];
  if (selected && !uniqueBranches.includes(selected)) options.push(selected);
  options.push(...uniqueBranches);
  selectEl.innerHTML = [
    `<option value="">${safe(branchSelectPlaceholder(selectEl, defaultBranch))}</option>`,
    ...options.map((branch) => `<option value="${escapeHtmlAttr(branch)}">${safe(branch)}</option>`),
  ].join("");
  selectEl.value = selected;
}

async function loadGitRepoBranches(repoUrl) {
  const normalizedRepoUrl = String(repoUrl || "").trim();
  if (!normalizedRepoUrl) return [];
  if (state.gitRepoBranches.has(normalizedRepoUrl)) {
    return state.gitRepoBranches.get(normalizedRepoUrl);
  }
  const data = await api(`/api/git-repos/branches?repo_url=${encodeURIComponent(normalizedRepoUrl)}`);
  const branches = Array.isArray(data?.branches) ? data.branches : [];
  state.gitRepoBranches.set(normalizedRepoUrl, branches);
  return branches;
}

async function refreshCreateRepoBranches(kind) {
  const form = document.getElementById("create-form");
  if (!form?.elements) return;
  const isAgentSettings = kind === "agent-settings";
  const repoField = isAgentSettings ? "agent_settings_repo_url" : "skill_repo_url";
  const branchField = isAgentSettings ? "agent_settings_branch" : "skill_branch";
  const selectId = isAgentSettings ? "create-agent-settings-branch-select" : "create-skill-branch-select";
  const feedbackId = isAgentSettings ? "create-agent-settings-msg" : "create-skills-msg";
  const defaultBranch = isAgentSettings ? state.agentDefaults?.default_agent_settings_branch : state.agentDefaults?.default_skill_branch;
  const repoUrl = createAgentFieldValue(form, repoField);
  if (!repoUrl) {
    populateBranchSelect(selectId, [], createAgentFieldValue(form, branchField), defaultBranch);
    setCreateAgentRepoFeedback(feedbackId, "", "");
    return;
  }
  setCreateAgentRepoFeedback(feedbackId, "", "Loading branches...");
  try {
    const branches = await loadGitRepoBranches(repoUrl);
    let selectedBranch = createAgentFieldValue(form, branchField);
    if (!selectedBranch && branches.length) {
      selectedBranch = branches.includes("master") ? "master" : (branches.includes("main") ? "main" : branches[0]);
    }
    populateBranchSelect(selectId, branches, selectedBranch, defaultBranch);
    setCreateAgentRepoFeedback(feedbackId, branches.length ? "success" : "", branches.length ? `${branches.length} branches loaded.` : "No branches found.");
  } catch (error) {
    populateBranchSelect(selectId, [], createAgentFieldValue(form, branchField), defaultBranch);
    setCreateAgentRepoFeedback(feedbackId, "error", error.message || "Failed to load branches.");
  }
}

function syncCreateRuntimeProfileState(form) {
  const profiles = state.runtimeProfiles || [];
  const hasProfiles = profiles.length > 0;
  const select = form?.elements?.["runtime_profile_id"];
  if (select) select.disabled = !hasProfiles;
  const emptyEl = document.getElementById("create-runtime-profile-empty");
  emptyEl?.classList.toggle("hidden", hasProfiles);
}

function renderCreateAgentReview(form, defaults) {
  const reviewEl = document.getElementById("create-agent-review");
  if (!reviewEl) return;
  const runtimeType = createAgentRuntimeType(form, defaults);
  const runtimeConfig = findRuntimeTypeConfig(defaults, runtimeType);
  const rows = [
    ["Assistant Name", createAgentFieldValue(form, "name") || "Untitled"],
    ["Runtime Type", runtimeType],
    ["Runtime Image", runtimeImagePreview(runtimeConfig) || "Configured default"],
    ["Runtime Profile", createAgentSelectedProfileLabel(form) || "Not selected"],
    ["Instructions Repository", createAgentFieldValue(form, "agent_settings_repo_url") || "Configured default"],
    ["Instructions Branch", createAgentFieldValue(form, "agent_settings_branch") || "Configured default"],
    ["Skill Repository", createAgentFieldValue(form, "skill_repo_url") || "Configured default"],
    ["Skill Branch", createAgentFieldValue(form, "skill_branch") || "Configured default"],
  ];
  reviewEl.innerHTML = rows.map(([label, value]) => `
    <div class="create-agent-review-item">
      <span class="create-agent-review-label">${safe(label)}</span>
      <div class="create-agent-review-value">${safe(value)}</div>
    </div>
  `).join("");
}

function setCreateAgentStep(form, step) {
  if (!form) return;
  const normalizedStep = CREATE_AGENT_STEPS[createAgentStepIndex(step)];
  const activeIndex = createAgentStepIndex(normalizedStep);
  state.createAgentStep = normalizedStep;
  form.dataset.currentStep = normalizedStep;
  form.querySelectorAll("[data-create-step-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.createStepPanel !== normalizedStep);
  });
  form.querySelectorAll("[data-create-step-indicator]").forEach((indicator) => {
    const index = createAgentStepIndex(indicator.dataset.createStepIndicator);
    indicator.classList.toggle("is-active", index === activeIndex);
    indicator.classList.toggle("is-complete", index < activeIndex);
    if (index === activeIndex) {
      indicator.setAttribute("aria-current", "step");
    } else {
      indicator.removeAttribute("aria-current");
    }
  });
  syncCreateRuntimeProfileState(form);
  const actions = form.querySelector(".create-agent-wizard-actions");
  actions?.classList.toggle("is-review", normalizedStep === "review");
  const backButton = form.querySelector("[data-create-back]");
  if (backButton) backButton.disabled = activeIndex === 0;
  if (normalizedStep === "review") renderCreateAgentReview(form, state.agentDefaults || {});
}

function validateCreateAgentStep(form) {
  const step = form?.dataset?.currentStep || "runtime";
  const msgEl = document.getElementById("create-msg");
  if (msgEl) {
    msgEl.textContent = "";
    setModalFeedback(msgEl, "", "");
  }
  if (step === "runtime") {
    const nameInput = form?.elements?.["name"];
    if (nameInput && !nameInput.checkValidity()) {
      nameInput.reportValidity();
      return false;
    }
  }
  if (step === "profile") {
    if (!(state.runtimeProfiles || []).length) {
      if (msgEl) {
        msgEl.textContent = "Create a runtime profile first.";
        setModalFeedback(msgEl, "error", msgEl.textContent);
      }
      return false;
    }
    if (!createAgentFieldValue(form, "runtime_profile_id")) {
      if (msgEl) {
        msgEl.textContent = "Choose a runtime profile.";
        setModalFeedback(msgEl, "error", msgEl.textContent);
      }
      return false;
    }
  }
  return true;
}

function moveCreateAgentStep(form, direction) {
  const currentIndex = createAgentStepIndex(form?.dataset?.currentStep || "runtime");
  if (direction > 0 && !validateCreateAgentStep(form)) return;
  const nextIndex = Math.max(0, Math.min(CREATE_AGENT_STEPS.length - 1, currentIndex + direction));
  setCreateAgentStep(form, CREATE_AGENT_STEPS[nextIndex]);
}

const EDIT_AGENT_STEPS = ["runtime", "profile", "instructions", "skills", "review"];

function editAgentStepIndex(step) {
  const index = EDIT_AGENT_STEPS.indexOf(step);
  return index >= 0 ? index : 0;
}

function editAgentFieldValue(form, name) {
  return createAgentFieldValue(form, name);
}

function editAgentRuntimeType(form, defaults) {
  const runtimeType = form?.dataset?.runtimeType || "native";
  return normalizeRuntimeTypeValue(runtimeType, defaults || state.agentDefaults || {});
}

function editAgentSelectedProfileLabel(form) {
  return createAgentSelectedProfileLabel(form);
}

function setEditAgentRepoFeedback(elementId, kind, message) {
  setCreateAgentRepoFeedback(elementId, kind, message);
}

async function refreshEditRepoBranches(kind) {
  const form = document.getElementById("edit-form");
  if (!form?.elements) return;
  const isAgentSettings = kind === "agent-settings";
  const repoField = isAgentSettings ? "agent_settings_repo_url" : "skill_repo_url";
  const branchField = isAgentSettings ? "agent_settings_branch" : "skill_branch";
  const selectId = isAgentSettings ? "edit-agent-settings-branch-select" : "edit-skill-branch-select";
  const feedbackId = isAgentSettings ? "edit-agent-settings-msg" : "edit-skills-msg";
  const defaultBranch = isAgentSettings ? state.agentDefaults?.default_agent_settings_branch : state.agentDefaults?.default_skill_branch;
  const repoUrl = editAgentFieldValue(form, repoField);
  if (!repoUrl) {
    populateBranchSelect(selectId, [], editAgentFieldValue(form, branchField), defaultBranch);
    setEditAgentRepoFeedback(feedbackId, "", "");
    return;
  }
  setEditAgentRepoFeedback(feedbackId, "", "Loading branches...");
  try {
    const branches = await loadGitRepoBranches(repoUrl);
    let selectedBranch = editAgentFieldValue(form, branchField);
    if (!selectedBranch && branches.length) {
      selectedBranch = branches.includes("master") ? "master" : (branches.includes("main") ? "main" : branches[0]);
    }
    populateBranchSelect(selectId, branches, selectedBranch, defaultBranch);
    setEditAgentRepoFeedback(feedbackId, branches.length ? "success" : "", branches.length ? `${branches.length} branches loaded.` : "No branches found.");
  } catch (error) {
    populateBranchSelect(selectId, [], editAgentFieldValue(form, branchField), defaultBranch);
    setEditAgentRepoFeedback(feedbackId, "error", error.message || "Failed to load branches.");
  }
}

function syncEditRuntimeProfileState(form) {
  const profiles = state.runtimeProfiles || [];
  const hasProfiles = profiles.length > 0;
  const select = form?.elements?.["runtime_profile_id"];
  if (select) select.disabled = !hasProfiles;
  const emptyEl = document.getElementById("edit-runtime-profile-empty");
  emptyEl?.classList.toggle("hidden", hasProfiles);
}

function editRuntimeTypeLabel(form, defaults) {
  const runtimeType = editAgentRuntimeType(form, defaults);
  const runtimeConfig = findRuntimeTypeConfig(defaults, runtimeType);
  const label = runtimeConfig?.label || runtimeType;
  return label === runtimeType ? runtimeType : `${label} (${runtimeType})`;
}

function updateEditRuntimeTypeDisplay(form, defaults) {
  const displayEl = document.getElementById("edit-runtime-type-display");
  if (!displayEl) return;
  displayEl.textContent = editRuntimeTypeLabel(form, defaults || state.agentDefaults || {});
}

function renderEditAgentReview(form, defaults) {
  const reviewEl = document.getElementById("edit-agent-review");
  if (!reviewEl) return;
  const runtimeType = editAgentRuntimeType(form, defaults);
  const runtimeConfig = findRuntimeTypeConfig(defaults, runtimeType);
  const rows = [
    ["Assistant Name", editAgentFieldValue(form, "name") || "Untitled"],
    ["Runtime Type", editRuntimeTypeLabel(form, defaults)],
    ["Runtime Image", runtimeImagePreview(runtimeConfig) || "Configured default"],
    ["Runtime Profile", editAgentSelectedProfileLabel(form) || "Not selected"],
    ["Instructions Repository", editAgentFieldValue(form, "agent_settings_repo_url") || "Configured default"],
    ["Instructions Branch", editAgentFieldValue(form, "agent_settings_branch") || "Configured default"],
    ["Skill Repository", editAgentFieldValue(form, "skill_repo_url") || "Configured default"],
    ["Skill Branch", editAgentFieldValue(form, "skill_branch") || "Configured default"],
  ];
  reviewEl.innerHTML = rows.map(([label, value]) => `
    <div class="create-agent-review-item">
      <span class="create-agent-review-label">${safe(label)}</span>
      <div class="create-agent-review-value">${safe(value)}</div>
    </div>
  `).join("");
}

function setEditAgentStep(form, step) {
  if (!form) return;
  const normalizedStep = EDIT_AGENT_STEPS[editAgentStepIndex(step)];
  const activeIndex = editAgentStepIndex(normalizedStep);
  form.dataset.currentStep = normalizedStep;
  form.querySelectorAll("[data-edit-step-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.editStepPanel !== normalizedStep);
  });
  form.querySelectorAll("[data-edit-step-indicator]").forEach((indicator) => {
    const index = editAgentStepIndex(indicator.dataset.editStepIndicator);
    indicator.classList.toggle("is-active", index === activeIndex);
    indicator.classList.toggle("is-complete", index < activeIndex);
    if (index === activeIndex) {
      indicator.setAttribute("aria-current", "step");
    } else {
      indicator.removeAttribute("aria-current");
    }
  });
  syncEditRuntimeProfileState(form);
  updateEditRuntimeTypeDisplay(form, state.agentDefaults || {});
  const actions = form.querySelector(".edit-agent-wizard-actions");
  actions?.classList.toggle("is-review", normalizedStep === "review");
  const backButton = form.querySelector("[data-edit-back]");
  if (backButton) backButton.disabled = activeIndex === 0;
  if (normalizedStep === "review") renderEditAgentReview(form, state.agentDefaults || {});
}

function validateEditAgentStep(form) {
  const step = form?.dataset?.currentStep || "runtime";
  const msgEl = document.getElementById("edit-msg");
  if (msgEl) {
    msgEl.textContent = "";
    setModalFeedback(msgEl, "", "");
  }
  if (step === "runtime") {
    const nameInput = form?.elements?.["name"];
    if (nameInput && !nameInput.checkValidity()) {
      nameInput.reportValidity();
      return false;
    }
  }
  if (step === "profile") {
    if (!(state.runtimeProfiles || []).length) {
      if (msgEl) {
        msgEl.textContent = "Create a runtime profile first.";
        setModalFeedback(msgEl, "error", msgEl.textContent);
      }
      return false;
    }
    if (!editAgentFieldValue(form, "runtime_profile_id")) {
      if (msgEl) {
        msgEl.textContent = "Choose a runtime profile.";
        setModalFeedback(msgEl, "error", msgEl.textContent);
      }
      return false;
    }
  }
  return true;
}

function moveEditAgentStep(form, direction) {
  const currentIndex = editAgentStepIndex(form?.dataset?.currentStep || "runtime");
  if (direction > 0 && !validateEditAgentStep(form)) return;
  const nextIndex = Math.max(0, Math.min(EDIT_AGENT_STEPS.length - 1, currentIndex + direction));
  setEditAgentStep(form, EDIT_AGENT_STEPS[nextIndex]);
}

function getRuntimeTypes(defaults) {
  const runtimeTypes = Array.isArray(defaults?.runtime_types) ? defaults.runtime_types : [];
  if (runtimeTypes.length) return runtimeTypes;
  return [{ value: "native", label: "EFP Native Runtime", image_repo: defaults?.image_repo || "", image_tag: defaults?.image_tag || "latest", default_mount_path: defaults?.mount_path || "/workspace" }];
}

function normalizeRuntimeTypeValue(value, defaults) {
  const raw = String(value || "").trim().toLowerCase();
  const runtimeTypes = getRuntimeTypes(defaults);
  if (runtimeTypes.some((item) => String(item?.value || "").trim().toLowerCase() === raw)) return raw;
  const fallback = String(defaults?.default_runtime_type || "native").trim().toLowerCase() || "native";
  if (runtimeTypes.some((item) => String(item?.value || "").trim().toLowerCase() === fallback)) return fallback;
  return String(runtimeTypes[0]?.value || "native").trim().toLowerCase() || "native";
}

function findRuntimeTypeConfig(defaults, runtimeType) {
  const normalized = normalizeRuntimeTypeValue(runtimeType, defaults);
  return getRuntimeTypes(defaults).find((item) => String(item?.value || "").trim().toLowerCase() === normalized) || getRuntimeTypes(defaults)[0] || null;
}

function runtimeImagePreview(config) {
  const repo = String(config?.image_repo || "").trim();
  const tag = String(config?.image_tag || "latest").trim() || "latest";
  return repo ? `${repo}:${tag}` : "";
}

function getCreateRuntimeTypes(defaults) {
  return getRuntimeTypes(defaults);
}

function getCreateDefaultRuntimeType(defaults) {
  return normalizeRuntimeTypeValue(defaults?.default_runtime_type || "native", defaults);
}

function runtimeTypeDescription(item) {
  const value = String(item?.value || "").trim().toLowerCase();
  if (value === "opencode") return "Use the opencode runtime adapter.";
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
  const settingsRepoInput = form.elements["agent_settings_repo_url"];
  if (settingsRepoInput) {
    const repoDefault = defaults?.default_agent_settings_repo_url || "";
    settingsRepoInput.value = repoDefault;
    settingsRepoInput.defaultValue = repoDefault;
  }
  const settingsBranchInput = form.elements["agent_settings_branch"];
  if (settingsBranchInput) {
    const branchDefault = defaults?.default_agent_settings_branch || "";
    settingsBranchInput.defaultValue = branchDefault;
    populateBranchSelect("create-agent-settings-branch-select", [], branchDefault, branchDefault);
  }
  const repoInput = form.elements["skill_repo_url"];
  if (repoInput) {
    const repoDefault = defaults?.default_skill_repo_url || "";
    repoInput.value = repoDefault;
    repoInput.defaultValue = repoDefault;
  }
  const branchInput = form.elements["skill_branch"];
  if (branchInput) {
    const branchDefault = defaults?.default_skill_branch || "";
    branchInput.defaultValue = branchDefault;
    populateBranchSelect("create-skill-branch-select", [], branchDefault, branchDefault);
  }
  const runtimeProfileSelect = form.elements["runtime_profile_id"];
  if (runtimeProfileSelect) {
    const defaultRuntimeProfileId = defaults?.default_runtime_profile_id || "";
    if (defaultRuntimeProfileId) runtimeProfileSelect.value = defaultRuntimeProfileId;
  }
  const runtimeTypeGroup = document.getElementById("create-runtime-type-select");
  populateRuntimeTypeRadioGroup(runtimeTypeGroup, defaults, getCreateDefaultRuntimeType(defaults));
  updateCreateRuntimeTypeHint(form, defaults);
  setCreateAgentStep(form, "runtime");
}

function populateRuntimeProfileSelect(selectEl, selectedId = '') {
  if (!selectEl) return;
  const profiles = state.runtimeProfiles || [];
  if (!profiles.length) {
    selectEl.innerHTML = '<option value="" disabled selected>No runtime profiles available</option>';
    selectEl.disabled = true;
    return;
  }
  selectEl.disabled = false;
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

async function loadDelegationRules() {
  try {
    const rules = await api("/api/delegation-rules");
    state.delegations = Array.isArray(rules) ? rules : [];
  } catch (_err) {
    state.delegations = [];
  }
  renderDelegationRuleNavList();
  return state.delegations;
}

function hasActiveDelegationFilters() {
  const filters = state.delegationFilters || {};
  return Boolean(filters.owner !== "all" || filters.source !== "all");
}

function delegationRuleMatchesFilters(rule) {
  const filters = state.delegationFilters || {};
  const source = String(rule?.source || rule?.trigger_type || "").trim();
  if (filters.owner === "mine" && Number(rule?.owner_user_id) !== state.currentUserId) return false;
  if (filters.source !== "all" && source !== filters.source) return false;
  return true;
}

function visibleDelegationRules() {
  return (Array.isArray(state.delegations) ? state.delegations : []).filter(delegationRuleMatchesFilters);
}

function syncDelegationFilterControls(visibleCount = null) {
  const filters = state.delegationFilters || { owner: "all", source: "all" };
  if (dom.delegationOwnerFilter && dom.delegationOwnerFilter.value !== filters.owner) dom.delegationOwnerFilter.value = filters.owner;
  if (dom.delegationSourceFilter && dom.delegationSourceFilter.value !== filters.source) dom.delegationSourceFilter.value = filters.source;
  if (dom.delegationFilterSummary) {
    const total = Array.isArray(state.delegations) ? state.delegations.length : 0;
    const shown = visibleCount === null ? visibleDelegationRules().length : visibleCount;
    const parts = [];
    if (filters.owner === "mine") parts.push("owned by you");
    if (filters.source !== "all") parts.push(delegationSourceLabel(filters.source));
    dom.delegationFilterSummary.textContent = parts.length ? `${shown} of ${total} shown - ${parts.join(", ")}` : `${total} delegations`;
  }
}

function renderDelegationRuleNavList(rules = null) {
  if (!dom.delegationRuleNavList) return;
  const items = Array.isArray(rules) ? rules : visibleDelegationRules();
  syncDelegationFilterControls(items.length);
  if (!items.length) {
    dom.delegationRuleNavList.innerHTML = `<div class="portal-bundle-list-state">${hasActiveDelegationFilters() ? "No delegations match these filters." : "No delegations found."}</div>`;
    return;
  }
  dom.delegationRuleNavList.innerHTML = "";
  items.forEach((rule) => {
    const source = rule.source || rule.trigger_type;
    const sourceLabel = delegationSourceLabel(source);
    const title = String(rule.name || sourceLabel || "Delegation").trim();
    const timeLabel = delegationDisplayTime(rule.created_at || rule.updated_at || rule.next_run_at || rule.last_run_at);
    const ownerLabel = delegationOwnerLabel(rule);
    const statusLabel = delegationRuleStatusLabel(rule);
    const statusDot = portalRowStatusDot(`Delegation status: ${statusLabel}`, delegationRuleStatusTone(rule));
    const row = document.createElement("button");
    row.type = "button";
    row.className = `portal-task-row portal-delegation-row${state.selectedDelegationRuleId === rule.id ? " is-active" : ""}`;
    row.innerHTML = `
      <div class="portal-task-row-head">
        <div class="portal-task-row-title">${safe(title)}</div>
        ${statusDot}
      </div>
      <div class="portal-task-row-meta">
        <span>Owner ${safe(ownerLabel)}</span>
        <span>${safe(timeLabel)}</span>
      </div>
    `;
    row.addEventListener("click", () => openDelegationRulePanel(rule.id));
    dom.delegationRuleNavList.append(row);
  });
}

const DELEGATION_SOURCE_OPTIONS = [
  ["github_pr_review", "GitHub PR Review"],
  ["github_pr_mention", "GitHub PR Mention"],
  ["jira_assignee", "Jira Assignee"],
  ["jira_mention", "Jira Mention"],
  ["timer", "Timer"],
];

function isDelegationTimerSource(source) {
  return String(source || "").trim() === "timer";
}

function delegationSourceLabel(source) {
  const normalized = String(source || "").trim();
  const found = DELEGATION_SOURCE_OPTIONS.find(([value]) => value === normalized);
  return found ? found[1] : normalized || "-";
}

function delegationProviderLabel(source) {
  const normalized = String(source || "").trim();
  if (normalized === "timer") return "Timer";
  if (normalized.startsWith("github_")) return "GitHub";
  if (normalized.startsWith("jira_")) return "Jira";
  return normalized || "-";
}

function delegationReplyTargetLabel(source) {
  if (isDelegationTimerSource(source)) return "No automatic reply";
  const provider = delegationProviderLabel(source);
  if (provider === "GitHub") return "PR comment";
  if (provider === "Jira") return "Jira comment";
  return "Reply";
}

function delegationInputLabel(source) {
  const normalized = String(source || "").trim();
  if (normalized === "github_pr_review") return "PR URL";
  if (normalized === "github_pr_mention") return "PR URL + comment";
  if (normalized === "jira_assignee") return "Jira URL";
  if (normalized === "jira_mention") return "Jira URL + comment";
  if (normalized === "timer") return "Cron schedule";
  return "Source payload";
}

function delegationRuleSkillLabel(rule) {
  const skillName = String(rule?.skill_name || "").trim().replace(/^\/+/, "");
  return skillName ? `/${skillName}` : "-";
}

function delegationRuleStatusLabel(rule) {
  if (rule?.target_agent_missing) return "Needs agent";
  return rule?.enabled ? "Enabled" : "Paused";
}

function delegationRuleStatusTone(rule) {
  if (rule?.target_agent_missing) return "warning";
  return rule?.enabled ? "success" : "neutral";
}

function delegationIntervalLabel(seconds) {
  const value = Number(seconds || 60);
  if (!Number.isFinite(value) || value <= 0) return "60s";
  if (value % 3600 === 0) return `${value / 3600}h`;
  if (value % 60 === 0) return `${value / 60}m`;
  return `${value}s`;
}

function delegationDisplayTime(value) {
  return formatTaskNavTime(value) || "-";
}

function delegationAgentLabel(agent, agentId) {
  const name = String(agent?.name || "").trim();
  const id = String(agentId || "").trim();
  if (name && id) return `${name} (${id})`;
  return name || id || "-";
}

function delegationOwnerLabel(rule) {
  const ownerName = String(rule?.owner_display_name || "").trim();
  if (ownerName) return ownerName;
  const ownerId = rule?.owner_user_id ?? "";
  return ownerId === "" || ownerId === null || ownerId === undefined ? "-" : `User ${ownerId}`;
}

function delegationCanManage(rule) {
  if (rule?.can_manage === true) return true;
  return Number(rule?.owner_user_id) === state.currentUserId;
}

function delegationTargetAgentLabel(rule, targetAgent = null) {
  const fallbackAgent = targetAgent || (rule?.target_agent_name ? { name: rule.target_agent_name } : null);
  const label = delegationAgentLabel(fallbackAgent, rule?.target_agent_id);
  return rule?.target_agent_missing ? `${label} (deleted)` : label;
}

function delegationProviderKey(source) {
  const normalized = String(source || "").trim();
  if (normalized === "timer") return "timer";
  if (normalized.startsWith("github_")) return "github";
  if (normalized.startsWith("jira_")) return "jira";
  return "";
}

function delegationCsv(value) {
  if (Array.isArray(value)) return value.join(", ");
  return String(value || "");
}

function delegationSplitCsv(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, arr) => arr.findIndex((candidate) => candidate.toLowerCase() === item.toLowerCase()) === index);
}

function delegationJsonAttr(value) {
  return escapeHtmlAttr(JSON.stringify(value || {}));
}

function collectDelegationSourceScope(formEl) {
  const source = String(formEl?.querySelector('[name="source"]')?.value || "").trim();
  const provider = delegationProviderKey(source);
  const scope = {};
  if (provider === "jira") {
    const jiraInstance = String(formEl.querySelector('[name="source_scope_jira_instance"]')?.value || "").trim();
    if (jiraInstance) scope.jira_instance = jiraInstance;
  }
  return scope;
}

function collectDelegationSourceConditions(formEl) {
  const source = String(formEl?.querySelector('[name="source"]')?.value || "").trim();
  const provider = delegationProviderKey(source);
  const conditions = {};
  if (provider === "github") {
    const repository = String(formEl.querySelector('[name="condition_repository"]')?.value || "").trim();
    const baseBranch = String(formEl.querySelector('[name="condition_base_branch"]')?.value || "").trim();
    const labelsInclude = delegationSplitCsv(formEl.querySelector('[name="condition_labels_include"]')?.value);
    const labelsExclude = delegationSplitCsv(formEl.querySelector('[name="condition_labels_exclude"]')?.value);
    const authorsInclude = delegationSplitCsv(formEl.querySelector('[name="condition_authors_include"]')?.value);
    const authorsExclude = delegationSplitCsv(formEl.querySelector('[name="condition_authors_exclude"]')?.value);
    if (repository) conditions.repository = repository;
    if (baseBranch) conditions.base_branch = baseBranch;
    if (labelsInclude.length) conditions.labels_include = labelsInclude;
    if (labelsExclude.length) conditions.labels_exclude = labelsExclude;
    if (authorsInclude.length) conditions.authors_include = authorsInclude;
    if (authorsExclude.length) conditions.authors_exclude = authorsExclude;
    const includeDrafts = formEl.querySelector('[name="condition_include_drafts"]');
    if (includeDrafts && !includeDrafts.checked) conditions.include_drafts = false;
  } else if (provider === "jira") {
    const projectKey = String(formEl.querySelector('[name="condition_project_key"]')?.value || "").trim();
    const issueType = String(formEl.querySelector('[name="condition_issue_type"]')?.value || "").trim();
    const statusInclude = delegationSplitCsv(formEl.querySelector('[name="condition_status_include"]')?.value);
    const statusExclude = delegationSplitCsv(formEl.querySelector('[name="condition_status_exclude"]')?.value);
    const priority = String(formEl.querySelector('[name="condition_priority"]')?.value || "").trim();
    const labelsInclude = delegationSplitCsv(formEl.querySelector('[name="condition_labels_include"]')?.value);
    const labelsExclude = delegationSplitCsv(formEl.querySelector('[name="condition_labels_exclude"]')?.value);
    if (projectKey) conditions.project_key = projectKey.toUpperCase();
    if (issueType) conditions.issue_type = issueType;
    if (statusInclude.length) conditions.status_include = statusInclude;
    if (statusExclude.length) conditions.status_exclude = statusExclude;
    if (priority) conditions.priority = priority;
    if (labelsInclude.length) conditions.labels_include = labelsInclude;
    if (labelsExclude.length) conditions.labels_exclude = labelsExclude;
  }
  return conditions;
}

function delegationConditionSummaryLabel(source, scope = {}, conditions = {}) {
  const provider = delegationProviderKey(source);
  if (provider === "timer") return "Scheduled by Portal timer";
  const parts = [];
  if (provider === "github") {
    if (conditions.repository) parts.push(`repo ${conditions.repository}`);
    if (conditions.base_branch) parts.push(`base ${conditions.base_branch}`);
    if (Array.isArray(conditions.labels_include) && conditions.labels_include.length) parts.push(`labels +${conditions.labels_include.join(", ")}`);
    if (Array.isArray(conditions.labels_exclude) && conditions.labels_exclude.length) parts.push(`labels -${conditions.labels_exclude.join(", ")}`);
    if (Array.isArray(conditions.authors_include) && conditions.authors_include.length) parts.push(`authors ${conditions.authors_include.join(", ")}`);
    if (Array.isArray(conditions.authors_exclude) && conditions.authors_exclude.length) parts.push(`exclude authors ${conditions.authors_exclude.join(", ")}`);
    if (conditions.include_drafts === false) parts.push("no drafts");
  } else if (provider === "jira") {
    if (scope.jira_instance) parts.push(`instance ${scope.jira_instance}`);
    if (conditions.project_key) parts.push(`project ${conditions.project_key}`);
    if (conditions.issue_type) parts.push(`type ${conditions.issue_type}`);
    if (Array.isArray(conditions.status_include) && conditions.status_include.length) parts.push(`status ${conditions.status_include.join(", ")}`);
    if (Array.isArray(conditions.status_exclude) && conditions.status_exclude.length) parts.push(`exclude status ${conditions.status_exclude.join(", ")}`);
    if (conditions.priority) parts.push(`priority ${conditions.priority}`);
    if (Array.isArray(conditions.labels_include) && conditions.labels_include.length) parts.push(`labels +${conditions.labels_include.join(", ")}`);
    if (Array.isArray(conditions.labels_exclude) && conditions.labels_exclude.length) parts.push(`labels -${conditions.labels_exclude.join(", ")}`);
  }
  return parts.length ? parts.join(" · ") : "All runtime profile source items";
}

function delegationJiraInstanceSelectHtml(scope = {}, preview = null) {
  const selected = String(scope.jira_instance || "").trim();
  const options = Array.isArray(preview?.options?.jira_instances) ? preview.options.jira_instances : [];
  if (!options.length) {
    const value = selected || "";
    const label = selected || "Default Jira instance";
    return `<select class="portal-form-select" name="source_scope_jira_instance"><option value="${escapeHtmlAttr(value)}" selected>${safe(label)}</option></select>`;
  }
  const selectedMatched = selected && options.some((option) => String(option.value || "") === selected);
  const optionHtml = options.map((option, index) => {
    const value = String(option.value || "").trim();
    const isSelected = selected ? value === selected : index === 0;
    return `<option value="${escapeHtmlAttr(value)}" ${isSelected ? "selected" : ""}>${safe(option.label || value)}</option>`;
  }).join("");
  const staleOption = selected && !selectedMatched ? `<option value="${escapeHtmlAttr(selected)}" selected>${safe(selected)} (missing)</option>` : "";
  return `<select class="portal-form-select" name="source_scope_jira_instance">${staleOption}${optionHtml}</select>`;
}

function delegationSourceConditionFieldsHtml(source, scope = {}, conditions = {}, preview = null) {
  const provider = delegationProviderKey(source);
  if (provider === "timer") return "";
  if (provider === "github") {
    const includeDraftsChecked = conditions.include_drafts === false ? "" : "checked";
    return `
      <div class="portal-panel-grid cols-2">
        <label class="portal-form-label"><span class="portal-form-label">Repository</span><input class="portal-form-input" name="condition_repository" value="${escapeHtmlAttr(conditions.repository || "")}" placeholder="owner/repo" /></label>
        <label class="portal-form-label"><span class="portal-form-label">Base branch</span><input class="portal-form-input" name="condition_base_branch" value="${escapeHtmlAttr(conditions.base_branch || "")}" placeholder="main" /></label>
        <label class="portal-form-label"><span class="portal-form-label">Include labels</span><input class="portal-form-input" name="condition_labels_include" value="${escapeHtmlAttr(delegationCsv(conditions.labels_include))}" placeholder="bug, backend" /></label>
        <label class="portal-form-label"><span class="portal-form-label">Exclude labels</span><input class="portal-form-input" name="condition_labels_exclude" value="${escapeHtmlAttr(delegationCsv(conditions.labels_exclude))}" placeholder="wip" /></label>
      </div>
      <details class="portal-collapsible portal-delegation-advanced">
        <summary class="portal-collapsible-summary"><span>More conditions</span><i data-lucide="chevron-down" class="w-4 h-4"></i></summary>
        <div class="portal-panel-grid cols-2 portal-delegation-advanced-body">
          <label class="portal-form-label"><span class="portal-form-label">PR authors</span><input class="portal-form-input" name="condition_authors_include" value="${escapeHtmlAttr(delegationCsv(conditions.authors_include))}" placeholder="octocat" /></label>
          <label class="portal-form-label"><span class="portal-form-label">Exclude authors</span><input class="portal-form-input" name="condition_authors_exclude" value="${escapeHtmlAttr(delegationCsv(conditions.authors_exclude))}" placeholder="bot-user" /></label>
          <label class="portal-toggle-field portal-delegation-toggle-field"><span>Include draft PRs</span><span class="toggle-switch" aria-label="Include draft PRs"><input type="checkbox" name="condition_include_drafts" ${includeDraftsChecked} /><span class="toggle-slider"></span></span></label>
        </div>
      </details>
    `;
  }
  if (provider === "jira") {
    return `
      <div class="portal-panel-grid cols-2">
        <label class="portal-form-label"><span class="portal-form-label">Jira instance</span>${delegationJiraInstanceSelectHtml(scope, preview)}</label>
        <label class="portal-form-label"><span class="portal-form-label">Project key</span><input class="portal-form-input" name="condition_project_key" value="${escapeHtmlAttr(conditions.project_key || "")}" placeholder="EFP" /></label>
        <label class="portal-form-label"><span class="portal-form-label">Issue type</span><input class="portal-form-input" name="condition_issue_type" value="${escapeHtmlAttr(conditions.issue_type || "")}" placeholder="Bug" /></label>
        <label class="portal-form-label"><span class="portal-form-label">Include statuses</span><input class="portal-form-input" name="condition_status_include" value="${escapeHtmlAttr(delegationCsv(conditions.status_include))}" placeholder="To Do, In Progress" /></label>
        <label class="portal-form-label"><span class="portal-form-label">Exclude statuses</span><input class="portal-form-input" name="condition_status_exclude" value="${escapeHtmlAttr(delegationCsv(conditions.status_exclude))}" placeholder="Done" /></label>
      </div>
      <details class="portal-collapsible portal-delegation-advanced">
        <summary class="portal-collapsible-summary"><span>More conditions</span><i data-lucide="chevron-down" class="w-4 h-4"></i></summary>
        <div class="portal-panel-grid cols-2 portal-delegation-advanced-body">
          <label class="portal-form-label"><span class="portal-form-label">Priority</span><input class="portal-form-input" name="condition_priority" value="${escapeHtmlAttr(conditions.priority || "")}" placeholder="High" /></label>
          <label class="portal-form-label"><span class="portal-form-label">Include labels</span><input class="portal-form-input" name="condition_labels_include" value="${escapeHtmlAttr(delegationCsv(conditions.labels_include))}" placeholder="support" /></label>
          <label class="portal-form-label"><span class="portal-form-label">Exclude labels</span><input class="portal-form-input" name="condition_labels_exclude" value="${escapeHtmlAttr(delegationCsv(conditions.labels_exclude))}" placeholder="blocked" /></label>
        </div>
      </details>
    `;
  }
  return `<div class="portal-inline-state is-visible">Select a source first.</div>`;
}

function renderDelegationSourceControls(formEl, scope = {}, conditions = {}, preview = null, { loading = false } = {}) {
  const container = formEl?.querySelector("[data-delegation-source-controls]");
  if (!container) return;
  const source = String(formEl.querySelector('[name="source"]')?.value || "").trim();
  const providerKey = delegationProviderKey(source);
  const providerLabel = delegationProviderLabel(source);
  const accountSummary = preview?.account_summary || (providerKey === "timer" ? "Portal timer" : `${providerLabel} from selected agent runtime profile`);
  const conditionSummary = delegationConditionSummaryLabel(source, scope, conditions);
  const status = String(preview?.status || (loading ? "loading" : "ok")).trim();
  const statusTone = status === "missing" ? "warning" : (loading ? "info" : "success");
  const statusLabel = status === "missing" ? "Needs source" : (loading ? "Checking" : "Ready");
  const warning = String(preview?.warning || "").trim();
  const conditionBuilder = providerKey === "timer" ? "" : `
      <div class="portal-delegation-condition-builder">
        <div class="portal-task-section-heading">
          <span>Source Conditions</span>
          <span>${safe(providerLabel)}</span>
        </div>
        ${delegationSourceConditionFieldsHtml(source, scope, conditions, preview)}
      </div>
  `;
  container.innerHTML = `
    <section class="portal-delegation-source-config">
      <div class="portal-delegation-source-account">
        <div>
          <span>${providerKey === "timer" ? "Source" : "Runtime source"}</span>
          <strong>${safe(accountSummary)}</strong>
          <small>${safe(conditionSummary)}</small>
        </div>
        <span class="portal-status-badge is-${safe(statusTone)}">${safe(statusLabel)}</span>
      </div>
      ${warning ? `<div class="portal-callout is-warning">${safe(warning)}</div>` : ""}
      ${conditionBuilder}
    </section>
  `;
  renderIcons();
}

async function refreshDelegationSourcePreview(formEl, { resetScope = false, resetConditions = false } = {}) {
  if (!formEl) return;
  const source = String(formEl.querySelector('[name="source"]')?.value || "").trim();
  const agentId = String(formEl.querySelector('[name="target_agent_id"]')?.value || "").trim();
  const scope = resetScope ? {} : collectDelegationSourceScope(formEl);
  const conditions = resetConditions ? {} : collectDelegationSourceConditions(formEl);
  renderDelegationSourceControls(formEl, scope, conditions, null, { loading: true });
  if (!source || !agentId) return;
  try {
    const params = new URLSearchParams({ target_agent_id: agentId, source });
    if (scope.jira_instance) params.set("jira_instance", scope.jira_instance);
    const preview = await api(`/api/delegation-rules/source-preview?${params.toString()}`);
    const nextScope = resetScope && preview?.options?.jira_instances?.[0]?.value
      ? { jira_instance: preview.options.jira_instances[0].value }
      : scope;
    renderDelegationSourceControls(formEl, nextScope, conditions, preview);
  } catch (error) {
    renderDelegationSourceControls(
      formEl,
      scope,
      conditions,
      {
        account_summary: delegationProviderKey(source) === "timer" ? "Portal timer" : `${delegationProviderLabel(source)} from selected agent runtime profile`,
        status: "missing",
        warning: error.message,
        options: {},
      },
    );
  }
}

function defaultDelegationTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch (error) {
    return "UTC";
  }
}

function collectDelegationTaskPrompt(formEl) {
  return String(formEl?.querySelector('[name="task_prompt"]')?.value || "").trim();
}

function collectDelegationSchedule(formEl) {
  const source = String(formEl?.querySelector('[name="source"]')?.value || "").trim();
  if (isDelegationTimerSource(source)) {
    const expression = String(formEl.querySelector('[name="schedule_cron_expression"]')?.value || "").trim();
    const timezone = String(formEl.querySelector('[name="schedule_timezone"]')?.value || "").trim() || defaultDelegationTimezone();
    const skipOverlapping = formEl.querySelector('[name="schedule_skip_overlapping"]')?.checked !== false;
    return {
      type: "cron",
      expression,
      timezone,
      misfire_policy: "fire_once",
      catchup: false,
      overlap_policy: skipOverlapping ? "skip_if_running" : "allow",
    };
  }
  const intervalSeconds = Number(formEl?.querySelector('[name="interval_seconds"]')?.value || 60);
  return { type: "interval", interval_seconds: Number.isFinite(intervalSeconds) && intervalSeconds > 0 ? intervalSeconds : 60 };
}

function renderDelegationSchedulePreview(formEl, preview = null, { loading = false, error = "" } = {}) {
  const container = formEl?.querySelector("[data-delegation-schedule-preview]");
  if (!container) return;
  if (loading) {
    container.className = "portal-inline-state is-visible";
    container.textContent = "Checking schedule...";
    return;
  }
  if (error || preview?.valid === false) {
    container.className = "portal-inline-state is-error";
    container.textContent = error || preview?.error || "Invalid schedule";
    return;
  }
  if (preview?.valid) {
    const nextRun = preview.next_run_local || preview.next_run_at || "-";
    container.className = "portal-inline-state is-success";
    container.textContent = `${preview.summary || "Schedule ready"} · Next ${nextRun}`;
    return;
  }
  container.className = "portal-inline-state is-visible";
  container.textContent = "";
}

async function refreshDelegationSchedulePreview(formEl, { debounce = false } = {}) {
  if (!formEl || !isDelegationTimerSource(formEl.querySelector('[name="source"]')?.value)) return;
  if (debounce) {
    window.clearTimeout(formEl._delegationSchedulePreviewTimer);
    formEl._delegationSchedulePreviewTimer = window.setTimeout(() => {
      refreshDelegationSchedulePreview(formEl).catch(() => {});
    }, 250);
    return;
  }
  const schedule = collectDelegationSchedule(formEl);
  if (!schedule.expression) {
    renderDelegationSchedulePreview(formEl, null, { error: "Cron expression is required" });
    return;
  }
  const requestId = `${Date.now()}:${Math.random()}`;
  formEl.dataset.delegationSchedulePreviewRequest = requestId;
  renderDelegationSchedulePreview(formEl, null, { loading: true });
  try {
    const preview = await api("/api/delegation-rules/schedule-preview", {
      method: "POST",
      body: JSON.stringify({ schedule }),
    });
    if (formEl.dataset.delegationSchedulePreviewRequest !== requestId) return;
    renderDelegationSchedulePreview(formEl, preview);
  } catch (err) {
    if (formEl.dataset.delegationSchedulePreviewRequest !== requestId) return;
    renderDelegationSchedulePreview(formEl, null, { error: err.message });
  }
}

function renderDelegationScheduleControls(formEl, schedule = {}, taskPrompt = "") {
  const container = formEl?.querySelector("[data-delegation-schedule-controls]");
  if (!container) return;
  const source = String(formEl.querySelector('[name="source"]')?.value || "").trim();
  const intervalField = formEl.querySelector("[data-delegation-interval-field]");
  const intervalInput = intervalField?.querySelector('[name="interval_seconds"]');
  if (!isDelegationTimerSource(source)) {
    if (intervalField) intervalField.classList.remove("hidden");
    if (intervalInput) {
      intervalInput.disabled = false;
      intervalInput.required = true;
    }
    container.innerHTML = "";
    return;
  }
  if (intervalField) intervalField.classList.add("hidden");
  if (intervalInput) {
    intervalInput.disabled = true;
    intervalInput.required = false;
  }
  const cronSchedule = schedule && schedule.type === "cron" ? schedule : {};
  const expression = String(cronSchedule.expression || "0 9 * * 1-5").trim();
  const timezone = String(cronSchedule.timezone || defaultDelegationTimezone()).trim();
  const skipOverlapping = cronSchedule.overlap_policy !== "allow";
  container.innerHTML = `
    <section class="portal-delegation-source-config" data-delegation-timer-config>
      <div class="portal-task-section-heading">
        <span>Timer</span>
        <span>Cron</span>
      </div>
      <label class="portal-form-label"><span class="portal-form-label">Task prompt</span><textarea class="portal-form-textarea" name="task_prompt" rows="5" required>${safe(taskPrompt)}</textarea></label>
      <div class="portal-panel-grid cols-2">
        <label class="portal-form-label"><span class="portal-form-label">Cron expression</span><input class="portal-form-input" name="schedule_cron_expression" value="${escapeHtmlAttr(expression)}" placeholder="30 9 * * 1-5" required /></label>
        <label class="portal-form-label"><span class="portal-form-label">Timezone</span><input class="portal-form-input" name="schedule_timezone" value="${escapeHtmlAttr(timezone)}" placeholder="Asia/Shanghai" required /></label>
      </div>
      <label class="portal-toggle-field portal-delegation-toggle-field"><span>Skip overlapping runs</span><span class="toggle-switch" aria-label="Skip overlapping runs"><input type="checkbox" name="schedule_skip_overlapping" ${skipOverlapping ? "checked" : ""} /><span class="toggle-slider"></span></span></label>
      <div data-delegation-schedule-preview class="portal-inline-state is-visible" aria-live="polite"></div>
    </section>
  `;
  renderIcons();
}

async function setupDelegationSourceForm(formEl, sourceScope = {}, sourceConditions = {}, schedule = {}, taskPrompt = "") {
  renderDelegationScheduleControls(formEl, schedule || {}, taskPrompt || "");
  renderDelegationSourceControls(formEl, sourceScope || {}, sourceConditions || {});
  await refreshDelegationSourcePreview(formEl);
  await refreshDelegationSchedulePreview(formEl);
}

function delegationRunStatusTone(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "success") return "success";
  if (normalized === "partial") return "warning";
  if (normalized === "failed") return "error";
  return "info";
}

function delegationEventStatusTone(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "reply_sent") return "success";
  if (["reply_failed", "failed"].includes(normalized)) return "error";
  if (["creating_task", "task_created"].includes(normalized)) return "warning";
  return "info";
}

function delegationEventSourceLabel(event) {
  const normalized = _safeJson(event?.normalized_payload_json) || {};
  return delegationSourceLabel(normalized.source || normalized.provider || "");
}

function delegationEventSourceLink(event) {
  const normalized = _safeJson(event?.normalized_payload_json) || {};
  const url = String(normalized.source_url || "").trim();
  if (!url) return safe(delegationEventSourceLabel(event));
  return `<a class="portal-delegation-source-link" href="${escapeHtmlAttr(url)}" target="_blank" rel="noopener">${safe(delegationEventSourceLabel(event))}</a>`;
}

function delegationEventReplyLabel(event) {
  const normalized = _safeJson(event?.normalized_payload_json) || {};
  const statusText = String(event?.status || "").trim();
  if (isDelegationTimerSource(normalized.source || normalized.provider || "")) {
    if (statusText === "reply_sent") return "Completed";
    if (statusText === "reply_failed") return "Completion failed";
    if (statusText === "task_created" || statusText === "task_done") return "Task pending";
    return "-";
  }
  if (statusText === "reply_sent") return "Reply sent";
  if (statusText === "reply_failed") return "Reply failed";
  if (statusText === "task_created" || statusText === "task_done") return "Reply pending";
  return "-";
}

function delegationTruncate(value, maxLength = 160) {
  return compactText(value, maxLength);
}

function delegationRunTimelineItems(runs) {
  const items = Array.isArray(runs) ? runs : [];
  if (!items.length) return '<div class="portal-inline-state is-visible">No runs yet.</div>';
  return items.slice(0, 8).map((run) => {
    const status = String(run.status || "unknown").trim();
    const tone = delegationRunStatusTone(status);
    const started = delegationDisplayTime(run.started_at);
    const finished = delegationDisplayTime(run.finished_at);
    const error = String(run.error_message || "").trim();
    return `
      <article class="portal-delegation-timeline-item">
        <div class="portal-delegation-timeline-main">
          <div class="portal-delegation-timeline-head">
            <span class="portal-status-badge is-${safe(tone)}">${safe(status)}</span>
            <span class="portal-panel-note">${safe(started)}</span>
          </div>
          <strong>Found ${safe(String(run.found_count ?? 0))} · Created ${safe(String(run.created_task_count ?? 0))} · Skipped ${safe(String(run.skipped_count ?? 0))}</strong>
          <div class="portal-delegation-timeline-meta">Finished ${safe(finished)}</div>
          ${error ? `<div class="portal-callout is-error">${safe(error)}</div>` : ""}
        </div>
      </article>
    `;
  }).join("");
}

function delegationEventTimelineItems(events) {
  const items = Array.isArray(events) ? events : [];
  if (!items.length) return '<div class="portal-inline-state is-visible">No tasks or replies yet.</div>';
  return items.slice(0, 12).map((event) => {
    const normalized = _safeJson(event?.normalized_payload_json) || {};
    const status = String(event.status || "unknown").trim();
    const tone = delegationEventStatusTone(status);
    const replyLabel = delegationEventReplyLabel(event);
    const replyTone = ["Reply sent", "Completed"].includes(replyLabel)
      ? "success"
      : (["Reply failed", "Completion failed"].includes(replyLabel) ? "error" : "neutral");
    const comment = delegationTruncate(normalized.source_comment || "", 180);
    const identity = String(normalized.represented_identity || "").trim();
    const taskId = String(event.task_id || "").trim();
    const error = String(event.error_message || "").trim();
    return `
      <article class="portal-delegation-timeline-item">
        <div class="portal-delegation-timeline-main">
          <div class="portal-delegation-timeline-head">
            <span class="portal-status-badge is-${safe(tone)}">${safe(status)}</span>
            <span class="portal-status-badge is-${safe(replyTone)}">${safe(replyLabel)}</span>
          </div>
          <strong>${delegationEventSourceLink(event)}</strong>
          <div class="portal-delegation-timeline-meta">
            <span>${safe(delegationDisplayTime(event.created_at))}</span>
            ${identity ? `<span>As ${safe(identity)}</span>` : ""}
          </div>
          ${comment ? `<div class="portal-delegation-comment">${safe(comment)}</div>` : ""}
          ${error ? `<div class="portal-callout is-error">${safe(error)}</div>` : ""}
        </div>
        <div class="portal-delegation-timeline-actions">
          ${taskId ? `<button class="portal-btn is-secondary" type="button" data-open-task-main="${escapeHtmlAttr(taskId)}"><i data-lucide="external-link" class="w-4 h-4"></i>Task</button>` : `<span class="portal-panel-note">No task</span>`}
        </div>
      </article>
    `;
  }).join("");
}

async function openDelegationRulePanel(ruleId, { updateRoute = true } = {}) {
  if (!ruleId) return;
  try {
    state.selectedDelegationRuleId = ruleId;
    renderDelegationRuleNavList();
    const detail = await api(`/api/delegation-rules/${encodeURIComponent(ruleId)}`);
    const runs = await loadDelegationRuleRuns(ruleId);
    const events = await loadDelegationRuleEvents(ruleId);
    const targetAgent = (state.mineAgents || []).find((item) => item.id === detail.target_agent_id);
    const source = detail.source || detail.trigger_type;
    const sourceLabel = delegationSourceLabel(source);
    const providerLabel = delegationProviderLabel(source);
    const intervalLabel = delegationIntervalLabel(detail.interval_seconds || 60);
    const scheduleLabel = String(detail.schedule_summary || "").trim() || `Every ${intervalLabel}`;
    const skillLabel = delegationRuleSkillLabel(detail);
    const statusLabel = delegationRuleStatusLabel(detail);
    const statusTone = delegationRuleStatusTone(detail);
    const agentLabel = delegationTargetAgentLabel(detail, targetAgent);
    const ownerLabel = delegationOwnerLabel(detail);
    const canManage = delegationCanManage(detail);
    const accountSummary = String(detail.source_account_summary || `${providerLabel} from selected agent runtime profile`).trim();
    const conditionSummary = String(detail.source_condition_summary || "All runtime profile source items").trim();
    const sourceWarning = String(detail.source_config_warning || "").trim();
    const sourceConfigTone = detail.source_config_status === "missing" ? "warning" : "success";
    const sourceConfigLabel = detail.source_config_status === "missing" ? "Needs source" : "Ready";
    const managementActions = canManage ? `
            <button class="portal-btn is-secondary" type="button" data-edit-delegation-rule="${escapeHtmlAttr(detail.id)}"><i data-lucide="pencil" class="w-4 h-4"></i>Edit</button>
            <button class="portal-btn is-secondary" type="button" data-run-delegation-once="${escapeHtmlAttr(detail.id)}"><i data-lucide="play" class="w-4 h-4"></i>Run once</button>
            <button class="portal-btn is-secondary" type="button" data-toggle-delegation-enabled="${escapeHtmlAttr(detail.id)}" data-next-enabled="${detail.enabled ? "false" : "true"}"><i data-lucide="${detail.enabled ? "pause" : "play"}" class="w-4 h-4"></i>${detail.enabled ? "Pause" : "Enable"}</button>
            <button class="portal-btn is-danger" type="button" data-delete-delegation-rule="${escapeHtmlAttr(detail.id)}"><i data-lucide="trash-2" class="w-4 h-4"></i>Delete</button>
    ` : '<span class="portal-panel-note">Only the owner can manage this delegation.</span>';
    const missingAgentCallout = detail.target_agent_missing ? `
        <div class="portal-callout is-warning portal-repair-callout">
          <div>
            <strong>Target agent was deleted.</strong>
            <span>${canManage ? "Choose a replacement agent to resume scheduled runs." : `Ask ${safe(ownerLabel)} to choose a replacement agent.`}</span>
          </div>
          ${canManage ? `<button class="portal-btn is-secondary" type="button" data-edit-delegation-rule="${escapeHtmlAttr(detail.id)}"><i data-lucide="wrench" class="w-4 h-4"></i>Change agent</button>` : ""}
        </div>
    ` : "";
    dom.workspaceDetailContent.innerHTML = `
      <div class="portal-panel-stack portal-delegation-detail">
        <div class="portal-task-detail-hero portal-delegation-hero">
          <div class="portal-task-detail-title">
            <span class="portal-status-badge is-${safe(statusTone)}">${safe(statusLabel)}</span>
            <div>
              <h2 class="portal-panel-title">${safe(detail.name || sourceLabel)}</h2>
              <p class="portal-panel-subtitle">${safe(sourceLabel)} · ${safe(agentLabel)} · ${safe(skillLabel)}</p>
            </div>
          </div>
          <div class="portal-task-actions">
            ${managementActions}
          </div>
        </div>

        ${missingAgentCallout}

        <section class="portal-task-metrics portal-delegation-metrics">
          <div><span>Source</span><strong>${safe(sourceLabel)}</strong></div>
          <div><span>Owner</span><strong>${safe(ownerLabel)}</strong></div>
          <div><span>Schedule</span><strong>${safe(scheduleLabel)}</strong></div>
          <div><span>Last Run</span><strong>${safe(delegationDisplayTime(detail.last_run_at))}</strong></div>
          <div><span>Next Run</span><strong>${safe(delegationDisplayTime(detail.next_run_at))}</strong></div>
        </section>

        <section class="portal-delegation-source-summary">
          <article class="portal-delegation-source-summary-item">
            <div>
              <span>${isDelegationTimerSource(source) ? "Source" : "Runtime Source"}</span>
              <strong>${safe(accountSummary)}</strong>
              ${sourceWarning ? `<small>${safe(sourceWarning)}</small>` : ""}
            </div>
            <span class="portal-status-badge is-${safe(sourceConfigTone)}">${safe(sourceConfigLabel)}</span>
          </article>
          <article class="portal-delegation-source-summary-item">
            <div>
              <span>Conditions</span>
              <strong>${safe(conditionSummary)}</strong>
              <small>${safe(sourceLabel)}</small>
            </div>
          </article>
        </section>

        <section class="portal-delegation-flow" aria-label="Delegation flow">
          <div class="portal-delegation-flow-step">
            <span>Trigger</span>
            <strong>${safe(sourceLabel)}</strong>
            <small>${safe(providerLabel)} · ${safe(delegationInputLabel(source))}</small>
          </div>
          <div class="portal-delegation-flow-step">
            <span>Task</span>
            <strong>${safe(skillLabel)}</strong>
            <small>${safe(agentLabel)}</small>
          </div>
          <div class="portal-delegation-flow-step">
            <span>Reply</span>
            <strong>${safe(delegationReplyTargetLabel(source))}</strong>
            <small>${safe(providerLabel)}</small>
          </div>
        </section>

        <div class="portal-task-detail-grid portal-delegation-detail-grid">
          <section class="portal-task-content-panel">
            <div class="portal-task-section-heading">
              <span>Recent Runs</span>
              <span>${safe(String((runs || []).length))}</span>
            </div>
            <div class="portal-delegation-timeline-list">${delegationRunTimelineItems(runs)}</div>
          </section>
          <section class="portal-task-content-panel">
            <div class="portal-task-section-heading">
              <span>Tasks / Replies</span>
              <span>${safe(String((events || []).length))}</span>
            </div>
            <div class="portal-delegation-timeline-list">${delegationEventTimelineItems(events)}</div>
          </section>
        </div>
      </div>
    `;
    renderIcons();
    setMainView("detail");
    dom.workspaceDetailContent.dataset.workspaceState = "delegation-rule-detail";
    if (updateRoute && !isApplyingPortalRoute) {
      commitPortalRoute({ section: "delegations", delegationRuleId: ruleId });
    }
  } catch (error) {
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed to load delegation: ${safe(error.message)}</div>`;
  }
}

async function openCreateDelegationRuleModal() {
  const mineAgents = state.mineAgents || [];
  if (!mineAgents.length) {
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">No agents available. Create or enable an agent first.</div>`;
    return;
  }
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "delegation-rule-create";
  const agentOptions = mineAgents
    .map((agent) => `<option value="${escapeHtmlAttr(agent.id)}">${safe(agent.name || agent.id)}</option>`)
    .join("");
  const sourceOptions = DELEGATION_SOURCE_OPTIONS
    .map(([value, label], index) => `<option value="${escapeHtmlAttr(value)}" ${index === 0 ? "selected" : ""}>${safe(label)}</option>`)
    .join("");
  dom.workspaceDetailContent.innerHTML = `
    <div class="modal portal-workspace-wizard-modal" role="dialog" aria-modal="true" aria-labelledby="create-delegation-modal-title" aria-hidden="false">
      <div class="modal-card panel create-agent-wizard-card">
        <div class="portal-modal-titlebar">
          <div>
            <h3 id="create-delegation-modal-title">Create Delegation</h3>
            <p class="portal-modal-copy">Route matching work into a writable agent.</p>
          </div>
          <button class="portal-modal-close" type="button" title="Close" aria-label="Close" data-close-delegation-create-modal>✕</button>
        </div>
        <form
          id="create-delegation-inline-form"
          class="stack portal-step-wizard"
          data-wizard-steps="basics,source,work,review"
          data-current-step="basics"
        >
          <ol class="create-agent-steps" aria-label="Create delegation steps" style="--portal-step-count: 4">
            <li class="create-agent-step is-active" data-wizard-step-indicator="basics">
              <span class="create-agent-step-index">1</span>
              <span class="create-agent-step-label">Basics</span>
            </li>
            <li class="create-agent-step" data-wizard-step-indicator="source">
              <span class="create-agent-step-index">2</span>
              <span class="create-agent-step-label">Source</span>
            </li>
            <li class="create-agent-step" data-wizard-step-indicator="work">
              <span class="create-agent-step-index">3</span>
              <span class="create-agent-step-label">Work</span>
            </li>
            <li class="create-agent-step" data-wizard-step-indicator="review">
              <span class="create-agent-step-index">4</span>
              <span class="create-agent-step-label">Review</span>
            </li>
          </ol>
          <section class="create-agent-step-panel" data-wizard-step-panel="basics">
            <label class="portal-form-label"><span class="portal-form-label">Name</span><input class="portal-form-input" name="name" required /></label>
            <label class="portal-form-label"><span class="portal-form-label">Agent</span><select class="portal-form-select" name="target_agent_id" required>${agentOptions}</select></label>
            <label class="portal-toggle-field"><span>Enabled</span><span class="toggle-switch" aria-label="Enabled"><input type="checkbox" name="enabled" checked /><span class="toggle-slider"></span></span></label>
          </section>
          <section class="create-agent-step-panel hidden" data-wizard-step-panel="source">
            <label class="portal-form-label"><span class="portal-form-label">Source</span><select class="portal-form-select" name="source" required>${sourceOptions}</select></label>
            <div data-delegation-source-controls></div>
          </section>
          <section class="create-agent-step-panel hidden" data-wizard-step-panel="work">
            <div data-delegation-schedule-controls></div>
            <label class="portal-form-label"><span class="portal-form-label">Skill</span><select class="portal-form-select" name="skill_name" required disabled><option value="">Select an agent first</option></select></label>
            <label class="portal-form-label" data-delegation-interval-field><span class="portal-form-label">Interval seconds</span><input class="portal-form-input" name="interval_seconds" type="number" value="60" min="1" required /></label>
          </section>
          <section class="create-agent-step-panel hidden" data-wizard-step-panel="review">
            <div class="create-agent-review-grid" data-delegation-review></div>
          </section>
          <div class="portal-modal-actions portal-task-form-actions portal-step-wizard-actions">
            <button class="portal-btn is-secondary" type="button" data-wizard-back>Back</button>
            <button class="portal-btn is-primary" type="button" data-wizard-next>Next</button>
            <button class="portal-btn is-primary" type="submit" data-wizard-submit>Create Delegation</button>
          </div>
        </form>
      </div>
    </div>
  `;
  const form = document.getElementById("create-delegation-inline-form");
  if (form) {
    await setupDelegationSourceForm(form);
    await populateCreateTaskSkillSelect(form);
    initializeInlineWizard(form);
  }
}

async function openEditDelegationRuleModal(ruleId) {
  const detail = await api(`/api/delegation-rules/${encodeURIComponent(ruleId)}`);
  if (!delegationCanManage(detail)) {
    showToast("Only the owner can edit this delegation.");
    return;
  }
  if (!state.mineAgents || !state.mineAgents.length) {
    await loadMineAgents();
  }
  const currentAgentId = String(detail.target_agent_id || "").trim();
  const currentAgentInList = (state.mineAgents || []).some((agent) => agent.id === currentAgentId);
  const currentAgentOption = !currentAgentId || currentAgentInList ? "" : (
    `<option value="${escapeHtmlAttr(currentAgentId)}" selected>${safe(delegationTargetAgentLabel(detail))}</option>`
  );
  const agentOptions = currentAgentOption + (state.mineAgents || [])
    .map((agent) => `<option value="${escapeHtmlAttr(agent.id)}" ${agent.id === currentAgentId ? "selected" : ""}>${safe(agent.name || agent.id)}</option>`)
    .join("");
  const sourceValue = String(detail.source || detail.trigger_type || "").trim();
  const sourceOptions = DELEGATION_SOURCE_OPTIONS
    .map(([value, label]) => `<option value="${escapeHtmlAttr(value)}" ${value === sourceValue ? "selected" : ""}>${safe(label)}</option>`)
    .join("");
  const skillName = String(detail.skill_name || "").trim().replace(/^\/+/, "");
  const skillOptionLabel = skillName ? `/${safe(skillName)}` : "Select an agent first";
  const schedule = detail.schedule || {};
  const taskPrompt = String(detail.task_prompt || "");
  const intervalSeconds = Number(schedule.type === "interval" ? (schedule.interval_seconds || 60) : (detail.interval_seconds || 60));
  const sourceScope = detail.source_scope || {};
  const sourceConditions = detail.source_conditions || {};
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "delegation-rule-edit";
  dom.workspaceDetailContent.innerHTML = `
    <div class="modal portal-workspace-wizard-modal" role="dialog" aria-modal="true" aria-labelledby="edit-delegation-modal-title" aria-hidden="false">
      <div class="modal-card panel create-agent-wizard-card">
        <div class="portal-modal-titlebar">
          <div>
            <h3 id="edit-delegation-modal-title">Edit Delegation</h3>
            <p class="portal-modal-copy">Update how this delegation selects source work and runs the agent.</p>
          </div>
          <button class="portal-modal-close" type="button" title="Close" aria-label="Close" data-open-delegation-rule="${escapeHtmlAttr(detail.id)}">✕</button>
        </div>
        <form
          id="edit-delegation-inline-form"
          class="stack portal-step-wizard"
          data-wizard-steps="basics,source,work,review"
          data-current-step="basics"
          data-rule-id="${escapeHtmlAttr(detail.id)}"
          data-original-name="${escapeHtmlAttr(detail.name || "")}"
          data-original-target-agent-id="${escapeHtmlAttr(currentAgentId)}"
          data-original-source="${escapeHtmlAttr(sourceValue)}"
          data-original-source-scope="${delegationJsonAttr(sourceScope)}"
          data-original-source-conditions="${delegationJsonAttr(sourceConditions)}"
          data-original-schedule="${delegationJsonAttr(schedule)}"
          data-original-task-prompt="${escapeHtmlAttr(JSON.stringify(taskPrompt))}"
          data-original-skill-name="${escapeHtmlAttr(skillName)}"
          data-original-interval-seconds="${escapeHtmlAttr(String(intervalSeconds))}"
          data-original-enabled="${detail.enabled ? "true" : "false"}"
          data-selected-skill-name="${escapeHtmlAttr(skillName)}"
        >
          <ol class="create-agent-steps" aria-label="Edit delegation steps" style="--portal-step-count: 4">
            <li class="create-agent-step is-active" data-wizard-step-indicator="basics">
              <span class="create-agent-step-index">1</span>
              <span class="create-agent-step-label">Basics</span>
            </li>
            <li class="create-agent-step" data-wizard-step-indicator="source">
              <span class="create-agent-step-index">2</span>
              <span class="create-agent-step-label">Source</span>
            </li>
            <li class="create-agent-step" data-wizard-step-indicator="work">
              <span class="create-agent-step-index">3</span>
              <span class="create-agent-step-label">Work</span>
            </li>
            <li class="create-agent-step" data-wizard-step-indicator="review">
              <span class="create-agent-step-index">4</span>
              <span class="create-agent-step-label">Review</span>
            </li>
          </ol>
          <section class="create-agent-step-panel" data-wizard-step-panel="basics">
            <label class="portal-form-label"><span class="portal-form-label">Name</span><input class="portal-form-input" name="name" value="${escapeHtmlAttr(detail.name || "")}" required /></label>
            <label class="portal-form-label"><span class="portal-form-label">Agent</span><select class="portal-form-select" name="target_agent_id" required>${agentOptions}</select></label>
            <label class="portal-toggle-field"><span>Enabled</span><span class="toggle-switch" aria-label="Enabled"><input type="checkbox" name="enabled" ${detail.enabled ? "checked" : ""} /><span class="toggle-slider"></span></span></label>
          </section>
          <section class="create-agent-step-panel hidden" data-wizard-step-panel="source">
            <label class="portal-form-label"><span class="portal-form-label">Source</span><select class="portal-form-select" name="source" required>${sourceOptions}</select></label>
            <div data-delegation-source-controls></div>
          </section>
          <section class="create-agent-step-panel hidden" data-wizard-step-panel="work">
            <div data-delegation-schedule-controls></div>
            <label class="portal-form-label"><span class="portal-form-label">Skill</span><select class="portal-form-select" name="skill_name" required disabled><option value="${escapeHtmlAttr(skillName)}">${skillOptionLabel}</option></select></label>
            <label class="portal-form-label" data-delegation-interval-field><span class="portal-form-label">Interval seconds</span><input class="portal-form-input" name="interval_seconds" type="number" value="${escapeHtmlAttr(String(intervalSeconds))}" min="1" required /></label>
          </section>
          <section class="create-agent-step-panel hidden" data-wizard-step-panel="review">
            <div class="create-agent-review-grid" data-delegation-review></div>
          </section>
          <div class="portal-modal-actions portal-task-form-actions portal-step-wizard-actions">
            <button class="portal-btn is-secondary" type="button" data-open-delegation-rule="${escapeHtmlAttr(detail.id)}"><i data-lucide="arrow-left" class="w-4 h-4"></i>Cancel</button>
            <button class="portal-btn is-secondary" type="button" data-wizard-back>Back</button>
            <button class="portal-btn is-primary" type="button" data-wizard-next>Next</button>
            <button class="portal-btn is-primary" type="submit" data-wizard-submit><i data-lucide="save" class="w-4 h-4"></i>Save</button>
          </div>
        </form>
      </div>
    </div>
  `;
  const form = document.getElementById("edit-delegation-inline-form");
  if (form) {
    await setupDelegationSourceForm(form, sourceScope, sourceConditions, schedule, taskPrompt);
    await populateCreateTaskSkillSelect(form);
    initializeInlineWizard(form);
  }
  renderIcons();
}

async function submitCreateDelegationRule(formEl) {
  const fd = new FormData(formEl);
  const source = String(fd.get("source") || "").trim();
  const schedule = collectDelegationSchedule(formEl);
  const payload = {
    name: String(fd.get("name") || "").trim(),
    target_agent_id: String(fd.get("target_agent_id") || "").trim(),
    skill_name: String(fd.get("skill_name") || "").trim(),
    source,
    source_scope: collectDelegationSourceScope(formEl),
    source_conditions: collectDelegationSourceConditions(formEl),
    interval_seconds: schedule.type === "interval" ? schedule.interval_seconds : Number(fd.get("interval_seconds") || 60),
    schedule,
    enabled: fd.get("enabled") !== null,
  };
  if (isDelegationTimerSource(source)) payload.task_prompt = collectDelegationTaskPrompt(formEl);
  const created = await api("/api/delegation-rules", { method: "POST", body: JSON.stringify(payload) });
  await loadDelegationRules();
  await openDelegationRulePanel(created.id);
}

async function submitEditDelegationRule(formEl) {
  const ruleId = String(formEl?.dataset?.ruleId || "").trim();
  if (!ruleId) throw new Error("Missing delegation id");
  const fd = new FormData(formEl);
  const payload = {};
  const name = String(fd.get("name") || "").trim();
  const targetAgentId = String(fd.get("target_agent_id") || "").trim();
  const source = String(fd.get("source") || "").trim();
  const sourceScope = collectDelegationSourceScope(formEl);
  const sourceConditions = collectDelegationSourceConditions(formEl);
  const originalSourceScope = _safeJson(formEl.dataset.originalSourceScope || "{}") || {};
  const originalSourceConditions = _safeJson(formEl.dataset.originalSourceConditions || "{}") || {};
  const originalSchedule = _safeJson(formEl.dataset.originalSchedule || "{}") || {};
  const originalTaskPrompt = String(_safeJson(formEl.dataset.originalTaskPrompt || "\"\"") || "");
  const originalSkillName = String(formEl.dataset.originalSkillName || "");
  const skillName = fd.has("skill_name") ? String(fd.get("skill_name") || "").trim() : originalSkillName;
  const schedule = collectDelegationSchedule(formEl);
  const intervalSeconds = schedule.type === "interval" ? schedule.interval_seconds : Number(fd.get("interval_seconds") || 60);
  const taskPrompt = collectDelegationTaskPrompt(formEl);
  const enabled = fd.get("enabled") !== null;
  if (name !== String(formEl.dataset.originalName || "")) payload.name = name;
  if (targetAgentId !== String(formEl.dataset.originalTargetAgentId || "")) payload.target_agent_id = targetAgentId;
  if (source !== String(formEl.dataset.originalSource || "")) {
    payload.source = source;
    payload.source_scope = sourceScope;
    payload.source_conditions = sourceConditions;
  } else {
    if (JSON.stringify(sourceScope) !== JSON.stringify(originalSourceScope)) payload.source_scope = sourceScope;
    if (JSON.stringify(sourceConditions) !== JSON.stringify(originalSourceConditions)) payload.source_conditions = sourceConditions;
  }
  if (fd.has("skill_name") && skillName !== originalSkillName) payload.skill_name = skillName;
  if (JSON.stringify(schedule) !== JSON.stringify(originalSchedule)) payload.schedule = schedule;
  if (schedule.type === "interval" && intervalSeconds !== Number(formEl.dataset.originalIntervalSeconds || 60)) payload.interval_seconds = intervalSeconds;
  if (isDelegationTimerSource(source) && taskPrompt !== originalTaskPrompt) payload.task_prompt = taskPrompt;
  if (enabled !== (formEl.dataset.originalEnabled === "true")) payload.enabled = enabled;
  if (!Object.keys(payload).length) {
    showToast("No changes to save");
    await openDelegationRulePanel(ruleId);
    return;
  }
  const updated = await api(`/api/delegation-rules/${encodeURIComponent(ruleId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  await loadDelegationRules();
  await openDelegationRulePanel(updated.id);
}

async function runDelegationRuleOnce(ruleId) {
  const result = await api(`/api/delegation-rules/${encodeURIComponent(ruleId)}/run-once`, { method: "POST" });
  showToast(`Run finished: created ${result.created_task_count} task(s)`);
  await openDelegationRulePanel(ruleId);
}

async function toggleDelegationRuleEnabled(ruleId, enabled) {
  await api(`/api/delegation-rules/${encodeURIComponent(ruleId)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled: !!enabled }),
  });
  await loadDelegationRules();
  await openDelegationRulePanel(ruleId);
}

async function deleteDelegationRule(ruleId) {
  if (!(await showConfirm({ title: "Delete delegation", message: "This can't be undone.", confirmText: "Delete", danger: true }))) return;
  await api(`/api/delegation-rules/${encodeURIComponent(ruleId)}`, { method: "DELETE" });
  await loadDelegationRules();
  if (state.delegations.length) {
    await openDelegationRulePanel(state.delegations[0].id);
  } else {
    renderWorkspaceDetailPlaceholder("No delegations found.", "delegations-placeholder");
  }
}

async function loadDelegationRuleRuns(ruleId) {
  return api(`/api/delegation-rules/${encodeURIComponent(ruleId)}/runs`);
}

async function loadDelegationRuleEvents(ruleId) {
  return api(`/api/delegation-rules/${encodeURIComponent(ruleId)}/events`);
}

function inlineWizardSteps(formEl) {
  return String(formEl?.dataset?.wizardSteps || "")
    .split(",")
    .map((step) => step.trim())
    .filter(Boolean);
}

function inlineWizardStepIndex(formEl, step) {
  const steps = inlineWizardSteps(formEl);
  const index = steps.indexOf(step);
  return index >= 0 ? index : 0;
}

function inlineWizardCurrentStep(formEl) {
  const steps = inlineWizardSteps(formEl);
  if (!steps.length) return "";
  const step = String(formEl?.dataset?.currentStep || "").trim();
  return steps.includes(step) ? step : steps[0];
}

function selectedOptionLabel(formEl, name) {
  const field = formEl?.querySelector(`[name="${name}"]`);
  if (!field) return "";
  if (field.tagName === "SELECT") {
    return field.options?.[field.selectedIndex]?.textContent?.trim() || field.value || "";
  }
  return String(field.value || "").trim();
}

function wizardPreviewText(value, fallback = "Not set") {
  const text = String(value || "").trim();
  if (!text) return fallback;
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

function renderWizardReviewRows(container, rows) {
  if (!container) return;
  container.innerHTML = rows.map(([label, value]) => `
    <div class="create-agent-review-item">
      <span class="create-agent-review-label">${safe(label)}</span>
      <div class="create-agent-review-value">${safe(value)}</div>
    </div>
  `).join("");
}

function renderTaskCreateReview(formEl) {
  renderWizardReviewRows(document.getElementById("create-task-review"), [
    ["Agent", selectedOptionLabel(formEl, "assignee_agent_id") || "Not selected"],
    ["Skill", selectedOptionLabel(formEl, "skill_name") || "Not selected"],
    ["Task Content", wizardPreviewText(formEl?.querySelector('[name="task_content"]')?.value)],
    ["Mode", "Background task"],
  ]);
}

function renderTaskFollowupReview(formEl) {
  renderWizardReviewRows(document.getElementById("continue-task-review"), [
    ["Task", formEl?.dataset?.taskId || "Current task"],
    ["Follow-up", wizardPreviewText(formEl?.querySelector('[name="task_content"]')?.value)],
  ]);
}

function delegationScheduleSummaryForForm(formEl) {
  const source = String(formEl?.querySelector('[name="source"]')?.value || "").trim();
  if (isDelegationTimerSource(source)) {
    const expression = String(formEl?.querySelector('[name="schedule_cron_expression"]')?.value || "").trim();
    const timezone = String(formEl?.querySelector('[name="schedule_timezone"]')?.value || "").trim() || defaultDelegationTimezone();
    return expression ? `Cron ${expression} (${timezone})` : `Cron schedule (${timezone})`;
  }
  const seconds = Number(formEl?.querySelector('[name="interval_seconds"]')?.value || 60);
  return delegationIntervalLabel(Number.isFinite(seconds) && seconds > 0 ? seconds : 60);
}

function renderDelegationReview(formEl) {
  const source = String(formEl?.querySelector('[name="source"]')?.value || "").trim();
  const scope = collectDelegationSourceScope(formEl);
  const conditions = collectDelegationSourceConditions(formEl);
  const rows = [
    ["Name", selectedOptionLabel(formEl, "name") || "Untitled delegation"],
    ["Agent", selectedOptionLabel(formEl, "target_agent_id") || "Not selected"],
    ["Source", delegationSourceLabel(source) || "Not selected"],
    ["Source Filters", delegationConditionSummaryLabel(source, scope, conditions)],
    ["Skill", selectedOptionLabel(formEl, "skill_name") || "Not selected"],
    ["Schedule", delegationScheduleSummaryForForm(formEl)],
    ["Enabled", formEl?.querySelector('[name="enabled"]')?.checked ? "Yes" : "No"],
  ];
  if (isDelegationTimerSource(source)) {
    rows.push(["Task Prompt", wizardPreviewText(collectDelegationTaskPrompt(formEl))]);
  }
  renderWizardReviewRows(formEl?.querySelector("[data-delegation-review]"), rows);
}

function renderInlineWizardReview(formEl) {
  if (!formEl) return;
  if (formEl.id === "create-agent-async-task-form") {
    renderTaskCreateReview(formEl);
  } else if (formEl.id === "continue-agent-task-form") {
    renderTaskFollowupReview(formEl);
  } else if (formEl.matches("#create-delegation-inline-form, #edit-delegation-inline-form")) {
    renderDelegationReview(formEl);
  }
}

function setInlineWizardStep(formEl, step) {
  const steps = inlineWizardSteps(formEl);
  if (!formEl || !steps.length) return;
  const normalizedStep = steps[inlineWizardStepIndex(formEl, step)];
  const activeIndex = inlineWizardStepIndex(formEl, normalizedStep);
  formEl.dataset.currentStep = normalizedStep;
  formEl.querySelectorAll("[data-wizard-step-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.wizardStepPanel !== normalizedStep);
  });
  formEl.querySelectorAll("[data-wizard-step-indicator]").forEach((indicator) => {
    const index = inlineWizardStepIndex(formEl, indicator.dataset.wizardStepIndicator);
    indicator.classList.toggle("is-active", index === activeIndex);
    indicator.classList.toggle("is-complete", index < activeIndex);
    if (index === activeIndex) indicator.setAttribute("aria-current", "step");
    else indicator.removeAttribute("aria-current");
  });
  const actions = formEl.querySelector(".portal-step-wizard-actions");
  actions?.classList.toggle("is-review", normalizedStep === "review");
  const backButton = formEl.querySelector("[data-wizard-back]");
  if (backButton) backButton.disabled = activeIndex === 0;
  if (normalizedStep === "review") renderInlineWizardReview(formEl);
}

function validateInlineWizardPanel(formEl, panel) {
  if (!formEl || !panel) return true;
  const disabledRequired = Array.from(panel.querySelectorAll("input[required]:disabled, select[required]:disabled, textarea[required]:disabled"));
  if (disabledRequired.length) {
    showToast("Complete this step before continuing.");
    return false;
  }
  const fields = Array.from(panel.querySelectorAll("input, select, textarea"));
  const invalid = fields.find((field) => field.willValidate && !field.checkValidity());
  if (invalid) {
    invalid.reportValidity();
    return false;
  }
  return true;
}

function validateInlineWizardStep(formEl) {
  const step = inlineWizardCurrentStep(formEl);
  const panel = Array.from(formEl?.querySelectorAll("[data-wizard-step-panel]") || [])
    .find((item) => item.dataset.wizardStepPanel === step);
  return validateInlineWizardPanel(formEl, panel);
}

function validateInlineWizardForm(formEl) {
  const panels = Array.from(formEl?.querySelectorAll("[data-wizard-step-panel]") || []);
  for (const panel of panels) {
    const disabledRequired = Array.from(panel.querySelectorAll("input[required]:disabled, select[required]:disabled, textarea[required]:disabled"));
    const fields = Array.from(panel.querySelectorAll("input, select, textarea"));
    const invalid = fields.find((field) => field.willValidate && !field.checkValidity());
    if (disabledRequired.length || invalid) {
      setInlineWizardStep(formEl, panel.dataset.wizardStepPanel || inlineWizardCurrentStep(formEl));
      return validateInlineWizardPanel(formEl, panel);
    }
  }
  return true;
}

function moveInlineWizardStep(formEl, direction) {
  const steps = inlineWizardSteps(formEl);
  if (!steps.length) return;
  if (direction > 0 && !validateInlineWizardStep(formEl)) return;
  const currentIndex = inlineWizardStepIndex(formEl, inlineWizardCurrentStep(formEl));
  const nextIndex = Math.max(0, Math.min(steps.length - 1, currentIndex + direction));
  setInlineWizardStep(formEl, steps[nextIndex]);
}

function prepareInlineWizardSubmit(formEl) {
  const steps = inlineWizardSteps(formEl);
  if (!steps.length) return true;
  if (inlineWizardCurrentStep(formEl) !== "review") {
    moveInlineWizardStep(formEl, 1);
    return false;
  }
  return validateInlineWizardForm(formEl);
}

function initializeInlineWizard(formEl) {
  if (!formEl || !inlineWizardSteps(formEl).length) return;
  setInlineWizardStep(formEl, inlineWizardCurrentStep(formEl));
  renderInlineWizardReview(formEl);
}

function openWorkspaceWizardModal(modalEl) {
  if (!modalEl) return;
  modalEl.classList.remove("hidden");
  modalEl.setAttribute("aria-hidden", "false");
  const formEl = modalEl.querySelector(".portal-step-wizard");
  if (formEl) {
    setInlineWizardStep(formEl, inlineWizardCurrentStep(formEl));
    renderInlineWizardReview(formEl);
  }
  const focusTarget = formEl?.querySelector("input:not([type='hidden']):not(:disabled), select:not(:disabled), textarea:not(:disabled)")
    || modalEl.querySelector("button:not(:disabled)");
  focusTarget?.focus?.();
}

function closeWorkspaceWizardModal(modalEl) {
  if (!modalEl) return;
  modalEl.classList.add("hidden");
  modalEl.setAttribute("aria-hidden", "true");
}

function closeCreateDelegationRuleModal() {
  state.selectedDelegationRuleId = null;
  renderDelegationRuleNavList();
  renderWorkspaceDetailPlaceholder(
    state.delegations.length ? "Select a delegation from the left sidebar." : "No delegations found.",
    "delegations-placeholder",
  );
  syncMainHeader();
  if (!isApplyingPortalRoute) {
    commitPortalRoute({ section: "delegations" });
  }
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
  const formEl = dom.workspaceDetailContent.querySelector("#create-agent-async-task-form");
  if (formEl) {
    initializeInlineWizard(formEl);
    await populateCreateTaskSkillSelect(formEl);
  }
}

function setTaskSkillSelectOption(selectEl, label, { disabled = true, value = "" } = {}) {
  if (!selectEl) return;
  selectEl.innerHTML = "";
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  option.disabled = disabled;
  option.selected = true;
  selectEl.append(option);
  selectEl.disabled = disabled;
}

async function loadTaskSkillsForAgent(agentId) {
  if (!agentId) return [];
  if (state.cachedSkillsByAgent.has(agentId)) {
    const cached = state.cachedSkillsByAgent.get(agentId) || [];
    if (agentId === state.selectedAgentId) state.cachedSkills = cached;
    return cached;
  }
  const data = await agentApiFor(agentId, "/api/skills");
  const rawSkills = Array.isArray(data?.skills) ? data.skills : (Array.isArray(data) ? data : []);
  const mapped = rawSkills.map(toSkillSuggestion).filter((item) => item.command);
  state.cachedSkillsByAgent.set(agentId, mapped);
  if (agentId === state.selectedAgentId) state.cachedSkills = mapped;
  return mapped;
}

async function populateCreateTaskSkillSelect(formEl) {
  if (!formEl) return;
  const agentSelect = formEl.querySelector('[name="assignee_agent_id"], [name="target_agent_id"]');
  const skillSelect = formEl.querySelector('[name="skill_name"]');
  const agentId = String(agentSelect?.value || "").trim();
  const selectedSkillName = String(formEl.dataset.selectedSkillName || "").trim().replace(/^\/+/, "");
  if (!skillSelect) return;
  if (!agentId) {
    setTaskSkillSelectOption(skillSelect, "Select an agent first");
    renderInlineWizardReview(formEl);
    return;
  }
  setTaskSkillSelectOption(skillSelect, "Loading skills...");
  try {
    const skills = await loadTaskSkillsForAgent(agentId);
    skillSelect.innerHTML = "";
    const callableSkills = skills.filter((skill) => skill.callable !== false);
    if (!callableSkills.length) {
      setTaskSkillSelectOption(skillSelect, "No callable skills found");
      skills.filter((skill) => skill.callable === false).forEach((skill) => {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = `${skill.command} unavailable`;
        option.disabled = true;
        option.title = skill.title || skill.blocked_reason || skill.desc || "";
        skillSelect.append(option);
      });
      renderInlineWizardReview(formEl);
      return;
    }
    let matchedSelectedSkill = false;
    callableSkills.forEach((skill, index) => {
      const option = document.createElement("option");
      option.value = String(skill.command || "").replace(/^\/+/, "");
      option.textContent = skill.command || `/${option.value}`;
      option.title = skill.desc || "";
      option.selected = selectedSkillName ? option.value === selectedSkillName : index === 0;
      matchedSelectedSkill = matchedSelectedSkill || option.selected;
      skillSelect.append(option);
    });
    if (selectedSkillName && !matchedSelectedSkill) {
      const option = document.createElement("option");
      option.value = selectedSkillName;
      option.textContent = `/${selectedSkillName}`;
      option.selected = true;
      skillSelect.prepend(option);
    }
    skills.filter((skill) => skill.callable === false).forEach((skill) => {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = `${skill.command} unavailable`;
      option.disabled = true;
      option.title = skill.title || skill.blocked_reason || skill.desc || "";
      skillSelect.append(option);
    });
    skillSelect.disabled = false;
    renderInlineWizardReview(formEl);
  } catch (error) {
    setTaskSkillSelectOption(skillSelect, `Failed to load skills: ${error.message}`);
    renderInlineWizardReview(formEl);
  }
}

async function submitCreateAgentAsyncTask(formEl) {
  const fd = new FormData(formEl);
  const created = await api("/api/agent-tasks/async", {
    method: "POST",
    body: JSON.stringify({
      assignee_agent_id: String(fd.get("assignee_agent_id") || "").trim(),
      skill_name: String(fd.get("skill_name") || "").trim(),
      task_content: String(fd.get("task_content") || "").trim(),
    }),
  });
  await refreshMyTasks();
  await openTaskDetailInMain(created.id);
}

async function submitContinueAgentTask(formEl) {
  const taskId = String(formEl?.dataset?.taskId || "").trim();
  if (!taskId) throw new Error("Missing task id");
  const fd = new FormData(formEl);
  const updated = await api(`/api/agent-tasks/${encodeURIComponent(taskId)}/followups`, {
    method: "POST",
    body: JSON.stringify({
      task_content: String(fd.get("task_content") || "").trim(),
    }),
  });
  await refreshMyTasks();
  await openTaskDetailInMain(updated.id);
}

async function rerunAgentTask(taskId) {
  const updated = await api(`/api/agent-tasks/${encodeURIComponent(taskId)}/rerun`, { method: "POST" });
  await refreshMyTasks();
  await openTaskDetailInMain(updated.id);
}

async function cancelAgentTask(taskId) {
  const cancelled = await api(`/api/agent-tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
  await refreshMyTasks();
  await openTaskDetailInMain(cancelled.id);
}

async function openEditDialog(agent) {
  await Promise.all([loadRuntimeProfiles(true), loadAgentDefaults()]);
  const form = document.getElementById("edit-form");
  if (form && form.elements) {
    if (form.elements["id"]) form.elements["id"].value = agent.id ?? "";
    if (form.elements["name"]) form.elements["name"].value = agent.name || "";
    form.dataset.runtimeType = normalizeRuntimeTypeValue(agent.runtime_type || state.agentDefaults?.default_runtime_type || "native", state.agentDefaults || {});
    updateEditRuntimeTypeDisplay(form, state.agentDefaults || {});
    if (form.elements["agent_settings_repo_url"]) form.elements["agent_settings_repo_url"].value = agent.agent_settings_repo_url || "";
    if (form.elements["agent_settings_branch"]) {
      populateBranchSelect(
        "edit-agent-settings-branch-select",
        [],
        agent.agent_settings_branch || "",
        state.agentDefaults?.default_agent_settings_branch || "",
      );
    }
    if (form.elements["skill_repo_url"]) form.elements["skill_repo_url"].value = agent.skill_repo_url || "";
    if (form.elements["skill_branch"]) {
      populateBranchSelect(
        "edit-skill-branch-select",
        [],
        agent.skill_branch || "",
        state.agentDefaults?.default_skill_branch || "",
      );
    }
    if (form.elements["runtime_profile_id"]) populateRuntimeProfileSelect(form.elements["runtime_profile_id"], agent.runtime_profile_id || "");
    syncEditRuntimeProfileState(form);
    setEditAgentStep(form, "runtime");
  }

  // Show the modal
  const editModal = document.getElementById("edit-modal");
  if (editModal) {
    editModal.classList.remove("hidden");
    editModal.setAttribute("aria-hidden", "false");
  }
  Promise.allSettled([
    refreshEditRepoBranches("agent-settings"),
    refreshEditRepoBranches("skills"),
  ]);
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
    || message?.metadata?.runtime_message_id
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
  if (chatState.inflightEventStream && (!requestId || chatState.inflightEventStream.requestId === requestId || chatState.inflightEventStream.id === requestId)) {
    chatState.inflightEventStream.completed = true;
    chatState.inflightEventStream.status = status;
    chatState.inflightEventStream.completion_state = finalPayload?.completion_state || (status === "error" ? "error" : "completed");
    chatState.lastEventStreamSnapshot = {
      ...chatState.inflightEventStream,
      completed: true,
      completedAt: Date.now(),
      requestId,
      sessionId: finalPayload?.session_id || requestCtx?.sessionIdAtSend || chatState.sessionId || "",
    };
    chatState.inflightEventStream = null;
  }
  if (typeof finalizeAgentTimelineState === "function") {
    finalizeAgentTimelineState(chatState, requestCtx, finalPayload);
  }
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
    const shouldContinue = await showConfirm({ title: "Retry response", message: "Retrying will remove this message and all messages after it.", confirmText: "Retry", danger: true });
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
      showToast(getRuntimeMutationErrorMessage(response, {}, "Failed to delete message"), { variant: 'error' });
      return;
    }

    if (!response.ok || !result.success) {
      showToast(getRuntimeMutationErrorMessage(response, result, "Failed to delete message"), { variant: 'error' });
      return;
    }

    truncateDomFromUserArticle(userArticle);
    if (chatState) chatState.pendingFiles = [];
    if (dom.chatInput) dom.chatInput.value = content;
    setChatStatus("Retrying...");
    await submitChatForSelectedAgent();
  } catch (err) {
    showToast("Retry failed: " + (err?.message || String(err)), { variant: 'error' });
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
function showToast(message, opts = {}) {
  const toast = document.getElementById('global-toast');
  if (!toast) return;
  const o = typeof opts === 'number' ? { duration: opts } : (opts || {});
  const duration = o.duration || 2000;
  const inner = toast.querySelector('div');
  inner.textContent = message;
  inner.classList.remove('is-error', 'is-info');
  if (o.variant === 'error') inner.classList.add('is-error');
  else if (o.variant === 'info') inner.classList.add('is-info');
  toast.classList.remove('hidden');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add('hidden'), duration);
}

// ===== wiring =====
function bindEvents() {
  // Edit modal events
  dom.editForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if ((form?.dataset?.currentStep || "runtime") !== "review") {
      moveEditAgentStep(form, 1);
      return;
    }
    if (!validateEditAgentStep(form)) return;
    const formData = new FormData(form);
    const id = formData.get("id");

    const updates = { name: formData.get("name")?.trim() };
    const agentSettingsRepoUrl = formData.get("agent_settings_repo_url")?.trim();
    const agentSettingsBranch = formData.get("agent_settings_branch")?.trim();
    const repoUrl = formData.get("skill_repo_url")?.trim();
    const branch = formData.get("skill_branch")?.trim();
    const runtimeProfileId = (formData.get("runtime_profile_id") || "").toString().trim();

    // Always include agent settings and skill fields; empty values mean "use configured default".
    if (agentSettingsRepoUrl !== undefined) updates.agent_settings_repo_url = agentSettingsRepoUrl || null;
    if (agentSettingsBranch !== undefined) updates.agent_settings_branch = agentSettingsBranch || null;
    if (repoUrl !== undefined) updates.skill_repo_url = repoUrl || null;
    if (branch !== undefined) updates.skill_branch = branch || null;
    updates.runtime_profile_id = runtimeProfileId || null;

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

  dom.editForm?.addEventListener("change", (event) => {
    if (event.currentTarget?.dataset?.currentStep === "review") {
      renderEditAgentReview(event.currentTarget, state.agentDefaults || {});
    }
  });

  dom.editForm?.addEventListener("click", async (event) => {
    const form = event.currentTarget;
    const loadBranches = event.target?.closest?.("[data-edit-load-branches]")?.dataset?.editLoadBranches;
    if (loadBranches) {
      await refreshEditRepoBranches(loadBranches);
      if (form?.dataset?.currentStep === "review") renderEditAgentReview(form, state.agentDefaults || {});
      return;
    }
    if (event.target?.closest?.("[data-edit-back]")) {
      moveEditAgentStep(form, -1);
      return;
    }
    if (event.target?.closest?.("[data-edit-next]")) {
      moveEditAgentStep(form, 1);
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
      showToast("Invalid session", { variant: 'error' });
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
      chatState.inflightEventStream = {
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
      if (typeof createAgentTimelineState === "function") {
        if (typeof dropInflightAgentTimelineState === "function") dropInflightAgentTimelineState(chatState);
        else chatState.inflightAgentTimeline = null;
        chatState.inflightAgentTimeline = createAgentTimelineState({
          requestId,
          sessionId: finalSessionId,
        });
      }
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
        showToast("Error editing message: " + message, { variant: 'error' });
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
  dom.dashboardMenuBtn?.addEventListener("click", () => openPortalSection("dashboard"));
  dom.railAssistantsBtn?.addEventListener("click", () => openPortalSection("assistants"));
  dom.agentSearchInput?.addEventListener("input", () => {
    state.agentFilters.query = String(dom.agentSearchInput.value || "").trim();
    renderAgentList();
  });
  dom.bundlesMenuBtn?.addEventListener("click", () => openPortalSection("bundles"));
  dom.taskNavList?.addEventListener("scroll", () => {
    if (state.activeNavSection === "tasks") loadMoreTasksIfNeeded();
  });
  dom.taskOwnerFilter?.addEventListener("change", () => {
    state.taskFilters.owner = dom.taskOwnerFilter.value || "all";
    refreshMyTasks({ reset: true });
  });
  dom.taskStatusFilter?.addEventListener("change", () => {
    state.taskFilters.status = dom.taskStatusFilter.value || "all";
    refreshMyTasks({ reset: true });
  });
  dom.delegationsMenuBtn?.addEventListener("click", () => openPortalSection("delegations"));
  dom.dashboardScopeFilter?.addEventListener("change", async () => {
    state.dashboardScope = dom.dashboardScopeFilter.value === "mine" ? "mine" : "all";
    await loadDashboardPanel();
  });
  dom.dashboardNavSection?.addEventListener("click", async (event) => {
    const shortcut = event.target.closest("[data-dashboard-shortcut]");
    if (!shortcut) return;
    event.preventDefault();
    if (state.activeNavSection !== "dashboard") {
      await openPortalSection("dashboard", { toggleIfSame: false });
    }
    scrollDashboardSection(shortcut.dataset.dashboardShortcut || "");
  });
  dom.delegationOwnerFilter?.addEventListener("change", () => {
    state.delegationFilters.owner = dom.delegationOwnerFilter.value || "all";
    renderDelegationRuleNavList();
  });
  dom.delegationSourceFilter?.addEventListener("change", () => {
    state.delegationFilters.source = dom.delegationSourceFilter.value || "all";
    renderDelegationRuleNavList();
  });
  dom.addDelegationBtn?.addEventListener("click", async () => {
    try {
      if (!state.mineAgents || !state.mineAgents.length) {
        await loadMineAgents();
      }
      await openCreateDelegationRuleModal();
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
    const delegationForm = event.target.closest("#create-delegation-inline-form");
    if (delegationForm) {
      event.preventDefault();
      if (!prepareInlineWizardSubmit(delegationForm)) return;
      try {
        await submitCreateDelegationRule(delegationForm);
      } catch (error) {
        showToast(`Create delegation failed: ${error.message}`, { variant: 'error' });
      }
      return;
    }
    const editDelegationForm = event.target.closest("#edit-delegation-inline-form");
    if (editDelegationForm) {
      event.preventDefault();
      if (!prepareInlineWizardSubmit(editDelegationForm)) return;
      try {
        await submitEditDelegationRule(editDelegationForm);
      } catch (error) {
        showToast(`Update delegation failed: ${error.message}`, { variant: 'error' });
      }
      return;
    }
    const taskForm = event.target.closest("#create-agent-async-task-form");
    if (taskForm) {
      event.preventDefault();
      if (!prepareInlineWizardSubmit(taskForm)) return;
      try {
        await submitCreateAgentAsyncTask(taskForm);
      } catch (error) {
        showToast(`Create task failed: ${error.message}`, { variant: 'error' });
      }
      return;
    }
    const continueTaskForm = event.target.closest("#continue-agent-task-form");
    if (continueTaskForm) {
      event.preventDefault();
      if (!prepareInlineWizardSubmit(continueTaskForm)) return;
      try {
        await submitContinueAgentTask(continueTaskForm);
      } catch (error) {
        showToast(`Continue task failed: ${error.message}`, { variant: 'error' });
      }
    }
  });
  dom.workspaceDetailContent?.addEventListener("change", async (event) => {
    const formEl = event.target.closest("#create-agent-async-task-form, #create-delegation-inline-form, #edit-delegation-inline-form");
    if (!formEl) return;
    if (event.target.matches('[name="assignee_agent_id"], [name="target_agent_id"]')) {
      if (formEl.id === "edit-delegation-inline-form") formEl.dataset.selectedSkillName = "";
      await populateCreateTaskSkillSelect(formEl);
      if (formEl.matches("#create-delegation-inline-form, #edit-delegation-inline-form")) {
        await refreshDelegationSourcePreview(formEl, { resetScope: true });
      }
      renderInlineWizardReview(formEl);
      return;
    }
    if (formEl.matches("#create-delegation-inline-form, #edit-delegation-inline-form") && event.target.matches('[name="source"]')) {
      renderDelegationScheduleControls(formEl);
      await refreshDelegationSchedulePreview(formEl);
      await refreshDelegationSourcePreview(formEl, { resetScope: true, resetConditions: true });
      renderInlineWizardReview(formEl);
      return;
    }
    if (formEl.matches("#create-delegation-inline-form, #edit-delegation-inline-form") && event.target.matches('[name="source_scope_jira_instance"]')) {
      await refreshDelegationSourcePreview(formEl);
      renderInlineWizardReview(formEl);
      return;
    }
    if (formEl.matches("#create-delegation-inline-form, #edit-delegation-inline-form") && event.target.matches('[name="schedule_skip_overlapping"]')) {
      await refreshDelegationSchedulePreview(formEl);
    }
    renderInlineWizardReview(formEl);
  });
  dom.workspaceDetailContent?.addEventListener("input", (event) => {
    const wizardForm = event.target.closest(".portal-step-wizard");
    if (wizardForm) renderInlineWizardReview(wizardForm);
    const formEl = event.target.closest("#create-delegation-inline-form, #edit-delegation-inline-form");
    if (!formEl) return;
    if (event.target.matches('[name="schedule_cron_expression"], [name="schedule_timezone"]')) {
      refreshDelegationSchedulePreview(formEl, { debounce: true }).catch(() => {});
    }
  });
  dom.workspaceDetailContent?.addEventListener("click", async (event) => {
    const dashboardScopeBtn = event.target.closest("button[data-dashboard-scope]");
    if (dashboardScopeBtn) {
      event.preventDefault();
      const nextScope = dashboardScopeBtn.dataset.dashboardScope === "mine" ? "mine" : "all";
      await loadDashboardPanel({ scope: nextScope });
      return;
    }

    const dashboardRefreshBtn = event.target.closest("[data-refresh-dashboard]");
    if (dashboardRefreshBtn) {
      event.preventDefault();
      await loadDashboardPanel();
      return;
    }

    const dashboardAgentBtn = event.target.closest("[data-open-dashboard-agent]");
    if (dashboardAgentBtn) {
      event.preventDefault();
      await openDashboardAgent(dashboardAgentBtn.dataset.openDashboardAgent || "");
      return;
    }

    const dashboardDelegationBtn = event.target.closest("[data-open-dashboard-delegation]");
    if (dashboardDelegationBtn) {
      event.preventDefault();
      await openDashboardDelegation(dashboardDelegationBtn.dataset.openDashboardDelegation || "");
      return;
    }

    const closeDelegationCreateBtn = event.target.closest("[data-close-delegation-create-modal]");
    if (closeDelegationCreateBtn) {
      event.preventDefault();
      closeCreateDelegationRuleModal();
      return;
    }

    const wizardButton = event.target.closest("[data-wizard-back], [data-wizard-next]");
    if (wizardButton) {
      event.preventDefault();
      const formEl = wizardButton.closest(".portal-step-wizard");
      if (!formEl) return;
      moveInlineWizardStep(formEl, wizardButton.matches("[data-wizard-back]") ? -1 : 1);
      return;
    }

    const openDelegationBtn = event.target.closest("[data-open-delegation-rule]");
    if (openDelegationBtn) {
      event.preventDefault();
      await openDelegationRulePanel(openDelegationBtn.dataset.openDelegationRule || "");
      return;
    }
    const editDelegationBtn = event.target.closest("[data-edit-delegation-rule]");
    if (editDelegationBtn) {
      event.preventDefault();
      try {
        await openEditDelegationRuleModal(editDelegationBtn.dataset.editDelegationRule || "");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Edit failed: ${safe(error.message)}</div>`;
      }
      return;
    }
    const runBtn = event.target.closest("[data-run-delegation-once]");
    if (runBtn) {
      event.preventDefault();
      try {
        await runDelegationRuleOnce(runBtn.dataset.runDelegationOnce || "");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Run once failed: ${safe(error.message)}</div>`;
      }
      return;
    }
    const toggleBtn = event.target.closest("[data-toggle-delegation-enabled]");
    if (toggleBtn) {
      event.preventDefault();
      try {
        await toggleDelegationRuleEnabled(toggleBtn.dataset.toggleDelegationEnabled || "", toggleBtn.dataset.nextEnabled === "true");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Update failed: ${safe(error.message)}</div>`;
      }
      return;
    }
    const deleteBtn = event.target.closest("[data-delete-delegation-rule]");
    if (deleteBtn) {
      event.preventDefault();
      try {
        await deleteDelegationRule(deleteBtn.dataset.deleteDelegationRule || "");
      } catch (error) {
        dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Delete failed: ${safe(error.message)}</div>`;
      }
    }
  });

  dom.workspaceDetailContent?.addEventListener("click", async (event) => {
    const closeTaskCreateBtn = event.target.closest("[data-close-task-create-modal]");
    if (closeTaskCreateBtn) {
      event.preventDefault();
      await returnFromTaskDetailToSidebar();
      return;
    }

    const openTaskFollowupBtn = event.target.closest("[data-open-task-followup-modal]");
    if (openTaskFollowupBtn) {
      event.preventDefault();
      openWorkspaceWizardModal(dom.workspaceDetailContent.querySelector("#task-followup-modal"));
      return;
    }

    const closeTaskFollowupBtn = event.target.closest("[data-close-task-followup-modal]");
    if (closeTaskFollowupBtn) {
      event.preventDefault();
      closeWorkspaceWizardModal(closeTaskFollowupBtn.closest(".modal"));
      return;
    }

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

    const cancelTaskBtn = event.target.closest("[data-cancel-task]");
    if (cancelTaskBtn) {
      event.preventDefault();
      const taskId = cancelTaskBtn.dataset.cancelTask || "";
      if (!taskId) return;
      try {
        await cancelAgentTask(taskId);
      } catch (error) {
        showToast(`Cancel task failed: ${error.message}`, { variant: 'error' });
      }
      return;
    }

    const rerunTaskBtn = event.target.closest("[data-rerun-task]");
    if (rerunTaskBtn) {
      event.preventDefault();
      const taskId = rerunTaskBtn.dataset.rerunTask || "";
      if (!taskId) return;
      try {
        await rerunAgentTask(taskId);
      } catch (error) {
        showToast(`Rerun task failed: ${error.message}`, { variant: 'error' });
      }
      return;
    }

    const deleteProfileBtn = event.target.closest("[data-delete-runtime-profile]");
    if (deleteProfileBtn) {
      event.preventDefault();
      const profileId = deleteProfileBtn.dataset.deleteRuntimeProfile || "";
      if (!profileId) return;
      if (!(await showConfirm({ title: "Delete runtime profile", message: "This can't be undone.", confirmText: "Delete", danger: true }))) return;
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
        showToast(err.message, { variant: 'error' });
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
      showToast(`Open task create failed: ${error.message}`, { variant: 'error' });
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
    if (createForm) {
      syncCreateRuntimeProfileState(createForm);
      setCreateAgentStep(createForm, "runtime");
    }
    document.getElementById("create-modal")?.classList.remove("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "false");
    Promise.allSettled([
      refreshCreateRepoBranches("agent-settings"),
      refreshCreateRepoBranches("skills"),
    ]);
  });

  document.getElementById("close-create-modal")?.addEventListener("click", () => {
    if (document.getElementById("create-form")?.dataset.submitting === "true") return;
    document.getElementById("create-modal")?.classList.add("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
  });

  document.getElementById("create-form")?.addEventListener("change", (event) => {
    if (event.target?.name === "runtime_type") {
      updateCreateRuntimeTypeHint(event.currentTarget, state.agentDefaults || {});
    }
    if (event.currentTarget?.dataset?.currentStep === "review") {
      renderCreateAgentReview(event.currentTarget, state.agentDefaults || {});
    }
  });

  document.getElementById("create-form")?.addEventListener("click", async (event) => {
    const form = event.currentTarget;
    const loadBranches = event.target?.closest?.("[data-load-branches]")?.dataset?.loadBranches;
    if (loadBranches) {
      await refreshCreateRepoBranches(loadBranches);
      if (form?.dataset?.currentStep === "review") renderCreateAgentReview(form, state.agentDefaults || {});
      return;
    }
    if (event.target?.closest?.("[data-create-back]")) {
      moveCreateAgentStep(form, -1);
      return;
    }
    if (event.target?.closest?.("[data-create-next]")) {
      moveCreateAgentStep(form, 1);
    }
  });

  document.getElementById("create-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if ((form?.dataset?.currentStep || "runtime") !== "review") {
      moveCreateAgentStep(form, 1);
      return;
    }
    if (!validateCreateAgentStep(form)) return;
    if (!beginSingleSubmit(form, { pendingText: "Creating...", closeButton: document.getElementById("close-create-modal") })) return;
    const formData = new FormData(form);
    const name = formData.get("name");
    const agentSettingsRepoUrl = (formData.get("agent_settings_repo_url") || "").toString().trim();
    const agentSettingsBranch = (formData.get("agent_settings_branch") || "").toString().trim();
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

      // Use form values if provided, or null to let backend apply configured defaults.
      const data = {
        name: name,
        runtime_type: runtimeType,
        agent_settings_repo_url: agentSettingsRepoUrl || null,
        agent_settings_branch: agentSettingsBranch || null,
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
      title: String(formData.get("title") || ""),
      domain: String(formData.get("domain") || ""),
      slug: String(formData.get("slug") || "").trim() || null,
      base_branch: String(formData.get("base_branch") || ""),
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
    showToast("Failed to open URL route: " + (error?.message || error), { variant: 'error' });
  });
});

window.addEventListener("popstate", () => {
  applyPortalRouteFromHash({ replaceInvalid: true }).catch((error) => {
    console.error("Failed to apply portal route from history", error);
    showToast("Failed to open URL route: " + (error?.message || error), { variant: 'error' });
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
  if (!agent) return 'native';
  var value = agent.runtime_type || agent.runtimeType || agent.runtime;
  if (typeof value !== 'string') return 'native';
  value = value.trim();
  return value || 'native';
}

function getSystemPromptUiModel(agent, config) {
  config = config || {};
  var runtimeType = String(config.runtime_type || getAgentRuntimeType(agent) || 'native');
  var configUi = config.ui && typeof config.ui === 'object' ? config.ui : {};
  var rawSections = Array.isArray(config.sections) ? config.sections : (Array.isArray(configUi.sections) ? configUi.sections : []);
  var sections = [];
  var seenSections = {};
  for (var i = 0; i < rawSections.length; i++) {
    var sectionName = String(rawSections[i] || '').trim();
    if (!sectionName || seenSections[sectionName]) continue;
    sections.push(sectionName);
    seenSections[sectionName] = true;
  }
  if (!sections.length) sections = ['agents'];

  var labels = {};
  var editable = {};
  var canToggle = {};
  var forcedEnabled = {};
  var uiLabels = configUi.labels && typeof configUi.labels === 'object' ? configUi.labels : {};
  for (var j = 0; j < sections.length; j++) {
    var name = sections[j];
    var sectionConfig = config[name] && typeof config[name] === 'object' ? config[name] : {};
    var configuredLabel = typeof sectionConfig.label === 'string' && sectionConfig.label.trim()
      ? sectionConfig.label.trim()
      : (typeof uiLabels[name] === 'string' && uiLabels[name].trim() ? uiLabels[name].trim() : '');
    labels[name] = configuredLabel || (name === 'agents' ? 'AGENTS' : String(name).toUpperCase().replace(/_/g, ' '));
    editable[name] = sectionConfig.editable !== false;
    canToggle[name] = sectionConfig.can_disable !== false;
    if (sectionConfig.can_disable === false) forcedEnabled[name] = true;
  }

  return {
    runtimeType: runtimeType,
    title: typeof configUi.title === 'string' && configUi.title.trim() ? configUi.title.trim() : 'System Prompt',
    description: typeof configUi.description === 'string' ? configUi.description : '',
    sections: sections,
    labels: labels,
    editable: editable,
    canToggle: canToggle,
    forcedEnabled: forcedEnabled
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
      var safeName = escapeHtmlAttr(name);
      var toggleHtml = isToggleable
        ? '<label class="toggle-switch"><input type="checkbox" id="sp-' + safeName + '-enabled" data-section="' + safeName + '" ' + (enabled ? 'checked' : '') + ' class="portal-system-prompt-check"' + disabledAttr + '><span class="toggle-slider"></span></label>'
        : '<label class="toggle-switch"><input type="checkbox" id="sp-' + safeName + '-enabled" data-section="' + safeName + '" checked class="portal-system-prompt-check" disabled><span class="toggle-slider"></span></label><span class="portal-muted">Always enabled</span>';
      var editAllowed = ui.editable[name] === true;
      var editDisabledAttr = canWrite ? '' : ' disabled';
      var editButton = editAllowed ? '<button data-section="' + safeName + '" data-action="edit" class="portal-btn is-secondary portal-system-prompt-edit" title="Edit ' + escapeHtmlAttr(label) + '"' + editDisabledAttr + '>Edit</button>' : '';

      var item = document.createElement('div');
      item.className = 'portal-system-prompt-item';
      item.innerHTML = '<div class="portal-checkbox-row">' + toggleHtml + '<span>' + escapeHtml(label) + '</span></div>' + editButton;
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
  var payload = {};
  payload[section] = { enabled: enabled };
  api('/a/' + agentId + '/api/agent/system-prompt/config', { method: 'PUT', body: JSON.stringify(payload) }).catch(function(e) {
    console.error('Failed to update:', e);
    showToast('Failed to update: ' + e.message, { variant: 'error' });
    loadSystemPromptConfig(agentId);
  });
}

function editSystemPromptSection(agentId, section) {
  api('/a/' + agentId + '/api/agent/system-prompt/' + section).then(function(data) {
    showSystemPromptEditor(agentId, section, data.content || '', data.enabled, data || {});
  }).catch(function(e) {
    console.error('Failed to load:', e);
    showToast('Failed to load: ' + e.message, { variant: 'error' });
  });
}

function showSystemPromptEditor(agentId, section, content, enabled, sectionConfig) {
  var currentAgent = state.mineAgents?.find(a => a.id === agentId);
  sectionConfig = sectionConfig || {};
  var label = typeof sectionConfig.label === 'string' && sectionConfig.label.trim()
    ? sectionConfig.label.trim()
    : resolveSystemPromptLabel(currentAgent, section, {});
  var title = label + ' Configuration';
  var canDisable = sectionConfig.can_disable !== false;

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
  enabledCheckbox.checked = canDisable ? enabled !== false : true;
  enabledCheckbox.disabled = !canDisable;
  enabledNote.textContent = '';
  enabledNote.classList.add('hidden');
  if (!canDisable) {
    enabledNote.textContent = 'Always enabled';
    enabledNote.classList.remove('hidden');
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
  var enabledCheckbox = document.getElementById('sp-editor-enabled');
  var enabled = enabledCheckbox.disabled ? true : enabledCheckbox.checked;
  var content = document.getElementById('sp-editor-content').value;

  api('/a/' + agentId + '/api/agent/system-prompt/' + section, {
    method: 'PUT',
    body: JSON.stringify({ enabled: enabled, content: content })
  }).then(function() {
    closeSystemPromptEditor();
    loadSystemPromptConfig(agentId);
  }).catch(function(e) {
    console.error('Failed to save:', e);
    showToast('Failed to save: ' + e.message, { variant: 'error' });
    loadSystemPromptConfig(agentId);
  });
}

// provider.retry UX copy: Provider API retrying. Check Runtime Profile LLM API key/base URL/proxy.
