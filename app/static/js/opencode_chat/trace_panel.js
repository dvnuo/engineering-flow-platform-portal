import { escapeHtml } from "./renderer_utils.js";

export function renderTracePanel(thinkingItems = []) {
  if (!thinkingItems.length) {
    return '<div class="opencode-chat-empty">No runtime trace yet.</div>';
  }
  return thinkingItems.map((item) => `
    <section class="opencode-trace-item" data-part-id="${escapeHtml(item.id)}">
      <div class="opencode-trace-kind">${escapeHtml(item.type || "step")}</div>
      <div>
        <strong>${escapeHtml(item.title || item.type || "Step")}</strong>
        <p>${escapeHtml(item.text || "")}</p>
      </div>
    </section>
  `).join("");
}
