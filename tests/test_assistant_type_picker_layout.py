"""A type's card is the same height however long its description is.

Measured on 2026-11-17 at 1366 x 768 with six types, two of them carrying a
paragraph for a description. A description had no length limit and the card
showed all of it, so a card grew to 278px in the create dialog (317px in the
first-run tour) and stretched its row neighbour with it. The dialog's list has
232px of room in its frame (tests/test_steady_layers.py) and the tour's step
340px, so neither showed a single type in full: two cut-off cards and a
scrollbar. Now a description is two lines on the card and the rest is a
tooltip, every card is 88px (four types fit the dialog's list without
scrolling), and the admin form stops a paragraph where it is typed.
"""

from pathlib import Path

from tests.test_assistant_type_editing import _render_panel

CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
SETUP_JS = Path("app/static/js/assistant_setup.js").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    return CSS.split(selector + " {", 1)[1].split("}", 1)[0]


def test_a_description_is_two_lines_on_the_card():
    rule = _rule(".portal-assistant-type-copy small")

    assert "-webkit-line-clamp: 2" in rule
    assert "overflow: hidden" in rule
    # An unbroken token (a URL) wraps instead of running out of the card.
    assert "overflow-wrap: anywhere" in rule


def test_a_cut_off_description_is_a_tooltip():
    # Only when it is cut off: a tooltip repeating the text under it is noise.
    assert "description.scrollHeight > description.clientHeight + 1" in SETUP_JS
    assert 'description.setAttribute("data-tooltip", description.textContent)' in SETUP_JS
    assert 'description.removeAttribute("data-tooltip")' in SETUP_JS


def test_a_selected_card_is_scrolled_into_view_with_its_focus_ring():
    # Arrow keys move the selection past the rows the list has room for.
    assert 'option.scrollIntoView({ block: "nearest" })' in SETUP_JS
    assert "scroll-margin: 4px" in _rule(".portal-assistant-type-option")


def test_the_admin_form_caps_the_description_on_create_and_edit():
    html = _render_panel()

    assert html.count('name="description" class="portal-form-input" maxlength="80"') == 2
    assert html.count("Keep it to one short sentence: the create card shows only two lines.") == 2
