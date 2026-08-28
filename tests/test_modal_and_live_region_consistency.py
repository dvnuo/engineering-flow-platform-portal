"""All four big modals behave the same way, and dynamic regions are announced.

Before this, Create Assistant / Edit Assistant / Create Runtime Profile answered
neither Escape nor a backdrop click and focused nothing on open, while Edit
Message did all three — and registered a fresh document keydown listener on
every open that was only removed if the user happened to close it with Escape.

Separately, the app's three main dynamic regions (toast, status line, message
list) carried no aria-live at all, so a screen reader user was told nothing
about operation results, assistant state, or completed responses.
"""

from pathlib import Path

import pytest

JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "modal_id,close_id",
    [
        ("create-modal", "close-create-modal"),
        ("edit-modal", "close-edit-modal"),
        ("create-runtime-profile-modal", "close-create-runtime-profile-modal"),
        ("message-edit-modal", "close-message-edit-modal"),
    ],
)
def test_every_big_modal_is_registered_with_the_shared_behaviour_layer(modal_id, close_id):
    assert f'{{ modalId: "{modal_id}", closeId: "{close_id}" }}' in JS
    assert f'id="{modal_id}"' in APP_HTML
    assert f'id="{close_id}"' in APP_HTML


def test_shared_layer_provides_escape_backdrop_focus_and_a_tab_trap():
    assert "function initManagedModals()" in JS
    assert 'if (event.key === "Escape")' in JS
    assert "if (event.target !== modal) return;" in JS
    assert "function focusFirstFieldInModal(modal)" in JS
    assert 'if (event.key !== "Tab") return;' in JS
    assert "initManagedModals();" in JS


def test_closing_goes_through_each_modals_own_close_button():
    # Those handlers hold the in-flight-submit guard; bypassing them would let
    # Escape discard a create that is already in progress.
    assert "function requestManagedModalClose(modal)" in JS
    assert "if (closeButton) closeButton.click();" in JS


def test_message_edit_modal_no_longer_registers_per_open_listeners():
    opener = JS.split("function openEditMessageModal(", 1)[1].split("\nfunction ", 1)[0]
    assert 'document.addEventListener("keydown", handleEsc)' not in opener
    assert "handleOutsideClick" not in opener


def test_toast_status_line_and_message_list_are_live_regions():
    assert 'id="global-toast"' in APP_HTML and 'aria-live="polite"' in APP_HTML
    assert 'id="chat-status" class="portal-statusline" role="status" aria-live="polite"' in APP_HTML
    assert 'id="chat-live-region" class="sr-only" role="status" aria-live="polite"' in APP_HTML


def test_errors_are_announced_assertively_but_routine_toasts_are_not():
    assert "stack.setAttribute('aria-live', isError ? 'assertive' : 'polite');" in JS


def test_completed_responses_are_announced_without_reading_the_stream():
    assert "function announceToChat(" in JS
    assert 'announceToChat("Assistant response ready.")' in JS


def test_sr_only_is_defined_locally_rather_than_left_to_the_tailwind_runtime():
    assert ".sr-only {" in CSS
    rule = CSS.split(".sr-only {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule
    assert "clip: rect(0, 0, 0, 0)" in rule
