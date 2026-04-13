from __future__ import annotations


def build_markdown_display_blocks(text: str) -> list[dict]:
    if not isinstance(text, str):
        return []
    if not text.strip():
        return []
    return [{"type": "markdown", "content": text}]


def _first_nonblank_str(block: dict, field_order: tuple[str, ...]) -> str:
    for field in field_order:
        value = block.get(field)
        if not isinstance(value, str):
            continue
        if not value.strip():
            continue
        return value
    return ""


def _meaningful_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if text.strip() else ""


def _normalize_display_block(block: dict) -> dict | None:
    if not isinstance(block, dict):
        return None

    raw_type = block.get("type")
    if not isinstance(raw_type, str):
        return None

    block_type = raw_type.strip().lower()
    if not block_type:
        return None

    text_fields = ("content", "text", "message", "output", "result", "value")

    if block_type == "markdown":
        content = _first_nonblank_str(block, text_fields)
        if not content:
            return None
        return {"type": "markdown", "content": content}

    if block_type == "code":
        code_text = _first_nonblank_str(block, ("content", "code", "text", "output", "result", "value"))
        if not code_text:
            return None
        normalized = {"type": "code", "content": code_text}
        lang = block.get("lang")
        if not isinstance(lang, str) or not lang.strip():
            lang = block.get("language")
        if isinstance(lang, str) and lang.strip():
            normalized["lang"] = lang
        return normalized

    if block_type == "tool_result":
        content = _first_nonblank_str(block, text_fields)
        if not content:
            return None
        normalized = {"type": "tool_result", "content": content}
        title = block.get("title")
        if isinstance(title, str) and title != "":
            normalized["title"] = title
        status = block.get("status")
        if isinstance(status, str) and status != "":
            normalized["status"] = status
        return normalized

    if block_type == "callout":
        content = _first_nonblank_str(block, text_fields)
        if not content:
            return None
        normalized = {"type": "callout", "content": content}
        title = block.get("title")
        if isinstance(title, str) and title != "":
            normalized["title"] = title
        tone = block.get("tone")
        if isinstance(tone, str) and tone != "":
            normalized["tone"] = tone
        return normalized

    if block_type == "table":
        headers = block.get("headers")
        if not isinstance(headers, list):
            headers = block.get("columns")
        headers = headers if isinstance(headers, list) else []
        rows = block.get("rows")
        rows = rows if isinstance(rows, list) else []
        fallback_text = _first_nonblank_str(block, text_fields)
        if not headers and not rows:
            if not fallback_text:
                return None
            return {"type": "markdown", "content": fallback_text}
        normalized = {"type": "table", "headers": headers, "rows": rows}
        if fallback_text:
            normalized["content"] = fallback_text
        return normalized

    fallback_text = _first_nonblank_str(block, text_fields)
    if not fallback_text:
        return None
    return {"type": "markdown", "content": fallback_text}


def normalize_display_blocks(raw_blocks, fallback_text: str = "") -> list[dict]:
    if not isinstance(raw_blocks, list):
        return build_markdown_display_blocks(fallback_text)

    normalized: list[dict] = []
    for block in raw_blocks:
        parsed = _normalize_display_block(block)
        if parsed is not None:
            normalized.append(parsed)

    if normalized:
        return normalized
    return build_markdown_display_blocks(fallback_text)


def normalize_assistant_chat_payload(data: dict, fallback_session_id: str = "") -> dict:
    if not isinstance(data, dict):
        data = {}

    assistant_message = _meaningful_text(data.get("response")) or _meaningful_text(data.get("content"))
    display_blocks = normalize_display_blocks(data.get("display_blocks"), assistant_message)

    if not assistant_message and not display_blocks:
        assistant_message = "(empty response)"

    events = data.get("events")
    if not isinstance(events, list):
        events = []

    return {
        "assistant_message": assistant_message,
        "display_blocks": display_blocks,
        "events": events,
        "session_id": data.get("session_id") or fallback_session_id or "",
        "user_message_id": data.get("user_message_id") or "",
    }
