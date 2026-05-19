from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path('app/static/js/chat_ui.js')


def _src():
    return SRC.read_text(encoding='utf-8')


def test_chat_ui_streaming_contract_markers():
    src = _src()
    assert '/api/chat/stream' in src
    assert 'getReader()' in src
    for name in ['runtime_event', 'delta', 'final', 'done', 'error', 'heartbeat']:
        assert name in src
    for marker in ['completion_state', 'incomplete_reason', 'continuation_count', 'progress_preview']:
        assert marker in src
    assert 'portal.waiting_for_runtime_events' in src
    assert 'renderCompletionStateWarning' in src
    assert 'finalizeIncompleteAssistantRow' in src
    assert 'mergeFinalThinkingSnapshot' in src
    assert 'context_state' in src
    assert 'handleAgentEventMessage' in src
    assert 'requestCtx.usedStream = true' in src


def test_chat_ui_streaming_supports_opencode_runtime_event_types():
    src = _src()
    for event_type in [
        'chat.started',
        'chat.stream_attached',
        'chat.stream_detached',
        'chat.run.started',
        'chat.run.completed',
        'chat.run.incomplete',
        'chat.run.failed',
        'chat.run.abort_failed',
        'chat.run.stale',
        'chat.run.aborted',
        'assistant.message.started',
        'assistant.message.updated',
        'assistant.message.completed',
        'portal.stream_detached',
        'portal.reconcile.started',
        'portal.reconcile.updated',
        'portal.reconcile.completed',
        'portal.reconcile.failed',
        'portal.active_request.cleared',
        'portal.abort.started',
        'portal.abort.completed',
        'portal.abort.failed',
        'opencode.session.aborted',
        'opencode.session.abort_failed',
        'opencode.session.missing',
        'opencode.status.validated',
        'opencode.status.inactive',
        'heartbeat',
        'status',
        'llm_thinking',
        'message.delta',
        'tool.started',
        'tool.completed',
        'tool.failed',
        'tool_call',
        'tool_result',
        'permission_request',
        'permission_resolved',
        'permission.denied',
        'permission.allowed',
        'provider.retry',
        'provider.rate_limit',
        'model.retry',
        'continuation.started',
        'continuation.prompt_sent',
        'continuation.completed',
        'continuation.failed',
        'continuation.max_turns_reached',
        'continuation.wall_timeout',
        'continuation.no_progress',
        'continuation.suppressed',
        'chat.timeout_recovery.started',
        'chat.timeout_recovery.poll',
        'chat.timeout_recovery.recovered',
        'chat.timeout_recovery.exhausted',
        'chat.incomplete',
        'chat.failed',
        'final',
    ]:
        assert event_type in src


def test_runtime_event_aliases_and_dedup_are_handled_by_helpers():
    src = _src()
    alias_helper = _extract_js_function(src, "normalizeRuntimeEventTypeAlias")
    normalize_event = _extract_js_function(src, "normalizeRuntimeEvent")
    dedup_id = _extract_js_function(src, "runtimeEventUniqueId")
    dedup_key = _extract_js_function(src, "runtimeEventDedupKey")
    merge_events = _extract_js_function(src, "mergeThinkingEvents")
    handle_event = _extract_js_function(src, "handleAgentEventMessage")

    for marker in [
        '"continuation.no_progress_timeout": "continuation.no_progress"',
        '"chat.timeout_recovery.recovery_exhausted": "chat.timeout_recovery.exhausted"',
        '"timeout_recovery.started": "chat.timeout_recovery.started"',
        '"timeout_recovery.poll": "chat.timeout_recovery.poll"',
        '"timeout_recovery.recovered": "chat.timeout_recovery.recovered"',
        '"timeout_recovery.exhausted": "chat.timeout_recovery.exhausted"',
    ]:
        assert marker in alias_helper

    assert "normalizeRuntimeEventTypeAlias(rawTypeValue)" in normalize_event
    assert "runtime_event_id" in dedup_id
    assert "event?.runtime_event_id" in dedup_id
    assert "data.runtime_event_id" in dedup_id
    assert "runtimeEventSummaryHash(summary)" in dedup_key
    assert "const createdAt = event.created_at || data.created_at || event.ts || \"\"" in dedup_key
    assert "const eventType = normalizeRuntimeEventTypeAlias" in dedup_key
    assert "const localRuntimeEventDedupKey" in merge_events
    assert "localRuntimeEventDedupKey(event)" in merge_events
    assert "runtimeEventDedupKey(entry)" in handle_event


def test_chat_stream_heartbeat_updates_live_thinking_state():
    src = _src()
    stream_handler = _extract_js_function(src, "handleChatStreamEvent")
    assert 'outerType === "heartbeat"' in stream_handler
    assert 'requestCtx.lastHeartbeatAt = Date.now()' in stream_handler
    assert 'event_type: "heartbeat"' in stream_handler
    assert 'handleAgentEventMessage(JSON.stringify(heartbeatPayload)' in stream_handler


def test_chat_started_can_adopt_runtime_request_id():
    src = _src()
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    assert 'type === "chat.started"' in handle_event
    assert 'chatState.activeRequest.runtimeRequestId = entry.request_id' in handle_event
    assert 'chatState.activeRequest.requestId = entry.request_id' in handle_event
    assert 'ensureEventSocketForAgent(currentAgentId, entry.session_id || currentSessionId, entry.request_id)' in handle_event


def test_events_websocket_uses_replay_reconnect_and_dedup():
    src = _src()
    ensure_socket = _extract_js_function(src, "ensureEventSocketForAgent")
    merge_events = _extract_js_function(src, "mergeThinkingEvents")
    normalize_event = _extract_js_function(src, "normalizeRuntimeEvent")
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    assert 'params.set("replay", "1")' in ensure_socket
    assert 'last_event_at' in ensure_socket
    assert 'scheduleEventSocketReconnect(agentId, session, requestId || "")' in ensure_socket
    assert 'state.eventWsReconnectAttempt' in src
    assert 'metadata.replayed' in normalize_event
    assert 'event_id' in normalize_event
    assert 'runtime_event_id' in normalize_event
    assert 'localRuntimeEventDedupKey(event)' in merge_events
    assert 'const entryDedupKey = runtimeEventDedupKey(entry);' in handle_event


def test_chat_stream_main_path_does_not_use_tasks():
    src = _src()
    submit_stream = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")

    assert "`/a/${agentIdAtSend}/api/chat/stream`" in submit_stream
    assert "/api/tasks" not in submit_stream
    assert "/api/tasks" not in submit_chat
    assert "task mode" not in submit_stream.lower()


def test_active_request_busy_state_uses_runtime_blocking_helper():
    src = _src()
    has_active = _extract_js_function(src, "hasActiveChatRequestForAgent")
    blocking = _extract_js_function(src, "isActiveRequestBlocking")

    assert "isActiveRequestBlocking(chatState)" in has_active
    assert "hasIncompleteInflightThinking(chatState)" in has_active
    for marker in [
        "req.aborted",
        "req.stale",
        "req.completed",
        "req.failed",
        "req.runtimeInactive",
        "req.opencodeInactive",
    ]:
        assert marker in blocking
    assert "chatState.activeRequest" not in has_active.replace("isActiveRequestBlocking(chatState)", "")


def test_opencode_long_chat_does_not_use_task_mode_or_direct_opencode():
    src = _src()
    proxy_src = Path("app/api/proxy.py").read_text(encoding="utf-8")
    submit_stream = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")

    assert "`/a/${agentIdAtSend}/api/chat/stream`" in submit_stream
    for forbidden in [
        "/api/tasks",
        "task mode",
        ":4096",
        "localhost:4096",
        "127.0.0.1:4096",
    ]:
        assert forbidden not in submit_stream
    assert "/api/tasks" not in submit_chat
    assert ":4096" not in proxy_src
    assert "localhost:4096" not in proxy_src
    assert "127.0.0.1:4096" not in proxy_src
    assert "def _is_control_plane_only_runtime_path" in proxy_src
    assert "if _is_control_plane_only_runtime_path(subpath):" in proxy_src
    assert "Runtime internal endpoints are not exposed via the user-facing Portal proxy." in proxy_src


def test_runtime_events_append_to_live_timeline_before_final():
    src = _src()
    trackable = _extract_js_function(src, "isTrackableThinkingEvent")
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    render_panel = _extract_js_function(src, "renderThinkingPanelFromClientState")
    display = _extract_js_function(src, "getThinkingEventDisplay")
    for event_type in [
        "continuation.prompt_sent",
        "continuation.no_progress",
        "continuation.suppressed",
        "chat.timeout_recovery.exhausted",
        "final",
    ]:
        assert event_type in trackable
    assert '"continuation.suppressed"' in display
    assert "Continuation suppressed" in display
    assert "data.metadata?.reason" in display
    assert "chatState.inflightThinking.events.push(entry)" in handle_event
    terminal_clause = handle_event[handle_event.rfind('if (type === "execution.completed"'):]
    assert "continuation.suppressed" not in terminal_clause
    assert "visibleEvents.map((event) =>" in render_panel
    assert "getThinkingEventDisplay(event)" in render_panel
    assert "portal-completion-banner" in render_panel


def test_live_thinking_panel_renders_status_banner_and_safe_details():
    src = _src()
    render_panel = _extract_js_function(src, "renderThinkingPanelFromClientState")
    assert "Thinking Process Live" in render_panel
    assert "portal-live-status" in render_panel
    assert "Elapsed:" in render_panel
    assert "Last event:" in render_panel
    assert "portal-completion-banner" in render_panel
    assert 'You can send "continue"' in render_panel
    assert "Still running. Live events will continue to appear here." in render_panel
    assert "Historical" in render_panel
    assert "portal-event-detail" in render_panel
    assert "sanitizeEventDetailPayload" in render_panel


def test_live_thinking_detail_sanitizer_redacts_secret_fields():
    src = _src()
    sanitizer = _extract_js_function(src, "sanitizeEventDetailPayload")
    secret_name = _extract_js_function(src, "isSecretEventFieldName")
    assert "authorization" in secret_name
    assert "api_key" in secret_name
    assert '"[redacted]"' in sanitizer


def test_chat_ui_streaming_does_not_use_eventsource_for_chat_stream():
    src = _src()
    assert 'EventSource(' not in src


def test_runtime_event_unwrap_preserves_embedded_type_and_stream_event_marker():
    src = _src()
    stream_handler = _extract_js_function(src, "handleChatStreamEvent")
    assert 'eventData?.data?.type' in src
    assert 'type: embeddedType || "runtime_event"' in src
    assert 'event_type: embeddedType || "runtime_event"' in src
    assert 'stream_event:' in src
    assert 'eventData.type || eventData.event_type || eventData?.data?.type || eventData?.data?.event_type || eventData.event' in stream_handler
    assert 'handleAgentEventMessage(JSON.stringify(streamEventPayload)' in stream_handler


def test_request_ctx_and_fallback_non_success_markers():
    src = _src()
    for marker in ['sawRuntimeEvent', 'sawDelta', 'sawFinal', 'streamCompleted', 'streamFailed', 'streamIncomplete']:
        assert marker in src
    assert 'finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, payload, "fallback")' in src
    assert 'runtime_events: payload?.runtime_events || []' in src


def test_finalize_incomplete_row_uses_safe_nested_markdown_container():
    src = _src()
    body = _extract_js_function(src, "finalizeIncompleteAssistantRow")
    assert 'markdownEl.className = "message-markdown max-w-none text-sm"' in body
    assert 'markdownEl.className = "message-markdown md-render' not in body
    assert 'warningBlock.className = "chat-completion-warning-block"' in body
    assert 'responseEl.className = "chat-incomplete-response md-render"' in body
    assert 'responseEl.dataset.md = responseText || "No final assistant response was returned. See Thinking Process for runtime events."' in body
    assert 'responseEl.dataset.displayBlocks = "[]"' in body
    assert 'renderMarkdown(responseEl.parentElement)' in body
    assert 'renderMarkdown(article)' not in body
    assert 'article.dataset.finalizedIncomplete = "1"' in body
    assert 'article.dataset.pendingAssistant = "0"' in body


def test_non_success_final_flow_is_unified_and_preserves_diagnostic_row():
    src = _src()
    helper = _extract_js_function(src, "finalizeNonSuccessChatResponse")
    assert 'finalizeIncompleteAssistantRow(agentId, requestCtx, finalPayload)' in helper
    assert 'mergeFinalThinkingSnapshot(agentId, requestCtx, finalPayload)' in helper
    assert 'finalizeTerminalThinkingState(agentId, requestCtx, finalPayload)' in helper
    assert 'setTerminalCompletionStatus(finalPayload)' in helper
    assert 'cleanupChatStreamRequest(agentId, requestCtx, { keepStatus: true })' in helper
    assert 'removeTemporaryAssistantRows' not in helper

    stream_handler = _extract_js_function(src, "handleChatStreamEvent")
    assert 'finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, eventData, "stream_final")' in stream_handler
    assert 'finalizeIncompleteAssistantRow(agentIdAtSend, requestCtx, eventData)' not in stream_handler


def test_non_success_diagnostic_fields_are_rendered_with_escaping():
    src = _src()
    body = _extract_js_function(src, "renderCompletionDiagnosticFields")
    for marker in [
        'completion_state',
        'incomplete_reason',
        'continuation_count',
        'progress_preview',
        'context_state',
        'contextState.summary',
        'contextState.current_state',
        'contextState.next_step',
        'escapeHtml(label)',
        'escapeHtml(String(value))',
    ]:
        assert marker in body


def test_stream_error_event_is_terminal():
    src = _src()
    stream_handler = _extract_js_function(src, "handleChatStreamEvent")
    for marker in [
        'outerType === "error"',
        'requestCtx.sawError = true',
        'requestCtx.streamFailed = true',
        'completion_state',
        'incomplete_reason',
        'finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, finalPayload, "stream_error")',
        'return "error"',
    ]:
        assert marker in stream_handler


def test_missing_final_detaches_instead_of_terminal_incomplete():
    src = _src()
    missing_final = _extract_js_function(src, "handleChatStreamMissingFinal")
    detached = _extract_js_function(src, "handleChatStreamDetached")
    stream_submit = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")

    assert "handleIncompleteChatStream" not in missing_final
    assert "handleChatStreamDetached(agentIdAtSend, requestCtx" in missing_final
    assert "requestCtx.streamDetached = true" in detached
    assert "requestCtx.streamIncomplete = false" in detached
    assert "requestCtx.streamFailed = false" in detached
    assert "finalizeTerminalThinkingState" not in detached
    assert "chatState.activeRequest = null" not in detached
    assert 'setChatStatus("Still running. Reconnecting…")' in detached
    assert '"portal.stream_detached"' in detached
    assert "ensureEventSocketForAgent(" in detached
    assert "startChatRunReconcileLoop(agentIdAtSend, requestCtx" in detached
    assert 'return "detached";' in stream_submit
    assert 'if (streamResult === "detached") return;' in submit_chat
    assert submit_chat.index('if (streamResult === "detached") return;') < submit_chat.index('fetch(`/a/${agentIdAtSend}/api/chat`')


def test_reconcile_loop_contract_and_runtime_paths():
    src = _src()
    start_loop = _extract_js_function(src, "startChatRunReconcileLoop")
    stop_loop = _extract_js_function(src, "stopChatRunReconcileLoop")
    reconcile_once = _extract_js_function(src, "reconcileChatRunOnce")
    apply_projection = _extract_js_function(src, "applyChatRunProjection")

    assert "requestCtx.reconcileTimerId" in start_loop
    assert "requestCtx.reconcileAttempt" in start_loop
    assert "requestCtx.lastReconcileAt" in reconcile_once
    assert "6 * 60 * 60 * 1000" in start_loop
    assert "document.hidden" in start_loop
    assert "clearTimeout(requestCtx.reconcileTimerId)" in stop_loop
    assert '`/api/chat/runs/${encodeURIComponent(requestId)}?validate=opencode`' in reconcile_once
    assert '`/api/sessions/${encodeURIComponent(sessionId)}/active-run`' in reconcile_once
    assert '`/api/sessions/${encodeURIComponent(sessionId)}`' in reconcile_once
    assert "isUnsupportedRunLookupError(error)" in reconcile_once
    assert "isRuntimeRunActuallyActive" in reconcile_once
    assert "applySessionProjectionThenClearStaleRun" in reconcile_once
    assert "clearStaleActiveRequest(agentId, requestCtx" in src
    assert 'setChatStatus(projection.status === "stream_detached" ? "Still running. Reconnecting…" : "Still running…")' in apply_projection
    assert "updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, { partial: true })" in apply_projection
    assert "handleAgentChatSuccess(agentId, requestCtx, finalPayload)" in apply_projection
    assert "finalizeNonSuccessChatResponse(agentId, requestCtx, finalPayload, \"reconcile\")" in apply_projection


def test_reconcile_clears_runtime_inactive_runs_and_stream_detached_without_opencode_active():
    src = _src()
    apply_projection = _extract_js_function(src, "applyChatRunProjection")
    runtime_active = _extract_js_function(src, "isRuntimeRunActuallyActive")
    clear_stale = _extract_js_function(src, "clearStaleActiveRequest")

    assert 'status === "stream_detached" && run.opencode_active !== true' in runtime_active
    assert "return false" in runtime_active
    assert "isChatRunRunningStatus(projection.status) && !runtimeRunActive" in apply_projection
    assert '"opencode.status.inactive"' in apply_projection
    assert 'clearStaleActiveRequest(agentId, requestCtx, "opencode_not_active")' in apply_projection
    assert "chatState.activeRequest = null" in clear_stale
    assert "chatState.inflightThinking.completed = true" in clear_stale
    assert "chatState.inflightThinking.stale = true" in clear_stale
    assert '"portal.active_request.cleared"' in clear_stale


def test_abort_active_chat_request_uses_runtime_abort_endpoints():
    src = _src()
    template = Path("app/templates/app.html").read_text(encoding="utf-8")
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    sync_controls = _extract_js_function(src, "syncSelectedAgentChatActionControls")

    assert "function abortActiveChatRequestForSelectedAgent" in src
    assert '`/api/chat/runs/${encodeURIComponent(requestId)}/abort`' in abort_fn
    assert '`/api/sessions/${encodeURIComponent(sessionId)}/abort`' in abort_fn
    assert "runtimeAbortSucceeded(result)" in abort_fn
    assert "runtimeAbortIndicatesInactive(result)" in abort_fn
    assert 'clearStaleActiveRequest(agentId, requestCtx, result?.stale ? "opencode_session_missing_after_abort" : "user_aborted")' in abort_fn
    assert '"portal.abort.started"' in abort_fn
    assert '"portal.abort.completed"' in abort_fn
    assert '"portal.abort.failed"' in abort_fn
    assert "/api/tasks" not in abort_fn
    assert ":4096" not in abort_fn
    assert 'id="abort-chat-run-btn"' in template
    assert "Stop run" in template
    assert "shouldShowAbortChatRunButton(agentId)" in sync_controls


def test_abort_active_chat_request_checks_runtime_abort_result():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    success_helper = _extract_js_function(src, "runtimeAbortSucceeded")
    inactive_helper = _extract_js_function(src, "runtimeAbortIndicatesInactive")

    assert "result.success === false" in success_helper
    assert "abortResult.success === false" in success_helper
    assert "Array.isArray(abortResult.errors) && abortResult.errors.length" in success_helper
    assert "result.success === true || abortResult.success === true || result.stale === true" in success_helper
    assert "missing_session_ids" in inactive_helper
    assert '"aborted", "stale", "completed", "incomplete", "failed", "cancelled", "canceled"' in inactive_helper
    assert "if (!runtimeAbortSucceeded(result))" in abort_fn
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in abort_fn


def test_abort_failure_does_not_clear_active_request():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    failure_start = abort_fn.index("if (!runtimeAbortSucceeded(result))")
    failure_end = abort_fn.index("return;", failure_start)
    failure_branch = abort_fn[failure_start:failure_end]

    assert '"portal.abort.failed"' in failure_branch
    assert 'setChatStatus("Unable to stop current run.", true)' in failure_branch
    assert "showToast(\"Unable to stop current run: \" + message)" in failure_branch
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in failure_branch
    assert "syncSelectedAgentChatActionControls()" in failure_branch
    assert "clearStaleActiveRequest" not in failure_branch


def test_remove_temporary_assistant_rows_is_request_scoped_and_content_safe():
    src = _src()
    helper = _extract_js_function(src, "removeTemporaryAssistantRows")
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")
    update_pending = _extract_js_function(src, "updatePendingAssistantStreamContent")
    update_or_create = _extract_js_function(src, "updateOrCreateAssistantRowForRequest")

    assert "options = {}" in helper
    assert "options.requestId || options.clientRequestId" in helper
    assert "options.forceAll === true" in helper
    assert "options.onlyEmpty !== false" in helper
    assert "assistantRowMatchesRequest(row, requestId)" in helper
    assert "assistantArticleHasVisibleContent(article)" in helper
    assert "removeTemporaryAssistantRows({ requestId: clientRequestId, onlyEmpty: true })" in submit_chat
    assert 'article.dataset.hasVisibleContent = "1"' in update_pending
    assert 'row.dataset.hasVisibleContent = "1"' in update_or_create


def test_events_update_assistant_bubble_and_do_not_show_hidden_reasoning():
    src = _src()
    trackable = _extract_js_function(src, "isTrackableThinkingEvent")
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    predicate = _extract_js_function(src, "isAssistantMessageRuntimeEvent")
    assistant_handler = _extract_js_function(src, "handleAssistantMessageRuntimeEvent")
    guard = _extract_js_function(src, "shouldIgnoreAssistantStreamDelta")

    for event_type in [
        "assistant.message.started",
        "assistant.message.updated",
        "assistant.message.completed",
        "message.delta",
        "message.completed",
    ]:
        assert event_type in trackable
        assert event_type in predicate
    assert "handleAssistantMessageRuntimeEvent(currentAgentId, chatState, entry" in handle_event
    assert "updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, { partial: true })" in assistant_handler
    assert "updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, { completed: true })" in assistant_handler
    assert "requestCtx.streamedText" in assistant_handler
    assert 'role === "user"' in guard
    assert 'rawType === "message.part.updated"' in guard


def test_session_render_hydrates_active_run_and_replays_events():
    src = _src()
    load_session = _extract_js_function(src, "loadSessionForAgent")
    reconcile_projection = _extract_js_function(src, "reconcileActiveRequestProjection")
    hydrate = _extract_js_function(src, "hydrateActiveRequestFromRun")

    assert "renderChatHistory(data.messages || [], data.metadata || {})" in load_session
    assert "reconcileActiveRequestProjection(agentId, normalized, data.metadata || {}, data.messages || [])" in load_session
    assert "metadata.active_run" in reconcile_projection
    assert "isValidatedRuntimeActiveRun(activeRun)" in reconcile_projection
    assert "hydrateActiveRequestFromRun(agentId, sessionId, activeRun, metadata)" in reconcile_projection
    assert "ensureEventSocketForAgent(agentId, sessionId" in reconcile_projection
    assert "startChatRunReconcileLoop(agentId, requestCtx" in reconcile_projection
    assert 'setChatStatus(activeStatus === "stream_detached" ? "Still running. Reconnecting…" : "Still running. Syncing…")' in reconcile_projection
    assert 'clearStaleActiveRequest(agentId, requestCtx, activeRun ? "opencode_not_active" : "metadata_active_run_null")' in reconcile_projection
    assert "streamDetached" in hydrate
