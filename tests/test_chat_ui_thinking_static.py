from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path('app/static/js/chat_ui.js')


def _src():
    return SRC.read_text(encoding='utf-8')


def test_thinking_event_types_and_request_id_guards_present():
    src = _src()
    for marker in [
        'chat.incomplete',
        'chat.blocked',
        'provider.retry',
        'execution.started',
        'execution.completed',
        'execution.failed',
        'portal.waiting_for_runtime_events',
        'tool.started',
        'permission_request',
    ]:
        assert marker in src
    assert 'request_id' in src
    assert 'mergeFinalThinkingSnapshot' in src
    for marker in ['completion_state', 'incomplete_reason', 'context_state']:
        assert marker in src


def test_merge_final_thinking_snapshot_preserves_terminal_diagnostics():
    src = _src()
    body = _extract_js_function(src, "mergeFinalThinkingSnapshot")
    for marker in [
        'completion_state: completionState || "completed"',
        'incomplete_reason: finalPayload?.incomplete_reason || ""',
        'contextState: finalContextState',
        'context_state: finalContextState',
    ]:
        assert marker in body


def test_stale_event_guard_uses_request_ids_and_last_thinking_snapshot():
    src = _src()
    handle_event = _extract_js_function(src, "handleAgentEventMessage")
    assert 'request_id' in src
    assert 'lastThinkingSnapshot?.requestId' in handle_event
    assert 'expectedRequestId' in src
