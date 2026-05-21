import { OpenCodeChatApi } from "./api_client.js";
import { connectOpenCodeEvents } from "./event_stream.js";
import { applyHealthSnapshot, applyMessageSnapshot, applyStatusSnapshot, deriveViewState } from "./projector.js";
import { renderOpenCodeChat } from "./renderer.js";
import { createOpenCodeChatStore } from "./store.js";

function conversationIdFrom(value) {
  if (!value || typeof value !== "object") return "";
  return String(value.id || value.conversation_id || value.conversationId || "").trim();
}

function listFromPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.conversations)) return payload.conversations;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

function generatedMessageId() {
  if (globalThis.crypto?.randomUUID) return `portal-${globalThis.crypto.randomUUID()}`;
  return `portal-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function errorMessage(error, fallback) {
  return error?.body?.message || error?.body?.detail || error?.message || fallback;
}

function conflictCode(error) {
  return error?.body?.error || error?.body?.code || error?.code || "";
}

function permissionResponseBody(decision, remember) {
  const normalized = String(decision || "").trim().toLowerCase();
  let response = "";
  if (["allow", "allow_once", "once"].includes(normalized)) response = "once";
  if (["allow_always", "always"].includes(normalized)) response = "always";
  if (["deny", "reject"].includes(normalized)) response = "reject";
  if (!response) response = normalized;
  const body = { response };
  if (remember === true) body.remember = true;
  return body;
}

export class OpenCodeChatController {
  constructor({ agentId, rootElement, api } = {}) {
    if (!agentId) throw new Error("agentId is required");
    this.agentId = agentId;
    this.rootElement = rootElement || null;
    this.api = api || new OpenCodeChatApi({ agentId });
    this.store = createOpenCodeChatStore({ agentId });
    this.draftText = "";
    this.events = null;
    this.creatingConversation = false;
    this.destroyed = false;
  }

  async init() {
    this.destroyed = false;
    try {
      await this.refreshHealth();
      await this.ensureConversation();
      await this.refreshStatus();
      await this.refreshMessages();
      if (["busy", "retry"].includes(this.store.sessionStatus)) this.connectEvents();
    } catch (error) {
      this.store.errors.push({ message: errorMessage(error, "Unable to initialize OpenCode chat") });
    }
    this.render();
  }

  async refreshHealth() {
    try {
      applyHealthSnapshot(this.store, await this.api.health());
    } catch (error) {
      this.store.runtimeHealth = "offline";
      this.store.errors.push({ message: errorMessage(error, "OpenCode runtime is offline") });
    }
  }

  async ensureConversation() {
    if (this.store.conversationId) return this.store.conversationId;
    const selected = this.rootElement?.dataset?.conversationId || "";
    if (selected) return this.loadConversation(selected);

    const payload = await this.api.listConversations({ limit: 1 });
    const firstConversationId = conversationIdFrom(listFromPayload(payload)[0]);
    if (firstConversationId) return this.loadConversation(firstConversationId);

    this.creatingConversation = true;
    this.render();
    try {
      const created = await this.api.createConversation({ title: "New chat" });
      const conversationId = conversationIdFrom(created.conversation || created);
      if (!conversationId) throw new Error("Conversation response did not include an id");
      this.store.conversationId = conversationId;
      if (this.rootElement?.dataset) this.rootElement.dataset.conversationId = conversationId;
      return conversationId;
    } finally {
      this.creatingConversation = false;
    }
  }

  async loadConversation(conversationId) {
    if (!conversationId) return "";
    const normalized = String(conversationId);
    if (this.store.conversationId !== normalized) {
      this.events?.close();
      this.events = null;
      this.store.conversationId = normalized;
      this.store.messagesById.clear();
      this.store.partsById.clear();
      this.store.messageOrder = [];
      this.store.permissionsById.clear();
      this.store.localSubmit = null;
    }
    if (this.rootElement?.dataset) this.rootElement.dataset.conversationId = normalized;
    await this.refreshSnapshot();
    return normalized;
  }

  async refreshStatus() {
    if (!this.store.conversationId) return;
    applyStatusSnapshot(this.store, await this.api.getStatus(this.store.conversationId));
  }

  async refreshMessages() {
    if (!this.store.conversationId) return;
    applyMessageSnapshot(this.store, await this.api.getMessages(this.store.conversationId));
  }

  async refreshSnapshot() {
    if (!this.store.conversationId) return;
    try {
      await this.refreshStatus();
      await this.refreshMessages();
      if (["busy", "retry"].includes(this.store.sessionStatus)) this.connectEvents();
      this.store.snapshotNeeded = false;
    } catch (error) {
      this.store.snapshotNeeded = true;
      this.store.errors.push({ message: errorMessage(error, "Unable to refresh OpenCode chat") });
    }
    this.render();
  }

  connectEvents() {
    if (!this.store.conversationId || this.destroyed) return;
    if (this.events) return;
    this.events = connectOpenCodeEvents({
      api: this.api,
      store: this.store,
      conversationId: this.store.conversationId,
      onChange: () => this.render(),
      onSnapshotNeeded: async () => {
        await this.refreshSnapshot();
      },
    });
  }

  async send(text, options = {}) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return;
    this.draftText = trimmed;
    await this.ensureConversation();
    await this.refreshStatus();
    if (["busy", "retry"].includes(this.store.sessionStatus)) {
      this.store.errors.push({ message: "Previous message still running." });
      this.render();
      return;
    }

    const messageId = generatedMessageId();
    this.store.localSubmit = { messageId, text: trimmed, startedAt: Date.now() };
    this.render();
    try {
      const response = await this.api.send(this.store.conversationId, {
        text: trimmed,
        prompt: trimmed,
        message_id: messageId,
        client_message_id: messageId,
        ...options,
      });
      this.draftText = "";
      if (response?.status) applyStatusSnapshot(this.store, response);
      this.connectEvents();
      setTimeout(() => this.refreshStatus().then(() => this.render()).catch(() => {}), 250);
    } catch (error) {
      this.store.localSubmit = null;
      if (error?.status === 409 && conflictCode(error) === "opencode_session_busy") {
        applyStatusSnapshot(this.store, error.body?.status || error.body || { status: { type: "busy", active: true } });
        this.store.errors.push({ message: "Previous message still running." });
      } else {
        this.store.errors.push({ message: errorMessage(error, "Unable to send message") });
      }
    }
    this.render();
  }

  async abort() {
    if (!this.store.conversationId) return;
    this.store.sessionStatus = "aborting";
    this.render();
    try {
      const response = await this.api.abort(this.store.conversationId);
      applyStatusSnapshot(this.store, response?.status ? response : { status: response });
      const status = response?.status || response || {};
      if (status.active === false || status?.status?.active === false) {
        await this.refreshSnapshot();
      } else {
        await this.refreshStatus();
      }
    } catch (error) {
      if (error?.status === 409 && conflictCode(error) === "opencode_abort_still_active") {
        this.store.errors.push({ message: "OpenCode still reports this session is running." });
        await this.refreshStatus();
      } else {
        this.store.errors.push({ message: errorMessage(error, "Unable to stop OpenCode") });
      }
    }
    this.render();
  }

  async respondPermission(permissionId, decision, remember) {
    if (!this.store.conversationId || !permissionId) return;
    try {
      await this.api.respondPermission(this.store.conversationId, permissionId, {
        ...permissionResponseBody(decision, remember),
      });
      this.store.permissionsById.delete(permissionId);
    } catch (error) {
      this.store.errors.push({ message: errorMessage(error, "Unable to respond to permission request") });
    }
    this.render();
  }

  async createNewConversation() {
    this.creatingConversation = true;
    this.render();
    try {
      const created = await this.api.createConversation({ title: "New chat" });
      const conversationId = conversationIdFrom(created.conversation || created);
      if (!conversationId) throw new Error("Conversation response did not include an id");
      await this.loadConversation(conversationId);
      this.draftText = "";
    } catch (error) {
      this.store.errors.push({ message: errorMessage(error, "Unable to create conversation") });
    } finally {
      this.creatingConversation = false;
    }
    this.render();
  }

  render() {
    const viewState = deriveViewState(this.store);
    renderOpenCodeChat(
      this.rootElement,
      viewState,
      {
        onDraftChange: (value) => {
          this.draftText = value;
        },
        onSend: (value) => this.send(value),
        onStop: () => this.abort(),
        onReconnect: () => this.refreshSnapshot(),
        onNewChat: () => this.createNewConversation(),
        onPermission: (permissionId, decision, remember) => this.respondPermission(permissionId, decision, remember),
      },
      { draftText: this.draftText, creatingConversation: this.creatingConversation },
    );
  }

  destroy() {
    this.destroyed = true;
    this.events?.close();
    this.events = null;
    if (this.rootElement) this.rootElement.innerHTML = "";
  }
}
