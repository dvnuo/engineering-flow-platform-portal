import json
import shutil
import subprocess
from pathlib import Path



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")
import pytest

from _js_extract_helpers import _extract_js_function


def test_chat_ui_includes_display_block_renderer_helpers():
    js_source = _chat_ui_js_source()
    assert "function parseDisplayBlocks(" in js_source
    assert "function renderDisplayBlocksToHtml(" in js_source
    assert "function renderSingleDisplayBlock(" in js_source
    assert "function renderCodeBlock(" in js_source
    assert "function renderTableBlock(" in js_source
    assert "function enhanceMarkdownBlock(" in js_source


def test_background_success_does_not_render_into_current_dom():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")
    js_file = _chat_ui_js_source()
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
    renderAgentListCalls: rendered,
    inflightThinking: ensureChatState("agent-A").inflightThinking,
    pendingThinkingEvents: ensureChatState("agent-A").pendingThinkingEvents
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["unread"] == 1
    assert data["needsReload"] is True
    assert data["bSession"] == "s-b"
    assert data["renderAgentListCalls"] == 1
    assert data["inflightThinking"] is None
    assert data["pendingThinkingEvents"] is None


def test_selected_agent_hidden_success_notifies_and_merges_events():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
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
{get_selected_assistant_display_name}
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

    js_file = _chat_ui_js_source()
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

    js_file = _chat_ui_js_source()
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

    js_file = _chat_ui_js_source()
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
chatStateA.attachmentHistory = [["old-1"], ["new-failed"]];
chatStateA.didAppendAttachmentHistoryForPendingSend = true;
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
  attachmentHistory: ensureChatState("agent-A").attachmentHistory,
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
    assert data["attachmentHistory"] == [["old-1"]]
    assert data["backgroundStatus"] == "error"
    assert data["needsReload"] is False
    assert data["renderCalls"] == 1


def test_render_chat_history_rebuilds_attachment_history_for_selected_agent():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history = _extract_js_function(js_file, "renderChatHistory")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [["dirty-old"]] }}]]),
}};
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild() {{}},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{ dom.messageList.innerHTML = "WELCOME"; }}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      appendChild() {{}},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history}
renderChatHistory([
  {{ role: "user", content: "u1", attachments: ["file-1"] }},
  {{ role: "assistant", content: "a1" }},
  {{ role: "user", content: "u2", attachments: [] }},
], {{}});
console.log(JSON.stringify({{
  attachmentHistory: state.chatStatesByAgent.get("agent-A").attachmentHistory
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["attachmentHistory"] == [["file-1"], []]


def test_render_chat_history_empty_clears_attachment_history():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    render_history = _extract_js_function(js_file, "renderChatHistory")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [["dirty-old"]] }}]]),
}};
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild() {{}},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{ dom.messageList.innerHTML = "WELCOME"; }}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
const document = {{
  createElement() {{
    return {{ className: "", dataset: {{}}, textContent: "", appendChild() {{}} }};
  }},
}};
{render_history}
renderChatHistory([], {{}});
console.log(JSON.stringify({{
  attachmentHistory: state.chatStatesByAgent.get("agent-A").attachmentHistory,
  messageListHtml: dom.messageList.innerHTML
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["attachmentHistory"] == []
    assert data["messageListHtml"] == "WELCOME"


def test_build_user_message_article_uses_current_user_display_name():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    build_user_message_article = _extract_js_function(js_file, "buildUserMessageArticle")

    script = f"""
const state = {{ currentUserName: "Alice" }};
function safe(value) {{ return String(value || ""); }}
function escapeHtml(value) {{ return String(value || ""); }}
function escapeHtmlAttr(value) {{ return String(value || ""); }}
{get_current_user_display_name}
{build_user_message_article}
const html = buildUserMessageArticle("hello", []);
console.log(JSON.stringify({{ html }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert 'message-author">Alice<' in data["html"]


def test_render_chat_history_prefers_author_name_for_user_and_assistant():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history = _extract_js_function(js_file, "renderChatHistory")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  currentUserName: "Portal User",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [] }}]]),
}};
const appendedRows = [];
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild(node) {{ appendedRows.push(node); }},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
function isTrackableThinkingEvent() {{ return false; }}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      children: [],
      appendChild(child) {{ this.children.push(child); }},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history}
renderChatHistory([
  {{ role: "user", content: "u", author_name: "Alice" }},
  {{ role: "assistant", content: "a", author_name: "Portal Agent" }},
], {{}});
const authorLabels = appendedRows.map((row) => row.children[0].children[0].textContent);
console.log(JSON.stringify({{ authorLabels }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["authorLabels"] == ["Alice", "Portal Agent"]


def test_render_chat_history_assistant_falls_back_to_selected_agent_name():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history = _extract_js_function(js_file, "renderChatHistory")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  currentUserName: "Portal User",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [] }}]]),
}};
const appendedRows = [];
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild(node) {{ appendedRows.push(node); }},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
function isTrackableThinkingEvent() {{ return false; }}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      children: [],
      appendChild(child) {{ this.children.push(child); }},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history}
renderChatHistory([
  {{ role: "assistant", content: "a" }},
], {{}});
const authorLabel = appendedRows[0].children[0].children[0].textContent;
console.log(JSON.stringify({{ authorLabel }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["authorLabel"] == "Agent A"


def test_render_chat_history_blank_author_name_falls_back_to_current_names():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history = _extract_js_function(js_file, "renderChatHistory")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Portal Agent",
  currentUserName: "Alice",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [] }}]]),
}};
const appendedRows = [];
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild(node) {{ appendedRows.push(node); }},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
function isTrackableThinkingEvent() {{ return false; }}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      children: [],
      appendChild(child) {{ this.children.push(child); }},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history}
renderChatHistory([
  {{ role: "user", content: "u", author_name: "   " }},
  {{ role: "assistant", content: "a", author_name: "   " }},
], {{}});
const authorLabels = appendedRows.map((row) => row.children[0].children[0].textContent);
console.log(JSON.stringify({{ authorLabels }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["authorLabels"] == ["Alice", "Portal Agent"]


def test_handle_agent_chat_success_passes_selected_assistant_name_to_final_message_builder():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Portal Agent",
  mineAgents: [{{id: "agent-A", name: "Portal Agent"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{}} }} }};
const document = {{ hidden: false }};
let capturedAuthorName = null;
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle(_content, _blocks, authorName) {{
  capturedAuthorName = authorName;
  return "";
}}
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
function loadSessionForAgent() {{ throw new Error("should not reload"); }}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{get_selected_assistant_display_name}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{
    session_id: "s-a2",
    response: "done",
    display_blocks: [],
    author_name: "Runtime Alias"
  }});
  console.log(JSON.stringify({{ capturedAuthorName }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["capturedAuthorName"] == "Portal Agent"


def test_ensure_event_socket_for_selected_agent_uses_active_request_id():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
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

    js_file = _chat_ui_js_source()
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


def test_switching_to_b_while_a_submitting_reenables_send_for_b():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    restore_composer = _extract_js_function(js_file, "restoreComposerForAgent")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{
  sendChatBtn: {{ disabled: true }},
  chatInput: {{ value: "" }},
}};
const attachmentsNode = {{ value: "" }};
const document = {{
  getElementById(id) {{
    if (id === "chat-attachments") return attachmentsNode;
    return null;
  }},
}};
function syncChatInputHeight() {{}}
function renderInputPreview() {{}}
{create_state}
{ensure_state}
{set_submitting}
{restore_composer}
ensureChatState("agent-A");
ensureChatState("agent-B");
setChatSubmittingForAgent("agent-A", true);
setChatSubmittingForAgent("agent-B", false);
state.selectedAgentId = "agent-B";
restoreComposerForAgent("agent-B");
console.log(JSON.stringify({{ disabled: dom.sendChatBtn.disabled }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["disabled"] is False
