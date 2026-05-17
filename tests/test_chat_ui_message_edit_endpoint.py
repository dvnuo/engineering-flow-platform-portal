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


def _run_message_edit_handler_with_payload(edit_payload: str, fallback_payload: str = "null") -> dict:
    handler = _extract_message_edit_submit_handler(_src())
    script = f"""
const events = {{}};
const elements = {{
  "message-edit-form": {{
    dataset: {{}},
    addEventListener(type, callback) {{ events[type] = callback; }},
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
const state = {{ selectedAgentId: "agent-1" }};
const chatState = {{ modelOverride: "" }};
const fetchCalls = [];
const agentApiCalls = [];
const renderCalls = [];
const submitCalls = [];
const truncateCalls = [];
const chatSubmittingValues = [];
let status = [];
let toast = [];
let closed = false;
let updatedSession = null;
let lastSession = null;
let controlsSynced = 0;
let editPayload = {edit_payload};
let fallbackPayload = {fallback_payload};

function makeJsonResponse(payload, ok = true, status = 200) {{
  return {{
    ok,
    status,
    headers: {{ get() {{ return "application/json"; }} }},
    clone() {{ return makeJsonResponse(payload, ok, status); }},
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
function beginSingleSubmit() {{ return true; }}
function endSingleSubmit() {{}}
function setChatStatus(value, isError = false) {{ status.push({{ value, isError }}); }}
function setChatSubmittingForAgent(agentId, active) {{
  if (agentId !== "agent-1") throw new Error("unexpected submitting agent " + agentId);
  chatSubmittingValues.push(active);
}}
async function fetch(url, options) {{
  fetchCalls.push({{ url, options, body: JSON.parse(options.body) }});
  return makeJsonResponse(editPayload);
}}
function closeEditMessageModal() {{ closed = true; }}
function updateAgentSession(agentId, sessionId) {{ updatedSession = {{ agentId, sessionId }}; }}
function setLastSessionId(agentId, sessionId) {{ lastSession = {{ agentId, sessionId }}; }}
function renderChatHistory(messages, metadata = {{}}) {{ renderCalls.push({{ messages, metadata }}); }}
async function agentApiFor(agentId, path) {{
  agentApiCalls.push({{ agentId, path }});
  return fallbackPayload;
}}
function addEditButtonsToMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function showToast(value) {{ toast.push(value); }}
async function handleErrorResponse() {{ return "handled error"; }}
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
    agentApiCalls,
    renderCalls,
    submitCalls,
    truncateCalls,
    chatSubmittingValues,
    status,
    toast,
    closed,
    updatedSession,
    lastSession,
    hiddenSessionValue: elements["chat-session-id"].value,
    modalAriaHidden: elements["message-edit-modal"]["aria-hidden"],
    controlsSynced,
  }}));
}})();
"""
    return _run_node(script)


def test_message_edit_handler_uses_runtime_edit_endpoint():
    handler = _extract_message_edit_submit_handler(_src())

    assert "/messages/${encodeURIComponent(messageId)}/edit" in handler
    assert 'method: "POST"' in handler
    assert '"Content-Type": "application/json"' in handler
    assert "content: newContent" in handler
    assert "delete-from-here" not in handler
    assert "truncateDomFromUserArticle" not in handler
    assert "dom.chatInput.value = newContent" not in handler
    assert "submitChatForSelectedAgent()" not in handler


def test_message_edit_handler_does_not_replace_regular_submit_flow():
    source = _src()
    submit = _extract_js_function(source, "submitChatForSelectedAgent")
    handler = _extract_message_edit_submit_handler(source)

    assert "async function submitChatForSelectedAgent()" in submit
    assert "submitChatForSelectedAgent()" not in handler


def test_message_edit_handler_renders_runtime_source_of_truth_messages():
    handler = _extract_message_edit_submit_handler(_src())

    assert "Array.isArray(result.messages)" in handler
    assert "renderChatHistory(result.messages)" in handler
    assert "updateAgentSession(agentId, finalSessionId)" in handler
    assert "setLastSessionId(agentId, finalSessionId)" in handler
    assert "agentApiFor(agentId, `/api/sessions/${encodeURIComponent(finalSessionId)}`)" in handler


def test_message_edit_handler_runtime_success_renders_four_returned_messages():
    returned_messages = [
        {"role": "user", "content": "hi", "id": "u-1"},
        {"role": "assistant", "content": "hi, how can i help", "id": "a-1"},
        {"role": "user", "content": "how are u??", "id": "u-2b"},
        {"role": "assistant", "content": "doing well，how can i help?", "id": "a-2b"},
    ]
    data = _run_message_edit_handler_with_payload(
        json.dumps({"success": True, "session_id": "s1", "messages": returned_messages})
    )

    assert len(data["fetchCalls"]) == 1
    fetch_call = data["fetchCalls"][0]
    assert fetch_call["url"] == "/a/agent-1/api/sessions/s1/messages/u-2/edit"
    assert fetch_call["options"]["method"] == "POST"
    assert fetch_call["body"] == {"content": "how are u??"}

    assert data["agentApiCalls"] == []
    assert data["submitCalls"] == []
    assert data["truncateCalls"] == []
    assert len(data["renderCalls"]) == 1
    rendered = data["renderCalls"][0]["messages"]
    assert rendered == returned_messages
    assert len(rendered) == 4
    assert rendered[1] == {"role": "assistant", "content": "hi, how can i help", "id": "a-1"}
    assert data["closed"] is True
    assert data["updatedSession"] == {"agentId": "agent-1", "sessionId": "s1"}
    assert data["lastSession"] == {"agentId": "agent-1", "sessionId": "s1"}
    assert data["hiddenSessionValue"] == "s1"
    assert data["modalAriaHidden"] == "true"
    assert data["chatSubmittingValues"] == [True, False]


def test_message_edit_handler_fallback_reload_renders_session_messages():
    fallback_messages = [
        {"role": "user", "content": "hi", "id": "u-1"},
        {"role": "assistant", "content": "hi, how can i help", "id": "a-1"},
        {"role": "user", "content": "how are u??", "id": "u-2b"},
        {"role": "assistant", "content": "doing well，how can i help?", "id": "a-2b"},
    ]
    data = _run_message_edit_handler_with_payload(
        json.dumps({"success": True, "session_id": "s1"}),
        json.dumps({"messages": fallback_messages, "metadata": {"source": "fallback"}}),
    )

    assert len(data["fetchCalls"]) == 1
    assert data["fetchCalls"][0]["url"] == "/a/agent-1/api/sessions/s1/messages/u-2/edit"
    assert data["fetchCalls"][0]["body"] == {"content": "how are u??"}
    assert data["agentApiCalls"] == [{"agentId": "agent-1", "path": "/api/sessions/s1"}]
    assert data["submitCalls"] == []
    assert data["truncateCalls"] == []
    assert len(data["renderCalls"]) == 1
    assert data["renderCalls"][0]["messages"] == fallback_messages
    assert data["renderCalls"][0]["metadata"] == {"source": "fallback"}
    assert data["renderCalls"][0]["messages"][1]["content"] == "hi, how can i help"
    assert data["chatSubmittingValues"] == [True, False]
