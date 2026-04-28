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
  "uploads",
  "users",
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

function addToAttachmentHistory(attachments) {
  const chatState = getChatState();
  if (!chatState) return;
  chatState.attachmentHistory.push(attachments);
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
  cachedMentionFiles: [],
  cachedMentionFilesByAgent: new Map(),
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

function createDefaultChatState() {
  return {
    sessionId: "",
    isSubmitting: false,
    pendingFiles: [],
    attachmentHistory: [],
    inflightThinking: null,
    lastThinkingSnapshot: null,
    pendingThinkingEvents: null,
    didAppendAttachmentHistoryForPendingSend: false,
    draftText: "",
    draftAttachmentsValue: "",
    activeRequest: null,
    needsReload: false,
    unreadCount: 0,
    backgroundStatus: "",
    lastCompletedRequestId: "",
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

function hasActiveChatRequestForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return false;
  // Phase-A constraint: one in-flight chat request per agent.
  // This keeps guard behavior deterministic without per-session concurrency state.
  return Boolean(
    chatState.isSubmitting
    || chatState.activeRequest
    || (chatState.inflightThinking && chatState.inflightThinking.completed === false)
  );
}

function activeChatRequestMessage(actionLabel = "perform this action") {
  return `This assistant is still working in the current session. Please wait until it finishes before you ${actionLabel}.`;
}

function guardNoActiveChatRequestForAgent(agentId, actionLabel = "perform this action") {
  if (!hasActiveChatRequestForAgent(agentId)) return true;
  const message = activeChatRequestMessage(actionLabel);
  showToast(message);
  setChatStatus(message, true);
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
    return;
  }

  const busy = hasActiveChatRequestForAgent(agentId);
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
  return await response.json();
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
          pf.status = "failed";
          pf.parseError = parseError?.message || "Parse failed";
          pf.error = pf.parseError;
          pf.parseData = null;
          renderInputPreview();
          showToast("File processing failed: " + pf.parseError);
          continue;
        }
      } else {
        pf.status = "uploaded";
        pf.error = "";
        pf.parseError = "";
        pf.parseData = null;
        renderInputPreview();
      }
      showToast("File uploaded: " + file.name);

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

function toSkillSuggestion(item) {
  const name = typeof item === "string" ? item : (item?.name || "");
  const command = normalizeSkillCommand(name);
  return {
    label: command,
    command,
    desc: typeof item === "string" ? "Skill" : (item?.description || "Skill"),
  };
}

function canWriteAgent(agent) {
  if (!agent) return false;
  return state.currentUserRole === "admin" || Number(agent.owner_user_id) === state.currentUserId;
}

function getSelectedAgent() {
  return state.mineAgents.find((item) => item.id === state.selectedAgentId) || null;
}

function getSelectedAgentStatus() {
  const agent = getSelectedAgent();
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
  if (!button.dataset.defaultTitle) {
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

function buildUserMessageArticle(text, attachments = []) {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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

  return `<div class="message-row message-row-user"><div class="message-meta message-meta-user"><span class="message-author">${escapeHtml(getCurrentUserDisplayName())}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-user" data-local-user="1" data-optimistic-user="1"><div class="message-body whitespace-pre-wrap text-sm">${safe(text)}</div>${attachmentHtml}</article></div>`;
}

function buildPendingAssistantArticle() {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const pendingAgentName = getSelectedAssistantDisplayName();
  return `<div class="message-row message-row-assistant" data-temporary-assistant="1"><div class="message-meta"><span class="message-author">${escapeHtml(pendingAgentName)}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-assistant assistant-message pending-assistant" data-pending-assistant="1"><div class="pending-assistant-label"><span>Thinking</span><span class="assistant-loading-dots"><i></i><i></i><i></i></span></div></article></div>`;
}

function buildAssistantMessageArticle(content, displayBlocks = [], authorName = "Assistant") {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const encodedMd = escapeHtmlAttr(content || "");
  const encodedBlocks = escapeHtmlAttr(JSON.stringify(displayBlocks || []));
  return `<div class="message-row message-row-assistant"><div class="message-meta"><span class="message-author">${escapeHtml(authorName)}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-assistant assistant-message"><div class="message-markdown md-render max-w-none text-sm" data-md="${encodedMd}" data-display-blocks="${encodedBlocks}"></div></article></div>`;
}

function removeTemporaryAssistantRows() {
  if (!dom.messageList) return;
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
  tempRows.forEach((row) => row.remove());
}

function removeLatestOptimisticUserRow() {
  const latest = getLatestOptimisticUserArticle();
  latest?.closest('.message-row')?.remove();
}

function getLatestOptimisticUserArticle() {
  const optimistic = Array.from(
    dom.messageList?.querySelectorAll('article[data-local-user="1"][data-optimistic-user="1"]') || []
  );
  return optimistic[optimistic.length - 1] || null;
}

function disconnectEventSocket() {
  if (state.eventWs) {
    try { state.eventWs.close(); } catch {}
  }
  state.eventWs = null;
  state.eventWsAgentId = null;
  state.eventWsSessionId = null;
  state.eventWsRequestId = null;
}

function isTrackableThinkingEvent(type) {
  return [
    "execution.started", "execution.completed", "execution.failed",
    "iteration_start", "llm_thinking", "tool_call", "tool_result",
    "skill_matched", "complete",
    "context_snapshot", "context_compaction_planned", "context_compaction_applied",
    // Skill mode events
    "skill_mode_start", "skill_step", "skill_session_start",
    "skill_compaction", "skill_complete",
    // Active skill contract events
    "skill_runtime_applied", "skill_contract_active",
    "skill_tool_denied", "skill_contract_cleared"
  ].includes(type);
}

// RUNTIME_EVENT_HELPER_START: normalizeRuntimeEvent
function normalizeRuntimeEvent(payload) {
  if (!payload || typeof payload !== "object") return null;

  // Runtime may wrap the event or send the event at top-level.
  const candidate = payload.event || payload.payload || payload;
  const rawType = candidate?.event_type || candidate?.type || "";
  if (!rawType) return null;

  const baseData = (candidate?.data && typeof candidate.data === "object") ? candidate.data : {};
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
    raw_type: rawType,
    lifecycle_type: lifecycleType,
    data: mergedData,
    session_id: candidate?.session_id || mergedData.session_id || "",
    request_id: candidate?.request_id || mergedData.request_id || "",
    agent_id: candidate?.agent_id || mergedData.agent_id || "",
    ts,
    state: candidate?.state || mergedData.state || "",
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
  const type = event?.type || "event";
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

function renderThinkingPanelFromClientState(chatState) {
  if (!dom.toolPanelBody) return;
  const snapshot = getActiveThinkingSnapshot(chatState);
  if (!snapshot) {
    dom.toolPanelBody.innerHTML = '<div class="portal-inline-state">Waiting for runtime events…</div>';
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
    const payload = view.args ?? view.result ?? view.output ?? null;
    const detailJson = (payload && (typeof payload === "string" || typeof payload === "object"))
      ? `<pre class="portal-panel-pre">${safe(truncateThinkingText(typeof payload === "string" ? payload : JSON.stringify(payload, null, 2), 800))}</pre>`
      : "";
    return `<div class="portal-timeline-event"><span class="portal-timeline-event-icon"><i data-lucide="${safe(view.icon)}"></i></span><div class="portal-timeline-event-body"><div class="portal-panel-title">${safe(view.title)}</div><div class="portal-panel-note">${safe(view.detail || "")}</div>${detailJson}</div></div>`;
  }).join("");

  dom.toolPanelBody.innerHTML = `
    <div class="portal-panel-stack portal-live-thinking" data-live-thinking-panel="1">
      <div class="portal-panel-section">
        <div class="portal-panel-title">Thinking Process · ${snapshot.completed ? "Completed" : "Live"}</div>
        <div class="portal-panel-note">Status: ${snapshot.completed ? "completed" : "running"}</div>
        <div class="portal-panel-note">Request ID: ${safe(snapshot.requestId || snapshot.id || "—")}</div>
        <div class="portal-panel-note">Session ID: ${safe(snapshot.sessionId || "—")}</div>
        <div class="portal-panel-note">Events: ${events.length}</div>
      </div>
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
  const isLiveRun = !!(localSnapshot && (chatState.activeRequest || !localSnapshot.completed));
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

function mergeThinkingEvents(primaryEvents, secondaryEvents) {
  const first = Array.isArray(primaryEvents) ? primaryEvents : [];
  const second = Array.isArray(secondaryEvents) ? secondaryEvents : [];
  const merged = [];
  const seen = new Set();
  const add = (event) => {
    if (!event || typeof event !== "object") return;
    const data = (event.data && typeof event.data === "object") ? event.data : {};
    const requestId = event.request_id || data.request_id || "";
    const sessionId = event.session_id || data.session_id || "";
    const key = `${event.type || ""}|${requestId}|${sessionId}|${JSON.stringify(data)}`;
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(event);
  };
  first.forEach(add);
  second.forEach(add);
  return merged;
}

function normalizePayloadThinkingEvents(events) {
  if (!Array.isArray(events)) return [];
  return events
    .map((event) => normalizeRuntimeEvent(event) || event)
    .filter((event) => event && typeof event === "object");
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
  const activeRequestId = chatState.activeRequest?.clientRequestId || "";
  const socketRequestId = socketCtx.requestId || "";
  const currentRequestId = socketRequestId || activeRequestId;
  if (entry.request_id && currentRequestId && entry.request_id !== currentRequestId) return;
  const eventMatchesActiveRequest = Boolean(
    entry.request_id && activeRequestId && entry.request_id === activeRequestId
  );
  const eventMatchesSocketRequest = Boolean(
    entry.request_id && socketRequestId && entry.request_id === socketRequestId
  );
  const eventMatchesLastCompletedRequest = Boolean(
    entry.request_id
    && chatState.lastCompletedRequestId
    && entry.request_id === chatState.lastCompletedRequestId
  );
  if (
    entry.session_id
    && !currentSessionId
    && !(eventMatchesActiveRequest || eventMatchesSocketRequest || eventMatchesLastCompletedRequest)
  ) {
    // Drop unmatched stale events when no current session is bound.
    // Otherwise they can recreate inflightThinking and cause false busy/session pollution.
    return;
  }

  // Handle additive runtime state fields while keeping existing event semantics.
  const isCompletion = isCompletionRuntimeState(entry.state);
  const type = entry.type;
  const lifecycleType = entry.lifecycle_type;

  if (!isTrackableThinkingEvent(type) && !lifecycleType && !isCompletion) return;

  const isLateEventForCompletedRequest = Boolean(
    !chatState.activeRequest
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
    };
  }
  if (entry.request_id && !chatState.inflightThinking.requestId) {
    chatState.inflightThinking.requestId = entry.request_id;
    chatState.inflightThinking.id = chatState.inflightThinking.id || entry.request_id;
  }
  if (entry.session_id && !chatState.inflightThinking.sessionId) {
    chatState.inflightThinking.sessionId = entry.session_id;
  }
  if (
    entry.session_id
    && !chatState.sessionId
    && (eventMatchesActiveRequest || eventMatchesSocketRequest)
  ) {
    chatState.sessionId = entry.session_id;
    state.agentSessionIds.set(currentAgentId, entry.session_id);
    if (currentAgentId === state.selectedAgentId && dom.chatSessionId) {
      dom.chatSessionId.value = entry.session_id;
    }
  }

  if (!chatState.inflightThinking) return;

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
  if (isThinkingPanelActiveForAgent(currentAgentId)) {
    scheduleThinkingPanelRefresh(currentAgentId);
  }

  if (type === "execution.completed" || type === "execution.failed" || type === "skill_complete" || isCompletion || lifecycleType === "execution.completed" || lifecycleType === "execution.failed") {
    chatState.inflightThinking.completed = true;
    chatState.lastThinkingSnapshot = { ...chatState.inflightThinking, completed: true };
  }
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
  const query = params.toString();
  const wsUrl = `${protocol}//${window.location.host}/a/${agentId}/api/events${query ? `?${query}` : ""}`;
  const ws = new WebSocket(wsUrl);
  state.eventWs = ws;
  state.eventWsAgentId = agentId;
  state.eventWsSessionId = session || "";
  state.eventWsRequestId = requestId || "";
  ws.onmessage = (event) => handleAgentEventMessage(event.data, { agentId, sessionId: session, requestId });
  ws.onclose = () => {
    if (state.eventWs === ws) disconnectEventSocket();
  };
  ws.onerror = () => {};
}

function ensureEventSocketForSelectedAgent() {
  if (typeof ensureEventSocketForAgent !== "function") return;
  const agentId = state.selectedAgentId;
  if (!agentId) return;
  const chatState = ensureChatState(agentId);
  const requestId = chatState?.activeRequest?.clientRequestId || "";
  ensureEventSocketForAgent(agentId, currentSessionIdForSelectedAgent(), requestId || null);
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
  dom.chatStatus.textContent = text;
  dom.chatStatus.className = `portal-statusline${isError ? " is-error" : ""}`;
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
    return md.render(normalizeMarkdownText("(empty response)"));
  }
  const html = parsedBlocks.map((block) => renderSingleDisplayBlock(block)).join("");
  if (html) return html;
  if (isMeaningfulText(fallbackMarkdown)) {
    return md.render(normalizeMarkdownText(fallbackMarkdown));
  }
  return md.render(normalizeMarkdownText("(empty response)"));
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

function setChatSubmittingForAgent(agentId, active) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.isSubmitting = !!active;
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
  chatState.draftAttachmentsValue = document.getElementById("chat-attachments")?.value || "";
}

function restoreComposerForAgent(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  if (dom.chatInput) dom.chatInput.value = chatState.draftText || "";
  const attachmentsInput = document.getElementById("chat-attachments");
  if (attachmentsInput) attachmentsInput.value = chatState.draftAttachmentsValue || "";
  syncChatInputHeight();
  renderInputPreview();
  renderComposerModelSelectorForAgent(agentId);
  if (agentId === state.selectedAgentId) syncSelectedAgentChatActionControls();
}

function markAgentUnread(agentId, status) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.unreadCount += 1;
  chatState.backgroundStatus = status || chatState.backgroundStatus || "completed";
}

function clearAgentUnread(agentId) {
  const chatState = ensureChatState(agentId);
  if (!chatState) return;
  chatState.unreadCount = 0;
  chatState.backgroundStatus = "";
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
      if (chatState?.isSubmitting) runtimeBadge = '<span class="portal-agent-chat-badge is-running">running</span>';
      else if (chatState?.backgroundStatus === "completed") runtimeBadge = '<span class="portal-agent-chat-badge is-completed">completed</span>';
      else if (chatState?.backgroundStatus === "error") runtimeBadge = '<span class="portal-agent-chat-badge is-error">error</span>';
      row.innerHTML = `
        <div class="portal-agent-row-head">
          <span class="portal-agent-name">${safe(agent.name)}</span>
          <span class="portal-agent-row-badges">${runtimeBadge}${unreadBadge}${sharedBadge}</span>
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

  // Build repo/branch section if present
  let repoSection = '';
  if (agent.repo_url) {
    const branch = agent.branch || state.agentDefaults?.default_branch || "";
    const branchLine = branch
      ? `<div class="portal-detail-subtle">Branch: <span class="portal-detail-value">${safe(branch)}</span></div>`
      : "";
    repoSection = `
      <div class="portal-detail-section">
        <div class="portal-detail-label">Repository</div>
        <code class="portal-detail-code">${safe(agent.repo_url)}</code>
        ${branchLine}
        <div id="agent-git-commit" class="portal-detail-subtle">Loading commit...</div>
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
  if (agent.repo_url) {
    fetchGitInfo(agent.id);
  }
}

async function fetchGitInfo(agentId) {
  const commitEl = document.getElementById("agent-git-commit");
  if (!commitEl) return;

  // Check if still viewing same agent (prevent stale response overwriting wrong agent)
  if (state.selectedAgentId !== agentId) return;

  try {
    const data = await api(`/a/${agentId}/api/git-info`);
    if (data.commit_id) {
      const shortCommit = data.commit_id.substring(0, 7);
      commitEl.textContent = 'Commit: ';

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
      commitEl.textContent = "Commit: Not available";
    } else if (data.status === 'error') {
      commitEl.className = "portal-inline-error";
      commitEl.textContent = "Git info unavailable";
    } else {
      commitEl.className = "portal-detail-subtle";
      commitEl.textContent = "Assistant not running";
    }
  } catch (e) {
    commitEl.className = "portal-inline-error";
    commitEl.textContent = "Failed to load commit";
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

async function selectAgentById(agentId) {
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
  state.cachedMentionFiles = [];
  state.selectedSuggestionIndex = -1;
  disconnectEventSocket();

  if (dom.chatAgentId) dom.chatAgentId.value = agentId || "";
  syncHiddenSessionInputFromState();
  restoreComposerForAgent(agentId);
  clearAgentUnread(agentId);
  clearMessageListToWelcome();

  await setActiveNavSection("assistants", { toggleIfSame: false });
  renderAgentList();
  await syncSelectedAgentState();
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

async function refreshAll({ preserveLayout = false } = {}) {
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

  const available = new Set(state.mineAgents.map((agent) => agent.id));
  const stored = localStorage.getItem(LAST_AGENT_STORAGE_KEY) || "";
  if (state.selectedAgentId && !available.has(state.selectedAgentId)) state.selectedAgentId = null;
  if (!state.selectedAgentId && stored && available.has(stored)) state.selectedAgentId = stored;
  if (!state.selectedAgentId && state.mineAgents.length) state.selectedAgentId = state.mineAgents[0].id;
  if (state.selectedAgentId) {
    localStorage.setItem(LAST_AGENT_STORAGE_KEY, state.selectedAgentId);
    window.selectedAgentId = state.selectedAgentId;
  }

  // Update owner-only button visibility after restoring last agent
  updateOwnerOnlyButtons(state.selectedAgentId);

  await setActiveNavSection("assistants", {
    toggleIfSame: false,
    preserveCollapsed: preserveLayout,
  });
  renderAgentList();
  await syncSelectedAgentState();
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
  // Runtime directly consumes image attachments in this array. Non-image files take a different path:
  // upload -> parse -> runtime session file_context keyed by stable session_id.
  // Keep auto-parse + session_id flow intact for documents even if they also appear in local preview UI.
  const uploadedFileIds = chatState.pendingFiles
    .filter((pf) => pf.file_id && pf.status === "uploaded")
    .map((pf) => pf.file_id);
  if (uploadedFileIds.length) return uploadedFileIds;
  const existingAttachments = document.getElementById("chat-attachments")?.value;
  if (!existingAttachments) return [];
  try {
    return JSON.parse(existingAttachments);
  } catch {
    return [];
  }
}

async function submitChatForSelectedAgent() {
  const agentIdAtSend = state.selectedAgentId;
  const chatState = ensureChatState(agentIdAtSend);
  if (!agentIdAtSend || !chatState) return;
  if (!guardNoActiveChatRequestForAgent(agentIdAtSend, "send another message")) return;
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
  const messageAtSend = dom.chatInput?.value?.trim() || "";
  if (!messageAtSend) return;
  const attachmentsAtSend = buildAttachmentsFromChatState(agentIdAtSend, chatState);
  const sessionIdAtSend = ensureChatSessionId(agentIdAtSend);
  const clientRequestId = `portal-chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const requestCtx = {
    agentId: agentIdAtSend,
    sessionIdAtSend,
    message: messageAtSend,
    attachments: attachmentsAtSend,
    clientRequestId,
    startedAt: Date.now(),
    backupMessage: messageAtSend,
    backupFiles: [...chatState.pendingFiles],
  };

  maybeRequestNotificationPermission();
  const modelOverride = (chatState.modelOverride || dom.chatModelSelect?.value || "").trim();
  const defaultModel = (chatState.profileDefaultModel || "").trim();
  const requestBody = {
    message: messageAtSend,
    session_id: sessionIdAtSend || undefined,
    attachments: attachmentsAtSend,
    client_request_id: clientRequestId,
    ...(modelOverride && modelOverride !== defaultModel ? { model_override: modelOverride } : {}),
  };
  chatState.didAppendAttachmentHistoryForPendingSend = false;
  removeWelcomeMessageIfPresent();
  removeTemporaryAssistantRows();
  hideSuggest();
  if (agentIdAtSend === state.selectedAgentId && dom.messageList) {
    const displayAttachments = chatState.pendingFiles.map((pf) => ({
      name: pf.file?.name || pf.name || "",
      type: pf.isImage ? "image" : "file",
      previewUrl: pf.previewUrl,
      url: pf.uploadedData?.url,
    }));
    dom.messageList.insertAdjacentHTML("beforeend", buildUserMessageArticle(messageAtSend, displayAttachments));
    dom.messageList.insertAdjacentHTML("beforeend", buildPendingAssistantArticle());
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
  chatState.attachmentHistory.push(attachmentsAtSend);
  chatState.didAppendAttachmentHistoryForPendingSend = true;
  chatState.pendingFiles = [];
  renderInputPreview();
  if (dom.chatInput) dom.chatInput.value = "";
  resetChatInputHeight();
  const attachmentsInput = document.getElementById("chat-attachments");
  if (attachmentsInput) attachmentsInput.value = "";
  setChatStatus("Sending...");
  chatState.activeRequest = requestCtx;
  setChatSubmittingForAgent(agentIdAtSend, true);

  try {
    const resp = await fetch(`/a/${agentIdAtSend}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    if (!resp.ok) throw new Error(await handleErrorResponse(resp));
    const payload = await resp.json();
    await handleAgentChatSuccess(agentIdAtSend, requestCtx, payload);
  } catch (error) {
    handleAgentChatFailure(agentIdAtSend, requestCtx, error);
  }
}

async function handleAgentChatSuccess(agentIdAtSend, requestCtx, payload) {
  const chatState = ensureChatState(agentIdAtSend);
  if (!chatState?.activeRequest || chatState.activeRequest.clientRequestId !== requestCtx.clientRequestId) return;
  const normalizeEvents = (typeof normalizePayloadThinkingEvents === "function")
    ? normalizePayloadThinkingEvents
    : (events) => Array.isArray(events) ? events.filter((event) => event && typeof event === "object") : [];
  const payloadThinkingEvents = [
    ...normalizeEvents(payload?.events || []),
    ...normalizeEvents(payload?.runtime_events || []),
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
  chatState.activeRequest = null;
  chatState.lastCompletedRequestId = payload.request_id || requestCtx.clientRequestId;
  chatState.didAppendAttachmentHistoryForPendingSend = false;
  chatState.inflightThinking = null;
  chatState.pendingThinkingEvents = null;
  setChatSubmittingForAgent(agentIdAtSend, false);
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

  removeTemporaryAssistantRows();
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
  const assistantHtml = buildAssistantMessageArticle(
    payload.response || "",
    payload.display_blocks || [],
    getSelectedAssistantDisplayName(payload.author_name || "Assistant"),
  );
  dom.messageList?.insertAdjacentHTML("beforeend", assistantHtml);
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
  if (!chatState?.activeRequest || chatState.activeRequest.clientRequestId !== requestCtx.clientRequestId) return;
  const restoredMessage = requestCtx.backupMessage || "";
  const restoredFiles = Array.isArray(requestCtx.backupFiles) ? requestCtx.backupFiles : [];
  const restoredAttachmentsValue = JSON.stringify(requestCtx.attachments || []);
  const shouldRollbackAttachmentHistory =
    !!chatState.didAppendAttachmentHistoryForPendingSend &&
    Array.isArray(chatState.attachmentHistory) &&
    chatState.attachmentHistory.length > 0;
  chatState.activeRequest = null;
  chatState.didAppendAttachmentHistoryForPendingSend = false;
  const errorMsg = error?.message || "Send failed";
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
  setChatSubmittingForAgent(agentIdAtSend, false);
  if (state.selectedAgentId !== agentIdAtSend) {
    if (shouldRollbackAttachmentHistory) {
      chatState.attachmentHistory.pop();
    }
    chatState.draftText = restoredMessage;
    chatState.pendingFiles = restoredFiles;
    chatState.draftAttachmentsValue = restoredAttachmentsValue;
    chatState.inflightThinking = null;
    chatState.pendingThinkingEvents = null;
    chatState.backgroundStatus = "error";
    chatState.needsReload = false;
    markAgentUnread(agentIdAtSend, "error");
    renderAgentList();
    const agentName = state.mineAgents.find((a) => a.id === agentIdAtSend)?.name || agentIdAtSend;
    notifyAgentCompletion(agentIdAtSend, agentName, "failed", errorMsg);
    return;
  }
  removeTemporaryAssistantRows();
  removeLatestOptimisticUserRow();
  if (shouldRollbackAttachmentHistory) {
    chatState.attachmentHistory.pop();
  }
  chatState.pendingFiles = restoredFiles;
  if (dom.chatInput) dom.chatInput.value = restoredMessage;
  const attachmentsInput = document.getElementById("chat-attachments");
  if (attachmentsInput) attachmentsInput.value = restoredAttachmentsValue;
  chatState.draftAttachmentsValue = restoredAttachmentsValue;
  renderInputPreview();
  syncChatInputHeight();
  if (typeof isThinkingPanelActiveForAgent === "function" && isThinkingPanelActiveForAgent(agentIdAtSend)) {
    if (typeof renderThinkingPanelFromClientState === "function") renderThinkingPanelFromClientState(chatState);
  }
  chatState.inflightThinking = null;
  setChatStatus(errorMsg, true);
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
    `<button type="button" data-i="${index}" class="portal-suggest-item"><div class="portal-suggest-title">${safe(item.label || item.title || "")}</div><div class="portal-suggest-desc">${safe(item.desc || "")}</div></button>`
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

function insertFileReference(fileIdOrRef) {
  if (!dom.chatInput) return;
  const raw = String(fileIdOrRef || "").trim();
  if (!raw) return;

  const token = raw.startsWith("@file_")
    ? raw
    : `@file_${raw.slice(0, 8)}`;
  const start = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
  const end = dom.chatInput.selectionEnd ?? dom.chatInput.value.length;
  const before = dom.chatInput.value.slice(0, start);
  const after = dom.chatInput.value.slice(end);
  const prefix = before && !/\s$/.test(before) ? " " : "";
  const suffix = after && !/^\s/.test(after) ? " " : "";
  dom.chatInput.setRangeText(`${prefix}${token}${suffix}`, start, end, "end");
  dom.chatInput.dispatchEvent(new Event("input", { bubbles: true }));
  dom.chatInput.focus();
  syncChatInputHeight();
  maybeShowSuggest();
}

// Fetch file preview and update pendingFile
async function maybeShowSuggest() {
  if (!dom.chatInput) return;
  const requestSeq = ++state.suggestRequestSeq;

  const text = dom.chatInput.value;
  const cursor = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
  const before = text.slice(0, cursor);
  const slash = before.match(/(^|\s)\/([^\s/]*)$/);
  const at = before.match(/(^|\s)@([^\s@]*)$/);

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

  if (at) {
    const mentionAgentKey = state.selectedAgentId ?? "__global__";
    if (state.cachedMentionFilesByAgent.has(mentionAgentKey)) {
      state.cachedMentionFiles = state.cachedMentionFilesByAgent.get(mentionAgentKey) || [];
    } else {
      const requestAgentKey = mentionAgentKey;
      try {
        const data = await agentApi("/api/files/list");
        const mentionFiles = (data.files || []).map((item) => ({
          title: `@file_${item.file_id.slice(0, 8)}`,
          desc: item.filename,
          full: `@file_${item.file_id}`,
        }));
        state.cachedMentionFilesByAgent.set(requestAgentKey, mentionFiles);
        if ((state.selectedAgentId ?? "__global__") === requestAgentKey) {
          state.cachedMentionFiles = mentionFiles;
        }
      } catch {
        if (!state.cachedMentionFilesByAgent.has(requestAgentKey)) {
          state.cachedMentionFilesByAgent.set(requestAgentKey, []);
        }
        if ((state.selectedAgentId ?? "__global__") === requestAgentKey) {
          state.cachedMentionFiles = [];
        }
      }
    }
    if (requestSeq !== state.suggestRequestSeq) return;
    const nowCursor = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
    const nowBefore = dom.chatInput.value.slice(0, nowCursor);
    const nowAt = nowBefore.match(/(^|\s)@([^\s@]*)$/);
    if (!nowAt) {
      hideSuggest();
      return;
    }

    const mentionQuery = (nowAt[2] || "").toLowerCase();
    const filteredMentionFiles = !mentionQuery
      ? state.cachedMentionFiles
      : state.cachedMentionFiles.filter((item) => {
        const haystack = [item.title, item.desc, item.full].map((v) => String(v || "").toLowerCase());
        return haystack.some((v) => v.includes(mentionQuery));
      });
    if (!filteredMentionFiles.length) {
      hideSuggest();
      return;
    }

    showSuggest(filteredMentionFiles, (item) => {
      const pickCursor = dom.chatInput.selectionStart ?? dom.chatInput.value.length;
      const pickBefore = dom.chatInput.value.slice(0, pickCursor);
      const pickAt = pickBefore.match(/(^|\s)@([^\s@]*)$/);
      if (!pickAt) return;
      // Replace from the start of "@" token to cursor while preserving preceding whitespace.
      const start = pickAt.index + pickAt[1].length;
      dom.chatInput.setRangeText(`${item.full} `, start, pickCursor, "end");
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
      "uploads",
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
    if (panelKey === "uploads") {
      await openMyUploads();
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
  const addRuntimeProfileBtn = dom.addRuntimeProfileBtn;
  const addAutomationBtn = dom.addAutomationBtn;
  if (addAgentBtn) addAgentBtn.classList.add("hidden");
  if (addBundleBtn) addBundleBtn.classList.add("hidden");
  if (refreshBundlesBtn) refreshBundlesBtn.classList.add("hidden");
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
  } else if (state.activeNavSection === "tasks") {
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
    } else if (state.activeNavSection === "tasks") {
    if (addTaskBtn) addTaskBtn.classList.remove("hidden");
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

async function setActiveNavSection(section, { toggleIfSame = true, preserveCollapsed = false } = {}) {
  const previousSection = state.activeNavSection;
  const sidebarWasCollapsed = state.secondaryPaneCollapsed;
  const validSections = new Set(["assistants", "bundles", "tasks", "runtime-profiles", "automations"]);
  if (!validSections.has(section)) return;

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

  if (state.secondaryPaneCollapsed) return;

  const didSwitchSection = section !== previousSection;
  const didRevealPane = sidebarWasCollapsed && !state.secondaryPaneCollapsed;
  const shouldRefreshVisibleSection = didSwitchSection || didRevealPane;

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
    if (
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
    await refreshRuntimeProfileList({ preserveSelection: true });
    if (state.activeNavSection === "runtime-profiles" && !state.secondaryPaneCollapsed) {
      const defaultProfile = state.runtimeProfiles.find((item) => item.is_default);
      const preferredProfile = defaultProfile || state.runtimeProfiles[0] || null;
      let targetProfileId = null;
      if (didSwitchSection || didRevealPane) {
        targetProfileId = preferredProfile ? preferredProfile.id : null;
        state.selectedRuntimeProfileId = targetProfileId;
      }

      if (targetProfileId) {
        await loadRuntimeProfilePanelContent(targetProfileId);
      } else {
        renderWorkspaceDetailPlaceholder("No runtime profiles found.", "runtime-profiles-placeholder");
      }
    }
  }

  if (state.activeNavSection === "tasks" && shouldRefreshVisibleSection) {
    await refreshMyTasks();
    if (
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
    if (first && !state.selectedAutomationRuleId) {
      await openAutomationRulePanel(first.id);
    } else if (!first) {
      renderWorkspaceDetailPlaceholder("No automations found.", "automations-placeholder");
    }
  }
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
      state.selectedBundleKey = key;
      renderRequirementBundleList();
      await setActiveNavSection("bundles", { toggleIfSame: false });
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

async function openRequirementBundleInMain(bundleRef = null) {
  if (!dom.workspaceDetailContent) return;
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  dom.workspaceDetailContent.innerHTML = '<div class="portal-inline-state">Loading bundles…</div>';
  try {
    let path = "/app/requirement-bundles/panel";
    if (bundleRef) {
      const params = new URLSearchParams({ repo: bundleRef.repo, path: bundleRef.path, branch: bundleRef.branch });
      path = `/app/requirement-bundles/open?${params.toString()}`;
      state.selectedBundleKey = bundleKeyFromRef(bundleRef);
      renderRequirementBundleList();
    }
    await htmx.ajax("GET", path, { target: "#workspace-detail-content", swap: "innerHTML" });
    dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
    syncMainHeader();
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
      state.selectedTaskId = task.id;
      renderTaskNavList();
      await setActiveNavSection("tasks", { toggleIfSame: false });
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

async function openTaskDetailInMain(taskId) {
  if (!dom.workspaceDetailContent) return;
  await setActiveNavSection("tasks", { toggleIfSame: false });
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
  } catch (error) {
    dom.workspaceDetailContent.dataset.workspaceState = "task-detail";
    dom.workspaceDetailContent.innerHTML = `<div class="portal-inline-state is-error">Failed: ${safe(error.message)}</div>`;
  }
}

async function returnFromTaskDetailToSidebar() {
  await setActiveNavSection("tasks", { toggleIfSame: false });
  state.selectedTaskId = null;
  renderTaskNavList();
  renderWorkspaceDetailPlaceholder("Select a task from the left sidebar.", "tasks-placeholder");
  syncMainHeader();
}

function renderChatHistory(messages, metadata = {}) {
  if (!dom.messageList) return;
  const selectedChatState = getChatState();

  if (!messages.length) {
    if (selectedChatState) selectedChatState.attachmentHistory = [];
    clearMessageListToWelcome();
    return;
  }

  dom.messageList.innerHTML = "";
  if (selectedChatState) selectedChatState.attachmentHistory = [];
  messages.forEach((message) => {
    if (message.role !== "user" && message.role !== "assistant") return;
    const isUser = message.role === "user";
    let timeStr = "";
    if (message.timestamp) {
      try {
        timeStr = new Date(message.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (e) {}
    }

    const container = document.createElement("div");
    container.className = `message-row ${isUser ? "message-row-user" : "message-row-assistant"}`;

    const header = document.createElement("div");
    header.className = `message-meta${isUser ? " message-meta-user" : ""}`;
    const roleLabel = document.createElement("span");
    roleLabel.className = "message-author";
    roleLabel.textContent = getHistoryMessageDisplayName(message, isUser);
    header.appendChild(roleLabel);
    if (timeStr) {
      const timeLabel = document.createElement("span");
      timeLabel.className = "message-timestamp";
      timeLabel.textContent = timeStr;
      header.appendChild(timeLabel);
    }
    container.appendChild(header);

    const article = document.createElement("article");
    article.className = `message-surface ${isUser ? "message-surface-user" : "message-surface-assistant assistant-message"}`;

    if (isUser) {
      article.dataset.localUser = "1";
      if (message.id) article.dataset.messageId = message.id;
      const content = document.createElement("div");
      content.className = "message-body whitespace-pre-wrap text-sm";
      content.textContent = message.content || "";
      article.appendChild(content);

      const normalizedAttachments = Array.isArray(message.attachments) ? message.attachments : [];
      if (selectedChatState) selectedChatState.attachmentHistory.push([...normalizedAttachments]);
      if (normalizedAttachments.length > 0) {
        const attachmentDiv = document.createElement("div");
        attachmentDiv.className = "message-attachments";
        attachmentDiv.dataset.attachments = JSON.stringify(normalizedAttachments);
        article.dataset.attachments = JSON.stringify(normalizedAttachments);
        normalizedAttachments.forEach((attachment) => {
          const isAttachmentObject = !!attachment && typeof attachment === "object" && !Array.isArray(attachment);
          const attachmentType = isAttachmentObject ? String(attachment.type || "").toLowerCase() : "";
          const imageUrl = isAttachmentObject ? (attachment.url || attachment.previewUrl || "") : "";
          const fileId = isAttachmentObject
            ? String(attachment.file_id || attachment.fileId || attachment.id || attachment.filename || "attachment")
            : String(attachment || "");
          const fileName = isAttachmentObject
            ? String(attachment.name || attachment.filename || attachment.file_name || fileId || "attachment")
            : fileId;

          if (attachmentType === "image" && imageUrl) {
            const img = document.createElement("img");
            img.src = imageUrl;
            img.className = "message-attachment-thumb";
            img.alt = fileName;
            img.dataset.fileId = fileId;
            attachmentDiv.appendChild(img);
            return;
          }

          const fileChip = document.createElement("div");
          fileChip.className = "message-attachment-file";
          const metaText = formatAttachmentMetaText(attachment);
          const baseText = `📄 ${fileName || fileId || "attachment"}`;
          fileChip.textContent = metaText ? `${baseText} · ${metaText}` : baseText;
          attachmentDiv.appendChild(fileChip);
        });
        article.appendChild(attachmentDiv);
      }
    } else {
      const content = document.createElement("div");
      content.className = "message-markdown md-render max-w-none text-sm";
      if (Array.isArray(message.display_blocks) && message.display_blocks.length) {
        content.dataset.displayBlocks = JSON.stringify(message.display_blocks);
      }
      content.dataset.md = message.content || "";
      article.appendChild(content);
    }

    container.appendChild(article);
    dom.messageList.appendChild(container);
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

  const data = await agentApiFor(agentId, `/api/sessions/${encodeURIComponent(normalized)}`);
  updateAgentSession(agentId, normalized);
  const latestChatState = ensureChatState(agentId);
  if (latestChatState) latestChatState.needsReload = false;
  if (render) {
    // Ensure agent name is set
    if (!state.selectedAgentName && state.selectedAgentId) {
      const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
      state.selectedAgentName = agent?.name || null;
    }
    renderChatHistory(data.messages || [], data.metadata || {});
    addEditButtonsToMessages();
    setChatStatus(`Loaded session ${normalized}`);
    if (agentId === state.selectedAgentId && !hasActiveChatRequestForAgent(agentId)) {
      const recoveryNotice = deriveSessionRecoveryNotice(data.metadata || {});
      if (recoveryNotice) {
        setChatStatus(recoveryNotice.message, recoveryNotice.level === "error");
      }
    }
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
    const resp = await fetch(`/a/${agentId}/api/sessions/${encodeURIComponent(normalizedSessionId)}`, {
      method: "DELETE",
    });
    if (!resp.ok) throw new Error(await handleErrorResponse(resp));

    if (normalizedSessionId === currentSessionIdForAgent(agentId)) {
      updateAgentSession(agentId, "");
      if (agentId === state.selectedAgentId) {
        const chatState = getChatState();
        if (chatState) chatState.inflightThinking = null;
        removeTemporaryAssistantRows();
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

async function loadServerFiles(path) {
  setToolPanel("Server Files", '<div class="portal-inline-state">Loading files…</div>', "server-files");

  try {
    const hasPath = typeof path === 'string' && path.length > 0;
    const endpoint = hasPath
      ? `/api/server-files?path=${encodeURIComponent(path)}`
      : '/api/server-files';
    const data = await agentApi(endpoint);
    const items = data.items || [];
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
      const safePath = item.path.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      return (
        `<div class="portal-file-row file-item" data-path="${safePath}" data-is-dir="${item.is_dir}">` +
          `<input type="checkbox" class="file-checkbox portal-file-checkbox" data-path="${safePath}" data-is-dir="${item.is_dir}" aria-label="${escapeHtml(item.name)}">` +
          `<div class="portal-file-name-cell name-cell" data-path="${safePath}" data-is-dir="${item.is_dir}">` +
            `<span class="portal-file-icon">${icon}</span>` +
            `<span class="portal-file-name">${escapeHtml(item.name)}</span>` +
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


async function openMyUploads() {
  if (!state.selectedAgentId) return;


  setToolPanel("Select Source", '<div class="portal-inline-state">Loading files…</div>', "uploads");

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/files/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("Select Source", `Failed: ${safe(error.message)}`, "uploads");
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

function normalizeInstanceInputs(root, group) {
  const container = root?.querySelector(`[data-instance-container="${group}"]`);
  const countInput = root?.querySelector(`[data-instance-count="${group}"]`);
  if (!container || !countInput) return;

  const items = Array.from(container.querySelectorAll(`[data-instance-item="${group}"]`));
  items.forEach((item, idx) => {
    const title = item.querySelector(".portal-settings-instance-title");
    if (title) title.textContent = `Instance ${idx + 1}`;
    item.querySelectorAll("input[data-field]").forEach((input) => {
      const field = input.dataset.field;
      input.name = `${group}_instances_${idx}_${field}`;
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

  const usernamePasswordHtml = `<input type="text" data-field="username" value="" placeholder="Email" class="portal-form-input" /><input type="password" data-field="password" value="" placeholder="Password" class="portal-form-input" />`;

  div.innerHTML = `
    <div class="portal-settings-instance-head">
      <span class="portal-settings-instance-title">Instance</span>
      <button type="button" class="portal-instance-remove" data-action="remove-instance" data-group="${group}">Remove</button>
    </div>
    <div class="portal-panel-grid cols-2"><input type="text" data-field="name" value="" placeholder="Name" class="portal-form-input" /><input type="text" data-field="url" value="" placeholder="URL (e.g. https://yourcompany.atlassian.net)" class="portal-form-input" /></div>
    <div class="portal-panel-grid cols-2">${usernamePasswordHtml}</div>
    <div class="portal-panel-grid cols-2"><input type="password" data-field="token" value="" placeholder="API Token" class="portal-form-input" />${projectHtml}</div>
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

function getManagedGithubBaseUrl(root) {
  const input = root?.querySelector('input[name="github_base_url"]');
  return (input?.value || "").trim();
}

function finishCopilotAuthWithMessage(root, message) {
  stopCopilotPolling(root);
  const statusText = root?.querySelector("#copilot_status_text");
  if (statusText) statusText.textContent = message || "Authorization failed";
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

  const copilotBtn = root.querySelector("#copilot_auth_btn");
  const authStatus = root.querySelector("#copilot_auth_status");
  const isCopilot = provider === "github_copilot";
  if (copilotBtn) copilotBtn.classList.toggle("hidden", !isCopilot);
  if (authStatus && !isCopilot) authStatus.classList.add("hidden");
  if (!isCopilot) stopCopilotPolling(root);
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

async function startCopilotAuth(root) {
  const authStatus = root.querySelector("#copilot_auth_status");
  const instructions = root.querySelector("#copilot_instructions");
  const statusText = root.querySelector("#copilot_status_text");
  const verifyLink = root.querySelector("#copilot_verify_link");
  const deviceLink = root.querySelector("#copilot_device_link");
  const userCode = root.querySelector("#copilot_user_code");
  const timer = root.querySelector("#copilot_timer");

  stopCopilotPolling(root);
  const authBase = getManagedCopilotAuthBase(root);
  const githubBaseUrl = getManagedGithubBaseUrl(root);

  try {
    const response = await fetch(`${authBase}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ github_base_url: githubBaseUrl }),
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

    if (authStatus) authStatus.classList.remove("hidden");
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
    if (statusText) statusText.textContent = "Waiting for authorization...";

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
        if (statusText) statusText.textContent = "Authorized successfully!";
        if (instructions) instructions.classList.add("hidden");
        const apiInput = root.querySelector('input[name="llm_api_key"]');
        if (apiInput && check.token) {
          apiInput.value = check.token;
          markManagedSectionTouched(root, "llm");
        }
        showToast("Copilot token inserted into API Key field. Save to persist.");
      } else if (check.status === "expired" || check.status === "declined" || check.status === "failed") {
        finishCopilotAuthWithMessage(root, check.message || check.error || "Authorization failed");
      } else {
        finishCopilotAuthWithMessage(root, `Authorization check failed: unknown status ${safe(check.status)}`);
      }
    }, (Number(data.interval) || 5) * 1000);
  } catch (error) {
    showToast(`Copilot authorization failed: ${safe(error.message)}`);
    finishCopilotAuthWithMessage(root, `Copilot authorization failed: ${safe(error.message)}`);
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
    if (event.target.closest("#copilot_auth_btn")) {
      event.preventDefault();
      await startCopilotAuth(root);
      return;
    }
    if (event.target.closest("#copilot_copy_btn")) {
      event.preventDefault();
      const code = root.querySelector("#copilot_user_code")?.textContent || "";
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
    removeTemporaryAssistantRows();
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
    chatState.activeRequest = null;
  }
  removeTemporaryAssistantRows();
  clearMessageListToWelcome();
  setChatSubmitting(false);
  resetChatInputHeight();
  setChatStatus("New chat started");
  dom.chatInput?.focus();
}

// ===== misc actions =====
async function action(path, method = "POST", needsConfirm = false) {
  if (needsConfirm && !confirm("Please confirm this action.")) return;
  await api(path, { method });
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

function applyCreateAgentDefaults(form, defaults) {
  if (!form?.elements) return;
  const repoInput = form.elements["repo_url"];
  if (repoInput) {
    const repoDefault = defaults?.default_repo_url || "";
    repoInput.value = repoDefault;
    repoInput.defaultValue = repoDefault;
  }
  const branchInput = form.elements["branch"];
  if (branchInput) {
    const branchDefault = defaults?.default_branch || "";
    branchInput.value = branchDefault;
    branchInput.defaultValue = branchDefault;
    branchInput.placeholder = branchDefault ? `Configured default branch (${branchDefault})` : "Configured default branch";
  }
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

async function loadRuntimeProfilePanelContent(profileId) {
  if (!profileId) return;
  state.selectedRuntimeProfileId = profileId;
  renderRuntimeProfileList();
  await htmx.ajax("GET", `/app/runtime-profiles/${encodeURIComponent(profileId)}/panel`, { target: "#workspace-detail-content", swap: "innerHTML" });
  if (typeof initializeManagedSettingsPanels === "function") initializeManagedSettingsPanels();
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "runtime-profile-detail";
  syncMainHeader();
}

async function openRuntimeProfileInMain(profileId, { ensureSection = true } = {}) {
  if (!profileId) return;
  if (ensureSection) {
    await setActiveNavSection("runtime-profiles", { toggleIfSame: false });
  }
  await loadRuntimeProfilePanelContent(profileId);
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

async function openAutomationRulePanel(ruleId) {
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
        <div class="portal-inline-state">Review target: <strong>${safe(`${trigger.review_target_type || "-"} / ${trigger.review_target || "-"}`)}</strong></div>
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
        <label class="portal-form-label"><span class="portal-form-label">Agent</span><select class="portal-form-select" name="target_agent_id" required>${agentOptions}</select></label>
        <label class="portal-form-label"><span class="portal-form-label">Owner</span><input class="portal-form-input" name="owner" required /></label>
        <label class="portal-form-label"><span class="portal-form-label">Repo</span><input class="portal-form-input" name="repo" required /></label>
        <label class="portal-form-label"><span class="portal-form-label">Review target type</span><select class="portal-form-select" name="review_target_type"><option value="user">user</option><option value="team">team</option></select></label>
        <label class="portal-form-label"><span class="portal-form-label">Review target</span><input class="portal-form-input" name="review_target" required /></label>
        <label class="portal-form-label"><span class="portal-form-label">Interval seconds</span><input class="portal-form-input" name="interval_seconds" type="number" value="60" min="30" max="3600" /></label>
        <button class="portal-btn is-primary" type="submit">Create</button>
      </form>
    </div>
  `;
}

async function submitCreateAutomationRule(formEl) {
  const fd = new FormData(formEl);
  const payload = {
    name: String(fd.get("name") || ""),
    target_agent_id: String(fd.get("target_agent_id") || ""),
    enabled: true,
    source_type: "github",
    trigger_type: "github_pr_review_requested",
    task_template_id: "github_pr_review",
    scope: { owner: String(fd.get("owner") || ""), repo: String(fd.get("repo") || "") },
    trigger_config: { review_target_type: String(fd.get("review_target_type") || "user"), review_target: String(fd.get("review_target") || "") },
    task_input_defaults: { review_event: "COMMENT", skill_name: "review-pull-request" },
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
    bundle_ref: {
      repo: String(fd.get("bundle_repo") || "").trim(),
      path: String(fd.get("bundle_path") || "").trim(),
      branch: String(fd.get("bundle_branch") || "").trim(),
    },
    manifest_ref: {
      repo: String(fd.get("manifest_repo") || "").trim() || String(fd.get("bundle_repo") || "").trim(),
      path: String(fd.get("manifest_path") || "").trim() || String(fd.get("bundle_path") || "").trim(),
      branch: String(fd.get("manifest_branch") || "").trim() || String(fd.get("bundle_branch") || "").trim(),
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

async function openEditDialog(agent) {
  await Promise.all([loadRuntimeProfiles(true), loadAgentDefaults()]);
  // Populate the edit form by setting input values directly
  const form = document.getElementById("edit-form");
  if (form && form.elements) {
    if (form.elements["id"]) {
      form.elements["id"].value = agent.id ?? "";
    }
    if (form.elements["name"]) {
      form.elements["name"].value = agent.name || "";
    }
    if (form.elements["repo_url"]) {
      form.elements["repo_url"].value = agent.repo_url || "";
    }
    if (form.elements["branch"]) {
      form.elements["branch"].value = agent.branch || "";
      form.elements["branch"].placeholder = state.agentDefaults?.default_branch
        ? `Configured default branch (${state.agentDefaults.default_branch})`
        : "Configured default branch";
    }
    if (form.elements["runtime_profile_id"]) {
      populateRuntimeProfileSelect(form.elements["runtime_profile_id"], agent.runtime_profile_id || "");
    }
  }

  // Show the modal
  const editModal = document.getElementById("edit-modal");
  if (editModal) {
    editModal.classList.remove("hidden");
    editModal.setAttribute("aria-hidden", "false");
  }
}

// Open message edit modal
function openEditMessageModal(messageId, currentContent, attachments = []) {
  document.getElementById("edit-message-id").value = messageId;
  document.getElementById("edit-message-content").value = currentContent;
  document.getElementById("edit-attachments").value = JSON.stringify(attachments);
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

function getUserArticleAttachments(article) {
  const attachments = [];

  if (article?.dataset?.attachments) {
    try {
      const parsedAttachments = JSON.parse(article.dataset.attachments);
      if (Array.isArray(parsedAttachments)) attachments.push(...parsedAttachments);
    } catch (_error) {}
  }

  if (attachments.length === 0 && dom.messageList && article) {
    const allUserArticles = Array.from(dom.messageList.querySelectorAll('article[data-local-user="1"]'));
    const articleIndex = allUserArticles.indexOf(article);
    const chatState = getChatState();
    if (
      chatState &&
      articleIndex >= 0 &&
      Array.isArray(chatState.attachmentHistory) &&
      articleIndex < chatState.attachmentHistory.length &&
      Array.isArray(chatState.attachmentHistory[articleIndex])
    ) {
      attachments.push(...chatState.attachmentHistory[articleIndex]);
    }
  }

  if (attachments.length === 0 && article) {
    const images = article.querySelectorAll("img");
    images.forEach((img) => {
      if (img.src && img.src.startsWith("blob:")) {
        const fileId = getFileIdFromBlobUrl(img.src);
        if (fileId) attachments.push(fileId);
      }
    });
  }

  return attachments;
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
    if (selectedChatState) selectedChatState.attachmentHistory = [];
    return;
  }

  const rows = Array.from(dom.messageList.querySelectorAll(".message-row"));
  const targetRow = userArticle.closest(".message-row");
  const targetRowIndex = rows.indexOf(targetRow);
  const userArticlesBeforeDelete = Array.from(dom.messageList.querySelectorAll('article[data-local-user="1"]'));
  const targetUserIndex = userArticlesBeforeDelete.indexOf(userArticle);

  if (!targetRow || targetRowIndex < 0) {
    clearMessageListToWelcome();
    if (selectedChatState) selectedChatState.attachmentHistory = [];
    return;
  }

  for (let i = rows.length - 1; i >= targetRowIndex; i -= 1) {
    rows[i].remove();
  }

  if (Array.isArray(selectedChatState?.attachmentHistory)) {
    const safeTargetUserIndex = targetUserIndex >= 0 ? targetUserIndex : selectedChatState.attachmentHistory.length;
    selectedChatState.attachmentHistory = selectedChatState.attachmentHistory.slice(0, safeTargetUserIndex);
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

  const userArticle = findPrecedingUserArticle(row);
  const userMessageId = userArticle?.dataset?.messageId || "";
  if (!userMessageId || userMessageId.startsWith("local-")) {
    showToast("Cannot retry this message yet");
    return;
  }

  const content = getUserArticleContent(userArticle).trim();
  const attachments = getUserArticleAttachments(userArticle);
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
      showToast("Failed to delete message");
      return;
    }

    if (!response.ok || !result.success) {
      showToast(result.error || "Failed to delete message");
      return;
    }

    truncateDomFromUserArticle(userArticle);
    if (chatState) chatState.pendingFiles = [];
    if (dom.chatInput) dom.chatInput.value = content;
    const attachmentsInput = document.getElementById("chat-attachments");
    if (attachmentsInput) attachmentsInput.value = JSON.stringify(attachments);
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
      const attachments = getUserArticleAttachments(article);
      openEditMessageModal(messageId, content, attachments);
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
    const repoUrl = formData.get("repo_url")?.trim();
    const branch = formData.get("branch")?.trim();
    const runtimeProfileId = (formData.get("runtime_profile_id") || "").toString().trim();

    // Always include repo_url and branch (empty string to clear)
    if (repoUrl !== undefined) updates.repo_url = repoUrl || null;
    if (branch !== undefined) updates.branch = branch || null;
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
    const messageId = document.getElementById("edit-message-id").value;
    const newContent = document.getElementById("edit-message-content").value;
    const sessionId = document.getElementById("chat-session-id")?.value;
    
    if (!messageId || !newContent.trim() || !sessionId) {
      showToast("Invalid session");
      return;
    }
    
    try {
      // Use delete-from-here to delete the target message and subsequent messages
      // Then send a new message with the edited content
      const response = await fetch(`/a/${state.selectedAgentId}/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/delete-from-here`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      
      let result = {};
      try {
        result = await response.json();
      } catch (e) {
        // Non-JSON response
        showToast("Failed to delete message");
        return;
      }
      
      if (!response.ok || !result.success) {
        showToast(result.error || "Failed to delete message");
        return;
      }
      
      // Close modal
      closeEditMessageModal();
      document.getElementById("message-edit-modal")?.setAttribute("aria-hidden", "true");
      
      if (result.success) {
        let targetUserArticle = null;
        if (dom.messageList) {
          const userArticles = Array.from(dom.messageList.querySelectorAll('article[data-local-user="1"]'));
          targetUserArticle = userArticles.find((article) => article.dataset.messageId === messageId) || null;
        }
        if (targetUserArticle) {
          truncateDomFromUserArticle(targetUserArticle);
        } else {
          clearMessageListToWelcome();
          const selectedChatState = getChatState();
          if (selectedChatState) selectedChatState.attachmentHistory = [];
        }
        
        // Now send the edited message to LLM for processing
        setChatStatus("Sending edited message to AI...");
        
        // Set the chat input to the edited content
        if (dom.chatInput) {
          dom.chatInput.value = newContent;
        }
        
        // Set attachments from edit-attachments hidden field
        const editAttachments = document.getElementById("edit-attachments")?.value || '[]';
        const attachmentsInput = document.getElementById("chat-attachments");
        if (attachmentsInput) {
          attachmentsInput.value = editAttachments;
        }
        
        await submitChatForSelectedAgent();
      } else {
        showToast(result.error || "Failed to delete message");
      }
    } catch (err) {
      showToast("Error editing message: " + err.message);
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
  dom.railAssistantsBtn?.addEventListener("click", () => setActiveNavSection("assistants"));
  dom.bundlesMenuBtn?.addEventListener("click", () => setActiveNavSection("bundles"));
  dom.automationsMenuBtn?.addEventListener("click", () => setActiveNavSection("automations"));
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
    await setActiveNavSection("bundles", { toggleIfSame: false });
    if (state.requirementBundles.length) {
      const first = state.requirementBundles[0];
      state.selectedBundleKey = bundleKey(first);
      renderRequirementBundleList();
      await openRequirementBundleInMain(first.bundle_ref);
    }
  });
  dom.homeOpenTasksBtn?.addEventListener("click", async () => {
    await setActiveNavSection("tasks", { toggleIfSame: false });
    if (state.myTasks.length) {
      await openTaskDetailInMain(state.myTasks[0].id);
    }
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

    const fileBtn = event.target.closest("[data-file-ref]");
    if (fileBtn) {
      event.preventDefault();
      insertFileReference(fileBtn.dataset.fileRef || "");
      hideSuggest();
      dom.chatInput?.focus();
      setChatStatus(`Inserted ${fileBtn.dataset.fileRef || "file reference"}`);
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

  dom.tasksMenuBtn?.addEventListener("click", () => setActiveNavSection("tasks"));

  dom.addTaskBtn?.addEventListener("click", async () => {
    try {
      await openTaskCreatePanelInMain();
    } catch (error) {
      showToast(`Open task create failed: ${error.message}`);
    }
  });
  dom.runtimeProfilesMenuBtn?.addEventListener("click", () => setActiveNavSection("runtime-profiles"));
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
    const repoUrl = (formData.get("repo_url") || "").toString().trim();
    const branch = (formData.get("branch") || "").toString().trim();
    const runtimeProfileId = (formData.get("runtime_profile_id") || "").toString().trim();

    const msgEl = document.getElementById("create-msg");

    try {
      const defaults = await loadAgentDefaults();

      if (!defaults.image_repo || !defaults.disk_size_gi) {
        throw new Error("Invalid defaults configuration");
      }

      // Use form values if provided, or null to skip repo
      const data = {
        name: name,
        image: defaults.image_repo + ":" + (defaults.image_tag || "latest"),
        repo_url: repoUrl || null,
        branch: branch || null,
        disk_size_gi: defaults.disk_size_gi,
        cpu: defaults.cpu,
        memory: defaults.memory,
        mount_path: defaults.mount_path,
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

  // Quick action buttons
  document.getElementById('quick-uploads-btn')?.addEventListener('click', () => {
    if (!state.selectedAgentId) {
      showToast('Please select an assistant first');
      return;
    }
    openMyUploads();
  });

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
  await refreshAll({ preserveLayout: true });
  await setActiveNavSection("assistants", {
    toggleIfSame: false,
    preserveCollapsed: true,
  });
  await restorePinnedToolPanelFromPreferencesOnce();
  renderMarkdown(document);
  renderIcons();
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
function renderSystemPromptSection(agent) {
  // Find the settings panel container
  var container = null;
  
  // First try: use #agent-meta inside settings panel if it exists
  var agentMeta = document.getElementById('agent-meta');
  if (agentMeta) {
    container = agentMeta;
  }
  
  // Second try: use #tool-panel-body if it exists  
  if (!container) {
    var toolPanelBody = document.getElementById('tool-panel-body');
    if (toolPanelBody) {
      container = toolPanelBody;
    }
  }
  
  // Third try: find visible aside element in settings panel
  if (!container) {
    var asides = document.querySelectorAll('aside');
    for (var i = 0; i < asides.length; i++) {
      if (asides[i].offsetParent !== null) {  // visible
        container = asides[i];
        break;
      }
    }
  }
  
  // Last resort: use body
  if (!container) {
    container = document.body;
  }
  
  // Remove existing section if present
  var existing = document.getElementById('system-prompt-section');
  if (existing) existing.remove();
  
  var section = document.createElement('div');
  section.id = 'system-prompt-section';
  section.className = 'portal-panel-section';
  section.innerHTML = '<div class="portal-panel-header"><div class="portal-detail-label">System Prompt</div></div><div id="system-prompt-items" class="portal-panel-stack"></div><div id="system-prompt-loading" class="portal-inline-state">Loading...</div><div id="system-prompt-error" class="portal-inline-state is-error hidden"></div>';
  
  container.appendChild(section);
  
  loadSystemPromptConfig(agent.id);
}

function loadSystemPromptConfig(agentId) {
  // Guard: don't touch DOM if not current agent
  if (state.selectedAgentId !== agentId) {
    return;
  }
  
  var loading = document.getElementById('system-prompt-loading');
  var error = document.getElementById('system-prompt-error');
  var items = document.getElementById('system-prompt-items');
  if (!items) return;
  
  loading.classList.remove('hidden');
  error.classList.add('hidden');
  items.innerHTML = '';
  
  api('/a/' + agentId + '/api/agent/system-prompt/config').then(function(config) {
    // Guard: check if response is stale (agent switched while request was in-flight)
    if (state.selectedAgentId !== agentId) {
      return;
    }
    
    // Check if agent is writable
    const currentAgent = state.mineAgents?.find(a => a.id === agentId);
    const canWrite = canWriteAgent(currentAgent);
    
    var sections = ['soul', 'user', 'agents', 'memory', 'daily_notes'];
    var labels = { soul: 'SOUL', user: 'USER', agents: 'AGENTS', memory: 'MEMORY', daily_notes: 'DAILY NOTES' };
    var hasEdit = { soul: true, user: true, agents: true, memory: true, daily_notes: false };
    
    for (var i = 0; i < sections.length; i++) {
      var name = sections[i];
      var enabled = config[name] && config[name].enabled !== undefined ? config[name].enabled : true;
      var disabledAttr = canWrite ? '' : ' disabled';
      var editButton = hasEdit[name] ? '<button data-section="' + name + '" data-action="edit" class="portal-btn is-secondary portal-system-prompt-edit" title="Edit ' + labels[name] + '"' + disabledAttr + '>Edit</button>' : '';
      var item = document.createElement('div');
      item.className = 'portal-system-prompt-item';
      item.innerHTML = '<div class="portal-checkbox-row"><label class="toggle-switch"><input type="checkbox" id="sp-' + name + '-enabled" data-section="' + name + '" ' + (enabled ? 'checked' : '') + ' class="portal-system-prompt-check"' + disabledAttr + '><span class="toggle-slider"></span></label><span>' + labels[name] + '</span></div>' + editButton;
      items.appendChild(item);
    }
    
    var checkboxes = items.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < checkboxes.length; j++) {
      checkboxes[j].addEventListener('change', function(e) {
        // Use current selected agent
        updateSystemPromptEnabled(state.selectedAgentId, e.target.dataset.section, e.target.checked);
      });
    }
    
    var editBtns = items.querySelectorAll('button[data-action="edit"]');
    for (var k = 0; k < editBtns.length; k++) {
      editBtns[k].addEventListener('click', (function(btn) {
        return function() {
          // Use current selected agent
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
  api('/a/' + agentId + '/api/agent/system-prompt/config', {
    method: 'PUT',
    body: JSON.stringify(payload)
  }).then(function() {
  }).catch(function(e) {
    console.error('Failed to update:', e);
    showToast('Failed to update: ' + e.message);
    // Reload config to revert UI to server state
    loadSystemPromptConfig(agentId);
  });
}

function editSystemPromptSection(agentId, section) {
  api('/a/' + agentId + '/api/agent/system-prompt/' + section).then(function(data) {
    showSystemPromptEditor(agentId, section, data.content || '', data.enabled);
  }).catch(function(e) {
    console.error('Failed to load:', e);
    showToast('Failed to load: ' + e.message);
  });
}

function showSystemPromptEditor(agentId, section, content, enabled) {
  var labels = { soul: 'SOUL', user: 'USER', agents: 'AGENTS', memory: 'MEMORY' };

  var modal = document.getElementById('system-prompt-editor-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'system-prompt-editor-modal';
    modal.className = 'modal hidden';
    modal.dataset.keyHandlerAttached = '0';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'sp-editor-title');
    modal.innerHTML = '<div class="modal-card panel portal-editor-modal-card"><div class="portal-modal-titlebar"><h3 id="sp-editor-title"></h3><button type="button" id="sp-editor-close" class="portal-modal-close" aria-label="Close">✕</button></div><div class="stack"><div class="portal-checkbox-row"><label class="toggle-switch"><input type="checkbox" id="sp-editor-enabled"><span class="toggle-slider"></span></label><span>Enable custom prompt for this section</span></div><textarea id="sp-editor-content" class="portal-form-textarea" rows="10" placeholder="Enter content..."></textarea><div class="portal-modal-actions"><button type="button" id="sp-editor-cancel" class="portal-btn is-secondary">Cancel</button><button type="button" id="sp-editor-save" class="portal-btn is-primary">Save</button></div></div></div>';
    document.body.appendChild(modal);

    modal._keyHandler = function(e) {
      if (e.key === 'Escape') {
        closeSystemPromptEditor();
      }
    };

    document.getElementById('sp-editor-close').addEventListener('click', closeSystemPromptEditor);
    modal.addEventListener('click', function(e) {
      if (e.target === modal) {
        closeSystemPromptEditor();
      }
    });
    document.getElementById('sp-editor-cancel').addEventListener('click', closeSystemPromptEditor);
    document.getElementById('sp-editor-save').addEventListener('click', function() {
      var currentAgentId = modal.dataset.agentId;
      var currentSection = modal.dataset.section;
      if (currentAgentId && currentSection) {
        saveSystemPromptSection(currentAgentId, currentSection);
      }
    });
  }

  if (modal.dataset.keyHandlerAttached !== '1') {
    document.addEventListener('keydown', modal._keyHandler);
    modal.dataset.keyHandlerAttached = '1';
  }

  document.getElementById('sp-editor-title').textContent = labels[section] + ' Configuration';
  document.getElementById('sp-editor-enabled').checked = enabled;
  document.getElementById('sp-editor-content').value = content;
  modal.dataset.section = section;
  modal.dataset.agentId = agentId;

  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');

  modal._previousActiveElement = document.activeElement;
  var focusTarget = document.getElementById('sp-editor-content') || document.getElementById('sp-editor-enabled');
  if (focusTarget && typeof focusTarget.focus === 'function') {
    focusTarget.focus();
  }
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
  if (modal._previousActiveElement) {
    modal._previousActiveElement.focus();
    modal._previousActiveElement = null;
  }
}

function saveSystemPromptSection(agentId, section) {
  var enabled = document.getElementById('sp-editor-enabled').checked;
  var content = document.getElementById('sp-editor-content').value;
  
  // Send to individual section endpoint
  api('/a/' + agentId + '/api/agent/system-prompt/' + section, {
    method: 'PUT',
    body: JSON.stringify({ enabled: enabled, content: content })
  }).then(function() {
    closeSystemPromptEditor();
    loadSystemPromptConfig(agentId);
  }).catch(function(e) {
    console.error('Failed to save:', e);
    showToast('Failed to save: ' + e.message);
  });
}
