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
  closeToolPanel: document.getElementById("close-tool-panel"),
  agentMeta: document.getElementById("agent-meta"),
  agentActions: document.getElementById("agent-actions"),
  topSettings: document.getElementById("top-settings"),
  composerPlusBtn: document.getElementById("composer-plus-btn"),
  composerMenu: document.getElementById("composer-menu"),
  uploadInput: document.getElementById("upload-input"),
  topUploadInline: document.getElementById("top-upload-inline"),
  logoutBtn: document.getElementById("logout-btn"),
  themeToggle: document.getElementById("theme-toggle"),
  addAgentBtn: document.getElementById("add-agent-btn"),
};

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
  currentUserRole: String(dom.appRoot?.dataset.role || "user"),
  eventWs: null,
  eventWsAgentId: null,
  inflightThinking: null,
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

// ===== generic helpers =====
function safe(value) {
  return String(value || "").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

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

function buildUserMessageArticle(text) {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `<div class="flex flex-col items-end"><div class="flex items-center gap-2 mb-1"><span class="text-xs font-semibold text-blue-400">You</span><span class="text-xs text-slate-500">${now}</span></div><article class="max-w-2xl rounded-2xl border border-blue-500/50 bg-blue-600/20 px-4 py-3 text-blue-50" data-local-user="1"><div class="whitespace-pre-wrap text-sm">${safe(text)}</div></article></div>`;
}

function buildPendingAssistantArticle() {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `<div class="flex flex-col items-start"><div class="flex items-center gap-2 mb-1"><span class="text-xs font-semibold text-emerald-400">Assistant</span><span class="text-xs text-slate-500">${now}</span></div><article class="max-w-2xl rounded-2xl border border-slate-600 bg-slate-800 px-4 py-3 assistant-message text-slate-100" data-pending-assistant="1"><div class="text-slate-300">Thinking...</div></article></div>`;
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
  return ["iteration_start", "llm_thinking", "tool_call", "tool_result", "complete"].includes(type);
}

function getThinkingEventDisplay(event) {
  const type = event?.type || "event";
  const data = event?.data || {};
  const byType = {
    iteration_start: { icon: "rotate-cw", title: "Iteration Start", detail: `Iteration ${data.iteration || 1}${data.total ? `/${data.total}` : ""}` },
    llm_thinking: { icon: "brain", title: "LLM Thinking", detail: data.message || "Model is reasoning" },
    tool_call: { icon: "wrench", title: "Tool Call", detail: data.tool ? `Calling ${data.tool}` : "Calling tool" },
    tool_result: { icon: data.success === false ? "x-circle" : "check-circle-2", title: "Tool Result", detail: data.success === false ? (data.error || "Tool failed") : (data.tool ? `${data.tool} completed` : "Tool completed") },
    complete: { icon: "flag", title: "Complete", detail: "Execution complete" },
  };
  return byType[type] || { icon: "circle", title: type.replaceAll("_", " "), detail: "" };
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
    const border = idx === events.length - 1 ? "" : (isDark ? " border-l border-slate-600" : " border-l border-slate-200");
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
    el.innerHTML = md.render(el.dataset.md || "");
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
  state.detailOpen = open;
  if (dom.detailPanel) dom.detailPanel.style.transform = open ? "translateX(0)" : "translateX(120%)";
  dom.detailBackdrop?.classList.toggle("hidden", !open);
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
  return '<article data-welcome="1" class="max-w-2xl rounded-2xl border border-slate-700 bg-slate-800/80 p-4"><p class="text-xs uppercase tracking-wide text-slate-400 mb-2">Assistant</p><div class="prose prose-invert max-w-none">👋 Welcome! Ask me anything.</div></article>';
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
  if (dom.chatSessionId) dom.chatSessionId.value = currentSessionIdForSelectedAgent();
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

// ===== selected agent state sync =====
function renderAgentList() {
  if (!dom.mineList) return;

  dom.mineList.innerHTML = "";
  if (!state.mineAgents.length) {
    dom.mineList.innerHTML = '<div class="text-slate-500 text-sm">No agents</div>';
    return;
  }

  const mine = state.mineAgents.filter((agent) => Number(agent.owner_user_id) === state.currentUserId);
  const shared = state.mineAgents.filter((agent) => Number(agent.owner_user_id) !== state.currentUserId);

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

  dom.agentMeta.innerHTML = `
    <div class="space-y-3 text-sm">
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Image</div>
        <div class="font-mono text-xs bg-slate-100 rounded px-2 py-1.5 break-all text-slate-700">${safe(agent.image)}</div>
      </div>
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Created</div>
        <div class="text-slate-700">${dateStr}</div>
      </div>
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Resources</div>
        <div class="flex flex-wrap gap-1.5">
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-blue-50 text-blue-700 text-xs font-medium">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
            ${cpu}
          </span>
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-purple-50 text-purple-700 text-xs font-medium">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
            ${mem}
          </span>
          <span class="inline-flex items-center px-2 py-1 rounded-md bg-green-50 text-green-700 text-xs font-medium">
            <svg class="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path></svg>
            ${disk}Gi
          </span>
        </div>
      </div>
      <div>
        <div class="text-xs text-slate-500 uppercase tracking-wide mb-1">Description</div>
        <div class="text-slate-700">${safe(agent.description || '-')}</div>
      </div>
    </div>
  `;
}

function renderAgentActions(agent, status) {
  if (!dom.agentActions) return;

  dom.agentActions.innerHTML = "";
  const writable = canWriteAgent(agent);

  const container = document.createElement("div");
  container.className = "space-y-2 rounded-xl border border-slate-700 bg-slate-800/40 p-2";

  const grid = document.createElement("div");
  grid.className = "grid grid-cols-3 gap-2";

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
    { label: agent.visibility === "public" ? "Unshare" : "Share", icon: agent.visibility === "public" ? "lock" : "share-2", classes: "border-indigo-500 bg-indigo-500 hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`) },
    { label: "Edit", icon: "pencil", classes: "border-slate-500 bg-slate-500 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => openEditDialog(agent) },
    { label: "Delete", icon: "trash-2", classes: "border-slate-500 bg-slate-500 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed", disabled: !writable, onClick: () => action(`/api/agents/${agent.id}/delete-runtime`, "POST", true) },
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
    if (status === "running") dom.selectedStatus.classList.add("border-emerald-200", "bg-emerald-50", "text-emerald-700");
    else if (status === "failed") dom.selectedStatus.classList.add("border-rose-200", "bg-rose-50", "text-rose-700");
    else dom.selectedStatus.classList.add("border-slate-200", "bg-slate-100", "text-slate-600");
  }

  if (dom.chatAgentId) dom.chatAgentId.value = agent.id;
  syncHiddenSessionInputFromState();

  renderAgentMeta(agent);
  renderAgentActions(agent, status);

  const running = status === "running";
  dom.centerPlaceholder.classList.toggle("hidden", running);
  dom.agentChatApp.classList.toggle("hidden", !running);
  
  // Auto-load last session if available
  if (running) {
    const lastSessionId = getLastSessionId(agent.id);
    if (lastSessionId) {
      try {
        await loadSession(lastSessionId);
      } catch (e) {
        // Session not found, start fresh
        console.log("Last session not found, starting fresh");
      }
    }
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
  if (state.selectedAgentId) localStorage.setItem(LAST_AGENT_STORAGE_KEY, state.selectedAgentId);

  renderAgentList();
  await syncSelectedAgentState();
}

// ===== chat submit lifecycle (HTMX) =====
function handleChatBeforeRequest(event) {
  if (event.target?.id !== "chat-form") return;
  if (state.isSubmittingChat) {
    event.preventDefault();
    return;
  }

  setChatSubmitting(true);
  state.pendingMessage = dom.chatInput?.value || "";
  removeWelcomeMessageIfPresent();
  removePendingAssistantPlaceholder();
  hideSuggest();
  if (dom.messageList && state.pendingMessage.trim()) {
    dom.messageList.insertAdjacentHTML("beforeend", buildUserMessageArticle(state.pendingMessage));
    const thinkingId = `thinking-${Date.now()}`;
    dom.messageList.insertAdjacentHTML("beforeend", buildPendingAssistantArticle());
    const pending = dom.messageList.querySelector('article[data-pending-assistant="1"]:last-of-type') || dom.messageList.lastElementChild;
    if (pending) pending.dataset.thinkingId = thinkingId;
    state.inflightThinking = { id: thinkingId, events: [], completed: false };
    if (pending) renderThinkingProcess(pending, state.inflightThinking.events);
    ensureEventSocketForSelectedAgent();
    scrollToBottom();
  }
  if (dom.chatInput) dom.chatInput.value = "";
  setChatStatus("Sending...");
}

function handleChatResponseError(event) {
  if (event.target?.id !== "chat-form") return;

  removePendingAssistantPlaceholder();
  setChatSubmitting(false);
  if (dom.chatInput && state.pendingMessage && !dom.chatInput.value.trim()) dom.chatInput.value = state.pendingMessage;
  state.pendingMessage = "";
  state.inflightThinking = null;
  
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
}

function handleChatAfterSwap(target) {
  if (target?.id !== "message-list") return;

  const pendingEvents = state.inflightThinking?.events ? [...state.inflightThinking.events] : [];
  removePendingAssistantPlaceholder();
  if (pendingEvents.length) attachThinkingToLatestAssistant(pendingEvents);
  state.inflightThinking = null;

  // OOB swap from chat partial updates hidden #chat-session-id. Keep per-agent session state in sync.
  const sessionFromInput = dom.chatSessionId?.value || "";
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

// ===== markdown + icons lifecycle =====
function initializeRenderLifecycle() {
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

function insertFileReference(fileRef) {
  // Runtime expects @file_<short_or_full_id>; using short id mirrors runtime webchat behavior.
  if (!dom.chatInput || !fileRef) return;

  const reference = `${fileRef} `;
  dom.chatInput.setRangeText(reference, dom.chatInput.selectionStart, dom.chatInput.selectionEnd, "end");
  dom.chatInput.focus();
}

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
      dom.chatInput.setRangeText(`${command} `, cursor - slash[2].length, cursor, "end");
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
      dom.chatInput.setRangeText(`${item.full} `, cursor - at[2].length, cursor, "end");
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
  dom.toolPanelBody.innerHTML = contentHtml;
  // Hide detail panel, show tool panel
  if (dom.detailPanel) dom.detailPanel.style.transform = "translateX(120%)";
  dom.detailBackdrop?.classList.add("hidden");
  dom.toolPanel.style.transform = "translateX(0)";
}

function closeToolPanel() {
  if (dom.toolPanel) dom.toolPanel.style.transform = "translateX(120%)";
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
    roleLabel.textContent = isUser ? "You" : "Assistant";
    
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
  renderChatHistory(data.messages || [], data.metadata || {});

  setChatStatus(`Loaded session ${normalized}`);
  // Only open sessions panel if explicitly requested
}

async function openServerFiles() {
  try {
    const data = await agentApi("/api/files");
    const rows = (data.items || []).map((item) => (
      `<div class="rounded-lg border border-slate-700 px-2 py-1"><span class="mr-2">${item.type === "dir" ? "📁" : "📄"}</span>${safe(item.name)}</div>`
    )).join("");
    setToolPanel("Server Files", `<div class="space-y-2">${rows || "No files"}</div>`);
  } catch (error) {
    setToolPanel("Server Files", `Failed: ${safe(error.message)}`);
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

  const fields = group === "jira"
    ? ["name", "url", "username", "password", "token", "project"]
    : ["name", "url", "username", "password", "token", "space"];

  const div = document.createElement("div");
  div.className = "rounded border border-slate-700 p-2 space-y-1";
  div.dataset.instanceItem = group;
  div.innerHTML = `
    <div class="flex justify-between items-center"><span class="text-xs text-slate-400">Instance</span><button type="button" class="text-xs text-rose-300" data-action="remove-instance" data-group="${group}">Remove</button></div>
    ${fields.map((f) => `<input type="${f === "password" || f === "token" ? "password" : "text"}" data-field="${f}" placeholder="${f[0].toUpperCase()}${f.slice(1)}" class="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs" />`).join("")}
  `;
  container.append(div);
  normalizeInstanceInputs(group);
}

function initializeSettingsPanel() {
  if (!dom.toolPanelBody?.querySelector("#settings-form")) return;
  normalizeInstanceInputs("jira");
  normalizeInstanceInputs("confluence");
}

async function openSettings() {
  if (!state.selectedAgentId) return;

  
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

async function uploadFile() {
  const file = dom.uploadInput?.files?.[0];
  if (!file) return;

  try {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`/a/${state.selectedAgentId}/api/files/upload`, { method: "POST", body: formData });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }

    setChatStatus(`Uploaded ${file.name}`);
    state.cachedMentionFiles = [];
    if (!dom.toolPanel?.classList.contains("hidden") && dom.toolPanelTitle?.textContent === "My Uploads") {
      await openMyUploads();
    }
  } catch (error) {
    setChatStatus(`Upload failed: ${safe(error.message)}`);
  } finally {
    dom.uploadInput.value = "";
  }
}

async function clearChat() {
  try {
    if (dom.chatSessionId?.value) {
      await agentApi("/api/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: dom.chatSessionId.value }),
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
  const name = prompt("Agent name", agent.name);
  if (name === null) return;

  await api(`/api/agents/${agent.id}`, {
    method: "PATCH",
    body: JSON.stringify({ name: name.trim() }),
  });
  await refreshAll();
}

// ===== wiring =====
function bindEvents() {
  dom.detailToggle?.addEventListener("click", () => setDetailOpen(!state.detailOpen));
  dom.detailClose?.addEventListener("click", () => setDetailOpen(false));
  dom.detailBackdrop?.addEventListener("click", () => setDetailOpen(false));
  dom.closeToolPanel?.addEventListener("click", closeToolPanel);

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

  dom.uploadInput?.addEventListener("change", uploadFile);

  dom.topUploadInline?.addEventListener("click", () => dom.uploadInput.click());
  dom.topServerFiles?.addEventListener("click", () => { setDetailOpen(true); openServerFiles(); });
  dom.topMyUploads?.addEventListener("click", () => { setDetailOpen(true); openMyUploads(); });
  dom.topSessions?.addEventListener("click", openSessionsPanel);
  dom.topSettings?.addEventListener("click", () => { setDetailOpen(true); openSettings(); });

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

  // Composer + menu
  dom.composerPlusBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    dom.composerMenu?.classList.toggle("hidden");
  });

  // Close menu when clicking outside
  document.addEventListener("click", (e) => {
    if (!dom.composerMenu?.contains(e.target) && e.target !== dom.composerPlusBtn) {
      dom.composerMenu?.classList.add("hidden");
    }
  });

  // Composer menu items
  document.querySelectorAll(".composer-menu-item").forEach((item, idx) => {
    item.addEventListener("click", () => {
      dom.composerMenu?.classList.add("hidden");
      switch (idx) {
        case 0: // New Chat
          startNewChatForSelectedAgent();
          break;
        case 1: // Upload File
          dom.uploadInput?.click();
          break;
        case 2: // My Uploads
          setDetailOpen(true);
          openMyUploads();
          break;
        case 3: // Server Files
          setDetailOpen(true);
          openServerFiles();
          break;
        case 4: // Sessions
          openSessionsPanel();
          break;
      }
    });
  });

  dom.themeToggle?.addEventListener("click", toggleTheme);

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
    const data = {
      name: formData.get("name"),
      image: formData.get("image"),
      disk_size_gi: Number(formData.get("disk_size_gi")),
      cpu: formData.get("cpu") || null,
      memory: formData.get("memory") || null,
    };
    const msgEl = document.getElementById("create-msg");
    try {
      msgEl.textContent = "Creating...";
      msgEl.className = "muted tiny";
      const resp = await fetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Failed to create agent");
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

  bindEvents();
  initializeRenderLifecycle();
  await refreshAll();
  renderMarkdown(document);
  renderIcons();
});

window.addEventListener("beforeunload", disconnectEventSocket);
