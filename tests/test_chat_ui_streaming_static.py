from pathlib import Path


def test_chat_ui_streaming_contract_markers():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
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
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'EventSource(' not in src


def test_runtime_event_unwrap_preserves_embedded_type_and_stream_event_marker():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'eventData?.data?.type' in src
    assert 'type: embeddedType || "runtime_event"' in src
    assert 'event_type: embeddedType || "runtime_event"' in src
    assert 'stream_event:' in src


def test_request_ctx_and_fallback_non_success_markers():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    for marker in ['sawRuntimeEvent', 'sawDelta', 'sawFinal', 'streamCompleted', 'streamFailed', 'streamIncomplete']:
        assert marker in src
    assert 'finalizeIncompleteAssistantRow(agentIdAtSend, requestCtx, payload)' in src
    assert 'runtime_events: payload?.runtime_events || []' in src
