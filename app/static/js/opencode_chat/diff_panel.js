import { escapeHtml } from "./renderer_utils.js";

export function renderDiffPanel(diff) {
  if (!diff) return '<div class="opencode-chat-empty">No diff selected.</div>';
  const text = typeof diff === "string" ? diff : (diff.patch || diff.diff || JSON.stringify(diff, null, 2));
  return `<pre class="opencode-diff">${escapeHtml(text)}</pre>`;
}
