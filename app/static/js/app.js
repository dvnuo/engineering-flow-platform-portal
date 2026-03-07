const mineList = document.getElementById("mine-list");
const publicList = document.getElementById("public-list");
const createForm = document.getElementById("create-form");
const createMsg = document.getElementById("create-msg");
const embedPanel = document.getElementById("robot-embed-panel");
const embedFrame = document.getElementById("robot-embed-frame");
const embedTitle = document.getElementById("embed-title");
const embedClose = document.getElementById("embed-close");

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

function buttonDisabledByStatus(label, status) {
  if (label === "Start") return !(status === "stopped" || status === "failed");
  if (label === "Stop") return status !== "running";
  if (label === "Open") return status !== "running";
  return false;
}

async function robotCard(robot, mine = true) {
  let statusInfo = { status: robot.status, cpu_usage: "N/A", memory_usage: "N/A", last_error: robot.last_error || null };
  try {
    statusInfo = await api(`/api/robots/${robot.id}/status`);
  } catch (_) {
    // keep fallback values
  }

  const box = document.createElement("div");
  box.className = "robot-card";
  box.innerHTML = `
    <div class="row-between">
      <strong>${robot.name}</strong>
      <span class="status status-${statusInfo.status}">${statusInfo.status}</span>
    </div>
    <p class="muted tiny">Image: ${robot.image}</p>
    <p class="muted tiny">Created: ${formatDate(robot.created_at)}</p>
    <p class="muted tiny">CPU request: ${robot.cpu || "N/A"} | Mem request: ${robot.memory || "N/A"}</p>
    <p class="muted tiny">CPU usage: ${statusInfo.cpu_usage || "N/A"} | Mem usage: ${statusInfo.memory_usage || "N/A"}</p>
    ${statusInfo.last_error ? `<p class="error tiny">Error: ${statusInfo.last_error}</p>` : ""}
    <div class="btn-row"></div>
  `;
  const row = box.querySelector(".btn-row");

  if (mine) {
    row.append(actionBtn("Start", `/api/robots/${robot.id}/start`, statusInfo.status));
    row.append(actionBtn("Stop", `/api/robots/${robot.id}/stop`, statusInfo.status));
    row.append(actionBtn("Open", () => openEmbeddedRobot(robot), statusInfo.status, "secondary"));
    row.append(btn(robot.visibility === "public" ? "Unshare" : "Share", () =>
      action(`/api/robots/${robot.id}/${robot.visibility === "public" ? "unshare" : "share"}`)
    ));
    row.append(btn("Delete Runtime", () => action(`/api/robots/${robot.id}/delete-runtime`, "POST", true), "secondary"));
    row.append(btn("Destroy", () => action(`/api/robots/${robot.id}/destroy`, "POST", true), "danger"));
  } else {
    row.append(actionBtn("Open", () => openEmbeddedRobot(robot), statusInfo.status, "secondary"));
  }
  return box;
}


function openEmbeddedRobot(robot) {
  if (!embedPanel || !embedFrame) {
    window.open(`/r/${robot.id}`, "_blank");
    return;
  }
  embedTitle.textContent = `Robot Preview: ${robot.name}`;
  embedFrame.src = `/r/${robot.id}`;
  embedPanel.classList.remove("hidden");
  embedPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeEmbeddedRobot() {
  if (!embedPanel || !embedFrame) return;
  embedFrame.src = "about:blank";
  embedPanel.classList.add("hidden");
}

function actionBtn(label, pathOrHandler, status, kind = "") {
  const onClick = typeof pathOrHandler === "string" ? () => action(pathOrHandler) : pathOrHandler;
  const b = btn(label, onClick, kind);
  const disabled = buttonDisabledByStatus(label, status);
  b.disabled = disabled;
  if (disabled) {
    b.classList.add("disabled");
    b.title = label === "Start"
      ? "Start is available only when robot is stopped/failed."
      : label === "Stop"
        ? "Stop is available only when robot is running."
        : "Open is available only when robot is running.";
  }
  return b;
}

function btn(label, onClick, kind = "") {
  const b = document.createElement("button");
  b.className = kind;
  b.textContent = label;
  b.onclick = onClick;
  return b;
}

async function action(path, method = "POST", confirmAction = false) {
  if (confirmAction && !confirm("Please confirm this action.")) return;
  try {
    await api(path, { method });
    await refreshAll();
  } catch (e) {
    alert(`Operation failed: ${e.message}`);
  }
}

async function loadMine() {
  mineList.innerHTML = "";
  const data = await api("/api/robots/mine");
  const cards = await Promise.all(data.map((r) => robotCard(r, true)));
  cards.forEach((c) => mineList.append(c));
  if (data.length === 0) mineList.innerHTML = '<p class="muted">No robots yet.</p>';
}

async function loadPublic() {
  publicList.innerHTML = "";
  const data = await api("/api/robots/public");
  const cards = await Promise.all(data.map((r) => robotCard(r, false)));
  cards.forEach((c) => publicList.append(c));
  if (data.length === 0) publicList.innerHTML = '<p class="muted">No public robots yet.</p>';
}

async function refreshAll() {
  await Promise.all([loadMine(), loadPublic()]);
}

createForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  createMsg.textContent = "";
  const fd = new FormData(createForm);
  const payload = Object.fromEntries(fd.entries());
  payload.disk_size_gi = Number(payload.disk_size_gi || 20);

  try {
    await api("/api/robots", { method: "POST", body: JSON.stringify(payload) });
    createForm.reset();
    createMsg.textContent = "Robot created.";
    await refreshAll();
  } catch (e) {
    createMsg.textContent = `Create failed: ${e.message}`;
  }
});

document.getElementById("refresh-my")?.addEventListener("click", loadMine);
document.getElementById("refresh-public")?.addEventListener("click", loadPublic);
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.href = "/login";
});

refreshAll();

embedClose?.addEventListener("click", closeEmbeddedRobot);
