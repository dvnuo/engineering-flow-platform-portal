export function createOpenCodeChatStore({ agentId } = {}) {
  return {
    agentId: agentId || "",
    conversationId: "",
    opencodeSessionId: "",
    runtimeHealth: "unknown",
    sessionStatus: "unknown",
    messagesById: new Map(),
    partsById: new Map(),
    messageOrder: [],
    localSubmit: null,
    eventConnection: "disconnected",
    snapshotNeeded: false,
    permissionsById: new Map(),
    children: [],
    todo: [],
    diff: null,
    ui: {
      selectedPanel: "thinking",
      expandedParts: new Set(),
      scrollAnchor: "",
    },
    errors: [],
  };
}
