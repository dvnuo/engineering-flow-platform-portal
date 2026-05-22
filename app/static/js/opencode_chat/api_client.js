function safeJsonStringify(value) {
  if (value === undefined) return undefined;
  return JSON.stringify(value);
}

function normalizeErrorBody(body, fallback) {
  if (body && typeof body === "object" && !Array.isArray(body)) return body;
  if (typeof body === "string" && body.trim()) return { detail: body };
  return { detail: fallback };
}

function buildQuery(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

export class OpenCodeChatApi {
  constructor({ agentId, fetchImpl = globalThis.fetch } = {}) {
    if (!agentId) throw new Error("agentId is required");
    if (typeof fetchImpl !== "function") throw new Error("fetch implementation is required");
    this.agentId = String(agentId);
    this.fetchImpl = fetchImpl;
    this.basePath = `/a/${encodeURIComponent(this.agentId)}/api/opencode`;
  }

  async request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const init = { ...options, headers };
    if (Object.prototype.hasOwnProperty.call(init, "json")) {
      headers["Content-Type"] = headers["Content-Type"] || "application/json";
      init.body = safeJsonStringify(init.json);
      delete init.json;
    }

    const response = await this.fetchImpl(`${this.basePath}${path}`, init);
    const contentType = response.headers?.get?.("content-type") || "";
    const text = await response.text();
    let body = text;
    if (contentType.includes("application/json") || /^[\[{]/.test(text.trim())) {
      try {
        body = text ? JSON.parse(text) : {};
      } catch {
        body = text;
      }
    }

    if (!response.ok) {
      const normalizedBody = normalizeErrorBody(body, response.statusText || "Request failed");
      throw {
        ok: false,
        status: response.status,
        statusText: response.statusText || "",
        code: normalizedBody.error || normalizedBody.code || normalizedBody.type || "",
        message: normalizedBody.message || normalizedBody.detail || response.statusText || "Request failed",
        body: normalizedBody,
      };
    }

    return body === "" ? {} : body;
  }

  health() {
    return this.request("/health", { method: "GET" });
  }

  createConversation({ title, parentConversationId } = {}) {
    return this.request("/conversations", {
      method: "POST",
      json: { title, parent_conversation_id: parentConversationId },
    });
  }

  listConversations({ limit, cursor } = {}) {
    return this.request(`/conversations${buildQuery({ limit, cursor })}`, { method: "GET" });
  }

  getConversation(conversationId) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}`, { method: "GET" });
  }

  getStatus(conversationId) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/status`, { method: "GET" });
  }

  getMessages(conversationId, { limit } = {}) {
    return this.request(
      `/conversations/${encodeURIComponent(conversationId)}/messages${buildQuery({ limit })}`,
      { method: "GET" },
    );
  }

  send(conversationId, body) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/send`, {
      method: "POST",
      json: body || {},
    });
  }

  abort(conversationId) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/abort`, {
      method: "POST",
      json: {},
    });
  }

  connectEvents(conversationId, handlers = {}) {
    const EventSourceImpl = handlers.EventSourceImpl || globalThis.EventSource;
    if (typeof EventSourceImpl !== "function") throw new Error("EventSource is not available");
    const source = new EventSourceImpl(
      `${this.basePath}/conversations/${encodeURIComponent(conversationId)}/events`,
    );
    if (typeof handlers.open === "function") source.addEventListener("open", handlers.open);
    if (typeof handlers.message === "function") source.addEventListener("message", handlers.message);
    if (typeof handlers.error === "function") source.addEventListener("error", handlers.error);
    return source;
  }

  respondPermission(conversationId, permissionId, body) {
    return this.request(
      `/conversations/${encodeURIComponent(conversationId)}/permissions/${encodeURIComponent(permissionId)}`,
      { method: "POST", json: body || {} },
    );
  }

  getChildren(conversationId) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/children`, { method: "GET" });
  }

  getTodo(conversationId) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/todo`, { method: "GET" });
  }

  getDiff(conversationId, messageId) {
    return this.request(
      `/conversations/${encodeURIComponent(conversationId)}/diff${buildQuery({ message_id: messageId })}`,
      { method: "GET" },
    );
  }

  fork(conversationId, { messageId } = {}) {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/fork`, {
      method: "POST",
      json: { message_id: messageId },
    });
  }
}
