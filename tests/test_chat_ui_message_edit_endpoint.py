import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src():
    return SRC.read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def _scan_to_matching(text: str, index: int, open_char: str, close_char: str) -> int:
    depth = 0
    i = index
    in_single = False
    in_double = False
    in_template = False
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_single:
            if char == "\\":
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue
        if in_template:
            if char == "\\":
                i += 2
                continue
            if char == "`":
                in_template = False
            i += 1
            continue
        if char == "/" and nxt == "/":
            nl = text.find("\n", i + 2)
            i = len(text) if nl == -1 else nl + 1
            continue
        if char == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                raise AssertionError("Unable to parse message edit handler; unterminated block comment")
            i = end + 2
            continue
        if char == "'":
            in_single = True
            i += 1
            continue
        if char == '"':
            in_double = True
            i += 1
            continue
        if char == "`":
            in_template = True
            i += 1
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"Unable to parse message edit handler; unmatched {open_char}{close_char}")


def _extract_message_edit_submit_handler(js_text: str) -> str:
    marker = 'document.getElementById("message-edit-form")?.addEventListener("submit"'
    start = js_text.find(marker)
    if start < 0:
        raise AssertionError("Unable to find message edit form submit handler")
    callback_start = js_text.find("=>", start)
    if callback_start < 0:
        raise AssertionError("Unable to find message edit submit callback")
    body_start = js_text.find("{", callback_start)
    if body_start < 0:
        raise AssertionError("Unable to find message edit submit callback body")
    body_end = _scan_to_matching(js_text, body_start, "{", "}")
    return js_text[start:body_end + 1]


def _run_message_edit_handler_with_payload(
    edit_payload: str,
    *,
    ok: bool = True,
    status_code: int = 200,
) -> dict:
    handler = _extract_message_edit_submit_handler(_src())
    script = f"""
const events = {{}};
const optimisticArticles = [];
const appendedHtml = [];
const elements = {{
  "message-edit-form": {{
    dataset: {{}},
    addEventListener(type, callback) {{ events[type] = callback; }},
    removeAttribute(name) {{ delete this[name]; }},
    setAttribute(name, value) {{ this[name] = value; }},
  }},
  "edit-message-id": {{ value: "u-2" }},
  "edit-message-content": {{ value: "how are u??" }},
  "chat-session-id": {{ value: "s1" }},
  "message-edit-modal": {{
    hidden: false,
    classList: {{ add(name) {{ this.lastAdded = name; }} }},
    setAttribute(name, value) {{ this[name] = value; }},
  }},
  "close-message-edit-modal": {{ disabled: false }},
}};
const document = {{
  getElementById(id) {{ return elements[id] || null; }},
}};
const dom = {{
  chatModelSelect: {{ value: "" }},
  messageList: {{
    insertAdjacentHTML(_position, html) {{
      appendedHtml.push(html);
      if (html.includes("message-surface-user")) {{
        optimisticArticles.push({{
          dataset: {{ localUser: "1", optimisticUser: "1" }},
          closest() {{ return null; }},
        }});
      }}
    }},
  }},
}};
const state = {{ selectedAgentId: "agent-1" }};
const chatState = {{ sessionId: "s1", modelOverride: "" }};
const fetchCalls = [];
const renderCalls = [];
const submitCalls = [];
const truncateCalls = [];
const chatSubmittingValues = [];
const beginCalls = [];
const endCalls = [];
const eventSocketCalls = [];
const pollCalls = [];
let status = [];
let toast = [];
let closed = false;
let updatedSession = null;
let lastSession = null;
let controlsSynced = 0;
let editPayload = {edit_payload};

Object.defineProperty(globalThis, "crypto", {{
  value: {{ randomUUID() {{ return "req-client-1"; }} }},
  configurable: true,
}});

function makeJsonResponse(payload, responseOk = true, responseStatus = 200) {{
  return {{
    ok: responseOk,
    status: responseStatus,
    headers: {{ get() {{ return "application/json"; }} }},
    clone() {{ return makeJsonResponse(payload, responseOk, responseStatus); }},
    async json() {{ return payload; }},
    async text() {{ return JSON.stringify(payload); }},
  }};
}}
function getChatState(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected agent id " + agentId);
  return chatState;
}}
function guardNoActiveChatRequestForAgent(agentId, actionLabel) {{
  if (agentId !== "agent-1") throw new Error("unexpected guard agent " + agentId);
  if (actionLabel !== "edit a message") throw new Error("unexpected guard label " + actionLabel);
  return true;
}}
function beginSingleSubmit(_form, options) {{ beginCalls.push(options); return true; }}
function endSingleSubmit(_form, options) {{ endCalls.push(options); }}
function setChatStatus(value, isError = false) {{ status.push({{ value, isError }}); }}
function setChatSubmittingForAgent(agentId, active) {{
  if (agentId !== "agent-1") throw new Error("unexpected submitting agent " + agentId);
  chatSubmittingValues.push(active);
  chatState.isSubmitting = active;
}}
async function fetch(url, options) {{
  fetchCalls.push({{ url, options, body: JSON.parse(options.body) }});
  return makeJsonResponse(editPayload, {str(ok).lower()}, {status_code});
}}
function closeEditMessageModal() {{ closed = true; }}
function updateAgentSession(agentId, sessionId) {{ updatedSession = {{ agentId, sessionId }}; chatState.sessionId = sessionId; }}
function setLastSessionId(agentId, sessionId) {{ lastSession = {{ agentId, sessionId }}; }}
function renderChatHistory(messages, metadata = {{}}) {{ renderCalls.push({{ messages, metadata }}); }}
function removeWelcomeMessageIfPresent() {{}}
function buildUserMessageArticle(text) {{ return `<div class="message-row-user"><article class="message-surface-user" data-local-user="1" data-optimistic-user="1">${{text}}</article></div>`; }}
function buildPendingAssistantArticle(requestId, text) {{ return `<div class="message-row-assistant"><article class="pending-assistant" data-client-request-id="${{requestId}}">${{text}}</article></div>`; }}
function getLatestOptimisticUserArticle() {{ return optimisticArticles[optimisticArticles.length - 1] || null; }}
function addEditButtonsToMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function ensureEventSocketForAgent(agentId, sessionId, requestId) {{ eventSocketCalls.push({{ agentId, sessionId, requestId }}); }}
function pollEditedSessionUntilComplete(agentId, sessionId, requestId, replacementUserMessageId, options) {{ pollCalls.push({{ agentId, sessionId, requestId, replacementUserMessageId, hasRequestCtx: !!options.requestCtx }}); }}
function showToast(value) {{ toast.push(value); }}
async function handleErrorResponse() {{ return "handled error"; }}
function getRuntimeMutationErrorMessage(_response, result, fallbackMessage) {{ return result?.detail || result?.error || fallbackMessage; }}
function handleEditedRegenerationFailure(_agentId, _requestCtx, message) {{ toast.push("post-accepted failure: " + message); }}
async function submitChatForSelectedAgent() {{
  submitCalls.push(true);
  throw new Error("submitChatForSelectedAgent should not be called by edit handler");
}}
function truncateDomFromUserArticle() {{
  truncateCalls.push(true);
  throw new Error("truncateDomFromUserArticle should not be called by edit handler");
}}
function syncSelectedAgentChatActionControls() {{ controlsSynced += 1; }}

{handler});

(async () => {{
  await events.submit({{ preventDefault() {{}}, currentTarget: elements["message-edit-form"] }});
  console.log(JSON.stringify({{
    fetchCalls,
    renderCalls,
    submitCalls,
    truncateCalls,
    chatSubmittingValues,
    beginCalls,
    endCalls,
    status,
    toast,
    closed,
    updatedSession,
    lastSession,
    hiddenSessionValue: elements["chat-session-id"].value,
    modalAriaHidden: elements["message-edit-modal"]["aria-hidden"] || null,
    controlsSynced,
    appendedHtml,
    optimisticMessageId: optimisticArticles[0]?.dataset?.messageId || "",
    eventSocketCalls,
    pollCalls,
    currentRequest: chatState.currentRequest || null,
    inflightThinking: chatState.inflightThinking || null,
  }}));
}})();
"""
    return _run_node(script)


def _extract_edit_poll_helpers(source: str) -> str:
    helper_names = [
        "getRuntimeMessageId",
        "isRenderableAssistantSessionMessage",
        "sessionMessageRequestId",
        "findAssistantAfterEditedUserMessage",
        "getEditedSessionFailureMessage",
        "shouldRenderEditedSessionForAgent",
        "completeEditedMessageRequest",
        "handleEditedRegenerationFailure",
        "finalizeEditedSessionMessages",
        "pollEditedSessionUntilComplete",
    ]
    return "\n".join(_extract_js_function(source, name) for name in helper_names)


def test_message_edit_handler_uses_async_endpoint():
    handler = _extract_message_edit_submit_handler(_src())

    assert "/messages/${encodeURIComponent(messageId)}/edit/async" in handler
    assert 'method: "POST"' in handler
    assert '"Content-Type": "application/json"' in handler
    assert "request_id" in handler
    assert "content: newContent" in handler
    assert "delete-from-here" not in handler
    assert "truncateDomFromUserArticle" not in handler
    assert "submitChatForSelectedAgent()" not in handler
    assert 'pendingText: "Editing..."' not in handler


def test_message_edit_handler_does_not_replace_regular_submit_flow():
    source = _src()
    submit = _extract_js_function(source, "submitChatForSelectedAgent")
    handler = _extract_message_edit_submit_handler(source)

    assert "async function submitChatForSelectedAgent()" in submit
    assert "submitChatForSelectedAgent()" not in handler


def test_accepted_response_closes_modal_before_llm_final_and_appends_pending_ui():
    prefix_messages = [
        {"role": "user", "content": "hi", "id": "u-1"},
        {"role": "assistant", "content": "hi, how can i help", "id": "a-1"},
    ]
    data = _run_message_edit_handler_with_payload(
        json.dumps({
            "success": True,
            "accepted": True,
            "async": True,
            "completion_state": "pending",
            "session_id": "s1",
            "request_id": "req-edit-1",
            "replacement_user_message_id": "u-2b",
            "messages": prefix_messages,
        })
    )

    assert len(data["fetchCalls"]) == 1
    fetch_call = data["fetchCalls"][0]
    assert fetch_call["url"] == "/a/agent-1/api/sessions/s1/messages/u-2/edit/async"
    assert fetch_call["options"]["method"] == "POST"
    assert fetch_call["body"]["content"] == "how are u??"
    assert fetch_call["body"]["request_id"] == "req-client-1"

    assert data["closed"] is True
    assert data["modalAriaHidden"] == "true"
    assert data["renderCalls"] == [{"messages": prefix_messages, "metadata": {}}]
    assert any("how are u??" in html for html in data["appendedHtml"])
    assert any("Regenerating response..." in html for html in data["appendedHtml"])
    assert data["optimisticMessageId"] == "u-2b"
    assert data["chatSubmittingValues"] == [True]
    assert data["status"][-1] == {"value": "Regenerating response...", "isError": False}
    assert data["submitCalls"] == []
    assert data["truncateCalls"] == []
    assert data["eventSocketCalls"] == [{"agentId": "agent-1", "sessionId": "s1", "requestId": "req-edit-1"}]
    assert data["pollCalls"] == [{
        "agentId": "agent-1",
        "sessionId": "s1",
        "requestId": "req-edit-1",
        "replacementUserMessageId": "u-2b",
        "hasRequestCtx": True,
    }]
    assert data["currentRequest"]["clientRequestId"] == "req-edit-1"
    assert data["currentRequest"]["edit"] is True
    assert data["inflightThinking"]["completed"] is False
    assert data["beginCalls"][0]["pendingText"] == "Saving..."
    assert len(data["endCalls"]) == 1


def test_accepted_before_completion_does_not_require_full_assistant_messages():
    data = _run_message_edit_handler_with_payload(
        json.dumps({
            "success": True,
            "accepted": True,
            "async": True,
            "completion_state": "pending",
            "session_id": "s1",
            "request_id": "req-edit-1",
            "replacement_user_message_id": "u-2b",
            "messages": [
                {"role": "user", "content": "hi", "id": "u-1"},
                {"role": "assistant", "content": "hi, how can i help", "id": "a-1"},
            ],
        })
    )

    assert data["closed"] is True
    assert data["modalAriaHidden"] == "true"
    assert len(data["renderCalls"][0]["messages"]) == 2
    assert any("Regenerating response..." in html for html in data["appendedHtml"])
    assert data["chatSubmittingValues"] == [True]


def test_accepted_then_polling_completion_renders_final_messages_and_clears_busy():
    source = _src()
    helpers = _extract_edit_poll_helpers(source)
    final_messages = [
        {"role": "user", "content": "hi", "id": "u-1"},
        {"role": "assistant", "content": "hi how can i help", "id": "a-1"},
        {"role": "user", "content": "how are u??", "id": "u-2b"},
        {"role": "assistant", "content": "doing well，how can i help?", "id": "a-2b"},
    ]
    script = f"""
const EDITED_MESSAGE_POLL_INTERVAL_MS = 2000;
const EDITED_MESSAGE_POLL_TIMEOUT_MS = 10 * 60 * 1000;
const state = {{ selectedAgentId: "agent-1" }};
const chatState = {{
  sessionId: "s1",
  currentRequest: {{ clientRequestId: "req-edit-1" }},
  inflightThinking: {{
    id: "req-edit-1",
    requestId: "req-edit-1",
    sessionId: "s1",
    completed: false,
    events: [],
  }},
}};
const agentApiCalls = [];
const renderCalls = [];
const chatSubmittingValues = [];
let status = [];
let controlsSynced = 0;
let editButtons = 0;
let icons = 0;
let scrolls = 0;

function ensureChatState(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected agent " + agentId);
  return chatState;
}}
function currentSessionIdForAgent(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected current session agent " + agentId);
  return chatState.sessionId;
}}
async function agentApiFor(agentId, path) {{
  agentApiCalls.push({{ agentId, path }});
  return {{ messages: {json.dumps(final_messages)}, metadata: {{ source: "poll" }} }};
}}
function renderChatHistory(messages, metadata = {{}}) {{ renderCalls.push({{ messages, metadata }}); }}
function addEditButtonsToMessages() {{ editButtons += 1; }}
function renderIcons() {{ icons += 1; }}
function scrollToBottom() {{ scrolls += 1; }}
function setChatStatus(value, isError = false) {{ status.push({{ value, isError }}); }}
function setChatSubmittingForAgent(agentId, active) {{
  if (agentId !== "agent-1") throw new Error("unexpected submitting agent " + agentId);
  chatSubmittingValues.push(active);
  chatState.isSubmitting = active;
}}
function syncSelectedAgentChatActionControls() {{ controlsSynced += 1; }}
function finalizeIncompleteAssistantRow() {{}}
function showToast() {{}}

{helpers}

(async () => {{
  await pollEditedSessionUntilComplete("agent-1", "s1", "req-edit-1", "u-2b", {{
    intervalMs: 1,
    timeoutMs: 1000,
    requestCtx: {{
      requestId: "req-edit-1",
      clientRequestId: "req-edit-1",
      sessionIdAtSend: "s1",
      edit: true,
    }},
  }});
  console.log(JSON.stringify({{
    agentApiCalls,
    renderCalls,
    chatSubmittingValues,
    status,
    currentRequestCleared: chatState.currentRequest === null,
    inflightThinkingCleared: chatState.inflightThinking === null,
    lastThinkingCompleted: chatState.lastThinkingSnapshot?.completed || false,
    controlsSynced,
    editButtons,
    icons,
    scrolls,
  }}));
}})();
"""
    data = _run_node(script)

    assert data["agentApiCalls"] == [{"agentId": "agent-1", "path": "/api/sessions/s1"}]
    assert len(data["renderCalls"]) == 1
    rendered = data["renderCalls"][0]["messages"]
    assert rendered == final_messages
    assert rendered[1] == {"role": "assistant", "content": "hi how can i help", "id": "a-1"}
    assert data["renderCalls"][0]["metadata"] == {"source": "poll"}
    assert data["chatSubmittingValues"] == [False]
    assert data["currentRequestCleared"] is True
    assert data["inflightThinkingCleared"] is True
    assert data["lastThinkingCompleted"] is True
    assert data["status"] == [{"value": "Ready", "isError": False}]
    assert data["editButtons"] == 1
    assert data["icons"] == 1
    assert data["scrolls"] == 1


def test_get_edited_session_failure_message_recognizes_edit_failed():
    helper = _extract_js_function(_src(), "getEditedSessionFailureMessage")
    direct_payload = {
        "messages": [
            {"role": "user", "content": "hi", "id": "u-1"},
            {"role": "assistant", "content": "hi", "id": "a-1"},
        ],
        "metadata": {
            "latest_event_type": "edit.failed",
            "latest_event_state": "error",
            "completion_state": "error",
            "request_id": "req-edit-1",
            "error": "simulated resend failure",
        },
    }
    runtime_event_payload = {
        "messages": [
            {"role": "user", "content": "hi", "id": "u-1"},
            {"role": "assistant", "content": "hi", "id": "a-1"},
        ],
        "metadata": {
            "runtime_events": [
                {
                    "type": "edit.failed",
                    "event_type": "edit.failed",
                    "state": "error",
                    "data": {"error": "background failed"},
                }
            ],
        },
    }
    script = f"""
{helper}
console.log(JSON.stringify({{
  direct: getEditedSessionFailureMessage({json.dumps(direct_payload)}),
  runtimeEvent: getEditedSessionFailureMessage({json.dumps(runtime_event_payload)}),
}}));
"""
    data = _run_node(script)

    assert data["direct"] == "simulated resend failure"
    assert data["runtimeEvent"] == "background failed"


def test_polling_sees_edit_failed_and_clears_busy_without_rendering_final_messages():
    source = _src()
    helpers = _extract_edit_poll_helpers(source)
    prefix_messages = [
        {"role": "user", "content": "hi", "id": "u-1"},
        {"role": "assistant", "content": "hi", "id": "a-1"},
        {"role": "user", "content": "how are u??", "id": "u-2b"},
    ]
    failure_payload = {
        "messages": prefix_messages,
        "metadata": {
            "latest_event_type": "edit.failed",
            "latest_event_state": "error",
            "completion_state": "error",
            "request_id": "req-edit-1",
            "error": "simulated resend failure",
        },
    }
    script = f"""
const EDITED_MESSAGE_POLL_INTERVAL_MS = 2000;
const EDITED_MESSAGE_POLL_TIMEOUT_MS = 10 * 60 * 1000;
const state = {{ selectedAgentId: "agent-1" }};
const chatState = {{
  sessionId: "s1",
  currentRequest: {{ clientRequestId: "req-edit-1", edit: true }},
  inflightThinking: {{
    id: "req-edit-1",
    requestId: "req-edit-1",
    sessionId: "s1",
    completed: false,
    events: [],
  }},
}};
const agentApiCalls = [];
const renderCalls = [];
const chatSubmittingValues = [];
const finalizerCalls = [];
let status = [];
let toast = [];
let controlsSynced = 0;
let editButtons = 0;
let icons = 0;
let scrolls = 0;

function ensureChatState(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected agent " + agentId);
  return chatState;
}}
function currentSessionIdForAgent(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected current session agent " + agentId);
  return chatState.sessionId;
}}
async function agentApiFor(agentId, path) {{
  agentApiCalls.push({{ agentId, path }});
  return {json.dumps(failure_payload)};
}}
function renderChatHistory(messages, metadata = {{}}) {{ renderCalls.push({{ messages, metadata }}); }}
function addEditButtonsToMessages() {{ editButtons += 1; }}
function renderIcons() {{ icons += 1; }}
function scrollToBottom() {{ scrolls += 1; }}
function setChatStatus(value, isError = false) {{ status.push({{ value, isError }}); }}
function setChatSubmittingForAgent(agentId, active) {{
  if (agentId !== "agent-1") throw new Error("unexpected submitting agent " + agentId);
  chatSubmittingValues.push(active);
  chatState.isSubmitting = active;
}}
function syncSelectedAgentChatActionControls() {{ controlsSynced += 1; }}
function finalizeIncompleteAssistantRow(agentId, requestCtx, payload) {{ finalizerCalls.push({{ agentId, requestId: requestCtx.clientRequestId, payload }}); }}
function showToast(value) {{ toast.push(value); }}

{helpers}

(async () => {{
  await pollEditedSessionUntilComplete("agent-1", "s1", "req-edit-1", "u-2b", {{
    intervalMs: 1,
    timeoutMs: 1000,
    requestCtx: {{
      requestId: "req-edit-1",
      clientRequestId: "req-edit-1",
      sessionIdAtSend: "s1",
      edit: true,
    }},
  }});
  console.log(JSON.stringify({{
    agentApiCalls,
    renderCalls,
    chatSubmittingValues,
    finalizerCalls,
    status,
    toast,
    currentRequestCleared: chatState.currentRequest === null,
    inflightThinkingCleared: chatState.inflightThinking === null,
    lastThinkingCompleted: chatState.lastThinkingSnapshot?.completed || false,
    controlsSynced,
    editButtons,
    icons,
    scrolls,
  }}));
}})();
"""
    data = _run_node(script)

    assert data["agentApiCalls"] == [{"agentId": "agent-1", "path": "/api/sessions/s1"}]
    assert data["renderCalls"] == []
    assert data["chatSubmittingValues"] == [False]
    assert len(data["finalizerCalls"]) == 1
    assert data["currentRequestCleared"] is True
    assert data["inflightThinkingCleared"] is True
    assert data["lastThinkingCompleted"] is True
    assert data["status"][-1] == {
        "value": "Edited message was saved, but regeneration failed: simulated resend failure",
        "isError": True,
    }
    assert data["toast"] == ["Edited message was saved, but regeneration failed: simulated resend failure"]
    assert data["editButtons"] == 1
    assert data["icons"] == 1
    assert data["scrolls"] == 1


def test_polling_timeout_clears_busy_and_marks_session_for_reload():
    source = _src()
    helpers = _extract_edit_poll_helpers(source)
    prefix_payload = {
        "messages": [
            {"role": "user", "content": "hi", "id": "u-1"},
            {"role": "assistant", "content": "hi", "id": "a-1"},
            {"role": "user", "content": "how are u??", "id": "u-2b"},
        ],
        "metadata": {"request_id": "req-edit-1"},
    }
    script = f"""
const EDITED_MESSAGE_POLL_INTERVAL_MS = 2000;
const EDITED_MESSAGE_POLL_TIMEOUT_MS = 10 * 60 * 1000;
const state = {{ selectedAgentId: "agent-1" }};
const chatState = {{
  sessionId: "s1",
  currentRequest: {{ clientRequestId: "req-edit-1", edit: true }},
  inflightThinking: {{
    id: "req-edit-1",
    requestId: "req-edit-1",
    sessionId: "s1",
    completed: false,
    events: [],
  }},
  needsReload: false,
}};
const agentApiCalls = [];
const renderCalls = [];
const chatSubmittingValues = [];
const finalizerCalls = [];
let status = [];
let controlsSynced = 0;
let editButtons = 0;
let icons = 0;
let scrolls = 0;

function ensureChatState(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected agent " + agentId);
  return chatState;
}}
function currentSessionIdForAgent(agentId) {{
  if (agentId !== "agent-1") throw new Error("unexpected current session agent " + agentId);
  return chatState.sessionId;
}}
async function agentApiFor(agentId, path) {{
  agentApiCalls.push({{ agentId, path }});
  return {json.dumps(prefix_payload)};
}}
function renderChatHistory(messages, metadata = {{}}) {{ renderCalls.push({{ messages, metadata }}); }}
function addEditButtonsToMessages() {{ editButtons += 1; }}
function renderIcons() {{ icons += 1; }}
function scrollToBottom() {{ scrolls += 1; }}
function setChatStatus(value, isError = false) {{ status.push({{ value, isError }}); }}
function setChatSubmittingForAgent(agentId, active) {{
  if (agentId !== "agent-1") throw new Error("unexpected submitting agent " + agentId);
  chatSubmittingValues.push(active);
  chatState.isSubmitting = active;
}}
function syncSelectedAgentChatActionControls() {{ controlsSynced += 1; }}
function finalizeIncompleteAssistantRow(agentId, requestCtx, payload) {{ finalizerCalls.push({{ agentId, requestId: requestCtx.clientRequestId, payload }}); }}
function showToast() {{}}

{helpers}

(async () => {{
  await pollEditedSessionUntilComplete("agent-1", "s1", "req-edit-1", "u-2b", {{
    intervalMs: 1,
    timeoutMs: 5,
    requestCtx: {{
      requestId: "req-edit-1",
      clientRequestId: "req-edit-1",
      sessionIdAtSend: "s1",
      edit: true,
    }},
  }});
  console.log(JSON.stringify({{
    agentApiCallCount: agentApiCalls.length,
    renderCalls,
    chatSubmittingValues,
    finalizerCalls,
    status,
    currentRequestCleared: chatState.currentRequest === null,
    inflightThinkingCleared: chatState.inflightThinking === null,
    lastThinkingCompleted: chatState.lastThinkingSnapshot?.completed || false,
    needsReload: chatState.needsReload,
    controlsSynced,
    editButtons,
    icons,
    scrolls,
  }}));
}})();
"""
    data = _run_node(script)

    assert data["agentApiCallCount"] >= 1
    assert data["renderCalls"] == []
    assert data["chatSubmittingValues"] == [False]
    assert len(data["finalizerCalls"]) == 1
    assert data["currentRequestCleared"] is True
    assert data["inflightThinkingCleared"] is True
    assert data["lastThinkingCompleted"] is True
    assert data["needsReload"] is True
    assert data["status"][-1] == {
        "value": "Regeneration is still running or timed out; refresh the session to check the latest result.",
        "isError": True,
    }
    assert "refresh" in data["status"][-1]["value"]
    assert "latest" in data["status"][-1]["value"]
    assert data["editButtons"] == 1
    assert data["icons"] == 1
    assert data["scrolls"] == 1


def test_trackable_thinking_events_include_edit_failed():
    body = _extract_js_function(_src(), "isTrackableThinkingEvent")
    script = f"""
{body}
console.log(JSON.stringify({{ editFailed: isTrackableThinkingEvent("edit.failed") }}));
"""
    data = _run_node(script)

    assert data["editFailed"] is True


def test_edit_async_pre_accepted_failure_keeps_modal_and_does_not_touch_dom():
    data = _run_message_edit_handler_with_payload(
        json.dumps({"success": False, "detail": "conflict"}),
        ok=False,
        status_code=409,
    )

    assert len(data["fetchCalls"]) == 1
    assert data["fetchCalls"][0]["url"] == "/a/agent-1/api/sessions/s1/messages/u-2/edit/async"
    assert data["closed"] is False
    assert data["modalAriaHidden"] is None
    assert data["renderCalls"] == []
    assert data["appendedHtml"] == []
    assert data["chatSubmittingValues"] == [False]
    assert data["toast"] == ["conflict"]
    assert data["status"][-1] == {"value": "conflict", "isError": True}
    assert data["submitCalls"] == []
    assert data["truncateCalls"] == []
