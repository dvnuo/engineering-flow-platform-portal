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


def test_portal_proxy_streams_opencode_conversation_events():
    proxy_src = Path("app/api/proxy.py").read_text(encoding="utf-8")

    assert 'normalized.startswith("api/opencode/conversations/")' in proxy_src
    assert 'normalized.endswith("/events")' in proxy_src
