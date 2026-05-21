import { OpenCodeChatController } from "./controller.js";

function normalized(value) {
  return String(value || "").trim().toLowerCase();
}

function chatMode(root) {
  return normalized(globalThis.EFP_OPENCODE_CHAT_UI_MODE || root?.dataset?.chatMode || "legacy");
}

function shouldRun(root) {
  return Boolean(
    root &&
    root.dataset.agentId &&
    normalized(root.dataset.runtimeType) === "opencode" &&
    chatMode(root) === "thin",
  );
}

function bootOpenCodeChat(root) {
  let controller = null;
  let controllerKey = "";

  const stop = () => {
    controller?.destroy();
    controller = null;
    controllerKey = "";
  };

  const sync = async () => {
    if (!shouldRun(root)) {
      stop();
      return;
    }
    const key = `${root.dataset.agentId}:${root.dataset.conversationId || ""}`;
    if (controller && controllerKey === key) return;
    stop();
    controllerKey = key;
    controller = new OpenCodeChatController({
      agentId: root.dataset.agentId,
      rootElement: root,
    });
    await controller.init();
  };

  const observer = new MutationObserver(() => {
    sync().catch((error) => {
      root.innerHTML = `<div class="opencode-banner is-error">${String(error?.message || error)}</div>`;
    });
  });
  observer.observe(root, {
    attributes: true,
    attributeFilter: ["data-agent-id", "data-runtime-type", "data-chat-mode", "data-conversation-id"],
  });

  root.addEventListener("opencode-chat:new-chat", () => {
    controller?.createNewConversation();
  });
  root.addEventListener("opencode-chat:refresh", () => {
    controller?.refreshSnapshot();
  });

  sync();
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("opencode-chat-root");
  if (root) bootOpenCodeChat(root);
});
