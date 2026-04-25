"""Helpers for extracting JS function/helper blocks from source files in tests."""


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
                    raise AssertionError(
                        f"Unable to parse function {function_name}; unterminated block comment"
                    )
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


def _extract_render_chat_history_dependencies(js_text: str) -> str:
    """Extract renderChatHistory with direct helper dependencies for Node harness tests."""
    format_attachment_meta_text = _extract_js_function(js_text, "formatAttachmentMetaText")
    render_chat_history = _extract_js_function(js_text, "renderChatHistory")
    return f"{format_attachment_meta_text}\n{render_chat_history}"
