"""The Working timeline is capped in height and scrolls on its own.

While the assistant thinks, the pending bubble's timeline gained a row per
tool, reasoning and text event (up to 80, each with details) and stretched the
bubble over several screens. The list now has a maximum height and its own
scrollbar, and chat_ui.js keeps it pinned to the newest row until the reader
scrolls up inside it.
"""

from pathlib import Path
import re

from tests._js_extract_helpers import _extract_js_function


CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, selector
    return match.group(1)


def test_timeline_list_has_a_height_cap_and_its_own_scrollbar():
    rule = _rule(".agent-timeline-list")
    assert re.search(r"max-height:\s*\d+px", rule), rule
    assert "overflow-y: auto" in rule
    # Rows keep their natural height inside the capped grid.
    assert "align-content: start" in rule


def test_render_pins_the_list_to_its_newest_row_unless_the_reader_scrolled_up():
    render = _extract_js_function(JS, "renderAgentTimelineForCurrentRequestNow")
    assert "keepAgentTimelineListPinned(listNode)" in render
    pin = _extract_js_function(JS, "keepAgentTimelineListPinned")
    assert 'listNode.addEventListener("scroll"' in pin
    assert "distanceFromBottom(listNode) <= AGENT_TIMELINE_PIN_THRESHOLD_PX" in pin
    assert 'if (listNode.dataset.pinned !== "0") listNode.scrollTop = listNode.scrollHeight;' in pin
    # The listener is wired once per list, not once per render.
    assert "if (!listNode.dataset.pinWired)" in pin
