function chatApp() {
  return { initialized: true };
}

const mineList = document.getElementById("mine-list");
const createForm = document.getElementById("create-form");
const createMsg = document.getElementById("create-msg");
const createModal = document.getElementById("create-modal");
const openCreateModalBtn = document.getElementById("open-create-modal");
const closeCreateModalBtn = document.getElementById("close-create-modal");

const embedTitle = document.getElementById("embed-title");
const selectedStatus = document.getElementById("selected-status");
const centerPlaceholder = document.getElementById("center-placeholder");
const agentMeta = document.getElementById("agent-meta");
const agentActions = document.getElementById("agent-actions");

const detailPanel = document.getElementById("detail-panel");
const detailToggle = document.getElementById("detail-toggle");
const detailCloseBtn = document.getElementById("detail-close");
const detailBackdrop = document.getElementById("detail-backdrop");
const toolPanel = document.getElementById("tool-panel");
const toolPanelTitle = document.getElementById("tool-panel-title");
const toolPanelBody = document.getElementById("tool-panel-body");
const closeToolPanelBtn = document.getElementById("close-tool-panel");

const agentChatApp = document.getElementById("agent-chat-app");
const messageList = document.getElementById("message-list");
const chatInput = document.getElementById("chat-input");
const chatSuggest = document.getElementById("chat-suggest");
const chatAgentId = document.getElementById("chat-agent-id");
const chatSessionId = document.getElementById("chat-session-id");
const chatStatus = document.getElementById("chat-status");

const topNewChatBtn = document.getElementById("top-new-chat");
const topUploadBtn = document.getElementById("top-upload");
const topServerFilesBtn = document.getElementById("top-server-files");
const topMyUploadsBtn = document.getElementById("top-my-uploads");
const topSettingsBtn = document.getElementById("top-settings");
const topClearChatBtn = document.getElementById("top-clear-chat");
const uploadInput = document.getElementById("upload-input");

let currentUser = null;
let mineAgents = [];
let agentStatus = new Map();
let selectedAgentId = null;
let detailsCollapsed = true;
let cachedSkills = [];
let cachedMentionFiles = [];

const md = window.markdownit({
  html: false,
  linkify: true,
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      return `<pre><code class="hljs language-${lang}">${hljs.highlight(str, { language: lang }).value}</code></pre>`;
    }
    return `<pre><code class="hljs">${md.utils.escapeHtml(str)}</code></pre>`;
  },
});

function setChatStatus(text) {
  if (chatStatus) chatStatus.textContent = text;
}

function renderMarkdownIn(scope = document) {
  scope.querySelectorAll(".md-render").forEach((el) => {
    const raw = el.dataset.md || "";
    el.innerHTML = md.render(raw);
  });
  scope.querySelectorAll("pre code").forEach((el) => hljs.highlightElement(el));
  if (window.lucide) window.lucide.createIcons();
}

function scrollToBottom() {
  if (!messageList) return;
  messageList.scrollTop = messageList.scrollHeight;
}

window.chatAfterSwap = function (event) {
  if (!event.detail.successful) {
    setChatStatus("Send failed");
    return;
  }
  renderMarkdownIn(messageList);
  scrollToBottom();
  chatInput.value = "";
  setChatStatus("Ready");
};

function openModal() {
  createModal?.classList.remove("hidden");
}

function closeModal() {
  createModal?.classList.add("hidden");
}

function setDetailsCollapsed(collapsed) {
  detailsCollapsed = collapsed;
  detailPanel.style.transform = collapsed ? "translateX(120%)" : "translateX(0)";
  detailBackdrop?.classList.toggle("hidden", collapsed);
}

function api(path, options = {}) {
  return fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  }).then(async (resp) => {
    if (!resp.ok) throw new Error(await resp.text());
    const ct = resp.headers.get("content-type") || "";
    return ct.includes("application/json") ? resp.json() : resp.text();
  });
}

async function agentApi(path, options = {}) {
  if (!selectedAgentId) throw new Error("No selected agent");
  return api(`/a/${selectedAgentId}${path}`, options);
}

function safe(v) { return String(v || "").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }

function renderAgentList() {
  mineList.innerHTML = "";
  if (!mineAgents.length) {
    mineList.innerHTML = '<div class="text-slate-500 text-sm">No agents</div>';
    return;
  }
  mineAgents.forEach((agent) => {
    const status = agentStatus.get(agent.id)?.status || agent.status;
    const btn = document.createElement("button");
    btn.className = `w-full rounded-xl border px-3 py-2 text-left ${selectedAgentId === agent.id ? "border-blue-500 bg-blue-500/10" : "border-slate-700 bg-slate-800/40"}`;
    btn.innerHTML = `<div class="flex items-center justify-between"><span class="font-medium">${safe(agent.name)}</span><span class="h-2.5 w-2.5 rounded-full ${status === "running" ? "bg-emerald-400" : "bg-slate-500"}"></span></div>`;
    btn.onclick = () => selectAgentById(agent.id);
    mineList.append(btn);
  });
}

function renderMeta(agent) {
  agentMeta.innerHTML = `
    <div class="space-y-2 text-sm">
      <div><span class="text-slate-400">Image</span><div class="font-semibold break-all">${safe(agent.image)}</div></div>
      <div><span class="text-slate-400">Created</span><div class="font-semibold">${safe(new Date(agent.created_at).toLocaleString())}</div></div>
      <div><span class="text-slate-400">Resources</span><div class="font-semibold">CPU ${safe(agent.cpu || "N/A")}, Mem ${safe(agent.memory || "N/A")}, Disk ${safe(agent.disk_size_gi)}Gi</div></div>
      <div><span class="text-slate-400">Description</span><div class="font-semibold">${safe(agent.description || "-")}</div></div>
    </div>
  `;
}

function groupedButton(label, cls, onClick) {
  const b = document.createElement("button");
  b.className = cls;
  b.type = "button";
  b.textContent = label;
  b.onclick = onClick;
  return b;
}

function renderActions(agent, status) {
  agentActions.innerHTML = "";
  const primary = document.createElement("div");
  primary.className = "space-y-2 rounded-xl border border-slate-700 bg-slate-800/40 p-2";
  const secondary = document.createElement("div");
  secondary.className = "space-y-2 rounded-xl border border-slate-700 bg-slate-800/40 p-2";

  const start = groupedButton("Start", "w-full rounded-lg bg-emerald-600/80 px-3 py-2 font-semibold", () => action(`/api/agents/${agent.id}/start`));
  const stop = groupedButton("Stop", "w-full rounded-lg bg-amber-500/90 px-3 py-2 font-semibold", () => action(`/api/agents/${agent.id}/stop`));
  start.disabled = !(status === "stopped" || status === "failed");
  stop.disabled = status !== "running";

  primary.append(start, stop);
  secondary.append(
    groupedButton(agent.visibility === "public" ? "Unshare" : "Share", "w-full rounded-lg bg-slate-700 px-3 py-2", () => action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`)),
    groupedButton("Edit", "w-full rounded-lg bg-slate-700 px-3 py-2", () => openEditDialog(agent)),
    groupedButton("Delete Runtime", "w-full rounded-lg bg-slate-700 px-3 py-2", () => action(`/api/agents/${agent.id}/delete-runtime`, "POST", true)),
    groupedButton("Destroy", "w-full rounded-lg bg-rose-600/90 px-3 py-2 font-semibold", () => action(`/api/agents/${agent.id}/destroy`, "POST", true)),
  );
  agentActions.append(primary, secondary);
}

async function renderDetails(agent) {
  if (!agent) {
    embedTitle.textContent = "Select an agent";
    selectedStatus.textContent = "idle";
    centerPlaceholder.classList.remove("hidden");
    agentChatApp.classList.add("hidden");
    return;
  }
  const status = agentStatus.get(agent.id)?.status || agent.status;
  embedTitle.textContent = agent.name;
  selectedStatus.textContent = status;
  chatAgentId.value = agent.id;

  renderMeta(agent);
  renderActions(agent, status);

  if (status === "running") {
    centerPlaceholder.classList.add("hidden");
    agentChatApp.classList.remove("hidden");
  } else {
    centerPlaceholder.classList.remove("hidden");
    agentChatApp.classList.add("hidden");
  }
}

async function selectAgentById(agentId) {
  selectedAgentId = agentId;
  chatSessionId.value = "";
  messageList.innerHTML = '<article class="max-w-3xl rounded-2xl border border-slate-700 bg-slate-800/80 p-4"><p class="text-xs uppercase tracking-wide text-slate-400 mb-2">Assistant</p><div class="prose prose-invert max-w-none">👋 Welcome! Ask me anything.</div></article>';
  renderAgentList();
  await renderDetails(mineAgents.find((a) => a.id === agentId) || null);
}

async function action(path, method = "POST", needConfirm = false) {
  if (needConfirm && !confirm("Please confirm this action.")) return;
  await api(path, { method });
  await refreshAll();
}

async function openEditDialog(agent) {
  const name = prompt("Agent name", agent.name);
  if (name === null) return;
  await api(`/api/agents/${agent.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
  await refreshAll();
}

async function refreshAll() {
  const [me, mine] = await Promise.all([api("/api/auth/me"), api("/api/agents/mine")]);
  currentUser = me;
  mineAgents = mine;

  const pairs = await Promise.all(mineAgents.map(async (agent) => {
    try { return [agent.id, await api(`/api/agents/${agent.id}/status`)]; }
    catch { return [agent.id, { status: agent.status }]; }
  }));
  agentStatus = new Map(pairs);

  if (!selectedAgentId && mineAgents[0]) selectedAgentId = mineAgents[0].id;
  renderAgentList();
  await renderDetails(mineAgents.find((a) => a.id === selectedAgentId) || null);
}

function showSuggest(items, onPick) {
  if (!items.length) return chatSuggest.classList.add("hidden");
  chatSuggest.innerHTML = items.map((it, i) => `<button type="button" data-i="${i}" class="w-full text-left rounded-lg px-2 py-1 hover:bg-slate-700"><div class="font-medium">${safe(it.title)}</div><div class="text-xs text-slate-400">${safe(it.desc || "")}</div></button>`).join("");
  chatSuggest.classList.remove("hidden");
  chatSuggest.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => onPick(items[Number(b.dataset.i)])));
}

async function maybeShowSuggest() {
  const t = chatInput.value;
  const c = chatInput.selectionStart;
  const b = t.slice(0, c);
  const slash = b.match(/(^|\s)\/(\w*)$/);
  const at = b.match(/(^|\s)@(\w*)$/);

  if (slash) {
    if (!cachedSkills.length) {
      try {
        const d = await agentApi("/api/skills");
        cachedSkills = (d.skills || []).map((s) => ({ title: `/${s}`, desc: "Skill" }));
      } catch { cachedSkills = []; }
    }
    return showSuggest(cachedSkills, (it) => { chatInput.setRangeText(`${it.title} `, c - slash[2].length, c, "end"); chatSuggest.classList.add("hidden"); });
  }

  if (at) {
    if (!cachedMentionFiles.length) {
      try {
        const d = await agentApi("/api/files/list");
        cachedMentionFiles = (d.files || []).map((f) => ({ title: `@file_${f.file_id.slice(0, 8)}`, desc: f.filename, full: `@file_${f.file_id}` }));
      } catch { cachedMentionFiles = []; }
    }
    return showSuggest(cachedMentionFiles, (it) => { chatInput.setRangeText(`${it.full} `, c - at[2].length, c, "end"); chatSuggest.classList.add("hidden"); });
  }
  chatSuggest.classList.add("hidden");
}

async function openServerFiles() {
  try {
    const d = await agentApi("/api/files");
    const rows = (d.items || []).map((it) => `<div class="rounded-lg border border-slate-700 px-2 py-1"><span class="mr-2">${it.type === "dir" ? "📁" : "📄"}</span>${safe(it.name)}</div>`).join("");
    toolPanelTitle.textContent = "Server Files";
    toolPanelBody.innerHTML = `<div class="space-y-2">${rows || "No files"}</div>`;
    toolPanel.classList.remove("hidden");
  } catch (e) {
    toolPanelTitle.textContent = "Server Files";
    toolPanelBody.textContent = `Failed: ${e.message}`;
    toolPanel.classList.remove("hidden");
  }
}

async function openMyUploads() {
  try {
    const d = await agentApi("/api/files/list");
    const rows = (d.files || []).map((f) => `<div class="rounded-lg border border-slate-700 px-2 py-1 flex items-center justify-between gap-2"><span class="truncate">${safe(f.filename)}</span><button class="rounded bg-slate-700 px-2 py-1 text-xs" data-cite="${f.file_id}">@file_${f.file_id.slice(0, 8)}</button></div>`).join("");
    toolPanelTitle.textContent = "My Uploads";
    toolPanelBody.innerHTML = `<div class="space-y-2">${rows || "No uploads"}</div>`;
    toolPanel.classList.remove("hidden");
    toolPanelBody.querySelectorAll("[data-cite]").forEach((btn) => btn.addEventListener("click", () => {
      chatInput.setRangeText(`@file_${btn.dataset.cite} `, chatInput.selectionStart, chatInput.selectionEnd, "end");
      chatInput.focus();
    }));
  } catch (e) {
    toolPanelTitle.textContent = "My Uploads";
    toolPanelBody.textContent = `Failed: ${e.message}`;
    toolPanel.classList.remove("hidden");
  }
}

async function openSettings() {
  const d = await agentApi("/api/config");
  const llm = d.config?.llm || {};
  toolPanelTitle.textContent = "Settings";
  toolPanelBody.innerHTML = `
    <div class="space-y-2">
      <label class="block text-xs text-slate-400">Provider<input id="st-provider" class="mt-1 w-full rounded bg-slate-900 border border-slate-700 px-2 py-1" value="${safe(llm.provider || "")}"></label>
      <label class="block text-xs text-slate-400">Model<input id="st-model" class="mt-1 w-full rounded bg-slate-900 border border-slate-700 px-2 py-1" value="${safe(llm.model || "")}"></label>
      <button id="st-save" class="rounded bg-blue-500 px-3 py-2 font-semibold">Save</button>
    </div>`;
  toolPanel.classList.remove("hidden");
  document.getElementById("st-save")?.addEventListener("click", async () => {
    await agentApi("/api/config/save", {
      method: "POST",
      body: JSON.stringify({ llm: { ...llm, provider: document.getElementById("st-provider").value, model: document.getElementById("st-model").value } }),
      headers: { "Content-Type": "application/json" },
    });
    setChatStatus("Settings saved");
  });
}

async function uploadFile() {
  const file = uploadInput.files?.[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  await fetch(`/a/${selectedAgentId}/api/files/upload`, { method: "POST", body: form });
  setChatStatus(`Uploaded ${file.name}`);
  uploadInput.value = "";
}

openCreateModalBtn?.addEventListener("click", openModal);
closeCreateModalBtn?.addEventListener("click", closeModal);
createModal?.addEventListener("click", (e) => { if (e.target === createModal) closeModal(); });

createForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(createForm);
  const payload = Object.fromEntries(fd.entries());
  payload.disk_size_gi = Number(payload.disk_size_gi || 20);
  try {
    const created = await api("/api/agents", { method: "POST", body: JSON.stringify(payload) });
    createMsg.textContent = "Created ✅";
    createForm.reset();
    await refreshAll();
    await selectAgentById(created.id);
    closeModal();
  } catch (e2) { createMsg.textContent = `Create failed: ${e2.message}`; }
});

detailToggle?.addEventListener("click", () => setDetailsCollapsed(!detailsCollapsed));
detailCloseBtn?.addEventListener("click", () => setDetailsCollapsed(true));
detailBackdrop?.addEventListener("click", () => setDetailsCollapsed(true));
closeToolPanelBtn?.addEventListener("click", () => toolPanel.classList.add("hidden"));

topNewChatBtn?.addEventListener("click", () => {
  chatSessionId.value = "";
  messageList.innerHTML = '<article class="max-w-3xl rounded-2xl border border-slate-700 bg-slate-800/80 p-4"><p class="text-xs uppercase tracking-wide text-slate-400 mb-2">Assistant</p><div class="prose prose-invert max-w-none">👋 New chat started.</div></article>';
  scrollToBottom();
});
topUploadBtn?.addEventListener("click", () => uploadInput.click());
topServerFilesBtn?.addEventListener("click", () => { setDetailsCollapsed(false); openServerFiles(); });
topMyUploadsBtn?.addEventListener("click", () => { setDetailsCollapsed(false); openMyUploads(); });
topSettingsBtn?.addEventListener("click", () => { setDetailsCollapsed(false); openSettings(); });
topClearChatBtn?.addEventListener("click", async () => {
  messageList.innerHTML = "";
  if (chatSessionId.value) {
    await agentApi("/api/clear", { method: "POST", body: JSON.stringify({ session_id: chatSessionId.value }), headers: { "Content-Type": "application/json" } });
  }
  chatSessionId.value = "";
});

uploadInput?.addEventListener("change", uploadFile);
chatInput?.addEventListener("input", maybeShowSuggest);
chatInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    htmx.trigger("#chat-form", "submit");
  }
});

document.getElementById("logout-btn")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.href = "/login";
});

document.addEventListener("htmx:afterSwap", (e) => {
  if (e.target?.id === "message-list") {
    renderMarkdownIn(messageList);
    scrollToBottom();
  }
});

document.addEventListener("DOMContentLoaded", async () => {
  await refreshAll();
  renderMarkdownIn(document);
  if (window.lucide) window.lucide.createIcons();
});
