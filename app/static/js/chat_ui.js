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
  messageList: document.getElementById("message-list"),
  chatInput: document.getElementById("chat-input"),
  chatSuggest: document.getElementById("chat-suggest"),
  chatAgentId: document.getElementById("chat-agent-id"),
  chatSessionId: document.getElementById("chat-session-id"),
  chatStatus: document.getElementById("chat-status"),
  sendChatBtn: document.getElementById("send-chat-btn"),
  uploadInput: document.getElementById("upload-input"),
  detailPanel: document.getElementById("detail-panel"),
  detailBackdrop: document.getElementById("detail-backdrop"),
  detailToggle: document.getElementById("detail-toggle"),
  detailClose: document.getElementById("detail-close"),
  toolPanel: document.getElementById("tool-panel"),
  toolPanelTitle: document.getElementById("tool-panel-title"),
  toolPanelBody: document.getElementById("tool-panel-body"),
  toolBackdrop: document.getElementById("tool-backdrop"),
  closeToolPanel: document.getElementById("close-tool-panel"),
  agentMeta: document.getElementById("agent-meta"),
  agentActions: document.getElementById("agent-actions"),
  topSettings: document.getElementById("top-settings"),
  uploadInput: document.getElementById("upload-input"),
  topUploadInline: document.getElementById("top-upload-inline"),
  logoutBtn: document.getElementById("logout-btn"),
  themeToggle: document.getElementById("theme-toggle"),
  usersMenuBtn: document.getElementById("users-menu-btn"),
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
        showToast('Please select an agent first');
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
  cachedSkills: [],
  cachedSkillsByAgent: new Map(),
  cachedMentionFiles: [],
  selectedSuggestionIndex: -1,
  // UI-only state: portal stores current selected session id per agent.
  // Runtime remains source-of-truth for full session history/messages.
  agentSessionIds: new Map(),
  isSubmittingChat: false,
  pendingMessage: "",
  currentUserId: Number(dom.appRoot?.dataset.userId || 0),
  currentUserName: dom.appRoot?.dataset.nickname || dom.appRoot?.dataset.username || "You",
  currentUserRole: String(dom.appRoot?.dataset.role || "user"),
  eventWs: null,
  eventWsAgentId: null,
  inflightThinking: null,
  pendingFiles: [],
  // Backup for restore on error
  pendingFilesBackup: [],
  messageBackup: "",
};

const md = window.markdownit({
  html: false,
  linkify: true,
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
      statusBadge = '<span class="absolute top-1 left-1 text-xs bg-yellow-500 text-white px-1 rounded">⏳</span>';
    } else if (pf.status === 'uploaded') {
      statusBadge = '<span class="absolute top-1 left-1 text-xs bg-green-500 text-white px-1 rounded">✓</span>';
    } else if (pf.status === 'failed') {
      statusBadge = '<span class="absolute top-1 left-1 text-xs bg-red-500 text-white px-1 rounded">✗</span>';
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

function buildUserMessageArticle(text, attachments = []) {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  let attachmentHtml = '';
  if (attachments.length > 0) {
    attachmentHtml = `<div class="flex flex-wrap gap-2 mt-2">${attachments.map(a => {
      const safeName = (a.name || '').replace(/[<>"'&]/g, '');
      const safeUrl = escapeHtmlAttr(a.previewUrl || a.url || '');
      const safeNameAttr = escapeHtmlAttr(safeName);
      if (a.type === 'image') {
        return `<img src="${safeUrl}" class="max-w-32 max-h-32 rounded-lg border border-slate-500 cursor-pointer hover:opacity-80" alt="${safeNameAttr}" data-preview-url="${safeUrl}" data-preview-name="${safeNameAttr}" data-is-image="true" />`;
      } else {
        return `<div class="flex items-center gap-1 px-2 py-1 rounded bg-slate-700 text-xs cursor-pointer hover:bg-slate-600" data-preview-url="${safeUrl}" data-preview-name="${safeNameAttr}" data-is-image="false">📄 ${safeName}</div>`;
      }
    }).join('')}</div>`;
  }

  return `<div class="flex flex-col items-end"><div class="flex items-center gap-2 mb-1"><span class="text-xs font-semibold text-blue-400">${state.currentUserName || "You"}</span><span class="text-xs text-slate-500">${now}</span></div><article class="max-w-2xl rounded-2xl border border-blue-500/50 bg-blue-600/20 px-4 py-3 text-blue-50" data-local-user="1"><div class="whitespace-pre-wrap text-sm">${safe(text)}</div>${attachmentHtml}</article></div>`;
}

function buildPendingAssistantArticle() {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const pendingAgentName = state.selectedAgentName || "Assistant";
  return `<div class="flex flex-col items-start"><div class="flex items-center gap-2 mb-1"><span class="text-xs font-semibold text-emerald-400">${escapeHtml(pendingAgentName)}</span><span class="text-xs text-slate-500">${now}</span></div><article class="max-w-2xl rounded-2xl border border-slate-600 bg-slate-800 px-4 py-3 assistant-message text-slate-100" data-pending-assistant="1"><div class="text-slate-300">Thinking...</div></article></div>`;
}

function removePendingAssistantPlaceholder() {
  if (!dom.messageList) return;
  dom.messageList.querySelectorAll('[data-pending-assistant="1"]').forEach((el) => el.remove());
}

function disconnectEventSocket() {
  if (state.eventWs) {
    try { state.eventWs.close(); } catch {}
  }
  state.eventWs = null;
  state.eventWsAgentId = null;
}

function isTrackableThinkingEvent(type) {
  return ["iteration_start", "llm_thinking", "tool_call", "tool_result", "skill_matched", "complete"].includes(type);
}

function getThinkingEventDisplay(event) {
  const type = event?.type || "event";
  const data = event?.data || {};
  const byType = {
    iteration_start: { icon: "rotate-cw", title: "Iteration Start", detail: `Iteration ${data.iteration || 1}${data.total ? `/${data.total}` : ""}` },
    llm_thinking: { icon: "brain", title: "LLM Thinking", detail: data.message || data.thinking || "Model is reasoning" },
    tool_call: { icon: "wrench", title: "Tool Call", detail: data.tool ? `Calling ${data.tool}` : "Calling tool", args: data.args },
    tool_result: { icon: data.success === false ? "x-circle" : "check-circle-2", title: "Tool Result", detail: data.success === false ? (data.error || "Tool failed") : (data.tool ? `${data.tool} completed` : "Tool completed"), result: data.result, output: data.output },
    skill_matched: { icon: "zap", title: "Skill Matched", detail: normalizeSkillCommand(data.skill) || "Skill matched", skill: data.skill },
    complete: { icon: "flag", title: "Complete", detail: "Execution complete", response: data.response, total_iterations: data.total_iterations },
  };
  return byType[type] || { icon: "circle", title: type.replaceAll("_", " "), detail: "" };
}

// Open Thinking Process panel - using backend rendering
async function openThinkingProcessPanel() {
  if (!state.selectedAgentId) {
    showToast('Please select an agent first');
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
    setToolPanel("Thinking Process", '<div class="text-xs text-slate-400">No session selected. Start a conversation first.</div>');
    return;
  }
  
  // Use htmx to load backend-rendered panel
  setToolPanel("Thinking Process", '<div class="text-xs text-slate-400">Loading...</div>');
  
  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/thinking/panel?session_id=${encodeURIComponent(currentSessionId)}`, {
      target: "#tool-panel-body",
      swap: "innerHTML"
    });
  } catch (err) {
    setToolPanel("Thinking Process", `<div class="text-xs text-red-500">Error: ${err.message}</div>`);
  }
}

function renderThinkingProcess(article, events) {
  if (!article) return;

  const isDark = document.documentElement.classList.contains("dark");
  let host = article.querySelector('[data-thinking-process="1"]');
  if (!host) {
    host = document.createElement("div");
    host.dataset.thinkingProcess = "1";
    host.className = isDark
      ? "mt-3 rounded-xl border border-slate-600 bg-slate-800/50 p-2"
      : "mt-3 rounded-xl border border-slate-200 bg-slate-50/80 p-2";
    article.append(host);
  }

  const expanded = host.dataset.expanded === "1";
  const count = events.length;
  const rows = events.map((event, idx) => {
    const view = getThinkingEventDisplay(event);
    const border = idx === events.length - 1 ? "" : (isDark ? " border-slate-600" : " border-slate-200");
    const iconBg = isDark ? "bg-slate-700 border-slate-600 text-slate-300" : "bg-white border-slate-300 text-slate-500";
    const titleColor = isDark ? "text-slate-200" : "text-slate-700";
    const detailColor = isDark ? "text-slate-400" : "text-slate-500";
    return `<div class="relative pl-6 pb-3${border}"><span class="absolute left-0 top-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full border ${iconBg}"><i data-lucide="${view.icon}" class="h-3 w-3"></i></span><div class="text-xs font-semibold ${titleColor}">${safe(view.title)}</div><div class="text-xs ${detailColor} whitespace-pre-wrap">${safe(view.detail || "")}</div></div>`;
  }).join("");

  const btnClass = isDark
    ? "w-full inline-flex items-center justify-between gap-2 rounded-lg border border-slate-600 bg-slate-700 px-2 py-1.5 text-xs text-slate-200 hover:bg-slate-600"
    : "w-full inline-flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-100";
  const waitingMsg = isDark ? "text-slate-400" : "text-slate-500";

  host.innerHTML = `
    <button type="button" data-thinking-toggle="1" class="${btnClass}">
      <span class="inline-flex items-center gap-1.5"><i data-lucide="brain"></i>View Thinking Process (${count} steps)</span>
      <i data-lucide="${expanded ? "chevron-up" : "chevron-down"}"></i>
    </button>
    <div data-thinking-timeline="1" class="mt-2 ${expanded ? "" : "hidden"}">
      ${count ? rows : `<div class="text-xs ${waitingMsg} px-1 py-1">Waiting for runtime events…</div>`}
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

function handleAgentEventMessage(raw) {
  if (!state.inflightThinking) return;

  let payload = null;
  try { payload = JSON.parse(raw); } catch { return; }
  const type = payload?.type;
  if (!isTrackableThinkingEvent(type)) return;

  const entry = { type, data: payload?.data || {}, ts: payload?.ts || Date.now() / 1000 };
  state.inflightThinking.events.push(entry);

  const pendingArticle = dom.messageList?.querySelector(`[data-thinking-id="${state.inflightThinking.id}"]`);
  if (pendingArticle) renderThinkingProcess(pendingArticle, state.inflightThinking.events);

  if (type === "complete") state.inflightThinking.completed = true;
}

function ensureEventSocketForSelectedAgent() {
  const agentId = state.selectedAgentId;
  if (!agentId) return;

  if (state.eventWs && state.eventWsAgentId === agentId && state.eventWs.readyState === WebSocket.OPEN) return;
  if (state.eventWs && state.eventWsAgentId !== agentId) disconnectEventSocket();
  if (state.eventWs?.readyState === WebSocket.CONNECTING) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${window.location.host}/a/${agentId}/api/events`);
  state.eventWs = ws;
  state.eventWsAgentId = agentId;

  ws.onmessage = (event) => handleAgentEventMessage(event.data);
  ws.onclose = () => {
    if (state.eventWs === ws) {
      state.eventWs = null;
      state.eventWsAgentId = null;
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
  if (dom.chatStatus) {
    dom.chatStatus.textContent = text;
    dom.chatStatus.className = isError ? "text-xs text-red-400" : "text-xs text-slate-400";
  }
}

function scrollToBottom() {
  if (dom.messageList) dom.messageList.scrollTop = dom.messageList.scrollHeight;
}

function renderMarkdown(scope = document) {
  scope.querySelectorAll(".md-render").forEach((el) => {
    let html = md.render(decodeHtml(el.dataset.md) || "");
      el.innerHTML = html;
      // Add target="_blank" to all links via DOM
      el.querySelectorAll('a').forEach(a => {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      });
  });
  scope.querySelectorAll("pre code").forEach((el) => hljs.highlightElement(el));
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
  // Close tool panel when opening detail panel (mutual exclusivity)
  if (open) {
    closeToolPanel();
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

async function agentApi(path, options = {}) {
  if (!state.selectedAgentId) throw new Error("No selected agent");
  return api(`/a/${state.selectedAgentId}${path}`, options);
}

function defaultWelcomeMessage() {
  const welcomeAgentName = state.selectedAgentName || "Assistant";
  return `<article data-welcome="1" class="max-w-2xl rounded-2xl border border-slate-700 bg-slate-800/80 p-4"><p class="text-xs uppercase tracking-wide text-slate-400 mb-2">${escapeHtml(welcomeAgentName)}</p><div class="prose prose-invert max-w-none">👋 Welcome! Ask me anything.</div></article>`;
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
    dom.mineList.innerHTML = '<div class="text-slate-500 text-sm">No agents</div>';
    return;
  }

  const mine = state.mineAgents.filter((agent) => Number(agent.owner_user_id) === state.currentUserId && agent.visibility !== "public");
  const shared = state.mineAgents.filter((agent) => Number(agent.owner_user_id) !== state.currentUserId && agent.visibility !== "public");
  const publicAgents = state.mineAgents.filter((agent) => agent.visibility === "public");

  const renderSection = (title, agents) => {
    if (!agents.length) return;
    const section = document.createElement("div");
    section.className = "space-y-2";
    section.innerHTML = `<div class="text-xs uppercase tracking-wide text-slate-400 mt-2">${safe(title)}</div>`;

    agents.forEach((agent) => {
      const status = state.agentStatus.get(agent.id)?.status || agent.status;
      const activeClass = state.selectedAgentId === agent.id
        ? "border-blue-500 bg-blue-500/10"
        : "border-slate-700 bg-slate-800/40";
      const badge = Number(agent.owner_user_id) === state.currentUserId ? "" : '<span class="ml-2 text-[10px] px-1.5 py-0.5 rounded-full border border-slate-200 bg-slate-100 text-slate-500">shared</span>';

      const button = document.createElement("button");
      button.className = `w-full rounded-xl border px-3 py-2 text-left ${activeClass}`;
      button.innerHTML = `<div class="flex items-center justify-between"><span class="font-medium">${safe(agent.name)}${badge}</span><span class="h-2.5 w-2.5 rounded-full ${status === "running" ? "bg-emerald-400" : "bg-slate-500"}"></span></div>`;
      button.addEventListener("click", () => selectAgentById(agent.id));
      section.append(button);
    });

    dom.mineList.append(section);
  };

  renderSection("My Space", mine);
  renderSection("Shared", shared);
  if (publicAgents.length) renderSection("Public", publicAgents);
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
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Repository</div>
        <div class="font-mono text-xs bg-slate-100 dark:bg-slate-800 rounded px-2 py-1.5 break-all text-slate-700 dark:text-slate-300">${safe(agent.repo_url)}</div>
        <div class="text-xs text-slate-500 mt-1">Branch: <span class="text-slate-700 dark:text-slate-300">${safe(branch)}</span></div>
        <div id="agent-git-commit" class="text-xs text-slate-400 mt-1">Loading commit...</div>
      </div>
    `;
  }

  dom.agentMeta.innerHTML = `
    <div class="space-y-3 text-sm">
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Image</div>
        <div class="font-mono text-xs bg-slate-100 dark:bg-slate-800 rounded px-2 py-1.5 break-all text-slate-700 dark:text-slate-300">${safe(agent.image)}</div>
      </div>
      ${repoSection}
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Created</div>
        <div class="text-slate-700 dark:text-slate-300">${dateStr}</div>
      </div>
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Resources</div>
        <div class="flex flex-wrap gap-1.5">
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 text-xs font-medium">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
            ${cpu}
          </span>
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 text-xs font-medium">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
            ${mem}
          </span>
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 text-xs font-medium">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path></svg>
            ${disk}Gi
          </span>
        </div>
      </div>
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Description</div>
        <div class="text-slate-700 dark:text-slate-300">${safe(agent.description || '-')}</div>
      </div>
      <div id="agent-usage" class="text-xs text-slate-400">Loading usage...</div>
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
    const data = await api(`/api/agents/${agentId}/git-info`);
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
        commitLink.className = 'text-blue-500 hover:underline font-mono';
        commitLink.textContent = shortCommit;
        commitEl.appendChild(commitLink);
      } else {
        const commitText = document.createElement('span');
        commitText.className = 'text-blue-500 font-mono';
        commitText.textContent = shortCommit;
        commitEl.appendChild(commitText);
      }
    } else if (data.status === 'running') {
      commitEl.textContent = "Commit: Not available";
    } else if (data.status === 'error') {
      commitEl.textContent = "Git info unavailable";
    } else {
      commitEl.textContent = "Agent not running";
    }
  } catch (e) {
    commitEl.textContent = "Failed to load commit";
  }
}

async function fetchUsageForAgent(agentId) {
  const usageEl = document.getElementById("agent-usage");
  if (!usageEl) return;
  try {
    const data = await api(`/api/agents/${agentId}/usage`);
    if (!data) {
      usageEl.textContent = "No usage data";
      return;
    }
    const global = data.global || {};
    const reqCount = global.request_count || 0;
    const cost = global.total_cost_usd || global.total_cost || 0;
    const inputTokens = global.total_input_tokens || global.total_input || 0;
    const outputTokens = global.total_output_tokens || global.total_output || 0;
    usageEl.innerHTML = `
      <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Usage (30 days)</div>
      <div class="grid grid-cols-2 gap-2">
        <div class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-2 py-2">
          <div class="text-slate-500 dark:text-slate-400">Requests</div>
          <div class="font-semibold text-slate-700 dark:text-slate-200">${reqCount}</div>
        </div>
        <div class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-2 py-2">
          <div class="text-slate-500 dark:text-slate-400">Cost</div>
          <div class="font-semibold text-slate-700 dark:text-slate-200">$${cost.toFixed(4)}</div>
        </div>
        <div class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-2 py-2">
          <div class="text-slate-500 dark:text-slate-400">Input</div>
          <div class="font-semibold text-slate-700 dark:text-slate-200">${inputTokens.toLocaleString()}</div>
        </div>
        <div class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-2 py-2">
          <div class="text-slate-500 dark:text-slate-400">Output</div>
          <div class="font-semibold text-slate-700 dark:text-slate-200">${outputTokens.toLocaleString()}</div>
        </div>
      </div>
    `;
  } catch (e) {
    usageEl.textContent = "No usage data";
  }
}

function renderAgentActions(agent, status) {
  if (!dom.agentActions) return;

  dom.agentActions.innerHTML = "";
  const writable = canWriteAgent(agent);

  const container = document.createElement("div");
  container.className = "space-y-2 rounded-xl border border-slate-700 bg-slate-800/40 p-2";

  const grid = document.createElement("div");
  grid.className = "grid grid-cols-4 gap-2";

  const buildIconBtn = ({ label, icon, classes, onClick, disabled = false }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.title = label;
    button.className = `h-14 rounded-lg border text-white inline-flex flex-col items-center justify-center gap-1 shadow-sm ${classes}`;
    button.innerHTML = `
      <i data-lucide="${icon}" class="w-4 h-4"></i>
      <span class="text-[10px] font-medium opacity-90">${label}</span>
    `;
    button.disabled = disabled;
    button.addEventListener("click", onClick);
    return button;
  };

  const actions = [
    { label: "Start", icon: "play", classes: "border-emerald-600 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable || !(status === "stopped" || status === "failed"), onClick: () => action(`/api/agents/${agent.id}/start`) },
    { label: "Stop", icon: "square", classes: "border-amber-500 bg-amber-500 hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable || status !== "running", onClick: () => action(`/api/agents/${agent.id}/stop`) },
    { label: "Restart", icon: "rotate-cw", classes: "border-blue-500 bg-blue-500 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable || status !== "running", onClick: () => action(`/api/agents/${agent.id}/restart`) },
    { label: agent.visibility === "public" ? "Unshare" : "Share", icon: agent.visibility === "public" ? "lock" : "share-2", classes: "border-indigo-500 bg-indigo-500 hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`) },
    { label: "Edit", icon: "pencil", classes: "border-slate-500 bg-slate-500 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => openEditDialog(agent) },
    { label: "Delete", icon: "trash-2", classes: "border-red-500 bg-red-500 hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/delete-runtime`, "DELETE", true) },
    { label: "Destroy", icon: "flame", classes: "border-rose-600 bg-rose-600 hover:bg-rose-700 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/destroy`, "POST", true) },
  ];

  actions.forEach((cfg) => grid.append(buildIconBtn(cfg)));
  container.append(grid);

  if (!writable) {
    const note = document.createElement("div");
    note.className = "text-xs text-slate-400";
    note.textContent = "Read-only for shared agent.";
    container.append(note);
  }

  dom.agentActions.append(container);
  renderIcons();
}

async function selectAgentById(agentId) {
  state.selectedAgentId = agentId;
  // Get agent name from state or lookup
  const allAgents = state.mineAgents || [];
  const selectedAgent = allAgents.find(a => a.id === agentId);
  state.selectedAgentName = escapeHtml(selectedAgent?.name) || null;
  
  // Update owner-only button visibility
  updateOwnerOnlyButtons(agentId);

  window.selectedAgentId = agentId;  // Expose for inline scripts
  if (agentId) localStorage.setItem(LAST_AGENT_STORAGE_KEY, agentId);
  state.cachedSkills = state.cachedSkillsByAgent.get(agentId) || [];
  state.cachedMentionFiles = [];
  state.selectedSuggestionIndex = -1;
  state.inflightThinking = null;
  disconnectEventSocket();

  if (dom.chatAgentId) dom.chatAgentId.value = agentId || "";
  syncHiddenSessionInputFromState();
  clearMessageListToWelcome();

  renderAgentList();
  await syncSelectedAgentState();
}

async function syncSelectedAgentState() {
  const agent = state.mineAgents.find((item) => item.id === state.selectedAgentId);

  if (!agent) {
    dom.embedTitle.textContent = "Select an agent";
    dom.selectedStatus.textContent = "idle";
    dom.centerPlaceholder.classList.remove("hidden");
    dom.agentChatApp.classList.add("hidden");
    return;
  }

  const status = state.agentStatus.get(agent.id)?.status || agent.status;
  dom.embedTitle.textContent = agent.name;
  dom.selectedStatus.textContent = status;
  if (dom.selectedStatus) {
    dom.selectedStatus.className = "px-3 py-1 rounded-full text-xs border";
    if (status === "running") dom.selectedStatus.classList.add("border-emerald-200", "dark:border-emerald-500", "bg-emerald-50", "dark:bg-emerald-900/40", "text-emerald-700", "dark:text-emerald-400");
    else if (status === "failed") dom.selectedStatus.classList.add("border-rose-200", "dark:border-rose-500", "bg-rose-50", "dark:bg-rose-900/40", "text-rose-700", "dark:text-rose-400");
    else dom.selectedStatus.classList.add("border-slate-200", "dark:border-slate-600", "bg-slate-100", "dark:bg-slate-800", "text-slate-600", "dark:text-slate-400");
  }

  if (dom.chatAgentId) dom.chatAgentId.value = agent.id;
  syncHiddenSessionInputFromState();

  renderAgentMeta(agent);
  renderAgentActions(agent, status);

  const running = status === "running";
  dom.centerPlaceholder.classList.toggle("hidden", running);
  dom.agentChatApp.classList.toggle("hidden", !running);

  // Auto-load last session if available (localStorage or remote)
  if (running) {
    const lastSessionId = getLastSessionId(agent.id);
    if (lastSessionId) {
      try {
        await loadSession(lastSessionId);
      } catch (e) {
        // Session not found locally, try remote
        await loadLastSessionFromRemote(agent.id);
      }
    } else {
      // No local session, fetch from remote
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
    console.log("Failed to load last session from remote:", e);
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

  // Build attachments from pendingFiles for display
  setChatSubmitting(true);
  removeWelcomeMessageIfPresent();
  removePendingAssistantPlaceholder();
  hideSuggest();

  // Build attachments from pending files for display
  const displayAttachments = state.pendingFiles.map(pf => ({
    name: pf.file.name,
    type: pf.isImage ? 'image' : 'file',
    previewUrl: pf.previewUrl,
    url: pf.uploadedData?.url
  }));

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
  setChatStatus("Sending...");

  // Note: attachments is now set via htmx:configRequest event
  // HTMX will submit the form with attachments in the payload
}

function handleChatResponseError(event) {
  if (event.target?.id !== "chat-form") return;

  removePendingAssistantPlaceholder();
  setChatSubmitting(false);
  state.inflightThinking = null;

  // Restore message and files from backup
  if (messageBackup || pendingFilesBackup.length > 0) {
    if (dom.chatInput) dom.chatInput.value = messageBackup;
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
    const errorDiv = document.createElement("div");
    errorDiv.className = "message message-error flex gap-3 py-3";
    errorDiv.innerHTML = `
      <div class="w-8 h-8 rounded-full bg-red-500/20 flex items-center justify-center flex-shrink-0">
        <i data-lucide="alert-circle" class="w-4 h-4 text-red-400"></i>
      </div>
      <div class="flex-1 min-w-0">
        <div class="text-red-400 text-sm">${errorMsg.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
      </div>
    `;
    dom.messageList.appendChild(errorDiv);
    renderIcons();
    scrollToBottom();
  }
}

function handleChatAfterRequest(event) {
  if (event.target?.id !== "chat-form") return;
  if (!event.detail?.successful) return;

  setChatSubmitting(false);
  state.pendingMessage = "";
  state.messagePrepared = false;
}

function handleChatAfterSwap(target) {
  if (target?.id !== "message-list") return;

  const pendingEvents = state.inflightThinking?.events ? [...state.inflightThinking.events] : [];
  removePendingAssistantPlaceholder();
  if (pendingEvents.length) attachThinkingToLatestAssistant(pendingEvents);
  state.inflightThinking = null;

  // OOB swap from chat partial updates hidden #chat-session-id. Keep per-agent session state in sync.
  // Re-query the element each time since OOB swap replaces the DOM element
  const sessionFromInput = document.getElementById("chat-session-id")?.value || "";
  updateSelectedAgentSession(sessionFromInput);

  renderMarkdown(dom.messageList);
  decorateToolMessages(dom.messageList);
  renderIcons();
  scrollToBottom();

  // Clean up any orphan header divs after all processing (divs without article child)
  const messageList = target;
  const children = Array.from(messageList.children);
  children.forEach(child => {
    // Check if it's a container div without an article child (orphan)
    if (child.tagName === 'DIV' && !child.querySelector('article') && child.querySelector('span')) {
      // Check if it has Assistant text
      if (child.textContent.includes('Assistant') || child.querySelector('.text-emerald-400')) {
        child.remove();
      }
    }
  });

  // Add timestamp to server-rendered Assistant messages if missing
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const assistantContainers = messageList.querySelectorAll('.assistant-header:not([data-timestamp-added])');
  assistantContainers.forEach(header => {
    const timeSpan = document.createElement("span");
    timeSpan.className = "text-xs text-slate-500";
    timeSpan.textContent = now;
    header.appendChild(timeSpan);
    header.setAttribute("data-timestamp-added", "true");
  });

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
      const uploadedFileIds = state.pendingFiles
        .filter(pf => pf.file_id && pf.status === 'uploaded')
        .map(pf => pf.file_id);
      event.detail.parameters.attachments = JSON.stringify(uploadedFileIds);

    }
  });

  document.addEventListener("htmx:beforeRequest", handleChatBeforeRequest);
  document.addEventListener("htmx:afterRequest", handleChatAfterRequest);
  document.addEventListener("htmx:afterSwap", (event) => {
    handleChatAfterSwap(event.target);
    if (event.target?.id === "tool-panel-body") initializeSettingsPanel();
    renderIcons();
  });
  document.addEventListener("htmx:responseError", handleChatResponseError);
}

// ===== suggestion popup hooks =====
function hideSuggest() {
  if (!dom.chatSuggest) return;
  dom.chatSuggest.classList.add("hidden");
  dom.chatSuggest.innerHTML = "";
}

function showSuggest(items, onPick) {
  if (!dom.chatSuggest) return;
  if (!items.length) {
    hideSuggest();
    return;
  }

  dom.chatSuggest.innerHTML = items.map((item, index) => (
    `<button type="button" data-i="${index}" class="w-full text-left rounded-lg px-2 py-1 hover:bg-slate-700"><div class="font-medium">${safe(item.label || item.title || "")}</div><div class="text-xs text-slate-400">${safe(item.desc || "")}</div></button>`
  )).join("");
  dom.chatSuggest.classList.remove("hidden");
  state.selectedSuggestionIndex = 0;

  const buttons = Array.from(dom.chatSuggest.querySelectorAll("button"));
  buttons.forEach((button) => {
    button.addEventListener("click", () => onPick(items[Number(button.dataset.i)]));
  });
  buttons[0]?.classList.add("bg-slate-700");
}

function moveSuggestionSelection(direction) {
  if (!dom.chatSuggest || dom.chatSuggest.classList.contains("hidden")) return;
  const buttons = Array.from(dom.chatSuggest.querySelectorAll("button"));
  if (!buttons.length) return;

  buttons.forEach((b) => b.classList.remove("bg-slate-700"));
  state.selectedSuggestionIndex = (state.selectedSuggestionIndex + direction + buttons.length) % buttons.length;
  const selected = buttons[state.selectedSuggestionIndex];
  selected.classList.add("bg-slate-700");
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

  const text = dom.chatInput.value;
  const cursor = dom.chatInput.selectionStart;
  const before = text.slice(0, cursor);
  const slash = before.match(/(^|\s)\/(\w*)$/);
  const at = before.match(/(^|\s)@(\w*)$/);

  if (slash) {
    if (!state.cachedSkills.length) {
      try {
        const data = await agentApi("/api/skills");
        state.cachedSkills = (data.skills || []).map(toSkillSuggestion).filter((item) => item.command);
        if (state.selectedAgentId) state.cachedSkillsByAgent.set(state.selectedAgentId, state.cachedSkills);
      } catch {
        state.cachedSkills = [];
      }
    }

    showSuggest(state.cachedSkills, (item) => {
      const command = normalizeSkillCommand(item.command || item.label || item.title);
      if (!command) return;
      // Replace from the start of "/" to cursor
      const start = slash.index;
      dom.chatInput.setRangeText(`${command} `, start, cursor, "end");
      hideSuggest();
    });
    return;
  }

  if (at) {
    if (!state.cachedMentionFiles.length) {
      try {
        const data = await agentApi("/api/files/list");
        state.cachedMentionFiles = (data.files || []).map((item) => ({
          title: `@file_${item.file_id.slice(0, 8)}`,
          desc: item.filename,
          full: `@file_${item.file_id}`,
        }));
      } catch {
        state.cachedMentionFiles = [];
      }
    }

    showSuggest(state.cachedMentionFiles, (item) => {
      // Replace from the start of "@" to cursor
      const start = at.index;
      dom.chatInput.setRangeText(`${item.full} `, start, cursor, "end");
      hideSuggest();
    });
    return;
  }

  hideSuggest();
}

// ===== toolbar actions =====
function setToolPanel(title, contentHtml) {
  if (!dom.toolPanel) return;
  dom.toolPanelTitle.textContent = title;
  // Use textContent for error messages to prevent XSS, innerHTML for HTML content
  if (typeof contentHtml === 'string' && contentHtml.startsWith('Failed:')) {
    dom.toolPanelBody.textContent = contentHtml.replace('Failed: ', '');
  } else {
    dom.toolPanelBody.innerHTML = contentHtml;
  }
  // Hide detail panel, show tool panel
  setDetailOpen(false);
  dom.toolPanel.style.transform = "translateX(0)";
  dom.toolBackdrop?.classList.remove("hidden");
}

function closeToolPanel() {
  if (dom.toolPanel) dom.toolPanel.style.transform = "translateX(120%)";
  dom.toolBackdrop?.classList.add("hidden");
}

async function openSessionsPanel() {
  if (!state.selectedAgentId) return;

  setToolPanel("Sessions", '<div class="text-xs text-slate-400">Loading sessions…</div>');

  await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/sessions/panel?current_session_id=${encodeURIComponent(currentSessionIdForSelectedAgent())}&limit=12`, {
    target: "#tool-panel-body",
    swap: "innerHTML",
  });
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

    // Format timestamp
    let timeStr = "";
    if (message.timestamp) {
      try {
        const ts = new Date(message.timestamp);
        timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (e) {}
    }

    // Create message container
    const container = document.createElement("div");
    container.className = isUser ? "flex flex-col items-end" : "flex flex-col items-start";

    // Role label and timestamp
    const header = document.createElement("div");
    header.className = "flex items-center gap-2 mb-1";

    const roleLabel = document.createElement("span");
    roleLabel.className = isUser ? "text-xs font-semibold text-blue-400" : "text-xs font-semibold text-emerald-400";
    const agentName = state.selectedAgentName || "Assistant";
    roleLabel.textContent = isUser ? (state.currentUserName || "You") : agentName;

    header.appendChild(roleLabel);

    if (timeStr) {
      const timeLabel = document.createElement("span");
      timeLabel.className = "text-xs text-slate-500";
      timeLabel.textContent = timeStr;
      header.appendChild(timeLabel);
    }

    container.appendChild(header);

    // Message bubble
    const article = document.createElement("article");
    if (isUser) {
      article.className = "max-w-2xl rounded-2xl border border-blue-500/50 bg-blue-600/20 px-4 py-3 text-blue-50";
      const content = document.createElement("div");
      content.className = "whitespace-pre-wrap text-sm";
      content.textContent = message.content || "";
      article.appendChild(content);

      // Render attachments from message.attachments (new format)
      const msgAttachments = message.attachments || [];
      if (msgAttachments.length > 0) {
        const attachmentDiv = document.createElement("div");
        attachmentDiv.className = "flex flex-wrap gap-2 mt-2";
        msgAttachments.forEach(fileId => {
          const img = document.createElement("img");
          img.src = `/a/${state.selectedAgentId}/api/files/${encodeURIComponent(fileId)}`;
          img.className = "max-w-32 max-h-32 rounded-lg border border-slate-500";
          img.alt = fileId;
          // Show placeholder on error
          img.onerror = () => {
            img.style.display = 'none';
          };
          attachmentDiv.appendChild(img);
        });
        article.appendChild(attachmentDiv);
      }
    } else {
      article.className = "max-w-2xl rounded-2xl border border-slate-700 bg-slate-800/80 px-4 py-3 assistant-message";
      const content = document.createElement("div");
      content.className = "md-render prose prose-invert max-w-none text-sm";
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

  setChatStatus(`Loaded session ${normalized}`);
  // Only open sessions panel if explicitly requested
}

async function openServerFiles() {
  const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
  if (!canWriteAgent(agent)) {
    setToolPanel("Server Files", `<div class="text-xs text-red-500">You do not have permission to access this agent's files.</div>`);
    return;
  }
  const workspacePath = '/root/.efp/workspace';
  await loadServerFiles(workspacePath);
}

async function loadServerFiles(path) {
  setToolPanel("Server Files", '<div class="text-xs text-slate-400">Loading files…</div>');

  try {
    const data = await agentApi(`/api/files?path=${encodeURIComponent(path)}`);
    const items = data.items || [];

    // Build breadcrumb with data attributes for event delegation
    const parts = path.split('/').filter(Boolean);
    let breadcrumb = '<a href="#" class="breadcrumb-link" data-path="/">/</a>';
    let currentPath = '';
    for (const part of parts) {
      currentPath += '/' + part;
      const escapedPath = escapeHtml(currentPath);
      breadcrumb += ' <a href="#" class="breadcrumb-link" data-path="' + escapedPath.replace(/"/g, '&quot;') + '">' + escapeHtml(part) + '</a>';
    }

    // Build file rows with checkboxes in separate cell
    const rows = items.map((item) => {
      const icon = item.is_dir ? '📁' : '📄';
      const safePath = item.path.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      return (
        `<div class="file-row group flex items-center gap-3 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 px-3 py-2 hover:border-blue-500 file-item" data-path="${safePath}" data-is-dir="${item.is_dir}">` +
          `<input type="checkbox" class="file-checkbox flex-shrink-0 w-4 h-4 rounded border border-slate-400 bg-white text-blue-600 focus:ring-blue-500 accent-blue-600" data-path="${safePath}" data-is-dir="${item.is_dir}" aria-label="${escapeHtml(item.name)}">` +
          `<div class="flex-1 flex items-center gap-2 cursor-pointer name-cell" data-path="${safePath}" data-is-dir="${item.is_dir}">` +
            `<span class="text-lg">${icon}</span>` +
            `<span class="flex-1 truncate text-sm text-slate-800 dark:text-slate-200">${escapeHtml(item.name)}</span>` +
          `</div>` +
        `</div>`
      );
    }).join("");

    // Set panel content with toolbar
    setToolPanel("Server Files",
      `<div class="space-y-3" id="server-files-panel">` +
        // Toolbar
        `<div class="flex items-center gap-2 border-b border-slate-200 dark:border-slate-700 pb-2">` +
          `<div class="text-xs text-slate-500 dark:text-slate-400 flex-1">${breadcrumb}</div>` +
          `<button class="sf-upload-btn px-2 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded">Upload ZIP</button>` +
          `<button class="sf-download-btn px-2 py-1 text-xs bg-green-600 hover:bg-green-700 text-white rounded" disabled>Download</button>` +
        `</div>` +
        // Select all
        `<div class="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">` +
          `<input type="checkbox" id="sf-select-all" class="w-4 h-4 rounded border border-slate-400 bg-white text-blue-600 focus:ring-blue-500 accent-blue-600"> <label for="sf-select-all">Select all</label>` +
        `</div>` +
        // File list
        `<div class="space-y-1">${rows || "Empty directory"}</div>` +
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
          // Skip if clicking checkbox
          if (e.target.type === 'checkbox') {
            updateDownloadButton(panel);
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
          // Skip if clicking checkbox
          if (e.target.type === 'checkbox') return;
          
          const filePath = cell.dataset.path;
          const isDir = cell.dataset.isDir === 'true';
          
          if (isDir) {
            loadServerFiles(filePath);
          } else {
            previewServerFile(filePath, path);
          }
        });
      });

      // Checkbox change handler
      panel.querySelectorAll('.file-checkbox').forEach(cb => {
        cb.addEventListener('change', () => updateDownloadButton(panel));
      });

      // Upload button handler
      panel.querySelector('.sf-upload-btn')?.addEventListener('click', () => {
        uploadZipToServer(path);
      });

      // Download button handler
      panel.querySelector('.sf-download-btn')?.addEventListener('click', () => {
        const selected = getSelectedFiles(panel);
        if (selected.length > 0) {
          downloadSelectedFiles(selected);
        }
      });

      // Breadcrumb click
      panel.querySelectorAll('.breadcrumb-link').forEach(link => {
        link.addEventListener('click', (e) => {
          e.preventDefault();
          loadServerFiles(link.dataset.path);
        });
      });

      // Initialize button state
      updateDownloadButton(panel);
    }
  } catch (error) {
    setToolPanel("Server Files", `Failed: ${error.message}`);
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

async function uploadZipToServer(targetPath) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setToolPanel("Server Files", `<div class="text-xs text-slate-400">Uploading ${escapeHtml(file.name)}…</div>`);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('path', targetPath);

      const resp = await fetch(`/a/${state.selectedAgentId}/api/files/upload-zip`, {
        method: 'POST',
        body: formData
      });

      if (!resp.ok) {
        const errText = await resp.text();
        setToolPanel("Server Files", `<div class="text-xs text-red-500">Upload failed: ${escapeHtml(errText)}</div>`);
        return;
      }

      const data = await resp.json();
      if (data.success) {
        const safeCount = Number.isFinite(Number(data.count)) ? Number(data.count) : 0;
        setToolPanel("Server Files", `<div class="text-xs text-green-500">Uploaded ${safeCount} files</div>`);
        loadServerFiles(targetPath);
      } else {
        setToolPanel("Server Files", `<div class="text-xs text-red-500">Upload failed: ${escapeHtml(data.error)}</div>`);
      }
    } catch (err) {
      setToolPanel("Server Files", `<div class="text-xs text-red-500">Upload failed: ${escapeHtml(err.message)}</div>`);
    }
  };
  input.click();
}

function downloadSelectedFiles(paths) {
  if (paths.length === 0) return;

  // Use repeated query params to avoid comma ambiguity
  const url = new URL(`${window.location.origin}/a/${state.selectedAgentId}/api/files/download`);
  paths.forEach(p => url.searchParams.append('paths', p));
  window.open(url.toString());
}

async function previewServerFile(filePath, currentDir) {
  try {
    const encodedPath = encodeURIComponent(filePath);
    const dir = currentDir || filePath.substring(0, filePath.lastIndexOf('/'));
    const resp = await agentApi(`/api/files/read?path=${encodedPath}`);

    // Build breadcrumb for navigation
    const parts = dir.split('/').filter(Boolean);
    let breadcrumb = '<a href="#" onclick="loadServerFiles(\'/\'); event.preventDefault();">/</a>';
    let currentPath = '';
    for (const part of parts) {
      currentPath += '/' + part;
      breadcrumb += ' <a href="#" onclick="loadServerFiles(\'' + currentPath + '\'); event.preventDefault();">' + part + '</a> /';
    }
    breadcrumb = breadcrumb.replace(/ \/$/, '');

    if (resp.error) {
      // Check if it's a binary file error - show file info instead
      if (resp.error.includes('binary')) {
        const size = resp.size || 'Unknown';
        const ext = filePath.split('.').pop().toLowerCase();
        const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext);

        if (isImage) {
          // Show image directly
          setToolPanel("File: " + filePath.split('/').pop(),
            `<div class="text-xs text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700 pb-2 mb-2">${breadcrumb}</div>` +
            `<div class="text-center"><img src="/a/${state.selectedAgentId}/api/files/read?path=${encodedPath}" class="max-w-full rounded" /></div>`
          );
        } else {
          setToolPanel("File: " + filePath.split('/').pop(),
            `<div class="text-xs text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700 pb-2 mb-2">${breadcrumb}</div>` +
            `<div class="text-slate-500 dark:text-slate-400">Binary file (${size} bytes)</div>` +
            `<div class="text-xs text-slate-400 mt-2">Type: ${ext.toUpperCase()}</div>`
          );
        }
      } else {
        setToolPanel("File Preview", `<div class="text-rose-500">Error: ${safe(resp.error)}</div>`);
      }
      return;
    }

    const content = resp.content || "(empty file)";
    const language = resp.language || 'text';
    setToolPanel("File: " + filePath.split('/').pop(),
      `<div class="text-xs text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700 pb-2 mb-2">${breadcrumb}</div>` +
      `<pre class="whitespace-pre-wrap text-xs bg-slate-100 dark:bg-slate-900 p-2 rounded overflow-auto max-h-96">${escapeHtml(content)}</pre>`
    );
  } catch (error) {
    setToolPanel("File Preview", `<div class="text-rose-500">Failed: ${safe(error.message)}</div>`);
  }
}

async function openSkillsPanel() {
  if (!state.selectedAgentId) return;


  setToolPanel("Skills", '<div class="text-xs text-slate-400">Loading skills…</div>');

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


  setToolPanel("Usage", '<div class="text-xs text-slate-400">Loading usage…</div>');

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


  setToolPanel("My Uploads", '<div class="text-xs text-slate-400">Loading files…</div>');

  try {
    await htmx.ajax("GET", `/app/agents/${state.selectedAgentId}/files/panel`, {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  } catch (error) {
    setToolPanel("My Uploads", `Failed: ${safe(error.message)}`);
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
  div.className = "rounded-lg border border-slate-200 dark:border-slate-600 p-3 space-y-2";
  div.dataset.instanceItem = group;

  // Build fields HTML matching server-rendered format
  const projectHtml = group === "jira"
    ? `<input type="text" data-field="project" value="" placeholder="Project" class="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" />`
    : `<input type="text" data-field="space" value="" placeholder="Space Key" class="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" />`;

  const usernamePasswordHtml = `<input type="text" data-field="username" value="" placeholder="Email" class="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" /><input type="password" data-field="password" value="" placeholder="Password" class="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" />`;

  div.innerHTML = `
    <div class="flex justify-between items-center">
      <span class="text-xs font-medium text-slate-600 dark:text-slate-300">Instance</span>
      <button type="button" class="text-xs text-red-600 dark:text-red-400 hover:underline" data-action="remove-instance" data-group="${group}">Remove</button>
    </div>
    <div class="grid grid-cols-2 gap-2"><input type="text" data-field="name" value="" placeholder="Name" class="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" /><input type="text" data-field="url" value="" placeholder="URL (e.g. https://yourcompany.atlassian.net)" class="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" /></div>
    <div class="grid grid-cols-2 gap-2">${usernamePasswordHtml}</div>
    <div class="grid grid-cols-2 gap-2"><input type="password" data-field="token" value="" placeholder="API Token" class="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500" />${projectHtml}</div>
  `;
  container.append(div);
  normalizeInstanceInputs(group);

  // Initialize password toggles for newly added inputs
  if (window.initPasswordToggles) {
    window.initPasswordToggles();
  }
}


function initializeSettingsPanel() {
  if (!dom.toolPanelBody?.querySelector("#settings-form")) return;
  normalizeInstanceInputs("jira");
  normalizeInstanceInputs("confluence");
}

async function openSettings() {
  if (!state.selectedAgentId) return;
  const agent = state.mineAgents?.find(a => a.id === state.selectedAgentId);
  if (!canWriteAgent(agent)) {
    setToolPanel("Settings", `<div class="text-xs text-red-500">You do not have permission to modify this agent's settings.</div>`);
    return;
  }


  setToolPanel("Settings", '<div class="text-xs text-slate-400">Loading settings…</div>');

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
    removePendingAssistantPlaceholder();
    clearMessageListToWelcome();
    setChatStatus("Chat cleared");
  } catch (error) {
    setChatStatus(`Clear failed: ${safe(error.message)}`);
  }
}

async function startNewChatForSelectedAgent() {
  updateSelectedAgentSession("");
  state.inflightThinking = null;
  removePendingAssistantPlaceholder();
  clearMessageListToWelcome();
  setChatStatus("New chat started");

  if (!dom.toolPanel?.classList.contains("hidden") && dom.toolPanelTitle?.textContent === "Sessions") {
    await openSessionsPanel();
  }
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
    console.error('Failed to copy config:', e);
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
    msgEl.className = "muted tiny";

    try {
      await api(`/api/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify(updates),
      });
      msgEl.textContent = "Saved!";
      msgEl.className = "text-green-400 tiny";
      setTimeout(() => {
        document.getElementById("edit-modal").classList.add("hidden");
        document.getElementById("edit-modal").setAttribute("aria-hidden", "true");
        refreshAll();
      }, 800);
    } catch (err) {
      msgEl.textContent = err.message || "Error saving";
      msgEl.className = "text-red-400 tiny";
    }
  });

  document.getElementById("close-edit-modal")?.addEventListener("click", () => {
    document.getElementById("edit-modal").classList.add("hidden");
    document.getElementById("edit-modal").setAttribute("aria-hidden", "true");
  });

  dom.detailToggle?.addEventListener("click", () => {
    if (state.detailOpen) {
      setDetailOpen(false);
    } else {
      setDetailOpen(true);
      // Render agent details to tool panel
      const agent = state.mineAgents.find(a => a.id === state.selectedAgentId) || publicAgents.find(a => a.id === state.selectedAgentId);
      if (agent) {
        dom.toolPanelTitle.textContent = "Agent Details";
        dom.toolPanelBody.innerHTML = '<div id="agent-meta" class="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 p-4 text-sm"></div><div id="agent-actions" class="space-y-2 mt-4"></div>';
        dom.agentMeta = document.getElementById("agent-meta");
        dom.agentActions = document.getElementById("agent-actions");
        renderAgentMeta(agent);
        renderAgentActions(agent, agent.status || "stopped");
      }
    }
  });
  dom.detailClose?.addEventListener("click", () => setDetailOpen(false));
  dom.detailBackdrop?.addEventListener("click", () => setDetailOpen(false));
  dom.closeToolPanel?.addEventListener("click", closeToolPanel);
  dom.toolBackdrop?.addEventListener("click", closeToolPanel);

  dom.chatInput?.addEventListener("input", () => {
    maybeShowSuggest();
    // Auto-expand textarea
    dom.chatInput.style.height = 'auto';
    dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 150) + 'px';
  });
  dom.chatInput?.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && !dom.chatSuggest?.classList.contains("hidden")) {
      event.preventDefault();
      moveSuggestionSelection(1);
      return;
    }
    if (event.key === "ArrowUp" && !dom.chatSuggest?.classList.contains("hidden")) {
      event.preventDefault();
      moveSuggestionSelection(-1);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !dom.chatSuggest?.classList.contains("hidden")) {
      event.preventDefault();
      if (pickCurrentSuggestion()) return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (state.isSubmittingChat) return;
      htmx.trigger("#chat-form", "submit");
    }
  });

  dom.uploadInput?.addEventListener("change", (e) => {
    if (e.target.files?.length) {
      addPendingFilesAndUpload(e.target.files);
      e.target.value = ''; // Clear to allow re-uploading same file
    }
  });

  dom.topUploadInline?.addEventListener("click", () => dom.uploadInput.click());
  dom.topSettings?.addEventListener("click", openSettings);

  dom.toolPanelBody?.addEventListener("click", async (event) => {
    const newChatBtn = event.target.closest("#sessions-new-chat-btn");
    if (newChatBtn) {
      event.preventDefault();
      await startNewChatForSelectedAgent();
      return;
    }

    const sessionBtn = event.target.closest("[data-session-id]");
    if (sessionBtn) {
      event.preventDefault();
      await loadSession(sessionBtn.dataset.sessionId || "");
      return;
    }

    const fileBtn = event.target.closest("[data-file-ref]");
    if (fileBtn) {
      event.preventDefault();
      insertFileReference(fileBtn.dataset.fileRef || "");
      setChatStatus(`Inserted ${fileBtn.dataset.fileRef || "file reference"}`);
      return;
    }

    const skillBtn = event.target.closest("[data-skill-command]");
    if (skillBtn) {
      event.preventDefault();
      const command = normalizeSkillCommand(skillBtn.dataset.skillCommand);
      if (!command) return;
      dom.chatInput.setRangeText(`${command} `, dom.chatInput.selectionStart, dom.chatInput.selectionEnd, "end");
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

  dom.themeToggle?.addEventListener("click", toggleTheme);

  dom.usersMenuBtn?.addEventListener("click", async () => {
    setToolPanel("Users", '<div class="text-xs text-slate-400">Loading users…</div>');
    try {
      await htmx.ajax("GET", "/app/users/panel", {
        target: "#tool-panel-body",
        swap: "innerHTML",
      });
    } catch (error) {
      setToolPanel("Users", `Failed: ${safe(error.message)}`);
    }
  });

  dom.addAgentBtn?.addEventListener("click", () => {
    document.getElementById("create-modal")?.classList.remove("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "false");
  });

  document.getElementById("close-create-modal")?.addEventListener("click", () => {
    document.getElementById("create-modal")?.classList.add("hidden");
    document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
  });

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
      msgEl.className = "muted tiny";
      const resp = await fetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!resp.ok) {
        throw new Error(await handleErrorResponse(resp));
      }
      const agent = await resp.json();
      msgEl.textContent = "Agent created!";
      msgEl.className = "text-green-400 tiny";
      form.reset();
      setTimeout(() => {
        document.getElementById("create-modal")?.classList.add("hidden");
        document.getElementById("create-modal")?.setAttribute("aria-hidden", "true");
        refreshAll();
      }, 1000);
    } catch (err) {
      msgEl.textContent = err.message;
      msgEl.className = "text-red-400 tiny";
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
      showToast('Please select an agent first');
      return;
    }
    openMyUploads();
  });

  document.getElementById('quick-new-chat-btn')?.addEventListener('click', () => {
    if (state.selectedAgentId) {
      startNewChatForSelectedAgent();
    }
  });

  // Server Files button in header
  document.getElementById('btn-files')?.addEventListener('click', () => {
    if (!state.selectedAgentId) {
      showToast('Please select an agent first');
      return;
    }
    openServerFiles();
  });

  // Sessions button in header
  document.getElementById('btn-sessions')?.addEventListener('click', () => {
    if (!state.selectedAgentId) {
      showToast('Please select an agent first');
      return;
    }
    openSessionsPanel();
  });

  // Thinking Process button in header
  document.getElementById('btn-thinking')?.addEventListener('click', () => {
    if (!state.selectedAgentId) {
      showToast('Please select an agent first');
      return;
    }
    openThinkingProcessPanel();
  });

  await refreshAll();
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
  section.className = 'mt-4 pt-4 border-t border-slate-200 dark:border-slate-700';
  section.innerHTML = '<div class="flex items-center justify-between mb-3"><div class="text-xs text-slate-500 uppercase tracking-wide">System Prompt</div></div><div id="system-prompt-items" class="space-y-2"></div><div id="system-prompt-loading" class="text-xs text-slate-400 py-2">Loading...</div><div id="system-prompt-error" class="text-xs text-red-500 py-2 hidden"></div>';
  
  container.appendChild(section);
  
  loadSystemPromptConfig(agent.id);
}

function loadSystemPromptConfig(agentId) {
  var loading = document.getElementById('system-prompt-loading');
  var error = document.getElementById('system-prompt-error');
  var items = document.getElementById('system-prompt-items');
  if (!items) return;
  
  loading.classList.remove('hidden');
  error.classList.add('hidden');
  items.innerHTML = '';
  
  api('/a/' + agentId + '/api/agent/system-prompt/config').then(function(config) {
    var sections = ['soul', 'user', 'agents', 'memory', 'daily_notes'];
    var labels = { soul: 'SOUL', user: 'USER', agents: 'AGENTS', memory: 'MEMORY', daily_notes: 'Daily Notes' };
    var hasEdit = { soul: true, user: true, agents: true, memory: true, daily_notes: false };
    
    for (var i = 0; i < sections.length; i++) {
      var name = sections[i];
      var enabled = config[name] && config[name].enabled !== undefined ? config[name].enabled : true;
      var editButton = hasEdit[name] ? '<button data-section="' + name + '" data-action="edit" class="text-blue-500 hover:text-blue-600 p-1.5 rounded-md hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors" title="Edit ' + labels[name] + '"><svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg></button>' : '';
      var item = document.createElement('div');
      item.className = 'flex items-center justify-between py-1';
      item.innerHTML = '<div class="flex items-center gap-2"><input type="checkbox" id="sp-' + name + '-enabled" data-section="' + name + '" ' + (enabled ? 'checked' : '') + ' class="rounded border-slate-300 dark:border-slate-600 text-blue-500 focus:ring-blue-500"><label for="sp-' + name + '-enabled" class="text-xs font-medium text-slate-700 dark:text-slate-300">' + labels[name] + '</label></div>' + editButton;
      items.appendChild(item);
    }
    
    var checkboxes = items.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < checkboxes.length; j++) {
      checkboxes[j].addEventListener('change', function(e) {
        updateSystemPromptEnabled(agentId, e.target.dataset.section, e.target.checked);
      });
    }
    
    var editBtns = items.querySelectorAll('button[data-action="edit"]');
    for (var k = 0; k < editBtns.length; k++) {
      editBtns[k].addEventListener('click', (function(btn) {
        return function() {
          editSystemPromptSection(agentId, btn.dataset.section);
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
    console.log('Updated ' + section + ' to ' + enabled);
  }).catch(function(e) {
    console.error('Failed to update:', e);
  });
}

function editSystemPromptSection(agentId, section) {
  api('/a/' + agentId + '/api/agent/system-prompt/' + section).then(function(data) {
    showSystemPromptEditor(agentId, section, data.content || '', data.enabled);
  }).catch(function(e) {
    console.error('Failed to load:', e);
  });
}

function showSystemPromptEditor(agentId, section, content, enabled) {
  var labels = { soul: 'SOUL', user: 'USER', agents: 'AGENTS', memory: 'MEMORY' };
  
  var modal = document.getElementById('system-prompt-editor-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'system-prompt-editor-modal';
    modal.className = 'modal hidden';
    modal.innerHTML = '<div class="modal-backdrop" id="sp-editor-backdrop"></div><div class="modal-card" style="width: min(600px, 90vw); max-height: 80vh;"><div class="flex items-center justify-between mb-4"><h3 id="sp-editor-title" class="text-lg font-semibold"></h3><button id="sp-editor-close" class="text-slate-400 hover:text-slate-600"><svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button></div><div class="mb-4"><label class="flex items-center gap-2 text-sm"><input type="checkbox" id="sp-editor-enabled" class="rounded border-slate-300"><span>Enabled</span></label></div><textarea id="sp-editor-content" class="w-full h-64 p-3 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-sm font-mono resize-none" placeholder="Enter content..."></textarea><div class="flex justify-end gap-2 mt-4"><button id="sp-editor-cancel" class="px-4 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700">Cancel</button><button id="sp-editor-save" class="px-4 py-2 text-sm rounded-lg bg-blue-500 text-white hover:bg-blue-600">Save</button></div></div></div>';
    document.body.appendChild(modal);
    
    document.getElementById('sp-editor-close').addEventListener('click', closeSystemPromptEditor);
    document.getElementById('sp-editor-backdrop').addEventListener('click', closeSystemPromptEditor);
    document.getElementById('sp-editor-cancel').addEventListener('click', closeSystemPromptEditor);
    document.getElementById('sp-editor-save').addEventListener('click', function() {
      saveSystemPromptSection(agentId, section);
    });
  }
  
  document.getElementById('sp-editor-title').textContent = labels[section] + ' Configuration';
  document.getElementById('sp-editor-enabled').checked = enabled;
  document.getElementById('sp-editor-content').value = content;
  modal.dataset.section = section;
  
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
}

function closeSystemPromptEditor() {
  var modal = document.getElementById('system-prompt-editor-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
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
    console.log('Saved ' + section + ': enabled=' + enabled);
    closeSystemPromptEditor();
    loadSystemPromptConfig(agentId);
  }).catch(function(e) {
    console.error('Failed to save:', e);
    alert('Failed to save: ' + e.message);
  });
}
