"""The create dialog shows nine types at once, on cards of one height.

Measured at 1366 x 768 on 2026-11-17 and 2026-11-18, in a page serving the
real stylesheet and script against stub types.

A description had no length limit and the card showed all of it, so a card
with a paragraph grew to 278px in the create dialog (317px in the first-run
tour) and stretched its row neighbour with it. The dialog was a 600 x 520
frame (tests/test_steady_layers.py) whose list had 232px of room, and the
tour's step 340px, so neither showed a single type in full: two cut-off cards
and a scrollbar.

Now a description is two lines on the card and the rest is a tooltip, so every
card is 88px, and the admin form stops a paragraph where it is typed. The
dialog is the size of the wizard, 900 x 600: three cards across at 278px each,
and 312px for the list, where three rows take 286px. A tenth type scrolls.
In a window under 900px wide the dialog is two across, and under 620px one.
The tour's card is still 560 x 480 and two across.
"""

import re
from pathlib import Path

from tests.test_assistant_type_editing import _render_panel

CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
SETUP_JS = Path("app/static/js/assistant_setup.js").read_text(encoding="utf-8")

THREE_ACROSS = re.compile(
    r"@media \(min-width: 900px\) \{\s*"
    r"#create-simple-modal \.portal-assistant-type-grid \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}"
)


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


def test_the_create_dialog_is_three_cards_across_in_the_wizards_frame():
    # Only in a window that leaves the dialog its width, and not in the tour,
    # whose card is 560px.
    assert THREE_ACROSS.search(CSS)

    frame = _rule("#create-simple-modal > .modal-card")
    assert "width: min(900px, calc(100vw - 2rem))" in frame
    # 80px more than before: the third row.
    assert "height: min(600px, calc(100vh - 2rem))" in frame

    # The same numbers as the wizard that Advanced setup opens from here.
    wizard = CSS.split(".modal-card.create-agent-wizard-card {")
    assert "width: min(900px, calc(100vw - 2rem))" in wizard[1].split("}", 1)[0]
    assert "--portal-layer-height: 600px" in wizard[2].split("}", 1)[0]


def test_the_rows_are_as_far_apart_as_the_columns():
    # A card is a label, and a label in a .stack form has a 5.6px bottom margin,
    # which put the rows 13.6px apart beside columns 8px apart.
    assert "margin: 0" in _rule(".portal-assistant-type-grid > label.portal-assistant-type-option")


def test_a_selected_card_is_scrolled_into_view_with_its_focus_ring():
    # Arrow keys move the selection past the rows the list has room for.
    assert 'option.scrollIntoView({ block: "nearest" })' in SETUP_JS
    assert "scroll-margin: 4px" in _rule(".portal-assistant-type-option")


def test_the_admin_form_caps_the_description_on_create_and_edit():
    html = _render_panel()

    assert html.count('name="description" class="portal-form-input" maxlength="80"') == 2
    assert html.count("Keep it to one short sentence: the create card shows only two lines.") == 2
