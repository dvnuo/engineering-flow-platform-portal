from pathlib import Path

from _js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src():
    return SRC.read_text(encoding="utf-8")


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
