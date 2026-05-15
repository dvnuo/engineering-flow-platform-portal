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
    incomplete = _extract_js_function(src, "handleIncompleteChatStream")
    cleanup = _extract_js_function(src, "cleanupChatStreamRequest")
    assert 'const parsedBatch = parseSseEventsFromChunk(buffer, chunkText)' in stream_submit
    assert 'const parsed = parseSseEvent(buffer)' in stream_submit
    assert 'await handleChatStreamMissingFinal(agentIdAtSend, requestCtx)' in stream_submit
    assert 'finalizeNonSuccessChatResponse(agentIdAtSend, requestCtx, finalPayload, reason)' in incomplete
    assert 'clearWaitingForRuntimeEventsTimer(requestCtx)' in cleanup
