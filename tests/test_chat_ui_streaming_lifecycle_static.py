from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path('app/static/js/chat_ui.js')


def _src():
    return SRC.read_text(encoding='utf-8')


def test_request_ctx_stream_lifecycle_fields_present():
    src = _src()
    for marker in [
        'requestId: clientRequestId',
        'streamStartedAt',
        'sawRuntimeEvent',
        'sawFinal',
        'streamCompleted',
        'streamFailed',
        'streamIncomplete',
    ]:
        assert marker in src


def test_stream_cleanup_and_fallback_runtime_events_markers_present():
    src = _src()
    assert 'finally {' in src
    assert 'setChatSubmittingForAgent(agentIdAtSend, false)' in src
    assert 'runtime_events: payload?.runtime_events || []' in src
    assert 'requestCtx.completed' not in src
    assert 'cleanupChatStreamRequest(' in src


def test_sse_parser_carries_buffer_and_catches_malformed_json():
    src = _src()
    chunk_parser = _extract_js_function(src, "parseSseEventsFromChunk")
    event_parser = _extract_js_function(src, "parseSseEvent")
    assert 'const merged = `${String(buffer || "")}${String(chunkText || "")}`' in chunk_parser
    assert 'return { events, buffer: remaining }' in chunk_parser
    assert "const rawData = dataLines.join('\\n')" in event_parser
    assert 'try { data = JSON.parse(rawData); } catch {}' in event_parser
    assert "return { eventName: eventName || 'message', data }" in event_parser


def test_stream_loop_handles_malformed_sse_without_leaving_busy_state():
    src = _src()
    stream_submit = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    detached = _extract_js_function(src, "handleChatStreamDetached")
    cleanup = _extract_js_function(src, "cleanupChatStreamRequest")
    assert 'const parsedBatch = parseSseEventsFromChunk(buffer, chunkText)' in stream_submit
    assert 'const parsed = parseSseEvent(buffer)' in stream_submit
    assert 'await handleChatStreamMissingFinal(agentIdAtSend, requestCtx)' in stream_submit
    assert 'requestCtx.streamDetached = true' in detached
    assert 'startChatRunReconcileLoop(agentIdAtSend, requestCtx' in detached
    assert 'clearWaitingForRuntimeEventsTimer(requestCtx)' in cleanup


def test_stream_loop_does_not_missing_final_after_error():
    src = _src()
    stream_submit = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    assert 'if (r === "error" || r === "final_non_success" || r === "final_incomplete") sawError = true;' in stream_submit
    assert "requestCtx.streamFailed" in stream_submit
    assert "sawError" in stream_submit
    assert stream_submit.index("requestCtx.streamFailed") < stream_submit.index("handleChatStreamMissingFinal(agentIdAtSend, requestCtx)")


def test_missing_final_detached_lifecycle_keeps_active_request_and_reconnects():
    src = _src()
    stream_submit = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    missing_final = _extract_js_function(src, "handleChatStreamMissingFinal")
    detached = _extract_js_function(src, "handleChatStreamDetached")

    assert 'return handleChatStreamDetached(agentIdAtSend, requestCtx, "missing_final"' in missing_final
    assert 'return "detached";' in stream_submit
    assert "chatState.activeRequest = null" not in detached
    assert "chatState.inflightThinking = null" not in detached
    assert "finalizeTerminalThinkingState" not in detached
    assert 'setChatStatus("Still running. Reconnecting…")' in detached
    assert '"portal.stream_detached"' in detached
    assert "ensureEventSocketForAgent(" in detached
    assert "startChatRunReconcileLoop(agentIdAtSend, requestCtx, { immediate: true })" in detached


def test_reconcile_lifecycle_finalizes_only_after_runtime_terminal_state():
    src = _src()
    apply_projection = _extract_js_function(src, "applyChatRunProjection")

    running_idx = apply_projection.index("isChatRunRunningStatus(projection.status)")
    completed_idx = apply_projection.index("isChatRunCompletedStatus(projection.status)")
    non_success_idx = apply_projection.index("isChatRunNonSuccessStatus(projection.status)")

    assert running_idx < completed_idx < non_success_idx
    assert 'setChatStatus(projection.status === "stream_detached" ? "Still running. Reconnecting…" : "Still running…")' in apply_projection
    assert "updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, { partial: true })" in apply_projection
    assert "updateOrCreateAssistantRowForRequest(agentId, requestCtx, finalPayload, { completed: true })" in apply_projection
    assert "await handleAgentChatSuccess(agentId, requestCtx, finalPayload)" in apply_projection
    assert "finalizeNonSuccessChatResponse(agentId, requestCtx, finalPayload, \"reconcile\")" in apply_projection
