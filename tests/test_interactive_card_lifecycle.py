"""What may and may not take the answer card off the screen.

A run that stops to ask a question also *finishes*: the loop returns with the
question pending, the request completes, and `chat.completed` arrives moments
after the card the completion was caused by. Treating that as "the block is
gone" removed the only control that could unblock the run.

Rebuilding the transcript has the same effect for a different reason -- the card
is not a history message, so a wholesale re-render drops it.

Both are now settled by asking the runtime what is outstanding, since it is the
only party that actually knows. These drive the real module under node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


MODULE = Path("app/static/js/interactive_input.js")
CHAT_UI = Path("app/static/js/chat_ui.js")

# Timers are a queue the test drains on purpose, so the debounce is observable
# rather than something to sleep through.
SHIM = """
const timers = [];
const documentHandlers = {};
const listHandlers = {};
let fetchCount = 0;

const list = {
  innerHTML: "",
  children: [],
  addEventListener: (t, fn) => { (listHandlers[t] = listHandlers[t] || []).push(fn); },
  append: (row) => { list.children.push(row); },
  querySelector: () => null,
};
const scroll = { scrollTop: 0, scrollHeight: 100, getBoundingClientRect: () => ({ top: 0 }) };

function makeRow() {
  return {
    id: "", className: "", innerHTML: "",
    // Enough of a query to answer "which request is on screen", which is how
    // the module decides whether a re-show would be a pointless rebuild.
    querySelector(selector) {
      if (selector !== "[data-request-id]") return null;
      const found = /data-request-id="([^"]*)"/.exec(this.innerHTML);
      return found ? { dataset: { requestId: found[1] } } : null;
    },
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ top: 10 }),
    remove() { list.children = list.children.filter((c) => c !== this); },
  };
}

globalThis.document = {
  readyState: "complete",
  addEventListener: (t, fn) => { (documentHandlers[t] = documentHandlers[t] || []).push(fn); },
  createElement: () => makeRow(),
  getElementById: (id) => {
    if (id === "message-list") return list;
    if (id === "message-scroll") return scroll;
    if (id === "portal-interactive-input") return list.children.find((c) => c.id === "portal-interactive-input") || null;
    return null;
  },
};
globalThis.window = {
  currentPortalAgentId: () => "a1",
  currentPortalSessionId: () => "s1",
  showToast: () => {},
  setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].fn = null; },
};

let pending = { session_id: "s1", question_request: null, permission_request: null };
const setPending = (v) => { pending = Object.assign({ session_id: "s1", question_request: null, permission_request: null }, v); };
globalThis.fetch = (url) => {
  fetchCount += 1;
  return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(pending)) });
};

const dispatch = (type, detail) => (documentHandlers[type] || []).forEach((fn) => fn({ detail }));
const runtimeEvent = (type, data) => dispatch("portal:runtime-event", { event: { type, data } });
const drainTimers = () => { const due = timers.splice(0); due.forEach((t) => t.fn && t.fn()); };
const settle = () => new Promise((r) => setImmediate(r));
const shown = () => !!list.children.find((c) => c.id === "portal-interactive-input");
const QUESTION = { request_id: "q-1", questions: [{ question: "Which project?", options: [{ label: "EFP" }] }] };

// submitQuestion is private to the module; the submit listener is the door.
const answerTheCard = () => {
  const form = {
    dataset: { requestId: "q-1" },
    querySelector: (sel) => (sel === "[data-interactive-msg]"
      ? { textContent: "", classList: { remove() {}, add() {} } }
      : null),
    querySelectorAll: (sel) => (sel === "[data-question-index]"
      ? [{ querySelector: (q) => (q === "[data-question-custom-input]" ? { value: "yes", disabled: false } : null) }]
      : []),
  };
  (listHandlers.submit || []).forEach((fn) => fn({
    target: { closest: (sel) => (sel === '[data-interactive-kind="question"]' ? form : null) },
    preventDefault() {},
  }));
};
"""


def _run_node(body: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping card lifecycle tests")
    script = f"{SHIM}\n{MODULE.read_text(encoding='utf-8')}\n(async () => {{\n{body}\n}})().catch((e) => {{ console.error(e); process.exit(1); }});"
    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def test_a_completed_run_does_not_remove_a_question_it_is_still_waiting_on():
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const afterAsk = shown();
runtimeEvent("chat.completed", {});
const beforeRecheck = shown();
drainTimers(); await settle();
console.log(JSON.stringify({ afterAsk, beforeRecheck, afterRecheck: shown() }));
""")

    assert result == {"afterAsk": True, "beforeRecheck": True, "afterRecheck": True}


def test_a_genuinely_finished_run_still_clears_the_card():
    # The re-check is authoritative in both directions, or a dead card would sit
    # there offering to unblock a run that already ended.
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
setPending({});
runtimeEvent("chat.completed", {});
drainTimers(); await settle();
console.log(JSON.stringify({ shown: shown() }));
""")

    assert result["shown"] is False


def test_a_burst_of_end_events_asks_the_runtime_once():
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const before = fetchCount;
runtimeEvent("chat.completed", {});
runtimeEvent("chat.failed", {});
runtimeEvent("chat.completed", {});
drainTimers(); await settle();
console.log(JSON.stringify({ requests: fetchCount - before, shown: shown() }));
""")

    assert result["requests"] == 1
    assert result["shown"] is True


def test_the_check_is_deferred_rather_than_immediate():
    # The run writes its pending request to session metadata around the same
    # moment it reports finishing; asking at once can read the state from
    # before the question landed.
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const before = fetchCount;
runtimeEvent("chat.completed", {});
await settle();
console.log(JSON.stringify({ askedWithoutDrainingTimers: fetchCount - before }));
""")

    assert result["askedWithoutDrainingTimers"] == 0


def test_a_rebuild_still_lets_the_runtime_have_the_last_word():
    # The card goes back immediately so it does not blink, but the answer is
    # not assumed: if the runtime says nothing is pending, it goes.
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
list.children = [];   // renderChatHistory wipes the list
dispatch("portal:history-rendered", { agentId: "a1" });
const restoredAtOnce = shown();
setPending({});
list.children = [];
dispatch("portal:history-rendered", { agentId: "a1" });
drainTimers(); await settle();
console.log(JSON.stringify({ restoredAtOnce, afterRuntimeSaysNo: shown() }));
""")

    assert result["restoredAtOnce"] is True
    assert result["afterRuntimeSaysNo"] is False


def test_a_rebuild_puts_the_card_back_at_once_rather_than_after_the_round_trip():
    # Clearing and waiting for the check made the card blink out and back on
    # every render, and a reconnecting session renders several times in a row.
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
list.children = [];
dispatch("portal:history-rendered", {});
console.log(JSON.stringify({ shownBeforeAnyFetch: shown() }));
""")

    assert result["shownBeforeAnyFetch"] is True


def test_showing_the_same_request_again_does_not_rebuild_the_form():
    # The recovery check runs on a timer and on every rebuild. Re-mounting would
    # throw away a half-typed answer each time.
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const first = list.children.find((c) => c.id === "portal-interactive-input");
runtimeEvent("question.requested", { question_request: QUESTION });
drainTimers(); await settle();
const second = list.children.find((c) => c.id === "portal-interactive-input");
console.log(JSON.stringify({ sameElement: first === second, rows: list.children.length }));
""")

    assert result["sameElement"] is True
    assert result["rows"] == 1


def test_a_different_request_does_replace_the_card():
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const first = list.children.find((c) => c.id === "portal-interactive-input");
const OTHER = { request_id: "q-2", questions: [{ question: "Which environment?", options: [{ label: "prod" }] }] };
runtimeEvent("question.requested", { question_request: OTHER });
const second = list.children.find((c) => c.id === "portal-interactive-input");
console.log(JSON.stringify({ replaced: first !== second, rows: list.children.length }));
""")

    assert result["replaced"] is True
    assert result["rows"] == 1


def test_a_resolved_permission_is_cleared_without_a_round_trip():
    # A resolution names what it resolved, so it needs no confirmation and the
    # card should not linger for a quarter of a second after the decision.
    result = _run_node("""
const PERM = { request_id: "p-1", tool: "bash", args: "ls" };
setPending({ permission_request: PERM });
runtimeEvent("permission.requested", { permission_request: PERM });
const before = fetchCount;
runtimeEvent("permission.resolved", {});
console.log(JSON.stringify({ shown: shown(), requests: fetchCount - before }));
""")

    assert result == {"shown": False, "requests": 0}


def test_a_half_typed_answer_is_kept_across_a_rebuild():
    """Wiring only -- the behaviour needs a layout engine and `CSS.escape`.

    Verified in a browser harness: with two questions, a typed free-text answer
    and a selected option both survive five consecutive rebuilds. Reconnecting
    destroys the form, and it tends to happen exactly while somebody is typing
    the answer that would unblock the run.
    """
    js = MODULE.read_text(encoding="utf-8")

    assert "function snapshotAnswers()" in js
    assert "function restoreAnswers()" in js
    # Captured on every edit, restored right after the form is rebuilt.
    assert 'list.addEventListener("input"' in js
    assert "snapshotAnswers();" in js.split('addEventListener("change"', 1)[1]
    assert "restoreAnswers();" in js.split("function showQuestion(", 1)[1]
    # A card that is gone has no draft worth keeping.
    assert "state.draft = null;" in js.split("function clearCard()", 1)[1]


def test_only_the_newest_session_load_is_allowed_to_paint():
    """Wiring only -- this needs several concurrent fetches to exercise.

    A reconnect starts several loads within a second (the socket recovering,
    the route applying, the agent being selected) and each one used to paint
    whatever it had finished fetching. That is the transcript flashing between
    welcome, history, and back.
    """
    js = CHAT_UI.read_text(encoding="utf-8")
    body = js.split("async function loadSessionForAgent(", 1)[1]

    claim = body.index("beginTranscript(")
    first_await = body.index("await agentApiFor(")
    check = body.index("transcriptTokenIsCurrent(")
    render = body.index("renderChatHistory(")

    assert claim < first_await, "the transcript must be claimed before the request goes out"
    assert first_await < check < render, "and the claim checked after it returns, before painting"


# ------------------------------------- not every parked run is a lost one


def _candidate(metadata: dict, persisted=None):
    """`inflightChatRunCandidate`, over a fake localStorage record."""
    js = CHAT_UI.read_text(encoding="utf-8")
    parts = "\n".join(
        _extract_js_function(js, name)
        for name in (
            "normalizeChatRunState",
            "chatRunRequestIdFromMetadata",
            "metadataIndicatesRunningChatRun",
            "metadataSaysWaitingForUserInput",
            "inflightChatRunCandidate",
        )
    )
    script = f"""
const RUNNING_CHAT_RUN_STATES = new Set(["running", "accepted", "queued", "in_progress"]);
let stored = {json.dumps(persisted)};
let cleared = false;
function getPersistedInflightChatRun() {{ return stored; }}
function clearPersistedInflightChatRun() {{ cleared = true; stored = null; }}
{parts}
const candidate = inflightChatRunCandidate("a1", "s1", {json.dumps(metadata)});
console.log(JSON.stringify({{ candidate, cleared }}));
"""
    return _run_node(script)


RUNNING_METADATA = {"last_execution_id": "req-1", "latest_event_state": "running"}
STALE_RECORD = {"agent_id": "a1", "session_id": "s1", "request_id": "req-1"}


def test_a_run_parked_on_a_question_is_not_something_to_reconnect_to():
    # It has no stream left to follow and no work in flight. Treating it as
    # inflight drew "Reconnecting", polled, read back `blocked` -- which counts
    # as terminal -- and reloaded the session, re-arming the same recovery.
    result = _candidate({"pending_question_request": {"request_id": "q-1"}}, STALE_RECORD)

    assert result["candidate"] is None
    assert result["cleared"] is True, "the record was written on send and never cleared"


def test_a_run_parked_on_an_approval_is_treated_the_same():
    assert _candidate({"pending_permission_request": {"id": "p-1"}}, STALE_RECORD)["candidate"] is None


@pytest.mark.parametrize(
    "metadata",
    [
        {"last_runtime_status": "waiting_for_question"},
        {"session_status": {"last_runtime_status": "waiting_for_permission"}},
        {"session_status": {"state": "waiting_for_question"}},
    ],
    ids=["top-level", "nested-status", "nested-state"],
)
def test_the_waiting_status_is_recognised_wherever_it_is_reported(metadata):
    # The runtime writes `last_runtime_status` on the session; Portal surfaces
    # it in more than one shape depending on the caller.
    assert _candidate(dict(metadata, last_execution_id="req-1"), STALE_RECORD)["candidate"] is None


def test_a_genuinely_running_run_is_still_recovered():
    result = _candidate(RUNNING_METADATA, None)

    assert result["candidate"]["request_id"] == "req-1"
    assert result["cleared"] is False


def test_a_persisted_record_still_wins_for_a_run_that_is_not_parked():
    result = _candidate({}, STALE_RECORD)

    assert result["candidate"]["request_id"] == "req-1"


def test_the_rebuild_announces_itself():
    js = CHAT_UI.read_text(encoding="utf-8")
    tail = js.split("function renderChatHistory(", 1)[1].split("\n}\n", 1)[0]

    assert 'dom.messageList.innerHTML = ""' in tail, "this is the wipe the event exists for"
    assert "portal:history-rendered" in tail


# ------------------------------ a run that stopped to ask has not stopped short


def test_a_parked_run_is_recognised_from_the_status_the_runtime_reports():
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "isWaitingForUserInputPayload")
    script = f"""
{fn}
const cases = [
  {{ status: "waiting_for_question" }},
  {{ status: "waiting_for_permission" }},
  {{ status: "WAITING_FOR_QUESTION" }},
  {{ status: "completed" }},
  {{ status: "max_iterations" }},
  {{ response: "" }},
  {{}},
];
console.log(JSON.stringify(cases.map(isWaitingForUserInputPayload)));
"""

    assert _run_node(script) == [True, True, True, False, False, False, False]


def test_a_parked_run_is_not_finalised_as_incomplete():
    # It has no response text and no completion_state, so every "did this
    # finish" test said no and the turn was labelled incomplete -- with a
    # `runtime_incomplete` toast for ordinary behaviour.
    js = CHAT_UI.read_text(encoding="utf-8")
    handler = _extract_js_function(js, "handleIncompleteChatStream")

    assert '"blocked"' in handler, "a parked run is blocked, not incomplete"
    assert "if (reason !== WAITING_FOR_USER_INPUT_REASON)" in handler, (
        "the toast reports a problem; being asked a question is not one"
    )


def test_every_incomplete_branch_checks_for_a_parked_run_first():
    js = CHAT_UI.read_text(encoding="utf-8")
    calls = js.count("await handleIncompleteChatStream(")
    guarded = js.count("WAITING_FOR_USER_INPUT_REASON")

    # One definition, one toast guard, one completion-state branch, and one
    # choice at each call site that can see a final payload.
    assert calls >= 3
    assert guarded >= calls, "a call site that cannot tell parked from incomplete will mislabel it"


# ------------------------------------------- one selection, one welcome, once


def test_selecting_the_same_assistant_twice_at_once_only_runs_once():
    js = CHAT_UI.read_text(encoding="utf-8")
    wrapper = _extract_js_function(js, "selectAgentById")
    script = f"""
let runs = 0;
let resolveIt;
const gate = new Promise((r) => {{ resolveIt = r; }});
async function performAgentSelection(agentId) {{ runs += 1; await gate; return agentId; }}
let inFlightAgentSelection = null;
{wrapper}
const a = selectAgentById("agent-1");
const b = selectAgentById("agent-1");
const c = selectAgentById("agent-2");
resolveIt();
Promise.all([a, b, c]).then(([first, second, other]) =>
  console.log(JSON.stringify({{ runs, first, second, other }})));
"""
    result = _run_node(script)

    # Startup selects from the saved last agent and from the route being
    # applied; each pass clears the transcript to the welcome message. `async`
    # hands every caller its own promise, so the count is what tells them apart.
    assert result["runs"] == 2, "the repeat for the same assistant should join the first"
    assert result["first"] == result["second"] == "agent-1"
    assert result["other"] == "agent-2"


def test_the_work_still_happens_where_the_route_expects_it():
    js = CHAT_UI.read_text(encoding="utf-8")
    work = _extract_js_function(js, "performAgentSelection")

    assert "clearMessageListToWelcome();" in work
    assert 'commitPortalRoute({ section: "assistants", agentId })' in work


# ------------------------------------- the answer starts a run worth watching


def test_answering_follows_the_run_the_runtime_just_started():
    """Both respond endpoints reply 202 with the resumed run's request id.

    Dropping it left the card gone, a toast saying "Continuing...", and a
    conversation that did not move again until the page was reloaded -- by
    which point the work had already happened.
    """
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const adopted = [];
globalThis.window.adoptPortalResumedChatRun = (agent, session, requestId) => {
  adopted.push({ agent, session, requestId });
  return Promise.resolve(true);
};
globalThis.fetch = () => Promise.resolve({
  ok: true, status: 202,
  text: () => Promise.resolve(JSON.stringify({ ok: true, session_id: "s1", request_id: "chat-resume-1", state: "running" })),
});
answerTheCard();
await settle(); await settle();
console.log(JSON.stringify({ adopted, cardGone: !shown() }));
""")

    assert result["cardGone"] is True
    assert result["adopted"] == [{"agent": "a1", "session": "s1", "requestId": "chat-resume-1"}]


def test_a_reply_without_a_request_id_is_survivable():
    # An older runtime, or a permission answer that resolved without resuming.
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
let called = 0;
globalThis.window.adoptPortalResumedChatRun = () => { called += 1; return Promise.resolve(true); };
globalThis.fetch = () => Promise.resolve({
  ok: true, status: 202, text: () => Promise.resolve(JSON.stringify({ ok: true })),
});
answerTheCard();
await settle(); await settle();
console.log(JSON.stringify({ called, cardGone: !shown() }));
""")

    assert result == {"called": 0, "cardGone": True}


def test_the_adoption_writes_the_record_the_recovery_path_looks_for():
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "adoptResumedChatRunForAgent")

    # The recovery path already knows how to build the request context, open
    # the socket and follow the run to its end; it just needs something to find.
    assert "persistInflightChatRun(" in fn
    assert "recoverInflightChatRunForAgent(" in fn
    assert 'pendingText: "Working"' in fn, "this run is starting, not reconnecting"
    assert "chatState.currentRequest" in fn, "never adopt over a run already being followed"


# --------------------------- a card belongs to one conversation, not the reader


def test_a_replayed_question_from_another_conversation_is_ignored():
    """The socket asks for `replay=1`, so an unanswered question is redelivered.

    Starting a new chat used to bring the old question straight back: the
    handler mounted whatever arrived without asking which conversation it
    happened in.
    """
    result = _run_node("""
setPending({ question_request: QUESTION });
dispatch("portal:runtime-event", {
  event: { type: "question.requested", session_id: "s-old", data: { question_request: QUESTION } },
  sessionId: "s-old",
});
console.log(JSON.stringify({ shown: shown() }));
""")

    # The shim's open session is "s1"; the event names "s-old".
    assert result["shown"] is False


def test_a_question_for_the_open_conversation_still_shows():
    result = _run_node("""
setPending({ question_request: QUESTION });
dispatch("portal:runtime-event", {
  event: { type: "question.requested", session_id: "s1", data: { question_request: QUESTION } },
  sessionId: "s1",
});
console.log(JSON.stringify({ shown: shown() }));
""")

    assert result["shown"] is True


def test_an_event_that_names_no_session_is_taken_at_face_value():
    # Some runtime events carry only a request id; dropping those would lose
    # live updates for the conversation actually on screen.
    result = _run_node("""
setPending({ question_request: QUESTION });
dispatch("portal:runtime-event", {
  event: { type: "question.requested", data: { question_request: QUESTION } },
});
console.log(JSON.stringify({ shown: shown() }));
""")

    assert result["shown"] is True


def test_a_rebuild_in_another_conversation_drops_the_card_instead_of_moving_it():
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const beforeMove = shown();
// The reader opens a different conversation and its transcript is rebuilt.
window.currentPortalSessionId = () => "s2";
list.children = [];
setPending({});
dispatch("portal:history-rendered", {});
const afterMove = shown();
drainTimers(); await settle();
console.log(JSON.stringify({ beforeMove, afterMove, afterCheck: shown() }));
""")

    assert result["beforeMove"] is True
    assert result["afterMove"] is False, "the question is not the new conversation's to answer"
    assert result["afterCheck"] is False


def test_starting_a_new_chat_is_a_clean_break():
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "startNewChatForSelectedAgent")

    assert 'beginTranscript(state.selectedAgentId, "")' in fn, "a new conversation is a new claim"
    assert "disconnectEventSocket();" in fn, "the old socket replays on every reconnect"


def test_clearing_the_conversation_narrows_the_event_filter():
    # The fallback to the socket's session meant clearing the conversation
    # *widened* the filter: `chatState.sessionId` went to "" while the socket
    # still held the old id, so every replayed event matched.
    js = CHAT_UI.read_text(encoding="utf-8")

    assert 'const currentSessionId = chatState.sessionId || "";' in js
    assert "chatState.sessionId || socketCtx.sessionId" not in js


# ------------------------- the composer and the card are one way forward


def _intent(pending, *, mounted=True):
    """What `portalPendingComposerIntent` reports for a given card."""
    return _run_node(f"""
setPending({json.dumps(pending)});
{"runtimeEvent('question.requested', { question_request: " + json.dumps(pending.get("question_request")) + " });" if pending.get("question_request") and mounted else ""}
{"runtimeEvent('permission.requested', { permission_request: " + json.dumps(pending.get("permission_request")) + " });" if pending.get("permission_request") and mounted else ""}
console.log(JSON.stringify({{ intent: window.portalPendingComposerIntent() }}));
""")


FREE_TEXT = {"request_id": "q-1", "questions": [{"question": "Which project?", "custom": True}]}
OPTIONS_ONLY = {"request_id": "q-2", "questions": [
    {"question": "Which project?", "custom": False, "options": [{"label": "EFP"}, {"label": "OPS"}]}]}
TWO_QUESTIONS = {"request_id": "q-3", "questions": [
    {"question": "Which project?", "custom": True},
    {"question": "Which type?", "custom": True}]}


def test_a_single_free_text_question_can_be_answered_from_the_composer():
    # The card's own text box does exactly this; the composer is a roomier way
    # to reach it.
    assert _intent({"question_request": FREE_TEXT})["intent"] == {"acceptsText": True, "reason": ""}


def test_a_question_with_no_free_text_sends_the_member_to_the_card():
    intent = _intent({"question_request": OPTIONS_ONLY})["intent"]

    assert intent["acceptsText"] is False
    assert "options" in intent["reason"]


def test_several_questions_cannot_be_answered_by_one_typed_line():
    intent = _intent({"question_request": TWO_QUESTIONS})["intent"]

    assert intent["acceptsText"] is False
    assert "questions" in intent["reason"]


def test_a_typed_line_cannot_approve_a_tool():
    intent = _intent({"permission_request": {"request_id": "p-1", "tool": "bash", "args": "ls"}})["intent"]

    assert intent["acceptsText"] is False
    assert "Approve" in intent["reason"]


def test_no_card_means_the_composer_behaves_normally():
    assert _intent({})["intent"] is None


def test_sending_while_a_question_is_pending_answers_it_rather_than_starting_a_turn():
    # The run stays stopped until the tool call is resolved. An ordinary message
    # does not resolve it: the next run replays the pending call, asks again and
    # stops -- so the message reached the transcript, never reached the model,
    # and the question came back looking like a new one.
    js = CHAT_UI.read_text(encoding="utf-8")
    submit = _extract_js_function(js, "submitChatForSelectedAgent")
    head = submit[: submit.index("portalAnswerPendingWithText") + 40]

    assert "portalPendingComposerIntent()" in head
    assert "if (!pendingIntent.acceptsText)" in head
    assert "showToast(pendingIntent.reason)" in head
    # And the answer path is taken before anything that would start a new turn.
    assert "guardNoActiveChatRequestForAgent" in submit[: submit.index("portalPendingComposerIntent")]


def test_the_composer_says_what_sending_will_do():
    js = CHAT_UI.read_text(encoding="utf-8")
    placeholder = _extract_js_function(js, "updateChatInputPlaceholder")

    assert "portalPendingComposerIntent" in placeholder
    assert "Type your answer" in placeholder
    assert 'document.addEventListener("portal:pending-input-changed", updateChatInputPlaceholder)' in js
