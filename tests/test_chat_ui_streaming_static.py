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
        'heartbeat',
        'llm_thinking',
        'message.delta',
        'tool.started',
        'tool.completed',
        'tool.failed',
        'permission_request',
        'permission_resolved',
        'provider.retry',
        'continuation.started',
        'continuation.completed',
        'continuation.failed',
        'chat.timeout_recovery.started',
        'chat.timeout_recovery.poll',
        'chat.timeout_recovery.recovered',
        'chat.incomplete',
        'chat.failed',
    ]:
        assert event_type in src


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
    assert 'const dedupKey = (event) =>' in merge_events
    assert 'const entryDedupKey = (() =>' in handle_event


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
