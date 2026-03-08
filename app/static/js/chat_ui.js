/**
 * Portal-native chat UI
 * Runtime is source-of-truth for chat/session/file APIs under /a/{agent_id}/api/...
 * Portal only handles browser UI state + HTMX partial rendering.
 */

function chatApp() {
  // Alpine.js: lightweight page-level state marker only.
  return { initialized: true };
}

// ===== DOM refs =====
const dom = {
  mineList: document.getElementById("mine-list"),
  embedTitle: document.getElementById("embed-title"),
  selectedStatus: document.getElementById("selected-status"),
  centerPlaceholder: document.getElementById("center-placeholder"),
  agentChatApp: document.getElementById("agent-chat-app"),
  messageList: document.getElementById("message-list"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  chatSuggest: document.getElementById("chat-suggest"),
  chatAgentId: document.getElementById("chat-agent-id"),
  chatSessionId: document.getElementById("chat-session-id"),
  chatStatus: document.getElementById("chat-status"),
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
  topNewChat: document.getElementById("top-new-chat"),
  topUpload: document.getElementById("top-upload"),
  topServerFiles: document.getElementById("top-server-files"),
  topMyUploads: document.getElementById("top-my-uploads"),
  topSettings: document.getElementById("top-settings"),
  topClearChat: document.getElementById("top-clear-chat"),
  logoutBtn: document.getElementById("logout-btn"),
};

// ===== app state =====
const state = {
  selectedAgentId: null,
  mineAgents: [],
  agentStatus: new Map(),
  detailOpen: false,
  cachedSkills: [],
  cachedMentionFiles: [],
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

function setChatStatus(text) {
  if (dom.chatStatus) dom.chatStatus.textContent = text;
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
  return '<article class="max-w-3xl rounded-2xl border border-slate-700 bg-slate-800/80 p-4"><p class="text-xs uppercase tracking-wide text-slate-400 mb-2">Assistant</p><div class="prose prose-invert max-w-none">👋 Welcome! Ask me anything.</div></article>';
}

// ===== selected agent state sync =====
function renderAgentList() {
  if (!dom.mineList) return;

  dom.mineList.innerHTML = "";
  if (!state.mineAgents.length) {
    dom.mineList.innerHTML = '<div class="text-slate-500 text-sm">No agents</div>';
    return;
  }

  state.mineAgents.forEach((agent) => {
    const status = state.agentStatus.get(agent.id)?.status || agent.status;
    const activeClass = state.selectedAgentId === agent.id
      ? "border-blue-500 bg-blue-500/10"
      : "border-slate-700 bg-slate-800/40";

    const button = document.createElement("button");
    button.className = `w-full rounded-xl border px-3 py-2 text-left ${activeClass}`;
    button.innerHTML = `<div class="flex items-center justify-between"><span class="font-medium">${safe(agent.name)}</span><span class="h-2.5 w-2.5 rounded-full ${status === "running" ? "bg-emerald-400" : "bg-slate-500"}"></span></div>`;
    button.addEventListener("click", () => selectAgentById(agent.id));
    dom.mineList.append(button);
  });
}

function renderAgentMeta(agent) {
  if (!dom.agentMeta) return;

  dom.agentMeta.innerHTML = `
    <div class="space-y-2 text-sm">
      <div><span class="text-slate-400">Image</span><div class="font-semibold break-all">${safe(agent.image)}</div></div>
      <div><span class="text-slate-400">Created</span><div class="font-semibold">${safe(new Date(agent.created_at).toLocaleString())}</div></div>
      <div><span class="text-slate-400">Resources</span><div class="font-semibold">CPU ${safe(agent.cpu || "N/A")}, Mem ${safe(agent.memory || "N/A")}, Disk ${safe(agent.disk_size_gi)}Gi</div></div>
      <div><span class="text-slate-400">Description</span><div class="font-semibold">${safe(agent.description || "-")}</div></div>
    </div>
  `;
}

function renderAgentActions(agent, status) {
  if (!dom.agentActions) return;

  dom.agentActions.innerHTML = "";

  const buildButton = (label, classes, onClick) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = classes;
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  };

  const primary = document.createElement("div");
  primary.className = "space-y-2 rounded-xl border border-slate-700 bg-slate-800/40 p-2";
  const secondary = document.createElement("div");
  secondary.className = "space-y-2 rounded-xl border border-slate-700 bg-slate-800/40 p-2";

  const startButton = buildButton("Start", "w-full rounded-lg bg-emerald-600/80 px-3 py-2 font-semibold", () => action(`/api/agents/${agent.id}/start`));
  const stopButton = buildButton("Stop", "w-full rounded-lg bg-amber-500/90 px-3 py-2 font-semibold", () => action(`/api/agents/${agent.id}/stop`));
  startButton.disabled = !(status === "stopped" || status === "failed");
  stopButton.disabled = status !== "running";

  primary.append(startButton, stopButton);
  secondary.append(
    buildButton(agent.visibility === "public" ? "Unshare" : "Share", "w-full rounded-lg bg-slate-700 px-3 py-2", () => action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`)),
    buildButton("Edit", "w-full rounded-lg bg-slate-700 px-3 py-2", () => openEditDialog(agent)),
    buildButton("Delete Runtime", "w-full rounded-lg bg-slate-700 px-3 py-2", () => action(`/api/agents/${agent.id}/delete-runtime`, "POST", true)),
    buildButton("Destroy", "w-full rounded-lg bg-rose-600/90 px-3 py-2 font-semibold", () => action(`/api/agents/${agent.id}/destroy`, "POST", true)),
  );

  dom.agentActions.append(primary, secondary);
}

async function selectAgentById(agentId) {
  state.selectedAgentId = agentId;
  state.cachedSkills = [];
  state.cachedMentionFiles = [];

  if (dom.chatSessionId) dom.chatSessionId.value = "";
  if (dom.chatAgentId) dom.chatAgentId.value = agentId || "";
  if (dom.messageList) dom.messageList.innerHTML = defaultWelcomeMessage();

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
  if (dom.chatAgentId) dom.chatAgentId.value = agent.id;

  renderAgentMeta(agent);
  renderAgentActions(agent, status);

  const running = status === "running";
  dom.centerPlaceholder.classList.toggle("hidden", running);
  dom.agentChatApp.classList.toggle("hidden", !running);
}

async function refreshAll() {
  const mine = await api("/api/agents/mine");
  state.mineAgents = mine;

  const pairs = await Promise.all(state.mineAgents.map(async (agent) => {
    try {
      return [agent.id, await api(`/api/agents/${agent.id}/status`)];
    } catch {
      return [agent.id, { status: agent.status }];
    }
  }));

  state.agentStatus = new Map(pairs);
  if (!state.selectedAgentId && state.mineAgents.length) state.selectedAgentId = state.mineAgents[0].id;

  renderAgentList();
  await syncSelectedAgentState();
}

// ===== chat submit lifecycle (HTMX) =====
function handleChatAfterSwap(target) {
  if (target?.id !== "message-list") return;

  // HTMX appends server-rendered partial; OOB hidden session_id input updates in-place.
  renderMarkdown(dom.messageList);
  renderIcons();
  scrollToBottom();

  if (dom.chatInput) dom.chatInput.value = "";
  setChatStatus("Ready");
}

function handleChatResponseError(event) {
  if (event.target?.id !== "chat-form") return;
  setChatStatus("Send failed");
}

function handleChatBeforeRequest(event) {
  if (event.target?.id !== "chat-form") return;
  setChatStatus("Thinking...");
}

// ===== suggestion popup hooks =====
function showSuggest(items, onPick) {
  if (!dom.chatSuggest) return;

  if (!items.length) {
    dom.chatSuggest.classList.add("hidden");
    dom.chatSuggest.innerHTML = "";
    return;
  }

  dom.chatSuggest.innerHTML = items.map((item, index) => (
    `<button type="button" data-i="${index}" class="w-full text-left rounded-lg px-2 py-1 hover:bg-slate-700"><div class="font-medium">${safe(item.title)}</div><div class="text-xs text-slate-400">${safe(item.desc || "")}</div></button>`
  )).join("");
  dom.chatSuggest.classList.remove("hidden");

  dom.chatSuggest.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => onPick(items[Number(button.dataset.i)]));
  });
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
        state.cachedSkills = (data.skills || []).map((item) => ({ title: `/${item}`, desc: "Skill" }));
      } catch {
        state.cachedSkills = [];
      }
    }

    showSuggest(state.cachedSkills, (item) => {
      dom.chatInput.setRangeText(`${item.title} `, cursor - slash[2].length, cursor, "end");
      dom.chatSuggest.classList.add("hidden");
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
      dom.chatSuggest.classList.add("hidden");
    });
    return;
  }

  dom.chatSuggest?.classList.add("hidden");
}

// ===== toolbar actions =====
function setToolPanel(title, contentHtml) {
  if (!dom.toolPanel) return;
  dom.toolPanelTitle.textContent = title;
  dom.toolPanelBody.innerHTML = contentHtml;
  dom.toolPanel.classList.remove("hidden");
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

async function openMyUploads() {
  try {
    const data = await agentApi("/api/files/list");
    const rows = (data.files || []).map((file) => (
      `<div class="rounded-lg border border-slate-700 px-2 py-1 flex items-center justify-between gap-2"><span class="truncate">${safe(file.filename)}</span><button class="rounded bg-slate-700 px-2 py-1 text-xs" data-cite="${file.file_id}">@file_${file.file_id.slice(0, 8)}</button></div>`
    )).join("");

    setToolPanel("My Uploads", `<div class="space-y-2">${rows || "No uploads"}</div>`);
    dom.toolPanelBody.querySelectorAll("[data-cite]").forEach((button) => {
      button.addEventListener("click", () => {
        dom.chatInput.setRangeText(`@file_${button.dataset.cite} `, dom.chatInput.selectionStart, dom.chatInput.selectionEnd, "end");
        dom.chatInput.focus();
      });
    });
  } catch (error) {
    setToolPanel("My Uploads", `Failed: ${safe(error.message)}`);
  }
}

async function openSettings() {
  try {
    const data = await agentApi("/api/config");
    setToolPanel("Settings", `<pre class="whitespace-pre-wrap">${safe(JSON.stringify(data.config || {}, null, 2))}</pre>`);
  } catch (error) {
    setToolPanel("Settings", `Failed: ${safe(error.message)}`);
  }
}

async function uploadFile() {
  const file = dom.uploadInput?.files?.[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);
  await fetch(`/a/${state.selectedAgentId}/api/files/upload`, { method: "POST", body: formData });

  setChatStatus(`Uploaded ${file.name}`);
  dom.uploadInput.value = "";
  state.cachedMentionFiles = [];
}

async function clearChat() {
  if (dom.messageList) dom.messageList.innerHTML = "";

  if (dom.chatSessionId?.value) {
    await agentApi("/api/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: dom.chatSessionId.value }),
    });
  }

  if (dom.chatSessionId) dom.chatSessionId.value = "";
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
  dom.closeToolPanel?.addEventListener("click", () => dom.toolPanel.classList.add("hidden"));

  dom.chatInput?.addEventListener("input", maybeShowSuggest);
  dom.chatInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      htmx.trigger("#chat-form", "submit");
    }
  });

  dom.uploadInput?.addEventListener("change", uploadFile);

  dom.topNewChat?.addEventListener("click", () => {
    if (dom.chatSessionId) dom.chatSessionId.value = "";
    if (dom.messageList) dom.messageList.innerHTML = defaultWelcomeMessage();
    scrollToBottom();
  });
  dom.topUpload?.addEventListener("click", () => dom.uploadInput.click());
  dom.topServerFiles?.addEventListener("click", () => { setDetailOpen(true); openServerFiles(); });
  dom.topMyUploads?.addEventListener("click", () => { setDetailOpen(true); openMyUploads(); });
  dom.topSettings?.addEventListener("click", () => { setDetailOpen(true); openSettings(); });
  dom.topClearChat?.addEventListener("click", clearChat);

  dom.logoutBtn?.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });

  document.addEventListener("htmx:beforeRequest", handleChatBeforeRequest);
  document.addEventListener("htmx:afterSwap", (event) => {
    handleChatAfterSwap(event.target);
    renderIcons();
  });
  document.addEventListener("htmx:responseError", handleChatResponseError);
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await refreshAll();
  renderMarkdown(document);
  renderIcons();
});
