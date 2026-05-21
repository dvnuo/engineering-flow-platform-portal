import { escapeHtml } from "./renderer_utils.js";

export function renderPermissionPanel(permissionRequests = []) {
  if (!permissionRequests.length) {
    return '<div class="opencode-chat-empty">No permission requests.</div>';
  }
  return permissionRequests.map((request) => {
    const id = request.permission_id || request.id || "";
    const title = request.title || request.action || request.tool || "Permission request";
    const detail = request.description || request.message || request.command || request.path || "";
    return `
      <section class="opencode-permission" data-permission-id="${escapeHtml(id)}">
        <div class="opencode-permission-main">
          <strong>${escapeHtml(title)}</strong>
          ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
        </div>
        <div class="opencode-permission-actions">
          <button type="button" class="opencode-btn" data-permission-decision="deny" data-permission-id="${escapeHtml(id)}">Deny</button>
          <button type="button" class="opencode-btn is-primary" data-permission-decision="allow_once" data-permission-id="${escapeHtml(id)}">Allow once</button>
        </div>
      </section>
    `;
  }).join("");
}
