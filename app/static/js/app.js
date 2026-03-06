const mineList = document.getElementById("mine-list");
const publicList = document.getElementById("public-list");
const createForm = document.getElementById("create-form");
const createMsg = document.getElementById("create-msg");

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

function robotCard(robot, mine = true) {
  const box = document.createElement("div");
  box.className = "robot-card";
  box.innerHTML = `
    <div class="row-between">
      <strong>${robot.name}</strong>
      <span class="status status-${robot.status}">${robot.status}</span>
    </div>
    <p class="muted tiny">${robot.image}</p>
    <p class="muted tiny">id: ${robot.id}</p>
    <div class="btn-row"></div>
  `;
  const row = box.querySelector(".btn-row");

  if (mine) {
    row.append(btn("Start", () => action(`/api/robots/${robot.id}/start`)));
    row.append(btn("Stop", () => action(`/api/robots/${robot.id}/stop`)));
    row.append(btn(robot.visibility === "public" ? "Unshare" : "Share", () =>
      action(`/api/robots/${robot.id}/${robot.visibility === "public" ? "unshare" : "share"}`)
    ));
    row.append(btn("Delete Runtime", () => action(`/api/robots/${robot.id}/delete-runtime`, "POST", true), "secondary"));
    row.append(btn("Destroy", () => action(`/api/robots/${robot.id}/destroy`, "POST", true), "danger"));
  } else {
    row.append(btn("Open", () => window.open(`/r/${robot.id}`, "_blank"), "secondary"));
  }
  return box;
}

function btn(label, onClick, kind = "") {
  const b = document.createElement("button");
  b.className = kind;
  b.textContent = label;
  b.onclick = onClick;
  return b;
}

async function action(path, method = "POST", confirmAction = false) {
  if (confirmAction && !confirm("确认执行该操作？")) return;
  try {
    await api(path, { method });
    await refreshAll();
  } catch (e) {
    alert(`操作失败: ${e.message}`);
  }
}

async function loadMine() {
  mineList.innerHTML = "";
  const data = await api("/api/robots/mine");
  data.forEach((r) => mineList.append(robotCard(r, true)));
  if (data.length === 0) mineList.innerHTML = '<p class="muted">暂无机器人</p>';
}

async function loadPublic() {
  publicList.innerHTML = "";
  const data = await api("/api/robots/public");
  data.forEach((r) => publicList.append(robotCard(r, false)));
  if (data.length === 0) publicList.innerHTML = '<p class="muted">暂无公开机器人</p>';
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
    createMsg.textContent = "创建成功";
    await refreshAll();
  } catch (e) {
    createMsg.textContent = `创建失败: ${e.message}`;
  }
});

document.getElementById("refresh-my")?.addEventListener("click", loadMine);
document.getElementById("refresh-public")?.addEventListener("click", loadPublic);
document.getElementById("logout-btn")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.href = "/login";
});

refreshAll();
