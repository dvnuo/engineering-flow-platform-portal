import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    return (_repo_root() / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")


def test_assistant_message_actions_hooks_present_in_chat_ui_and_css():
    js_source = _chat_ui_js_source()
    css_source = (_repo_root() / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "function addAssistantActionsToMessages" in js_source
    assert "function retryAssistantMessage" in js_source
    assert "function getAssistantCopyText" in js_source
    assert "function findPrecedingUserArticle" in js_source
    assert "function truncateDomFromUserArticle" in js_source
    assert "function addUserEditButtonsToMessages" in js_source
    assert "function addEditButtonsToMessages" in js_source
    assert "function shouldDecorateChatSwapTarget" in js_source
    assert "function decorateChatMessageRegion" in js_source
    assert "function hasFollowingMessageRows" in js_source
    assert "assistant-copy-btn" in js_source
    assert "assistant-retry-btn" in js_source
    assert "delete-from-here" in js_source
    lifecycle_block = js_source[js_source.find("function initializeRenderLifecycle"):js_source.find("// ===== suggestion popup hooks =====")]
    assert "decorateChatMessageRegion(target)" in lifecycle_block
    decorate_block = js_source[js_source.find("function decorateChatMessageRegion"):js_source.find("function initializeRenderLifecycle")]
    assert "addEditButtonsToMessages();" in decorate_block

    assert ".message-actions" in css_source
    assert ".message-actions-assistant" in css_source
    assert ".message-action-btn" in css_source
    assert ".message-row:hover .message-actions" in css_source


def test_get_assistant_copy_text_prefers_raw_markdown_dataset_when_available():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    get_assistant_copy_text = _extract_js_function(js_source, "getAssistantCopyText")

    script = f"""
function parseDisplayBlocks() {{ return []; }}
{get_assistant_copy_text}
const markdown = '# Hello\\n\\n```js\\nx\\n```';
const article = {{
  textContent: 'Rendered toolbar Copy text',
  querySelector(selector) {{
    if (selector === '.message-markdown') {{
      return {{ dataset: {{ md: markdown, displayBlocks: '[]' }} }};
    }}
    return null;
  }}
}};
const copied = getAssistantCopyText(article);
console.log(JSON.stringify({{ copied }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    assert payload["copied"] == "# Hello\n\n```js\nx\n```"
    assert "Rendered toolbar" not in payload["copied"]


def test_get_assistant_copy_text_display_block_code_value_avoids_toolbar_fallback():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    is_meaningful_text = _extract_js_function(js_source, "isMeaningfulText")
    pick_first = _extract_js_function(js_source, "pickFirstMeaningfulBlockValue")
    get_display_text = _extract_js_function(js_source, "getDisplayBlockText")
    has_renderable = _extract_js_function(js_source, "hasRenderableDisplayBlock")
    parse_blocks = _extract_js_function(js_source, "parseDisplayBlocks")
    get_block_copy = _extract_js_function(js_source, "getDisplayBlockCopyText")
    get_assistant_copy_text = _extract_js_function(js_source, "getAssistantCopyText")

    script = f"""
{is_meaningful_text}
{pick_first}
{get_display_text}
{has_renderable}
{parse_blocks}
{get_block_copy}
{get_assistant_copy_text}
const displayBlocks = JSON.stringify([{{ type: "code", value: "console.log(1)" }}]);
const article = {{
  textContent: "Copy console.log(1)",
  querySelector(selector) {{
    if (selector === ".message-markdown") {{
      return {{ dataset: {{ md: "", displayBlocks }} }};
    }}
    return null;
  }}
}};
console.log(JSON.stringify({{ copied: getAssistantCopyText(article) }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    assert payload["copied"] == "console.log(1)"
    assert "Copy" not in payload["copied"]


def test_get_assistant_copy_text_table_block_fallback_uses_table_data():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    is_meaningful_text = _extract_js_function(js_source, "isMeaningfulText")
    pick_first = _extract_js_function(js_source, "pickFirstMeaningfulBlockValue")
    get_display_text = _extract_js_function(js_source, "getDisplayBlockText")
    has_renderable = _extract_js_function(js_source, "hasRenderableDisplayBlock")
    parse_blocks = _extract_js_function(js_source, "parseDisplayBlocks")
    get_block_copy = _extract_js_function(js_source, "getDisplayBlockCopyText")
    get_assistant_copy_text = _extract_js_function(js_source, "getAssistantCopyText")

    script = f"""
{is_meaningful_text}
{pick_first}
{get_display_text}
{has_renderable}
{parse_blocks}
{get_block_copy}
{get_assistant_copy_text}
const displayBlocks = JSON.stringify([{{ type: "table", headers: ["A", "B"], rows: [[1,2],[3,4]] }}]);
const article = {{
  textContent: "bad fallback Copy",
  querySelector(selector) {{
    if (selector === ".message-markdown") {{
      return {{ dataset: {{ md: "", displayBlocks }} }};
    }}
    return null;
  }}
}};
console.log(JSON.stringify({{ copied: getAssistantCopyText(article) }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    assert "A | B" in payload["copied"]
    assert "1 | 2" in payload["copied"]
    assert "bad fallback" not in payload["copied"]


def test_has_following_message_rows_scans_all_siblings():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    has_following = _extract_js_function(js_source, "hasFollowingMessageRows")

    script = f"""
{has_following}
const second = {{ nextElementSibling: null, matches: (selector) => selector === ".message-row" }};
const first = {{ nextElementSibling: second, matches: () => false }};
const row = {{ nextElementSibling: first }};
console.log(JSON.stringify({{ result: hasFollowingMessageRows(row) }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    assert payload["result"] is True


def test_retry_assistant_message_uses_preceding_user_message_id_in_delete_url():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    find_preceding = _extract_js_function(js_source, "findPrecedingUserArticle")
    has_following = _extract_js_function(js_source, "hasFollowingMessageRows")
    retry_assistant = _extract_js_function(js_source, "retryAssistantMessage")

    script = f"""
const state = {{ selectedAgentId: "agent-1" }};
const dom = {{ chatInput: {{ value: "" }} }};
const deletedUrls = [];
const userArticle = {{
  dataset: {{ messageId: "u2" }},
}};
const userRow = {{
  previousElementSibling: null,
  querySelector(selector) {{
    if (selector === 'article[data-local-user="1"]') return userArticle;
    return null;
  }},
}};
const assistantRow = {{
  dataset: {{ messageId: "assistant-should-not-be-used" }},
  previousElementSibling: userRow,
  nextElementSibling: null,
  querySelector() {{ return null; }},
}};
function getChatState() {{ return {{ isSubmitting: false, pendingFiles: [] }}; }}
function currentSessionIdForAgent() {{ return "session-9"; }}
function getUserArticleContent() {{ return "retry this"; }}
function truncateDomFromUserArticle() {{}}
function setChatStatus() {{}}
async function submitChatForSelectedAgent() {{}}
function showToast() {{}}
const window = {{ confirm: () => true }};
const document = {{
  getElementById(id) {{
    if (id === "chat-session-id") return {{ value: "session-9" }};
    if (id === "chat-attachments") return {{ value: "" }};
    return null;
  }}
}};
async function fetch(url) {{
  deletedUrls.push(url);
  return {{ ok: true, async json() {{ return {{ success: true }}; }} }};
}}
{find_preceding}
{has_following}
{retry_assistant}
(async () => {{
  await retryAssistantMessage(assistantRow);
  console.log(JSON.stringify({{ deletedUrls }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    assert payload["deletedUrls"], "expected retryAssistantMessage to call delete-from-here"
    assert "/messages/u2/delete-from-here" in payload["deletedUrls"][0]
    assert "assistant-should-not-be-used" not in payload["deletedUrls"][0]


def test_message_mutation_failure_uses_friendly_runtime_error_helper():
    js_source = _chat_ui_js_source()

    assert "function getRuntimeMutationErrorMessage" in js_source
    assert "unsupported_by_opencode_adapter_mvp" in js_source
    assert "This runtime does not support retry/edit yet" in js_source

    retry_start = js_source.find("async function retryAssistantMessage")
    edit_start = js_source.find("document.getElementById(\"message-edit-form\")?.addEventListener(\"submit\"")
    assert retry_start != -1
    assert edit_start != -1

    retry_block = js_source[retry_start:js_source.find("function getAssistantCopyText", retry_start)]
    edit_block = js_source[edit_start:js_source.find("if (closeBtn)", edit_start)]

    assert "showToast(getRuntimeMutationErrorMessage(response, result, \"Failed to delete message\"));" in retry_block
    assert "showToast(getRuntimeMutationErrorMessage(response, result, \"Failed to delete message\"));" in edit_block
    assert js_source.count('showToast(getRuntimeMutationErrorMessage(response, {}, "Failed to delete message"));') >= 2


def test_chat_stream_final_payload_preserves_assistant_message_id():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    assert 'assistant_message_id: data?.assistant_message_id || ""' in js_source

    is_delta = _extract_js_function(js_source, "isChatStreamDeltaPayload")
    event_type = _extract_js_function(js_source, "getChatStreamEventType")
    text_payload = _extract_js_function(js_source, "getChatStreamTextPayload")
    normalize_data = _extract_js_function(js_source, "normalizeChatStreamEventData")
    handle_stream = _extract_js_function(js_source, "handleChatStreamEvent")

    script = f"""
let captured = null;
const state = {{ selectedAgentId: "agent-1" }};
const dom = {{ messageList: null }};
function updatePendingAssistantStreamContent() {{}}
async function handleAgentChatSuccess(agentId, requestCtx, payload) {{ captured = payload; }}
function handleAgentEventMessage() {{}}
{is_delta}
{event_type}
{text_payload}
{normalize_data}
{handle_stream}
(async () => {{
  const result = await handleChatStreamEvent(
    "agent-1",
    {{ sessionIdAtSend: "s1", clientRequestId: "r1", streamedText: "" }},
    "final",
    {{
      response: "done",
      session_id: "s1",
      request_id: "r1",
      user_message_id: "u-1",
      assistant_message_id: "a-1",
      events: [],
      runtime_events: []
    }}
  );
  console.log(JSON.stringify({{ result, captured }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    assert payload["result"] == "final"
    assert payload["captured"]["user_message_id"] == "u-1"
    assert payload["captured"]["assistant_message_id"] == "a-1"


def test_get_runtime_mutation_error_message_behavior():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    helper = _extract_js_function(js_source, "getRuntimeMutationErrorMessage")

    script = f"""
{helper}
const unsupported = getRuntimeMutationErrorMessage(
  {{ status: 501 }},
  {{ error: "unsupported_by_opencode_adapter_mvp" }},
  "Failed to delete message",
);
const passthrough = getRuntimeMutationErrorMessage(
  {{ status: 400 }},
  {{ error: "message_not_found" }},
  "Failed to delete message",
);
const fallback = getRuntimeMutationErrorMessage(
  {{ status: 500 }},
  {{}},
  "Failed to delete message",
);
const friendly = getRuntimeMutationErrorMessage(
  {{ status: 501 }},
  {{}},
  "Failed to delete message",
);
console.log(JSON.stringify({{ unsupported, passthrough, fallback, friendly }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)

    assert payload["unsupported"] == (
        "This runtime does not support retry/edit yet. Please refresh the session after the runtime is upgraded, or start a new chat."
    )
    assert payload["unsupported"] != "unsupported_by_opencode_adapter_mvp"
    assert payload["passthrough"] == "message_not_found"
    assert payload["fallback"] == "Failed to delete message"
    assert "does not support retry/edit yet" in payload["friendly"]


def test_build_assistant_message_article_adds_optional_message_id_attribute():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_source = _chat_ui_js_source()
    build_assistant = _extract_js_function(js_source, "buildAssistantMessageArticle")

    script = f"""
function escapeHtmlAttr(value) {{ return String(value); }}
function escapeHtml(value) {{ return String(value || ""); }}
function safe(value) {{ return String(value || ""); }}
function parseDisplayBlocks(value) {{ return Array.isArray(value) ? value : []; }}
function renderDisplayBlocks() {{ return ""; }}
function markedParse(value) {{ return String(value || ""); }}
{build_assistant}
const withId = buildAssistantMessageArticle("hello", [], "Assistant", "a-123");
const withoutId = buildAssistantMessageArticle("hello", [], "Assistant");
console.log(JSON.stringify({{ withId, withoutId }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)

    assert 'data-message-id="a-123"' in payload["withId"]
    assert 'data-message-id=""' not in payload["withoutId"]
