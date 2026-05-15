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
