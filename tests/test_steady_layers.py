"""Steady layers: a popup keeps one size from the moment it opens until it closes.

A dialog that grows and shrinks while the member works in it moves both of its
edges (it is centred), and with them the field being typed in and the button
about to be pressed. Wizards did it on every step, pickers on every list that
loaded, and one dialog after another was built that way because nothing said
it should not be.

This file is where it is said. Every popup layer of the Portal is listed in
``LAYERS`` with how it stays put, and a layer that is not listed fails the
first test: whoever adds a dialog decides, here, which of these it is.

    FRAME   the card has a height; the part that changes scrolls inside it
            and the buttons stay on its bottom edge
    KEPT    what shows up later has its room from the start
    HELD    it keeps the height it had when its content is drawn again
    STILL   nothing in it comes or goes while it is open
    VIEWER  it shows one thing and takes that thing's size

The sizes themselves are measured in a browser; what is pinned here is the
markup and the rules that make them hold.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
CSS_RULES = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)

FRAME, KEPT, HELD, STILL, VIEWER = "frame", "kept", "held", "still", "viewer"

# (file, text at the layer's root, how it is held, what happens in it)
LAYERS = [
    ("app/templates/app.html", 'id="create-modal"', FRAME, "four steps"),
    ("app/templates/app.html", 'id="edit-modal"', FRAME, "four steps"),
    ("app/templates/app.html", 'id="message-edit-modal"', STILL, "one text box and one button"),
    ("app/templates/app.html", 'id="file-preview-modal"', VIEWER, "one image or one link"),
    ("app/templates/app.html", 'id="onboarding-overlay"', FRAME, "three steps"),
    ("app/templates/app.html", 'id="create-simple-modal"', FRAME, "the kinds of assistant load after it opens"),
    ("app/templates/partials/assistant_types_panel.html", 'id="assistant-type-create-modal"', STILL, "a form drawn by the server"),
    ("app/templates/partials/task_create_panel.html", "create-task-modal-title", FRAME, "four steps"),
    ("app/templates/partials/task_detail_panel.html", 'id="task-followup-modal"', FRAME, "two steps"),
    ("app/templates/partials/users_panel.html", "data-admin-pending-modal", HELD, "a list that gets shorter with each removal"),
    ("app/templates/partials/users_panel.html", 'id="admin-allowlist-modal"', STILL, "a form; its message line is always there"),
    ("app/static/js/card_picker.js", "portal-picker-root", FRAME, "a list that loads and filters; a form whose lists float"),
    ("app/static/js/chat_ui.js", "portal-share-dialog-root", KEPT, "the link and its status appear on Create link"),
    ("app/static/js/chat_ui.js", "portal-file-move-dialog-root", FRAME, "a folder list reloaded on every click"),
    ("app/static/js/chat_ui.js", "portal-file-choice-dialog-root", STILL, "one question, three buttons"),
    ("app/static/js/chat_ui.js", "create-delegation-modal-title", FRAME, "four steps, and fields that follow the source"),
    ("app/static/js/chat_ui.js", "edit-delegation-modal-title", FRAME, "four steps, and fields that follow the source"),
    ("app/static/js/chat_ui.js", "system-prompt-editor-modal", STILL, "a switch and a text box"),
    ("app/static/js/dialogs.js", '"modal portal-dialog";', STILL, "confirm, prompt and alert"),
    ("app/static/js/mobile_testing.js", "data-recording-dialog", FRAME, "a form, then progress, then a result"),
]

LAYER_ROOT = re.compile(r"""(?:class|className)\s*=\s*\\?["'`](?:modal|portal-onboarding)[\s"'`]""")
SOURCES = sorted((ROOT / "app/templates").rglob("*.html")) + sorted((ROOT / "app/static/js").rglob("*.js"))


def _layer_roots():
    """Every place a layer is made: (file, line, the text around its root)."""
    for path in SOURCES:
        if "lib" in path.relative_to(ROOT).parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in LAYER_ROOT.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            start = max(0, match.start() - 160)
            yield path.relative_to(ROOT).as_posix(), line, text[start: match.start() + 420], match.start() - start


def _nearest_marker(rel: str, window: str, origin: int):
    best = None
    for file, marker, _how, _what in LAYERS:
        if file != rel:
            continue
        at = window.find(marker)
        while at >= 0:
            distance = abs(at - origin)
            if best is None or distance < best[0]:
                best = (distance, marker)
            at = window.find(marker, at + 1)
    return best[1] if best else None


def _declarations(selector: str) -> dict[str, str]:
    """What the stylesheet declares for exactly this selector, later rules winning."""
    wanted = " ".join(selector.split())
    found: dict[str, str] = {}
    for match in re.finditer(r"(?m)^([^{}@/]+)\{([^{}]*)\}", CSS_RULES):
        selectors = [" ".join(part.split()) for part in match.group(1).split(",")]
        if wanted not in selectors:
            continue
        for declaration in match.group(2).split(";"):
            if ":" in declaration:
                name, value = declaration.split(":", 1)
                found[name.strip()] = " ".join(value.split())
    return found


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ------------------------------------------------------------------ inventory


def test_every_layer_is_listed_with_how_it_is_held():
    seen = []
    unlisted = []
    for rel, line, window, origin in _layer_roots():
        marker = _nearest_marker(rel, window, origin)
        if marker is None:
            unlisted.append(f"{rel}:{line}")
        else:
            seen.append((rel, marker))
    assert not unlisted, (
        "A popup layer is not listed in LAYERS. Decide how it keeps its size while it is open "
        f"(see the top of this file), then add it: {unlisted}"
    )
    listed = [(file, marker) for file, marker, _how, _what in LAYERS]
    assert sorted(set(seen)) == sorted(listed), "LAYERS lists a layer that is no longer made"


def test_each_way_of_holding_a_layer_is_one_this_file_explains():
    for _file, marker, how, what in LAYERS:
        assert how in {FRAME, KEPT, HELD, STILL, VIEWER}, marker
        assert what, marker


# --------------------------------------------------------------------- frames

# The card that is the frame, and the part of it that scrolls.
FRAMES = {
    ".modal-card.create-agent-wizard-card": ".modal-card.create-agent-wizard-card > form > .create-agent-step-panel",
    ".portal-dialog-card.portal-picker-dialog.has-list": ".portal-dialog-card.portal-picker-dialog.has-list > .portal-picker-list",
    ".portal-onboarding-card": ".portal-onboarding-card > .portal-onboarding-step",
    "#create-simple-modal > .modal-card": "#create-simple-modal > .modal-card > form > .portal-assistant-type-grid",
    "[data-recording-dialog] > .portal-dialog-card": "[data-recording-dialog] > .portal-dialog-card > .efp-replay-body",
}


@pytest.mark.parametrize("card,scrolling", sorted(FRAMES.items()))
def test_a_frame_has_a_height_and_one_part_that_scrolls(card, scrolling):
    frame = _declarations(card)
    # A height, not a ceiling: `max-height` lets the card follow its content.
    assert frame.get("height", "").startswith("min("), card
    assert "100vh" in frame["height"], "the frame must still fit a short window"
    assert frame.get("max-height", "none") == "none", card
    assert frame.get("display") == "flex" and frame.get("flex-direction") == "column", card

    part = _declarations(scrolling)
    assert part.get("flex") == "1 1 auto" and part.get("min-height") == "0", scrolling
    assert part.get("overflow-y") == "auto" or part.get("overflow") == "auto", scrolling


def test_every_wizard_is_the_one_framed_card():
    cards = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        cards += re.findall(r'class="(modal-card[^"]*create-agent-wizard-card[^"]*)"', text)
    # Create and edit an assistant, create and continue a task, create and edit a delegation.
    assert len(cards) == 6
    wizard = _declarations(".modal-card.create-agent-wizard-card")
    assert wizard["height"] == "min(var(--portal-layer-height), calc(100vh - 2rem))"
    assert wizard["overflow"] == "hidden"
    # The step, not the card, is what scrolls: Back and Next stay where they are.
    assert "overflow-y" not in wizard


def test_every_step_of_a_wizard_is_a_panel_the_frame_can_scroll():
    panels = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        panels += re.findall(r"<section\b[^>]*data-(?:wizard|create|edit)-step-panel=[^>]*>", text)
    assert len(panels) >= 20
    for tag in panels:
        assert "create-agent-step-panel" in tag, tag


def test_the_delegation_wizards_are_the_tall_frame():
    chat = _read("app/static/js/chat_ui.js")
    for title in ("create-delegation-modal-title", "edit-delegation-modal-title"):
        card = chat.split(f'aria-labelledby="{title}"', 1)[1].split(">", 2)[1]
        assert "create-agent-wizard-card is-tall" in card, title
    assert _declarations(".modal-card.create-agent-wizard-card.is-tall")["--portal-layer-height"] == "720px"


def test_the_replay_dialog_draws_its_three_states_in_one_box():
    source = _read("app/static/js/mobile_testing.js")
    assert '<div class="efp-replay-body">${body}</div>' in source


def test_the_folder_picker_keeps_its_list_and_its_path_in_place():
    listing = _declarations(".portal-file-move-list")
    assert listing["height"].startswith("min(") and listing["overflow-y"] == "auto"
    path = _declarations(".portal-file-breadcrumb.portal-file-move-path")
    assert path["flex-wrap"] == "nowrap" and path["overflow-x"] == "auto"
    # One line, and the end of it (the folder being looked at) is what shows.
    assert "crumbsEl.scrollLeft = crumbsEl.scrollWidth;" in _read("app/static/js/chat_ui.js")


# --------------------------------------------------------------------- picker


def test_a_picker_with_a_source_is_a_frame_from_the_moment_it_opens():
    source = _read("app/static/js/card_picker.js")
    picker = source.split("function showPicker(", 1)[1].split("function optionList(", 1)[0]
    assert "const framed = Boolean(opts.source);" in picker
    assert 'framed ? "has-list" : ""' in picker
    assert "steady: framed," in picker
    # Its list is in the dialog from the start, not shown once rows arrive.
    assert 'class="portal-picker-list${framed ? "" : " hidden"}"' in picker
    # Values used before can only be filtered down: the list keeps its height.
    assert "if (!framed && list.count()) list.keepSize();" in picker


def test_a_forms_lists_float_over_the_form():
    source = _read("app/static/js/card_picker.js")
    form = source.split("function showCardForm(", 1)[1]
    # Outside the card, so nothing in the form moves when one opens.
    assert "shell.root.appendChild(control.popEl);" in form
    assert 'control.popEl.classList.toggle("hidden", !open);' in form
    assert 'control.listEl.classList.toggle("hidden"' not in form
    # Escape closes an open list before it closes the dialog.
    escape = form.split("shell.setEscape(", 1)[1].split("shell.confirmEl", 1)[0]
    assert escape.index('showList("")') < escape.index("finish(null)")

    pop = _declarations(".portal-picker-pop")
    assert pop["position"] == "fixed"
    inside = _declarations(".portal-picker-pop > .portal-picker-list")
    assert inside["max-height"] == "none" and inside["min-height"] == "0"


# ----------------------------------------------------------------- kept, held


def test_the_share_dialog_has_the_links_row_before_the_link_exists():
    chat = _read("app/static/js/chat_ui.js")
    dialog = chat.split("async function openSessionShareDialog(", 1)[1].split("async function deleteSessionForAgent(", 1)[0]
    assert '<div class="portal-share-link" data-share-link-row>' in dialog
    assert '<p class="portal-share-status" data-share-status aria-live="polite"></p>' in dialog
    # Empty until Create link, never taken out of the dialog.
    assert 'linkRow.classList.toggle("hidden"' not in dialog
    assert 'statusLine.classList.toggle("hidden"' not in dialog
    assert "copyBtn.disabled = !share;" in dialog
    assert 't("Not shared yet.")' in dialog


def test_the_pending_list_comes_back_at_the_height_it_had():
    admin = _read("app/static/js/admin_users.js")
    assert 'card.style.minHeight = keepHeight ? keepHeight + "px" : "";' in admin
    assert "var heldHeight = fromPendingList ? pendingModalHeight() : 0;" in admin
    assert "openPendingModal(heldHeight);" in admin


def test_lines_that_come_and_go_keep_their_room():
    # A dialog's message line, with or without a message in it.
    assert _declarations(".portal-modal-feedback")["min-height"].endswith("px")
    # The browser panel's state line, with or without its button.
    assert _declarations(".composer-browser-window-state")["min-height"] == "30px"
