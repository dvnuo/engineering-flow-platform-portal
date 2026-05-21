import { renderPermissionPanel } from "./permission_panel.js";
import { renderTracePanel } from "./trace_panel.js";
import { renderDiffPanel } from "./diff_panel.js";
import { escapeHtml } from "./renderer_utils.js";

function badgeClass(status) {
  if (status === "online" || status === "idle") return "is-ok";
  if (status === "busy" || status === "retry" || status === "aborting") return "is-busy";
  if (status === "offline") return "is-error";
  return "";
}

function renderMessage(message) {
  const author = message.role === "user" ? "You" : (message.role === "system" ? "System" : "Assistant");
  const body = message.text || (message.role === "assistant" ? "" : " ");
  return `
    <article class="opencode-message is-${escapeHtml(message.role)}" data-canonical-message="1" data-message-id="${escapeHtml(message.id)}">
      <div class="opencode-message-meta">
        <span>${escapeHtml(author)}</span>
        ${message.time ? `<time>${escapeHtml(message.time)}</time>` : ""}
      </div>
      <div class="opencode-message-body">${escapeHtml(body)}</div>
    </article>
  `;
}

function renderMessages(viewState) {
  const canonical = viewState.messages.map(renderMessage).join("");
  const pending = viewState.localSubmit ? `
    <article class="opencode-message is-pending" data-local-submit="1">
      <div class="opencode-message-meta"><span>You</span><span>Sending...</span></div>
      <div class="opencode-message-body">${escapeHtml(viewState.localSubmit.text || "")}</div>
      <div class="opencode-message-pending">Waiting for OpenCode...</div>
    </article>
  ` : "";
  return canonical || pending ? `${canonical}${pending}` : '<div class="opencode-chat-empty">Start a conversation.</div>';
}

function renderErrors(errors = []) {
  if (!errors.length) return "";
  return `
    <div class="opencode-banner-list">
      ${errors.slice(-3).map((error) => {
        const message = error?.message || error?.detail || error?.error || String(error || "OpenCode error");
        return `<div class="opencode-banner is-error">${escapeHtml(message)}</div>`;
      }).join("")}
    </div>
  `;
}

function renderToolbar(viewState, creatingConversation) {
  const stopButton = viewState.canStop
    ? '<button type="button" class="opencode-btn" data-opencode-stop>Stop</button>'
    : "";
  const reconnectButton = viewState.showReconnect
    ? '<button type="button" class="opencode-btn" data-opencode-reconnect>Reconnect</button>'
    : "";
  return `
    <div class="opencode-chat-toolbar">
      <div class="opencode-chat-badges">
        <span class="opencode-badge ${badgeClass(viewState.runtimeBadge.status)}">Runtime: ${escapeHtml(viewState.runtimeBadge.label)}</span>
        <span class="opencode-badge ${badgeClass(viewState.sessionBadge.status)}">Session: ${escapeHtml(viewState.sessionBadge.label)}</span>
      </div>
      <div class="opencode-chat-actions">
        ${reconnectButton}
        ${stopButton}
        <button type="button" class="opencode-btn" data-opencode-new-chat ${creatingConversation ? "disabled" : ""}>New chat</button>
      </div>
    </div>
  `;
}

function renderComposer(viewState, draftText) {
  return `
    <form class="opencode-composer" data-opencode-composer>
      <textarea data-opencode-input rows="1" placeholder="Ask OpenCode...">${escapeHtml(draftText || "")}</textarea>
      <button type="submit" class="opencode-send" ${viewState.canSend ? "" : "disabled"}>Send</button>
    </form>
  `;
}

export function renderOpenCodeChat(rootElement, viewState, handlers = {}, options = {}) {
  if (!rootElement) return;
  rootElement.innerHTML = `
    <div class="opencode-chat-shell">
      ${renderToolbar(viewState, options.creatingConversation === true)}
      ${renderErrors(viewState.errors)}
      <div class="opencode-chat-layout">
        <main class="opencode-chat-main">
          <div class="opencode-message-list" data-opencode-message-list>
            ${renderMessages(viewState)}
          </div>
          ${renderComposer(viewState, options.draftText || "")}
        </main>
        <aside class="opencode-side-panel">
          <div class="opencode-panel-tabs" role="tablist">
            <button type="button" class="is-active" data-opencode-panel="thinking">Trace</button>
            <button type="button" data-opencode-panel="permissions">Permissions</button>
            <button type="button" data-opencode-panel="diff">Diff</button>
          </div>
          <section class="opencode-panel-body" data-opencode-panel-body="thinking">
            ${renderTracePanel(viewState.thinkingItems)}
          </section>
          <section class="opencode-panel-body" data-opencode-panel-body="permissions">
            ${renderPermissionPanel(viewState.permissionRequests)}
          </section>
          <section class="opencode-panel-body" data-opencode-panel-body="diff">
            ${renderDiffPanel(viewState.diff)}
          </section>
        </aside>
      </div>
    </div>
  `;

  const input = rootElement.querySelector("[data-opencode-input]");
  if (input && typeof handlers.onDraftChange === "function") {
    input.addEventListener("input", () => handlers.onDraftChange(input.value));
  }

  rootElement.querySelector("[data-opencode-composer]")?.addEventListener("submit", (event) => {
    event.preventDefault();
    handlers.onSend?.(input?.value || "");
  });
  rootElement.querySelector("[data-opencode-stop]")?.addEventListener("click", () => handlers.onStop?.());
  rootElement.querySelector("[data-opencode-reconnect]")?.addEventListener("click", () => handlers.onReconnect?.());
  rootElement.querySelector("[data-opencode-new-chat]")?.addEventListener("click", () => handlers.onNewChat?.());
  rootElement.querySelectorAll("[data-permission-decision]").forEach((button) => {
    button.addEventListener("click", () => {
      handlers.onPermission?.(button.dataset.permissionId, button.dataset.permissionDecision, false);
    });
  });
}
