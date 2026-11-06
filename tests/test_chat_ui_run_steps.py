"""A finished answer keeps the run's steps under it as a closed disclosure."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap

import pytest

from tests._js_extract_helpers import _extract_js_function


JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


def test_completion_folds_the_live_timeline_into_steps_instead_of_dropping_it():
    clear = _extract_js_function(JS, "clearAssistantTimelineFromArticle")
    assert "collapseAgentTimelineIntoSteps(article, timelineEl)" in clear
    assert "?.remove()" not in clear.split("collapseAgentTimelineIntoSteps")[0], "the timeline is folded, not removed"
    collapse = _extract_js_function(JS, "collapseAgentTimelineIntoSteps")
    assert 'row.classList.contains("is-text")' in collapse, "the streaming-text row is not a step"
    assert 'article.querySelector("[data-run-steps]")' in collapse, "a snapshot-built disclosure is not replaced by the live rows"
    finalize = _extract_js_function(JS, "finalizeAgentTimelineState")
    assert "renderRunStepsForSnapshot(chatState, requestCtx)" in finalize
    assert ".chat-run-steps .agent-timeline-list { max-height: 320px; }" in CSS


def test_steps_disclosure_lists_every_step_closed():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    helpers = "\n".join(_extract_js_function(JS, name) for name in ("isAgentTimelinePacedItem", "getAgentTimelineRenderableItems", "runStepsDetailsHtml"))
    script = "\n".join([
        helpers,
        textwrap.dedent(r"""
            const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
            const tn = (count, one, other) => (count === 1 ? one : other).replace("{n}", String(count));
            const rendered = [];
            const renderAgentTimelineRowsHtml = (timeline) => {
              rendered.push(timeline);
              return timeline.items.map((item) => `<div class="agent-timeline-row is-${item.kind}"></div>`).join("");
            };
            const assert = require("node:assert/strict");
            const timeline = {
              requestId: "req-1",
              visibleItemCount: 1,
              items: [
                { kind: "step", title: "Started" },
                { kind: "tool", title: "Read file", status: "completed" },
                { kind: "reasoning", title: "Reasoning summary", status: "completed" },
                { kind: "text", title: "Writing response", status: "completed" },
                { kind: "permission", title: "Permission", status: "completed" },
              ],
            };
            const html = runStepsDetailsHtml(timeline);
            assert.match(html, /^<details class="portal-collapsible chat-run-steps" data-run-steps="3">/);
            assert.doesNotMatch(html, /<details[^>]*\sopen/);
            assert.match(html, /<summary class="portal-collapsible-summary"><span>3 steps<\/span><\/summary>/);
            assert.equal((html.match(/agent-timeline-row/g) || []).length, 3, "tool, reasoning and permission rows; no text or step rows");
            assert.equal(rendered[0].visibleItemCount, Number.MAX_SAFE_INTEGER, "pacing is over: every step shows");
            assert.equal(runStepsDetailsHtml({ items: [{ kind: "text" }] }), "");
            assert.equal(runStepsDetailsHtml(null), "");
            console.log("ok");
        """),
    ])
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "steps.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert result.stdout.strip() == "ok"
