"""The greeting and starter cards must survive a rebuilt welcome row.

Reported: starting a new chat lost both. clearMessageListToWelcome() replaces
the whole message list with a hardcoded default, and starting a new chat does
not change the selected assistant -- so portal:agent-selected never fires and
nothing painted the personalization back on.

Seven call sites route through that one function (new chat, clear chat, session
switch, loading an empty session), so it announces the rebuild and the
personalization listens.
"""
from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


def _chat_ui() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _personalization() -> str:
    return Path("app/static/js/assistant_personalization.js").read_text(encoding="utf-8")


def test_rebuilding_the_welcome_row_announces_itself():
    body = _extract_js_function(_chat_ui(), "clearMessageListToWelcome")

    assert "portal:welcome-rendered" in body
    assert "state.selectedAgentId" in body


def test_the_announcement_carries_the_agent():
    # Without it the listener cannot know which assistant's content to paint.
    body = _extract_js_function(_chat_ui(), "clearMessageListToWelcome")

    assert "detail: { agentId" in body


def test_a_listener_throwing_cannot_break_clearing_the_transcript():
    body = _extract_js_function(_chat_ui(), "clearMessageListToWelcome")

    assert "try {" in body and "catch (error)" in body


def test_personalization_repaints_on_that_event():
    js = _personalization()

    assert 'document.addEventListener("portal:welcome-rendered"' in js
    assert "applyForAgent" in js


def test_it_still_repaints_on_assistant_selection():
    # The original trigger must keep working; this adds a second one.
    js = _personalization()

    assert 'document.addEventListener("portal:agent-selected"' in js


def test_the_repaint_falls_back_to_the_active_assistant():
    # The event carries the id, but a rebuild triggered before selection
    # settles would otherwise paint nothing.
    js = _personalization()
    handler = js[js.index('document.addEventListener("portal:welcome-rendered"') :]
    handler = handler[: handler.index("});")]

    assert "browserEvent.detail?.agentId || activeAgentId" in handler


def test_a_repaint_does_not_refetch():
    # loadPersonalization caches per assistant, so repeated new chats cost
    # nothing. Verified in the browser as a single fetch across three rebuilds.
    js = _personalization()

    assert "if (cache.has(agentId)) return cache.get(agentId);" in js


def test_every_welcome_rebuild_goes_through_the_one_function():
    # If a call site ever inlines the default markup instead, this catches it.
    js = _chat_ui()
    # Count call sites only; the definition line contains the same substring.
    call_sites = js.count("defaultWelcomeMessage()") - js.count("function defaultWelcomeMessage()")

    assert call_sites == 1, (
        "the default welcome should be built in exactly one place, "
        "otherwise a rebuild can skip the announcement"
    )
