from pathlib import Path


def test_app_template_contains_new_portal_shell():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
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


def test_frontend_assets_include_phase_b_fixups():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    css_source = Path("app/static/css/app.css").read_text(encoding="utf-8")
    web_source = Path("app/web.py").read_text(encoding="utf-8")

    assert "ensureRunningSelectedAssistant" in js_source
    assert "setButtonDisabled" in js_source
    assert "closest('.message-row')" in js_source
    assert 'data-optimistic-user="1"' in js_source
    assert "getLatestOptimisticUserArticle" in js_source
    assert "portal-statusline" in js_source
    assert "state.detailOpen = false" in js_source
    assert 'document.documentElement.getAttribute("data-theme")' in js_source
    assert 'toolPanelTitle?.textContent === "Sessions"' not in js_source
    assert ".assistant-header" not in js_source
    assert ".flex.flex-col" not in js_source
    assert "message message-error flex gap-3 py-3" not in js_source
    assert "buildPendingAssistantRowForEvents" in js_source

    assert ".portal-tool-panel" in css_source
    assert ".portal-tool-panel-head" in css_source
    assert ".portal-tool-panel-body" in css_source
    assert ".portal-detail-card" in css_source
    assert ".message-surface-error" in css_source
    assert ".portal-detail-action-grid" in css_source
    assert ".portal-detail-action-btn" in css_source
    assert ".portal-thinking-block" in css_source
    assert ".portal-thinking-toggle" in css_source
    assert ".portal-statusline.is-error" in css_source
    assert ":disabled" in css_source

    assert '"timestamp": datetime.now().strftime("%H:%M")' in web_source
