from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src():
    return SRC.read_text(encoding="utf-8")


def test_missing_final_lifecycle_finishes_native_stream_as_incomplete():
    src = _src()
    missing_final = _extract_js_function(src, "handleChatStreamMissingFinal")

    assert 'handleIncompleteChatStream(agentIdAtSend, requestCtx, "missing_final"' in missing_final
    assert 'return "handled";' in missing_final
    assert "handleChatStreamDetached" not in src
    assert "Reconnecting" not in missing_final


def test_reconcile_loop_and_run_lookup_are_removed():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")

    assert "startChatRun" + "ReconcileLoop" not in src
    assert "reconcileChatRun" + "Once" not in src
    assert "/api/chat/" + "runs" not in abort_fn
    assert "/active" + "-run" not in abort_fn
    assert "refreshOpenCodeSessionStatusForAgent" not in abort_fn


def test_stop_control_only_follows_local_submit_state():
    src = _src()
    abort_visibility = _extract_js_function(src, "shouldShowAbortChatRunButton")

    assert "agentUsesThinOpenCodeChat" not in abort_visibility
    assert "isSubmitting" in abort_visibility
