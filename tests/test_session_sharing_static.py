"""Static wiring of the session header, the share dialog and the share page."""

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_app_shell_has_the_session_header_and_the_share_switch():
    html = _read("app/templates/app.html")
    assert 'id="session-header"' in html
    assert 'id="session-header-title"' in html
    assert 'data-session-header-action="rename"' in html
    assert 'data-session-header-action="regenerate"' in html
    assert 'data-session-header-action="share"' in html
    assert "data-share-anyone-enabled=" in html
    # The header sits inside the chat section so it hides with it.
    assert html.index('id="session-header"') > html.index('id="agent-chat-app"')
    assert html.index('id="session-header"') < html.index('id="message-scroll"')


def test_chat_ui_reads_the_name_with_the_transcript_and_asks_again_after_a_turn():
    js = _read("app/static/js/chat_ui.js")
    assert "function setSessionName(" in js
    assert "function renderSessionHeader(" in js
    assert "function scheduleSessionTitleRefresh(" in js
    assert "function refreshSessionName(" in js
    assert "/api/sessions/${encodeURIComponent(normalizedSessionId)}/name" in js
    assert "/title/generate" in js
    assert "function openSessionShareDialog(" in js
    assert 'action === "share"' in js
    assert 'closest("[data-session-header-action]")' in js
    assert "setSessionName(agentId, normalized, data?.name, data?.name_source)" in js
    assert "scheduleSessionTitleRefresh(agentIdAtSend" in js
    assert "sessionHeader: document.getElementById(\"session-header\")" in js
    # Helpers reached from other paths are guarded, as the node harnesses stub them.
    assert 'typeof renderSessionHeader === "function"' in js
    assert "sessions/${encodeURIComponent(sessionId)}/share" in js


def test_sessions_panel_offers_share_beside_rename_and_delete():
    html = _read("app/templates/partials/sessions_panel.html")
    assert 'data-session-action="share"' in html
    assert 'data-session-action="rename"' in html
    assert 'data-session-action="delete"' in html
    assert "is-shared" in html
    assert "data-session-name-source" in html


def test_share_page_is_standalone():
    html = _read("app/templates/share.html")
    assert "share_view.js" in html
    assert "efp_cards.js" in html
    assert "chat_ui.js" not in html
    assert 'id="share-transcript"' in html
    assert 'id="share-toc"' in html
    assert 'id="share-show-tools"' in html
    assert "{% block head %}" in html
    assert "{% block head %}{% endblock %}" in _read("app/templates/base.html")


def test_share_view_renders_like_the_chat():
    js = _read("app/static/js/share_view.js")
    assert "window.markdownit" in js
    assert "html: false" in js
    assert "portal-mermaid-src" in js
    assert "securityLevel: \"strict\"" in js
    assert "EfpCards.buildFromCode" in js
    # The data URL comes from the page, never from a path the script builds.
    assert "page.dataset.shareDataUrl" in js
    assert 'fetch(url, { credentials: "same-origin"' in js
    assert "AUTO_REFRESH_MS = 30000" in js
    assert "login_url" in js
    css = _read("app/static/css/share.css")
    assert "@media print" in css
    assert "--portal-chat-width: 860px" in css


def test_login_scripts_only_return_to_a_share_path():
    js = _read("app/static/js/auth_form.js")
    assert "function postLoginDestination(" in js
    assert "/^\\/share\\/[A-Za-z0-9_-]+\\/?$/" in js
    assert "postLoginDestination" in _read("app/static/js/login_copilot.js")
    login = _read("app/templates/login.html")
    assert "nextPath:" in login
    assert "/login/sso{% if next_path %}?next=" in login
