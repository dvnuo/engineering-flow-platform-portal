import subprocess
import textwrap
from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def test_sse_parser_handles_split_multiline_malformed_and_heartbeat():
    """Node harness for the pure SSE parser helpers; no jsdom dependency required."""
    src = SRC.read_text(encoding="utf-8")
    parser_js = "\n".join(
        [
            _extract_js_function(src, "parseSseEvent"),
            _extract_js_function(src, "parseSseEventsFromChunk"),
        ]
    )
    script = (
        parser_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const first = parseSseEventsFromChunk(
              "",
              "event: runtime_event\ndata: {\"type\":\"llm_thinking\","
            );
            assert.equal(first.events.length, 0);
            assert.match(first.buffer, /llm_thinking/);

            const second = parseSseEventsFromChunk(first.buffer, "\"delta\":\"x\"}\n\n");
            assert.equal(second.buffer, "");
            assert.equal(second.events.length, 1);
            assert.equal(second.events[0].eventName, "runtime_event");
            assert.equal(second.events[0].data.type, "llm_thinking");
            assert.equal(second.events[0].data.delta, "x");

            const multiline = parseSseEvent(
              "event: runtime_event\n"
                + "data: {\"type\":\"tool.started\",\n"
                + "data: \"tool\":\"bash\"}"
            );
            assert.equal(multiline.eventName, "runtime_event");
            assert.equal(multiline.data.type, "tool.started");
            assert.equal(multiline.data.tool, "bash");

            const malformed = parseSseEvent("event: runtime_event\ndata: {\"bad\"");
            assert.equal(malformed.eventName, "runtime_event");
            assert.equal(typeof malformed.data, "string");
            assert.match(malformed.data, /\{"bad"/);

            const heartbeat = parseSseEvent("event: heartbeat\ndata: {}\n\n");
            assert.equal(heartbeat.eventName, "heartbeat");
            assert.deepEqual(heartbeat.data, {});
            """
        )
    )

    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_merge_final_thinking_snapshot_node_smoke_for_non_success_payload():
    src = SRC.read_text(encoding="utf-8")
    snapshot_js = "\n".join(
        [
            _extract_js_function(src, "mergeThinkingEvents"),
            _extract_js_function(src, "normalizeThinkingEvents"),
            _extract_js_function(src, "normalizePayloadThinkingEvents"),
            _extract_js_function(src, "getCompletionState"),
            _extract_js_function(src, "mergeFinalThinkingSnapshot"),
        ]
    )
    runtime_helpers = "\n".join(
        [
            "const completionRuntimeState = { terminal: new Set([\"completed\",\"error\",\"failed\",\"blocked\",\"incomplete\",\"empty_final\"]) };",
            "function normalizeRuntimeEvent(event) { return event && typeof event === \"object\" ? event : null; }",
        ]
    )
    script = (
        runtime_helpers
        + "\n"
        + snapshot_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const chatState = {
              lastThinkingSnapshot: { events: [] },
              inflightThinking: { events: [] },
              lastCompletedRequestId: "",
            };

            const state = {
              selectedAgentId: "agent-1",
              toolPanelOpen: false,
              activeUtilityPanel: "",
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }

            function isThinkingPanelActiveForAgent() {
              return false;
            }

            function renderThinkingPanelFromClientState() {
              throw new Error("should not render in this test");
            }

            mergeFinalThinkingSnapshot(
              "agent-1",
              {
                requestId: "req-1",
                clientRequestId: "req-1",
                sessionIdAtSend: "sess-1",
              },
              {
                completion_state: "incomplete",
                incomplete_reason: "auto_continue_max_turns_reached",
                continuation_count: 3,
                request_id: "req-1",
                session_id: "sess-1",
                context_state: {
                  summary: "repo scan",
                  current_state: "incomplete",
                  next_step: "retry",
                },
                runtime_events: [
                  {
                    type: "chat.incomplete",
                    request_id: "req-1",
                    session_id: "sess-1",
                    data: {
                      message: "Incomplete after auto-continue",
                      request_id: "req-1",
                      session_id: "sess-1",
                    },
                  },
                ],
              }
            );

            assert.equal(chatState.lastThinkingSnapshot.requestId, "req-1");
            assert.equal(chatState.lastThinkingSnapshot.sessionId, "sess-1");
            assert.equal(chatState.lastThinkingSnapshot.completion_state, "incomplete");
            assert.equal(chatState.lastThinkingSnapshot.incomplete_reason, "auto_continue_max_turns_reached");
            assert.equal(chatState.lastThinkingSnapshot.continuation_count, 3);
            assert.equal(chatState.lastThinkingSnapshot.events.length, 1);
            assert.equal(chatState.lastThinkingSnapshot.events[0].type, "chat.incomplete");
            assert.equal(chatState.lastThinkingSnapshot.contextState.summary, "repo scan");
            """
        )
    )

    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_thinking_normalizer_regression_guards():
    src = SRC.read_text(encoding="utf-8")
    merge_final = _extract_js_function(src, "mergeFinalThinkingSnapshot")
    success = _extract_js_function(src, "handleAgentChatSuccess")

    assert "normalizeEvents(" not in merge_final
    assert "normalizePayloadThinkingEvents(finalPayload?.runtime_events || [])" in merge_final
    assert "mergeThinkingEvents(existing.events || [], finalPayloadEvents)" in merge_final
    assert "const normalizeEvents" not in success
    assert "function normalizePayloadThinkingEvents(events)" in src or "function normalizeThinkingEvents(events)" in src
