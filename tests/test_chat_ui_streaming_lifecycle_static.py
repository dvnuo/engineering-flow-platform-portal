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


def test_reconcile_lifecycle_clears_inactive_active_run_null_and_stale_runs():
    src = _src()
    reconcile_once = _extract_js_function(src, "reconcileChatRunOnce")
    session_clear = _extract_js_function(src, "applySessionProjectionThenClearStaleRun")
    reconcile_projection = _extract_js_function(src, "reconcileActiveRequestProjection")

    assert '`/api/chat/runs/${encodeURIComponent(requestId)}?validate=opencode`' in reconcile_once
    assert '`/api/sessions/${encodeURIComponent(sessionId)}/active-run`' in reconcile_once
    assert '"chat.run.stale"' in reconcile_once
    assert '"opencode.status.inactive"' in reconcile_once
    assert 'activeRun ? "opencode_not_active" : "active_run_null"' in reconcile_once
    assert 'metadata: {' in session_clear
    assert 'active_run: null' in session_clear
    assert 'clearStaleActiveRequest(agentId, requestCtx, reason)' in session_clear
    assert 'activeRun ? "opencode_not_active" : "metadata_active_run_null"' in reconcile_projection


def test_user_abort_and_agent_lifecycle_clear_active_request():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    action_fn = _extract_js_function(src, "action")
    parser = _extract_js_function(src, "parseAgentLifecycleAction")

    assert 'setChatStatus("Stopping current run…")' in abort_fn
    assert '`/api/chat/runs/${encodeURIComponent(requestId)}/abort`' in abort_fn
    assert '`/api/sessions/${encodeURIComponent(sessionId)}/abort`' in abort_fn
    assert "if (!runtimeAbortSucceeded(result))" in abort_fn
    assert "runtimeAbortIndicatesInactive(result)" in abort_fn
    assert 'clearStaleActiveRequest(agentId, requestCtx, result?.stale ? "opencode_session_missing_after_abort" : "user_aborted")' in abort_fn
    assert "parseAgentLifecycleAction(path)" in action_fn
    assert "clearStaleActiveRequest(" in action_fn
    assert '"agent_stopped"' in action_fn
    assert '"agent_restarted"' in action_fn
    assert "loadSessionForAgent(lifecycle.agentId, chatState.sessionId" in action_fn
    assert "(stop|restart)" in parser


def test_abort_failed_keeps_active_request_and_reconcile_continues():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    failure_start = abort_fn.index("if (!runtimeAbortSucceeded(result))")
    failure_end = abort_fn.index("return;", failure_start)
    failure_branch = abort_fn[failure_start:failure_end]

    assert '"portal.abort.failed"' in failure_branch
    assert 'setChatStatus("Unable to stop current run.", true)' in failure_branch
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in failure_branch
    assert "syncSelectedAgentChatActionControls()" in failure_branch
    assert "clearStaleActiveRequest" not in failure_branch


def test_abort_success_clears_only_when_runtime_indicates_inactive():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    success_start = abort_fn.index("appendPortalChatRuntimeEvent(agentId, requestCtx, \"portal.abort.completed\"")
    success_branch = abort_fn[success_start:]

    assert "if (runtimeAbortIndicatesInactive(result))" in success_branch
    assert 'clearStaleActiveRequest(agentId, requestCtx, result?.stale ? "opencode_session_missing_after_abort" : "user_aborted")' in success_branch
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in success_branch


def test_abort_missing_session_clears_as_stale():
    src = _src()
    inactive_helper = _extract_js_function(src, "runtimeAbortIndicatesInactive")
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")

    assert "missing_session_ids" in inactive_helper
    assert "!(abortResult.errors || []).length" in inactive_helper
    assert '"opencode_session_missing_after_abort"' in abort_fn
