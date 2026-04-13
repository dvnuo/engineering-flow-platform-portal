"""Tests for web.py - settings and config."""
import json
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


def test_agent_settings_panel():
    """Test agent settings panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/settings/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_settings_save():
    """Test agent settings save."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/app/agents/agent-123/settings/save", 
                         json={"llm": {"provider": "openai"}})
    assert response.status_code in [200, 302, 400, 401, 403, 404]


def test_agent_files_panel():
    """Test agent files panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/files/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_sessions_panel():
    """Test agent sessions panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/sessions/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_skills_panel():
    """Test agent skills panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/skills/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_usage_panel():
    """Test agent usage panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/usage/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_users_panel():
    """Test users panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code in [200, 302, 401, 403]


def test_proxy_agents_usage():
    """Test canonical agents usage proxy route."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/usage")
    assert response.status_code in [401, 403, 404, 409, 502]


def test_proxy_agent_api():
    """Test proxy to agent API."""
    from app.main import app
    client = TestClient(app)
    # Test proxy endpoint
    response = client.post("/a/agent-123/api/chat", 
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def test_proxy_agent_files_list():
    """Test proxy to agent files list."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/files")
    assert response.status_code in [401, 403, 404, 500]


def test_proxy_agent_events():
    """Test proxy to agent events."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/events")
    assert response.status_code in [400, 401, 403, 404]


def test_agent_runtime_destroy():
    """Test agent runtime destroy."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/destroy")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_runtime_delete():
    """Test agent runtime delete."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/delete-runtime")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_defaults():
    """Test agent defaults endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/defaults")
    assert response.status_code in [200, 401, 403]


def test_proxy_api_chat_stream():
    """Test proxy chat stream endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/a/agent-123/api/chat/stream",
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def test_chat_ui_includes_display_block_renderer_helpers():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function parseDisplayBlocks(" in js_source
    assert "function renderDisplayBlocksToHtml(" in js_source
    assert "function renderSingleDisplayBlock(" in js_source
    assert "function renderCodeBlock(" in js_source
    assert "function renderTableBlock(" in js_source
    assert "function enhanceMarkdownBlock(" in js_source


def _extract_js_helper_block(js_text: str, helper_name: str) -> str:
    start_marker = f"// RUNTIME_EVENT_HELPER_START: {helper_name}"
    end_marker = f"// RUNTIME_EVENT_HELPER_END: {helper_name}"
    start = js_text.find(start_marker)
    if start < 0:
        raise AssertionError(f"Unable to find start marker for {helper_name} in chat_ui.js")
    end = js_text.find(end_marker, start)
    if end < 0:
        raise AssertionError(f"Unable to find end marker for {helper_name} in chat_ui.js")
    return js_text[start + len(start_marker):end].strip()


def _extract_js_function(js_text: str, function_name: str) -> str:
    markers = [f"async function {function_name}(", f"function {function_name}("]
    start = -1
    for marker in markers:
        start = js_text.find(marker)
        if start >= 0:
            break
    if start < 0:
        raise AssertionError(f"Unable to find function {function_name} in chat_ui.js")

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
                    raise AssertionError(f"Unable to parse function {function_name}; unterminated block comment")
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
        raise AssertionError(f"Unable to parse function {function_name}; unmatched {open_char}{close_char}")

    signature_paren_start = js_text.find("(", start)
    if signature_paren_start < 0:
        raise AssertionError(f"Unable to parse function {function_name} signature start")
    signature_paren_end = _scan_to_matching(js_text, signature_paren_start, "(", ")")

    body_start = -1
    for index in range(signature_paren_end + 1, len(js_text)):
        if js_text[index].isspace():
            continue
        body_start = index
        break
    if body_start < 0 or js_text[body_start] != "{":
        raise AssertionError(f"Unable to parse function {function_name} body start")

    body_end = _scan_to_matching(js_text, body_start, "{", "}")
    return js_text[start:body_end + 1]


def test_chat_ui_display_block_helpers_behavior():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping display block helper test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    meaningful_text_block = _extract_js_function(js_file, "isMeaningfulText")
    pick_value_block = _extract_js_function(js_file, "pickFirstMeaningfulBlockValue")
    has_renderable_block = _extract_js_function(js_file, "hasRenderableDisplayBlock")
    parse_block = _extract_js_function(js_file, "parseDisplayBlocks")
    text_block = _extract_js_function(js_file, "getDisplayBlockText")
    code_block = _extract_js_function(js_file, "renderCodeBlock")
    table_block = _extract_js_function(js_file, "renderTableBlock")
    single_block = _extract_js_function(js_file, "renderSingleDisplayBlock")
    render_blocks_block = _extract_js_function(js_file, "renderDisplayBlocksToHtml")

    script = f"""
const safe = (v) => String(v ?? "");
const normalizeMarkdownText = (v) => String(v || "");
const escapeHtmlAttr = (v) => String(v ?? "");
const md = {{ render: (v) => `<p>${{v}}</p>` }};
{meaningful_text_block}
{pick_value_block}
{has_renderable_block}
{parse_block}
{text_block}
{code_block}
{table_block}
{single_block}
{render_blocks_block}

const result = {{
  invalidParseLength: parseDisplayBlocks("not-json").length,
  objectInputLength: parseDisplayBlocks({{}}).length,
  arrayInputLength: parseDisplayBlocks([{{ type: "markdown", content: "ok" }}]).length,
  filteredBlankTypeLength: parseDisplayBlocks(JSON.stringify([
    {{ type: "   ", content: "x" }},
    {{ type: "markdown", content: "ok" }},
  ])).length,
  filteredBlankTypedContentLength: parseDisplayBlocks([
    {{ type: "tool_result", content: "   " }},
    {{ type: "markdown", content: "ok" }},
  ]).length,
  columnsTable: renderTableBlock({{ columns: ["A"], rows: [["1"]] }}),
  fallbackOnly: renderTableBlock({{ content: "fallback only" }}),
  toolResult: renderSingleDisplayBlock({{
    type: "tool_result",
    status: "success",
    title: "Bash",
    content: "Done",
  }}),
  codeFromText: renderSingleDisplayBlock({{
    type: "code",
    lang: "python",
    text: "print(1)",
  }}),
  toolResultFromOutput: renderSingleDisplayBlock({{
    type: "tool_result",
    title: "Bash",
    status: "success",
    output: "done from output",
  }}),
  blankContentFallsBackToOutput: renderSingleDisplayBlock({{
    type: "tool_result",
    title: "Bash",
    status: "success",
    content: "   ",
    output: "done from output",
  }}),
  toolResultFromResult: renderSingleDisplayBlock({{
    type: "tool_result",
    title: "Bash",
    status: "success",
    result: "done from result",
  }}),
  calloutFromMessage: renderSingleDisplayBlock({{
    type: "callout",
    tone: "warning",
    title: "注意",
    message: "需要确认",
  }}),
  markdownFromValue: renderSingleDisplayBlock({{
    type: "markdown",
    value: "hello from value",
  }}),
  blankCodeContentFallsBackToText: renderSingleDisplayBlock({{
    type: "code",
    lang: "python",
    content: "   ",
    text: "print(1)",
  }}),
  blankCodeFieldFallsBackToText: renderSingleDisplayBlock({{
    type: "code",
    lang: "python",
    code: "   ",
    text: "print(1)",
  }}),
  codeOnly: renderCodeBlock({{
    type: "code",
    code: "print(1)",
    language: "python",
  }}),
  renderCodeFromCodeField: renderCodeBlock({{
    type: "code",
    code: "print(1)",
    language: "python",
  }}),
  renderCodeBlankContentFallback: renderCodeBlock({{
    type: "code",
    content: "   ",
    text: "x = 1",
    language: "python",
  }}),
  calloutFromEnglishMessage: renderSingleDisplayBlock({{
    type: "callout",
    tone: "warning",
    title: "Note",
    message: "Heads up",
  }}),
  bodylessBlocksFallbackPlaceholder: renderDisplayBlocksToHtml([
    {{ type: "tool_result", title: "Bash", content: "   " }},
  ], ""),
}};
console.log(JSON.stringify(result));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert data["invalidParseLength"] == 0
    assert data["objectInputLength"] == 0
    assert data["arrayInputLength"] == 1
    assert data["filteredBlankTypeLength"] == 1
    assert data["filteredBlankTypedContentLength"] == 1
    assert "<th>A</th>" in data["columnsTable"]
    assert "<table>" not in data["fallbackOnly"]
    assert "<p>fallback only</p>" in data["fallbackOnly"]
    assert "message-tool-result is-success" in data["toolResult"]
    assert "print(1)" in data["codeFromText"]
    assert "language-python" in data["codeFromText"]
    assert "message-tool-result is-success" in data["toolResultFromOutput"]
    assert "done from output" in data["toolResultFromOutput"]
    assert "done from output" in data["blankContentFallsBackToOutput"]
    assert "done from result" in data["toolResultFromResult"]
    assert "message-callout is-warning" in data["calloutFromMessage"]
    assert "注意" in data["calloutFromMessage"]
    assert "需要确认" in data["calloutFromMessage"]
    assert "hello from value" in data["markdownFromValue"]
    assert "print(1)" in data["blankCodeContentFallsBackToText"]
    assert "print(1)" in data["blankCodeFieldFallsBackToText"]
    assert "print(1)" in data["codeOnly"]
    assert "print(1)" in data["renderCodeFromCodeField"]
    assert "language-python" in data["renderCodeFromCodeField"]
    assert "Copy" in data["renderCodeFromCodeField"]
    assert "x = 1" in data["renderCodeBlankContentFallback"]
    assert "Heads up" in data["calloutFromEnglishMessage"]
    assert "(empty response)" in data["bodylessBlocksFallbackPlaceholder"]


def test_chat_ui_runtime_event_helpers_behavior():
    """Behavior-level coverage for runtime event normalization and completion states."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    normalize_block = _extract_js_helper_block(js_file, "normalizeRuntimeEvent")
    completion_block = _extract_js_helper_block(js_file, "completionRuntimeState")

    script = f"""
{completion_block}
{normalize_block}

const legacy = normalizeRuntimeEvent({{
  type: "tool_result",
  data: {{ tool: "search", message: "done" }},
  ts: 123,
}});

const normalized = normalizeRuntimeEvent({{
  event_type: "tool_result",
  state: "running",
  session_id: "s1",
  request_id: "r1",
  agent_id: "a1",
  summary: "Tool completed",
  detail_payload: {{ tool: "search" }},
  created_at: "2026-04-04T00:00:00Z",
}});

const precedence = normalizeRuntimeEvent({{
  type: "legacy_type",
  event_type: "normalized_type",
}});

const wrapped = normalizeRuntimeEvent({{
  event: {{
    event_type: "llm_thinking",
    summary: "Reasoning",
    created_at: "2026-04-04T00:00:00Z",
  }}
}});

const zeroTs = normalizeRuntimeEvent({{ type: "tool_result", ts: 0, data: {{}} }});
const zeroStringTs = normalizeRuntimeEvent({{ type: "tool_result", ts: "0", data: {{}} }});
const legacyComplete = normalizeRuntimeEvent({{ type: "complete", data: {{ response: "ok" }} }});
const completionState = normalizeRuntimeEvent({{ event_type: "tool_result", state: "completed", data: {{ tool: "search" }} }});
const failedState = normalizeRuntimeEvent({{ event_type: "tool_result", state: "failed", data: {{ error: "boom" }} }});
const failedResult = normalizeRuntimeEvent({{ event_type: "tool_result", detail_payload: {{ success: false, error: "tool failed" }} }});

const result = {{
  legacy,
  normalized,
  precedence,
  wrapped,
  zeroTs,
  zeroStringTs,
  legacyComplete,
  completionState,
  failedState,
  failedResult,
  invalid: [normalizeRuntimeEvent(null), normalizeRuntimeEvent({{}}), normalizeRuntimeEvent({{foo: "bar"}})],
  completionStates: [
    isCompletionRuntimeState("complete"),
    isCompletionRuntimeState("completed"),
    isCompletionRuntimeState("done"),
    isCompletionRuntimeState("finished"),
    isCompletionRuntimeState("running"),
    isCompletionRuntimeState(""),
    isCompletionRuntimeState(null),
  ]
}};
console.log(JSON.stringify(result));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    legacy = data["legacy"]
    assert legacy["type"] == "tool_result"
    assert legacy["data"]["tool"] == "search"
    assert legacy["data"]["message"] == "done"
    assert legacy["ts"] == 123
    assert legacy.get("state", "") == ""

    normalized = data["normalized"]
    assert normalized["type"] == "tool_result"
    assert normalized["data"]["tool"] == "search"
    assert normalized["data"]["message"] == "Tool completed"
    assert normalized["data"]["request_id"] == "r1"
    assert normalized["data"]["session_id"] == "s1"
    assert normalized["data"]["agent_id"] == "a1"
    assert normalized["request_id"] == "r1"
    assert normalized["session_id"] == "s1"
    assert normalized["agent_id"] == "a1"
    assert normalized["state"] == "running"
    assert isinstance(normalized["ts"], (int, float))
    assert data["precedence"]["type"] == "normalized_type"

    wrapped = data["wrapped"]
    assert wrapped["type"] == "llm_thinking"
    assert wrapped["data"]["message"] == "Reasoning"

    assert data["invalid"] == [None, None, None]
    assert data["completionStates"] == [True, True, True, True, False, False, False]


def test_update_agent_session_is_isolated_per_agent():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")

    script = f"""
const state = {{
  selectedAgentId: "agent-B",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
{create_state}
{ensure_state}
{update_session}
updateAgentSession("agent-A", "s-a");
updateAgentSession("agent-B", "s-b");
updateAgentSession("agent-A", "s-a-2");
console.log(JSON.stringify({{
  a: ensureChatState("agent-A").sessionId,
  b: ensureChatState("agent-B").sessionId,
  mapA: state.agentSessionIds.get("agent-A"),
  mapB: state.agentSessionIds.get("agent-B"),
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["a"] == "s-a-2"
    assert data["b"] == "s-b"
    assert data["mapA"] == "s-a-2"
    assert data["mapB"] == "s-b"


def test_background_success_does_not_render_into_current_dom():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")
    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    mark_unread = _extract_js_function(js_file, "markAgentUnread")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-B",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}, {{id: "agent-B", name: "Agent B"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map([["agent-B", "s-b"]]),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{ throw new Error("should not append"); }} }} }};
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function setChatSubmittingForAgent(agentId, active) {{ ensureChatState(agentId).isSubmitting = active; }}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return null; }}
function buildAssistantMessageArticle() {{ return ""; }}
function attachThinkingToLatestAssistant() {{}}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
let rendered = 0;
function renderAgentList() {{ rendered += 1; }}
function notifyAgentCompletion() {{}}
{create_state}
{ensure_state}
{update_session}
{mark_unread}
{merge_events}
{handle_success}
const aState = ensureChatState("agent-A");
aState.activeRequest = {{ clientRequestId: "req-a" }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{ session_id: "s-a2", response: "ok" }});
  console.log(JSON.stringify({{
    unread: ensureChatState("agent-A").unreadCount,
    needsReload: ensureChatState("agent-A").needsReload,
    bSession: state.agentSessionIds.get("agent-B"),
    renderAgentListCalls: rendered
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["unread"] == 1
    assert data["needsReload"] is True
    assert data["bSession"] == "s-b"
    assert data["renderAgentListCalls"] == 1


def test_selected_agent_hidden_success_notifies_and_merges_events():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{}} }} }};
const document = {{ hidden: true }};
let notifyCalls = 0;
let editCalls = 0;
let attachedEvents = [];
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle() {{ return ""; }}
function attachThinkingToLatestAssistant(events) {{ attachedEvents = events; }}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{ editCalls += 1; }}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{ notifyCalls += 1; }}
function loadSessionForAgent() {{ throw new Error("should not reload"); }}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
chatState.inflightThinking = {{ events: [{{type: "execution.started", request_id: "req-a", session_id: "s-a", data: {{ message: "ws" }} }}] }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{
    session_id: "s-a2",
    response: "done",
    events: [{{type: "tool_result", request_id: "req-a", session_id: "s-a2", data: {{ message: "payload" }} }}]
  }});
  console.log(JSON.stringify({{
    notifyCalls,
    editCalls,
    mergedCount: attachedEvents.length,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["notifyCalls"] == 1
    assert data["editCalls"] == 1
    assert data["mergedCount"] == 2


def test_success_without_optimistic_row_reloads_session():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{ throw new Error("must not append"); }} }} }};
const document = {{ hidden: false }};
let reloadCalls = [];
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return null; }}
function attachThinkingToLatestAssistant() {{}}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{}}
async function loadSessionForAgent(agentId, sessionId, options) {{ reloadCalls.push([agentId, sessionId, options?.render]); }}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{ session_id: "s-a2", response: "done" }});
  console.log(JSON.stringify({{ reloadCalls }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["reloadCalls"] == [["agent-A", "s-a2", True]]


def test_failure_restores_hidden_attachments_and_hidden_tab_notifies():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    handle_failure = _extract_js_function(js_file, "handleAgentChatFailure")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{
  chatInput: {{ value: "" }},
  messageList: {{ insertAdjacentHTML() {{}} }},
}};
const attachmentNode = {{ value: "" }};
const document = {{
  hidden: true,
  getElementById(id) {{ return id === "chat-attachments" ? attachmentNode : null; }},
}};
let notifyCalls = 0;
function removeTemporaryAssistantRows() {{}}
function removeLatestOptimisticUserRow() {{}}
function renderInputPreview() {{}}
function syncChatInputHeight() {{}}
function setChatStatus() {{}}
function safe(v) {{ return String(v); }}
function scrollToBottom() {{}}
function renderIcons() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{ notifyCalls += 1; }}
{create_state}
{ensure_state}
{set_submitting}
{handle_failure}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
handleAgentChatFailure("agent-A", {{
  clientRequestId: "req-a",
  attachments: ["file-1", "file-2"],
  backupFiles: [],
  backupMessage: "msg"
}}, new Error("boom"));
console.log(JSON.stringify({{
  attachmentsValue: attachmentNode.value,
  draftAttachmentsValue: ensureChatState("agent-A").draftAttachmentsValue,
  notifyCalls
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["attachmentsValue"] == '["file-1","file-2"]'
    assert data["draftAttachmentsValue"] == '["file-1","file-2"]'
    assert data["notifyCalls"] == 1


def test_background_failure_restores_original_agent_draft_state_only():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    mark_unread = _extract_js_function(js_file, "markAgentUnread")
    handle_failure = _extract_js_function(js_file, "handleAgentChatFailure")

    script = f"""
const state = {{
  selectedAgentId: "agent-B",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}, {{id: "agent-B", name: "Agent B"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{
  chatInput: {{ value: "" }},
  messageList: {{ insertAdjacentHTML() {{ throw new Error("must not touch current DOM"); }} }},
}};
const document = {{
  hidden: false,
  getElementById() {{ throw new Error("must not read selected DOM attachments in background branch"); }},
}};
let renderCalls = 0;
function removeTemporaryAssistantRows() {{}}
function removeLatestOptimisticUserRow() {{}}
function renderInputPreview() {{}}
function syncChatInputHeight() {{}}
function setChatStatus() {{}}
function safe(v) {{ return String(v); }}
function scrollToBottom() {{}}
function renderIcons() {{}}
function markAgentUnread(agentId, status) {{
  const chatState = ensureChatState(agentId);
  chatState.unreadCount += 1;
  chatState.backgroundStatus = status;
}}
function renderAgentList() {{ renderCalls += 1; }}
function notifyAgentCompletion() {{}}
{create_state}
{ensure_state}
{set_submitting}
{mark_unread}
{handle_failure}
const chatStateA = ensureChatState("agent-A");
chatStateA.activeRequest = {{ clientRequestId: "req-a" }};
handleAgentChatFailure("agent-A", {{
  clientRequestId: "req-a",
  backupMessage: "fix this",
  backupFiles: [{{id: "pf-1"}}],
  attachments: ["file-1", "file-2"],
}}, new Error("failed"));
console.log(JSON.stringify({{
  draftText: ensureChatState("agent-A").draftText,
  draftAttachmentsValue: ensureChatState("agent-A").draftAttachmentsValue,
  pendingFilesLen: ensureChatState("agent-A").pendingFiles.length,
  backgroundStatus: ensureChatState("agent-A").backgroundStatus,
  needsReload: ensureChatState("agent-A").needsReload,
  renderCalls
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["draftText"] == "fix this"
    assert data["draftAttachmentsValue"] == '["file-1","file-2"]'
    assert data["pendingFilesLen"] == 1
    assert data["backgroundStatus"] == "error"
    assert data["needsReload"] is False
    assert data["renderCalls"] == 1


def test_ensure_event_socket_for_selected_agent_uses_active_request_id():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    ensure_socket_for_agent_fn = _extract_js_function(js_file, "ensureEventSocketForAgent")
    ensure_socket_fn = _extract_js_function(js_file, "ensureEventSocketForSelectedAgent")

    script = f"""
{ensure_socket_for_agent_fn}
{ensure_socket_fn}
const CONNECTING = 0;
const OPEN = 1;
const window = {{ location: {{ protocol: "https:", host: "portal.test" }} }};
let createdUrl = "";
const state = {{
  selectedAgentId: "agent-A",
  eventWs: null,
  eventWsAgentId: null,
  eventWsSessionId: null,
  eventWsRequestId: null,
}};
function currentSessionIdForSelectedAgent() {{ return "s-1"; }}
function ensureChatState() {{ return {{ activeRequest: {{ clientRequestId: "req-live-1" }} }}; }}
function disconnectEventSocket() {{}}
function handleAgentEventMessage() {{}}
class FakeWebSocket {{
  constructor(url) {{ this.url = url; this.readyState = CONNECTING; createdUrl = url; }}
}}
FakeWebSocket.CONNECTING = CONNECTING;
FakeWebSocket.OPEN = OPEN;
globalThis.WebSocket = FakeWebSocket;
ensureEventSocketForSelectedAgent();
console.log(JSON.stringify({{ createdUrl }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert "session_id=s-1" in data["createdUrl"]
    assert "request_id=req-live-1" in data["createdUrl"]
def test_chat_ui_event_socket_replaces_stale_connecting_session():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    ensure_socket_for_agent_fn = _extract_js_function(js_file, "ensureEventSocketForAgent")
    ensure_socket_fn = _extract_js_function(js_file, "ensureEventSocketForSelectedAgent")

    script = f"""
{ensure_socket_for_agent_fn}
{ensure_socket_fn}

const events = [];
const CLOSED = 3;
const CONNECTING = 0;
const OPEN = 1;
let currentSession = "new-session";
let websocketCreated = 0;

const window = {{
  location: {{
    protocol: "https:",
    host: "portal.test",
  }}
}};

const state = {{
  selectedAgentId: "agent-A",
  eventWs: {{
    readyState: CONNECTING,
    close: () => {{
      events.push("closed:old");
      state.eventWs.readyState = CLOSED;
    }},
  }},
  eventWsAgentId: "agent-A",
  eventWsSessionId: "old-session",
  eventWsRequestId: "old-req",
}};

function ensureChatState() {{ return {{ sessionId: currentSession }}; }}

function currentSessionIdForSelectedAgent() {{
  return currentSession;
}}

function disconnectEventSocket() {{
  if (state.eventWs) state.eventWs.close();
  state.eventWs = null;
  state.eventWsAgentId = null;
  state.eventWsSessionId = null;
  state.eventWsRequestId = null;
}}

class FakeWebSocket {{
  constructor(url) {{
    this.url = url;
    this.readyState = CONNECTING;
    websocketCreated += 1;
    events.push("opened:" + url);
  }}
  close() {{
    this.readyState = CLOSED;
    events.push("closed:new");
  }}
}}
FakeWebSocket.CONNECTING = CONNECTING;
FakeWebSocket.OPEN = OPEN;
globalThis.WebSocket = FakeWebSocket;

ensureEventSocketForAgent("agent-A", "s-1", "req-1");
const directUrl = state.eventWs?.url || null;

ensureEventSocketForSelectedAgent();
const firstSocket = state.eventWs;
const firstUrl = firstSocket?.url || null;
const firstSession = state.eventWsSessionId;
const firstCreated = websocketCreated;

ensureEventSocketForSelectedAgent();
const secondSocket = state.eventWs;
const secondUrl = secondSocket?.url || null;
const secondSession = state.eventWsSessionId;
const secondCreated = websocketCreated;

console.log(JSON.stringify({{
  events,
  firstUrl,
  firstSession,
  firstCreated,
  secondUrl,
  secondSession,
  secondCreated,
  sameSocketOnSecondCall: firstSocket === secondSocket,
  directUrl,
}}));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert "closed:old" in data["events"]
    assert data["directUrl"] == "wss://portal.test/a/agent-A/api/events?session_id=s-1&request_id=req-1"
    assert data["firstUrl"] == "wss://portal.test/a/agent-A/api/events?session_id=new-session"
    assert data["firstSession"] == "new-session"
    assert data["firstCreated"] == 2
    assert data["secondCreated"] == 2
    assert data["secondUrl"] == data["firstUrl"]
    assert data["secondSession"] == "new-session"
    assert data["sameSocketOnSecondCall"] is True


def test_chat_ui_set_active_nav_section_avoids_reloading_visible_lists():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    set_active_nav_section_fn = _extract_js_function(js_file, "setActiveNavSection")

    script = f"""
{set_active_nav_section_fn}

function noop() {{}}
function makeToggleObj() {{
  return {{
    classList: {{
      toggle: noop,
    }},
  }};
}}

const dom = {{
  railAssistantsBtn: makeToggleObj(),
  bundlesMenuBtn: makeToggleObj(),
  tasksMenuBtn: makeToggleObj(),
  assistantsNavSection: makeToggleObj(),
  bundlesNavSection: makeToggleObj(),
  tasksNavSection: makeToggleObj(),
  workspaceDetailContent: {{
    dataset: {{
      workspaceState: "idle",
    }},
  }},
}};

let bundleRefreshCount = 0;
let taskRefreshCount = 0;
const state = {{}};

function applySecondaryPaneState() {{}}
function renderSecondaryPaneHeader() {{}}
function syncMainHeader() {{}}
function showAssistantDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "assistant-default";
}}
function showBundlesLoadingMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "bundles-loading";
}}
function showTasksLoadingMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "tasks-loading";
}}
function showBundlesDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "bundles-default";
}}
function showTasksDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "tasks-default";
}}
async function refreshRequirementBundles() {{
  bundleRefreshCount += 1;
}}
async function refreshMyTasks() {{
  taskRefreshCount += 1;
}}

async function runScenarioA() {{
  bundleRefreshCount = 0;
  taskRefreshCount = 0;
  Object.assign(state, {{
    activeNavSection: "bundles",
    secondaryPaneCollapsed: false,
    selectedBundleKey: "bundle-1",
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  await setActiveNavSection("bundles", {{ toggleIfSame: false }});
  return {{
    bundleRefreshCount,
    activeNavSection: state.activeNavSection,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
  }};
}}

async function runScenarioB() {{
  bundleRefreshCount = 0;
  taskRefreshCount = 0;
  Object.assign(state, {{
    activeNavSection: "assistants",
    secondaryPaneCollapsed: false,
    selectedBundleKey: null,
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "assistant-default";
  await setActiveNavSection("bundles", {{ toggleIfSame: false }});
  return {{
    bundleRefreshCount,
    activeNavSection: state.activeNavSection,
  }};
}}

async function runScenarioC() {{
  bundleRefreshCount = 0;
  taskRefreshCount = 0;
  Object.assign(state, {{
    activeNavSection: "bundles",
    secondaryPaneCollapsed: true,
    selectedBundleKey: null,
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  await setActiveNavSection("bundles");
  return {{
    bundleRefreshCount,
    secondaryPaneCollapsed: state.secondaryPaneCollapsed,
  }};
}}

async function runScenarioD() {{
  bundleRefreshCount = 0;
  taskRefreshCount = 0;
  Object.assign(state, {{
    activeNavSection: "tasks",
    secondaryPaneCollapsed: false,
    selectedBundleKey: null,
    selectedTaskId: "task-1",
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "task-detail";
  await setActiveNavSection("tasks", {{ toggleIfSame: false }});
  return {{
    taskRefreshCount,
    activeNavSection: state.activeNavSection,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
  }};
}}

(async () => {{
  const result = {{
    scenarioA: await runScenarioA(),
    scenarioB: await runScenarioB(),
    scenarioC: await runScenarioC(),
    scenarioD: await runScenarioD(),
  }};
  console.log(JSON.stringify(result));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert data["scenarioA"]["bundleRefreshCount"] == 0
    assert data["scenarioA"]["activeNavSection"] == "bundles"
    assert data["scenarioA"]["workspaceState"] == "bundle-detail"

    assert data["scenarioB"]["bundleRefreshCount"] == 1
    assert data["scenarioB"]["activeNavSection"] == "bundles"

    assert data["scenarioC"]["bundleRefreshCount"] == 1
    assert data["scenarioC"]["secondaryPaneCollapsed"] is False

    assert data["scenarioD"]["taskRefreshCount"] == 0
    assert data["scenarioD"]["activeNavSection"] == "tasks"
    assert data["scenarioD"]["workspaceState"] == "task-detail"


def test_thinking_process_template_prefers_normalized_fields():
    template = Path("app/templates/partials/thinking_process_panel.html").read_text(encoding="utf-8")
    assert template.find("event.event_type or event.type") != -1
    assert template.find("event.summary") < template.find("event.data and event.data.message")
