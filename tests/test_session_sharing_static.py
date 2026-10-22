"""Static wiring of session names, the share dialog and the share page."""

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _share_dialog_source() -> str:
    js = _read("app/static/js/chat_ui.js")
    start = js.index("async function openSessionShareDialog(")
    return js[start:js.index("async function deleteSessionForAgent(", start)]


def test_app_shell_has_no_session_header_and_keeps_the_share_switch():
    html = _read("app/templates/app.html")
    # The bar above the transcript is gone: the sessions panel shows, renames
    # and shares sessions.
    for marker in ('id="session-header"', "data-session-header-action", "portal-session-header"):
        assert marker not in html
    assert "portal-session-header" not in _read("app/static/css/app.css")
    assert "data-share-anyone-enabled=" in html


def test_chat_ui_keeps_session_names_current_in_the_sessions_panel():
    js = _read("app/static/js/chat_ui.js")
    for removed in ("renderSessionHeader", "sessionHeader", "regenerateSessionTitleForAgent", "refreshSessionShareBadge", "data-session-header-action"):
        assert removed not in js
    assert "function setSessionName(" in js
    assert "function scheduleSessionTitleRefresh(" in js
    assert "function refreshSessionName(" in js
    assert "/api/sessions/${encodeURIComponent(normalizedSessionId)}/name" in js
    assert "setSessionName(agentId, normalized, data?.name, data?.name_source)" in js
    assert "scheduleSessionTitleRefresh(agentIdAtSend" in js
    assert "updateSessionsPanelRowName(normalizedSessionId, data.name, data.name_source)" in js
    # A runtime without the name endpoint is asked once, not after every turn.
    assert "sessionNameEndpointMissing" in js
    # The sessions panel opens the share dialog; a change updates its row in place.
    assert 'action === "share"' in js
    assert "function openSessionShareDialog(" in js
    assert "sessions/${encodeURIComponent(sessionId)}/share" in js
    assert "function updateSessionsPanelRowShare(" in js
    assert "cssEscapeForSelector(String(sessionId))" in js


def test_share_dialog_offers_anyone_first_and_saves_an_audience_change_at_once():
    dialog = _share_dialog_source()
    # A new link offers the widest audience the Portal allows: anyone with the
    # link while SESSION_SHARE_ANYONE_ENABLED is on.
    assert 'let selectedScope = share ? scopeOf(share.scope) : scopeOf("anyone");' in dialog
    assert dialog.index('value="anyone"') < dialog.index('value="members"')
    # Once the link exists, picking the other audience saves it; there is no
    # Update step that a member could skip by closing the dialog.
    assert 'input.addEventListener("change"' in dialog
    assert "saveShare(selectedScope, { create: false })" in dialog
    assert 'submitBtn.classList.toggle("hidden", Boolean(share))' in dialog
    assert "Update" not in dialog
    # Who can open the link right now, under the link.
    assert "data-share-status" in dialog
    assert "SHARE_SCOPE_COPY[scopeOf(share.scope)]" in dialog
    # The Stop sharing confirm takes Escape itself.
    assert ':not(.portal-share-dialog-root)' in dialog


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
    for script in ("lib/markdown-it.min.js", "js/markdown_cjk_friendly.js", "lib/highlight.min.js", "lib/lucide.min.js"):
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
    # The member's turns are bubbles on the right, the assistant's answers plain text.
    assert "share-msg--user" in js
    assert "share-msg--assistant" in js
    css = _read("app/static/css/share.css")
    assert "@media print" in css
    assert 'html[data-theme="dark"]' in css
    # One centred column with the contents in the margin, both placed
    # explicitly: a hidden table of contents (a one-question session) must not
    # pull the conversation into the narrow margin column.
    assert ".share-aside { grid-column: 1;" in css
    assert ".share-column { grid-column: 2;" in css
    assert ".share-transcript.is-tools-hidden .share-tool-row { display: none; }" in css
    assert all(ord(ch) < 128 for ch in css)


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
