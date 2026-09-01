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


# ------------------------------------------------------------- card input


def test_a_card_asks_through_the_app_dialog_not_window_prompt():
    # window.prompt is unstyled, ignores the theme, and has nowhere to put the
    # card's title -- so the question arrives without the context that makes it
    # answerable. dialogs.js exists precisely to replace it.
    js = _personalization()
    body = _extract_js_function(js, "askForCardInput")

    assert "window.showPrompt" in body
    assert body.index("window.showPrompt") < body.index("window.prompt("), (
        "the app dialog must be tried before the native fallback"
    )


def test_the_dialog_carries_the_card_title_and_its_own_label():
    body = _extract_js_function(_personalization(), "askForCardInput")

    assert "title: card.title" in body
    assert "message: label" in body
    assert "placeholder," in body


def test_the_value_is_required():
    # The prompt only appears for cards that need it, so an empty answer would
    # compose a prompt with a hole where the ticket key should be.
    body = _extract_js_function(_personalization(), "askForCardInput")

    assert "required: true" in body


def test_it_falls_back_when_the_dialog_module_is_absent():
    body = _extract_js_function(_personalization(), "askForCardInput")

    assert 'typeof window.showPrompt === "function"' in body
    assert "window.prompt(" in body


def test_cancelling_leaves_the_composer_alone():
    # showPrompt resolves null on cancel; treating that as an empty string
    # would drop a half-formed prompt into the box.
    js = _personalization()
    handler = js[js.index('document.getElementById("message-list")?.addEventListener("click"') :]
    handler = handler[: handler.index("\n    });")]

    assert "await askForCardInput(card)" in handler, "the dialog is async now"
    assert "if (answer === null) return;" in handler


def test_the_dialog_module_loads_before_this_script():
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")

    assert "js/dialogs.js" in base
    assert "js/assistant_personalization.js" in app_html
