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
    assert "assistant-copy-btn" in js_source
    assert "assistant-retry-btn" in js_source
    assert "delete-from-here" in js_source

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
