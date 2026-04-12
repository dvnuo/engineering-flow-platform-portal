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
  agentMeta: document.getElementById("agent-meta"),
  agentActions: document.getElementById("agent-actions"),
  topSettings: document.getElementById("top-settings"),
  logoutBtn: document.getElementById("logout-btn"),
  themeToggle: document.getElementById("theme-toggle"),
  railAssistantsBtn: document.getElementById("rail-assistants-btn"),
  usersMenuBtn: document.getElementById("users-menu-btn"),
  tasksMenuBtn: document.getElementById("tasks-menu-btn"),
  bundlesMenuBtn: document.getElementById("bundles-menu-btn"),
  portalShell: document.querySelector(".portal-shell"),
  portalSecondaryPane: document.getElementById("portal-secondary-pane"),
  secondaryPaneEyebrow: document.getElementById("secondary-pane-eyebrow"),
  secondaryPaneTitle: document.getElementById("secondary-pane-title"),
  secondaryPaneActions: document.getElementById("secondary-pane-actions"),
  assistantsNavSection: document.getElementById("assistants-nav-section"),
  bundlesNavSection: document.getElementById("bundles-nav-section"),
  tasksNavSection: document.getElementById("tasks-nav-section"),
  bundleNavList: document.getElementById("bundle-nav-list"),
  taskNavList: document.getElementById("task-nav-list"),
  addBundleBtn: document.getElementById("add-bundle-btn"),
  headerNewChatBtn: document.getElementById("header-new-chat-btn"),
  composerAttachBtn: document.getElementById("composer-attach-btn"),
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
      // Only process file items, not strings
      if (item.kind !== 'file') continue;
      
      const file = item.getAsFile();
      if (!file) continue;
      
      const isImage = item.type.startsWith('image/');
      
      if (isImage) {
        const ext = item.type.split('/')[1] || 'png';
        const name = file.name || 'pasted-image-' + Date.now() + '.' + ext;
        files.push({ file: file, name: name, isImage: true });
      }
      else if (item.type.startsWith('application/') || item.type.startsWith('text/') || item.type.startsWith('audio/') || item.type.startsWith('video/')) {
        files.push({ file: file, name: file.name || 'pasted-file', isImage: false });
      }
    }
    
    // Only prevent default if there are actual files to upload
    if (files.length > 0) {
      e.preventDefault();
      
      if (!state.selectedAgentId) {
        showToast('Please select an assistant first');
        return;
      }
      
      for (const { file, name, isImage } of files) {
        const pf = {
          id: 'paste_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
          file: file,
          name: name || file.name,
          isImage: isImage,
          previewUrl: null,
          status: 'uploading',
        };
        
        // Generate preview for images only
        if (isImage) {
          pf.previewUrl = URL.createObjectURL(file);
        }
        
        state.pendingFiles.push(pf);
        renderInputPreview();
        
        uploadPendingFile(pf)
          .then(data => {
            pf.status = 'uploaded';
            pf.uploadedData = data;
            pf.file_id = data.file_id || data.id;
            renderInputPreview();
            showToast('File uploaded: ' + name);
          })
          .catch(err => {
            pf.status = 'failed';
            pf.error = err.message;
            renderInputPreview();
            showToast('Upload failed: ' + err.message);
          });
      }
    }
  });
}

const LAST_AGENT_STORAGE_KEY = "portal-last-agent-id";

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
  if (!state.attachmentHistory) {
    state.attachmentHistory = [];
  }
  state.attachmentHistory.push(attachments);
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
  detailOpen: false,
  activeUtilityPanel: null,
  cachedSkills: [],
  cachedSkillsByAgent: new Map(),
  cachedMentionFiles: [],
  cachedMentionFilesByAgent: new Map(),
  selectedSuggestionIndex: -1,
  // UI-only state: portal stores current selected session id per agent.
  // Runtime remains source-of-truth for full session history/messages.
  agentSessionIds: new Map(),
  isSubmittingChat: false,
  pendingMessage: "",
  currentUserId: Number(dom.appRoot?.dataset.userId || 0),
  currentUserName: dom.appRoot?.dataset.nickname || dom.appRoot?.dataset.username || "You",
  attachmentHistory: [],
  currentUserRole: String(dom.appRoot?.dataset.role || "user"),
  eventWs: null,
  eventWsAgentId: null,
  eventWsSessionId: null,
  inflightThinking: null,
  pendingThinkingEvents: null,  // Events from HTMX response (skill mode)
  pendingFiles: [],
  isComposingInput: false,
  suggestRequestSeq: 0,
  suggestBlurHideTimer: null,
  // Backup for restore on error
  pendingFilesBackup: [],
  messageBackup: "",
  requirementBundles: [],
  selectedBundleKey: null,
  activeNavSection: "assistants",
  secondaryPaneCollapsed: false,
  myTasks: [],
  selectedTaskId: null,
  didAppendAttachmentHistoryForPendingSend: false,
  serverFilesRootPath: null,
  serverFilesCurrentPath: null,
};

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

function removePendingFile(id) {
  const pf = state.pendingFiles.find(f => f.id === id);
  if (pf) {
    // Abort upload if in progress
    if (pf.xhr && pf.xhr.abort) {
      pf.xhr.abort();
      pf.cancelled = true;
    }
  }
  const idx = state.pendingFiles.findIndex(f => f.id === id);
  if (idx !== -1) {
    // Don't revoke immediately - wait for message to be sent
    // The blob URLs are needed for the optimistic UI message
    state.pendingFiles.splice(idx, 1);
    renderInputPreview();
  }
}

function clearPendingFiles() {
  // Abort any in-progress uploads and revoke blob URLs to prevent memory leaks
  state.pendingFiles.forEach(pf => {
    if (pf.xhr && pf.xhr.abort) {
      pf.xhr.abort();
    }
    // Revoke blob URL to free memory
    if (pf.previewUrl && pf.previewUrl.startsWith('blob:')) {
      URL.revokeObjectURL(pf.previewUrl);
    }
  });
  state.pendingFiles = [];
  renderInputPreview();

  // Clear attachments field
  const attachmentsInput = document.getElementById('chat-attachments');
  if (attachmentsInput) {
    attachmentsInput.value = '';
  }
}

// Add files and upload immediately
async function addPendingFilesAndUpload(files) {
  for (const file of files) {
    const isImage = file.type.startsWith('image/');
    const pf = {
      id: generateFileId(),
      file: file,
      previewUrl: null,
      name: file.name,
      isImage: isImage,
      status: 'uploading'
    };
    state.pendingFiles.push(pf);
    renderInputPreview();

    // Generate preview
    if (isImage) {
      // Use URL.createObjectURL for better memory efficiency
      pf.previewUrl = URL.createObjectURL(file);
      renderInputPreview();  // Re-render to show preview
    }

    // Upload immediately
    try {
      const data = await uploadPendingFile(pf);
      pf.status = 'uploaded';
      pf.uploadedData = data;
      pf.file_id = data.file_id || data.id;
      
      // Store blob URL to file ID mapping
      if (pf.previewUrl && pf.file_id) {
        setBlobUrlMapping(pf.previewUrl, pf.file_id);
      }
      
      renderInputPreview();
      showToast('File uploaded: ' + file.name);

      // Note: Do NOT add to attachments here - will be built from pendingFiles when sending
      // Image will be shown in input-preview-area via renderInputPreview()

      // Connect WebSocket after upload completes
      ensureEventSocketForSelectedAgent();
    } catch (error) {
      pf.status = 'failed';
      renderInputPreview();
      showToast('Upload failed: ' + error.message);
    }
  }
}

function renderInputPreview() {
  const container = document.getElementById('input-preview-area');
  if (!container) return;

  if (state.pendingFiles.length === 0) {
    container.classList.add('hidden');
    container.innerHTML = '';
    return;
  }

  container.classList.remove('hidden');
  container.innerHTML = state.pendingFiles.map(pf => {
    let content = '';
    let statusBadge = '';

    // Status badge
    if (pf.status === 'uploading') {
      statusBadge = '<span class="input-preview-badge is-uploading" aria-hidden="true">⏳</span>';
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

async function uploadPendingFile(pf) {
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
          pf.status = 'uploaded';
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
    const url = '/a/' + state.selectedAgentId + '/api/files/upload';
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

function updateChatInputPlaceholder() {
  if (!dom.chatInput) return;
  const assistantName = String(state.selectedAgentName || "").trim();
  dom.chatInput.placeholder = assistantName
    ? `Ask ${assistantName} anything...`
    : "Ask me anything...";
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

  return `<div class="message-row message-row-user"><div class="message-meta message-meta-user"><span class="message-author">You</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-user" data-local-user="1" data-optimistic-user="1"><div class="message-body whitespace-pre-wrap text-sm">${safe(text)}</div>${attachmentHtml}</article></div>`;
}

function buildPendingAssistantArticle() {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const pendingAgentName = state.selectedAgentName || "Assistant";
  return `<div class="message-row message-row-assistant" data-temporary-assistant="1"><div class="message-meta"><span class="message-author">${escapeHtml(pendingAgentName)}</span><span class="message-timestamp">${now}</span></div><article class="message-surface message-surface-assistant assistant-message pending-assistant" data-pending-assistant="1"><div class="pending-assistant-label"><span>Thinking</span><span class="assistant-loading-dots"><i></i><i></i><i></i></span></div></article></div>`;
}

function buildPendingAssistantRowForEvents(thinkingId) {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const name = state.selectedAgentName || "Assistant";
  return `
    <div class="message-row message-row-assistant" data-temporary-assistant="1" data-thinking-fallback="1">
      <div class="message-meta">
        <span class="message-author">${escapeHtml(name)}</span>
        <span class="message-timestamp">${now}</span>
      </div>
      <article class="message-surface message-surface-assistant assistant-message pending-thinking" data-thinking-id="${escapeHtmlAttr(thinkingId)}"></article>
    </div>
  `;
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
}

function isTrackableThinkingEvent(type) {
  return [
    "execution.started", "execution.completed", "execution.failed",
    "iteration_start", "llm_thinking", "tool_call", "tool_result",
    "skill_matched", "complete",
    // Skill mode events
    "skill_mode_start", "skill_step", "skill_session_start",
    "skill_compaction", "skill_complete"
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
    // Skill mode events
    skill_mode_start: { icon: "play-circle", title: "Skill Mode", detail: `Starting: ${data.skill || "Skill"}` },
    skill_step: { icon: "list-checks", title: `Step: ${data.step || "Step"}`, detail: data.detail || "", status: data.status },
    skill_session_start: { icon: "clipboard-list", title: "Skill Session", detail: `Goal: ${data.goal || ""}` },
    skill_compaction: { icon: data.status === "completed" ? "archive" : "scissors", title: "Compaction", detail: data.status === "completed" ? `Steps: ${data.remaining_steps}` : `Tokens: ${data.current_tokens}` },
    skill_complete: { icon: "check-square", title: data.reason === "finish" ? "Skill Finished" : "Skill Awaiting Input", detail: data.result || data.question || "" },
  };
  return byType[type] || { icon: "circle", title: type.replaceAll("_", " "), detail: "" };
}

// Open Thinking Process panel - using backend rendering
async function openThinkingProcessPanel() {
  if (!state.selectedAgentId) {
    showToast('Please select an assistant first');
    return;
  }
  
  // Try state first (updated after message received), fall back to hidden input for new sessions
  // Note: we re-query the element each time because OOB swap replaces the DOM element
  let currentSessionId = currentSessionIdForSelectedAgent();
  const hiddenSessionInput = document.getElementById("chat-session-id");
  if (!currentSessionId && hiddenSessionInput) {
    currentSessionId = (hiddenSessionInput.value || "").trim();
  }
  
  if (!currentSessionId) {
    setToolPanel("Thinking Process", '<div class="portal-inline-state">No session selected. Start a conversation first.</div>');
    return;
  }
  
  // Use htmx to load backend-rendered panel
  setToolPanel("Thinking Process", '<div class="portal-inline-state">Loading…</div>');
  
  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/thinking/panel?session_id=${encodeURIComponent(currentSessionId)}`, {
      target: "#tool-panel-body",
      swap: "innerHTML"
    });
  } catch (err) {
    setToolPanel("Thinking Process", `<div class="portal-inline-state is-error">Error: ${safe(err.message)}</div>`);
  }
}

function renderThinkingProcess(article, events) {
  if (!article) return;

  let host = article.querySelector('[data-thinking-process="1"]');
  if (!host) {
    host = document.createElement("div");
    host.dataset.thinkingProcess = "1";
    host.className = "portal-thinking-block";
    article.append(host);
  }

  const expanded = host.dataset.expanded === "1";
  const count = events.length;
  const rows = events.map((event) => {
    const view = getThinkingEventDisplay(event);
    return `<div class="portal-thinking-step"><span class="portal-thinking-step-icon"><i data-lucide="${view.icon}" class="h-3 w-3"></i></span><div class="portal-thinking-step-title">${safe(view.title)}</div><div class="portal-thinking-step-detail">${safe(view.detail || "")}</div></div>`;
  }).join("");

  host.innerHTML = `
    <button type="button" data-thinking-toggle="1" class="portal-thinking-toggle">
      <span class="inline-flex items-center gap-1.5"><i data-lucide="brain"></i>View Thinking Process (${count} steps)</span>
      <i data-lucide="${expanded ? "chevron-up" : "chevron-down"}"></i>
    </button>
    <div data-thinking-timeline="1" class="portal-thinking-timeline ${expanded ? "" : "hidden"}">
      ${count ? rows : `<div class="portal-thinking-empty">Waiting for runtime events…</div>`}
    </div>
  `;

  host.querySelector('[data-thinking-toggle="1"]')?.addEventListener("click", () => {
    const timeline = host.querySelector('[data-thinking-timeline="1"]');
    const isExpanded = !timeline.classList.contains("hidden");
    host.dataset.expanded = isExpanded ? "0" : "1";
    renderThinkingProcess(article, events);
  });

  renderIcons();
}

function attachThinkingToLatestAssistant(events) {
  if (!dom.messageList || !events?.length) return false;
  const assistants = Array.from(dom.messageList.querySelectorAll("article.assistant-message:not([data-pending-assistant='1'])"));
  const target = assistants[assistants.length - 1];
  if (!target) return false;
  renderThinkingProcess(target, events);
  return true;
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

// Note: Event rendering is handled by attachThinkingToLatestAssistant in handleChatAfterSwap

function handleAgentEventMessage(raw) {
  let payload = null;
  try { payload = JSON.parse(raw); } catch { return; }

  const entry = normalizeRuntimeEvent(payload);
  if (!entry) return;
  const currentSessionId = currentSessionIdForSelectedAgent();
  if (entry.session_id && currentSessionId && entry.session_id !== currentSessionId) return;

  // Handle additive runtime state fields while keeping existing event semantics.
  const isCompletion = isCompletionRuntimeState(entry.state);
  const type = entry.type;
  const lifecycleType = entry.lifecycle_type;

  if (!isTrackableThinkingEvent(type) && !lifecycleType && !isCompletion) return;

  // Initialize inflightThinking if not set and we have skill mode events
  if (!state.inflightThinking && type?.startsWith("skill_")) {
    // Create a placeholder thinking panel for skill mode
    const thinkingId = `thinking-${Date.now()}`;
    state.inflightThinking = { id: thinkingId, events: [], completed: false };
    
    // Find or create the assistant message placeholder
    let assistantPlaceholder = dom.messageList?.querySelector('article.assistant-message.pending-thinking');
    if (!assistantPlaceholder) {
      dom.messageList?.insertAdjacentHTML("beforeend", buildPendingAssistantRowForEvents(thinkingId));
      assistantPlaceholder = dom.messageList?.querySelector(`article.pending-thinking[data-thinking-id="${thinkingId}"]`);
    }
    if (assistantPlaceholder) {
      assistantPlaceholder.dataset.thinkingId = thinkingId;
      renderThinkingProcess(assistantPlaceholder, state.inflightThinking.events);
    }
  }

  if (!state.inflightThinking) return;

  if (!state.inflightThinking.started && type !== "execution.started") {
    state.inflightThinking.events.push({
      type: "execution.started",
      raw_type: "execution.started",
      lifecycle_type: "execution.started",
      data: { message: "Execution started" },
      ts: entry.ts,
      state: "started",
    });
    state.inflightThinking.started = true;
  }

  state.inflightThinking.events.push(entry);
  if (type === "execution.started") state.inflightThinking.started = true;

  if (lifecycleType && lifecycleType !== type) {
    const terminalDetail = lifecycleType === "execution.failed"
      ? (entry?.data?.error || entry?.data?.message || "Execution failed")
      : (entry?.data?.message || "Execution complete");
    state.inflightThinking.events.push({
      type: lifecycleType,
      raw_type: lifecycleType,
      lifecycle_type: lifecycleType,
      data: { ...entry.data, message: terminalDetail },
      ts: entry.ts,
      state: entry.state,
    });
  }

  const pendingArticle = dom.messageList?.querySelector(`[data-thinking-id="${state.inflightThinking.id}"]`);
  if (pendingArticle) renderThinkingProcess(pendingArticle, state.inflightThinking.events);

  if (type === "execution.completed" || type === "execution.failed" || type === "skill_complete" || isCompletion || lifecycleType === "execution.completed" || lifecycleType === "execution.failed") {
    state.inflightThinking.completed = true;
  }
}

function ensureEventSocketForSelectedAgent() {
  const agentId = state.selectedAgentId;
  if (!agentId) return;
  const sessionId = currentSessionIdForSelectedAgent();

  if (state.eventWs) {
    const sameAgent = state.eventWsAgentId === agentId;
    const sameSession = (state.eventWsSessionId || "") === (sessionId || "");
    const readyState = state.eventWs.readyState;
    if (sameAgent && sameSession && (readyState === WebSocket.OPEN || readyState === WebSocket.CONNECTING)) return;
    // Replace stale in-flight sockets too, otherwise a previous session's CONNECTING socket can attach to the wrong stream.
    disconnectEventSocket();
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const sessionQuery = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const ws = new WebSocket(`${protocol}//${window.location.host}/a/${agentId}/api/events${sessionQuery}`);
  state.eventWs = ws;
  state.eventWsAgentId = agentId;
  state.eventWsSessionId = sessionId || "";

  ws.onmessage = (event) => handleAgentEventMessage(event.data);
  ws.onclose = () => {
    if (state.eventWs === ws) {
      state.eventWs = null;
      state.eventWsAgentId = null;
      state.eventWsSessionId = null;
    }
  };
  ws.onerror = () => {};
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

function parseDisplayBlocks(raw) {
  const normalizeBlocks = (blocks) => {
    if (!Array.isArray(blocks)) return [];
    return blocks
      .filter((block) => block && typeof block === "object" && typeof block.type === "string")
      .map((block) => ({ ...block, type: String(block.type).trim() }))
      .filter((block) => block.type.length > 0);
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
  return String(
    block.content
    ?? block.text
    ?? block.message
    ?? block.output
    ?? block.result
    ?? block.value
    ?? ""
  );
}

function renderCodeBlock(block) {
  const language = String(block?.lang || block?.language || "").trim().toLowerCase();
  const code = String(block?.code ?? getDisplayBlockText(block));
  const className = language ? `language-${language}` : "";
  return `
    <section class="message-block message-block-code">
      <div class="message-codeblock">
        <div class="message-codeblock-toolbar">
          <span class="message-codeblock-lang">${safe(language || "text")}</span>
          <button type="button" class="message-codeblock-copy" data-copy-text="${escapeHtmlAttr(code)}">Copy</button>
        </div>
        <pre><code class="${className}">${safe(code)}</code></pre>
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
  if (!Array.isArray(blocks) || !blocks.length) {
    return md.render(normalizeMarkdownText(fallbackMarkdown));
  }
  const html = blocks.map((block) => renderSingleDisplayBlock(block)).join("");
  return html || md.render(normalizeMarkdownText(fallbackMarkdown));
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
  if (open) {
    closeSessionsDrawer();
  }
  state.detailOpen = open;
  // Use unified tool-panel for agent details
  if (dom.toolPanel) dom.toolPanel.style.transform = open ? "translateX(0)" : "translateX(120%)";
  dom.toolBackdrop?.classList.toggle("hidden", !open);
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
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const err = await resp.json();
    const detail = err.detail;
    if (Array.isArray(detail)) {
      return detail.map(e => e.msg || JSON.stringify(e)).join(", ");
    }
    return detail || "Unknown error";
  }
  return await resp.text() || "Unknown error";
}

async function agentApi(path, options = {}) {
  if (!state.selectedAgentId) throw new Error("No selected assistant");
  return api(`/a/${state.selectedAgentId}${path}`, options);
}

function defaultWelcomeMessage() {
  const welcomeAgentName = state.selectedAgentName || "Assistant";
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

function setChatSubmitting(active) {
  state.isSubmittingChat = active;
  if (dom.sendChatBtn) dom.sendChatBtn.disabled = active;
}

function currentSessionIdForSelectedAgent() {
  return state.agentSessionIds.get(state.selectedAgentId) || "";
}

function syncHiddenSessionInputFromState() {
  // Re-query the element each time since OOB swap replaces the DOM element
  const hiddenInput = document.getElementById("chat-session-id");
  if (hiddenInput) hiddenInput.value = currentSessionIdForSelectedAgent();
}

function updateSelectedAgentSession(sessionId) {
  if (!state.selectedAgentId) return;

  const value = (sessionId || "").trim();
  if (value) {
    state.agentSessionIds.set(state.selectedAgentId, value);
    setLastSessionId(state.selectedAgentId, value);
  } else {
    state.agentSessionIds.delete(state.selectedAgentId);
    setLastSessionId(state.selectedAgentId, null);
  }
  syncHiddenSessionInputFromState();
  ensureEventSocketForSelectedAgent();
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
      const isActive = state.selectedAgentId === agent.id;
      const row = document.createElement("button");
      row.type = "button";
      row.className = `portal-agent-row${isActive ? " is-active" : ""}`;
      const sharedBadge = Number(agent.owner_user_id) === state.currentUserId ? "" : '<span class="portal-agent-shared">shared</span>';
      row.innerHTML = `
        <div class="portal-agent-row-head">
          <span class="portal-agent-name">${safe(agent.name)}</span>
          ${sharedBadge}
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
    const branch = agent.branch || 'main';
    repoSection = `
      <div class="portal-detail-section">
        <div class="portal-detail-label">Repository</div>
        <code class="portal-detail-code">${safe(agent.repo_url)}</code>
        <div class="portal-detail-subtle">Branch: <span class="portal-detail-value">${safe(branch)}</span></div>
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
    { label: agent.visibility === "public" ? "Unshare" : "Share", icon: agent.visibility === "public" ? "lock" : "share-2", variantClass: "is-info", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`) },
    { label: "Edit", icon: "pencil", variantClass: "is-neutral", disabled: !writable, onClick: () => openEditDialog(agent) },
    { label: "Delete", icon: "trash-2", variantClass: "is-danger", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/delete-runtime`, "DELETE", true) },
    { label: "Destroy", icon: "flame", variantClass: "is-danger", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/destroy`, "POST", true) },
  ];

  actions.forEach((cfg) => container.append(buildIconBtn(cfg)));

  if (!writable) {
    const note = document.createElement("div");
    note.className = "portal-detail-note";
    note.textContent = "Read-only for shared assistant.";
    container.append(note);
  }

  dom.agentActions.append(container);
  renderIcons();
}

async function selectAgentById(agentId) {
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
  state.inflightThinking = null;
  disconnectEventSocket();

  if (dom.chatAgentId) dom.chatAgentId.value = agentId || "";
  syncHiddenSessionInputFromState();
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
    return;
  }

  const status = getSelectedAgentStatus();
  state.selectedAgentName = agent.name || null;
  updateChatInputPlaceholder();
  dom.embedTitle.textContent = agent.name;
  dom.selectedStatus.textContent = status;
  dom.selectedStatus.className = `toolbar-status-badge status-${status}`;
  setChatStatus("Ready");
  const needsStart = status !== "running";
  setButtonDisabled(dom.headerNewChatBtn, needsStart, "Start the assistant from Assistant details first");
  setButtonDisabled(sessionsBtn, needsStart, "Start the assistant from Assistant details first");
  setButtonDisabled(dom.homeStartChatBtn, needsStart, "Start the assistant from Assistant details first");
  dom.homeTitle && (dom.homeTitle.textContent = `${agent.name}`);
  dom.homeSubtitle && (dom.homeSubtitle.textContent = "Choose an assistant from the left to start chatting, inspect tasks, or browse bundles.");
  if (dom.homeAgentSummary) {
    if (status !== "running") dom.homeAgentSummary.textContent = `${agent.name} is ${status}. Start it to open chat.`;
    else dom.homeAgentSummary.textContent = `${agent.name} is running. Open a session or start a new chat.`;
  }

  if (dom.chatAgentId) dom.chatAgentId.value = agent.id;
  syncHiddenSessionInputFromState();

  renderAgentMeta(agent);
  renderAgentActions(agent, status);

  const running = status === "running";
  setMainView(running ? "chat" : "home");
  syncMainHeader();

  if (running) {
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
    const data = await agentApi("/api/sessions?limit=1");
    const sessions = data.sessions || [];
    if (sessions.length > 0) {
      // Use session_id (not id) for session objects
      const lastSessionId = sessions[0].session_id;
      if (lastSessionId) {
        setLastSessionId(agentId, lastSessionId);
        await loadSession(lastSessionId);
      }
    }
  } catch (e) {
  }
}

async function refreshAll() {
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

  await setActiveNavSection("assistants", { toggleIfSame: false });
  renderAgentList();
  await syncSelectedAgentState();
}

// ===== chat submit lifecycle =====
function handleChatBeforeRequest(event) {
  if (event.target?.id !== "chat-form") return;
  if (state.isSubmittingChat) {
    event.preventDefault();
    return;
  }

  // Block submission if any files are still uploading
  const uploadingFiles = state.pendingFiles.filter(pf => pf.status === 'uploading');
  if (uploadingFiles.length > 0) {
    event.preventDefault();
    showToast(`Waiting for ${uploadingFiles.length} file(s) to upload...`);
    return;
  }

  // Get the message
  const message = dom.chatInput?.value?.trim() || "";
  if (!message) {
    event.preventDefault();
    return;
  }

  // Backup message and files for potential restore on error
  messageBackup = message;
  pendingFilesBackup = [...state.pendingFiles];
  state.didAppendAttachmentHistoryForPendingSend = false;

  // Build attachments from pendingFiles for display
  setChatSubmitting(true);
  removeWelcomeMessageIfPresent();
  removeTemporaryAssistantRows();
  hideSuggest();

  // Build attachments from pending files for display
  let displayAttachments = [];
  
  // First try to get from pendingFiles
  displayAttachments = state.pendingFiles.map(pf => ({
    name: pf.file.name,
    type: pf.isImage ? 'image' : 'file',
    previewUrl: pf.previewUrl,
    url: pf.uploadedData?.url
  }));
  
  // If no pending files, check chat-attachments (for Edit flow)
  if (displayAttachments.length === 0) {
    const chatAttachmentsInput = document.getElementById("chat-attachments");
    if (chatAttachmentsInput && chatAttachmentsInput.value) {
      try {
        const attachmentIds = JSON.parse(chatAttachmentsInput.value);
        displayAttachments = attachmentIds.map(id => ({
          name: id,
          type: 'image',
          previewUrl: `/a/${state.selectedAgentId}/api/files/${encodeURIComponent(id)}`,
          url: `/a/${state.selectedAgentId}/api/files/${encodeURIComponent(id)}`
        }));
      } catch (e) {
        // console.error('Failed to parse attachments:', e);
      }
    }
  }

  if (dom.messageList && message) {
    dom.messageList.insertAdjacentHTML("beforeend", buildUserMessageArticle(message, displayAttachments));
    const thinkingId = 'thinking-' + Date.now();
    dom.messageList.insertAdjacentHTML("beforeend", buildPendingAssistantArticle());
    const pending = dom.messageList.querySelector('article[data-pending-assistant="1"]:last-of-type') || dom.messageList.lastElementChild;
    if (pending) pending.dataset.thinkingId = thinkingId;
    state.inflightThinking = { id: thinkingId, events: [], completed: false };
    if (pending) renderThinkingProcess(pending, state.inflightThinking.events);
    ensureEventSocketForSelectedAgent();
    scrollToBottom();
  }

  // Clear pending files and input (message is already captured in 'message' variable)
  clearPendingFiles();
  if (dom.chatInput) dom.chatInput.value = "";
  resetChatInputHeight();
  setChatStatus("Sending...");

  // Note: attachments is now set via htmx:configRequest event
  // HTMX will submit the form with attachments in the payload
}

function handleChatResponseError(event) {
  if (event.target?.id !== "chat-form") return;

  removeTemporaryAssistantRows();
  removeLatestOptimisticUserRow();
  if (state.didAppendAttachmentHistoryForPendingSend && Array.isArray(state.attachmentHistory) && state.attachmentHistory.length) {
    state.attachmentHistory.pop();
  }
  state.didAppendAttachmentHistoryForPendingSend = false;
  setChatSubmitting(false);
  state.inflightThinking = null;

  // Restore message and files from backup
  if (messageBackup || pendingFilesBackup.length > 0) {
    if (dom.chatInput) dom.chatInput.value = messageBackup;
    syncChatInputHeight();
    state.pendingFiles = pendingFilesBackup;
    renderInputPreview();
    pendingFilesBackup = [];
    messageBackup = "";
  }

  // Extract error message from response
  let errorMsg = "Send failed";
  const xhr = event.detail?.xhr;
  if (xhr) {
    try {
      const response = JSON.parse(xhr.responseText);
      errorMsg = response.detail || response.message || `Error: ${xhr.status} ${xhr.statusText}`;
    } catch (e) {
      errorMsg = xhr.responseText || `Error: ${xhr.status} ${xhr.statusText}`;
    }
  }
  setChatStatus(errorMsg, true);

  // Also show error in message list
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
}

function handleChatAfterRequest(event) {
  if (event.target?.id !== "chat-form") return;
  if (!event.detail?.successful) return;

  setChatSubmitting(false);
  state.didAppendAttachmentHistoryForPendingSend = false;
  state.pendingMessage = "";
  state.messagePrepared = false;

  // Extract events from response for later rendering (after HTMX swap)
  try {
    const xhr = event.detail.xhr;
    if (xhr && xhr.responseText) {
      // Parse once and extract both events and user message ID
      const parser = new DOMParser();
      const doc = parser.parseFromString(xhr.responseText, "text/html");
      const oobInput = doc.querySelector('#chat-user-message-id');
      if (oobInput && oobInput.value) {
        const userMessageId = oobInput.value;
        const optimisticUserArticle = getLatestOptimisticUserArticle();
        if (optimisticUserArticle) {
          optimisticUserArticle.dataset.messageId = userMessageId;
          delete optimisticUserArticle.dataset.optimisticUser;

          const parentContainer = optimisticUserArticle.parentElement;
          const editBtn = optimisticUserArticle.querySelector('.edit-msg-btn') ||
            parentContainer?.querySelector?.('.edit-msg-btn');
          if (editBtn) {
            const contentEl = optimisticUserArticle.querySelector('.message-body, .whitespace-pre-wrap');
            const content = contentEl ? contentEl.textContent : '';
            const attachments = state.attachmentHistory.length > 0
              ? (state.attachmentHistory[state.attachmentHistory.length - 1] || [])
              : [];
            editBtn.onclick = () => openEditMessageModal(userMessageId, content, attachments);
          }
        }
      }

      // Extract events
      const assistantArticle = doc.querySelector('article.assistant-message');
      if (assistantArticle) {
        const eventsData = assistantArticle.querySelector('[data-events]');
        if (eventsData) {
          try {
            const events = JSON.parse(eventsData.dataset.events);
            // Validate events is an array before storing
            if (Array.isArray(events) && events.length > 0) {
              state.pendingThinkingEvents = events;
            }
          } catch (e) {
            console.error('Failed to parse events:', e);
          }
        }
      }
      
    }
  } catch (e) {
    // Ignore errors
  }
  
  // Add edit buttons to the newly sent message
  addEditButtonsToMessages();
}

function handleChatAfterSwap(target) {
  if (target?.id !== "message-list") return;

  // Merge both WebSocket events and HTMX response events
  const wsEvents = state.inflightThinking?.events ? [...state.inflightThinking.events] : [];
  const htmxEvents = state.pendingThinkingEvents || [];
  
  // Combine and dedupe events (HTMX events are usually skill mode, WS are regular)
  const allEvents = [...wsEvents];
  htmxEvents.forEach(e => {
    if (!allEvents.some(ex => ex.type === e.type && JSON.stringify(ex.data) === JSON.stringify(e.data))) {
      allEvents.push(e);
    }
  });
  
  removeTemporaryAssistantRows();
  if (allEvents.length) attachThinkingToLatestAssistant(allEvents);
  
  // Clear states
  state.pendingThinkingEvents = null;
  state.inflightThinking = null;
  state.didAppendAttachmentHistoryForPendingSend = false;

  // OOB swap from chat partial updates hidden #chat-session-id. Keep per-agent session state in sync.
  // Re-query the element each time since OOB swap replaces the DOM element
  const sessionFromInput = document.getElementById("chat-session-id")?.value || "";
  updateSelectedAgentSession(sessionFromInput);

  renderMarkdown(dom.messageList);
  decorateToolMessages(dom.messageList);
  renderIcons();
  scrollToBottom();

  setChatStatus("Ready");
}

// ===== HTML decode helper =====
function decodeHtml(text) {
  if (!text) return '';
  const textarea = document.createElement('textarea');
  textarea.innerHTML = text;
  return textarea.value || '';
}

// ===== markdown + icons lifecycle =====
function initializeRenderLifecycle() {
  document.addEventListener("htmx:configRequest", (event) => {
    // This fires right before HTMX makes the request - perfect time to set attachments
    const elt = event.detail.requestConfig?.elt || event.target;
    if (elt?.id === "chat-form") {
      // Check if attachments already set (e.g., from Edit flow)
      const chatAttachmentsInput = document.getElementById("chat-attachments");
      const existingAttachments = chatAttachmentsInput?.value;
      
      // If already set (from Edit), don't overwrite
      if (existingAttachments && existingAttachments !== '') {
        event.detail.parameters.attachments = existingAttachments;
      } else {
        // Normal send - use pendingFiles
        const uploadedFileIds = state.pendingFiles
          .filter(pf => pf.file_id && pf.status === 'uploaded')
          .map(pf => pf.file_id);
        event.detail.parameters.attachments = JSON.stringify(uploadedFileIds);
        
        // Store attachments in history (always record, even empty arrays for indexing)
        addToAttachmentHistory(uploadedFileIds);
        state.didAppendAttachmentHistoryForPendingSend = true;
      }
    }
  });

  document.addEventListener("htmx:beforeRequest", handleChatBeforeRequest);
  document.addEventListener("htmx:afterRequest", handleChatAfterRequest);
  document.addEventListener("htmx:afterSwap", (event) => {
    handleChatAfterSwap(event.target);
    if (event.target?.id === "tool-panel-body") initializeSettingsPanel();
    if (event.target?.id === "message-list") addEditButtonsToMessages();
    renderIcons();
  });
  document.addEventListener("htmx:responseError", handleChatResponseError);
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
  // fileIdOrRef can be either:
  // - Full file_id (e.g., "1f516fcb...")
  // - File reference like "@file_xxx"
  let fileId = fileIdOrRef;

  // If it's a reference format, extract the ID
  const fileIdMatch = fileIdOrRef.match(/@file_(.+)/);
  if (fileIdMatch) {
    fileId = fileIdMatch[1];
  }

  if (fileId) {
    // Add to pendingFiles state and render preview in input-preview-area
    // Attachments will be built from pendingFiles when sending the message
    const existingPf = state.pendingFiles.find(pf => pf.file_id === fileId);
    if (!existingPf) {
      const pf = {
        id: fileId,
        file_id: fileId,
        file: { name: 'Uploaded file' },
        name: 'Uploaded file',
        previewUrl: `/a/${state.selectedAgentId}/api/files/${encodeURIComponent(fileId)}`,
        isImage: false,
        status: 'uploaded'
      };
      state.pendingFiles.push(pf);
      renderInputPreview();
    }
  }

  // Don't add to chat input - use attachments field instead
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

// ===== toolbar actions =====
function setToolPanel(title, contentHtml, panelKey = null) {
  if (!dom.toolPanel) return;
  state.detailOpen = false;
  closeSessionsDrawer();
  state.activeUtilityPanel = panelKey;
  dom.toolPanelTitle.textContent = title;
  if (typeof contentHtml === 'string' && contentHtml.startsWith('Failed:')) {
    dom.toolPanelBody.textContent = contentHtml.replace('Failed: ', '');
  } else {
    dom.toolPanelBody.innerHTML = contentHtml;
  }
  dom.toolPanel.style.transform = "translateX(0)";
  dom.toolBackdrop?.classList.remove("hidden");
}

function closeToolPanel() {
  state.detailOpen = false;
  state.activeUtilityPanel = null;
  if (dom.toolPanel) dom.toolPanel.style.transform = "translateX(120%)";
  dom.toolBackdrop?.classList.add("hidden");
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
  if (!dom.toolPanel) return;
  if (state.activeUtilityPanel !== "sessions") return;
  if (dom.toolPanel.style.transform === "translateX(120%)") {
    state.activeUtilityPanel = null;
    return;
  }
  closeToolPanel();
}

async function toggleSessionsDrawer() {
  const isToolPanelOpen = dom.toolPanel && dom.toolPanel.style.transform !== "translateX(120%)";
  if (state.activeUtilityPanel === "sessions" && isToolPanelOpen) {
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

function applySecondaryPaneState() {
  dom.portalShell?.classList.toggle("is-secondary-collapsed", state.secondaryPaneCollapsed);
  dom.portalSecondaryPane?.classList.toggle("is-hidden", state.secondaryPaneCollapsed);
}

function renderSecondaryPaneHeader() {
  if (!dom.secondaryPaneEyebrow || !dom.secondaryPaneTitle || !dom.secondaryPaneActions) return;
  const addAgentBtn = dom.addAgentBtn;
  const addBundleBtn = dom.addBundleBtn;
  if (addAgentBtn) addAgentBtn.classList.add("hidden");
  if (addBundleBtn) addBundleBtn.classList.add("hidden");

  if (state.activeNavSection === "assistants") {
    dom.secondaryPaneEyebrow.textContent = "My Space";
    dom.secondaryPaneTitle.textContent = "Assistants";
    if (addAgentBtn) addAgentBtn.classList.remove("hidden");
  } else if (state.activeNavSection === "bundles") {
    dom.secondaryPaneEyebrow.textContent = "Workspace";
    dom.secondaryPaneTitle.textContent = "Bundles";
    if (addBundleBtn) addBundleBtn.classList.remove("hidden");
  } else {
    dom.secondaryPaneEyebrow.textContent = "Workspace";
    dom.secondaryPaneTitle.textContent = "Tasks";
  }
}

function syncMainHeader() {
  const assistantMode = state.activeNavSection === "assistants";

  const sessionsBtn = document.getElementById("btn-sessions");
  const assistantOnlyControls = [dom.selectedStatus, sessionsBtn, dom.headerNewChatBtn, dom.detailToggle, dom.topSettings, document.getElementById("btn-thinking"), document.getElementById("btn-files")];
  assistantOnlyControls.forEach((el) => {
    if (!el) return;
    el.classList.toggle("hidden", !assistantMode);
  });

  if (assistantMode) {
    restoreAssistantHeaderState();
  } else {
    dom.embedTitle.textContent = state.activeNavSection === "bundles" ? "Requirement Bundles" : "My Tasks";
    setChatStatus(state.activeNavSection === "bundles" ? "Browse and open bundle detail in the main stage" : "Browse tasks and open task detail in the main stage");
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
  }
}

function bundleKeyFromRef(ref) {
  if (!ref) return null;
  return `${ref.repo || ""}|${ref.path || ""}|${ref.branch || ""}`;
}

function bundleKey(item) {
  return bundleKeyFromRef(item?.bundle_ref);
}

async function setActiveNavSection(section, { toggleIfSame = true } = {}) {
  const previousSection = state.activeNavSection;
  const sidebarWasCollapsed = state.secondaryPaneCollapsed;
  const validSections = new Set(["assistants", "bundles", "tasks"]);
  if (!validSections.has(section)) return;

  if (section === state.activeNavSection && toggleIfSame) {
    state.secondaryPaneCollapsed = !state.secondaryPaneCollapsed;
  } else {
    state.activeNavSection = section;
    state.secondaryPaneCollapsed = false;
  }

  dom.railAssistantsBtn?.classList.toggle("is-active", state.activeNavSection === "assistants");
  dom.bundlesMenuBtn?.classList.toggle("is-active", state.activeNavSection === "bundles");
  dom.tasksMenuBtn?.classList.toggle("is-active", state.activeNavSection === "tasks");

  dom.assistantsNavSection?.classList.toggle("hidden", state.activeNavSection !== "assistants");
  dom.bundlesNavSection?.classList.toggle("hidden", state.activeNavSection !== "bundles");
  dom.tasksNavSection?.classList.toggle("hidden", state.activeNavSection !== "tasks");

  applySecondaryPaneState();
  renderSecondaryPaneHeader();
  syncMainHeader();

  if (state.secondaryPaneCollapsed) return;

  const didSwitchSection = section !== previousSection;

  if (didSwitchSection) {
    if (section === "assistants") {
      showAssistantDefaultMainView();
    } else if (section === "bundles") {
      showBundlesLoadingMainView();
    } else if (section === "tasks") {
      showTasksLoadingMainView();
    }
  }

  if (state.activeNavSection === "bundles") {
    await refreshRequirementBundles();
    if (
      state.activeNavSection === "bundles" &&
      !state.secondaryPaneCollapsed &&
      !state.selectedBundleKey &&
      dom.workspaceDetailContent?.dataset.workspaceState === "bundles-loading"
    ) {
      showBundlesDefaultMainView();
    }
  }

  if (state.activeNavSection === "tasks") {
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

  if (sidebarWasCollapsed && !state.secondaryPaneCollapsed) {
    return;
  }
}

function renderRequirementBundleList(errorMessage = "") {
  if (!dom.bundleNavList) return;
  if (errorMessage) {
    dom.bundleNavList.innerHTML = `<div class="portal-inline-state is-error">${safe(errorMessage)}</div>`;
    return;
  }

  if (!state.requirementBundles.length) {
    dom.bundleNavList.innerHTML = '<div class="portal-bundle-list-state">No bundles found</div>';
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
      <div class="portal-bundle-meta">${safe(item.domain || "unknown")} · ${safe(item.status || "unknown")}</div>
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

async function refreshRequirementBundles() {
  if (!dom.bundleNavList) return;
  dom.bundleNavList.innerHTML = '<div class="portal-bundle-list-state">Loading bundles…</div>';
  try {
    const bundles = await api("/api/requirement-bundles");
    state.requirementBundles = Array.isArray(bundles) ? bundles : [];
    if (state.selectedBundleKey && !state.requirementBundles.some((item) => bundleKey(item) === state.selectedBundleKey)) {
      state.selectedBundleKey = null;
    }
    renderRequirementBundleList();
  } catch (error) {
    renderRequirementBundleList(`Failed to load bundles: ${error.message}`);
  }
}

async function openRequirementBundleInMain(bundleRef = null) {
  if (!dom.workspaceDetailContent) return;
  setMainView("detail");
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  dom.workspaceDetailContent.innerHTML = '<div class="portal-inline-state">Loading requirement bundles…</div>';
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

  if (!messages.length) {
    clearMessageListToWelcome();
    return;
  }

  dom.messageList.innerHTML = "";
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
    roleLabel.textContent = message.author_name || (isUser ? "You" : (state.selectedAgentName || "Assistant"));
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

      const msgAttachments = message.attachments || [];
      if (msgAttachments.length > 0) {
        const attachmentDiv = document.createElement("div");
        attachmentDiv.className = "message-attachments";
        attachmentDiv.dataset.attachments = JSON.stringify(msgAttachments);
        article.dataset.attachments = JSON.stringify(msgAttachments);
        msgAttachments.forEach(fileId => {
          const img = document.createElement("img");
          img.src = `/a/${state.selectedAgentId}/api/files/${encodeURIComponent(fileId)}`;
          img.className = "message-attachment-thumb";
          img.alt = fileId;
          img.dataset.fileId = fileId;
          img.onerror = () => { img.style.display = "none"; };
          attachmentDiv.appendChild(img);
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

  const storedEvents = Array.isArray(metadata?.thinking_events) ? metadata.thinking_events
    .filter((event) => isTrackableThinkingEvent(event?.type))
    .map((event) => ({ type: event.type, data: event.data || event, ts: event.ts || Date.now() / 1000 })) : [];
  if (storedEvents.length) attachThinkingToLatestAssistant(storedEvents);

  scrollToBottom();
}

async function loadSession(sessionId) {
  const normalized = (sessionId || "").trim();
  if (!normalized) return;

  const data = await agentApi(`/api/sessions/${encodeURIComponent(normalized)}`);
  updateSelectedAgentSession(normalized);
  // Ensure agent name is set
  if (!state.selectedAgentName && state.selectedAgentId) {
    const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
    state.selectedAgentName = agent?.name || null;
  }
  renderChatHistory(data.messages || [], data.metadata || {});
  addEditButtonsToMessages();

  setChatStatus(`Loaded session ${normalized}`);
  // Only open sessions panel if explicitly requested
}

async function openServerFiles() {
  const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
  if (!canWriteAgent(agent)) {
    setToolPanel("Server Files", `<div class="portal-inline-state is-error">You do not have permission to access this assistant's files.</div>`);
    return;
  }
  state.serverFilesRootPath = null;
  state.serverFilesCurrentPath = null;
  await loadServerFiles();
}

function buildServerFilesBreadcrumb(path, rootPath) {
  const normalizedRoot = String(rootPath || '').replace(/\/+$/, '');
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
  setToolPanel("Server Files", '<div class="portal-inline-state">Loading files…</div>');

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
          `<input type="checkbox" class="file-checkbox" data-path="${safePath}" data-is-dir="${item.is_dir}" aria-label="${escapeHtml(item.name)}">` +
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
          `</div>` +
        `</div>` +
        `<div class="portal-file-select-row">` +
          `<input type="checkbox" id="sf-select-all"> <label for="sf-select-all">Select all</label>` +
        `</div>` +
        `<div class="portal-panel-stack">${rows || '<div class="portal-inline-state">Empty directory</div>'}</div>` +
      `</div>`
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

      // Initialize button state
      updateDownloadButton(panel);
    }
  } catch (error) {
    setToolPanel("Server Files", `<div class="portal-inline-state is-error">Failed: ${safe(error.message)}</div>`);
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

function updateDownloadButton(panel) {
  const btn = panel.querySelector('.sf-download-btn');
  const selectAll = document.getElementById('sf-select-all');
  const checkboxes = panel.querySelectorAll('.file-checkbox:not([disabled])');
  const checkedBoxes = panel.querySelectorAll('.file-checkbox:not([disabled]):checked');

  const selected = getSelectedFiles(panel);
  if (btn) {
    btn.disabled = selected.length === 0;
    btn.textContent = selected.length > 0 ? `Download (${selected.length})` : 'Download';
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

    setToolPanel("Server Files", `<div class="portal-inline-state">Uploading ${escapeHtml(file.name)}…</div>`);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('path', targetPath);

      const resp = await fetch(`/a/${state.selectedAgentId}/api/server-files/upload`, {
        method: 'POST',
        body: formData
      });

      if (!resp.ok) {
        const errText = await resp.text();
        setToolPanel("Server Files", `<div class="portal-inline-state is-error">Upload failed: ${escapeHtml(errText)}</div>`);
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
        setToolPanel("Server Files", `<div class="portal-inline-state is-success">${message}</div>`);
        loadServerFiles(targetPath);
      } else {
        setToolPanel("Server Files", `<div class="portal-inline-state is-error">Upload failed: ${escapeHtml(data.error)}</div>`);
      }
    } catch (err) {
      setToolPanel("Server Files", `<div class="portal-inline-state is-error">Upload failed: ${escapeHtml(err.message)}</div>`);
    }
  };
  input.click();
}

function downloadSelectedFiles(paths) {
  if (paths.length === 0) return;

  // Use repeated query params to avoid comma ambiguity
  const url = new URL(`${window.location.origin}/a/${state.selectedAgentId}/api/server-files/download`);
  paths.forEach(p => url.searchParams.append('paths', p));
  window.open(url.toString());
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
        `<div class="portal-preview-image-wrap"><img src="${contentUrl}" class="max-w-full rounded" /></div>`
      );
      return;
    }
    if (binaryPreviewExtensions.pdf.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<iframe src="${contentUrl}" class="w-full" style="min-height: 70vh;" title="${escapeHtmlAttr(fileName)}"></iframe>`
      );
      return;
    }
    if (binaryPreviewExtensions.audio.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<audio controls src="${contentUrl}" class="w-full"></audio>`
      );
      return;
    }
    if (binaryPreviewExtensions.video.includes(ext)) {
      setToolPanel("File: " + fileName,
        `<div class="portal-file-preview-header">${breadcrumb}</div>` +
        `<video controls src="${contentUrl}" class="max-w-full rounded"></video>`
      );
      return;
    }

    const resp = await agentApi(`/api/server-files/read?path=${encodedPath}`);
    if (resp.error) throw new Error(resp.error);
    const content = resp.content || "(empty file)";
    setToolPanel("File: " + fileName,
      `<div class="portal-file-preview-header">${breadcrumb}</div>` +
      `<pre class="portal-panel-pre">${escapeHtml(content)}</pre>`
    );
  } catch (error) {
    setToolPanel("File Preview",
      `<div class="portal-inline-state is-error">Unable to preview this file: ${safe(error.message)}</div>`
    );
  }
}

async function openSkillsPanel() {
  if (!state.selectedAgentId) return;


  setToolPanel("Skills", '<div class="portal-inline-state">Loading skills…</div>');

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
    setToolPanel("Skills", `Failed: ${safe(error.message)}`);
  }
}


async function openUsagePanel() {
  if (!state.selectedAgentId) return;


  setToolPanel("Usage", '<div class="portal-inline-state">Loading usage…</div>');

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/usage/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("Usage", `Failed: ${safe(error.message)}`);
  }
}


async function openMyUploads() {
  if (!state.selectedAgentId) return;


  setToolPanel("Select Source", '<div class="portal-inline-state">Loading files…</div>');

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/files/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("Select Source", `Failed: ${safe(error.message)}`);
  }
}


function normalizeInstanceInputs(group) {
  const container = dom.toolPanelBody?.querySelector(`[data-instance-container="${group}"]`);
  const countInput = dom.toolPanelBody?.querySelector(`[data-instance-count="${group}"]`);
  if (!container || !countInput) return;

  const items = Array.from(container.querySelectorAll(`[data-instance-item="${group}"]`));
  items.forEach((item, idx) => {
    const title = item.querySelector("span");
    if (title) title.textContent = `Instance ${idx + 1}`;
    item.querySelectorAll("input[data-field]").forEach((input) => {
      const field = input.dataset.field;
      input.name = `${group}_instances_${idx}_${field}`;
    });
  });
  countInput.value = String(items.length);
}

function addInstanceRow(group) {
  const container = dom.toolPanelBody?.querySelector(`[data-instance-container="${group}"]`);
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
  normalizeInstanceInputs(group);

  // Initialize password toggles for newly added inputs
  if (window.initPasswordToggles) {
    window.initPasswordToggles();
  }
}


function initializeSettingsPanel() {
  const root = dom.toolPanelBody?.querySelector("#settings-panel-root");
  if (!root || !dom.toolPanelBody?.querySelector("#settings-form")) return;

  normalizeInstanceInputs("jira");
  normalizeInstanceInputs("confluence");

  if (root.dataset.actionsBound === "1") return;
  root.dataset.actionsBound = "1";

  root.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-settings-action]");
    if (!btn) return;
    event.preventDefault();

    const agentId = root.dataset.agentId || state.selectedAgentId;
    if (!agentId) {
      showToast("Please select an assistant first");
      return;
    }

    const action = btn.dataset.settingsAction;
    if (action === "generate-ssh-key") {
      if (typeof window.generateSSHKey === "function") {
        await window.generateSSHKey(agentId);
      }
      return;
    }

    if (action === "copy-config") {
      await copyAgentConfig(agentId);
      return;
    }

    if (action === "paste-config") {
      await pasteAgentConfig(agentId);
    }
  });
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
    state.inflightThinking = null;
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
  closeSessionsDrawer();
  updateSelectedAgentSession("");
  state.inflightThinking = null;
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

async function openEditDialog(agent) {
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
      form.elements["branch"].value = agent.branch || "master";
    }
  }

  // Show the modal
  const editModal = document.getElementById("edit-modal");
  if (editModal) {
    editModal.classList.remove("hidden");
    editModal.setAttribute("aria-hidden", "false");
  }
}

// Copy agent config to clipboard
async function copyAgentConfig(agentId) {
  try {
    // Fetch config from agent
    const resp = await fetch(`/a/${agentId}/api/config`);
    if (!resp.ok) throw new Error('Failed to fetch config');
    const data = await resp.json();

    const configStr = JSON.stringify(data.config, null, 2);

    // Use clipboard API or fallback
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(configStr);
    } else {
      // Fallback for non-secure context
      const textarea = document.createElement('textarea');
      textarea.value = configStr;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }

    // Show global toast
    showToast('Configuration copied to clipboard!');
  } catch (e) {
    // console.error('Failed to copy config:', e);
    showToast('Failed to copy: ' + e.message);
  }
}

// Paste agent config from clipboard - shows modal
let pasteModalAgentId = null;

async function pasteAgentConfig(agentId) {
  pasteModalAgentId = agentId;
  const modal = document.getElementById('paste-modal');
  const textarea = document.getElementById('paste-config-text');
  if (!modal || !textarea) {
    showToast('Paste modal not available');
    return;
  }
  textarea.value = '';
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  textarea.focus();
}

// Setup paste modal event listeners (call once on load)
function setupPasteModal() {
  const modal = document.getElementById('paste-modal');
  const closeBtn = document.getElementById('close-paste-modal');
  const cancelBtn = document.getElementById('cancel-paste-btn');
  const confirmBtn = document.getElementById('confirm-paste-btn');
  const textarea = document.getElementById('paste-config-text');

  if (!modal) return;

  function closePasteModal() {
    const successMsg = document.getElementById('paste-success-msg');
    if (successMsg) {
      successMsg.classList.add('hidden');
      successMsg.textContent = '';
    }
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    pasteModalAgentId = null;
  }

  if (closeBtn) closeBtn.addEventListener('click', closePasteModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closePasteModal);
  if (modal) {
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closePasteModal();
    });
  }

  if (confirmBtn) {
    confirmBtn.addEventListener('click', async function() {
      if (!pasteModalAgentId) return;

      const text = textarea.value.trim();
      if (!text) {
        showToast('Please paste configuration JSON');
        return;
      }

      try {
        const config = JSON.parse(text);

        const resp = await fetch(`/a/${pasteModalAgentId}/api/config/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config),
        });

        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.error || 'Failed to save config');
        }

        // Show global toast and close modal
        showToast('Configuration applied successfully!');
        setTimeout(closePasteModal, 1500);
      } catch (e) {
        showToast('Failed to apply: ' + e.message);
      }
    });
  }
}

// Initialize paste modal on load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupPasteModal);
} else {
  setupPasteModal();
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

// Add edit buttons to user messages
function addEditButtonsToMessages() {
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
      const contentEl = article.querySelector('.message-body, .whitespace-pre-wrap');
      const content = contentEl ? contentEl.textContent : '';
      const attachments = [];

      if (article.dataset.attachments) {
        try { attachments.push(...JSON.parse(article.dataset.attachments)); } catch (e) {}
      }

      if (attachments.length === 0) {
        const allUserArticles = Array.from(dom.messageList?.querySelectorAll('article[data-local-user="1"]') || []);
        const articleIndex = allUserArticles.indexOf(article);
        if (articleIndex >= 0 && articleIndex < state.attachmentHistory.length) {
          attachments.push(...state.attachmentHistory[articleIndex]);
        }
      }

      if (attachments.length === 0) {
        const images = article.querySelectorAll('img');
        images.forEach(img => {
          if (img.src && img.src.startsWith('blob:')) {
            const fileId = getFileIdFromBlobUrl(img.src);
            if (fileId) attachments.push(fileId);
          }
        });
      }

      openEditMessageModal(messageId, content, attachments);
    };

    container.appendChild(editBtn);
    container.tabIndex = 0;
    container.setAttribute("aria-label", "User message actions");
  });
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

    // Always include repo_url and branch (empty string to clear)
    if (repoUrl !== undefined) updates.repo_url = repoUrl || null;
    if (branch !== undefined) updates.branch = branch || null;

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
        // Remove the target message and subsequent messages from the UI
        // This ensures the old messages are cleared before the new ones are added
        if (dom.messageList) {
          const rows = Array.from(dom.messageList.querySelectorAll('.message-row'));
          const targetRowIndex = rows.findIndex((row) => {
            const article = row.querySelector('article[data-message-id]');
            return article && article.dataset.messageId === messageId;
          });

          if (targetRowIndex >= 0) {
            for (let i = rows.length - 1; i >= targetRowIndex; i -= 1) {
              rows[i].remove();
            }

            const userArticles = Array.from(dom.messageList.querySelectorAll('article[data-local-user="1"]'));
            const targetUserIndex = userArticles.findIndex((article) => article.dataset.messageId === messageId);
            if (targetUserIndex >= 0 && Array.isArray(state.attachmentHistory)) {
              state.attachmentHistory = state.attachmentHistory.slice(0, targetUserIndex);
            }
          } else {
            clearMessageListToWelcome();
            state.attachmentHistory = [];
          }
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
        
        // Trigger HTMX form submission to send the message
        htmx.trigger("#chat-form", "submit");
      } else {
        showToast(result.error || "Failed to delete message");
      }
    } catch (err) {
      showToast("Error editing message: " + err.message);
    }
  });

  dom.detailToggle?.addEventListener("click", () => {
    if (!state.selectedAgentId) {
      showToast("Please select an assistant first");
      return;
    }
    if (state.detailOpen) {
      setDetailOpen(false);
    } else {
      setDetailOpen(true);
      // Render agent details to tool panel
      const agent = state.mineAgents.find(a => a.id === state.selectedAgentId);
      if (agent) {
        dom.toolPanelTitle.textContent = "Assistant details";
        dom.toolPanelBody.innerHTML = `\n          <div id="agent-meta" class="portal-detail-card"></div>\n          <div id="agent-actions" class="portal-detail-actions"></div>\n        `;
        dom.agentMeta = document.getElementById("agent-meta");
        dom.agentActions = document.getElementById("agent-actions");
        renderAgentMeta(agent);
        renderAgentActions(agent, agent.status || "stopped");
      }
    }
  });
  dom.closeToolPanel?.addEventListener("click", closeToolPanel);
  dom.toolBackdrop?.addEventListener("click", closeToolPanel);

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
      if (state.isSubmittingChat) return;
      htmx.trigger("#chat-form", "submit");
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
  dom.headerNewChatBtn?.addEventListener("click", () => startNewChatForSelectedAgent());
  dom.railAssistantsBtn?.addEventListener("click", () => setActiveNavSection("assistants"));
  dom.bundlesMenuBtn?.addEventListener("click", () => setActiveNavSection("bundles"));
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

  dom.topSettings?.addEventListener("click", openSettings);

  dom.toolPanelBody?.addEventListener("click", async (event) => {
    const newChatBtn = event.target.closest("#sessions-new-chat-btn");
    if (newChatBtn) {
      event.preventDefault();
      closeSessionsDrawer();
      await startNewChatForSelectedAgent();
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

    const addBtn = event.target.closest('[data-action="add-instance"]');
    if (addBtn) {
      event.preventDefault();
      addInstanceRow(addBtn.dataset.group || "jira");
      return;
    }

    const removeBtn = event.target.closest('[data-action="remove-instance"]');
    if (removeBtn) {
      event.preventDefault();
      const group = removeBtn.dataset.group || "jira";
      removeBtn.closest(`[data-instance-item="${group}"]`)?.remove();
      normalizeInstanceInputs(group);
    }
  });

  dom.workspaceDetailContent?.addEventListener("click", async (event) => {
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
    }
  });

  dom.themeToggle?.addEventListener("click", toggleTheme);

  dom.usersMenuBtn?.addEventListener("click", async () => {
    setToolPanel("Users", '<div class="portal-inline-state">Loading users…</div>');
    try {
      await htmx.ajax("GET", "/app/users/panel", {
        target: "#tool-panel-body",
        swap: "innerHTML",
      });
    } catch (error) {
      setToolPanel("Users", `Failed: ${safe(error.message)}`);
    }
  });

  dom.tasksMenuBtn?.addEventListener("click", () => setActiveNavSection("tasks"));

  dom.addBundleBtn?.addEventListener("click", () => {
    dom.createBundleModal?.classList.remove("hidden");
    dom.createBundleModal?.setAttribute("aria-hidden", "false");
    if (dom.createBundleMsg) {
      dom.createBundleMsg.textContent = "";
      setModalFeedback(dom.createBundleMsg, "", dom.createBundleMsg.textContent);
    }
  });

  dom.closeCreateBundleModal?.addEventListener("click", () => {
    dom.createBundleModal?.classList.add("hidden");
    dom.createBundleModal?.setAttribute("aria-hidden", "true");
  });

  dom.addAgentBtn?.addEventListener("click", () => {
    document.getElementById("create-modal")?.classList.remove("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "false");
  });

  document.getElementById("close-create-modal")?.addEventListener("click", () => {
    document.getElementById("create-modal")?.classList.add("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
  });

  document.getElementById("create-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);
    const name = formData.get("name");
    const repoUrl = formData.get("repo_url");
    const branch = formData.get("branch");

    const msgEl = document.getElementById("create-msg");

    try {
      // Get defaults from config
      const defaultsResp = await fetch("/api/agents/defaults");
      if (!defaultsResp.ok) {
        throw new Error(await handleErrorResponse(defaultsResp));
      }
      const defaults = await defaultsResp.json();

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
      setTimeout(() => {
        document.getElementById("create-modal")?.classList.add("hidden");
        document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
        refreshAll();
      }, 1000);
    } catch (err) {
      msgEl.textContent = err.message;
      setModalFeedback(msgEl, "error", msgEl.textContent);
    }
  });

  dom.createBundleForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
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
      await setActiveNavSection("bundles", { toggleIfSame: false });
      await refreshRequirementBundles();
      state.selectedBundleKey = bundleKeyFromRef(detail.bundle_ref);
      renderRequirementBundleList();
      await openRequirementBundleInMain(detail.bundle_ref);
    } catch (err) {
      if (dom.createBundleMsg) {
        dom.createBundleMsg.textContent = err.message;
        setModalFeedback(dom.createBundleMsg, "error", dom.createBundleMsg.textContent);
      }
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
  const toolPanel = document.getElementById('tool-panel');
  if (resizeHandle && toolPanel) {
    let isResizing = false;
    resizeHandle.addEventListener('mousedown', (e) => {
      isResizing = true;
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', (e) => {
      if (!isResizing) return;
      const newWidth = window.innerWidth - e.clientX - 24; // 24px offset
      const minWidth = 300;
      const maxWidth = window.innerWidth - 24;
      const clampedWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));
      toolPanel.style.width = clampedWidth + 'px';
    });
    document.addEventListener('mouseup', () => {
      isResizing = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    });
  }

  bindEvents();
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

  // Form submit is handled by HTMX via hx-on::before-request

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

  await refreshAll();
  await setActiveNavSection("assistants", { toggleIfSame: false });
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
      item.innerHTML = '<label class="portal-checkbox-row"><input type="checkbox" id="sp-' + name + '-enabled" data-section="' + name + '" ' + (enabled ? 'checked' : '') + ' class="portal-system-prompt-check"' + disabledAttr + '><span>' + labels[name] + '</span></label>' + editButton;
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
    modal.innerHTML = '<div class="modal-backdrop" id="sp-editor-backdrop"></div><div class="modal-card panel portal-editor-modal-card"><div class="portal-modal-titlebar"><h3 id="sp-editor-title"></h3><button type="button" id="sp-editor-close" class="portal-modal-close" aria-label="Close">✕</button></div><div class="stack"><label class="portal-checkbox-row"><input type="checkbox" id="sp-editor-enabled"><span>Enable custom prompt for this section</span></label><textarea id="sp-editor-content" class="portal-form-textarea" rows="10" placeholder="Enter content..."></textarea><div class="portal-modal-actions"><button type="button" id="sp-editor-cancel" class="portal-btn is-secondary">Cancel</button><button type="button" id="sp-editor-save" class="portal-btn is-primary">Save</button></div></div></div>';
    document.body.appendChild(modal);

    modal._keyHandler = function(e) {
      if (e.key === 'Escape') {
        closeSystemPromptEditor();
      }
    };

    document.getElementById('sp-editor-close').addEventListener('click', closeSystemPromptEditor);
    document.getElementById('sp-editor-backdrop').addEventListener('click', closeSystemPromptEditor);
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
