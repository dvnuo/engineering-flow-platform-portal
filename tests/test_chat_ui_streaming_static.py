from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src():
    return SRC.read_text(encoding="utf-8")


def test_native_legacy_chat_streaming_contract_remains_available():
    src = _src()
    submit_stream = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")

    assert "`/a/${agentIdAtSend}/api/chat/stream`" in submit_stream
    assert "getReader()" in submit_stream
    assert "requestCtx.usedStream = true" in submit_stream
    assert "handleChatStreamEvent(agentIdAtSend, requestCtx" in submit_stream
    assert "fetch(`/a/${agentIdAtSend}/api/chat`" in submit_chat
    assert "/api/tasks" not in submit_stream
    assert "/api/tasks" not in submit_chat
    assert ":4096" not in src


def test_chat_submit_uses_native_stream_without_opencode_preflight_or_simple_bypass():
    src = _src()
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")
    submit_stream = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")

    guard_idx = submit_chat.index('guardNoActiveChatRequestForAgent(agentIdAtSend, "send another message")')
    stream_idx = submit_chat.index("trySubmitChatStreamForSelectedAgent")
    fallback_idx = submit_chat.index("fetch(`/a/${agentIdAtSend}/api/chat`")
    assert guard_idx < stream_idx < fallback_idx

    assert "preflight" + "ActiveRunForSession" not in submit_chat
    assert "agentUsesThinOpenCodeChat" not in submit_chat
    assert "agentUsesThinOpenCodeChat" not in submit_stream


def test_opencode_long_task_markers_are_removed_from_chat_ui():
    src = _src()
    forbidden = [
        "chat_run" + "_already_active",
        "handleChatRun" + "AlreadyActive",
        "startChatRun" + "ReconcileLoop",
        "reconcileChatRun" + "Once",
        "stream" + "Detached",
        "wait_reconnect" + "_or_stop",
        "/active-run",
        "/api/chat/runs",
        "Previous message" + " still running",
        "Still running. Reconnecting",
    ]

    for marker in forbidden:
        assert marker not in src


def test_opencode_simple_mount_helpers_are_not_reintroduced():
    src = _src()

    assert "syncOpenCodeChatRootForAgent" not in src
    assert "getOpenCodeChatUiMode" not in src
    assert "agentUsesThinOpenCodeChat" not in src


def test_missing_final_native_stream_finishes_as_incomplete_without_reconnect():
    src = _src()
    missing_final = _extract_js_function(src, "handleChatStreamMissingFinal")

    assert 'handleIncompleteChatStream(agentIdAtSend, requestCtx, "missing_final"' in missing_final
    assert "handleChatStreamDetached" not in src
    assert "Reconnecting" not in missing_final
