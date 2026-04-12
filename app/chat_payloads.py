def normalize_assistant_chat_payload(data: dict, fallback_session_id: str = "") -> dict:
    if not isinstance(data, dict):
        data = {}

    display_blocks = data.get("display_blocks")
    if not isinstance(display_blocks, list):
        display_blocks = []

    assistant_message = data.get("response") or data.get("content") or ""
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
