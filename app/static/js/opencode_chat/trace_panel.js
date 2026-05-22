import { escapeHtml } from "./renderer_utils.js";

function childSessionId(child) {
  return String(child?.id || child?.session_id || child?.sessionID || child?.uuid || "").trim();
}

function childSessionStatus(child) {
  return String(child?.status || child?.state || child?.type || "").trim();
}

export function renderTracePanel(thinkingItems = [], children = []) {
  const childSessions = Array.isArray(children) ? children.filter((child) => childSessionId(child)) : [];
  if (!thinkingItems.length && !childSessions.length) {
    return '<div class="opencode-chat-empty">No runtime trace yet.</div>';
  }
  const childBlocks = childSessions.map((child) => {
    const sessionId = childSessionId(child);
    const status = childSessionStatus(child);
    return `
      <section class="opencode-trace-item" data-child-session-id="${escapeHtml(sessionId)}">
        <div class="opencode-trace-kind">child</div>
        <div>
          <strong>${escapeHtml(sessionId)}</strong>
          ${status ? `<p>${escapeHtml(status)}</p>` : ""}
        </div>
      </section>
    `;
  }).join("");
  const thinkingBlocks = thinkingItems.map((item) => `
    <section class="opencode-trace-item" data-part-id="${escapeHtml(item.id)}">
      <div class="opencode-trace-kind">${escapeHtml(item.type || "step")}</div>
      <div>
        <strong>${escapeHtml(item.title || item.type || "Step")}</strong>
        <p>${escapeHtml(item.text || "")}</p>
      </div>
    </section>
  `).join("");
  return `${thinkingBlocks}${childBlocks}`;
}
