from pathlib import Path


SRC = Path("app/static/js/opencode_chat/api_client.js")


def test_api_client_uses_portal_opencode_proxy_paths_only():
    src = SRC.read_text(encoding="utf-8")

    assert "/api/opencode" in src
    assert "/conversations" in src
    assert "/health" in src
    assert "/send" in src
    assert "/abort" in src
    assert "/permissions/" in src
    assert "/children" in src
    assert "/todo" in src
    assert "/diff" in src
    assert "/fork" in src

    assert ":4096" not in src
    assert "/api/chat" not in src
    assert "/api/chat/stream" not in src
    assert "/api/tasks" not in src
    assert "EventSource" not in src
    assert "connectEvents" not in src


def test_api_client_keeps_sync_chat_endpoints():
    src = SRC.read_text(encoding="utf-8")

    assert "/conversations/${encodeURIComponent(conversationId)}/send" in src
    assert "/conversations/${encodeURIComponent(conversationId)}/messages" in src
