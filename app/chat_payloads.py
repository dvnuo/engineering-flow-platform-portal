def build_markdown_display_blocks(text: str) -> list[dict]:
    if text is None:
        return []
    raw_text = str(text)
    if not raw_text.strip():
        return []
    return [{"type": "markdown", "content": raw_text}]


def _first_text_value(block: dict, field_order: tuple[str, ...]) -> str:
    for field in field_order:
        value = block.get(field)
        if value is None:
            continue
        text = str(value)
        if text.strip():
            return text
    return ""


def _meaningful_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if text.strip() else ""


def _text_value(block: dict) -> str:
    return _first_text_value(block, ("content", "text", "message", "output", "result", "value"))


def _normalize_display_block(block: dict) -> dict | None:
    if not isinstance(block, dict):
        return None

    raw_type = block.get("type")
    if not isinstance(raw_type, str):
        return None

    block_type = raw_type.strip().lower()
    if not block_type:
        return None

    if block_type == "markdown":
        content = _text_value(block)
        return {"type": "markdown", "content": content} if content else None

    if block_type == "callout":
        content = _text_value(block)
        if not content:
            return None
        normalized = {"type": "callout", "content": content}
        title = block.get("title")
        if title not in (None, ""):
            normalized["title"] = str(title)
        tone = block.get("tone")
        if tone not in (None, ""):
            normalized["tone"] = str(tone)
        return normalized

    if block_type == "tool_result":
        content = _text_value(block)
        if not content:
            return None
        normalized = {"type": "tool_result", "content": content}
        title = block.get("title")
        if title not in (None, ""):
            normalized["title"] = str(title)
        status = block.get("status")
        if status not in (None, ""):
            normalized["status"] = str(status)
        return normalized

    if block_type == "code":
        content = _first_text_value(block, ("content", "code", "text", "value", "output"))
        if not content:
            return None
        normalized = {"type": "code", "content": content}
        language = block.get("lang")
        if language in (None, ""):
            language = block.get("language")
        if language not in (None, ""):
            normalized["lang"] = str(language)
        return normalized

    if block_type == "table":
        headers = block.get("headers")
        if not isinstance(headers, list):
            headers = block.get("columns")
        headers = [str(item) for item in headers] if isinstance(headers, list) else []
        rows = block.get("rows")
        rows = rows if isinstance(rows, list) else []
        normalized = {"type": "table", "headers": headers, "rows": rows}
        content = _text_value(block)
        if content:
            normalized["content"] = content
        if headers or rows or content:
            return normalized
        return None

    content = _text_value(block)
    if not content:
        return None
    return {"type": "markdown", "content": content}


def normalize_display_blocks(raw_blocks, fallback_text: str = "") -> list[dict]:
    if not isinstance(raw_blocks, list):
        return build_markdown_display_blocks(fallback_text)

    normalized = []
    for block in raw_blocks:
        parsed = _normalize_display_block(block)
        if parsed:
            normalized.append(parsed)

    if normalized:
        return normalized
    return build_markdown_display_blocks(fallback_text)


def normalize_assistant_chat_payload(data: dict, fallback_session_id: str = "") -> dict:
    if not isinstance(data, dict):
        data = {}

    response_text = _meaningful_text(data.get("response"))
    content_text = _meaningful_text(data.get("content"))
    assistant_message = response_text or content_text or ""
    display_blocks = normalize_display_blocks(data.get("display_blocks"), assistant_message)

    if not assistant_message and not display_blocks:
        assistant_message = "(empty response)"

    events = data.get("events", [])
    if not isinstance(events, list):
        events = []

    return {
        "assistant_message": assistant_message,
        "display_blocks": display_blocks,
        "events": events,
        "session_id": data.get("session_id") or fallback_session_id or "",
        "user_message_id": data.get("user_message_id") or "",
    }
