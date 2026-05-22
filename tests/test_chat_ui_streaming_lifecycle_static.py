from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src():
    return SRC.read_text(encoding="utf-8")


def test_missing_final_detached_lifecycle_keeps_active_request_for_native_streams():
    src = _src()
    missing_final = _extract_js_function(src, "handleChatStreamMissingFinal")
    detached = _extract_js_function(src, "handleChatStreamDetached")

    assert 'return handleChatStreamDetached(agentIdAtSend, requestCtx, "missing_final"' in missing_final
    assert 'return "detached";' in detached
    assert "chatState.activeRequest = null" not in detached
    assert "chatState.inflightThinking = null" not in detached
    assert 'setChatStatus("Still running. Syncing…")' in detached
    assert "Reconnecting" not in detached


def test_reconcile_no_longer_uses_opencode_run_lookup_or_active_run_endpoints():
    src = _src()
    reconcile_once = _extract_js_function(src, "reconcileChatRunOnce")
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")

    assert "/api/chat/runs" not in reconcile_once
    assert "/active-run" not in reconcile_once
    assert "/api/chat/runs" not in abort_fn
    assert "agentApiFor(agentId, `/api/sessions/${encodeURIComponent(sessionId)}`)" in reconcile_once


def test_open_code_simple_mode_stop_control_is_not_shown():
    src = _src()
    abort_visibility = _extract_js_function(src, "shouldShowAbortChatRunButton")

    assert "agentUsesThinOpenCodeChat(getAgentById(agentId))" in abort_visibility
    assert "return false" in abort_visibility
