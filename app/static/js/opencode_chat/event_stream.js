import { applyOpenCodeEvent } from "./projector.js";

const EVENT_NAMES = [
  "opencode.connected",
  "opencode.session.status",
  "opencode.message.updated",
  "opencode.message.part.updated",
  "opencode.message.part.delta",
  "opencode.permission.requested",
  "opencode.snapshot.required",
  "opencode.error",
];

function mergeEnvelopeData(parsed) {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  const nested = parsed.data && typeof parsed.data === "object" && !Array.isArray(parsed.data)
    ? parsed.data
    : {};
  return {
    ...nested,
    ...parsed,
    raw_data: nested,
  };
}

function parseEventPayload(eventName, rawData) {
  if (rawData && typeof rawData === "object") {
    const type = eventName === "message" ? (rawData.type || rawData.event || eventName) : eventName;
    return { type, data: mergeEnvelopeData(rawData) };
  }
  try {
    const parsed = JSON.parse(String(rawData || "{}"));
    if (parsed && typeof parsed === "object") {
      const type = eventName === "message" ? (parsed.type || parsed.event || eventName) : eventName;
      return { type, data: mergeEnvelopeData(parsed) };
    }
  } catch {
    return { type: eventName, data: { message: String(rawData || "") } };
  }
  return { type: eventName, data: {} };
}

export function connectOpenCodeEvents({
  api,
  store,
  conversationId,
  onChange = () => {},
  onSnapshotNeeded = () => {},
  backoffBaseMs = 500,
  maxReconnects = 5,
} = {}) {
  if (!api) throw new Error("api is required");
  if (!store) throw new Error("store is required");
  if (!conversationId) throw new Error("conversationId is required");

  let source = null;
  let reconnectTimer = null;
  let reconnectAttempt = 0;
  let closed = false;

  const clearReconnectTimer = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const closeSource = () => {
    if (source && typeof source.close === "function") source.close();
    source = null;
  };

  const notifySnapshotNeeded = async (reason) => {
    store.snapshotNeeded = true;
    try {
      await onSnapshotNeeded({ reason });
    } catch (error) {
      store.errors.push({ message: error?.message || String(error), reason });
    }
  };

  const attachEvent = (eventName, listener) => {
    if (!source || typeof source.addEventListener !== "function") return;
    source.addEventListener(eventName, listener);
  };

  const handlePayload = (eventName, rawData) => {
    const payload = parseEventPayload(eventName, rawData);
    applyOpenCodeEvent(store, payload);
    onChange();
  };

  const scheduleReconnect = () => {
    if (closed || reconnectAttempt >= maxReconnects) return;
    const delay = Math.min(8000, backoffBaseMs * (2 ** reconnectAttempt));
    reconnectAttempt += 1;
    clearReconnectTimer();
    reconnectTimer = setTimeout(async () => {
      if (closed) return;
      await notifySnapshotNeeded("reconnect");
      open();
    }, delay);
  };

  function open() {
    clearReconnectTimer();
    closeSource();
    store.eventConnection = "connecting";
    onChange();

    source = api.connectEvents(conversationId, {
      open: () => {
        reconnectAttempt = 0;
        store.eventConnection = "connected";
        applyOpenCodeEvent(store, { type: "opencode.connected", data: {} });
        onChange();
      },
      message: (event) => handlePayload("message", event?.data),
      error: async () => {
        closeSource();
        store.eventConnection = "disconnected";
        onChange();
        await notifySnapshotNeeded("event-source-error");
        scheduleReconnect();
      },
    });

    EVENT_NAMES.forEach((eventName) => {
      attachEvent(eventName, (event) => handlePayload(eventName, event?.data));
    });
  }

  open();

  return {
    close() {
      closed = true;
      clearReconnectTimer();
      closeSource();
      store.eventConnection = "disconnected";
    },
    reconnectNow() {
      if (closed) return;
      reconnectAttempt = 0;
      open();
    },
    get source() {
      return source;
    },
  };
}
