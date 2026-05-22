const THINKING_PART_TYPES = new Set([
  "reasoning",
  "tool",
  "step",
  "patch",
  "file",
  "agent",
  "retry",
  "compaction",
]);

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (value === undefined || value === null) continue;
    const text = String(value).trim();
    if (text) return text;
  }
  return "";
}

function normalizeHealth(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (["ok", "ready", "healthy", "online", "running", "up"].includes(raw)) return "online";
  if (["offline", "down", "unhealthy", "error", "failed", "missing"].includes(raw)) return "offline";
  return "unknown";
}

function normalizeStatus(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (["idle", "ready", "completed", "complete", "done"].includes(raw)) return "idle";
  if (["busy", "running", "active", "working", "accepted", "submitted", "queued"].includes(raw)) return "busy";
  if (["retry", "retrying", "recovering"].includes(raw)) return "retry";
  if (["aborting", "stopping", "cancelling", "canceling"].includes(raw)) return "aborting";
  return "unknown";
}

function messageInfoFrom(value) {
  const candidate = asObject(value);
  const info = asObject(candidate.info);
  const id = firstNonEmpty(
    info.id,
    info.messageID,
    info.message_id,
    info.messageId,
    candidate.id,
    candidate.messageID,
    candidate.message_id,
    candidate.messageId,
    candidate.info_id,
  );
  if (!id) return null;
  return {
    ...info,
    id,
    role: firstNonEmpty(info.role, candidate.role, "assistant"),
    sessionID: firstNonEmpty(info.sessionID, info.session_id, candidate.session_id, candidate.sessionID),
    time: firstNonEmpty(info.time, info.created_at, candidate.time, candidate.created_at),
  };
}

function normalizePart(part, messageId = "") {
  const candidate = asObject(part);
  const id = firstNonEmpty(candidate.id, candidate.partID, candidate.part_id, candidate.partId);
  if (!id) return null;
  return {
    ...candidate,
    id,
    messageId: firstNonEmpty(
      candidate.messageId,
      candidate.messageID,
      candidate.message_id,
      messageId,
    ),
    type: firstNonEmpty(candidate.type, candidate.kind, "text"),
  };
}

function messageParts(message, messageId) {
  const parts = Array.isArray(message?.parts) ? message.parts : [];
  return parts.map((part) => normalizePart(part, messageId)).filter(Boolean);
}

function partText(part) {
  return firstNonEmpty(part?.text, part?.content, part?.delta, part?.summary, part?.output);
}

function roleLabel(role) {
  const normalized = String(role || "").trim().toLowerCase();
  if (normalized === "user") return "user";
  if (normalized === "system") return "system";
  return "assistant";
}

function orderedMessages(store) {
  return store.messageOrder
    .map((id) => store.messagesById.get(id))
    .filter(Boolean);
}

function partsForMessage(store, message) {
  const direct = Array.isArray(message.parts)
    ? message.parts.map((part) => normalizePart(part, message.info?.id || message.id)).filter(Boolean)
    : [];
  const directIds = new Set(direct.map((part) => part.id));
  const mapped = Array.from(store.partsById.values()).filter((part) => {
    const messageId = firstNonEmpty(part.messageId, part.message_id);
    return messageId && messageId === message.info?.id && !directIds.has(part.id);
  });
  return [...direct, ...mapped];
}

export function applyHealthSnapshot(store, payload) {
  const data = asObject(payload);
  const status = firstNonEmpty(data.status, data.health, data.state, data.runtime_status);
  if (data.ok === true && !status) {
    store.runtimeHealth = "online";
    return store;
  }
  if (data.ok === false && !status) {
    store.runtimeHealth = "offline";
    return store;
  }
  store.runtimeHealth = normalizeHealth(status);
  return store;
}

export function applyStatusSnapshot(store, payload) {
  const data = asObject(payload);
  const status = asObject(data.status);
  const rawType = firstNonEmpty(status.type, data.type, data.session_status, data.state, data.status);
  store.sessionStatus = normalizeStatus(rawType);
  store.opencodeSessionId = firstNonEmpty(
    status.session_id,
    status.sessionID,
    status.opencode_session_id,
    data.session_id,
    data.sessionID,
    data.opencode_session_id,
    store.opencodeSessionId,
  );
  const active = status.active ?? data.active;
  if (active === false || store.sessionStatus === "idle") store.localSubmit = null;
  return store;
}

export function applyMessageSnapshot(store, payload) {
  const data = asObject(payload);
  const messages = Array.isArray(data.messages) ? data.messages : [];
  store.messagesById.clear();
  store.partsById.clear();
  store.messageOrder = [];

  messages.forEach((message, index) => {
    const info = messageInfoFrom(message);
    if (!info) return;
    const parts = messageParts(message, info.id);
    const storedMessage = { ...message, info, parts };
    store.messagesById.set(info.id, storedMessage);
    store.messageOrder.push(info.id);
    parts.forEach((part) => store.partsById.set(part.id, part));
    if (!info.time) storedMessage.info.time = String(index).padStart(8, "0");
  });

  store.messageOrder.sort((leftId, rightId) => {
    const left = store.messagesById.get(leftId)?.info?.time || "";
    const right = store.messagesById.get(rightId)?.info?.time || "";
    return String(left).localeCompare(String(right));
  });

  if (store.localSubmit) {
    const pendingId = store.localSubmit.messageId;
    const seen = messages.some((message) => {
      const info = messageInfoFrom(message);
      return info?.id === pendingId || message?.client_message_id === pendingId;
    });
    if (seen || store.sessionStatus === "idle") store.localSubmit = null;
  }
  store.snapshotNeeded = false;
  return store;
}

export function applyOpenCodeEvent(store, event) {
  return store;
}

export function deriveViewState(store) {
  const messages = orderedMessages(store).map((message) => {
    const role = roleLabel(message.info?.role || message.role);
    const parts = partsForMessage(store, message);
    const text = parts
      .filter((part) => String(part.type || "text").toLowerCase() === "text")
      .map(partText)
      .filter(Boolean)
      .join("");
    return {
      id: message.info.id,
      role,
      time: message.info.time || "",
      text,
      parts,
      canonical: message,
    };
  });

  const thinkingItems = [];
  messages.forEach((message) => {
    message.parts.forEach((part) => {
      const type = String(part.type || "").toLowerCase();
      if (!THINKING_PART_TYPES.has(type)) return;
      const fallback = type === "reasoning" ? "Thinking..." : `${type || "Step"}...`;
      thinkingItems.push({
        id: part.id,
        messageId: message.id,
        type,
        title: firstNonEmpty(part.title, part.name, part.tool, type),
        text: part.hidden === true || part.redacted === true ? fallback : firstNonEmpty(part.summary, part.text, part.content, fallback),
        raw: part,
      });
    });
  });

  const permissionRequests = Array.from(store.permissionsById.values());
  const canSend = store.runtimeHealth === "online" && store.sessionStatus === "idle" && store.localSubmit == null;
  const canStop = false;
  const showReconnect = false;

  return {
    runtimeBadge: { label: store.runtimeHealth, status: store.runtimeHealth },
    sessionBadge: { label: store.sessionStatus, status: store.sessionStatus },
    canSend,
    canStop,
    showReconnect,
    messages,
    thinkingItems,
    permissionRequests,
    errors: [...store.errors],
    localSubmit: store.localSubmit,
    eventConnection: store.eventConnection,
    children: [...store.children],
    todo: [...store.todo],
    diff: store.diff,
  };
}
