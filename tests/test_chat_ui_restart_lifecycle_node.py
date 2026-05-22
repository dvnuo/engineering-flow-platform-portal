import shutil
import subprocess
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _restart_lifecycle_functions(src):
    return "\n".join(
        _extract_js_function(src, name)
        for name in [
            "parseAgentLifecycleAction",
            "applyLocalAgentStatus",
            "updateAgentRuntimeStatusCache",
            "waitForAgentRuntimeStatus",
            "pollAgentUntilRestartComplete",
            "agentRestartErrorMessage",
            "resetLocalChatSubmissionForAgent",
            "action",
        ]
    )


def test_restart_action_polls_status_until_runtime_ready_node_smoke():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping restart lifecycle behavior test")

    src = SRC.read_text(encoding="utf-8")
    functions = _restart_lifecycle_functions(src)
    script = (
        """
const assert = require("node:assert/strict");

const apiCalls = [];
const statusCalls = [];
const calls = [];
const statusQueue = ["restarting", "running"];
const chatState = {
  currentRequest: { clientRequestId: "req-1", sessionIdAtSend: "s-1" },
  sessionId: "s-1",
};

const state = {
  selectedAgentId: "agent-1",
  mineAgents: [{ id: "agent-1", name: "Assistant", status: "running" }],
  agentStatus: new Map([["agent-1", { status: "running" }]]),
  eventWsAgentId: "agent-1",
};
const dom = {
  selectedStatus: { textContent: "", className: "" },
};

globalThis.setTimeout = (resolve, _ms) => {
  resolve();
  return 0;
};

function confirm() { return true; }
function setChatStatus(text, isError = false) {
  statusCalls.push({ text, isError, apiCallCount: apiCalls.length });
}
function showToast(message) { calls.push(["toast", message]); }
function ensureChatState(agentId) {
  assert.equal(agentId, "agent-1");
  return chatState;
}
function clearWaitingForRuntimeEventsTimer(requestCtx) { calls.push(["clearWaiting", requestCtx.clientRequestId]); }
function cancelAssistantTypewriter(requestCtx) { calls.push(["cancelTypewriter", requestCtx.clientRequestId]); }
function setChatSubmittingForAgent(agentId, submitting) {
  calls.push(["setSubmitting", agentId, submitting]);
  chatState.isSubmitting = submitting;
}
async function refreshAll() { calls.push(["refreshAll"]); }
function disconnectEventSocket() { calls.push(["disconnectEventSocket"]); state.eventWsAgentId = null; }
function ensureEventSocketForSelectedAgent() { calls.push(["ensureEventSocketForSelectedAgent"]); }
async function loadSessionForAgent(agentId, sessionId, options) {
  calls.push(["loadSessionForAgent", agentId, sessionId, options.render]);
}
function renderAgentList() { calls.push(["renderAgentList"]); }
function renderAgentActions(_agent, status) { calls.push(["renderAgentActions", status]); }
function syncSelectedAgentChatActionControls() { calls.push(["syncSelectedAgentChatActionControls"]); }
async function api(path, options = {}) {
  apiCalls.push({ path, method: options.method || "" });
  if (path === "/api/agents/agent-1/restart") return { status: "restarting", last_error: "Restart requested: req-1" };
  if (path === "/api/agents/agent-1/status") {
    return { id: "agent-1", status: statusQueue.shift() || "running" };
  }
  throw new Error(`unexpected api call ${path}`);
}
"""
        + functions
        + """

(async () => {
  await action("/api/agents/agent-1/restart");
  for (let i = 0; i < 6; i += 1) await Promise.resolve();

  assert.equal(apiCalls[0].path, "/api/agents/agent-1/restart");
  assert.equal(apiCalls.filter((call) => call.path === "/api/agents/agent-1/status").length, 2);
  assert.ok(calls.some((call) => call[0] === "clearWaiting" && call[1] === "req-1"));
  assert.ok(calls.some((call) => call[0] === "setSubmitting" && call[2] === false));
  assert.ok(calls.some((call) => call[0] === "disconnectEventSocket"));
  assert.ok(calls.some((call) => call[0] === "loadSessionForAgent" && call[2] === "s-1"));
  assert.ok(calls.some((call) => call[0] === "ensureEventSocketForSelectedAgent"));

  const requested = statusCalls.find((call) => call.text === "Restart requested.\\nWaiting for runtime pod to restart…");
  const waiting = statusCalls.find((call) => call.text === "Restarting assistant… waiting for runtime pod to become ready.");
  const final = statusCalls.find((call) => call.text === "Assistant restart completed.");
  assert.ok(requested, "restart should show requested/waiting status");
  assert.ok(waiting, "restart should show rollout waiting status");
  assert.ok(final, "restart should show final ready status");
  assert.ok(final.apiCallCount >= 3, "final ready status must wait for restart plus status polling");
  assert.equal(statusCalls.some((call) => call.text === "Assistant restarted."), false);
  assert.equal(chatState.currentRequest, null);
  assert.equal(state.agentStatus.get("agent-1").status, "running");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    )

    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_restart_action_does_not_downgrade_running_after_fast_poll_or_refresh():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping restart lifecycle behavior test")

    src = SRC.read_text(encoding="utf-8")
    functions = _restart_lifecycle_functions(src)
    script = (
        """
const assert = require("node:assert/strict");

const apiCalls = [];
const statusCalls = [];
const calls = [];
const chatState = { currentRequest: null, sessionId: "s-1" };
const state = {
  selectedAgentId: "agent-1",
  mineAgents: [{ id: "agent-1", name: "Assistant", status: "running" }],
  agentStatus: new Map([["agent-1", { status: "running" }]]),
  eventWsAgentId: null,
};
const dom = {
  selectedStatus: { textContent: "", className: "" },
};

globalThis.setTimeout = (resolve, _ms) => {
  resolve();
  return 0;
};

function confirm() { return true; }
function setChatStatus(text, isError = false) { statusCalls.push({ text, isError }); }
function showToast(message) { calls.push(["toast", message]); }
function ensureChatState(agentId) {
  assert.equal(agentId, "agent-1");
  return chatState;
}
function clearWaitingForRuntimeEventsTimer(requestCtx) { if (requestCtx) calls.push(["clearWaiting", requestCtx.clientRequestId]); }
function cancelAssistantTypewriter(requestCtx) { if (requestCtx) calls.push(["cancelTypewriter", requestCtx.clientRequestId]); }
function setChatSubmittingForAgent(agentId, submitting) {
  calls.push(["setSubmitting", agentId, submitting]);
  chatState.isSubmitting = submitting;
}
async function refreshAll() {
  calls.push(["refreshAll"]);
  state.agentStatus.set("agent-1", { status: "running" });
  state.mineAgents[0].status = "running";
}
function disconnectEventSocket() { calls.push(["disconnectEventSocket"]); }
function ensureEventSocketForSelectedAgent() { calls.push(["ensureEventSocketForSelectedAgent"]); }
async function loadSessionForAgent(agentId, sessionId, options) {
  calls.push(["loadSessionForAgent", agentId, sessionId, options.render]);
}
function renderAgentList() { calls.push(["renderAgentList"]); }
function renderAgentActions(_agent, status) { calls.push(["renderAgentActions", status]); }
function syncSelectedAgentChatActionControls() { calls.push(["syncSelectedAgentChatActionControls"]); }
async function api(path, options = {}) {
  apiCalls.push({ path, method: options.method || "" });
  if (path === "/api/agents/agent-1/restart") return { status: "restarting" };
  if (path === "/api/agents/agent-1/status") return { id: "agent-1", status: "running" };
  throw new Error(`unexpected api call ${path}`);
}
"""
        + functions
        + """

(async () => {
  await action("/api/agents/agent-1/restart");
  for (let i = 0; i < 6; i += 1) await Promise.resolve();

  assert.equal(state.agentStatus.get("agent-1").status, "running");
  assert.equal(state.mineAgents[0].status, "running");
  assert.equal(statusCalls.some((call) => call.text === "Assistant restarted."), false);

  const completedIndex = statusCalls.findIndex((call) => call.text === "Assistant restart completed.");
  assert.ok(completedIndex >= 0, "restart should complete from status polling");
  const waitingAfterCompleted = statusCalls
    .slice(completedIndex + 1)
    .some((call) => call.text === "Restarting assistant… waiting for runtime pod to become ready.");
  assert.equal(waitingAfterCompleted, false);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    )

    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
