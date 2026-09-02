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


def test_the_first_measurement_lands_when_the_chat_view_becomes_visible():
    # The chat surface is toggled with `display: none`, where everything
    # measures 0. ResizeObserver fires on the transition back; an event-based
    # substitute (input, resize) does not, and would leave the reserve at its
    # fallback until the member happened to type.
    fn = _extract_js_function(CHAT_UI.read_text(encoding="utf-8"), "trackChatSurfaceSizes")

    assert "ResizeObserver" in fn
    assert "} else {" not in fn, (
        "no event-based fallback: it cannot see the display:none transition, and "
        "the stylesheet already requires color-mix(), which is far newer"
    )


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


# ------------------------------------ one load, one paint, at any speed


def test_the_transcript_is_held_still_while_a_conversation_is_fetched():
    """The welcome is an outcome of the load, not a step on the way to it.

    Showing it up front meant showing it and taking it away again for every
    assistant that has a conversation -- and on a throttled connection the gap
    between the two is long enough to read.
    """
    js = CHAT_UI.read_text(encoding="utf-8")
    selection = _extract_js_function(js, "performAgentSelection")

    assert "showConversationLoading();" in selection
    assert "clearMessageListToWelcome();\n\n  await setActiveNavSection" not in js
    # Not every path through a selection ends in a transcript.
    assert "if (conversationIsLoading()) clearMessageListToWelcome();" in selection


def test_the_three_fetches_a_conversation_needs_run_together():
    # Sequentially, each paints as it lands and the order depends on the
    # network, which is why the same load looked different at every speed.
    js = CHAT_UI.read_text(encoding="utf-8")
    body = js.split("async function loadSessionForAgent(", 1)[1]
    body = body[: body.index("\nasync function ", 10)]

    prefetch = body.index("Promise.allSettled(")
    session_fetch = body.index("await agentApiFor(")
    join = body.index("await companions;")
    paint = body.index("renderChatHistory(")

    assert prefetch < session_fetch, "the companions must be in flight before we wait on the session"
    assert session_fetch < join < paint, "and joined before anything is drawn"
    assert "portalPrefetchPersonalization" in body
    assert "portalPrefetchPendingInput" in body


def test_a_prefetched_answer_is_applied_rather_than_refetched():
    js = INTERACTIVE.read_text(encoding="utf-8")
    check = _extract_js_function(js, "checkPendingInput")

    assert "takeFreshPrefetch()" in check, "otherwise the card lands a round trip after the history"
    assert "applyPendingInput(" in check


def test_a_stale_prefetch_is_not_trusted():
    # Held across a session switch or a long pause, it would show something the
    # run has moved on from.
    js = INTERACTIVE.read_text(encoding="utf-8")
    take = _extract_js_function(js, "takeFreshPrefetch")

    assert "held.session !== sessionId()" in take
    assert "PREFETCH_MAX_AGE_MS" in take
    assert "state.prefetched = null;" in take, "a prefetch is good for one paint"


def test_the_placeholder_is_visibly_a_placeholder():
    css = CSS.read_text(encoding="utf-8")

    assert ".portal-conversation-skeleton {" in css
    base = _rules(".portal-skeleton-line")[0]
    assert "animation:" in base
    assert "var(--portal-" in base and "#" not in base
    assert ".portal-skeleton-line { animation: none; }" in css, "respect prefers-reduced-motion"


# ------------------------------------------- the transcript has one owner


def _transcript_bundle() -> str:
    js = CHAT_UI.read_text(encoding="utf-8")
    functions = (
        "beginTranscript",
        "currentTranscriptToken",
        "transcriptTokenIsCurrent",
        "writeTranscript",
        "transcriptShowsConversation",
        "markTranscriptReady",
    )
    decl = js.split("const transcript = {", 1)[1].split("};", 1)[0]
    return "const transcript = {" + decl + "};\n" + "\n".join(
        _extract_js_function(js, name) for name in functions
    )


def test_a_write_for_a_superseded_conversation_is_dropped():
    """This is what makes the order of responses stop mattering.

    A slow answer for the conversation the reader has left has nothing useful
    to say about the one they are looking at now.
    """
    result = _run_node(f"""
{_transcript_bundle()}
const painted = [];
const first = beginTranscript("a1", "s1");
const second = beginTranscript("a1", "s2");
const wroteStale = writeTranscript(first, () => painted.push("stale"));
const wroteCurrent = writeTranscript(second, () => painted.push("current"));
console.log(JSON.stringify({{ wroteStale, wroteCurrent, painted }}));
""")

    assert result == {"wroteStale": False, "wroteCurrent": True, "painted": ["current"]}


def test_the_order_answers_arrive_in_cannot_change_what_is_painted():
    """Every arrival order of three responses produces the same writes.

    This is the throttling report as an assertion: on a fast connection the
    three fetches land close enough together to look like one step, and on a
    slow one the same load used to walk visibly through whatever order they
    happened to return in.
    """
    result = _run_node(f"""
{_transcript_bundle()}
const runs = [];
const orders = [[0,1,2],[0,2,1],[1,0,2],[1,2,0],[2,0,1],[2,1,0]];
for (const order of orders) {{
  const token = beginTranscript("a1", "s1");
  const answers = ["history", "personalization", "pendingInput"];
  const arrived = [];
  // Responses land in this order; none of them paints on arrival.
  for (const index of order) arrived.push(answers[index]);
  const painted = [];
  writeTranscript(token, () => painted.push("compose:" + arrived.length));
  runs.push({{ order: order.join(""), painted }});
}}
console.log(JSON.stringify(runs));
""")

    assert len(result) == 6
    assert {tuple(run["painted"]) for run in result} == {("compose:3",)}, (
        "every arrival order must end in exactly one paint of the complete state"
    )


def test_a_live_run_can_tell_whether_its_conversation_is_still_open():
    # Streaming writes are keyed and incremental -- re-rendering the list for
    # every typewriter delta would cost the reader their scroll position. What
    # they were missing is a way to know they are still relevant.
    result = _run_node(f"""
{_transcript_bundle()}
beginTranscript("a1", "s1");
console.log(JSON.stringify({{
  sameConversation: transcriptShowsConversation("a1", "s1"),
  otherSession: transcriptShowsConversation("a1", "s2"),
  otherAgent: transcriptShowsConversation("a2", "s1"),
  sessionUnknown: transcriptShowsConversation("a1", ""),
}}));
""")

    assert result == {
        "sameConversation": True,
        "otherSession": False,
        "otherAgent": False,
        "sessionUnknown": True,
    }


def test_the_prefetch_is_told_which_conversation_it_is_for():
    # `updateAgentSession` does not run until the session response lands, so
    # reading the session from state here asked about whatever was open before
    # -- nothing at all on a fresh page, which made the prefetch a no-op and put
    # the card a round trip behind the history it belongs to.
    js = CHAT_UI.read_text(encoding="utf-8")
    body = js.split("async function loadSessionForAgent(", 1)[1]
    body = body[: body.index("\nasync function ", 10)]

    assert "window.portalPrefetchPendingInput(agentId, normalized)" in body
    assert body.index("portalPrefetchPendingInput(") < body.index("updateAgentSession(agentId, normalized)")
