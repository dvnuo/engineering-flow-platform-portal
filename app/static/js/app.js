const mineList = document.getElementById("mine-list");
const publicList = document.getElementById("public-list");
const createForm = document.getElementById("create-form");
const createMsg = document.getElementById("create-msg");
const createModal = document.getElementById("create-modal");
const openCreateModalBtn = document.getElementById("open-create-modal");
const closeCreateModalBtn = document.getElementById("close-create-modal");
const embedTitle = document.getElementById("embed-title");
const selectedStatus = document.getElementById("selected-status");
const centerPlaceholder = document.getElementById("center-placeholder");
const robotMeta = document.getElementById("robot-meta");
const robotActions = document.getElementById("robot-actions");
const refreshAllBtn = document.getElementById("refresh-all");
const detailPanel = document.getElementById("detail-panel");
const detailToggle = document.getElementById("detail-toggle");
const detailToggleSide = document.getElementById("detail-toggle-side");
const themeToggle = document.getElementById("theme-toggle");
const THEME_STORAGE_KEY = "portal-theme";
const robotChatShell = document.getElementById("robot-chat-shell");
const chatMessages = document.getElementById("chat-messages");
const chatInput = document.getElementById("chat-input");
const chatSendBtn = document.getElementById("chat-send-btn");
const chatStatus = document.getElementById("chat-status");
const newChatBtn = document.getElementById("new-chat-btn");
const refreshRecentsBtn = document.getElementById("refresh-recents-btn");
const recentSessions = document.getElementById("recent-sessions");
const uploadInput = document.getElementById("chat-upload-input");
const openServerFilesBtn = document.getElementById("open-server-files");
const openMyUploadsBtn = document.getElementById("open-my-uploads");
const openChatSettingsBtn = document.getElementById("open-chat-settings");
const openTerminalBtn = document.getElementById("open-terminal");
const refreshToolDataBtn = document.getElementById("refresh-tool-data");
const detailToolTitle = document.getElementById("detail-tool-title");
const detailToolContent = document.getElementById("detail-tool-content");
let currentUser = null;
let mineRobots = [];
let publicRobots = [];
let robotStatus = new Map();
let selectedRobotId = null;
let detailsCollapsed = true;
const chatState = {
  histories: new Map(),
  sessionIds: new Map(),
};
function applyTheme(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", nextTheme);
  if (themeToggle) themeToggle.textContent = "Theme";
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
  if (detailToggle) detailToggle.textContent = "Details";
  if (detailToggleSide) detailToggleSide.textContent = "Toggle";
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
function renderDetailToolContent(title, body) {
  if (detailToolTitle) detailToolTitle.textContent = title;
  if (detailToolContent) {
    detailToolContent.textContent = typeof body === "string" ? body : JSON.stringify(body, null, 2);
  }
}
function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
function toolListFromData(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.files)) return data.files;
  if (Array.isArray(data?.items)) return data.items;
  if (Array.isArray(data?.data)) return data.data;
  return [];
}
function formatBytes(size) {
  const n = Number(size || 0);
  if (!Number.isFinite(n) || n <= 0) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
function renderServerFilesPanel(data) {
  if (!detailToolContent) return;
  const files = toolListFromData(data);
  if (!files.length) {
    detailToolContent.innerHTML = '<div class="muted">No server files.</div>';
    return;
  }
  const rows = files.map((item) => {
    const path = item.path || item.name || item.filepath || "unknown";
    const kind = item.type || item.kind || "file";
    const size = formatBytes(item.size || item.file_size || item.bytes);
    return `<div class="tool-item"><div><div class="tool-item-title">${escapeHtml(path)}</div><div class="tiny muted">${escapeHtml(kind)} · ${escapeHtml(size)}</div></div></div>`;
  }).join("");
  detailToolContent.innerHTML = `<div class="tool-list">${rows}</div>`;
}
function renderUploadsPanel(data) {
  if (!detailToolContent) return;
  const files = toolListFromData(data);
  if (!files.length) {
    detailToolContent.innerHTML = '<div class="muted">No uploaded files.</div>';
    return;
  }
  const rows = files.map((item) => {
    const id = item.file_id || item.id || item.uuid || "-";
    const name = item.filename || item.name || item.path || id;
    const contentType = item.content_type || item.type || "unknown";
    const size = formatBytes(item.size || item.file_size || item.bytes);
    return `<div class="tool-item"><div><div class="tool-item-title">${escapeHtml(name)}</div><div class="tiny muted">${escapeHtml(contentType)} · ${escapeHtml(size)}</div></div><button type="button" class="secondary tiny upload-insert" data-file-id="${escapeHtml(id)}">Insert Ref</button></div>`;
  }).join("");
  detailToolContent.innerHTML = `<div class="tool-list">${rows}</div>`;
  detailToolContent.querySelectorAll('.upload-insert').forEach((btn) => {
    btn.addEventListener('click', () => {
      const fileId = btn.getAttribute('data-file-id');
      if (!fileId || !chatInput) return;
      chatInput.value = `${chatInput.value}${chatInput.value ? "\n" : ""}@file_${fileId}`;
      chatInput.focus();
      chatStatus.textContent = `Inserted @file_${fileId}`;
    });
  });
}
function renderSettingsPanel(settings, robotId) {
  if (!detailToolTitle || !detailToolContent) return;
  detailToolTitle.textContent = "Settings";
  detailToolContent.innerHTML = "";
  const editor = document.createElement("textarea");
  editor.className = "settings-editor";
  editor.value = JSON.stringify(settings, null, 2);
  const actions = document.createElement("div");
  actions.className = "settings-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.textContent = "Save Settings";
  saveBtn.className = "secondary";
  saveBtn.onclick = async () => {
    try {
      const payload = JSON.parse(editor.value || "{}");
      await tryProxyApi(robotId, ["api/config/save", "api/settings/save"], {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      chatStatus.textContent = "Settings saved";
    } catch (err) {
      chatStatus.textContent = `Settings save failed: ${err.message}`;
    }
  };
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.textContent = "Format JSON";
  resetBtn.className = "secondary";
  resetBtn.onclick = () => {
    try {
      editor.value = JSON.stringify(JSON.parse(editor.value || "{}"), null, 2);
    } catch (err) {
      chatStatus.textContent = `Invalid JSON: ${err.message}`;
    }
  };
  actions.append(saveBtn, resetBtn);
  detailToolContent.append(editor, actions);
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
async function proxyApi(robotId, path, options = {}) {
  const cleanPath = path.startsWith("/") ? path.slice(1) : path;
  const resp = await fetch(`/r/${robotId}/${cleanPath}`, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  const ct = resp.headers.get("content-type") || "";
  return ct.includes("application/json") ? resp.json() : resp.text();
}
async function tryProxyApi(robotId, paths, options = {}) {
  let lastErr = null;
  for (const path of paths) {
    try {
      return await proxyApi(robotId, path, options);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("No available proxy endpoint");
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
function isMine(robot) {
  return currentUser && robot.owner_user_id === currentUser.id;
}
function getSelectedRobot() {
  return [...mineRobots, ...publicRobots].find((r) => r.id === selectedRobotId) || null;
}
function buttonDisabledByStatus(label, status) {
  if (label === "Start") return !(status === "stopped" || status === "failed");
  if (label === "Stop") return status !== "running";
  if (label === "Open") return status !== "running";
  return false;
}
function button(label, onClick, kind = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = kind;
  b.textContent = label;
  b.onclick = onClick;
  return b;
}
function getHistory(robotId) {
  return chatState.histories.get(robotId) || [];
}
function setHistory(robotId, history) {
  chatState.histories.set(robotId, history);
}
function appendMessage(role, text) {
  const node = document.createElement("article");
  node.className = `chat-message ${role === "user" ? "from-user" : "from-assistant"}`;
  node.innerHTML = `<p class="tiny muted">${role === "user" ? "You" : "Assistant"}</p><div>${String(text || "").replace(/</g, "&lt;")}</div>`;
  chatMessages?.append(node);
  if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
}
function normalizeSessionList(data) {
  if (Array.isArray(data)) return data;
  return data?.sessions || data?.items || data?.data || [];
}
function normalizeSessionMessages(data) {
  const messages = Array.isArray(data) ? data : (data?.messages || data?.history || data?.items || []);
  return messages
    .map((item) => ({
      role: item.role || (item.type === "human" ? "user" : "assistant"),
      content: item.content || item.text || item.message || "",
    }))
    .filter((item) => item.content);
}
function getSessionId(item) {
  return item?.id || item?.session_id || item?.sessionId || null;
}
function renderHistory(robot) {
  if (!chatMessages || !robot) return;
  const history = getHistory(robot.id);
  chatMessages.innerHTML = "";
  if (!history.length) {
    chatMessages.innerHTML = '<div class="welcome-box">Welcome. How can I help you today?</div>';
    return;
  }
  history.forEach((m) => appendMessage(m.role, m.content));
}
async function openEditDialog(robot) {
  const name = prompt("Robot name", robot.name);
  if (name === null) return;
  const image = prompt("Container image", robot.image);
  if (image === null) return;
  const diskInput = prompt("Disk size (Gi)", String(robot.disk_size_gi || 20));
  if (diskInput === null) return;
  const cpu = prompt("CPU request", robot.cpu || "");
  if (cpu === null) return;
  const memory = prompt("Memory request", robot.memory || "");
  if (memory === null) return;
  const description = prompt("Description", robot.description || "");
  if (description === null) return;
  const diskSize = Number(diskInput);
  if (!Number.isFinite(diskSize) || diskSize < 1) {
    alert("Disk size must be at least 1 Gi.");
    return;
  }
  try {
    await api(`/api/robots/${robot.id}`, {
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
    selectRobotById(robot.id);
  } catch (e) {
    alert(`Update failed: ${e.message}`);
  }
}
async function action(path, method = "POST", confirmAction = false) {
  if (confirmAction && !confirm("Please confirm this action.")) return;
  try {
    await api(path, { method });
    await refreshAll();
    if (selectedRobotId) selectRobotById(selectedRobotId);
  } catch (e) {
    alert(`Operation failed: ${e.message}`);
  }
}
function renderRobotList(container, robots) {
  container.innerHTML = "";
  if (robots.length === 0) {
    container.innerHTML = '<p class="muted tiny">No robots.</p>';
    return;
  }
  robots.forEach((robot) => {
    const status = robotStatus.get(robot.id)?.status || robot.status;
    const item = document.createElement("button");
    item.type = "button";
    item.className = `robot-list-item ${selectedRobotId === robot.id ? "active" : ""}`;
    item.innerHTML = `
      <span class="robot-avatar">${robot.name[0]?.toUpperCase() || "R"}</span>
      <span class="robot-name">${robot.name}</span>
      <span class="status-dot status-${status}"></span>
    `;
    item.onclick = () => selectRobotById(robot.id);
    container.append(item);
  });
}
function renderDetails(robot) {
  if (!robot) {
    robotMeta.textContent = "No robot selected.";
    renderDetailToolContent("Select a tool", "ServerFiles / My Uploads / Settings 将在这里展示。");
    robotActions.innerHTML = "";
    embedTitle.textContent = "Select a robot from left list";
    selectedStatus.className = "status";
    selectedStatus.textContent = "idle";
    robotChatShell?.classList.add("hidden");
    centerPlaceholder.classList.remove("hidden");
    return;
  }
  const statusInfo = robotStatus.get(robot.id) || { status: robot.status };
  const status = statusInfo.status || "stopped";
  embedTitle.textContent = robot.name;
  selectedStatus.className = getStatusClass(status);
  selectedStatus.textContent = status;
  robotMeta.innerHTML = `
    <p><strong>Image:</strong> ${robot.image}</p>
    <p><strong>Created:</strong> ${formatDate(robot.created_at)}</p>
    <p><strong>CPU:</strong> ${robot.cpu || "N/A"}</p>
    <p><strong>Memory:</strong> ${robot.memory || "N/A"}</p>
    <p><strong>Disk:</strong> ${robot.disk_size_gi || "N/A"}Gi</p>
    <p><strong>Description:</strong> ${robot.description || "-"}</p>
    ${statusInfo.last_error ? `<p class="error tiny">Error: ${statusInfo.last_error}</p>` : ""}
  `;
  if (status === "running") {
    centerPlaceholder.classList.add("hidden");
    robotChatShell?.classList.remove("hidden");
    renderHistory(robot);
    refreshRecents();
  } else {
    robotChatShell?.classList.add("hidden");
    centerPlaceholder.classList.remove("hidden");
    centerPlaceholder.innerHTML = `
      <h3>${robot.name} is ${status}</h3>
      <p class="muted">Start this robot to chat here.</p>
    `;
  }
  robotActions.innerHTML = "";
  if (isMine(robot)) {
    const startBtn = button("Start", () => action(`/api/robots/${robot.id}/start`));
    startBtn.disabled = buttonDisabledByStatus("Start", status);
    const stopBtn = button("Stop", () => action(`/api/robots/${robot.id}/stop`));
    stopBtn.disabled = buttonDisabledByStatus("Stop", status);
    const shareBtn = button(robot.visibility === "public" ? "Unshare" : "Share", () =>
      action(`/api/robots/${robot.id}/${robot.visibility === "public" ? "unshare" : "share"}`), "secondary");
    const editBtn = button("Edit", () => openEditDialog(robot), "secondary");
    const deleteRuntimeBtn = button("Delete Runtime", () => action(`/api/robots/${robot.id}/delete-runtime`, "POST", true), "secondary");
    const destroyBtn = button("Destroy", () => action(`/api/robots/${robot.id}/destroy`, "POST", true), "danger");
    robotActions.append(startBtn, stopBtn, shareBtn, editBtn, deleteRuntimeBtn, destroyBtn);
  } else {
    const openBtn = button("Open in Tab", () => window.open(`/r/${robot.id}`, "_blank"), "secondary");
    openBtn.disabled = buttonDisabledByStatus("Open", status);
    robotActions.append(openBtn);
  }
}
function selectRobotById(robotId) {
  selectedRobotId = robotId;
  renderRobotList(mineList, mineRobots);
  renderRobotList(publicList, publicRobots);
  renderDetails(getSelectedRobot());
}
async function loadStatusForRobots(robots) {
  const pairs = await Promise.all(robots.map(async (robot) => {
    try {
      const status = await api(`/api/robots/${robot.id}/status`);
      return [robot.id, status];
    } catch (_) {
      return [robot.id, { status: robot.status, last_error: robot.last_error || null }];
    }
  }));
  pairs.forEach(([id, status]) => robotStatus.set(id, status));
}
async function refreshAll() {
  const [me, mine, pub] = await Promise.all([
    api("/api/auth/me"),
    api("/api/robots/mine"),
    api("/api/robots/public"),
  ]);
  currentUser = me;
  mineRobots = mine;
  publicRobots = pub.filter((r) => !mine.some((m) => m.id === r.id));
  robotStatus = new Map();
  await loadStatusForRobots([...mineRobots, ...publicRobots]);
  if (!selectedRobotId && mineRobots[0]) selectedRobotId = mineRobots[0].id;
  if (selectedRobotId && ![...mineRobots, ...publicRobots].some((r) => r.id === selectedRobotId)) {
    selectedRobotId = mineRobots[0]?.id || publicRobots[0]?.id || null;
  }
  renderRobotList(mineList, mineRobots);
  renderRobotList(publicList, publicRobots);
  renderDetails(getSelectedRobot());
}
async function sendChat() {
  const robot = getSelectedRobot();
  if (!robot || !chatInput || !chatInput.value.trim()) return;
  const text = chatInput.value.trim();
  chatInput.value = "";
  const history = getHistory(robot.id);
  const sessionId = chatState.sessionIds.get(robot.id);
  history.push({ role: "user", content: text });
  setHistory(robot.id, history);
  renderHistory(robot);
  chatStatus.textContent = "Sending...";
  try {
    const payload = { message: text };
    if (sessionId) payload.session_id = sessionId;
    const resp = await tryProxyApi(robot.id, ["api/chat", "api/v1/chat", "api/chat/send"], {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const answer = resp.reply || resp.response || resp.message || JSON.stringify(resp);
    history.push({ role: "assistant", content: answer });
    setHistory(robot.id, history);
    if (resp.session_id) chatState.sessionIds.set(robot.id, resp.session_id);
    renderHistory(robot);
    chatStatus.textContent = "Ready";
  } catch (err) {
    appendMessage("assistant", `请求失败：${err.message}`);
    chatStatus.textContent = "Request failed";
  }
}
async function refreshRecents() {
  const robot = getSelectedRobot();
  if (!robot || !recentSessions) return;
  recentSessions.textContent = "Loading...";
  try {
    const data = await tryProxyApi(robot.id, ["api/sessions", "api/chat/sessions", "api/recent", "api/recent-sessions"]);
    const items = normalizeSessionList(data);
    if (!items.length) {
      recentSessions.textContent = "No recent sessions";
      return;
    }
    recentSessions.innerHTML = "";
    items.slice(0, 8).forEach((item) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "recent-item secondary";
      const sessionId = getSessionId(item);
      row.textContent = item.title || item.name || sessionId || "session";
      row.onclick = async () => {
        if (!sessionId) {
          chatStatus.textContent = "Session selected";
          return;
        }
        chatState.sessionIds.set(robot.id, sessionId);
        chatStatus.textContent = `Loading session ${sessionId}...`;
        try {
          const historyResp = await tryProxyApi(robot.id, [
            `api/sessions/${encodeURIComponent(sessionId)}`,
            `api/chat/sessions/${encodeURIComponent(sessionId)}`,
            `api/recent/${encodeURIComponent(sessionId)}`,
          ]);
          const history = normalizeSessionMessages(historyResp);
          setHistory(robot.id, history);
          renderHistory(robot);
          chatStatus.textContent = `Session: ${sessionId}`;
        } catch (_) {
          setHistory(robot.id, []);
          renderHistory(robot);
          chatStatus.textContent = `Session: ${sessionId}`;
        }
      };
      recentSessions.append(row);
    });
  } catch (_) {
    recentSessions.textContent = "Recents API unavailable";
  }
}
async function handleUploadFiles() {
  const robot = getSelectedRobot();
  if (!robot || !uploadInput?.files?.length) return;
  const form = new FormData();
  [...uploadInput.files].forEach((f) => {
    form.append("files", f);
    form.append("file", f);
  });
  chatStatus.textContent = "Uploading files...";
  try {
    await tryProxyApi(robot.id, ["api/files/upload", "api/upload", "api/uploads"], { method: "POST", body: form });
    chatStatus.textContent = `Uploaded ${uploadInput.files.length} file(s)`;
  } catch (err) {
    chatStatus.textContent = `Upload failed: ${err.message}`;
  } finally {
    uploadInput.value = "";
  }
}
function bindToolButtons() {
  openServerFilesBtn?.addEventListener("click", async () => {
    const robot = getSelectedRobot();
    if (!robot) return;
    renderDetailToolContent("Server Files", "Loading...");
    try {
      const files = await tryProxyApi(robot.id, ["api/files?path=.", "api/server-files", "api/files/server", "api/files"]);
      if (detailToolTitle) detailToolTitle.textContent = "Server Files";
      renderServerFilesPanel(files);
    } catch (err) {
      renderDetailToolContent("Server Files", `接口暂不可用: ${err.message}`);
    }
  });
  openMyUploadsBtn?.addEventListener("click", async () => {
    const robot = getSelectedRobot();
    if (!robot) return;
    renderDetailToolContent("My Uploads", "Loading...");
    try {
      const uploads = await tryProxyApi(robot.id, ["api/files/list", "api/my-uploads", "api/uploads", "api/files/my"]);
      if (detailToolTitle) detailToolTitle.textContent = "My Uploads";
      renderUploadsPanel(uploads);
    } catch (err) {
      renderDetailToolContent("My Uploads", `接口暂不可用: ${err.message}`);
    }
  });
  openChatSettingsBtn?.addEventListener("click", async () => {
    const robot = getSelectedRobot();
    if (!robot) return;
    renderDetailToolContent("Settings", "Loading...");
    try {
      const settings = await tryProxyApi(robot.id, ["api/settings", "api/chat/settings", "api/config"]);
      renderSettingsPanel(settings, robot.id);
    } catch (err) {
      renderDetailToolContent("Settings", `接口暂不可用: ${err.message}`);
    }
  });
  refreshToolDataBtn?.addEventListener("click", async () => {
    if (!detailToolTitle?.textContent || detailToolTitle.textContent === "Select a tool") return;
    if (detailToolTitle.textContent.includes("Server")) {
      openServerFilesBtn?.click();
      return;
    }
    if (detailToolTitle.textContent.includes("Upload")) {
      openMyUploadsBtn?.click();
      return;
    }
    if (detailToolTitle.textContent.includes("Settings")) {
      openChatSettingsBtn?.click();
    }
  });
}
createForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  createMsg.textContent = "";
  const fd = new FormData(createForm);
  const payload = Object.fromEntries(fd.entries());
  payload.disk_size_gi = Number(payload.disk_size_gi || 20);
  try {
    const created = await api("/api/robots", { method: "POST", body: JSON.stringify(payload) });
    createMsg.textContent = "Created";
    createForm.reset();
    await refreshAll();
    selectRobotById(created.id);
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
chatSendBtn?.addEventListener("click", sendChat);
chatInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});
newChatBtn?.addEventListener("click", async () => {
  const robot = getSelectedRobot();
  if (!robot) return;
  try {
    const data = await tryProxyApi(robot.id, ["api/chat/new", "api/new-chat", "api/sessions/new"], { method: "POST" });
    const sid = data?.session_id || data?.id || null;
    if (sid) chatState.sessionIds.set(robot.id, sid);
  } catch (_) {
    // graceful fallback to local-only reset
  }
  setHistory(robot.id, []);
  if (!chatState.sessionIds.get(robot.id)) {
    chatState.sessionIds.delete(robot.id);
  }
  renderHistory(robot);
  chatStatus.textContent = "New chat started";
});
refreshRecentsBtn?.addEventListener("click", refreshRecents);
uploadInput?.addEventListener("change", handleUploadFiles);
openTerminalBtn?.addEventListener("click", () => {
  const robot = getSelectedRobot();
  if (!robot) return;
  window.open(`/r/${robot.id}/terminal`, "_blank");
});
bindToolButtons();
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeCreateModal();
  }
});
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.href = "/login";
});
initTheme();
setDetailsCollapsed(true);
themeToggle?.addEventListener("click", toggleTheme);
refreshAll();
