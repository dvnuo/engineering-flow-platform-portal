"""A finished answer keeps the run's steps under it as a closed disclosure.

A step is something the assistant did: a tool call (connector requests
included), a permission or a question. Reasoning summaries, context
compactions and provider retries are not steps, so an answer that only thought
ends with no disclosure, the same as it shows after a reload.
"""

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
    assert ".filter(isRunStepTimelineRow)" in collapse, "only step rows move into the disclosure"
    row_filter = _extract_js_function(JS, "isRunStepTimelineRow")
    assert 'row.classList.contains("is-tool")' in row_filter
    assert 'row.classList.contains("is-permission")' in row_filter
    for kind in ("is-text", "is-reasoning", "is-compaction", "is-provider", "is-step"):
        assert kind not in row_filter, f"{kind} rows are not steps"
    assert 'article.querySelector("[data-run-steps]")' in collapse, "a snapshot-built disclosure is not replaced by the live rows"
    finalize = _extract_js_function(JS, "finalizeAgentTimelineState")
    assert "renderRunStepsForSnapshot(chatState, requestCtx)" in finalize
    assert ".chat-run-steps .agent-timeline-list { max-height: 320px; }" in CSS


def test_steps_disclosure_lists_every_step_closed_and_skips_thinking():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    helpers = "\n".join(_extract_js_function(JS, name) for name in ("isAgentTimelinePacedItem", "getAgentTimelineRenderableItems", "isRunStepTimelineItem", "runStepsDetailsHtml"))
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
                { kind: "reasoning", title: "Reasoning summary", status: "completed" },
                { kind: "tool", title: "Read file", status: "completed" },
                { kind: "compaction", title: "Context compaction", status: "completed" },
                { kind: "provider", title: "Provider retry", status: "completed" },
                { kind: "reasoning", title: "Reasoning summary", status: "completed" },
                { kind: "text", title: "Writing response", status: "completed" },
                { kind: "permission", title: "Permission", status: "completed" },
              ],
            };
            const html = runStepsDetailsHtml(timeline);
            assert.match(html, /^<details class="portal-collapsible chat-run-steps" data-run-steps="2">/);
            assert.doesNotMatch(html, /<details[^>]*\sopen/);
            assert.match(html, /<summary class="portal-collapsible-summary"><span>2 steps<\/span><\/summary>/);
            assert.equal((html.match(/agent-timeline-row/g) || []).length, 2, "the tool and permission rows; no reasoning, compaction, provider, text or step rows");
            assert.match(html, /is-tool/);
            assert.match(html, /is-permission/);
            assert.equal(rendered[0].visibleItemCount, Number.MAX_SAFE_INTEGER, "pacing is over: every step shows");
            // An answer the model only thought about: three reasoning summaries
            // and the streamed text. Nothing to retrace, so no disclosure.
            const thinkingOnly = {
              requestId: "req-2",
              visibleItemCount: 4,
              items: [
                { kind: "step", title: "Started" },
                { kind: "reasoning", title: "Reasoning summary", status: "completed" },
                { kind: "reasoning", title: "Reasoning summary", status: "completed" },
                { kind: "reasoning", title: "Reasoning summary", status: "completed" },
                { kind: "text", title: "Writing response", status: "completed" },
                { kind: "step", title: "Completed" },
              ],
            };
            assert.equal(runStepsDetailsHtml(thinkingOnly), "", "thinking alone leaves no steps block");
            assert.equal(runStepsDetailsHtml({ items: [{ kind: "compaction" }, { kind: "provider" }] }), "", "housekeeping rows alone leave no steps block");
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


REDUCER_HELPERS = (
    "normalizeRuntimeEventTypeAlias",
    "isTrackableStreamEvent",
    "isCompletionRuntimeState",
    "normalizeRuntimeEvent",
    "runtimeEventSummaryHash",
    "runtimeEventUniqueId",
    "runtimeEventDedupKey",
    "createAgentTimelineState",
    "isAgentTimelinePacedItem",
    "getAgentTimelineRenderableItems",
    "getAgentTimelineVisibleItems",
    "advanceAgentTimelineReveal",
    "clearAgentTimelineRevealTimer",
    "ensureAgentTimelineState",
    "getAgentTimelineEventData",
    "getAgentTimelineField",
    "normalizeAgentTimelineJsonText",
    "truncateAgentTimelineText",
    "getAgentTimelineCallId",
    "getAgentTimelinePartId",
    "getAgentTimelineEventKey",
    "findAgentTimelineItem",
    "upsertAgentTimelineItem",
    "appendAgentTimelineText",
    "agentTimelineToolName",
    "agentTimelineInputText",
    "agentTimelineOutputText",
    "agentTimelineReasoningText",
    "reduceAgentTimelineStepEvent",
    "reduceAgentTimelineTextEvent",
    "reduceAgentTimelineReasoningEvent",
    "reduceAgentTimelineToolEvent",
    "reduceAgentTimelineCompactionEvent",
    "reduceAgentTimelinePermissionEvent",
    "reduceAgentTimelineGenericEvent",
    "reduceAgentTimelineEvent",
    "isRunStepTimelineItem",
    "runStepsDetailsHtml",
)


def test_a_run_that_only_thought_ends_with_no_steps_block():
    """The reported case: reasoning events stream in (a runtime build that still
    sends them, in either runtime's shape), the answer lands, and the bubble must
    not gain an "N steps" disclosure made of the reasoning rows."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    helpers = "\n".join(_extract_js_function(JS, name) for name in REDUCER_HELPERS)
    script = "\n".join([
        'const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);',
        helpers,
        textwrap.dedent(r"""
            const escapeHtml = (value) => String(value ?? "");
            const renderAgentTimelineRowsHtml = (timeline) => getAgentTimelineVisibleItems(timeline)
              .map((item) => `<div class="agent-timeline-row is-${item.kind}"></div>`).join("");
            const assert = require("node:assert/strict");
            const ids = { request_id: "req-1", session_id: "sess-1" };
            const ev = (id, type, properties) => normalizeRuntimeEvent({ id, type, properties, ...ids });
            const run = (events) => {
              const chatState = { sessionId: "sess-1", currentRequest: { requestId: "req-1", clientRequestId: "req-1", sessionIdAtSend: "sess-1" } };
              events.forEach((event) => reduceAgentTimelineEvent(chatState, event));
              return chatState.inflightAgentTimeline;
            };
            const kinds = (timeline) => timeline.items.map((item) => item.kind);

            // OpenCode shape: one reasoning part per summary, three of them.
            const opencodeThinking = run([
              ev("e1", "session.next.step.started", { model: "gpt-6.1-sol" }),
              ev("e2", "session.next.reasoning.started", { partID: "r1" }),
              ev("e3", "session.next.reasoning.delta", { partID: "r1", delta: "Weighing the options" }),
              ev("e4", "session.next.reasoning.ended", { partID: "r1" }),
              ev("e5", "session.next.reasoning.started", { partID: "r2" }),
              ev("e6", "session.next.reasoning.ended", { partID: "r2", summary: "Checking the edge cases" }),
              ev("e7", "session.next.reasoning.started", { partID: "r3" }),
              ev("e8", "session.next.reasoning.ended", { partID: "r3", summary: "Settling on an answer" }),
              ev("e9", "session.next.text.started", { partID: "t1" }),
              ev("e10", "session.next.text.delta", { partID: "t1", delta: "Here is the answer." }),
              ev("e11", "session.next.text.ended", { partID: "t1" }),
              ev("e12", "session.next.step.ended", { message: "done" }),
            ]);
            assert.deepEqual(kinds(opencodeThinking), ["step", "reasoning", "reasoning", "reasoning", "text"], "the live Working list still shows the thinking");
            assert.equal(runStepsDetailsHtml(opencodeThinking), "", "no steps block after a thinking-only answer");

            // Native legacy shape: llm_thinking events without a part id, each its own row.
            const nativeThinking = run([
              ev("n1", "execution.started", { model: "gpt-6.1-sol" }),
              ev("n2", "llm_thinking", { thinking: "Reading the question" }),
              ev("n3", "llm_thinking", { thinking: "Comparing approaches" }),
              ev("n4", "llm_thinking", { thinking: "Writing it up" }),
              ev("n5", "message.delta", { delta: "Here is the answer." }),
              ev("n6", "complete", { message: "done" }),
            ]);
            assert.equal(kinds(nativeThinking).filter((kind) => kind === "reasoning").length, 3);
            assert.equal(runStepsDetailsHtml(nativeThinking), "", "no steps block after a thinking-only answer (native)");

            // The same run with a tool call and a permission keeps those two as its steps.
            const withTools = run([
              ev("e1", "session.next.step.started", { model: "gpt-6.1-sol" }),
              ev("e2", "session.next.reasoning.started", { partID: "r1" }),
              ev("e4", "session.next.reasoning.ended", { partID: "r1" }),
              ev("w1", "session.next.tool.called", { callID: "c1", tool: "read", input: { path: "README.md" } }),
              ev("w2", "session.next.tool.success", { callID: "c1", tool: "read", output: "# Title" }),
              ev("w3", "permission.requested", { permission_id: "p1", tool: "bash", message: "Allow bash?" }),
              ev("w4", "permission.resolved", { permission_id: "p1", tool: "bash", status: "allowed" }),
              ev("e9", "session.next.text.started", { partID: "t1" }),
              ev("e11", "session.next.text.ended", { partID: "t1" }),
              ev("e12", "session.next.step.ended", { message: "done" }),
            ]);
            const html = runStepsDetailsHtml(withTools);
            assert.match(html, /data-run-steps="2"/);
            assert.equal((html.match(/is-tool/g) || []).length, 1);
            assert.equal((html.match(/is-permission/g) || []).length, 1);
            assert.doesNotMatch(html, /is-reasoning/);
            console.log("ok");
        """),
    ])
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "steps_reducer.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert result.stdout.strip() == "ok"
