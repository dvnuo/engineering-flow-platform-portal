const mineList = document.getElementById("mine-list");
const publicList = document.getElementById("public-list");
const createForm = document.getElementById("create-form");
const createMsg = document.getElementById("create-msg");
const createModal = document.getElementById("create-modal");
const openCreateModalBtn = document.getElementById("open-create-modal");
const closeCreateModalBtn = document.getElementById("close-create-modal");

const embedFrame = document.getElementById("agent-embed-frame");
const embedTitle = document.getElementById("embed-title");
const selectedStatus = document.getElementById("selected-status");
const centerPlaceholder = document.getElementById("center-placeholder");
const agentMeta = document.getElementById("agent-meta");
const agentActions = document.getElementById("agent-actions");
const refreshAllBtn = document.getElementById("refresh-all");

const detailPanel = document.getElementById("detail-panel");
const detailToggle = document.getElementById("detail-toggle");
const detailToggleSide = document.getElementById("detail-toggle-side");

const themeToggle = document.getElementById("theme-toggle");
const THEME_STORAGE_KEY = "portal-theme";

let currentUser = null;
let mineAgents = [];
let publicAgents = [];
let agentStatus = new Map();
let selectedAgentId = null;
let detailsCollapsed = true;

function heroIcon(id) {
  return `<svg class="hi" aria-hidden="true"><use href="#${id}"></use></svg>`;
}

function applyTheme(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", nextTheme);
  if (themeToggle) themeToggle.innerHTML = heroIcon(nextTheme === "dark" ? "hi-moon" : "hi-sun");
}

function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  applyTheme(saved || "dark");
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  const next = current === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_STORAGE_KEY, next);
  applyTheme(next);
}

function setDetailsCollapsed(collapsed) {
  detailsCollapsed = collapsed;
  detailPanel?.classList.toggle("collapsed", collapsed);
  if (detailToggle) detailToggle.innerHTML = heroIcon("hi-bars");
  if (detailToggleSide) detailToggleSide.innerHTML = heroIcon(collapsed ? "hi-chevron-left" : "hi-chevron-right");
}

function toggleDetails() {
  setDetailsCollapsed(!detailsCollapsed);
}

function openCreateModal() {
  createModal?.classList.remove("hidden");
  createModal?.setAttribute("aria-hidden", "false");
}

function closeCreateModal() {
  createModal?.classList.add("hidden");
  createModal?.setAttribute("aria-hidden", "true");
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  const ct = resp.headers.get("content-type") || "";
  return ct.includes("application/json") ? resp.json() : resp.text();
}

function formatDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function getStatusClass(status) {
  return `status status-${status || "stopped"}`;
}

function isMine(agent) {
  return currentUser && agent.owner_user_id === currentUser.id;
}

function getSelectedAgent() {
  return [...mineAgents, ...publicAgents].find((r) => r.id === selectedAgentId) || null;
}

function buttonDisabledByStatus(label, status) {
  if (label === "Start") return !(status === "stopped" || status === "failed");
  if (label === "Stop") return status !== "running";
  if (label === "Open") return status !== "running";
  return false;
}

function button(label, onClick, kind = "", iconId = null) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = kind;
  b.innerHTML = iconId ? `${heroIcon(iconId)}<span>${label}</span>` : label;
  b.onclick = onClick;
  return b;
}

async function openEditDialog(agent) {
  const name = prompt("Agent name", agent.name);
  if (name === null) return;
  const image = prompt("Container image", agent.image);
  if (image === null) return;
  const diskInput = prompt("Disk size (Gi)", String(agent.disk_size_gi || 20));
  if (diskInput === null) return;
  const cpu = prompt("CPU request", agent.cpu || "");
  if (cpu === null) return;
  const memory = prompt("Memory request", agent.memory || "");
  if (memory === null) return;
  const description = prompt("Description", agent.description || "");
  if (description === null) return;

  const diskSize = Number(diskInput);
  if (!Number.isFinite(diskSize) || diskSize < 1) {
    alert("Disk size must be at least 1 Gi.");
    return;
  }

  try {
    await api(`/api/agents/${agent.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: name.trim(),
        image: image.trim(),
        disk_size_gi: Math.trunc(diskSize),
        cpu: cpu.trim() || null,
        memory: memory.trim() || null,
        description: description.trim() || null,
      }),
    });
    await refreshAll();
    selectAgentById(agent.id);
  } catch (e) {
    alert(`Update failed: ${e.message}`);
  }
}

async function action(path, method = "POST", confirmAction = false) {
  if (confirmAction && !confirm("Please confirm this action.")) return;
  try {
    await api(path, { method });
    await refreshAll();
    if (selectedAgentId) selectAgentById(selectedAgentId);
  } catch (e) {
    alert(`Operation failed: ${e.message}`);
  }
}

function renderAgentList(container, agents) {
  container.innerHTML = "";
  if (agents.length === 0) {
    container.innerHTML = '<p class="muted tiny">No agents.</p>';
    return;
  }

  agents.forEach((agent) => {
    const status = agentStatus.get(agent.id)?.status || agent.status;
    const item = document.createElement("button");
    item.type = "button";
    item.className = `agent-list-item ${selectedAgentId === agent.id ? "active" : ""}`;
    item.innerHTML = `
      <span class="agent-avatar">${agent.name[0]?.toUpperCase() || "R"}</span>
      <span class="agent-name">${agent.name}</span>
      <span class="status-dot status-${status}"></span>
    `;
    item.onclick = () => selectAgentById(agent.id);
    container.append(item);
  });
}

function renderDetails(agent) {
  if (!agent) {
    agentMeta.textContent = "No agent selected.";
    agentActions.innerHTML = "";
    embedTitle.textContent = "Select an agent from left list";
    selectedStatus.className = "status";
    selectedStatus.textContent = "idle";
    embedFrame.src = "about:blank";
    embedFrame.classList.add("hidden");
    centerPlaceholder.classList.remove("hidden");
    return;
  }

  const statusInfo = agentStatus.get(agent.id) || { status: agent.status };
  const status = statusInfo.status || "stopped";

  embedTitle.textContent = agent.name;
  selectedStatus.className = getStatusClass(status);
  selectedStatus.textContent = status;

  agentMeta.innerHTML = `
    <p><strong>Image:</strong> ${agent.image}</p>
    <p><strong>Created:</strong> ${formatDate(agent.created_at)}</p>
    <p><strong>CPU:</strong> ${agent.cpu || "N/A"}</p>
    <p><strong>Memory:</strong> ${agent.memory || "N/A"}</p>
    <p><strong>Disk:</strong> ${agent.disk_size_gi || "N/A"}Gi</p>
    <p><strong>Description:</strong> ${agent.description || "-"}</p>
    ${statusInfo.last_error ? `<p class="error tiny">Error: ${statusInfo.last_error}</p>` : ""}
  `;

  if (status === "running") {
    centerPlaceholder.classList.add("hidden");
    embedFrame.classList.remove("hidden");
    if (!embedFrame.src.endsWith(`/a/${agent.id}`)) embedFrame.src = `/a/${agent.id}`;
  } else {
    embedFrame.src = "about:blank";
    embedFrame.classList.add("hidden");
    centerPlaceholder.classList.remove("hidden");
    centerPlaceholder.innerHTML = `
      <h3>${agent.name} is ${status}</h3>
      <p class="muted">Start this agent to open it in the center view.</p>
    `;
  }

  agentActions.innerHTML = "";
  if (isMine(agent)) {
    const startBtn = button("Start", () => action(`/api/agents/${agent.id}/start`), "", "hi-plus");
    startBtn.disabled = buttonDisabledByStatus("Start", status);

    const stopBtn = button("Stop", () => action(`/api/agents/${agent.id}/stop`), "", "hi-x-mark");
    stopBtn.disabled = buttonDisabledByStatus("Stop", status);

    const shareBtn = button(agent.visibility === "public" ? "Unshare" : "Share", () =>
      action(`/api/agents/${agent.id}/${agent.visibility === "public" ? "unshare" : "share"}`), "secondary", "hi-chevron-right");

    const editBtn = button("Edit", () => openEditDialog(agent), "secondary", "hi-bars");
    const deleteRuntimeBtn = button("Delete Runtime", () => action(`/api/agents/${agent.id}/delete-runtime`, "POST", true), "secondary", "hi-arrow-path");
    const destroyBtn = button("Destroy", () => action(`/api/agents/${agent.id}/destroy`, "POST", true), "danger", "hi-logout");

    agentActions.append(startBtn, stopBtn, shareBtn, editBtn, deleteRuntimeBtn, destroyBtn);
  } else {
    const openBtn = button("Open in Tab", () => window.open(`/a/${agent.id}`, "_blank"), "secondary", "hi-chevron-right");
    openBtn.disabled = buttonDisabledByStatus("Open", status);
    agentActions.append(openBtn);
  }
}

function selectAgentById(agentId) {
  selectedAgentId = agentId;
  renderAgentList(mineList, mineAgents);
  renderAgentList(publicList, publicAgents);
  renderDetails(getSelectedAgent());
}

async function loadStatusForAgents(agents) {
  const pairs = await Promise.all(agents.map(async (agent) => {
    try {
      const status = await api(`/api/agents/${agent.id}/status`);
      return [agent.id, status];
    } catch (_) {
      return [agent.id, { status: agent.status, last_error: agent.last_error || null }];
    }
  }));
  pairs.forEach(([id, status]) => agentStatus.set(id, status));
}

async function refreshAll() {
  const [me, mine, pub] = await Promise.all([
    api("/api/auth/me"),
    api("/api/agents/mine"),
    api("/api/agents/public"),
  ]);

  currentUser = me;
  mineAgents = mine;
  publicAgents = pub.filter((r) => !mine.some((m) => m.id === r.id));
  agentStatus = new Map();
  await loadStatusForAgents([...mineAgents, ...publicAgents]);

  if (!selectedAgentId && mineAgents[0]) selectedAgentId = mineAgents[0].id;
  if (selectedAgentId && ![...mineAgents, ...publicAgents].some((r) => r.id === selectedAgentId)) {
    selectedAgentId = mineAgents[0]?.id || publicAgents[0]?.id || null;
  }

  renderAgentList(mineList, mineAgents);
  renderAgentList(publicList, publicAgents);
  renderDetails(getSelectedAgent());
}

createForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  createMsg.textContent = "";
  const fd = new FormData(createForm);
  const payload = Object.fromEntries(fd.entries());
  payload.disk_size_gi = Number(payload.disk_size_gi || 20);

  try {
    const created = await api("/api/agents", { method: "POST", body: JSON.stringify(payload) });
    createMsg.textContent = "Created ✅";
    createForm.reset();
    await refreshAll();
    selectAgentById(created.id);
    closeCreateModal();
  } catch (e) {
    createMsg.textContent = `Create failed: ${e.message}`;
  }
});

refreshAllBtn?.addEventListener("click", refreshAll);
detailToggle?.addEventListener("click", toggleDetails);
detailToggleSide?.addEventListener("click", toggleDetails);
openCreateModalBtn?.addEventListener("click", openCreateModal);
closeCreateModalBtn?.addEventListener("click", closeCreateModal);
createModal?.addEventListener("click", (e) => {
  if (e.target === createModal) closeCreateModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeCreateModal();
});

initTheme();
setDetailsCollapsed(true);
themeToggle?.addEventListener("click", toggleTheme);
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.href = "/login";
});

refreshAll();
