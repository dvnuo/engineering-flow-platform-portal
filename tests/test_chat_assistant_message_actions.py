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
