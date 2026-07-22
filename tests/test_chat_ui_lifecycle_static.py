from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path('app/static/js/chat_ui.js')


def _src():
    return SRC.read_text(encoding='utf-8')


def test_has_active_chat_request_uses_only_local_submit_state():
    src = _src()
    body = _extract_js_function(src, "hasActiveChatRequestForAgent")
    assert "chatState?.isSubmitting" in body
    assert 'isOpenCodeSessionInactivePayload(payload)' not in body
    assert 'isOpenCodeSessionBlocking(chatState)' not in body
    assert 'chatState.inflightEventStream && chatState.inflightEventStream.completed === false' not in body
    assert 'hasIncompleteInflightThinking(chatState)' not in body


def test_terminal_thinking_cleanup_clears_busy_state():
    src = _src()
    body = _extract_js_function(src, "finalizeTerminalStreamState")
    assert 'chatState.inflightEventStream.completed = true' in body
    assert 'chatState.inflightEventStream = null' in body
    assert 'chatState.currentRequest?.clientRequestId === requestCtx?.clientRequestId' in body
    assert 'chatState.currentRequest = null' in body
    assert 'chatState.isSubmitting = false' in body
    assert 'clearWaitingForRuntimeEventsTimer(requestCtx)' in body
    assert 'syncSelectedAgentChatActionControls()' in body


def test_cleanup_chat_stream_request_uses_terminal_cleanup_and_clears_request_flags():
    src = _src()
    body = _extract_js_function(src, "cleanupChatStreamRequest")
    assert 'finalizeTerminalStreamState(agentIdAtSend, requestCtx' in body
    assert 'setChatSubmittingForAgent(agentIdAtSend, false)' in body
    assert 'chatState.currentRequest = null' in body


def test_non_success_and_error_paths_set_terminal_status():
    src = _src()
    non_success = _extract_js_function(src, "finalizeNonSuccessChatResponse")
    failure = _extract_js_function(src, "handleAgentChatFailure")
    status = _extract_js_function(src, "setTerminalCompletionStatus")
    assert 'setTerminalCompletionStatus(finalPayload)' in non_success
    assert 'setTerminalCompletionStatus(finalPayload)' in failure
    for marker in [
        'Blocked',
        'Incomplete',
        'Error',
        'Empty final response',
        'Finished with non-success state',
    ]:
        assert marker in status


def test_stream_error_source_marks_stream_failed():
    src = _src()
    helper = _extract_js_function(src, "finalizeNonSuccessChatResponse")
    assert '"stream_error"' in helper
    assert '"runtime_error"' in helper
    assert "requestCtx.streamFailed = true" in helper
    assert "requestCtx.streamIncomplete = true" in helper
    assert helper.index("failureSources.has(source)") < helper.index("requestCtx.streamIncomplete = true")


def test_error_path_clears_active_request_is_submitting_and_inflight_thinking():
    src = _src()
    failure = _extract_js_function(src, "handleAgentChatFailure")
    terminal = _extract_js_function(src, "finalizeTerminalStreamState")
    assert 'requestCtx.streamFailed = true' in failure
    assert 'requestCtx.terminalPayload = finalPayload' in failure
    assert 'finalizeTerminalStreamState(agentIdAtSend, requestCtx, finalPayload)' in failure
    assert 'chatState.currentRequest = null' in terminal
    assert 'chatState.isSubmitting = false' in terminal
    assert 'chatState.inflightEventStream = null' in terminal


def test_completed_success_and_fallback_paths_clear_busy_state():
    src = _src()
    success = _extract_js_function(src, "handleAgentChatSuccess")
    submit = _extract_js_function(src, "submitChatForSelectedAgent")
    assert 'chatState.currentRequest = null' in success
    assert 'chatState.inflightEventStream = null' in success
    assert 'setChatSubmittingForAgent(agentIdAtSend, false)' in success
    assert 'setChatStatus("Ready")' in success
    assert 'finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, payload, "fallback")' in submit
    assert 'await handleAgentChatSuccess(agentIdAtSend, requestCtx' in submit


def test_missing_final_guard_respects_stream_failed():
    src = _src()
    missing_final = _extract_js_function(src, "handleChatStreamMissingFinal")
    for marker in [
        "requestCtx?.streamFailed",
        "requestCtx?.streamIncomplete",
        "requestCtx?.streamCompleted",
        "requestCtx?.sawError",
    ]:
        assert marker in missing_final
    guard_index = missing_final.index("return \"handled\";")
    incomplete_index = missing_final.index("handleIncompleteChatStream(")
    assert guard_index < incomplete_index
    assert "handleChatStreamDetached" not in src
