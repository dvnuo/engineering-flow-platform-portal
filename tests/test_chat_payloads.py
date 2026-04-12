from app.chat_payloads import (
    build_markdown_display_blocks,
    normalize_assistant_chat_payload,
    normalize_display_blocks,
)


def test_normalize_assistant_chat_payload_prefers_meaningful_response_over_blank_content():
    payload = normalize_assistant_chat_payload(
        {
            "response": "hello",
            "content": "   ",
            "display_blocks": [],
        }
    )
    assert payload["assistant_message"] == "hello"


def test_normalize_assistant_chat_payload_builds_placeholder_only_when_text_and_blocks_absent():
    payload = normalize_assistant_chat_payload(
        {
            "response": "   ",
            "display_blocks": [{"type": "   "}],
        }
    )
    assert payload["assistant_message"] == "(empty response)"
    assert payload["display_blocks"] == []


def test_normalize_display_blocks_supports_code_field_alias():
    blocks = normalize_display_blocks(
        [{"type": "code", "code": "print(1)", "language": "python"}],
        "",
    )
    assert blocks[0]["type"] == "code"
    assert blocks[0]["content"] == "print(1)"
    assert blocks[0]["lang"] == "python"


def test_build_markdown_display_blocks_preserves_original_whitespace_when_meaningful():
    raw = "\n# Title\n\nBody\n"
    blocks = build_markdown_display_blocks(raw)
    assert blocks == [{"type": "markdown", "content": raw}]


def test_normalize_display_blocks_table_without_structure_falls_back_to_markdown():
    blocks = normalize_display_blocks([{"type": "table", "content": "fallback only"}], "")
    assert blocks == [{"type": "markdown", "content": "fallback only"}]


def test_normalize_assistant_chat_payload_uses_content_when_response_missing():
    payload = normalize_assistant_chat_payload({"content": "from content"})
    assert payload["assistant_message"] == "from content"
    assert payload["display_blocks"] == [{"type": "markdown", "content": "from content"}]


def test_normalize_assistant_chat_payload_events_and_session_fallbacks():
    payload = normalize_assistant_chat_payload(
        {"events": "bad", "session_id": "", "user_message_id": None},
        fallback_session_id="session-fallback",
    )
    assert payload["events"] == []
    assert payload["session_id"] == "session-fallback"
    assert payload["user_message_id"] == ""
