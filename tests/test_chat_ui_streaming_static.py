from pathlib import Path


def test_chat_ui_streaming_contract_markers():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert '/api/chat/stream' in src
    assert 'getReader()' in src
    for name in ['runtime_event', 'delta', 'final', 'done', 'error', 'heartbeat']:
        assert name in src
    assert 'completion_state' in src
    assert 'incomplete_reason' in src
    assert 'handleAgentEventMessage' in src


def test_chat_ui_streaming_does_not_use_eventsource_for_chat_stream():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'EventSource(' not in src
