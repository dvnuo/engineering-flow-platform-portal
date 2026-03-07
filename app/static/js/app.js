const mineList = document.getElementById("mine-list");
const publicList = document.getElementById("public-list");
const createForm = document.getElementById("create-form");
const createMsg = document.getElementById("create-msg");
const createModal = document.getElementById("create-modal");
const openCreateModalBtn = document.getElementById("open-create-modal");
const closeCreateModalBtn = document.getElementById("close-create-modal");

const embedFrame = document.getElementById("robot-embed-frame");
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

let currentUser = null;
let mineRobots = [];
let publicRobots = [];
let robotStatus = new Map();
let selectedRobotId = null;
let detailsCollapsed = true;

function applyTheme(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", nextTheme);
  if (themeToggle) themeToggle.textContent = nextTheme === "dark" ? "☾" : "☀";
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
  if (detailToggle) detailToggle.textContent = collapsed ? "☰" : "⮞";
  if (detailToggleSide) detailToggleSide.textContent = collapsed ? "◀" : "▶";
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
    robotActions.innerHTML = "";
    embedTitle.textContent = "Select a robot from left list";
    selectedStatus.className = "status";
    selectedStatus.textContent = "idle";
    embedFrame.src = "about:blank";
    embedFrame.classList.add("hidden");
    centerPlaceholder.classList.remove("hidden");
    return;
  }

  const statusInfo = robotStatus.get(robot.id) || { status: robot.status };
  const status = statusInfo.status || "stopped";

  embedTitle.textContent = robot.name;
  selectedStatus.className = getStatusClass(status);
  selectedStatus.textContent = status;

  robotMeta.innerHTML = `
    <p><strong>🧱 Image:</strong> ${robot.image}</p>
    <p><strong>🕓 Created:</strong> ${formatDate(robot.created_at)}</p>
    <p><strong>⚙ CPU:</strong> ${robot.cpu || "N/A"}</p>
    <p><strong>🧠 Memory:</strong> ${robot.memory || "N/A"}</p>
    <p><strong>💾 Disk:</strong> ${robot.disk_size_gi || "N/A"}Gi</p>
    <p><strong>📄 Description:</strong> ${robot.description || "-"}</p>
    ${statusInfo.last_error ? `<p class="error tiny">Error: ${statusInfo.last_error}</p>` : ""}
  `;

  if (status === "running") {
    centerPlaceholder.classList.add("hidden");
    embedFrame.classList.remove("hidden");
    if (!embedFrame.src.endsWith(`/r/${robot.id}`)) embedFrame.src = `/r/${robot.id}`;
  } else {
    embedFrame.src = "about:blank";
    embedFrame.classList.add("hidden");
    centerPlaceholder.classList.remove("hidden");
    centerPlaceholder.innerHTML = `
      <h3>${robot.name} is ${status}</h3>
      <p class="muted">Start this robot to open it in the center view.</p>
    `;
  }

  robotActions.innerHTML = "";
  if (isMine(robot)) {
    const startBtn = button("▶ Start", () => action(`/api/robots/${robot.id}/start`));
    startBtn.disabled = buttonDisabledByStatus("Start", status);

    const stopBtn = button("⏹ Stop", () => action(`/api/robots/${robot.id}/stop`));
    stopBtn.disabled = buttonDisabledByStatus("Stop", status);

    const shareBtn = button(robot.visibility === "public" ? "🔒 Unshare" : "🌍 Share", () =>
      action(`/api/robots/${robot.id}/${robot.visibility === "public" ? "unshare" : "share"}`), "secondary");

    const editBtn = button("✏ Edit", () => openEditDialog(robot), "secondary");
    const deleteRuntimeBtn = button("🗑 Runtime", () => action(`/api/robots/${robot.id}/delete-runtime`, "POST", true), "secondary");
    const destroyBtn = button("💥 Destroy", () => action(`/api/robots/${robot.id}/destroy`, "POST", true), "danger");

    robotActions.append(startBtn, stopBtn, shareBtn, editBtn, deleteRuntimeBtn, destroyBtn);
  } else {
    const openBtn = button("🔗 Open in Tab", () => window.open(`/r/${robot.id}`, "_blank"), "secondary");
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

createForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  createMsg.textContent = "";
  const fd = new FormData(createForm);
  const payload = Object.fromEntries(fd.entries());
  payload.disk_size_gi = Number(payload.disk_size_gi || 20);

  try {
    const created = await api("/api/robots", { method: "POST", body: JSON.stringify(payload) });
    createMsg.textContent = "Created ✅";
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
