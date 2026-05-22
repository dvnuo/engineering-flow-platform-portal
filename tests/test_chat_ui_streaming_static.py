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


def test_opencode_simple_mode_bypasses_legacy_stream_submit_path():
    src = _src()
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")
    submit_stream = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")

    guard_idx = submit_chat.index("if (localAgentUsesThinOpenCodeChat(selectedAgent))")
    preflight_idx = submit_chat.index("preflightActiveRunForSession")
    stream_idx = submit_chat.index("trySubmitChatStreamForSelectedAgent")
    fallback_idx = submit_chat.index("fetch(`/a/${agentIdAtSend}/api/chat`")
    assert guard_idx < preflight_idx < stream_idx < fallback_idx

    stream_guard_idx = submit_stream.index(
        'if (localAgentUsesThinOpenCodeChat(localGetAgentById(agentIdAtSend))) return "unsupported";'
    )
    stream_post_idx = submit_stream.index("fetch(`/a/${agentIdAtSend}/api/chat/stream`")
    assert stream_guard_idx < stream_post_idx


def test_opencode_long_task_markers_are_removed_from_chat_ui():
    src = _src()
    forbidden = [
        "chat_run_already_active",
        "/active-run",
        "/api/chat/runs",
        "Previous message still running",
        "Still running. Reconnecting",
    ]

    for marker in forbidden:
        assert marker not in src


def test_opencode_simple_mount_hides_legacy_root_and_keeps_native_legacy_root():
    src = _src()
    sync_fn = _extract_js_function(src, "syncOpenCodeChatRootForAgent")
    mode_fn = _extract_js_function(src, "getOpenCodeChatUiMode")

    assert 'configured === "thin" || configured === "simple"' in mode_fn
    assert 'runtimeType === "opencode"' in sync_fn
    assert 'mode === "thin" || mode === "simple"' in sync_fn
    assert 'root.classList.toggle("hidden", !useThin)' in sync_fn
    assert 'legacyRoot?.classList.toggle("hidden", useThin)' in sync_fn
    assert 'root.dataset.runtimeType = runtimeType' in sync_fn
    assert 'root.dataset.chatMode = mode' in sync_fn


def test_detached_native_stream_uses_sync_status_without_reconnect_copy():
    src = _src()
    detached = _extract_js_function(src, "handleChatStreamDetached")

    assert 'setChatStatus("Still running. Syncing…")' in detached
    assert "Reconnecting" not in detached
    assert "chatState.activeRequest = null" not in detached
    assert '"portal.stream_detached"' in detached
