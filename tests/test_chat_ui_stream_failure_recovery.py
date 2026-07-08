"""Broken chat streams must verify run status before reporting failure.

The run usually continues detached on the runtime after a transport break;
declaring "Send failed" (and inviting a duplicate re-send) without checking
the run status loses the running response.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def test_handle_agent_chat_failure_verifies_run_status_before_failing():
    js_source = SRC.read_text(encoding="utf-8")
    assert "async function tryRecoverBrokenChatStream(" in js_source
    assert "async function handleAgentChatFailure(" in js_source
    failure_fn = _extract_js_function(js_source, "handleAgentChatFailure")
    assert "tryRecoverBrokenChatStream" in failure_fn
    assert "requestCtx.usedStream" in failure_fn
    assert "requestCtx.aborted" in failure_fn
    # The call site must await the now-async failure handler so exceptions
    # cannot become unhandled promise rejections.
    assert "await handleAgentChatFailure(" in js_source
    assert re.search(r"(?<!await )(?<!function )handleAgentChatFailure\(agentIdAtSend", js_source) is None


def test_recovered_run_poll_has_give_up_bounds():
    js_source = SRC.read_text(encoding="utf-8")
    assert "RECOVERED_RUN_POLL_MAX_MS" in js_source
    assert "RECOVERED_RUN_POLL_MAX_UNKNOWN_STREAK" in js_source
    poll_fn = _extract_js_function(js_source, "scheduleRecoveredChatRunPoll")
    assert "recoveryUnknownStreak" in poll_fn
    assert "finishRecoveredChatRun" in poll_fn


def _poll_constants(js_source: str) -> str:
    lines = []
    for name in (
        "RECOVERED_RUN_POLL_MAX_MS",
        "RECOVERED_RUN_POLL_MAX_UNKNOWN_STREAK",
        "RECOVERED_RUN_POLL_MAX_FALLBACK_FROZEN_STREAK",
    ):
        match = re.search(rf"^const {name} = .+;$", js_source, re.MULTILINE)
        assert match, f"missing const {name} in chat_ui.js"
        lines.append(match.group(0))
    return "\n".join(lines)


def test_try_recover_broken_chat_stream_behavior_node_smoke():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping stream failure recovery behavior test")

    js_source = SRC.read_text(encoding="utf-8")
    functions = "\n".join(
        _extract_js_function(js_source, name)
        for name in ["tryRecoverBrokenChatStream", "isTerminalChatRunState", "normalizeChatRunStatus"]
    )
    script = (
        """
const assert = require("node:assert/strict");

const calls = [];
let chatState;
let statusResponse;
let statusError = null;
let recoverResult = true;

const state = { selectedAgentId: "agent-1" };

function ensureChatState(_agentId) { return chatState; }
function cancelAssistantTypewriter(_ctx) { calls.push(["cancelTypewriter"]); }
async function fetchChatRunStatusForAgent(agentId, sessionId, requestId) {
  calls.push(["status", agentId, sessionId, requestId]);
  if (statusError) throw statusError;
  return statusResponse;
}
async function finishRecoveredChatRun(agentId, sessionId, requestId, _ctx, payload) {
  calls.push(["finish", agentId, sessionId, requestId, payload && payload.state]);
}
function persistInflightChatRun(agentId, entry) { calls.push(["persist", agentId, entry.session_id, entry.request_id]); }
function setChatStatus(text) { calls.push(["chatStatus", text]); }
async function recoverInflightChatRunForAgent(agentId, sessionId, _metadata, _options) {
  calls.push(["recover", agentId, sessionId]);
  return recoverResult;
}

"""
        + functions
        + """

(async () => {
  // 1. Running run: hands off to the recovery flow, clears the broken request.
  const requestCtx = { clientRequestId: "req-1", sessionIdAtSend: "s-1", message: "hello", startedAt: Date.now(), usedStream: true };
  chatState = { currentRequest: requestCtx, sessionId: "s-1" };
  statusResponse = { ok: true, state: "running", terminal: false };
  let recovered = await tryRecoverBrokenChatStream("agent-1", requestCtx);
  assert.equal(recovered, true);
  assert.ok(calls.some((c) => c[0] === "persist" && c[3] === "req-1"));
  assert.ok(calls.some((c) => c[0] === "recover" && c[2] === "s-1"));
  assert.equal(chatState.currentRequest, null);

  // 2. Terminal run: delivers the finished response from history.
  calls.length = 0;
  const requestCtx2 = { clientRequestId: "req-2", sessionIdAtSend: "s-2", usedStream: true };
  chatState = { currentRequest: requestCtx2, sessionId: "s-2" };
  statusResponse = { ok: true, state: "completed", terminal: true };
  recovered = await tryRecoverBrokenChatStream("agent-1", requestCtx2);
  assert.equal(recovered, true);
  assert.ok(calls.some((c) => c[0] === "finish" && c[3] === "req-2" && c[4] === "completed"));
  assert.ok(!calls.some((c) => c[0] === "recover"));

  // 3. Status fetch fails: falls back to normal failure handling.
  calls.length = 0;
  const requestCtx3 = { clientRequestId: "req-3", sessionIdAtSend: "s-3", usedStream: true };
  chatState = { currentRequest: requestCtx3, sessionId: "s-3" };
  statusError = new Error("network down");
  recovered = await tryRecoverBrokenChatStream("agent-1", requestCtx3);
  assert.equal(recovered, false);
  assert.equal(chatState.currentRequest, requestCtx3);
  statusError = null;

  // 4. Unknown state: falls back to normal failure handling.
  calls.length = 0;
  const requestCtx4 = { clientRequestId: "req-4", sessionIdAtSend: "s-4", usedStream: true };
  chatState = { currentRequest: requestCtx4, sessionId: "s-4" };
  statusResponse = { ok: false, state: "unknown", error: "chat_run_not_found" };
  recovered = await tryRecoverBrokenChatStream("agent-1", requestCtx4);
  assert.equal(recovered, false);
  assert.ok(!calls.some((c) => c[0] === "recover" || c[0] === "finish"));

  // 5. Recovery handoff fails: the broken request is restored for failure UX.
  calls.length = 0;
  const requestCtx5 = { clientRequestId: "req-5", sessionIdAtSend: "s-5", usedStream: true };
  chatState = { currentRequest: requestCtx5, sessionId: "s-5" };
  statusResponse = { ok: true, state: "running", terminal: false };
  recoverResult = false;
  recovered = await tryRecoverBrokenChatStream("agent-1", requestCtx5);
  assert.equal(recovered, false);
  assert.equal(chatState.currentRequest, requestCtx5);

  console.log("ok");
})().catch((error) => { console.error(error); process.exit(1); });
"""
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_recovered_run_poll_gives_up_after_unknown_streak_node_smoke():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping recovered run poll behavior test")

    js_source = SRC.read_text(encoding="utf-8")
    functions = "\n".join(
        _extract_js_function(js_source, name)
        for name in ["scheduleRecoveredChatRunPoll", "stopRecoveredRunPolling", "isTerminalChatRunState", "normalizeChatRunStatus"]
    )
    script = (
        _poll_constants(js_source)
        + """
const assert = require("node:assert/strict");

const calls = [];
const requestCtx = { clientRequestId: "req-1" };
const chatState = { currentRequest: requestCtx };

function ensureChatState(_agentId) { return chatState; }
async function fetchChatRunStatusForAgent(_agentId, _sessionId, _requestId) {
  calls.push(["status"]);
  return { ok: false, state: "unknown", error: "chat_run_not_found" };
}
async function finishRecoveredChatRun(agentId, sessionId, requestId, _ctx, payload) {
  calls.push(["finish", requestId, payload && payload.state]);
  chatState.currentRequest = null;
}
function showToast(message) { calls.push(["toast", message]); }

let pending = [];
globalThis.setTimeout = (callback, _ms) => { pending.push(callback); return pending.length; };
globalThis.clearTimeout = (_id) => {};

"""
        + functions
        + """

(async () => {
  scheduleRecoveredChatRunPoll("agent-1", "s-1", "req-1", requestCtx);
  // Drive the timer chain until it stops rescheduling.
  for (let i = 0; i < 50 && pending.length; i += 1) {
    const next = pending.shift();
    await next();
  }
  const finishCalls = calls.filter((c) => c[0] === "finish");
  assert.equal(finishCalls.length, 1);
  assert.equal(finishCalls[0][1], "req-1");
  assert.equal(finishCalls[0][2], "unknown");
  const statusCalls = calls.filter((c) => c[0] === "status");
  assert.ok(statusCalls.length <= 10, `expected bounded polling, got ${statusCalls.length}`);
  assert.ok(calls.some((c) => c[0] === "toast"));
  assert.equal(pending.length, 0);
  console.log("ok");
})().catch((error) => { console.error(error); process.exit(1); });
"""
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_recovered_run_poll_gives_up_on_frozen_fallback_running_node_smoke():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping frozen fallback poll behavior test")

    js_source = SRC.read_text(encoding="utf-8")
    functions = "\n".join(
        _extract_js_function(js_source, name)
        for name in ["scheduleRecoveredChatRunPoll", "stopRecoveredRunPolling", "isTerminalChatRunState", "normalizeChatRunStatus"]
    )
    script = (
        _poll_constants(js_source)
        + """
const assert = require("node:assert/strict");

const calls = [];
const requestCtx = { clientRequestId: "req-1" };
const chatState = { currentRequest: requestCtx };
let statusResponses = [];

function ensureChatState(_agentId) { return chatState; }
async function fetchChatRunStatusForAgent(_agentId, _sessionId, _requestId) {
  calls.push(["status"]);
  return statusResponses.length > 1 ? statusResponses.shift() : statusResponses[0];
}
async function finishRecoveredChatRun(agentId, sessionId, requestId, _ctx, payload) {
  calls.push(["finish", requestId, payload && payload.state]);
  chatState.currentRequest = null;
}
function showToast(message) { calls.push(["toast", message]); }

let pending = [];
globalThis.setTimeout = (callback, _ms) => { pending.push(callback); return pending.length; };
globalThis.clearTimeout = (_id) => {};

"""
        + functions
        + """

(async () => {
  // A phantom: fallback-sourced running with a frozen updated_at (nothing
  // will ever refresh it; e.g. old-runtime metadata after a pod restart).
  statusResponses = [
    { ok: true, state: "running", terminal: false, source_of_truth: "session_metadata", updated_at: "2026-01-01T00:00:00Z" },
  ];
  scheduleRecoveredChatRunPoll("agent-1", "s-1", "req-1", requestCtx);
  for (let i = 0; i < 50 && pending.length; i += 1) {
    const next = pending.shift();
    await next();
  }
  const finishCalls = calls.filter((c) => c[0] === "finish");
  assert.equal(finishCalls.length, 1);
  assert.equal(finishCalls[0][2], "unknown");
  const statusCalls = calls.filter((c) => c[0] === "status");
  assert.ok(statusCalls.length <= 12, `expected bounded polling, got ${statusCalls.length}`);

  // Registry-sourced running must NOT trip the fallback bound: it polls on
  // until terminal.
  calls.length = 0;
  pending = [];
  const requestCtx2 = { clientRequestId: "req-2" };
  chatState.currentRequest = requestCtx2;
  statusResponses = [];
  for (let i = 0; i < 15; i += 1) {
    statusResponses.push({ ok: true, state: "running", terminal: false, source_of_truth: "run_registry", updated_at: "2026-01-01T00:00:00Z" });
  }
  statusResponses.push({ ok: true, state: "completed", terminal: true, source_of_truth: "run_registry" });
  scheduleRecoveredChatRunPoll("agent-1", "s-1", "req-2", requestCtx2);
  for (let i = 0; i < 50 && pending.length; i += 1) {
    const next = pending.shift();
    await next();
  }
  const finishCalls2 = calls.filter((c) => c[0] === "finish");
  assert.equal(finishCalls2.length, 1);
  assert.equal(finishCalls2[0][2], "completed");
  assert.ok(!calls.some((c) => c[0] === "toast"));

  console.log("ok");
})().catch((error) => { console.error(error); process.exit(1); });
"""
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_stream_execution_observer_ignores_keepalive_comment_blocks():
    from app.services.agent_execution_registry import ChatStreamExecutionObserver

    observer = ChatStreamExecutionObserver("exec-1")
    observer.feed(b": keepalive\n\n: keepalive\n\n")
    assert observer.event_count == 0

    observer.feed(b'event: final\ndata: {"response": "done"}\n\n')
    assert observer.event_count == 1
    assert observer.final_payload == {"response": "done"}
