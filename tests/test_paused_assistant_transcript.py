"""A paused assistant shows a paused row, not the greeting.

Reported: an assistant in the stopped state showed "Welcome! Ask me anything"
as its whole transcript. The chat view stays up for a paused assistant so a
message can wake it, but its sessions live in the runtime and cannot be
fetched while it is down -- so selection left the loading placeholder in
place, and the fallback for a placeholder was the greeting. The banner said
paused, the composer said paused, the transcript said welcome.

Two more copies of the greeting made it worse: app.html carried its own
(author "Assistant", timestamp "Ready"), on screen before any script ran; and
the personalization module cached the proxy's 409 for a paused assistant as
"no personalization", so the generic greeting outlived the wake.
"""
from pathlib import Path

from tests._js_extract_helpers import _extract_js_function

CHAT_UI = Path("app/static/js/chat_ui.js")
PERSONALIZATION = Path("app/static/js/assistant_personalization.js")
APP_HTML = Path("app/templates/app.html")


def _chat_ui() -> str:
    return CHAT_UI.read_text(encoding="utf-8")


def _block(js: str, opener: str) -> str:
    """The source from `opener` to the next top-level function."""
    body = js.split(opener, 1)[1]
    end = min(i for i in (body.find("\nfunction "), body.find("\nasync function ")) if i > 0)
    return body[:end]


# ------------------------------------------------------------ one greeting


def test_the_generic_greeting_is_defined_once():
    js = _chat_ui()

    assert js.count("Welcome! Ask me anything.") == 1
    assert 'const DEFAULT_WELCOME_MARKDOWN = "' in js
    assert "escapeHtmlAttr(DEFAULT_WELCOME_MARKDOWN)" in _extract_js_function(js, "defaultWelcomeMessage")


def test_the_template_no_longer_carries_its_own_copy():
    html = APP_HTML.read_text(encoding="utf-8")

    assert "Ask me anything" not in html
    assert 'data-welcome="1"' not in html
    # The live region the screen-reader announcements need stays.
    assert 'id="chat-live-region"' in html


# ------------------------------------------------------------- paused row


def test_the_paused_row_is_not_a_welcome():
    # data-welcome is what the personalization paints the greeting onto, and
    # what "a fresh chat" means to the send path. The paused row is neither.
    body = _extract_js_function(_chat_ui(), "assistantPausedMessage")

    assert 'data-assistant-paused="1"' in body
    assert "data-welcome" not in body
    assert "Ask me anything" not in body


def test_the_paused_row_says_what_sending_does():
    body = _extract_js_function(_chat_ui(), "assistantPausedMessage")

    assert "is paused" in body
    assert "opens a new chat" in body
    assert "is starting" in body
    assert "is restarting" in body
    # The timestamp slot carries the same label as the header badge, not "Ready".
    assert "health.label" in body
    assert '">Ready<' not in body


def test_a_paused_assistant_gets_the_row_instead_of_the_greeting():
    sync = _block(_chat_ui(), "async function syncSelectedAgentState() {")

    assert "if (showChat && !running && transcriptShowsNoConversation()) showAssistantPausedState(agent, status);" in sync


def test_a_conversation_already_on_screen_is_left_alone_when_the_assistant_pauses():
    # Idle auto-stop happens underneath the reader; their history must stay.
    body = _extract_js_function(_chat_ui(), "transcriptShowsNoConversation")

    assert 'row.dataset.conversationLoading === "1"' in body
    assert 'row.dataset.assistantPaused === "1"' in body
    assert 'row.dataset.welcome === "1"' in body
    assert ".every(" in body


def test_the_row_follows_the_status_like_the_badge_and_placeholder_do():
    js = _chat_ui()
    apply_status = _extract_js_function(js, "applyLocalAgentStatus")

    assert "updateChatInputPlaceholder();" in apply_status
    assert "refreshAssistantPausedState();" in apply_status

    refresh = _extract_js_function(js, "refreshAssistantPausedState")
    assert 'status === "running"' in refresh
    assert "row.dataset.assistantStatus === status" in refresh


def test_a_start_that_restored_nothing_clears_the_row():
    js = _chat_ui()
    sync = _block(js, "async function syncSelectedAgentState() {")

    # Both exits of the running branch settle the transcript.
    assert sync.count("settleTranscriptAfterStart(agent.id);") == 2

    settle = _extract_js_function(js, "settleTranscriptAfterStart")
    assert "state.selectedAgentId !== agentId" in settle
    assert "clearMessageListToWelcome();" in settle


def test_a_lone_paused_row_makes_way_for_the_first_message():
    body = _extract_js_function(_chat_ui(), "removeWelcomeMessageIfPresent")

    assert '[data-assistant-paused="1"]' in body


def test_the_start_estimate_is_read_not_repeated():
    js = _chat_ui()

    assert "about 40 seconds" not in js
    assert "typicalStartSeconds()" in _extract_js_function(js, "updateChatInputPlaceholder")
    assert "startup?.typical_seconds" in _extract_js_function(js, "typicalStartSeconds")


# --------------------------------------------------------- personalization


def test_a_failed_personalization_fetch_is_not_remembered():
    js = PERSONALIZATION.read_text(encoding="utf-8")
    body = js[js.index("async function loadPersonalization(") :]
    body = body[: body.index("\n  }\n") + 4]

    # The cache still answers repeat rebuilds without a fetch...
    assert "if (cache.has(agentId)) return cache.get(agentId);" in body
    # ...but only a definite answer goes in.
    assert body.count("cache.set(") == 1
    assert "if (settled) cache.set(agentId, payload);" in body
    assert "response.status === 404" in body
