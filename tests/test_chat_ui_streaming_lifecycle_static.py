from pathlib import Path


def test_request_ctx_stream_lifecycle_fields_present():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
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
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'finally {' in src
    assert 'setChatSubmittingForAgent(agentIdAtSend, false)' in src
    assert 'runtime_events: payload?.runtime_events || []' in src
