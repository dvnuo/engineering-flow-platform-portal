import json
import shutil
import subprocess
from pathlib import Path

from _js_extract_helpers import _extract_js_function


def _js_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        import pytest
        pytest.skip("node is not installed")
    completed = subprocess.run([node_bin, "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _base_functions(js: str) -> str:
    names = [
        "parseSseEvent",
        "normalizeChatStreamEventName",
        "normalizeChatStreamEventData",
        "isChatStreamWrapperEventName",
        "isChatStreamFinalEventName",
        "isDirectCompletionEventName",
        "isChatStreamDeltaEventName",
        "getChatStreamEventType",
        "isChatStreamDeltaPayload",
        "getChatStreamTextPayload",
        "getCompletionState",
        "isCompletedFinalPayload",
        "isNonSuccessFinalPayload",
        "finalResponseText",
        "handleChatStreamEvent",
        "handleChatStreamMissingFinal",
        "trySubmitChatStreamForSelectedAgent",
    ]
    return "\n".join(_extract_js_function(js, name) for name in names)


def test_stream_behaviors_cover_delta_final_and_candidate_paths():
    js = _js_source()
    script = f"""
let successCalls = [];
let incompleteCalls = [];
let status = [];
let currentRequestCleared = false;
const state = {{ selectedAgentId: "agent-1" }};
const dom = {{ messageList: null }};
function updatePendingAssistantStreamContent() {{}}
function rememberAssociatedRuntimeDeltaEvent() {{}}
function getAssociatedRuntimeDeltaEvent() {{ return null; }}
function shouldIgnoreAssistantStreamDelta() {{ return false; }}
function handleAgentEventMessage() {{}}
function showToast() {{}}
function setChatStatus(msg) {{ status.push(msg); }}
function removeTemporaryAssistantRows() {{}}
function loadSessionForAgent() {{ return Promise.resolve(); }}
function syncSelectedAgentChatActionControls() {{}}
function addEditButtonsToMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function renderAgentList() {{}}
function markAgentUnread() {{}}
function setChatSubmittingForAgent() {{}}
function ensureChatState() {{ return chatState; }}
function clearWaitingForRuntimeEventsTimer() {{}}
function cancelAssistantTypewriter() {{}}
function appendPortalChatRuntimeEvent() {{}}
function ensureEventSocketForAgent() {{}}
async function handleAgentChatSuccess(agentId, requestCtx, payload) {{ successCalls.push(payload); chatState.currentRequest = null; }}
async function handleIncompleteChatStream(agentId, requestCtx, reason, payload) {{ incompleteCalls.push({{ reason, payload }}); requestCtx.completed = true; requestCtx.streamFailed = true; chatState.currentRequest = null; currentRequestCleared = true; }}
async function handleErrorResponse() {{ return "bad"; }}
let fetchQueue = [];
async function fetch() {{ return fetchQueue.shift(); }}
const chatState = {{ currentRequest: null, sessionId: "s1", inflightEventStream: null, needsReload: false }};
{_base_functions(js)}

(async () => {{
  // A: delta-only close
  successCalls = []; incompleteCalls = []; currentRequestCleared = false;
  const reqA = {{ clientRequestId: "rA", sessionIdAtSend: "s1", streamedText: "", streamEvents: [], runtimeEvents: [] }};
  chatState.currentRequest = reqA;
  fetchQueue.push({{
    status: 200, ok: true,
    body: {{
      getReader() {{
        const encoder = new TextEncoder();
        const seq = [{{ value: encoder.encode("event: delta\\ndata: {{\\\"delta\\\":\\\"I am fetching\\\"}}\\n\\n"), done: false }}, {{ done: true }}];
        let i = 0;
        return {{ read: async () => seq[i++] || {{ done: true }} }};
      }}
    }}
  }});
  await trySubmitChatStreamForSelectedAgent("agent-1", reqA, {{}});
  const caseA = {{ success: successCalls.length, incomplete: incompleteCalls.length, reason: incompleteCalls[0]?.reason || "", cleared: currentRequestCleared, active: chatState.currentRequest === reqA }};

  // B: final completed wins over streamedText
  successCalls = []; incompleteCalls = [];
  const reqB = {{ clientRequestId: "rB", sessionIdAtSend: "s1", streamedText: "I am fetching ...", streamEvents: [], runtimeEvents: [] }};
  chatState.currentRequest = reqB;
  await handleChatStreamEvent("agent-1", reqB, "final", {{ completion_state: "completed", response: "Agenda summary ...", session_id: "s1", request_id: "rB" }});
  const caseB = {{ success: successCalls.length, response: successCalls[0]?.response || "", incomplete: incompleteCalls.length }};

  // C: final non-success immediate incomplete
  successCalls = []; incompleteCalls = [];
  const reqC = {{ clientRequestId: "rC", sessionIdAtSend: "s1", streamedText: "", streamEvents: [], runtimeEvents: [] }};
  chatState.currentRequest = reqC;
  const resC = await handleChatStreamEvent("agent-1", reqC, "final", {{ ok: false, completion_state: "incomplete", response: "ended" }});
  const caseC = {{ result: resC, success: successCalls.length, incomplete: incompleteCalls.length }};

  // D/E: candidate legacy success + non-success
  successCalls = []; incompleteCalls = [];
  const reqD = {{ clientRequestId: "rD", sessionIdAtSend: "s1", streamedText: "", streamEvents: [], runtimeEvents: [], streamFinalCandidate: {{ response: "Legacy final" }} }};
  chatState.currentRequest = reqD;
  fetchQueue.push({{ status: 200, ok: true, body: {{ getReader() {{ return {{ read: async () => ({{ done: true }}) }}; }} }} }});
  await trySubmitChatStreamForSelectedAgent("agent-1", reqD, {{}});
  const caseD = {{ success: successCalls.length, response: successCalls[0]?.response || "", incomplete: incompleteCalls.length }};

  successCalls = []; incompleteCalls = [];
  const reqE = {{ clientRequestId: "rE", sessionIdAtSend: "s1", streamedText: "", streamEvents: [], runtimeEvents: [], streamFinalCandidate: {{ ok: false, completion_state: "blocked", response: "blocked" }} }};
  chatState.currentRequest = reqE;
  fetchQueue.push({{ status: 200, ok: true, body: {{ getReader() {{ return {{ read: async () => ({{ done: true }}) }}; }} }} }});
  await trySubmitChatStreamForSelectedAgent("agent-1", reqE, {{}});
  const caseE = {{ success: successCalls.length, incomplete: incompleteCalls.length }};

  successCalls = []; incompleteCalls = [];
  const reqH = {{ clientRequestId: "rH", sessionIdAtSend: "s1", streamedText: "", streamEvents: [], runtimeEvents: [] }};
  chatState.currentRequest = reqH;
  const resH = await handleChatStreamEvent("agent-1", reqH, "final", {{ ok: false, completion_state: "empty_final", response: "" }});
  const caseH = {{ result: resH, success: successCalls.length, incomplete: incompleteCalls.length }};

  console.log(JSON.stringify({{ caseA, caseB, caseC, caseD, caseE, caseH }}));
}})();
"""
    data = _run_node(script)
    assert data["caseA"]["success"] == 0
    assert data["caseA"]["incomplete"] == 1
    assert data["caseA"]["reason"] == "missing_final"
    assert data["caseA"]["cleared"] is True
    assert data["caseA"]["active"] is False

    assert data["caseB"]["success"] == 1
    assert data["caseB"]["response"] == "Agenda summary ..."
    assert data["caseB"]["incomplete"] == 0

    assert data["caseC"]["result"] == "final_non_success"
    assert data["caseC"]["success"] == 0
    assert data["caseC"]["incomplete"] == 1

    assert data["caseD"]["success"] == 1
    assert data["caseD"]["response"] == "Legacy final"
    assert data["caseD"]["incomplete"] == 0

    assert data["caseE"]["success"] == 0
    assert data["caseE"]["incomplete"] == 1
    assert data["caseH"]["result"] == "final_non_success"
    assert data["caseH"]["success"] == 0
    assert data["caseH"]["incomplete"] == 1


def test_non_stream_fallback_handles_ok_false_and_normalizes_message_response():
    js = _js_source()
    submit_fn = _extract_js_function(js, "submitChatForSelectedAgent")
    script = f"""
let successCalls = [];
let incompleteCalls = [];
const state = {{ selectedAgentId: "agent-1" }};
const dom = {{ chatInput: {{ value: "hi" }}, chatModelSelect: null, messageList: null }};
let chatState = {{
  pendingFiles: [],
  modelOverride: "",
  profileDefaultModel: "",
  currentRequest: null,
  sessionId: "s1",
  inflightEventStream: null,
}};
function ensureChatState() {{ return chatState; }}
function guardNoActiveChatRequestForAgent() {{ return true; }}
function buildAttachmentsFromChatState() {{ return []; }}
function ensureChatSessionId() {{ return "s1"; }}
function maybeRequestNotificationPermission() {{}}
function parseSkillSlashInput() {{ return null; }}
function findCachedSkillForSlash() {{ return null; }}
function removeWelcomeMessageIfPresent() {{}}
function removeTemporaryAssistantRows() {{}}
function hideSuggest() {{}}
function buildUserMessageArticle() {{ return ""; }}
function buildPendingAssistantArticle() {{ return ""; }}
function ensureEventSocketForAgent() {{}}
function isThinkingPanelActiveForAgent() {{ return false; }}
function renderThinkingPanelFromClientState() {{}}
function scrollToBottom() {{}}
function renderInputPreview() {{}}
function resetChatInputHeight() {{}}
function setChatStatus() {{}}
function setChatSubmittingForAgent() {{}}
function showToast() {{}}
const document = {{ getElementById() {{ return null; }} }};
function finalResponseText(payload) {{ return payload?.response || payload?.message || payload?.text || ""; }}
function getCompletionState(payload) {{ return String(payload?.completion_state || "").trim().toLowerCase(); }}
function isCompletedFinalPayload(payload) {{ const s = getCompletionState(payload); if (s) return s === "completed" || s === "success"; return typeof payload?.response === "string" && payload.response.length > 0; }}
function isNonSuccessFinalPayload(payload) {{ const s = getCompletionState(payload); return ["blocked","error","failed","incomplete","pending"].includes(s) || payload?.ok === false; }}
async function trySubmitChatStreamForSelectedAgent() {{ return "unsupported"; }}
async function handleAgentChatSuccess(agentId, ctx, payload) {{ successCalls.push(payload); }}
async function handleIncompleteChatStream(agentId, ctx, reason, payload) {{ incompleteCalls.push({{reason,payload}}); }}
function handleAgentChatFailure() {{}}
async function handleErrorResponse() {{ return "err"; }}
let fetchQueue = [];
async function fetch() {{ return fetchQueue.shift(); }}
{submit_fn}

(async () => {{
  fetchQueue.push({{ ok: true, json: async () => ({{ ok: false, completion_state: "error", response: "tool failed" }}) }});
  await submitChatForSelectedAgent();
  const caseF = {{ success: successCalls.length, incomplete: incompleteCalls.length }};

  successCalls = []; incompleteCalls = [];
  chatState.currentRequest = null;
  dom.chatInput.value = "hi again";
  fetchQueue.push({{ ok: true, json: async () => ({{ completion_state: "completed", message: "Agenda summary ..." }}) }});
  await submitChatForSelectedAgent();
  const caseG = {{ success: successCalls.length, response: successCalls[0]?.response || "", incomplete: incompleteCalls.length }};
  console.log(JSON.stringify({{ caseF, caseG }}));
}})();
"""
    data = _run_node(script)
    assert data["caseF"]["success"] == 0
    assert data["caseF"]["incomplete"] == 1
    assert data["caseG"]["success"] == 1
    assert data["caseG"]["response"] == "Agenda summary ..."
    assert data["caseG"]["incomplete"] == 0


def test_runtime_event_normalization_and_dedup_helpers_handle_live_timeline_events():
    js = _js_source()
    script = f"""
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
{_extract_js_function(js, "normalizeRuntimeEventTypeAlias")}
{_extract_js_function(js, "isTrackableStreamEvent")}
{_extract_js_function(js, "isCompletionRuntimeState")}
{_extract_js_function(js, "normalizeRuntimeEvent")}
{_extract_js_function(js, "runtimeEventSummaryHash")}
{_extract_js_function(js, "runtimeEventUniqueId")}
{_extract_js_function(js, "runtimeEventDedupKey")}
{_extract_js_function(js, "mergeRuntimeStreamEvents")}

const normalized = normalizeRuntimeEvent({{
  event_type: "message.delta",
  runtime_event_id: "runtime-1",
  created_at: "2026-05-18T12:00:00Z",
  detail_payload: {{ delta: "hello" }},
}});
const duplicateByRuntimeId = {{
  type: "message.delta",
  runtime_event_id: "runtime-1",
  created_at: "2026-05-18T12:00:01Z",
  summary: "different summary",
  data: {{ message: "different summary" }},
}};
const fallbackA = {{
  type: "provider.retry",
  created_at: "2026-05-18T12:00:02Z",
  summary: "retrying",
  data: {{ message: "retrying" }},
}};
const fallbackB = {{
  type: "provider.retry",
  created_at: "2026-05-18T12:00:02Z",
  summary: "retrying",
  data: {{ message: "retrying" }},
}};
const merged = mergeRuntimeStreamEvents([normalized, fallbackA], [duplicateByRuntimeId, fallbackB]);
console.log(JSON.stringify({{
  normalizedType: normalized.type,
  rawType: normalized.raw_type,
  runtimeEventId: normalized.runtime_event_id,
  trackableRetry: isTrackableStreamEvent("provider.retry"),
  trackableFinal: isTrackableStreamEvent("final"),
  keyById: runtimeEventDedupKey(normalized),
  fallbackKeysMatch: runtimeEventDedupKey(fallbackA) === runtimeEventDedupKey(fallbackB),
  mergedTypes: merged.map((event) => event.type),
  mergedCount: merged.length,
}}));
"""
    data = _run_node(script)
    assert data["normalizedType"] == "message.delta"
    assert data["rawType"] == "message.delta"
    assert data["runtimeEventId"] == "runtime-1"
    assert data["trackableRetry"] is True
    assert data["trackableFinal"] is True
    assert data["keyById"] == "id:runtime-1"
    assert data["fallbackKeysMatch"] is True
    assert data["mergedTypes"] == ["message.delta", "provider.retry"]
    assert data["mergedCount"] == 2
