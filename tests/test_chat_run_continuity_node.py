"""A reply in flight survives what the page does meanwhile.

Switching to another assistant and back, a hidden tab, a refresh, a second
window and a broken stream each used to lose the run's progress on screen, or
report a send as failed while the run went on. The native runtime numbers the
events of a run (run_seq), keeps them in a journal the event socket attaches
to (after_seq), and reports the run in flight with the session (live_run);
these tests drive the chat_ui.js helpers that use that, and the paths that
keep working without it (the opencode runtime).
"""

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


CHAT_UI = Path("app/static/js/chat_ui.js")
BRIDGE = Path("app/static/js/connectors/local_bridge.js")


def _src(path: Path = CHAT_UI) -> str:
    return path.read_text(encoding="utf-8")


def _functions(*names: str, path: Path = CHAT_UI) -> str:
    src = _src(path)
    return "\n".join(_extract_js_function(src, name) for name in names)


def _constants(*names: str) -> str:
    src = _src()
    lines = []
    for name in names:
        match = re.search(rf"^const {name} = .+;$", src, re.MULTILINE)
        assert match, f"missing const {name} in chat_ui.js"
        lines.append(match.group(0))
    return "\n".join(lines)


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping chat run continuity test")
    # Through a file: the bundles are past Windows' command-line limit.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "case.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


SEQ_HELPERS = (
    "runSeqCursorFor",
    "setRunSeqCursor",
    "isFollowingChatRun",
    "acceptRunSeq",
    "requestRunCatchUp",
    "noteRunJournalHole",
    "handleEventSocketControlMessage",
)

SEQ_HARNESS = """
const calls = [];
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
function ensureEventSocketForAgent(...args) { calls.push(["socket", ...args]); }
function renderAgentTimelineForCurrentRequest(agentId) { calls.push(["render", agentId]); }
let chatState = null;
function ensureChatState() { return chatState; }
function runAllTimers() { while (timers.length) timers.shift().fn(); }
"""


# ------------------------------------------------- one copy of every event


def test_the_second_copy_of_an_event_is_dropped_and_the_chat_stream_is_never_held_back():
    result = _run_node(
        _constants("RUN_SEQ_CURSOR_LIMIT", "RUN_CATCH_UP_GRACE_MS")
        + SEQ_HARNESS
        + _functions(*SEQ_HELPERS)
        + textwrap.dedent(
            """
            chatState = {
              sessionId: "s1",
              currentRequest: { clientRequestId: "r1", requestId: "r1", liveStream: true },
              inflightAgentTimeline: { requestId: "r1" },
            };
            const event = (seq, extra = {}) => ({ request_id: "r1", run_seq: seq, ...extra });
            const stream = { agentId: "a1", requestId: "r1" };
            const socket = { agentId: "a1", requestId: "r1", viaSocket: true, attachedAfterSeq: 0 };
            const seen = [
              acceptRunSeq("a1", chatState, event(1), stream),
              acceptRunSeq("a1", chatState, event(1), socket),
              acceptRunSeq("a1", chatState, event(2), socket),
              acceptRunSeq("a1", chatState, event(2), stream),
              // The stream skipping ahead is still the stream: taken.
              acceptRunSeq("a1", chatState, event(4), stream),
            ];
            // The socket's merged copy of 3..6 straddles what the stream drew:
            // dropped, and the stream gets its grace period to deliver 5 and 6.
            const straddle = acceptRunSeq("a1", chatState, event(6, { run_seq_from: 3 }), socket);
            const graceTimer = timers.map((timer) => timer.ms);
            // A merged copy that starts right after what was drawn is taken whole.
            const merged = acceptRunSeq("a1", chatState, event(7, { run_seq_from: 5 }), socket);
            // The stream did deliver: no second attach.
            runAllTimers();
            const socketCallsAfterGrace = calls.filter((call) => call[0] === "socket").length;
            // Other runs and unnumbered events (the opencode runtime) pass.
            const passing = [
              acceptRunSeq("a1", chatState, { request_id: "other-run", run_seq: 1 }, socket),
              acceptRunSeq("a1", chatState, { request_id: "r1", run_seq: 0 }, socket),
              acceptRunSeq("a1", chatState, { request_id: "", run_seq: 3 }, socket),
            ];
            console.log(JSON.stringify({
              seen, straddle, graceTimer, merged, socketCallsAfterGrace, passing,
              cursor: runSeqCursorFor(chatState, "r1"),
            }));
            """
        )
    )

    assert result["seen"] == [True, False, True, False, True]
    assert result["straddle"] is False
    assert result["graceTimer"] == [3000]
    assert result["merged"] is True
    assert result["socketCallsAfterGrace"] == 0
    assert result["passing"] == [True, True, True]
    assert result["cursor"] == 7


def test_a_socket_that_skips_ahead_is_attached_again_from_the_last_event_drawn():
    result = _run_node(
        _constants("RUN_SEQ_CURSOR_LIMIT", "RUN_CATCH_UP_GRACE_MS")
        + SEQ_HARNESS
        + _functions(*SEQ_HELPERS)
        + textwrap.dedent(
            """
            // Followed from the socket alone (a recovered run): no stream to wait for.
            chatState = {
              sessionId: "s1",
              currentRequest: { clientRequestId: "r1", requestId: "r1", recovered: true, liveStream: false },
              inflightAgentTimeline: { requestId: "r1" },
            };
            setRunSeqCursor(chatState, "r1", 3);
            const socket = { agentId: "a1", requestId: "r1", viaSocket: true, attachedAfterSeq: 0 };
            const skipped = acceptRunSeq("a1", chatState, { request_id: "r1", run_seq: 6 }, socket);
            const delays = timers.map((timer) => timer.ms);
            runAllTimers();
            const attach = calls.find((call) => call[0] === "socket");

            // The socket attached after 3 and its first event is 9: the runtime
            // no longer has 4..8. Taken, and the timeline says so.
            calls.length = 0;
            const confirmed = acceptRunSeq("a1", chatState, { request_id: "r1", run_seq: 9 }, { ...socket, attachedAfterSeq: 3 });
            console.log(JSON.stringify({
              skipped, delays, attach, confirmed,
              cursor: runSeqCursorFor(chatState, "r1"),
              missing: chatState.inflightAgentTimeline.earlierStepsMissing === true,
              rendered: calls.some((call) => call[0] === "render"),
            }));
            """
        )
    )

    assert result["skipped"] is False
    assert result["delays"] == [250]
    assert result["attach"] == ["socket", "a1", "s1", "r1", {"reattach": True}]
    assert result["confirmed"] is True
    assert result["cursor"] == 9
    assert result["missing"] is True
    assert result["rendered"] is True


def test_the_welcome_says_how_far_the_catch_up_goes_and_a_resync_marks_the_gap():
    result = _run_node(
        _constants("RUN_SEQ_CURSOR_LIMIT", "RUN_CATCH_UP_GRACE_MS")
        + SEQ_HARNESS
        + _functions(*SEQ_HELPERS)
        + textwrap.dedent(
            """
            chatState = {
              sessionId: "s1",
              currentRequest: { clientRequestId: "r1", requestId: "r1" },
              inflightAgentTimeline: { requestId: "r1" },
            };
            const ctx = { agentId: "a1", requestId: "r1", viaSocket: true, attachedAfterSeq: 0 };
            const welcome = handleEventSocketControlMessage("a1", JSON.stringify({
              type: "connected",
              filters: { request_id: "r1" },
              attach: { request_id: "r1", found: true, state: "running", after_seq: 0, first_seq: 1, last_seq: 12 },
            }), ctx);
            const missingAfterWelcome = chatState.inflightAgentTimeline.earlierStepsMissing === true;
            const resync = handleEventSocketControlMessage("a1", JSON.stringify({
              type: "resync",
              data: { request_id: "r1", reason: "journal_truncated", first_seq: 40 },
            }), ctx);
            const event = handleEventSocketControlMessage("a1", JSON.stringify({
              type: "session.next.text.delta",
              data: { delta: "the bridge connected" },
            }), ctx);
            // A runtime without a journal sends no attach block.
            const plainCtx = { agentId: "a1", requestId: "r1", viaSocket: true, attachedAfterSeq: 0 };
            handleEventSocketControlMessage("a1", JSON.stringify({ type: "connected", filters: {} }), plainCtx);
            console.log(JSON.stringify({
              welcome, resync, event,
              catchUpThroughSeq: ctx.catchUpThroughSeq,
              attachMode: ctx.attachMode === true,
              missingAfterWelcome,
              missingAfterResync: chatState.inflightAgentTimeline.earlierStepsMissing === true,
              cursor: runSeqCursorFor(chatState, "r1"),
              plainCatchUp: plainCtx.catchUpThroughSeq || 0,
            }));
            """
        )
    )

    # The welcome goes on to the event handler as before; resync stops here.
    assert result["welcome"] is False
    assert result["resync"] is True
    assert result["event"] is False
    assert result["catchUpThroughSeq"] == 12
    assert result["attachMode"] is True
    assert result["missingAfterWelcome"] is False
    assert result["missingAfterResync"] is True
    assert result["cursor"] == 39
    assert result["plainCatchUp"] == 0


def test_events_carry_their_run_number_through_normalization():
    src = _src()
    start = src.index("// RUNTIME_EVENT_HELPER_START: normalizeRuntimeEvent")
    end = src.index("// RUNTIME_EVENT_HELPER_END: normalizeRuntimeEvent")
    result = _run_node(
        src[start:end]
        + textwrap.dedent(
            """
            function isCompletionRuntimeState() { return false; }
            // As the socket sends it: the projected event under `data`.
            const fromSocket = normalizeRuntimeEvent({
              type: "session.next.text.delta",
              data: { id: "efp_1@3-6", type: "session.next.text.delta", request_id: "r1", run_seq: 6, run_seq_from: 3, delta: "abc" },
            });
            // As the chat stream wrapper hands it on.
            const fromStream = normalizeRuntimeEvent({
              type: "session.next.tool.called",
              event_type: "session.next.tool.called",
              request_id: "r1",
              data: { id: "efp_2", request_id: "r1", run_seq: 7 },
            });
            const unnumbered = normalizeRuntimeEvent({ type: "session.next.text.delta", data: { delta: "x" } });
            console.log(JSON.stringify({
              socket: [fromSocket.run_seq, fromSocket.run_seq_from],
              stream: [fromStream.run_seq, fromStream.run_seq_from],
              unnumbered: [unnumbered.run_seq, unnumbered.run_seq_from],
            }));
            """
        )
    )

    assert result == {"socket": [6, 3], "stream": [7, 0], "unnumbered": [0, 0]}


# ---------------------------------------------------------- the run's socket


SOCKET_HARNESS = """
const CLOSED = 3;
const CONNECTING = 0;
const OPEN = 1;
const window = { location: { protocol: "https:", host: "portal.test" } };
const state = { selectedAgentId: "agent-A", eventWs: null };
const handled = [];
function handleAgentEventMessage(raw, ctx) { handled.push({ raw, ctx }); }
function updateEventSocketStatus() {}
function hasLiveEventSocketWork() { return false; }
function scheduleEventSocketReconnect() {}
function syncSelectedAgentChatActionControls() {}
function setChatStatus() {}
function disconnectEventSocket() {
  if (state.eventWs) state.eventWs.close();
  state.eventWs = null;
  state.eventWsAgentId = null;
  state.eventWsSessionId = null;
  state.eventWsRequestId = null;
}
const opened = [];
class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = CONNECTING; opened.push(url); }
  close() { this.readyState = CLOSED; }
}
FakeWebSocket.CONNECTING = CONNECTING;
FakeWebSocket.OPEN = OPEN;
globalThis.WebSocket = FakeWebSocket;
let chatState = null;
function ensureChatState() { return chatState; }
"""


def test_a_run_socket_asks_for_what_follows_the_last_event_drawn():
    result = _run_node(
        _constants("RUN_SEQ_CURSOR_LIMIT", "RUN_CATCH_UP_GRACE_MS")
        + SOCKET_HARNESS
        + _functions(
            "runSeqCursorFor",
            "setRunSeqCursor",
            "isFollowingChatRun",
            "noteRunJournalHole",
            "handleEventSocketControlMessage",
            "ensureEventSocketForAgent",
        )
        + textwrap.dedent(
            """
            chatState = {
              sessionId: "s-1",
              currentRequest: { clientRequestId: "req-1" },
              inflightEventStream: { requestId: "req-1", lastEventCreatedAt: "2026-11-10T08:59:20Z" },
              lastEventStreamSnapshot: { lastEventCreatedAt: "2026-11-10T08:00:00Z" },
            };
            setRunSeqCursor(chatState, "req-1", 41);
            ensureEventSocketForAgent("agent-A", "s-1", "req-1");
            const runUrl = state.eventWs.url;
            // The same run again: kept, unless asked to attach again.
            ensureEventSocketForAgent("agent-A", "s-1", "req-1");
            const keptCount = opened.length;
            setRunSeqCursor(chatState, "req-1", 57);
            ensureEventSocketForAgent("agent-A", "s-1", "req-1", { reattach: true });
            const reattachUrl = state.eventWs.url;
            // The welcome and the run's events share one context per socket.
            state.eventWs.onmessage({ data: JSON.stringify({ type: "connected", attach: { request_id: "req-1", found: true, first_seq: 1, last_seq: 60 } }) });
            state.eventWs.onmessage({ data: JSON.stringify({ type: "x", data: { run_seq: 58 } }) });
            const sharedCtx = handled.length === 2 && handled[0].ctx === handled[1].ctx ? handled[1].ctx : null;
            // A run whose own stream this is not: no time cursor from another run.
            ensureEventSocketForAgent("agent-A", "s-1", "req-2");
            const otherRunUrl = state.eventWs.url;
            // The session's socket is what it always was.
            ensureEventSocketForAgent("agent-A", "s-1", null);
            const sessionUrl = state.eventWs.url;
            console.log(JSON.stringify({
              runUrl, keptCount, reattachUrl, otherRunUrl, sessionUrl,
              opened: opened.length,
              catchUpThroughSeq: sharedCtx ? sharedCtx.catchUpThroughSeq : null,
              attachedAfterSeq: sharedCtx ? sharedCtx.attachedAfterSeq : null,
              viaSocket: sharedCtx ? sharedCtx.viaSocket : null,
            }));
            """
        )
    )

    base = "wss://portal.test/a/agent-A/api/events?session_id=s-1"
    assert result["runUrl"] == (
        f"{base}&request_id=req-1&replay=1&after_seq=41&replay_limit=500&last_event_at=2026-11-10T08%3A59%3A20Z"
    )
    assert result["keptCount"] == 1
    assert result["reattachUrl"] == (
        f"{base}&request_id=req-1&replay=1&after_seq=57&replay_limit=500&last_event_at=2026-11-10T08%3A59%3A20Z"
    )
    assert result["catchUpThroughSeq"] == 60
    assert result["attachedAfterSeq"] == 57
    assert result["viaSocket"] is True
    assert result["otherRunUrl"] == f"{base}&request_id=req-2&replay=1&after_seq=0&replay_limit=500"
    assert result["sessionUrl"] == f"{base}&replay=1&last_event_at=2026-11-10T08%3A59%3A20Z"
    assert result["opened"] == 4


def test_selecting_the_assistant_already_selected_keeps_its_socket():
    src = _src()
    selection = _extract_js_function(src, "performAgentSelection")
    assert "if (previousAgentId !== agentId) disconnectEventSocket();" in selection

    load = _extract_js_function(src, "loadSessionForAgent")
    guard = load[: load.index("const token = render ? beginTranscript(")]
    assert "ensureEventSocketForSelectedAgent();" in guard, "a load skipped for the conversation on screen still opens its socket"


# --------------------------------------------- the history under a live run


SPLIT_HELPERS = (
    "getHistoryUserVisibleContent",
    "runtimeTimestampMs",
    "storedUserMessageIs",
    "splitHistoryForFollowedRun",
)


def test_the_history_leaves_out_what_the_live_row_draws():
    result = _run_node(
        _functions(*SPLIT_HELPERS)
        + textwrap.dedent(
            """
            let chatState = null;
            function ensureChatState() { return chatState; }
            const history = [
              { id: "u1", role: "user", content: "earlier question", created_at: "2026-11-10T08:50:00.000001Z" },
              { id: "a1", role: "assistant", content: "earlier answer", created_at: "2026-11-10T08:50:09.5Z" },
              { id: "u2", role: "user", content: "run the three tools", created_at: "2026-11-10T08:59:15.111394Z" },
              { id: "a2", role: "assistant", content: "", created_at: "2026-11-10T08:59:20.222940Z" },
              { id: "t2", role: "tool", content: "tool output", created_at: "2026-11-10T08:59:20.266035Z" },
            ];
            const ids = (split) => split.messages.map((message) => message.id);

            // With live_run (the native runtime): by the run's start time.
            chatState = {
              sessionId: "s1",
              currentRequest: {
                clientRequestId: "r1", sessionIdAtSend: "s1", recovered: true,
                liveRun: { request_id: "r1", started_at: "2026-11-10T08:59:13.369607Z" },
              },
            };
            const byTime = splitHistoryForFollowedRun("a", "s1", history);

            // Without it, for a run this page sent: what follows its own message.
            chatState = { sessionId: "s1", currentRequest: { clientRequestId: "r1", sessionIdAtSend: "s1", message: "run  the three tools " } };
            const own = splitHistoryForFollowedRun("a", "s1", history);
            // ...unless the runtime has not stored that message yet.
            chatState.currentRequest.message = "a newer question";
            const ownUnstored = splitHistoryForFollowedRun("a", "s1", history);

            // A run recovered without a start time: the stored rows stay.
            chatState = { sessionId: "s1", currentRequest: { clientRequestId: "r1", sessionIdAtSend: "s1", recovered: true } };
            const recovered = splitHistoryForFollowedRun("a", "s1", history);

            // Nothing followed, another session, an edit's regeneration: untouched.
            chatState = { sessionId: "s1", currentRequest: null };
            const idle = splitHistoryForFollowedRun("a", "s1", history);
            chatState = { sessionId: "s1", currentRequest: { clientRequestId: "r1", sessionIdAtSend: "s2", message: "run the three tools" } };
            const otherSession = splitHistoryForFollowedRun("a", "s1", history);
            chatState = { sessionId: "s1", currentRequest: { clientRequestId: "r1", sessionIdAtSend: "s1", edit: true, message: "run the three tools" } };
            const edit = splitHistoryForFollowedRun("a", "s1", history);

            console.log(JSON.stringify({
              byTime: [ids(byTime), byTime.userMessage && byTime.userMessage.id, byTime.followed],
              own: [ids(own), own.userMessage && own.userMessage.id],
              ownUnstored: [ids(ownUnstored), ownUnstored.userMessage, ownUnstored.followed],
              recovered: [ids(recovered), recovered.userMessage, recovered.followed],
              idle: [ids(idle), idle.followed],
              otherSession: otherSession.followed,
              edit: edit.followed,
              timestamps: [
                runtimeTimestampMs("2026-11-10T08:59:13.369607Z"),
                runtimeTimestampMs("2026-11-10T08:59:13.369607"),
                runtimeTimestampMs("2026-11-10 08:59:13.369607+00:00"),
                runtimeTimestampMs(1794301153.369607),
                Number.isNaN(runtimeTimestampMs("")),
              ],
            }));
            """
        )
    )

    assert result["byTime"] == [["u1", "a1", "u2"], "u2", True]
    assert result["own"] == [["u1", "a1", "u2"], "u2"]
    assert result["ownUnstored"] == [["u1", "a1", "u2", "a2", "t2"], None, True]
    assert result["recovered"] == [["u1", "a1", "u2", "a2", "t2"], None, True]
    assert result["idle"] == [["u1", "a1", "u2", "a2", "t2"], False]
    assert result["otherSession"] is False
    assert result["edit"] is False
    stamp = 1794301153369
    assert result["timestamps"][:3] == [stamp, stamp, stamp]
    assert abs(result["timestamps"][3] - stamp) < 1
    assert result["timestamps"][4] is True


RESTORE_HARNESS = """
const inserted = [];
const drawn = [];
const historyUserArticles = [];
const dom = {
  messageList: {
    querySelectorAll(selector) {
      return selector === 'article[data-local-user="1"]' ? historyUserArticles : [];
    },
    querySelector(selector) {
      if (selector === 'article[data-pending-assistant="1"]') return inserted.some((html) => html.startsWith("<pending")) ? {} : null;
      return null;
    },
    insertAdjacentHTML(position, html) { inserted.push(html); },
    lastElementChild: {},
  },
};
let chatState = null;
function ensureChatState() { return chatState; }
function transcriptAcceptsLiveWrite() { return true; }
function removeWelcomeMessageIfPresent() {}
function renderIcons() {}
function scrollToBottom() {}
function buildUserMessageArticle(text, attachments, options) { return `<user ${options.clientRequestId}>${text}`; }
function buildPendingAssistantArticle(requestId, pendingText) { return `<pending ${requestId}>${pendingText}`; }
function advanceAgentTimelineReveal(timeline, count) { timeline.visibleItemCount = count; }
function renderAgentTimelineForCurrentRequestNow(agentId, state, options) { drawn.push(["timeline", options.requestCtx.clientRequestId]); }
function updatePendingAssistantStreamContent(agentId, text, options) { drawn.push(["text", text, options.streaming]); }
"""


def test_coming_back_puts_the_live_row_under_its_message_drawn_from_memory():
    result = _run_node(
        RESTORE_HARNESS
        + _functions("agentTimelineMatchesRequest", "restoreFollowedChatRunView")
        + textwrap.dedent(
            """
            // This page sent the run; history already has its message (u2).
            historyUserArticles.push({ dataset: { localUser: "1", messageId: "u1" } }, { dataset: { localUser: "1", messageId: "u2" } });
            const typewriter = { targetText: "Part one, part two", visibleText: "Part one", cancelled: false };
            chatState = {
              currentRequest: { clientRequestId: "r1", requestId: "r1", sessionIdAtSend: "s1", typewriter },
              inflightAgentTimeline: { requestId: "r1", assistantText: "Part one, part two", visibleItemCount: 1 },
            };
            const restored = restoreFollowedChatRunView("a", { followed: true, userMessage: { id: "u2" } });
            const own = {
              restored,
              inserted: inserted.slice(),
              marked: historyUserArticles[1].dataset,
              unmarked: historyUserArticles[0].dataset,
              revealed: chatState.inflightAgentTimeline.visibleItemCount,
              drawn: drawn.slice(),
              typed: typewriter.visibleText,
            };

            // Not stored yet: the message is drawn again from what was sent.
            inserted.length = 0; drawn.length = 0; historyUserArticles.length = 0;
            chatState = {
              currentRequest: { clientRequestId: "r2", sessionIdAtSend: "s1", displayMessage: "hello there", typewriter: { targetText: "", visibleText: "", cancelled: false } },
              inflightAgentTimeline: { requestId: "r2", assistantText: "", visibleItemCount: 0 },
            };
            restoreFollowedChatRunView("a", { followed: true, userMessage: null });
            const unstored = inserted.slice();

            // A run joined from elsewhere: only its row, saying it reconnects.
            inserted.length = 0;
            chatState = {
              currentRequest: { clientRequestId: "r3", sessionIdAtSend: "s1", recovered: true, typewriter: { targetText: "", visibleText: "", cancelled: false } },
              inflightAgentTimeline: { requestId: "r3", assistantText: "", visibleItemCount: 0 },
            };
            restoreFollowedChatRunView("a", { followed: true, userMessage: null });
            const joined = inserted.slice();
            const notFollowed = restoreFollowedChatRunView("a", { followed: false });
            console.log(JSON.stringify({ own, unstored, joined, notFollowed }));
            """
        )
    )

    own = result["own"]
    assert own["restored"] is True
    assert own["inserted"] == ["<pending r1>Thinking"]
    assert own["marked"] == {"localUser": "1", "messageId": "u2", "optimisticUser": "1", "clientRequestId": "r1"}
    assert own["unmarked"] == {"localUser": "1", "messageId": "u1"}
    assert own["revealed"] is None  # Infinity does not survive JSON: every step shown
    assert own["drawn"] == [["timeline", "r1"]]
    assert own["typed"] == "Part one, part two", "drawn whole, not typed out again"
    assert result["unstored"] == ["<user r2>hello there", "<pending r2>Thinking"]
    assert result["joined"] == ["<pending r3>Reconnecting"]
    assert result["notFollowed"] is False


def test_a_session_payload_names_the_run_in_flight():
    result = _run_node(
        _constants("RUNNING_CHAT_RUN_STATES")
        + _functions("normalizeChatRunState", "liveRunFromSessionPayload")
        + textwrap.dedent(
            """
            console.log(JSON.stringify({
              running: liveRunFromSessionPayload({ live_run: { request_id: " r1 ", state: "running", started_at: "2026-11-10T08:59:13.369607Z", last_seq: 38 } }),
              cancelling: liveRunFromSessionPayload({ live_run: { request_id: "r1", state: "cancelling" } }),
              finished: liveRunFromSessionPayload({ live_run: { request_id: "r1", state: "completed" } }),
              none: liveRunFromSessionPayload({ live_run: null }),
              missing: liveRunFromSessionPayload({}),
              noId: liveRunFromSessionPayload({ live_run: { state: "running" } }),
            }));
            """
        )
    )

    assert result["running"] == {
        "request_id": "r1",
        "state": "running",
        "started_at": "2026-11-10T08:59:13.369607Z",
        "last_seq": 38,
    }
    assert result["cancelling"]["state"] == "cancelling"
    assert result["finished"] is None
    assert result["none"] is None
    assert result["missing"] is None
    assert result["noId"] is None


def test_a_runtime_that_reports_live_run_is_believed_either_way():
    load = _extract_js_function(_src(), "loadSessionForAgent")
    adopt = load.index("adoptLiveChatRunForAgent(agentId, normalized, liveRun)")
    socket = load.index("updateAgentSession(agentId, normalized)")
    assert adopt < socket, "the run is followed before the socket opens, so the socket is the run's own"
    assert "else clearPersistedInflightChatRun(agentId);" in load, "nothing in flight: no stale record to chase"
    assert "if (recoverRunning && !reportsLiveRun) {" in load, "the status round trip is only for runtimes without live_run"
    paint = load.index("renderChatHistory(split.messages")
    restore = load.index("restoreFollowedChatRunView(agentId, split)")
    assert paint < load.index("markTranscriptReady(token)") < restore


# ------------------------------------------------------------ a hidden tab


def test_a_hidden_page_draws_the_run_whole_without_waiting_on_timers():
    result = _run_node(
        _functions(
            "chatPageIsHidden",
            "getAssistantTypewriterState",
            "queueAssistantTypewriter",
            "flushAssistantTypewriter",
            "isAgentTimelinePacedItem",
            "getAgentTimelineRenderableItems",
            "advanceAgentTimelineReveal",
            "clearAgentTimelineRevealTimer",
            "scheduleAgentTimelineReveal",
        )
        + textwrap.dedent(
            """
            globalThis.document = { hidden: true };
            const window = { matchMedia: () => ({ matches: false }) };
            const timers = [];
            globalThis.setTimeout = (fn, ms) => { timers.push(["timeout", ms]); return 1; };
            globalThis.setInterval = (fn, ms) => { timers.push(["interval", ms]); return 2; };
            globalThis.clearInterval = () => {};
            globalThis.clearTimeout = () => {};
            const drawn = [];
            function updatePendingAssistantStreamContent(agentId, text, options) { drawn.push([text, options.streaming]); }
            function renderAgentTimelineForCurrentRequest() {}

            (async () => {
              const requestCtx = {};
              queueAssistantTypewriter("a", requestCtx, "Hello, wor");
              queueAssistantTypewriter("a", requestCtx, "Hello, world.");
              await flushAssistantTypewriter("a", requestCtx, "Hello, world. Done.");
              const timeline = {
                requestId: "r1", completed: false, visibleItemCount: 0, revealTimer: null,
                items: [{ kind: "tool" }, { kind: "reasoning" }, { kind: "text" }, { kind: "tool" }],
              };
              const chatState = { inflightAgentTimeline: timeline };
              scheduleAgentTimelineReveal("a", chatState, {});
              console.log(JSON.stringify({ timers, drawn, visible: requestCtx.typewriter.visibleText, revealed: timeline.visibleItemCount }));
            })().catch((error) => { console.error(error); process.exit(1); });
            """
        )
    )

    assert result["timers"] == [], "a hidden page's timers come a second or a minute late"
    assert result["drawn"] == [
        ["Hello, wor", True],
        ["Hello, world.", True],
        ["Hello, world. Done.", True],
        ["Hello, world. Done.", False],
    ]
    assert result["visible"] == "Hello, world. Done."
    assert result["revealed"] == 3


def test_coming_back_to_the_page_resyncs_the_run():
    src = _src()
    resync = _extract_js_function(src, "resyncLiveChatRunViews")
    assert "wakeChatRunRetryWaiters();" in resync
    assert "advanceAgentTimelineReveal(timeline, Infinity);" in resync
    assert "renderAgentTimelineForCurrentRequestNow(" in resync
    assert "ensureEventSocketForSelectedAgent();" in resync
    assert 'if (!document.hidden) resyncLiveChatRunViews();' in src
    assert 'window.addEventListener("pageshow", resyncLiveChatRunViews);' in src
    assert 'window.addEventListener("online", resyncLiveChatRunViews);' in src


def test_only_a_followed_run_is_drawn():
    # A socket opened while a recovery was still asking about the run painted a
    # timeline into the recovery's placeholder that the recovery then emptied.
    handler = _extract_js_function(_src(), "handleAgentEventMessage")
    assert "if (chatState.currentRequest && typeof renderAgentTimelineForCurrentRequest === \"function\")" in handler
    assert "if (chatState.currentRequest && typeof scheduleAgentTimelineReveal === \"function\")" in handler
    # A run followed from its socket alone hears the runtime too: no "waiting
    # for runtime events" row while its events arrive.
    accepted = handler.index("acceptRunSeq(currentAgentId, chatState, entry, socketCtx)")
    assert accepted < handler.index("chatState.currentRequest.sawRuntimeEvent = true;")


# --------------------------------------------- the same message, not twice


def test_sending_a_message_again_that_is_still_being_answered_joins_it():
    result = _run_node(
        _functions("handleSessionBusyRefusal")
        + textwrap.dedent(
            """
            const state = { selectedAgentId: "a" };
            const calls = [];
            let chatState = null;
            function ensureChatState() { return chatState; }
            function clearWaitingForRuntimeEventsTimer() {}
            function stopRecoveredRunPolling() {}
            function clearPersistedInflightChatRun() {}
            function dropInflightAgentTimelineState(cs) { cs.inflightAgentTimeline = null; }
            function transcriptAcceptsLiveWrite() { return false; }
            function setChatSubmittingForAgent() {}
            function restoreQueuedIntoComposer(cs, queued) { calls.push(["composer", queued.text]); }
            function setChatStatus(text) { calls.push(["status", text]); }
            function showToast() {}
            function syncSelectedAgentChatActionControls() {}
            async function recoverInflightChatRunForAgent(agentId, sessionId, metadata) {
              calls.push(["join", metadata.request_id]);
              return true;
            }

            (async () => {
              // The earlier send of this message was reported failed while the
              // runtime could not be reached; it is the run still answering.
              chatState = { sessionId: "s1", unconfirmedSend: { requestId: "run-1", message: "deploy it" } };
              const ctx = { clientRequestId: "run-2", sessionIdAtSend: "s1", backupMessage: "deploy it" };
              await handleSessionBusyRefusal("a", ctx, { error: "session_busy", active_request_id: "run-1" });
              const again = { queued: chatState.queuedMessage, calls: calls.slice(), memory: chatState.unconfirmedSend };

              // A different message behind the same run waits its turn, as before.
              calls.length = 0;
              chatState = { sessionId: "s1", unconfirmedSend: { requestId: "run-1", message: "deploy it" } };
              await handleSessionBusyRefusal("a", { clientRequestId: "run-3", sessionIdAtSend: "s1", backupMessage: "and then test it" }, { active_request_id: "run-1" });
              const other = { queued: chatState.queuedMessage && chatState.queuedMessage.text, calls: calls.slice() };
              console.log(JSON.stringify({ again, other }));
            })().catch((error) => { console.error(error); process.exit(1); });
            """
        )
    )

    again = result["again"]
    assert again["queued"] is None
    assert again["memory"] is None
    assert ["join", "run-1"] in again["calls"]
    assert ["status", "Joined the reply in progress."] in again["calls"]
    assert not any(call[0] == "composer" for call in again["calls"])
    other = result["other"]
    assert other["queued"] == "and then test it"
    assert ["status", "Joined the reply in progress; your message is sent when it finishes."] in other["calls"]


def test_a_failure_the_runtime_could_not_confirm_is_remembered():
    failure = _extract_js_function(_src(), "handleAgentChatFailure")
    remember = failure.index("chatState.unconfirmedSend = {")
    assert failure.index("tryRecoverBrokenChatStream(") < remember
    assert "if (requestCtx.runOutcomeUnknown) {" in failure


# ------------------------------------------- the browser bridge, replayed


BRIDGE_HARNESS = """
const state = { handled: new Set() };
const modules = new Map();
const executed = [];
const fetched = [];
let pendingAnswer = { status: 200, body: { session_id: "s1", requests: [] } };
modules.set("local_bridge", { execute: async (request) => { executed.push(request.id); return { ok: true, result: {} }; } });
function clientId() { return "tab-1"; }
function currentAgentId() { return "agent-A"; }
globalThis.fetch = async (url, options = {}) => {
  fetched.push([options.method || "GET", url]);
  if (url.endsWith("/connectors/pending")) {
    if (pendingAnswer.throws) throw new Error("offline");
    return { ok: pendingAnswer.status === 200, status: pendingAnswer.status, text: async () => JSON.stringify(pendingAnswer.body) };
  }
  return { ok: true, status: 200, text: async () => "{}" };
};
function detail(id, replayed) {
  return {
    agentId: "agent-A",
    event: {
      type: "connector.request",
      replayed,
      data: { session_id: "s1", connector_type: "local_bridge", connector_request: { id, connector_type: "local_bridge", target_client_id: "tab-1" } },
    },
  };
}
"""


def test_a_replayed_browser_request_runs_only_while_the_runtime_still_waits_for_it():
    result = _run_node(
        BRIDGE_HARNESS
        + _functions(
            "requestJson",
            "respond",
            "eventData",
            "connectorRequestStillPending",
            "handleConnectorRequest",
            path=BRIDGE,
        )
        + textwrap.dedent(
            """
            (async () => {
              // Live: runs without asking.
              await handleConnectorRequest(detail("cr_live", false));
              const askedForLive = fetched.some((call) => call[1].endsWith("/connectors/pending"));
              // Replayed and already answered (another tab, or this one before a refresh): skipped.
              await handleConnectorRequest(detail("cr_done", true));
              // Replayed and still waiting: runs.
              pendingAnswer = { status: 200, body: { session_id: "s1", requests: [{ id: "cr_waiting" }] } };
              await handleConnectorRequest(detail("cr_waiting", true));
              // A runtime that cannot say (the opencode runtime has no such route): as before.
              pendingAnswer = { status: 404, body: { error: "not found" } };
              await handleConnectorRequest(detail("cr_unknown_404", true));
              pendingAnswer = { throws: true };
              await handleConnectorRequest(detail("cr_unknown_offline", true));
              // A second copy of one already handled: nothing, not even a question.
              const before = fetched.length;
              await handleConnectorRequest(detail("cr_waiting", true));
              console.log(JSON.stringify({
                executed, askedForLive,
                secondCopyFetched: fetched.length - before,
                pendingUrl: fetched.find((call) => call[1].endsWith("/connectors/pending"))[1],
              }));
            })().catch((error) => { console.error(error); process.exit(1); });
            """
        )
    )

    assert result["executed"] == ["cr_live", "cr_waiting", "cr_unknown_404", "cr_unknown_offline"]
    assert result["askedForLive"] is False
    assert result["secondCopyFetched"] == 0
    assert result["pendingUrl"] == "/a/agent-A/api/sessions/s1/connectors/pending"


def test_journal_catch_up_is_flagged_as_replayed_before_listeners_see_it():
    handler = _extract_js_function(_src(), "handleAgentEventMessage")
    flagged = handler.index("entry.replayed = true;")
    dispatched = handler.index('new CustomEvent("portal:runtime-event"')
    assert flagged < dispatched
    assert "entry.run_seq <= socketCtx.catchUpThroughSeq" in handler
