from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_page_uses_assistant_wording(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=1, username="phase-a", nickname="Phase A", role="user")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)

    client = TestClient(app)
    response = client.get("/app")

    assert response.status_code == 200
    html = response.text
    assert "Assistants" in html
    assert "Create Assistant" in html
    assert "Select an assistant" in html
    assert "Message an assistant" in html
    assert "Active Agent" not in html
    assert "Type message, / for skills" not in html


def test_chat_response_partial_has_message_row_and_timestamp_hook():
    partial = Path("app/templates/partials/chat_response.html").read_text(encoding="utf-8")

    assert "message-row" in partial
    assert "message-timestamp" in partial


def test_chat_ui_assets_include_phase_a_hooks():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    css_source = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert "Message an assistant" in js_source
    assert "Message " in js_source
    assert "message-timestamp" in js_source
    assert "updateChatInputPlaceholder" in js_source

    assert ".message-row .message-timestamp" in css_source
    assert ".message-row:hover .message-timestamp" in css_source
    assert ".message-row:focus-within .edit-msg-btn" in css_source
    assert "@media (hover: none)" in css_source
