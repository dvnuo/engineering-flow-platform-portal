import subprocess
import textwrap
from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def test_sse_parser_handles_split_multiline_malformed_and_heartbeat():
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


def test_canonical_snapshot_conversion_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "getCanonicalMessagesFromSessionPayload"),
                _extract_js_function(src, "canonicalPartText"),
                _extract_js_function(src, "canonicalMessageVisibleText"),
                _extract_js_function(src, "canonicalMessageToLegacyDisplayMessage"),
                _extract_js_function(src, "canonicalMessagesToLegacyDisplayMessages"),
                _extract_js_function(src, "canonicalPartToThinkingItem"),
                _extract_js_function(src, "canonicalMessagesToThinkingItems"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const canonical = getCanonicalMessagesFromSessionPayload({
              canonical_messages: [
                {
                  message_id: "user-1",
                  role: "user",
                  info: { id: "user-1", role: "user" },
                  parts: [{ id: "u-part-1", type: "text", text: "Please inspect" }],
                },
                {
                  message_id: "assistant-1",
                  role: "assistant",
                  info: { id: "assistant-1", role: "assistant" },
                  parts: [
                    { id: "r-part-1", type: "reasoning", text: "hidden reasoning" },
                    { id: "t-part-1", type: "tool", tool: "bash", state: { status: "completed" }, output: "ok" },
                    { id: "a-part-1", type: "text", text: "Visible answer" },
                  ],
                },
              ],
            });
            const legacy = canonicalMessagesToLegacyDisplayMessages(canonical);
            const assistant = legacy.find((message) => message.role === "assistant");
            const thinking = canonicalMessagesToThinkingItems(canonical);

            assert.equal(canonical.length, 2);
            assert.equal(legacy.length, 2);
            assert.equal(legacy[0].content, "Please inspect");
            assert.equal(assistant.content, "Visible answer");
            assert.equal(assistant.content.includes("hidden reasoning"), false);
            assert.equal(thinking.some((item) => item.kind === "reasoning" && item.text === "hidden reasoning"), true);
            assert.equal(thinking.some((item) => item.kind === "tool" && item.tool === "bash"), true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_long_task_sse_recovery_markers_are_removed():
    src = SRC.read_text(encoding="utf-8")
    forbidden = [
        "chat_run" + "_already_active",
        "startChatRun" + "ReconcileLoop",
        "reconcileChatRun" + "Once",
        "stream" + "Detached",
        "/active" + "-run",
        "/api/chat/" + "runs",
        "Previous message" + " still running",
        "Still running. Reconnecting",
    ]

    for marker in forbidden:
        assert marker not in src


def test_non_success_hint_keeps_continue_for_generic_incomplete_only():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "nonSuccessHintForPayload")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const busy = nonSuccessHintForPayload({ status: "busy" });
            assert.equal(busy.includes('send "continue"'), false);
            assert.match(busy, /still working/);

            const stillActive = nonSuccessHintForPayload({ error: "opencode_abort_still_active" });
            assert.equal(stillActive.includes('send "continue"'), false);
            assert.match(stillActive, /Reset the session|start a new chat/);

            const incomplete = nonSuccessHintForPayload({
              completion_state: "incomplete",
              incomplete_reason: "idle incomplete",
            });
            assert.match(incomplete, /send "continue"/);
            """
        )
    )
    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
