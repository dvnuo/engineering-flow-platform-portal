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
    # A runtime without the name endpoint is asked once, not after every turn.
    assert "sessionNameEndpointMissing" in js
    # A share change updates the one sessions row in place.
    assert "function updateSessionsPanelRowShare(" in js
    assert "cssEscapeForSelector(String(sessionId))" in js
    # The Stop sharing confirm takes Escape itself.
    assert ':not(.portal-share-dialog-root)' in js


def test_sessions_panel_offers_share_beside_rename_and_delete():
    html = _read("app/templates/partials/sessions_panel.html")
    assert 'data-session-action="share"' in html
    assert 'data-session-action="rename"' in html
    assert 'data-session-action="delete"' in html
    assert "is-shared" in html
    assert "data-session-name-source" in html
    assert 'data-session-shared-badge="1"' in html


def test_share_page_is_standalone():
    html = _read("app/templates/share.html")
    assert "share_view.js" in html
    assert "efp_cards.js" in html
    assert "chat_ui.js" not in html
    assert 'id="share-transcript"' in html
    assert 'id="share-toc"' in html
    assert 'id="share-show-tools"' in html
    assert "{% block head %}" in html
    base = _read("app/templates/base.html")
    assert "{% block head %}{% endblock %}" in base
    # The page loads only what it renders with: the app shell's scripts stay
    # behind the block it overrides.
    assert "{% block head_scripts %}" in base
    assert "{% block head_scripts %}" in html
    for script in ("lib/markdown-it.min.js", "lib/highlight.min.js", "lib/lucide.min.js"):
        assert script in html
    for script in ("lib/alpine.min.js", "lib/htmx.min.js", "js/dialogs.js", "js/tooltips.js", "js/chat_ui.js"):
        assert script not in html


def test_share_view_renders_like_the_chat():
    js = _read("app/static/js/share_view.js")
    assert "window.markdownit" in js
    assert "html: false" in js
    assert "md.validateLink = safeLink" in js
    assert "portal-mermaid-src" in js
    assert "securityLevel: \"strict\"" in js
    assert "suppressErrorRendering: true" in js
    assert "EfpCards.buildFromCode(code, { readonly: true, surface: \"share\" })" in js
    # The data URL comes from the page, never from a path the script builds.
    assert "page.dataset.shareDataUrl" in js
    assert 'fetch(url, { credentials: "same-origin"' in js
    assert "AUTO_REFRESH_MS = 30000" in js
    assert "login_url" in js
    # Tool steps toggle by class; a refresh with the same transcript keeps the DOM.
    assert "is-tools-hidden" in js
    assert "function transcriptFingerprint(" in js
    # Open in Portal lands on this session (chat_ui.js getLastSessionKey).
    assert 'LAST_SESSION_KEY_PREFIX = "portal-last-session-"' in js
    assert "getLastSessionKey(agentId)" in _read("app/static/js/chat_ui.js")
    assert "`portal-last-session-${agentId}`" in _read("app/static/js/chat_ui.js")
    # Code and tests stay ASCII; the page's typography is written as escapes.
    assert all(ord(ch) < 128 for ch in js)
    css = _read("app/static/css/share.css")
    assert "@media print" in css
    assert "--portal-chat-width: 860px" in css
    assert ".share-transcript.is-tools-hidden .share-tool-row { display: none; }" in css


def test_login_scripts_only_return_to_a_share_path():
    js = _read("app/static/js/auth_form.js")
    assert "function postLoginDestination(" in js
    assert "/^\\/share\\/[A-Za-z0-9_-]+\\/?$/" in js
    copilot = _read("app/static/js/login_copilot.js")
    assert "postLoginDestination" in copilot
    # The shared session that sent the member to sign in wins over the
    # server's generic landing page.
    assert 'shared !== "/app" ? shared : (result.redirect || "/app")' in copilot
    login = _read("app/templates/login.html")
    assert "nextPath:" in login
    assert "/login/sso{% if next_path %}?next=" in login
