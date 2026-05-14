from pathlib import Path


def test_thinking_event_types_and_request_id_guards_present():
    src = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    for marker in [
        'continuation.started',
        'chat.incomplete',
        'chat.blocked',
        'provider.retry',
        'tool.started',
        'permission_request',
    ]:
        assert marker in src
    assert 'lastCompletedRequestId' in src
    assert 'request_id' in src
