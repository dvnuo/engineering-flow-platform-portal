"""The create dialog and the first-run tour show nine types at once, on cards of one height.

Measured at 1366 x 768 on 2026-11-17 and 2026-11-18, in a page serving the
real stylesheet and script against stub types.

A description had no length limit and the card showed all of it, so a card
with a paragraph grew to 278px in the create dialog (317px in the tour) and
stretched its row neighbour with it. The dialog was a 600 x 520 frame
(tests/test_steady_layers.py) whose list had 232px of room, and the tour a
560 x 480 one whose step had 340px, so neither showed a single type in full:
two cut-off cards and a scrollbar.

Now a description is two lines on the card and the rest is a tooltip, so every
card is 88px, and the admin form stops a paragraph where it is typed. The list
is three cards across in both layers, in a window at least 900px wide (two
across under that, one under 620px).

The dialog is the size of the wizard, 900 x 600: cards of 278px, and 312px for
the list, where three rows take 286px. A tenth type scrolls.

The tour is 900 x 720. 140px of that is not the step, and its last step is
530px with three rows in it under the heading and the name field, so nine show
without scrolling in a window at least 720px tall. In one of 650px the card is
602px and six show. Its two steps of text keep a column of about 500px in the
middle of the card.
"""

import re
from pathlib import Path

from tests.test_assistant_type_editing import _render_panel

CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
SETUP_JS = Path("app/static/js/assistant_setup.js").read_text(encoding="utf-8")
APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")

THREE_ACROSS = re.compile(
    r"@media \(min-width: 900px\) \{\s*"
    r"\.portal-assistant-type-grid \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}"
)


def _rule(selector: str) -> str:
    return CSS.split(selector + " {", 1)[1].split("}", 1)[0]


def _rules(selector: str) -> list[str]:
    """Every block the stylesheet has for exactly this selector, in order."""
    return re.findall(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", CSS)


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


def test_the_list_is_three_cards_across_wherever_it_is_shown():
    # One rule for the dialog and the tour, in a window that leaves them their
    # 900px.
    assert THREE_ACROSS.search(CSS)


def test_the_create_dialog_is_the_wizards_frame():
    frame = _rule("#create-simple-modal > .modal-card")
    assert "width: min(900px, calc(100vw - 2rem))" in frame
    # 80px more than before: the third row.
    assert "height: min(600px, calc(100vh - 2rem))" in frame

    # The same numbers as the wizard that Advanced setup opens from here.
    base, steady = _rules(".modal-card.create-agent-wizard-card")
    assert "width: min(900px, calc(100vw - 2rem))" in base
    assert "--portal-layer-height: 600px" in steady


def test_the_tour_is_as_wide_as_the_dialog_and_tall_enough_for_three_rows():
    base, steady = _rules(".portal-onboarding-card")
    assert "width: min(900px, 100%)" in base
    # The last step needs a card of about 670px; 720px is the tall wizard's height.
    assert "height: min(720px, calc(100vh - 48px))" in steady
    assert _rule(".modal-card.create-agent-wizard-card.is-tall").strip() == "--portal-layer-height: 720px;"


def test_the_tours_steps_of_text_keep_their_column_in_the_middle_of_the_card():
    text_step = ".portal-onboarding-card > .portal-onboarding-step:not(.is-form)"
    assert "padding-inline: max(3px, calc(50% - 250px))" in _rule(text_step)
    # Auto margins, which give way when a short window makes the step scroll.
    assert "margin-top: auto" in _rule(text_step + " > :first-child")
    assert "margin-bottom: auto" in _rule(text_step + " > :last-child")

    # The step with the list is the one that is not: it starts at the top,
    # because the list arrives after the step shows.
    assert APP_HTML.count('class="portal-onboarding-step is-form hidden" data-onboarding-step="2"') == 1
    assert APP_HTML.count("portal-onboarding-step is-form") == 1
    assert APP_HTML.count('class="portal-onboarding-step') == 3


def test_the_tours_icon_keeps_its_shape_when_the_step_scrolls():
    assert "flex: 0 0 auto" in _rule(".portal-onboarding-art")


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
