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


def test_chat_ui_streaming_does_not_use_eventsource_for_chat_stream():
    src = _src()
    assert 'EventSource(' not in src


def test_runtime_event_unwrap_preserves_embedded_type_and_stream_event_marker():
    src = _src()
    assert 'eventData?.data?.type' in src
    assert 'type: embeddedType || "runtime_event"' in src
    assert 'event_type: embeddedType || "runtime_event"' in src
    assert 'stream_event:' in src


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
