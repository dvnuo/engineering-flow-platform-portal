"""The composer floats over the conversation, so the layout has to give it room.

`.portal-composer-wrap` is absolutely positioned at the foot of the scroll area
with a translucent, blurred background. Anything the message list puts under it
shows through and cannot be clicked. The list therefore has to reserve space of
its own, and a card that must stay usable has to fit in what is left.

Both sizes are measured at runtime rather than assumed, because the composer
grows for several unrelated reasons -- a multi-line draft, an attachment strip,
the skill chip, the stop button -- and each of them has to move the reserve.

Geometry cannot be asserted without a layout engine, so these pin the wiring:
that the sizes are published, that the two consumers read them, and that the
scroll correction which keeps a question on screen is still there.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


CHAT_UI = Path("app/static/js/chat_ui.js")
INTERACTIVE = Path("app/static/js/interactive_input.js")
CSS = Path("app/static/css/app.css")

COMPOSER_VAR = "--portal-composer-height"
VIEWPORT_VAR = "--portal-chat-viewport-height"


def _rules(selector: str) -> list[str]:
    """Every block for this selector, including the ones inside media queries."""
    css = CSS.read_text(encoding="utf-8")
    parts = css.split(selector + " {")[1:]
    assert parts, f"{selector} is not defined"
    return [part.split("}", 1)[0] for part in parts]


def _rule(selector: str) -> str:
    rules = _rules(selector)
    assert len(rules) == 1, f"{selector} has {len(rules)} blocks; assert on all of them"
    return rules[0]


def _run_node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping scroll behaviour test")
    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


# ------------------------------------------------------- the bottom reserve


def test_every_bottom_reserve_follows_the_composer_rather_than_a_fixed_guess():
    # A flat 200px was the desktop value and 152px the narrow one. A composer
    # with eight lines of draft is 288px, and the narrow layout stacks its
    # toolbar, so it is taller there, not shorter. Everything past the reserve
    # sat on top of the last message.
    rules = _rules(".portal-message-list")

    assert len(rules) == 2, "one base rule and one narrow-screen override"
    for rule in rules:
        assert f"var({COMPOSER_VAR}" in rule, "the reserve must track the measured composer"
        assert "200px)" in rule or "200px);" in rule
        assert "152px" not in rule


def test_every_reserve_keeps_a_value_for_the_first_paint():
    # The stylesheet loads before any script runs, so the fallback is what the
    # first paint uses.
    for rule in _rules(".portal-message-list"):
        assert f"var({COMPOSER_VAR}, 200px)" in rule


def test_every_reserve_clears_the_composer_rather_than_just_matching_it():
    for rule in _rules(".portal-message-list"):
        assert "+ 24px" in rule, "the last message should not sit flush against the composer"


# ------------------------------------------------- the card that must fit


def test_the_card_body_is_capped_and_scrolls_on_its_own():
    # Four questions with options are ~1128px of content. The heading says what
    # is being asked and the actions are how you answer, so the questions are
    # what gives way.
    rule = _rule(".portal-interactive-body")

    assert "overflow-y: auto" in rule
    assert f"var({VIEWPORT_VAR}" in rule
    assert f"var({COMPOSER_VAR}" in rule


def test_the_cap_has_a_floor_that_cannot_pad_a_short_card():
    # A permission card with no preview is 58px of body. `min-height` would
    # stretch it to the floor and leave blank space under the checkbox; max()
    # gives the same protection without that.
    rule = _rule(".portal-interactive-body")

    assert "max(" in rule
    assert "min-height" not in rule


def test_a_tiny_window_cannot_drive_the_cap_negative():
    # 420px tall minus a 316px composer minus 160px of card chrome is negative;
    # a negative max-height would drop the declaration and un-cap the card.
    rule = _rule(".portal-interactive-body")
    floor = rule.split("max(", 1)[1].split(",", 1)[0].strip()

    assert floor.endswith("px")
    assert int(floor.removesuffix("px")) > 0


# ----------------------------------------------------------- the publisher


def test_both_sizes_are_published_from_one_place():
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "trackChatSurfaceSizes")

    assert COMPOSER_VAR in fn
    assert VIEWPORT_VAR in fn
    assert "trackChatSurfaceSizes();" in js, "nothing calls it"


def test_the_sizes_are_observed_and_not_computed_once():
    # The composer grows from a draft, an attachment, the skill chip, and the
    # stop button. Measuring at startup only would be right for about a second.
    fn = _extract_js_function(CHAT_UI.read_text(encoding="utf-8"), "trackChatSurfaceSizes")

    assert "ResizeObserver" in fn
    assert fn.count(".observe(") == 2, "both the composer and the scroll area change size"


def test_an_engine_without_resizeobserver_still_tracks_the_common_cases():
    fn = _extract_js_function(CHAT_UI.read_text(encoding="utf-8"), "trackChatSurfaceSizes")
    fallback = fn.split("} else {", 1)[1]

    assert 'addEventListener("input"' in fallback
    assert 'addEventListener("resize"' in fallback


# --------------------------------------------- keeping the question on screen


def test_mounting_a_card_gives_back_whatever_the_jump_to_bottom_cut_off():
    """The real mountCard, over a fake scroller that reports real geometry."""
    source = INTERACTIVE.read_text(encoding="utf-8")
    mount = _extract_js_function(source, "mountCard")
    script = f"""
const calls = [];
let scrollTop = 0;
const SCROLL_TOP = 100;      // where the scroll viewport starts on screen
const CARD_TOP_AT_BOTTOM = 40;  // card's top once scrolled fully down: 60px cut off

const scroll = {{
  get scrollTop() {{ return scrollTop; }},
  set scrollTop(v) {{ scrollTop = v; calls.push(v); }},
  scrollHeight: 5000,
  getBoundingClientRect: () => ({{ top: SCROLL_TOP }}),
}};
const row = {{
  id: "", className: "", innerHTML: "",
  querySelector: () => null,
  getBoundingClientRect: () => ({{ top: CARD_TOP_AT_BOTTOM + (scrollTop - 5000) }}),
}};
const list = {{ append: () => {{}} }};
globalThis.document = {{
  getElementById: (id) => (id === "message-scroll" ? scroll : id === "message-list" ? list : null),
  createElement: () => row,
}};
globalThis.window = {{ setTimeout: () => {{}} }};
function renderIcons() {{}}
const CARD_ID = "portal-interactive-input";
{mount}
mountCard("<form></form>");
console.log(JSON.stringify({{ calls, finalScrollTop: scrollTop }}));
"""
    result = _run_node(script)

    # First the jump to the bottom, then the correction back up by the 60px of
    # card that the jump hid above the viewport.
    assert result["calls"][0] == 5000
    assert result["finalScrollTop"] == 4940


def test_the_correction_never_scrolls_a_card_that_already_fits():
    source = INTERACTIVE.read_text(encoding="utf-8")
    mount = _extract_js_function(source, "mountCard")
    script = f"""
let scrollTop = 0;
const scroll = {{
  get scrollTop() {{ return scrollTop; }},
  set scrollTop(v) {{ scrollTop = v; }},
  scrollHeight: 5000,
  getBoundingClientRect: () => ({{ top: 100 }}),
}};
// Card top is below the viewport top: nothing was cut off.
const row = {{ id: "", className: "", innerHTML: "", querySelector: () => null,
  getBoundingClientRect: () => ({{ top: 260 }}) }};
globalThis.document = {{
  getElementById: (id) => (id === "message-scroll" ? scroll : id === "message-list" ? {{ append: () => {{}} }} : null),
  createElement: () => row,
}};
globalThis.window = {{ setTimeout: () => {{}} }};
function renderIcons() {{}}
const CARD_ID = "portal-interactive-input";
{mount}
mountCard("<form></form>");
console.log(JSON.stringify({{ finalScrollTop: scrollTop }}));
"""

    assert _run_node(script)["finalScrollTop"] == 5000
