from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_page_contains_new_portal_shell(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=1, username="phase-a", nickname="Phase A", role="user")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)

    client = TestClient(app)
    response = client.get("/app")

    assert response.status_code == 200
    html = response.text
    assert "portal-shell" in html
    assert "portal-rail" in html
    assert "portal-agent-pane" in html
    assert "sessions-drawer" in html
    assert "sessions-drawer-body" in html
    assert "header-new-chat-btn" in html
    assert "bundles-menu-btn" in html
    assert "home-open-bundles-btn" in html
    assert "Message an assistant" in html


def test_chat_response_partial_contract():
    partial = Path("app/templates/partials/chat_response.html").read_text(encoding="utf-8")
    assert "message-row" in partial
    assert "message-surface-assistant" in partial
    assert "message-timestamp" in partial
    assert "message-markdown" in partial


def test_frontend_assets_include_drawer_hooks_and_tokens():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    css_source = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert "openSessionsDrawer" in js_source
    assert "closeSessionsDrawer" in js_source
    assert "toggleSessionsDrawer" in js_source
    assert "headerNewChatBtn" in js_source
    assert "bundlesMenuBtn" in js_source
    assert "composerAttachBtn" in js_source
    assert "homeOpenBundlesBtn" in js_source
    assert "homeOpenTasksBtn" in js_source
    assert "publicAgents.find(" not in js_source

    assert "--portal-app-bg" in css_source
    assert ".portal-shell" in css_source
    assert ".portal-sessions-drawer" in css_source
    assert ".message-surface-user" in css_source
    assert ".message-surface-assistant" in css_source
    assert ".assistant-loading-dots" in css_source
    assert "@keyframes portal-message-in" in css_source
    assert "@keyframes portal-dot-pulse" in css_source
