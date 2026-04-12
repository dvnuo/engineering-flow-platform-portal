from app.chat_payloads import normalize_assistant_chat_payload


def test_normalize_assistant_chat_payload_prefers_response_over_content():
    payload = normalize_assistant_chat_payload(
        {
            "response": "from response",
            "content": "from content",
            "display_blocks": [],
        }
    )
    assert payload["assistant_message"] == "from response"


def test_normalize_assistant_chat_payload_uses_content_when_response_missing():
    payload = normalize_assistant_chat_payload({"content": "from content"})
    assert payload["assistant_message"] == "from content"
    assert payload["display_blocks"] == []


def test_normalize_assistant_chat_payload_block_only_keeps_empty_assistant_message():
    payload = normalize_assistant_chat_payload(
        {
            "response": "",
            "display_blocks": [
                {"type": "tool_result", "title": "Bash", "status": "success", "output": "done"}
            ],
        }
    )
    assert payload["assistant_message"] == ""


def test_normalize_assistant_chat_payload_empty_payload_uses_placeholder():
    payload = normalize_assistant_chat_payload({})
    assert payload["assistant_message"] == "(empty response)"


def test_normalize_assistant_chat_payload_field_fallbacks_and_type_safety():
    payload = normalize_assistant_chat_payload(
        {
            "events": "not-a-list",
            "display_blocks": "not-a-list",
            "session_id": "",
            "user_message_id": None,
        },
        fallback_session_id="fallback-session",
    )
    assert payload["events"] == []
    assert payload["display_blocks"] == []
    assert payload["session_id"] == "fallback-session"
    assert payload["user_message_id"] == ""
