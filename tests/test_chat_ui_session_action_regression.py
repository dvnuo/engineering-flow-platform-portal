from pathlib import Path


def test_chat_ui_session_action_wiring_regression():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert "function renameSessionForAgent(" in js_source
    assert "function deleteSessionForAgent(" in js_source
    assert "data-session-action" in js_source
    assert 'event.target.closest("[data-session-action]")' in js_source
    assert "/a/${agentId}/api/sessions/${encodeURIComponent(normalizedSessionId)}/rename" in js_source
    assert "fetch(`/app/agents/${encodeURIComponent(agentId)}/sessions/${encodeURIComponent(normalizedSessionId)}`" in js_source


def test_chat_ui_delete_session_error_parsing_regression():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "payload.detail ||" in js_source
    assert "payload.error ||" in js_source
    assert "payload.detail || await handleErrorResponse(resp)" not in js_source
