"""A finished answer shows the answer alone.

The Working list is a progress view for the run in flight. When the answer
lands it is removed, and nothing is folded under the answer: a reasoning model
that only thought, a run that called tools, and an answer loaded from history
all end the same way. The "N steps" disclosure that portal#512 kept under the
answer was removed at the member's request.
"""

from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


def test_completion_removes_the_working_list_outright():
    clear = _extract_js_function(JS, "clearAssistantTimelineFromArticle")
    assert "article.querySelector(\"[data-agent-timeline='1']\")?.remove();" in clear
    assert 'article.querySelector(".assistant-waiting-indicator")?.remove();' in clear
    assert "collapse" not in clear.lower(), "the timeline is removed, not folded"
    finalize = _extract_js_function(JS, "finalizeAgentTimelineState")
    assert "renderRunStepsForSnapshot" not in finalize
    assert "dropInflightAgentTimelineState(chatState)" in finalize


def test_no_steps_disclosure_is_left_in_the_code():
    for marker in (
        "chat-run-steps",
        "data-run-steps",
        "agent-timeline-steps",
        "runStepsDetailsHtml",
        "placeRunStepsDetails",
        "collapseAgentTimelineIntoSteps",
        "renderRunStepsForSnapshot",
        "isRunStepTimelineItem",
    ):
        assert marker not in JS, marker
        assert marker not in CSS, marker
    # The in-flight Working list itself is untouched.
    assert "data-agent-timeline-items" in JS
    assert ".agent-timeline-list" in CSS
