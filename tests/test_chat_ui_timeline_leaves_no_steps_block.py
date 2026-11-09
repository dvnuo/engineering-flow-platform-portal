"""A finished answer shows the answer alone.

The Working list is a progress view for the run in flight. When the answer
lands it is removed, and nothing is folded under the answer or kept on the
chat state, so the live answer matches what renderChatHistory draws after a
reload. The "N steps" disclosure that portal#512 kept under the answer was
removed in portal#520.
"""

import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


def _absent(marker: str) -> None:
    # count() rather than `not in`: a failing `not in` against the 960 KB
    # script makes pytest print a diff of the whole file in CI.
    assert JS.count(marker) == 0, f"{marker} is back in chat_ui.js"
    assert CSS.count(marker) == 0, f"{marker} is back in app.css"


def test_no_steps_disclosure_is_left_in_the_code():
    for marker in (
        "chat-run-steps",
        "data-run-steps",
        "agent-timeline-steps",
        "runStepsDetailsHtml",
        "placeRunStepsDetails",
        "collapseAgentTimelineIntoSteps",
        "renderRunStepsForSnapshot",
        "lastAgentTimelineSnapshot",
    ):
        _absent(marker)
    # The in-flight Working list itself is untouched.
    assert JS.count("data-agent-timeline-items") > 0
    assert CSS.count(".agent-timeline-list") > 0


def test_every_completion_path_clears_the_working_list():
    # The three functions that turn a pending bubble into a finished one: the
    # completed branch of the live row update, the final-payload finaliser and
    # the incomplete/failed finaliser.
    for name in ("updateOrCreateAssistantRowForRequest", "finalizePendingAssistantRow", "finalizeIncompleteAssistantRow"):
        assert "clearAssistantTimelineFromArticle(article);" in _extract_js_function(JS, name), name
    # The selector the removal uses is the marker the markup sets, in both
    # places that create the list.
    build = _extract_js_function(JS, "buildPendingAssistantArticle")
    assert 'data-agent-timeline="1"' in build
    render = _extract_js_function(JS, "renderAgentTimelineForCurrentRequestNow")
    assert 'created.dataset.agentTimeline = "1"' in render
    clear = _extract_js_function(JS, "clearAssistantTimelineFromArticle")
    assert "[data-agent-timeline='1']" in clear


def test_completion_removes_the_working_list_and_keeps_nothing():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    helpers = "\n".join(_extract_js_function(JS, name) for name in (
        "clearAssistantTimelineFromArticle",
        "agentTimelineMatchesRequest",
        "clearAgentTimelineRevealTimer",
        "dropInflightAgentTimelineState",
        "finalizeAgentTimelineState",
    ))
    script = "\n".join([
        helpers,
        textwrap.dedent(r"""
            const assert = require("node:assert/strict");
            const removed = [];
            const node = (name) => ({ remove() { removed.push(name); } });
            const article = {
              querySelector(selector) {
                if (selector === "[data-agent-timeline='1']") return node("timeline");
                if (selector === ".assistant-waiting-indicator") return node("waiting");
                return null;
              },
              appendChild() { throw new Error("nothing may be added to a finished answer"); },
              insertAdjacentHTML() { throw new Error("nothing may be added to a finished answer"); },
            };
            clearAssistantTimelineFromArticle(article);
            assert.deepEqual(removed, ["timeline", "waiting"]);
            clearAssistantTimelineFromArticle(null);
            // A second completion event for an article already cleaned.
            removed.length = 0;
            clearAssistantTimelineFromArticle({ querySelector: () => null });
            assert.deepEqual(removed, []);

            // The in-flight state goes with its reveal timer; nothing is kept.
            let cleared = null;
            globalThis.clearTimeout = (id) => { cleared = id; };
            const chatState = { inflightAgentTimeline: { requestId: "req-1", revealTimer: 42, items: [{ kind: "tool" }] } };
            finalizeAgentTimelineState(chatState, { clientRequestId: "req-1" });
            assert.equal(chatState.inflightAgentTimeline, null);
            assert.equal(cleared, 42);
            assert.deepEqual(Object.keys(chatState), ["inflightAgentTimeline"], "no snapshot or other leftover on the chat state");
            // Another request's completion leaves this run's state alone.
            const other = { inflightAgentTimeline: { requestId: "req-2", revealTimer: null } };
            finalizeAgentTimelineState(other, { clientRequestId: "req-3", requestId: "req-3" });
            assert.ok(other.inflightAgentTimeline);
            console.log(JSON.stringify({ ok: true }));
        """),
    ])
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "timeline_cleared.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert json.loads(result.stdout.strip()) == {"ok": True}
