from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src():
    return SRC.read_text(encoding="utf-8")


def test_opencode_chat_mode_accepts_thin_and_simple():
    src = _src()
    mode_fn = _extract_js_function(src, "getOpenCodeChatUiMode")
    agent_mode_fn = _extract_js_function(src, "agentUsesThinOpenCodeChat")

    assert 'configured === "thin" || configured === "simple"' in mode_fn
    assert 'mode === "thin" || mode === "simple"' in agent_mode_fn


def test_selecting_opencode_runtime_mounts_simple_root_and_hides_legacy_chat():
    src = _src()
    sync_fn = _extract_js_function(src, "syncOpenCodeChatRootForAgent")

    assert 'root.dataset.agentId = nextAgentId' in sync_fn
    assert 'root.dataset.runtimeType = runtimeType' in sync_fn
    assert 'root.dataset.chatMode = mode' in sync_fn
    assert 'root.classList.toggle("hidden", !useThin)' in sync_fn
    assert 'legacyRoot?.classList.toggle("hidden", useThin)' in sync_fn
    assert 'runtimeType === "opencode"' in sync_fn
    assert 'mode === "thin" || mode === "simple"' in sync_fn


def test_opencode_simple_mode_does_not_enter_legacy_submit_or_stream_paths():
    src = _src()
    submit_fn = _extract_js_function(src, "submitChatForSelectedAgent")
    stream_fn = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")

    guard_idx = submit_fn.index("if (localAgentUsesThinOpenCodeChat(selectedAgent))")
    preflight_idx = submit_fn.index("preflightActiveRunForSession")
    stream_idx = submit_fn.index("trySubmitChatStreamForSelectedAgent")
    post_idx = submit_fn.index("fetch(`/a/${agentIdAtSend}/api/chat`")
    assert guard_idx < preflight_idx < stream_idx < post_idx

    stream_guard_idx = stream_fn.index("if (localAgentUsesThinOpenCodeChat(localGetAgentById(agentIdAtSend))) return \"unsupported\";")
    stream_post_idx = stream_fn.index("fetch(`/a/${agentIdAtSend}/api/chat/stream`")
    assert stream_guard_idx < stream_post_idx


def test_opencode_simple_mode_skips_active_run_preflight_and_stop_button():
    src = _src()
    preflight_fn = _extract_js_function(src, "preflightActiveRunForSession")
    abort_visibility_fn = _extract_js_function(src, "shouldShowAbortChatRunButton")

    assert preflight_fn.strip() == "async function preflightActiveRunForSession(agentId, sessionId) {\n  return false;\n}"
    assert 'if (agentUsesThinOpenCodeChat(getAgentById(agentId))) return false;' in abort_visibility_fn


def test_native_runtime_still_uses_legacy_chat_form():
    src = _src()

    assert 'document.getElementById("chat-form")?.addEventListener("submit"' in src
    assert "await submitChatForSelectedAgent();" in src
    assert 'if (agentUsesThinOpenCodeChat()) return;' in src
    assert "`/a/${agentIdAtSend}/api/chat/stream`" in src
    assert "fetch(`/a/${agentIdAtSend}/api/chat`" in src
