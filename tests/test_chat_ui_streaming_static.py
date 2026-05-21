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
    assert ":4096" not in src


def test_chat_submit_preflights_active_run_before_optimistic_ui():
    src = _src()
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")
    preflight = _extract_js_function(src, "preflightActiveRunForSession")
    get_active = _extract_js_function(src, "getActiveRunFromPayload")
    runtime_active = _extract_js_function(src, "isRuntimeRunActuallyActive")

    assert "preflightActiveRunForSession" in src
    assert "`/api/sessions/${encodeURIComponent(sessionId)}/active-run`" in preflight
    assert "getActiveRunFromPayload(payload)" in preflight
    assert "hydrateActiveRequestFromRun(agentId, sessionId, activeRun" in preflight
    assert 'appendPortalChatRuntimeEvent(agentId, requestCtx, "portal.chat_run_already_active"' in preflight
    assert "ensureEventSocketForAgent(" in preflight
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in preflight
    assert "setChatSubmittingForAgent(agentId, false)" in preflight
    assert "Previous message still running" in preflight

    assert "payload.active_run" in get_active
    assert "payload.run" in get_active
    assert "payload.data.active_run" in get_active
    assert "payload.active" in get_active

    for marker in [
        "run.opencode_active === true",
        'run.source_of_truth === "opencode"',
        '"busy"',
        '"retry"',
        "run.stale === true",
        "run.aborted === true",
        "run.completed === true",
        "run.runtimeInactive === true",
        "run.opencodeInactive === true",
    ]:
        assert marker in runtime_active

    session_idx = submit_chat.index("const sessionIdAtSend = ensureChatSessionId(agentIdAtSend);")
    assert "localPreflightActiveRunForSession" in submit_chat
    preflight_idx = submit_chat.index("const activeRunBlocked = await localPreflightActiveRunForSession(agentIdAtSend, sessionIdAtSend);")
    client_request_idx = submit_chat.index("const clientRequestId")
    user_row_idx = submit_chat.index("buildUserMessageArticle")
    pending_row_idx = submit_chat.index("buildPendingAssistantArticle")
    clear_input_idx = submit_chat.index("dom.chatInput.value = \"\"")
    clear_files_idx = submit_chat.index("chatState.pendingFiles = []")
    stream_post_idx = submit_chat.index("trySubmitChatStreamForSelectedAgent(agentIdAtSend, requestCtx, requestBody)")

    assert session_idx < preflight_idx < client_request_idx
    assert preflight_idx < user_row_idx
    assert preflight_idx < pending_row_idx
    assert preflight_idx < clear_input_idx
    assert preflight_idx < clear_files_idx
    assert preflight_idx < stream_post_idx
    assert "if (activeRunBlocked) return;" in submit_chat


def test_chat_run_already_active_has_specialized_stream_and_fallback_handling():
    src = _src()
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")
    stream_handler = _extract_js_function(src, "handleChatStreamEvent")
    detector = _extract_js_function(src, "isChatRunAlreadyActivePayload")
    handler = _extract_js_function(src, "handleChatRunAlreadyActive")

    assert "function isChatRunAlreadyActivePayload" in src
    assert "function handleChatRunAlreadyActive" in src
    assert "chat_run_already_active" in detector
    assert "isRuntimeRunActuallyActive(activeRun)" in detector
    assert "removeTemporaryAssistantRows({ requestId: requestCtx.clientRequestId, onlyEmpty: false })" in handler
    assert "removeOptimisticUserRowForRequest(requestCtx)" in handler
    assert "removeLatestOptimisticUserRow({ requestCtx, onlyLocal: true })" in handler
    assert "dom.chatInput.value = requestCtx.backupMessage" in handler
    assert "hydrateActiveRequestFromRun(agentId, sessionId, activeRun" in handler
    assert "refreshOpenCodeSessionStatusForAgent(agentId, sessionId, chatState)" in handler
    assert "hydrateActiveRequestFromSessionStatus(agentId, sessionId, statusPayload)" in handler
    assert "startChatRunReconcileLoop(agentId, activeCtx, { immediate: true })" in handler
    assert "startChatRunReconcileLoop(agentId, statusCtx, { immediate: true })" in handler
    assert '"portal.chat_run_already_active"' in handler

    error_idx = stream_handler.index('if (outerType === "error")')
    assert "localIsChatRunAlreadyActivePayload" in stream_handler
    assert "localHandleChatRunAlreadyActive" in stream_handler
    error_active_idx = stream_handler.index("if (localIsChatRunAlreadyActivePayload(eventData))", error_idx)
    generic_error_idx = stream_handler.index("requestCtx.streamFailed = true", error_idx)
    assert error_idx < error_active_idx < generic_error_idx

    final_idx = stream_handler.index("if (isChatStreamFinalEventName(outerType) || isDirectCompletionEventName(outerType))")
    final_active_idx = stream_handler.index("if (localIsChatRunAlreadyActivePayload(eventData))", final_idx)
    non_success_idx = stream_handler.index("if (localIsNonSuccessFinalPayload(eventData))", final_idx)
    assert final_idx < final_active_idx < non_success_idx

    resp_idx = submit_chat.index("if (!resp.ok)")
    clone_json_idx = submit_chat.index("structuredError = await resp.clone().json()", resp_idx)
    active_payload_idx = submit_chat.index("if (localIsChatRunAlreadyActivePayload(structuredError))", resp_idx)
    handle_error_idx = submit_chat.index("throw new Error(await handleErrorResponse(resp))", resp_idx)
    assert resp_idx < clone_json_idx < active_payload_idx < handle_error_idx
    assert "/api/tasks" not in submit_chat
    assert ":4096" not in submit_chat


def test_chat_run_already_active_rejected_request_and_optimistic_user_markers():
    src = _src()
    build_user = _extract_js_function(src, "buildUserMessageArticle")
    submit_chat = _extract_js_function(src, "submitChatForSelectedAgent")
    remove_user = _extract_js_function(src, "removeOptimisticUserRowForRequest")
    handler = _extract_js_function(src, "handleChatRunAlreadyActive")
    preflight = _extract_js_function(src, "preflightActiveRunForSession")

    assert "options = {}" in build_user
    assert "options.clientRequestId" in build_user
    assert 'data-local-user="1"' in build_user
    assert 'data-client-request-id="' in build_user
    assert "user-message" in build_user

    assert "buildUserMessageArticle(displayMessage, displayAttachments, { clientRequestId })" in submit_chat
    assert "function removeOptimisticUserRowForRequest" in src
    assert "cssEscapeForSelector(requestId)" in remove_user
    assert 'article.user-message[data-client-request-id="' in remove_user
    assert "last.dataset.persisted === \"1\"" in remove_user
    assert "last.dataset.messageId" in remove_user
    assert "last.dataset.opencodeMessageId" in remove_user

    rejected_idx = handler.index("const rejectedClientRequestId = String(requestCtx?.clientRequestId || \"\")")
    match_idx = handler.index("chatState.activeRequest.clientRequestId === rejectedClientRequestId")
    clear_idx = handler.index("chatState.activeRequest = null", rejected_idx)
    hydrate_idx = handler.index("const activeCtx = hydrateActiveRequestFromRun")
    assert rejected_idx < match_idx < clear_idx < hydrate_idx
    assert "stopChatRunReconcileLoop(chatState.activeRequest)" in handler[rejected_idx:hydrate_idx]

    assert 'appendPortalChatRuntimeEvent(agentId, requestCtx, "portal.chat_run_already_active"' in preflight
    assert "Runtime reports an active OpenCode run before sending; send was not submitted." in preflight


def test_assistant_row_lookup_never_selects_user_article():
    src = _src()
    finder = _extract_js_function(src, "findAssistantArticleForRequest")
    updater = _extract_js_function(src, "updateOrCreateAssistantRowForRequest")

    assert "function isAssistantArticle" in src
    assert 'article.assistant-message[data-client-request-id="' in finder
    assert 'article[data-pending-assistant="1"][data-client-request-id="' in finder
    assert 'article.assistant-message[data-request-id="' in finder
    assert 'article[data-pending-assistant="1"][data-request-id="' in finder
    assert 'article.assistant-message[data-message-id="' in finder
    assert 'article.assistant-message[data-primary-message-id="' in finder
    assert "article.assistant-message[data-message-ids]" in finder
    assert "article[data-pending-assistant='1'][data-message-ids]" in finder
    assert 'article[data-client-request-id="${escaped}"]' not in finder
    assert 'article[data-request-id="${escaped}"]' not in finder
    assert 'article[data-message-id="${escaped}"]' not in finder
    assert 'article[data-primary-message-id="${escaped}"]' not in finder
    assert 'querySelectorAll("article[data-message-ids]")' not in finder
    assert "if (isAssistantArticle(byClient)) return byClient;" in finder
    assert "if (isAssistantArticle(containing)) return containing;" in finder
    assert "if (article && !isAssistantArticle(article))" in updater
    assert "article = null;" in updater


def test_opencode_canonical_snapshot_and_status_helpers_are_wired():
    src = _src()
    load_session = _extract_js_function(src, "loadSessionForAgent")
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    session_state_only = _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent")
    maybe_refresh = _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent")
    refresh_status = _extract_js_function(src, "refreshOpenCodeSessionStatusForAgent")
    render_panel = _extract_js_function(src, "renderThinkingPanelFromClientState")
    state_notes = _extract_js_function(src, "renderOpenCodeRuntimeStateNotes")
    set_status = _extract_js_function(src, "setChatStatus")

    for helper in [
        "function getCanonicalMessagesFromSessionPayload",
        "function canonicalMessagesToLegacyDisplayMessages",
        "function canonicalMessagesToThinkingItems",
        "function applyOpenCodeCanonicalEventToChatState",
        "function normalizeOpenCodeSessionStatusType",
        "function isOpenCodeSessionInactivePayload",
        "function buildOpenCodeInactiveSessionStatusPayload",
        "function markOpenCodeProjectionInactive",
        "function isOpenCodeSessionStatusBlockingPayload",
        "function isOpenCodeSessionBlocking",
        "function computeOpenCodeRuntimeUiState",
        "function isOpenCodeSessionStateOnlyEvent",
        "function isOpenCodeCanonicalSnapshotEvent",
        "function refreshOpenCodeSessionStatusForAgent",
        "function buildSyntheticRunFromSessionStatus",
        "function hydrateActiveRequestFromSessionStatus",
    ]:
        assert helper in src

    assert 'rawType === "session.status"' in session_state_only
    assert 'rawType === "session.updated"' in session_state_only
    assert 'rawType === "session.idle"' in session_state_only
    assert 'reconcileHint === "fetch_session_messages"' in session_state_only

    assert "const canonicalMessages = getCanonicalMessagesFromSessionPayload(data)" in load_session
    assert "const statusPayload = await refreshOpenCodeSessionStatusForAgent(agentId, normalized, latestChatState)" in load_session
    assert "isOpenCodeSessionStatusBlockingPayload(statusPayload)" in load_session
    assert "hydrateActiveRequestFromSessionStatus(agentId, normalized, statusPayload)" in load_session
    assert "ensureEventSocketForAgent(" in load_session
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in load_session
    assert 'setChatStatus("Previous message still running. Reconnecting…")' in load_session
    assert "const messagesForRender = canonicalMessages.length" in load_session
    assert "canonicalMessagesToLegacyDisplayMessages(canonicalMessages)" in load_session
    assert "data.messages || []" in load_session
    assert "source_of_truth: canonicalMessages.length ? \"opencode\"" in load_session
    assert "canonical_messages: canonicalMessages" in load_session
    assert "session_status: statusPayload || data.metadata?.session_status || null" in load_session
    assert "renderChatHistory(normalizedPayload.messages || [], normalizedPayload.metadata || {})" in load_session
    assert "reconcileActiveRequestProjection(agentId, normalized, normalizedPayload.metadata || {}, normalizedPayload.messages || [])" in load_session

    assert "applyOpenCodeCanonicalEventToChatState" in handle_event
    assert "localApplyOpenCodeCanonicalEventToChatState(chatState, entry)" in handle_event
    assert "markOpenCodeProjectionInactive(" in handle_event
    assert "maybeRefreshSessionSnapshotForOpenCodeEvent" in handle_event
    assert "isCurrentSessionCanonicalSnapshotEvent" in handle_event
    assert "!isCurrentSessionCanonicalSnapshotEvent" in handle_event
    assert "appliedCanonicalEvent && !chatState.activeRequest && isOpenCodeCanonicalSnapshotEvent(entry)" in handle_event
    assert "const isSessionStateOnlyCanonicalEvent = appliedCanonicalEvent" in handle_event
    assert "isOpenCodeSessionStateOnlyEvent(entry)" in handle_event
    assert "if (isSessionStateOnlyCanonicalEvent)" in handle_event
    assert "return;" in handle_event[handle_event.index("if (isSessionStateOnlyCanonicalEvent)"):]
    assert handle_event.index("if (isSessionStateOnlyCanonicalEvent)") < handle_event.index("if (!chatState.inflightThinking)")
    assert "reconcile_hint" in src
    assert "fetch_session_messages" in src
    assert "openCodeProjection" in src
    assert "opencCodeProjection" not in src

    assert "projection.needsSnapshot = false" in maybe_refresh
    assert "projection.snapshotRefreshError = \"\"" in maybe_refresh
    assert "projection.needsSnapshot = true" in maybe_refresh
    assert "projection.snapshotRefreshLastFailedAt = Date.now()" in maybe_refresh
    assert "Date.now() - lastFailedAt < 10000" in maybe_refresh

    assert '"message.part.updated"' in src
    assert '"message.completed"' in src

    assert "`/api/sessions/${encodeURIComponent(sessionId)}/status`" in refresh_status
    assert "agentApiFor(" in refresh_status
    assert "sessionStatusPayload" in refresh_status
    assert "isOpenCodeSessionInactivePayload(payload)" in refresh_status
    assert "markOpenCodeProjectionInactive(" in refresh_status
    assert "activeChildSessions" in refresh_status
    assert "sessionStatusError" in refresh_status

    assert "renderOpenCodeRuntimeStateNotes(uiState)" in render_panel
    assert "Runtime:" in state_notes
    assert "Session:" in state_notes
    assert "Message:" in state_notes
    assert "dataset.runtimeHealth" in set_status
    assert "dataset.sessionStatus" in set_status
    assert "dataset.messageProgress" in set_status
    assert "visibleStatusText" in set_status
    assert "openCodeRuntimeUiStatusText(uiState)" in set_status
    assert '["busy", "retry"].includes' in set_status
    assert "statusDetail.join" in set_status
    canonical_apply = _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState")
    assert 'eventType === "session.updated"' in canonical_apply
    assert 'rawType !== "session.updated"' in canonical_apply
    assert 'rawType === "session.status" || rawType === "session.updated"' in canonical_apply
    assert ":4096" not in src
    assert "/api/tasks" not in handle_event
    assert "/api/tasks" not in load_session


def test_refresh_busy_session_reconnects_and_refreshes_snapshot_static_contract():
    src = _src()
    load_session = _extract_js_function(src, "loadSessionForAgent")
    canonical_apply = _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState")
    maybe_refresh = _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent")
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    reconcile_projection = _extract_js_function(src, "reconcileActiveRequestProjection")

    assert "const statusPayload = await refreshOpenCodeSessionStatusForAgent(agentId, normalized, latestChatState)" in load_session
    assert "isOpenCodeSessionStatusBlockingPayload(statusPayload)" in load_session
    assert "hydrateActiveRequestFromSessionStatus(agentId, normalized, statusPayload)" in load_session
    assert "ensureEventSocketForAgent(" in load_session
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in load_session
    assert 'setChatStatus("Previous message still running. Reconnecting…")' in load_session

    assert "!chatState.activeRequest" in canonical_apply
    assert '"message.completed"' in canonical_apply
    assert '"session.idle"' in canonical_apply
    assert "projection.needsSnapshot = true" in canonical_apply
    assert 'projection.sessionStatus = "idle"' in canonical_apply
    idle_start = canonical_apply.index('if (rawType === "session.idle")')
    idle_branch = canonical_apply[idle_start:canonical_apply.index("return true;", idle_start)]
    assert "projection.sessionStatusPayload" in idle_branch
    assert "buildOpenCodeInactiveSessionStatusPayload" in idle_branch
    assert '"safe_to_send"' in _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload")
    assert "active_run: null" in _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload")

    assert "maybeRefreshSessionSnapshotForOpenCodeEvent(currentAgentId, chatState" in handle_event
    assert "appliedCanonicalEvent && !chatState.activeRequest" in handle_event
    assert "loadSessionForAgent(agentId, sessionId, { render: agentId === state.selectedAgentId })" in maybe_refresh
    assert "projection.needsSnapshot = false" in maybe_refresh

    assert "isOpenCodeSessionStatusBlockingPayload(sessionStatusPayload || {})" in reconcile_projection
    assert 'setChatStatus("Previous message still running. Reconnecting…")' in reconcile_projection
    assert "syncSelectedAgentChatActionControls()" in reconcile_projection


def test_active_request_busy_state_uses_runtime_blocking_helper():
    src = _src()
    has_active = _extract_js_function(src, "hasActiveChatRequestForAgent")
    local_submit = _extract_js_function(src, "isLocalSubmitPendingBeforeOpenCodeStatus")
    blocking = _extract_js_function(src, "isActiveRequestBlocking")
    session_blocking = _extract_js_function(src, "isOpenCodeSessionBlocking")
    status_blocking = _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload")
    sync_controls = _extract_js_function(src, "syncSelectedAgentChatActionControls")
    abort_visibility = _extract_js_function(src, "shouldShowAbortChatRunButton")

    assert "function isLocalSubmitPendingBeforeOpenCodeStatus" in src
    assert "chatState.isSubmitting !== true" in local_submit
    assert "ageMs > 120000" in local_submit
    assert "req.stale === true" in local_submit
    assert "isLocalSubmitPendingBeforeOpenCodeStatus(chatState)" in has_active
    assert "isLocalSubmitPendingBeforeOpenCodeStatus(chatState)" in abort_visibility
    assert has_active.index("isLocalSubmitPendingBeforeOpenCodeStatus(chatState)") < has_active.index("const knownInactive")
    assert abort_visibility.index("isLocalSubmitPendingBeforeOpenCodeStatus(chatState)") < abort_visibility.index("isOpenCodeSessionInactivePayload(payload)")
    assert "isOpenCodeSessionBlocking(chatState)" in has_active
    assert "isOpenCodeSessionInactivePayload(payload)" in has_active
    assert "markOpenCodeProjectionInactive(agentId, chatState" in has_active
    assert '"opencode_status_not_active"' in has_active
    assert "isOpenCodeSessionInactivePayload(payload)" in session_blocking
    assert "isOpenCodeSessionStatusBlockingPayload(payload)" in session_blocking
    assert "payload?.active === true" in status_blocking
    assert 'payload?.action_hint === "wait_reconnect_or_stop"' in status_blocking
    assert '"busy"' in status_blocking
    assert '"retry"' in status_blocking
    assert "shouldShowAbortChatRunButton(agentId)" in sync_controls
    assert "isOpenCodeSessionBlocking(chatState)" in abort_visibility
    assert "isOpenCodeSessionInactivePayload(payload)" in abort_visibility
    assert "markOpenCodeProjectionInactive(agentId, chatState" in abort_visibility
    assert "chatState?.activeRequest" not in abort_visibility
    assert "hasIncompleteInflightThinking(chatState)" not in abort_visibility
    for marker in [
        "req.aborted",
        "req.stale",
        "req.completed",
        "req.failed",
        "req.runtimeInactive",
        "req.opencodeInactive",
    ]:
        assert marker in blocking
    assert "isActiveRequestBlocking(chatState)" not in has_active
    assert "hasIncompleteInflightThinking(chatState)" not in has_active


def test_inactive_opencode_event_deferral_runs_before_canonical_apply():
    src = _src()
    handle_event = _extract_js_function(src, "handleAgentEventMessage")

    assert "function shouldDeferInactiveOpenCodeEventForFreshLocalSubmit" in src
    assert "function isInactiveOpenCodeSessionEvent" in src
    assert "function runtimeEventTimestampMs" in src
    assert "shouldDeferInactiveOpenCodeEventForFreshLocalSubmit(" in handle_event
    assert handle_event.index("shouldDeferInactiveOpenCodeEventForFreshLocalSubmit(") < handle_event.index("localApplyOpenCodeCanonicalEventToChatState(chatState, entry)")
    assert '"portal.opencode_inactive_event.deferred"' in handle_event


def test_submit_chat_clears_old_opencode_projection_before_active_request():
    src = _src()
    submit_fn = _extract_js_function(src, "submitChatForSelectedAgent")

    assert "pendingLocalSubmit" in submit_fn
    assert "chatState.openCodeProjection.sessionStatusPayload = null" in submit_fn
    assert "chatState.openCodeProjection.sessionStatus = \"\"" in submit_fn
    assert submit_fn.index("chatState.openCodeProjection.sessionStatusPayload = null") < submit_fn.index("chatState.activeRequest = requestCtx")
    assert submit_fn.index("chatState.activeRequest = requestCtx") < submit_fn.index("setChatSubmittingForAgent(agentIdAtSend, true)")


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
    hint_helper = _extract_js_function(src, "nonSuccessHintForPayload")
    assert "Thinking Process Live" in render_panel
    assert "portal-live-status" in render_panel
    assert "Elapsed:" in render_panel
    assert "Last event:" in render_panel
    assert "portal-completion-banner" in render_panel
    assert "nonSuccessHintForPayload" in render_panel
    assert 'You can send "continue"' in hint_helper
    assert "chat_run_already_active" in hint_helper
    assert "stop the run, or reset this session" in hint_helper
    assert "opencode_abort_still_active" in hint_helper
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
    abort_session = _extract_js_function(src, "abortSessionForAgent")
    sync_controls = _extract_js_function(src, "syncSelectedAgentChatActionControls")

    assert "function abortActiveChatRequestForSelectedAgent" in src
    assert '`/api/chat/runs/${encodeURIComponent(requestId)}/abort`' in abort_fn
    assert '`/a/${agentId}/api/sessions/${encodeURIComponent(sessionId)}/abort`' in abort_session
    assert "abortSessionForAgent(agentId, sessionId, { forceDetach: true })" in abort_fn
    assert "body: JSON.stringify({ force_detach: forceDetach })" in abort_session
    assert "const forceDetach = options.forceDetach !== false" in abort_session
    assert abort_fn.index("if (sessionId)") < abort_fn.index("`/api/chat/runs/${encodeURIComponent(requestId)}/abort`")
    assert "hardResetSessionForAgent" in src
    assert "runtimeAbortSucceeded(result)" in abort_fn
    assert "runtimeAbortIndicatesInactive(result)" in abort_fn
    assert "handleSessionAbortSuccess(agentId, chatState, requestCtx, sessionId, result)" in abort_fn
    assert '"portal.abort.started"' in abort_fn
    assert '"portal.abort.completed"' in abort_fn
    assert '"portal.abort.failed"' in abort_fn
    assert "/api/tasks" not in abort_fn
    assert ":4096" not in abort_fn
    assert 'id="abort-chat-run-btn"' in template
    assert "Stop run" in template
    assert "shouldShowAbortChatRunButton(agentId)" in sync_controls


def test_abort_synthetic_opencode_session_run_uses_session_abort_endpoint():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    synthetic = _extract_js_function(src, "isSyntheticOpenCodeSessionRequest")
    abort_session = _extract_js_function(src, "abortSessionForAgent")
    session_success = _extract_js_function(src, "handleSessionAbortSuccess")

    assert "function isSyntheticOpenCodeSessionRequest" in src
    assert 'requestId.startsWith("opencode-session-")' in synthetic
    assert "openCodeProjection?.sessionStatusPayload" in synthetic
    assert "payload?.active === true" in synthetic
    assert "isOpenCodeSessionBlocking(chatState)" in synthetic
    assert "requestCtx.fromSessionStatus === true" in synthetic

    assert '`/a/${agentId}/api/sessions/${encodeURIComponent(sessionId)}/abort`' in abort_session
    assert "if (sessionId)" in abort_fn
    assert "abortSessionForAgent(agentId, sessionId, { forceDetach: true })" in abort_fn
    assert '`/api/chat/runs/${encodeURIComponent(requestId)}/abort`' in abort_fn
    assert "handleSessionAbortSuccess(agentId, chatState, requestCtx, sessionId" in abort_fn

    assert 'chatState.openCodeProjection.sessionStatus = "idle"' in session_success
    assert 'buildOpenCodeInactiveSessionStatusPayload("idle", result)' in session_success
    inactive_payload = _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload")
    assert "active: false" in inactive_payload
    assert 'source_of_truth: "opencode"' in inactive_payload
    assert "status_type: normalizedStatus" in inactive_payload
    assert "active_run: null" in inactive_payload
    assert '"portal.abort.detached_old_opencode_session"' in session_success
    assert "hardResetSessionForAgent(agentId, sessionId)" in session_success
    assert "syncSelectedAgentChatActionControls()" in session_success
    assert "loadSessionForAgent(agentId, sessionId" in session_success


def test_abort_active_chat_request_checks_runtime_abort_result():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    success_helper = _extract_js_function(src, "runtimeAbortSucceeded")
    inactive_helper = _extract_js_function(src, "runtimeAbortIndicatesInactive")

    assert "result.success !== true" in success_helper
    assert 'result.error === "opencode_abort_still_active"' in success_helper
    assert "result.active === true" in success_helper
    assert "result.detached_old_session === true" in success_helper
    assert "missing_session_ids" in inactive_helper
    assert '"idle", "aborted", "stopped", "missing", "stale", "completed"' in inactive_helper
    assert 'actionHint === "safe_to_send"' in inactive_helper
    assert "if (!runtimeAbortSucceeded(result))" in abort_fn
    assert "startChatRunReconcileLoop(agentId, requestCtx, { immediate: true })" in abort_fn


def test_abort_failure_does_not_clear_active_request():
    src = _src()
    abort_fn = _extract_js_function(src, "abortActiveChatRequestForSelectedAgent")
    session_success = _extract_js_function(src, "handleSessionAbortSuccess")
    failure_start = abort_fn.index("if (!runtimeAbortSucceeded(result))")
    failure_end = abort_fn.index("return;", failure_start)
    failure_branch = abort_fn[failure_start:failure_end]
    helper_failure_start = session_success.rindex('appendPortalChatRuntimeEvent(agentId, ctx, "portal.abort.failed"')
    helper_failure_branch = session_success[helper_failure_start:]

    assert "handleSessionAbortSuccess(agentId, chatState, requestCtx, sessionId, result || {})" in failure_branch
    assert '"portal.abort.failed"' in session_success
    assert 'setChatStatus("Unable to stop current run.", true)' in helper_failure_branch
    assert 'showToast("Unable to stop current run: " + String(result?.error || result?.detail || "abort failed"))' in helper_failure_branch
    assert "startChatRunReconcileLoop(agentId, ctx, { immediate: true })" in helper_failure_branch
    assert "syncSelectedAgentChatActionControls()" in helper_failure_branch
    assert "clearStaleActiveRequest" not in helper_failure_branch


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
    assert "assistantRuntimeEventResult = handleAssistantMessageRuntimeEvent(" in handle_event
    assert "updateOrCreateAssistantRowForRequest(agentId, requestCtx, rowPayload, { partial: true })" in assistant_handler
    assert "completed: false" in assistant_handler
    assert "requestCtx.awaitingAuthoritativeFinal = true" in assistant_handler
    assert 'return "completed_reconcile"' in assistant_handler
    assert "requestCtx.streamedText" in assistant_handler
    assert 'role === "user"' in guard
    assert 'rawType === "message.part.updated"' in guard


def test_authoritative_stream_final_contracts():
    src = _src()
    assistant_handler = _extract_js_function(src, "handleAssistantMessageRuntimeEvent")
    stream_handler = _extract_js_function(src, "handleChatStreamEvent")
    stream_submit = _extract_js_function(src, "trySubmitChatStreamForSelectedAgent")
    success_handler = _extract_js_function(src, "handleAgentChatSuccess")
    preview_helper = _extract_js_function(src, "isLikelySyntheticFinalPreviewDelta")

    assert "void handleAgentChatSuccess" not in assistant_handler
    assert "requestCtx.awaitingAuthoritativeFinal = true" in assistant_handler
    assert 'return "completed_reconcile"' in assistant_handler

    assert "function isSyntheticFinalDeltaEvent" in src
    assert "function isLikelySyntheticFinalPreviewDelta" in src
    assert "isSyntheticFinalDeltaEvent(eventData, associatedEvent)" in preview_helper
    assert "requestCtx?.syntheticFinalDeltaPreview" in preview_helper
    assert "requestCtx?.awaitingAuthoritativeFinal" in preview_helper
    assert "requestCtx?.sawAssistantMessageCompleted" in preview_helper
    assert "requestCtx?.sawRunCompleted" in preview_helper
    assert "lacksCanonicalMarkers" in preview_helper
    assert "containsEllipsis || deltaText.length >= 80" in preview_helper

    wrapper_idx = stream_handler.index("rememberAssociatedRuntimeDeltaEvent(requestCtx, eventData, embeddedType);")
    wrapper_record_idx = stream_handler.index("requestCtx.syntheticFinalDeltaPreview = {", wrapper_idx)
    assert wrapper_idx < wrapper_record_idx
    assert "localIsSyntheticFinalDeltaEvent(eventData, null)" in stream_handler[wrapper_idx:wrapper_record_idx]
    assert "response: wrapperDeltaText || requestCtx.streamedText || \"\"" in stream_handler[wrapper_record_idx:]
    assert "observedAt: Date.now()" in stream_handler[wrapper_record_idx:]

    delta_idx = stream_handler.index("if (isChatStreamDeltaEventName(outerType))")
    associated_idx = stream_handler.index("const associatedEvent = getAssociatedRuntimeDeltaEvent(requestCtx, deltaText);", delta_idx)
    synthetic_idx = stream_handler.index("isLikelySyntheticFinalPreviewDelta(", delta_idx)
    preview_idx = stream_handler.index("if (isSyntheticPreviewDelta)", synthetic_idx)
    append_idx = stream_handler.index('requestCtx.streamedText = (requestCtx.streamedText || "") + (deltaText || "")', delta_idx)
    helper_call = stream_handler[synthetic_idx:preview_idx]
    preview_branch = stream_handler[preview_idx:append_idx]
    assert associated_idx < synthetic_idx < preview_idx < append_idx
    assert "eventData" in helper_call
    assert "requestCtx" in helper_call
    assert "associatedEvent" in helper_call
    assert "const existingPreview = String(" in preview_branch
    assert "const nextPreview = String(deltaText || \"\")" in preview_branch
    assert "nextPreview.length > existingPreview.length" in preview_branch
    assert "requestCtx.streamedText = previewText" in preview_branch
    assert "requestCtx.awaitingAuthoritativeFinal = true" in preview_branch
    assert "response: previewText" in preview_branch
    assert "observedAt: requestCtx.syntheticFinalDeltaPreview?.observedAt || Date.now()" in preview_branch
    assert "updatePendingAssistantStreamContent(agentIdAtSend, previewText" in preview_branch
    assert "queueAssistantTypewriter" not in preview_branch
    assert '(requestCtx.streamedText || "") + (deltaText || "")' not in preview_branch

    final_idx = stream_handler.index("if (isChatStreamFinalEventName(outerType) || isDirectCompletionEventName(outerType))")
    final_branch = stream_handler[final_idx:]
    assert "requestCtx.authoritativeFinalReceived = true" in final_branch
    assert "allowFinalWithoutActiveRequest: true" in final_branch
    assert 'source: "stream_final"' in final_branch

    assert 'chatState.openCodeProjection.sessionStatus = "idle"' in success_handler
    assert "active: false" in success_handler
    assert "active_run: null" in success_handler
    assert "chatState.openCodeProjection.needsSnapshot = false" in success_handler

    assert "finalizeFromSessionSnapshotAfterCompletedLifecycle" in src
    assert "requestCtx.awaitingAuthoritativeFinal" in stream_submit
    assert "requestCtx.sawAssistantMessageCompleted" in stream_submit
    assert "requestCtx.sawRunCompleted" in stream_submit
    assert "stream_final_missing_after_completed_event" in stream_submit
    assert ":4096" not in src
    assert "/api/tasks" not in stream_submit


def test_terminal_runtime_event_is_null_safe_after_assistant_finalization():
    src = _src()
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    terminal_helper = _extract_js_function(src, "markThinkingTerminalFromEvent")
    ensure_socket = _extract_js_function(src, "ensureEventSocketForAgent")
    onmessage_branch = ensure_socket[
        ensure_socket.index("ws.onmessage = (event) => {"):
        ensure_socket.index("ws.onclose =", ensure_socket.index("ws.onmessage = (event) => {"))
    ]

    assert "function markThinkingTerminalFromEvent" in src
    assert "assistantRuntimeEventResult" in handle_event
    assert 'assistantRuntimeEventResult === "finalized"' in handle_event
    assert "markThinkingTerminalFromEvent(chatState, entry)" in handle_event
    assert "chatState.inflightThinking.completed = true" not in handle_event
    assert "chatState.inflightThinking.completed = true" in terminal_helper
    assert terminal_helper.index("if (chatState.inflightThinking)") < terminal_helper.index("chatState.inflightThinking.completed = true")
    assert "try {" in onmessage_branch
    assert "catch (error)" in onmessage_branch
    assert '"portal.event_handler.failed"' in onmessage_branch
    assert (
        "startChatRunReconcileLoop" in onmessage_branch
        or "maybeRefreshSessionSnapshotForOpenCodeEvent" in onmessage_branch
    )


def test_session_render_hydrates_active_run_and_replays_events():
    src = _src()
    load_session = _extract_js_function(src, "loadSessionForAgent")
    reconcile_projection = _extract_js_function(src, "reconcileActiveRequestProjection")
    hydrate = _extract_js_function(src, "hydrateActiveRequestFromRun")
    hydrate_status = _extract_js_function(src, "hydrateActiveRequestFromSessionStatus")
    synthetic_status = _extract_js_function(src, "buildSyntheticRunFromSessionStatus")

    assert "getCanonicalMessagesFromSessionPayload(data)" in load_session
    assert "canonicalMessagesToLegacyDisplayMessages(canonicalMessages)" in load_session
    assert "renderChatHistory(normalizedPayload.messages || [], normalizedPayload.metadata || {})" in load_session
    assert "reconcileActiveRequestProjection(agentId, normalized, normalizedPayload.metadata || {}, normalizedPayload.messages || [])" in load_session
    assert "metadata.active_run" in reconcile_projection
    assert "metadata.session_status" in reconcile_projection
    assert "isOpenCodeSessionStatusBlockingPayload(sessionStatusPayload || {})" in reconcile_projection
    assert "isValidatedRuntimeActiveRun(activeRun)" in reconcile_projection
    assert "hydrateActiveRequestFromRun(agentId, sessionId, activeRun, metadata)" in reconcile_projection
    assert "hydrateActiveRequestFromSessionStatus(agentId, normalized, statusPayload)" in load_session
    assert "ensureEventSocketForAgent(agentId, sessionId" in reconcile_projection
    assert "startChatRunReconcileLoop(agentId, requestCtx" in reconcile_projection
    assert 'setChatStatus(activeStatus === "stream_detached" ? "Still running. Reconnecting…" : "Still running. Syncing…")' in reconcile_projection
    assert 'setChatStatus("Previous message still running. Reconnecting…")' in reconcile_projection
    assert 'clearStaleActiveRequest(agentId, requestCtx, activeRun ? "opencode_not_active" : "metadata_active_run_null")' in reconcile_projection
    assert "streamDetached" in hydrate
    assert "buildSyntheticRunFromSessionStatus(sessionId, statusPayload)" in hydrate_status
    assert 'source_of_truth: "opencode"' in synthetic_status
    assert 'action_hint: statusPayload.action_hint || "wait_reconnect_or_stop"' in synthetic_status
