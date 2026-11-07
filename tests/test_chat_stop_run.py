"""Stopping a reply, and sending while one is in flight.

The Stop button waits for the runtime to settle the run and closes the row as
stopped, not failed; a ``final`` whose completion_state is "cancelled" does
the same; a message typed while a reply is in flight is queued and sent when
the reply ends, or at once with ``interrupt_in_flight_run`` from "Send now"; a
409 ``session_busy`` is handled as a wait, not a failure; and the execution
registry records a stopped run as cancelled.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import I18N_STUB, _extract_js_function
from app.services.agent_execution_registry import _chat_payload_to_terminal

CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "case.js"
        script_path.write_text(I18N_STUB + script, encoding="utf-8")
        result = subprocess.run([node_bin, str(script_path)], check=False, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _functions(*names: str) -> str:
    return "\n".join(_extract_js_function(CHAT_JS, name) for name in names)


# --------------------------------------------------------------- the Stop button


def test_stop_waits_for_the_runtime_and_closes_the_row_as_stopped():
    abort = _extract_js_function(CHAT_JS, "abortActiveChatRequestForSelectedAgent")

    assert "req.cancelRequested = true" in abort
    assert "outcome = await agentApiFor(agentId, `/api/chat/runs/${encodeURIComponent(requestId)}/cancel${query}`" in abort
    assert "finalizeStoppedChatResponse(agentId, req" in abort
    assert "The runtime did not confirm the stop" in abort
    # The old path dropped the request and unlocked the composer before the
    # runtime had answered, and never finished the row.
    assert "chatState.currentRequest = null" not in abort
    assert "req.streamFailed = true" not in abort
    assert "abortController" not in abort
    assert ".catch(() => {})" not in abort


def test_a_cancelled_final_closes_the_row_as_stopped_not_failed():
    handler = _extract_js_function(CHAT_JS, "handleChatStreamEvent")
    assert 'localGetCompletionState(eventData) === "cancelled"' in handler
    assert "finalizeStoppedChatResponse(agentIdAtSend, requestCtx, eventData)" in handler
    assert 'if (requestCtx?.stopFinalized) return "event";' in handler

    non_success = _extract_js_function(CHAT_JS, "finalizeNonSuccessChatResponse")
    assert 'completionState === "cancelled"' in non_success.split("failureSources.has(source)", 1)[0]

    stopped = _extract_js_function(CHAT_JS, "finalizeStoppedChatResponse")
    assert "markAssistantArticleStopped(pendingArticle" in stopped
    assert 'completion_state: "cancelled"' in stopped
    assert "finalizePendingAssistantRow(agentId, requestCtx, payload)" in stopped
    assert "cleanupChatStreamRequest(agentId, requestCtx, { keepStatus: true })" in stopped


def test_history_marks_a_stopped_reply():
    history = _extract_js_function(CHAT_JS, "renderChatHistory")
    assert "assistantGroupWasStopped(entry)" in history
    assert "markAssistantArticleStopped(article" in history


def test_terminal_helpers_know_cancelled():
    script = _functions(
        "getCompletionState",
        "isNonSuccessFinalPayload",
        "isCompletedFinalPayload",
        "terminalStatusFromCompletionState",
        "assistantGroupWasStopped",
    ) + """
console.log(JSON.stringify({
  nonSuccess: isNonSuccessFinalPayload({ completion_state: "cancelled" }),
  completed: isCompletedFinalPayload({ completion_state: "cancelled", response: "partial" }),
  terminal: terminalStatusFromCompletionState("cancelled"),
  stoppedByStatus: assistantGroupWasStopped({ messages: [{ status: "cancelled" }] }),
  stoppedByMetadata: assistantGroupWasStopped({ messages: [{ status: "complete", metadata: { cancelled: true } }] }),
  notStopped: assistantGroupWasStopped({ messages: [{ status: "complete" }] }),
}));
"""
    data = _run_node(script)
    assert data == {
        "nonSuccess": True,
        "completed": False,
        "terminal": "cancelled",
        "stoppedByStatus": True,
        "stoppedByMetadata": True,
        "notStopped": False,
    }


def test_recovery_notice_says_nothing_for_a_stopped_reply():
    script = _extract_js_function(CHAT_JS, "deriveSessionRecoveryNotice") + """
console.log(JSON.stringify({
  byType: deriveSessionRecoveryNotice({ latest_event_type: "chat.cancelled" }),
  byState: deriveSessionRecoveryNotice({ latest_event_state: "cancelled", latest_event_type: "chat.started", request_id: "r" }),
  byCompletion: deriveSessionRecoveryNotice({ completion_state: "cancelled", latest_event_type: "chat.started", request_id: "r" }),
  failed: deriveSessionRecoveryNotice({ latest_event_type: "chat.failed" }),
}));
"""
    data = _run_node(script)
    assert data["byType"] is None
    assert data["byState"] is None
    assert data["byCompletion"] is None
    assert data["failed"]["level"] == "error"


def test_a_run_being_stopped_still_counts_as_running():
    assert 'RUNNING_CHAT_RUN_STATES = new Set(["running", "accepted", "queued", "in_progress", "cancelling"])' in CHAT_JS


def test_timeline_marks_a_cancelled_step_as_stopped():
    from test_chat_ui_agent_timeline_node import _run_node as _run_timeline_node, _timeline_bundle

    script = f"""
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
{_timeline_bundle(CHAT_JS)}

const chatState = {{
  sessionId: "sess-1",
  currentRequest: {{ requestId: "req-1", clientRequestId: "req-1", sessionIdAtSend: "sess-1" }},
}};
const events = [
  normalizeRuntimeEvent({{ id: "evt-step-start", type: "session.next.step.started", properties: {{ model: "gpt-test" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-text-d1", type: "session.next.text.delta", properties: {{ partID: "text-1", delta: "hello" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-cancelled", type: "session.next.step.cancelled", state: "cancelled", properties: {{ phase: "provider_stream" }}, session_id: "sess-1", request_id: "req-1" }}),
];
const cancelledEvent = events[2];
events.forEach((event) => reduceAgentTimelineEvent(chatState, event));
const timeline = chatState.inflightAgentTimeline;
const step = timeline.items.find((item) => item.kind === "step");
console.log(JSON.stringify({{
  lifecycle: cancelledEvent.lifecycle_type,
  normalizedType: cancelledEvent.type,
  completed: timeline.completed,
  status: timeline.status,
  stepStatus: step.status,
  stepTitle: step.title,
}}));
"""
    data = _run_timeline_node(script)
    assert data["lifecycle"] == "execution.cancelled"
    assert data["normalizedType"] == "execution.cancelled"
    assert data["completed"] is True
    assert data["status"] == "cancelled"
    assert data["stepStatus"] == "cancelled"
    assert data["stepTitle"] == "Agent step stopped"


# ------------------------------------------------- sending while a reply runs


def test_send_queues_behind_the_running_reply_instead_of_refusing():
    submit = _extract_js_function(CHAT_JS, "submitChatForSelectedAgent")
    queue_idx = submit.index("queueComposerMessageForAgent(agentIdAtSend)")
    guard_idx = submit.index('guardNoActiveChatRequestForAgent(agentIdAtSend, "send another message")')
    assert queue_idx < guard_idx
    assert "...(chatState.sendInterruptsActiveRun ? { interrupt_in_flight_run: true } : {})" in submit
    assert "handleSessionBusyRefusal(agentIdAtSend, requestCtx, error.sessionBusy)" in submit

    controls = _extract_js_function(CHAT_JS, "syncSelectedAgentChatActionControls")
    assert "dom.sendChatBtn.disabled = needsStart && !wakeable" in controls
    assert "renderQueuedComposerMessage(agentId)" in controls

    stream = _extract_js_function(CHAT_JS, "trySubmitChatStreamForSelectedAgent")
    assert "readSessionBusyRefusal(resp)" in stream
    assert "busyError.sessionBusy = refusal" in stream

    assert 'id="composer-queued-message"' in APP_HTML


def test_queued_message_goes_when_the_reply_ends_and_send_now_interrupts():
    script = """
const state = { selectedAgentId: "agent-A", chatStatesByAgent: new Map(), agentSessionIds: new Map() };
const container = {
  hidden: true,
  innerHTML: "",
  classList: {
    add(name) { if (name === "hidden") container.hidden = true; },
    remove(name) { if (name === "hidden") container.hidden = false; },
  },
};
const dom = { chatInput: { value: "", dispatchEvent() {} }, composerQueuedMessage: container };
const calls = { toasts: [], status: [], submits: [], aborts: 0 };
function escapeHtml(value) { return String(value ?? ""); }
function escapeHtmlAttr(value) { return String(value ?? ""); }
function showToast(message) { calls.toasts.push(message); }
function setChatStatus(message) { calls.status.push(message); }
function renderInputPreview() {}
function resetChatInputHeight() {}
function syncChatInputHeight() {}
function clearDraftForAgent() {}
function focusComposer() {}
async function submitChatForSelectedAgent() {
  const chatState = ensureChatState(state.selectedAgentId);
  calls.submits.push({ text: dom.chatInput.value, interrupt: chatState.sendInterruptsActiveRun, files: chatState.pendingFiles.length });
  dom.chatInput.value = "";
  chatState.pendingFiles = [];
}
async function abortActiveChatRequestForSelectedAgent() {
  calls.aborts += 1;
  const chatState = ensureChatState(state.selectedAgentId);
  chatState.isSubmitting = false;
  renderQueuedComposerMessage(state.selectedAgentId);
}
""" + _functions(
        "createDefaultChatState",
        "ensureChatState",
        "hasActiveChatRequestForAgent",
        "queuedComposerMessagePreview",
        "queueComposerMessageForAgent",
        "renderQueuedComposerMessage",
        "restoreQueuedIntoComposer",
        "flushQueuedComposerMessage",
        "restoreQueuedMessageToComposer",
        "discardQueuedMessage",
        "interruptAndSendQueuedMessage",
    ) + """
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
(async () => {
  const chatState = ensureChatState("agent-A");
  chatState.sessionId = "s-1";
  chatState.isSubmitting = true;
  dom.chatInput.value = "second question";
  chatState.pendingFiles = [{ status: "uploaded", name: "a.txt" }];
  const queued = queueComposerMessageForAgent("agent-A");
  const afterQueue = {
    queued,
    chipShown: !container.hidden,
    input: dom.chatInput.value,
    pending: chatState.pendingFiles.length,
    preview: container.innerHTML.includes("second question"),
    submits: calls.submits.length,
  };
  // The reply ends: the queued message goes on its own.
  chatState.isSubmitting = false;
  renderQueuedComposerMessage("agent-A");
  await tick();
  const afterFlush = { submits: calls.submits.slice(), chipShown: !container.hidden, queuedMessage: chatState.queuedMessage };
  // "Send now" stops the reply first and marks the send as an interrupt.
  chatState.isSubmitting = true;
  dom.chatInput.value = "urgent";
  queueComposerMessageForAgent("agent-A");
  await interruptAndSendQueuedMessage("agent-A");
  await tick();
  const afterInterrupt = { aborts: calls.aborts, submits: calls.submits.slice(), flagReset: chatState.sendInterruptsActiveRun };
  // Discard drops it; Edit hands it back.
  chatState.isSubmitting = true;
  dom.chatInput.value = "never mind";
  queueComposerMessageForAgent("agent-A");
  discardQueuedMessage("agent-A");
  const afterDiscard = { queuedMessage: chatState.queuedMessage, chipShown: !container.hidden };
  dom.chatInput.value = "edit me";
  queueComposerMessageForAgent("agent-A");
  const inputWhileQueued = dom.chatInput.value;
  restoreQueuedMessageToComposer("agent-A");
  const afterEdit = { input: dom.chatInput.value, inputWhileQueued, queuedMessage: chatState.queuedMessage };
  console.log(JSON.stringify({ afterQueue, afterFlush, afterInterrupt, afterDiscard, afterEdit }));
})();
"""
    data = _run_node(script)
    assert data["afterQueue"] == {"queued": True, "chipShown": True, "input": "", "pending": 0, "preview": True, "submits": 0}
    assert data["afterFlush"]["submits"] == [{"text": "second question", "interrupt": False, "files": 1}]
    assert data["afterFlush"]["chipShown"] is False
    assert data["afterFlush"]["queuedMessage"] is None
    assert data["afterInterrupt"]["aborts"] == 1
    assert data["afterInterrupt"]["submits"][-1] == {"text": "urgent", "interrupt": True, "files": 0}
    assert data["afterInterrupt"]["flagReset"] is False
    assert data["afterDiscard"] == {"queuedMessage": None, "chipShown": False}
    assert data["afterEdit"] == {"input": "edit me", "inputWhileQueued": "", "queuedMessage": None}


# ------------------------------------------------------- execution registry


def test_chat_payload_to_terminal_records_a_stopped_run_as_cancelled():
    status, code, message, summary = _chat_payload_to_terminal(
        status_code=200,
        payload={"completion_state": "cancelled", "status": "cancelled", "response": "the first words"},
    )
    assert (status, code, message, summary) == ("cancelled", "chat_cancelled", None, "the first words")


# ------------------------------------------- editing the message of a stopped reply


def test_stop_offers_the_message_back_and_send_replaces_the_stopped_turn():
    stopped = _extract_js_function(CHAT_JS, "finalizeStoppedChatResponse")
    assert "offerStoppedMessageForResend(agentId, requestCtx, payload)" in stopped

    submit = _extract_js_function(CHAT_JS, "submitChatForSelectedAgent")
    attachments_idx = submit.index("buildAttachmentsFromChatState(agentIdAtSend, chatState)")
    replace_idx = submit.index("replaceStoppedTurnBeforeSend(agentIdAtSend, chatState)")
    request_idx = submit.index('const requestMessage = messageAtSend || "[attachment]"')
    assert attachments_idx < replace_idx < request_idx

    controls = _extract_js_function(CHAT_JS, "syncSelectedAgentChatActionControls")
    assert "renderReplaceStoppedChip(agentId)" in controls

    marker = _extract_js_function(CHAT_JS, "markAssistantArticleStopped")
    assert '"data-stopped-action", "edit-resend"' in marker

    retry = _extract_js_function(CHAT_JS, "retryAssistantMessage")
    assert "pendingFilesFromUserArticle(userArticle)" in retry
    assert "chatState.pendingFiles = []" not in retry

    history = _extract_js_function(CHAT_JS, "renderChatHistory")
    assert "article.dataset.attachments = JSON.stringify(compactAttachmentsForArticle(normalizedAttachments))" in history
    bubble = _extract_js_function(CHAT_JS, "buildUserMessageArticle")
    assert "data-attachments=" in bubble

    default_state = _extract_js_function(CHAT_JS, "createDefaultChatState")
    assert "replaceStoppedTurn: null" in default_state
    assert 'id="composer-replace-stopped"' in APP_HTML


def test_a_stopped_message_returns_to_the_composer_for_editing():
    script = """
const state = { selectedAgentId: "agent-A", chatStatesByAgent: new Map(), agentSessionIds: new Map() };
const container = {
  hidden: true,
  innerHTML: "",
  classList: {
    add(name) { if (name === "hidden") container.hidden = true; },
    remove(name) { if (name === "hidden") container.hidden = false; },
  },
};
const userArticle = { dataset: { optimisticUser: "1", clientRequestId: "req-1" } };
const dom = {
  chatInput: { value: "", dispatchEvent() {} },
  composerReplaceStopped: container,
  messageList: { querySelector(selector) { return selector.includes('data-client-request-id="req-1"') ? userArticle : null; } },
};
const calls = { editButtons: 0, reconciled: [], focus: 0 };
function escapeHtml(value) { return String(value ?? ""); }
function escapeHtmlAttr(value) { return String(value ?? ""); }
function renderInputPreview() {}
function syncChatInputHeight() {}
function focusComposer() { calls.focus += 1; }
function addEditButtonsToMessages() { calls.editButtons += 1; }
async function reconcileOptimisticUserMessageId(agentId, sessionId, article) {
  calls.reconciled.push(sessionId);
  article.dataset.messageId = "msg-from-session";
  return "msg-from-session";
}
function getLatestOptimisticUserArticle() { return userArticle; }
""" + _functions(
        "createDefaultChatState",
        "ensureChatState",
        "restoreQueuedIntoComposer",
        "cssAttributeValue",
        "findUserArticleForStoppedTurn",
        "setReplaceStoppedTurn",
        "renderReplaceStoppedChip",
        "offerStoppedMessageForResend",
        "dismissReplaceStoppedTurn",
    ) + """
const tick = () => new Promise((resolve) => setTimeout(resolve, 5));
(async () => {
  const chatState = ensureChatState("agent-A");
  chatState.sessionId = "s-1";
  const requestCtx = {
    clientRequestId: "req-1",
    sessionIdAtSend: "s-1",
    backupMessage: "first question",
    backupPendingFiles: [{ status: "uploaded", name: "a.txt", file_id: "f1" }],
  };
  const offered = offerStoppedMessageForResend("agent-A", requestCtx, { user_message_id: "msg-u1" });
  const afterStop = {
    offered,
    input: dom.chatInput.value,
    files: chatState.pendingFiles.length,
    chipShown: !container.hidden,
    chipText: container.innerHTML.includes("Sending replaces the stopped message."),
    turn: chatState.replaceStoppedTurn && chatState.replaceStoppedTurn.userMessageId,
    bubbleId: userArticle.dataset.messageId,
    stillOptimistic: "optimisticUser" in userArticle.dataset,
    editButtons: calls.editButtons,
    focus: calls.focus,
  };
  dismissReplaceStoppedTurn("agent-A");
  const afterDismiss = { chipShown: !container.hidden, turn: chatState.replaceStoppedTurn, input: dom.chatInput.value };
  // Something typed during the reply is the next message: nothing is restored.
  chatState.pendingFiles = [];
  dom.chatInput.value = "next question";
  const typedOffered = offerStoppedMessageForResend("agent-A", requestCtx, { user_message_id: "msg-u1" });
  const afterTyped = { offered: typedOffered, input: dom.chatInput.value, turn: chatState.replaceStoppedTurn };
  // A stop made for "Send now" leaves the queued message in charge.
  dom.chatInput.value = "";
  chatState.queuedMessage = { text: "queued" };
  const queuedOffered = offerStoppedMessageForResend("agent-A", requestCtx, { user_message_id: "msg-u1" });
  const afterQueued = { offered: queuedOffered, input: dom.chatInput.value, turn: chatState.replaceStoppedTurn };
  chatState.queuedMessage = null;
  // No id on the final (a forced stop): the session is asked for it.
  delete userArticle.dataset.messageId;
  const forcedOffered = offerStoppedMessageForResend("agent-A", requestCtx, {});
  await tick();
  const afterForced = { offered: forcedOffered, reconciled: calls.reconciled.slice(), bubbleId: userArticle.dataset.messageId, chipShown: !container.hidden };
  // The conversation changes under it: the chip goes, the text stays.
  chatState.sessionId = "s-2";
  renderReplaceStoppedChip("agent-A");
  const afterSwitch = { chipShown: !container.hidden, turn: chatState.replaceStoppedTurn, input: dom.chatInput.value };
  console.log(JSON.stringify({ afterStop, afterDismiss, afterTyped, afterQueued, afterForced, afterSwitch }));
})();
"""
    data = _run_node(script)
    assert data["afterStop"] == {
        "offered": True,
        "input": "first question",
        "files": 1,
        "chipShown": True,
        "chipText": True,
        "turn": "msg-u1",
        "bubbleId": "msg-u1",
        "stillOptimistic": False,
        "editButtons": 1,
        "focus": 1,
    }
    assert data["afterDismiss"] == {"chipShown": False, "turn": None, "input": "first question"}
    assert data["afterTyped"] == {"offered": False, "input": "next question", "turn": None}
    assert data["afterQueued"] == {"offered": False, "input": "", "turn": None}
    assert data["afterForced"] == {"offered": True, "reconciled": ["s-1"], "bubbleId": "msg-from-session", "chipShown": True}
    assert data["afterSwitch"] == {"chipShown": False, "turn": None, "input": "first question"}


def test_sending_the_edited_message_replaces_the_stopped_turn():
    script = """
const state = { selectedAgentId: "agent-A", chatStatesByAgent: new Map(), agentSessionIds: new Map() };
const container = { hidden: true, innerHTML: "", classList: { add() { container.hidden = true; }, remove() { container.hidden = false; } } };
const userArticle = { dataset: { messageId: "msg-u1" } };
const dom = {
  chatInput: { value: "edited question", dispatchEvent() {} },
  composerReplaceStopped: container,
  messageList: { querySelector(selector) { return selector.includes('data-message-id="msg-u1"') ? userArticle : null; } },
};
const calls = { fetches: [], truncated: [], toasts: [], status: [] };
let sessionMessages = [];
let deleteOk = true;
function escapeHtml(value) { return String(value ?? ""); }
function escapeHtmlAttr(value) { return String(value ?? ""); }
function showToast(message) { calls.toasts.push(message); }
function setChatStatus(message) { calls.status.push(message); }
function currentSessionIdForAgent() { return "s-1"; }
function getHistoryUserVisibleContent(message) { return String(message.content || ""); }
function truncateDomFromUserArticle(article) { calls.truncated.push(article); }
async function agentApiFor(agentId, path) { return { messages: sessionMessages }; }
async function fetch(url) {
  calls.fetches.push(url);
  return { ok: deleteOk, async json() { return { success: deleteOk }; } };
}
""" + _functions(
        "createDefaultChatState",
        "ensureChatState",
        "cssAttributeValue",
        "findUserArticleForStoppedTurn",
        "setReplaceStoppedTurn",
        "renderReplaceStoppedChip",
        "latestUserMessageInSession",
        "replaceStoppedTurnBeforeSend",
    ) + """
(async () => {
  const chatState = ensureChatState("agent-A");
  chatState.sessionId = "s-1";
  // The stopped message is the last one of the session: it goes, this one replaces it.
  sessionMessages = [{ role: "user", id: "msg-u1", content: "first question" }, { role: "assistant", id: "msg-a1", status: "cancelled" }];
  setReplaceStoppedTurn("agent-A", chatState, { userMessageId: "msg-u1", sessionId: "s-1", text: "first question" });
  const replaced = await replaceStoppedTurnBeforeSend("agent-A", chatState);
  const afterReplace = { result: replaced, fetches: calls.fetches.slice(), truncated: calls.truncated.length, turn: chatState.replaceStoppedTurn, toasts: calls.toasts.length, chipShown: !container.hidden };
  // Someone sent a newer message meanwhile: nothing is deleted, this goes as a new message.
  calls.fetches = [];
  sessionMessages = [{ role: "user", id: "msg-u1", content: "first question" }, { role: "user", id: "msg-u2", content: "from another tab" }];
  setReplaceStoppedTurn("agent-A", chatState, { userMessageId: "msg-u1", sessionId: "s-1", text: "first question" });
  const newer = await replaceStoppedTurnBeforeSend("agent-A", chatState);
  const afterNewer = { result: newer, fetches: calls.fetches.length, turn: chatState.replaceStoppedTurn, toasts: calls.toasts.length };
  // No id known (an older runtime): the last user message with the same text is it.
  calls.fetches = [];
  sessionMessages = [{ role: "user", id: "msg-u9", content: "first question" }];
  setReplaceStoppedTurn("agent-A", chatState, { userMessageId: "", clientRequestId: "req-x", sessionId: "s-1", text: "first question" });
  const byText = await replaceStoppedTurnBeforeSend("agent-A", chatState);
  const afterByText = { result: byText, fetches: calls.fetches.slice() };
  // The runtime refuses the deletion: sent as a new message, and said so.
  calls.fetches = [];
  deleteOk = false;
  sessionMessages = [{ role: "user", id: "msg-u1", content: "first question" }];
  setReplaceStoppedTurn("agent-A", chatState, { userMessageId: "msg-u1", sessionId: "s-1", text: "first question" });
  const refused = await replaceStoppedTurnBeforeSend("agent-A", chatState);
  const afterRefused = { result: refused, fetches: calls.fetches.length, turn: chatState.replaceStoppedTurn, toasts: calls.toasts.length };
  // A second Send while the first replacement is under way does nothing.
  deleteOk = true;
  setReplaceStoppedTurn("agent-A", chatState, { userMessageId: "msg-u1", sessionId: "s-1", text: "first question" });
  chatState.replaceStoppedTurn.replacing = true;
  const busy = await replaceStoppedTurnBeforeSend("agent-A", chatState);
  console.log(JSON.stringify({ afterReplace, afterNewer, afterByText, afterRefused, busy }));
})();
"""
    data = _run_node(script)
    assert data["afterReplace"]["result"] == "replaced"
    assert data["afterReplace"]["fetches"] == ["/a/agent-A/api/sessions/s-1/messages/msg-u1/delete-from-here"]
    assert data["afterReplace"]["truncated"] == 1
    assert data["afterReplace"]["turn"] is None
    assert data["afterReplace"]["toasts"] == 0
    assert data["afterReplace"]["chipShown"] is False
    assert data["afterNewer"] == {"result": "sent-as-new", "fetches": 0, "turn": None, "toasts": 1}
    assert data["afterByText"] == {"result": "replaced", "fetches": ["/a/agent-A/api/sessions/s-1/messages/msg-u9/delete-from-here"]}
    assert data["afterRefused"] == {"result": "sent-as-new", "fetches": 1, "turn": None, "toasts": 2}
    assert data["busy"] == "busy"


def test_edit_and_resend_from_a_stopped_row_brings_text_and_files_back():
    script = """
const state = { selectedAgentId: "agent-A", chatStatesByAgent: new Map(), agentSessionIds: new Map() };
const container = { hidden: true, innerHTML: "", classList: { add() { container.hidden = true; }, remove() { container.hidden = false; } } };
const dom = { chatInput: { value: "", dispatchEvent() {} }, composerReplaceStopped: container };
const calls = { confirms: 0, toasts: [] };
let confirmAnswer = true;
let shownContent = "first question";
const attachments = [{ file_id: "f1", name: "a.txt", content_type: "text/plain", size: 12, type: "file", parsed: true }];
const userArticle = { dataset: { messageId: "msg-u1", attachments: JSON.stringify(attachments) } };
const userRow = { previousElementSibling: null, querySelector(selector) { return selector === 'article[data-local-user="1"]' ? userArticle : null; } };
const assistantRow = { previousElementSibling: userRow, querySelector() { return null; } };
const assistantArticle = { closest(selector) { return selector === ".message-row" ? assistantRow : null; } };
function escapeHtml(value) { return String(value ?? ""); }
function escapeHtmlAttr(value) { return String(value ?? ""); }
function showToast(message) { calls.toasts.push(message); }
function renderInputPreview() {}
function syncChatInputHeight() {}
function hasActiveChatRequestForAgent() { return false; }
function getUserArticleContent() { return shownContent; }
async function showConfirm() { calls.confirms += 1; return confirmAnswer; }
""" + _functions(
        "createDefaultChatState",
        "ensureChatState",
        "restoreQueuedIntoComposer",
        "findPrecedingUserArticle",
        "compactAttachmentsForArticle",
        "pendingFilesFromAttachments",
        "userArticleAttachments",
        "pendingFilesFromUserArticle",
        "setReplaceStoppedTurn",
        "renderReplaceStoppedChip",
        "beginEditAndResendFromStoppedRow",
    ) + """
(async () => {
  const chatState = ensureChatState("agent-A");
  chatState.sessionId = "s-1";
  const began = await beginEditAndResendFromStoppedRow("agent-A", assistantArticle);
  const first = {
    began,
    input: dom.chatInput.value,
    files: chatState.pendingFiles.map((pf) => [pf.file_id, pf.status, pf.uploadedData.parsed]),
    turn: chatState.replaceStoppedTurn && [chatState.replaceStoppedTurn.userMessageId, chatState.replaceStoppedTurn.sessionId],
    confirms: calls.confirms,
    chipShown: !container.hidden,
  };
  // Something else is in the composer: the member is asked before it is replaced.
  chatState.replaceStoppedTurn = null;
  chatState.pendingFiles = [];
  dom.chatInput.value = "something else";
  confirmAnswer = false;
  const declined = await beginEditAndResendFromStoppedRow("agent-A", assistantArticle);
  const second = { began: declined, input: dom.chatInput.value, confirms: calls.confirms, turn: chatState.replaceStoppedTurn };
  // An attachment-only message: the placeholder is not text to edit, the file is.
  dom.chatInput.value = "";
  shownContent = "[attachment]";
  const filesOnly = await beginEditAndResendFromStoppedRow("agent-A", assistantArticle);
  const third = { began: filesOnly, input: dom.chatInput.value, files: chatState.pendingFiles.length };
  // Nothing to bring back.
  chatState.replaceStoppedTurn = null;
  chatState.pendingFiles = [];
  userArticle.dataset.attachments = "";
  const empty = await beginEditAndResendFromStoppedRow("agent-A", assistantArticle);
  const fourth = { began: empty, toasts: calls.toasts.length };
  const compact = compactAttachmentsForArticle([{ file_id: "f1", name: "a.txt" }, { name: "no-id" }, { id: "img1", type: "image", url: "/files/img1", previewUrl: "blob:x" }]);
  const restored = pendingFilesFromAttachments(compact);
  const roundTrip = {
    ids: compact.map((item) => item.file_id),
    restored: restored.map((pf) => [pf.file_id, pf.isImage, pf.previewUrl, pf.status]),
    badJson: userArticleAttachments({ dataset: { attachments: "{not json" } }),
  };
  console.log(JSON.stringify({ first, second, third, fourth, roundTrip }));
})();
"""
    data = _run_node(script)
    assert data["first"] == {
        "began": True,
        "input": "first question",
        "files": [["f1", "uploaded", True]],
        "turn": ["msg-u1", "s-1"],
        "confirms": 0,
        "chipShown": True,
    }
    assert data["second"] == {"began": False, "input": "something else", "confirms": 1, "turn": None}
    assert data["third"] == {"began": True, "input": "", "files": 1}
    assert data["fourth"] == {"began": False, "toasts": 1}
    assert data["roundTrip"] == {
        "ids": ["f1", "img1"],
        "restored": [["f1", False, None, "uploaded"], ["img1", True, "blob:x", "uploaded"]],
        "badJson": [],
    }

    failed = _chat_payload_to_terminal(status_code=200, payload={"completion_state": "error", "error": "boom"})
    assert failed[0] == "failed"
