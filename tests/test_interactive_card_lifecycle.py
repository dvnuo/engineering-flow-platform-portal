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
CSS = Path("app/static/css/app.css")
APP_HTML = Path("app/templates/app.html")

NL = chr(10)

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
    intent = _intent({"question_request": FREE_TEXT})["intent"]

    assert intent["acceptsText"] is True
    assert intent["asked"] == "Which project?"


def test_a_question_that_offered_no_free_text_can_still_be_answered_in_words():
    # `custom: false` is how the card chooses to render, not a rule about what
    # the member may say -- and the runtime does not enforce it either.
    intent = _intent({"question_request": OPTIONS_ONLY})["intent"]

    assert intent["acceptsText"] is True
    assert "in your own words" in intent["note"]


def test_one_line_answers_the_first_of_several_questions_and_says_so():
    # The runtime takes a shorter answers array than there are questions and
    # reports the rest as unanswered, so the assistant can follow up on what is
    # still open. Blocking the composer here would have been stricter than the
    # runtime and stricter than the member needs.
    intent = _intent({"question_request": TWO_QUESTIONS})["intent"]

    assert intent["acceptsText"] is True
    assert "stay open" in intent["note"], "the member should know the others are still waiting"


def test_a_typed_line_cannot_approve_a_tool():
    # `permission/respond` wants approve or deny; prose is neither, and guessing
    # which one it meant is not a guess to make about running a tool.
    intent = _intent({"permission_request": {"request_id": "p-1", "tool": "bash", "args": "ls"}})["intent"]

    assert intent["acceptsText"] is False
    assert "Approve" in intent["reason"]


def test_the_composer_supplies_its_own_answer_rather_than_filling_the_card():
    # An options-only card has no text box to fill, and a card with several
    # questions has more than one.
    js = MODULE.read_text(encoding="utf-8")
    fn = js.split("window.portalAnswerPendingWithText = async (text) => {", 1)[1].split("\n  };", 1)[0]

    assert "submitQuestion(form, { answers: [String(text || \"\")] })" in fn
    assert "data-question-custom-input" not in fn


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
    listener = js.split('document.addEventListener("portal:pending-input-changed"', 1)[1]
    assert "updateChatInputPlaceholder();" in listener.split("});", 1)[0]


# ------------------------------------- an answer is part of the conversation


def _grouped(messages):
    """`groupSessionMessagesForDisplay` over a history payload."""
    js = CHAT_UI.read_text(encoding="utf-8")
    bundle = "\n".join(
        _extract_js_function(js, name)
        for name in ("questionAnswerFromMessage", "getAssistantDisplayGroupKey", "groupSessionMessagesForDisplay")
    )
    return _run_node(f"""
{bundle}
const entries = groupSessionMessagesForDisplay({json.dumps(messages)});
console.log(JSON.stringify(entries.map((e) => ({{ type: e.type, pairs: e.pairs || null }}))));
""")


def _answer_message(answers, questions, **extra):
    message = {
        "id": "m-answer",
        "role": "tool",
        "tool_name": "question",
        "content": 'User has answered your questions: "Which project?"="EFP".',
        "parts": [{"tool_result": {"metadata": {"answers": answers, "questions": questions}}}],
    }
    message.update(extra)
    return message


def test_an_answer_appears_in_the_transcript():
    """Answering left no trace at all -- the member watched their reply vanish.

    It is stored as the question tool's result, and the display grouping kept
    only `user` and `assistant`.
    """
    entries = _grouped([
        {"id": "u1", "role": "user", "content": "create a ticket"},
        _answer_message([["EFP"]], [{"header": "Project", "question": "Which project?"}]),
    ])

    assert [e["type"] for e in entries] == ["message", "question_answer"]
    assert entries[1]["pairs"] == [{"label": "Project", "value": "EFP"}]


def test_the_question_is_kept_with_the_answer():
    # A transcript read later has to say what "EFP" was an answer to.
    entries = _grouped([_answer_message(
        [["EFP"], ["Bug"]],
        [{"header": "Project"}, {"question": "What issue type?"}],
    )])

    assert entries[0]["pairs"] == [
        {"label": "Project", "value": "EFP"},
        {"label": "What issue type?", "value": "Bug"},
    ]


def test_other_tool_results_are_still_left_out_of_the_transcript():
    # Rendering every tool result would bury the conversation in bash output.
    entries = _grouped([
        {"id": "t1", "role": "tool", "tool_name": "bash", "content": "total 48",
         "parts": [{"tool_result": {"metadata": {"stdout": "total 48"}}}]},
        {"id": "a1", "role": "assistant", "content": "Listed the directory."},
    ])

    assert [e["type"] for e in entries] == ["assistant_group"]


def test_an_answer_with_nothing_in_it_is_not_rendered():
    entries = _grouped([_answer_message([[""], ["  "]], [{"header": "Project"}])])

    assert entries == []


def test_an_answer_ends_the_assistant_turn_it_belongs_to():
    # What the assistant says next was said with the answer in hand, so it is a
    # new group rather than a continuation of the one that asked.
    entries = _grouped([
        {"id": "a1", "role": "assistant", "content": "Which project?"},
        _answer_message([["EFP"]], [{"header": "Project"}]),
        {"id": "a2", "role": "assistant", "content": "Created EFP-1."},
    ])

    assert [e["type"] for e in entries] == ["assistant_group", "question_answer", "assistant_group"]


def test_the_answer_is_shown_before_the_card_goes():
    js = MODULE.read_text(encoding="utf-8")
    submit = js.split("async function submitQuestion(", 1)[1].split("\n  }", 1)[0]

    assert submit.index("showAnswerInTranscript(") < submit.index("clearCard()"), (
        "clearing first means the member watches their reply disappear"
    )


def test_the_provisional_row_is_replaced_rather_than_doubled():
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = js.split("window.renderPortalAnswerNow = function renderPortalAnswerNow(", 1)[1].split("\n};", 1)[0]

    assert 'querySelectorAll("[data-provisional-answer]")' in fn
    assert 'row.dataset.provisionalAnswer = "1"' in fn


# ------------------------------ the card takes the composer's place, by default


def _mode(intent, *, clicks=0):
    """Drive `syncComposerMode` over a fake composer for a given pending card."""
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "syncComposerMode")
    return _run_node(f"""
const classes = {{ form: new Set(["hidden"]), bar: new Set(["hidden"]), button: new Set() }};
const el = (key, extra) => Object.assign({{
  classList: {{
    add: (c) => classes[key].add(c),
    remove: (c) => classes[key].delete(c),
    toggle: (c, on) => (on ? classes[key].add(c) : classes[key].delete(c)),
    contains: (c) => classes[key].has(c),
  }},
}}, extra || {{}});
const note = {{ textContent: "" }};
const button = el("button", {{ textContent: "" }});
const bar = el("bar", {{ querySelector: (s) => (s === "[data-composer-switch-note]" ? note : button) }});
const form = el("form", {{}});
globalThis.document = {{ getElementById: (id) => (id === "chat-form" ? form : id === "composer-mode-switch" ? bar : null) }};
const dom = {{ chatInput: {{ focus: () => {{}} }} }};
globalThis.window = {{ portalPendingComposerIntent: () => ({json.dumps(intent)}) }};
let composerMode = "card";
{fn}
syncComposerMode();
for (let i = 0; i < {clicks}; i += 1) {{
  composerMode = composerMode === "message" ? "card" : "message";
  syncComposerMode();
}}
console.log(JSON.stringify({{
  composerHidden: classes.form.has("hidden"),
  barHidden: classes.bar.has("hidden"),
  switchOffered: !classes.button.has("hidden"),
  switchText: button.textContent,
  note: note.textContent,
}}));
""")


def test_a_card_takes_the_composer_off_the_screen_by_default():
    # The card is what the assistant just put in front of them, and it is the
    # one surface that can answer every kind of question.
    result = _mode({"acceptsText": True, "reason": ""})

    assert result["composerHidden"] is True
    assert result["barHidden"] is False
    assert result["switchOffered"] is True
    assert result["switchText"] == "Type your answer instead"


def test_the_switch_brings_the_composer_back():
    result = _mode({"acceptsText": True, "reason": "", "note": "Your message answers the question above."}, clicks=1)

    assert result["composerHidden"] is False
    assert result["switchText"] == "Back to the card"
    assert "answers the question" in result["note"]


def test_the_note_says_what_this_particular_line_will_do():
    # A card with several questions and a card with one are answered very
    # differently by one sentence.
    result = _mode(
        {"acceptsText": True, "reason": "", "note": "Your message answers the first question; the rest stay open."},
        clicks=1,
    )

    assert result["note"] == "Your message answers the first question; the rest stay open."


def test_the_switch_returns_to_the_card():
    result = _mode({"acceptsText": True, "reason": ""}, clicks=2)

    assert result["composerHidden"] is True
    assert result["switchText"] == "Type your answer instead"


def test_no_switch_is_offered_for_an_approval():
    # The one thing a typed line cannot be. Reading approve or deny out of prose
    # is a guess nobody should make on the member's behalf.
    result = _mode({"acceptsText": False, "reason": "Approve or reject the tool above to continue."})

    assert result["switchOffered"] is False
    assert result["composerHidden"] is True
    assert result["note"] == "Approve or reject the tool above to continue."


def test_with_no_card_the_composer_is_simply_there():
    result = _mode(None)

    assert result["composerHidden"] is False
    assert result["barHidden"] is True


def test_a_new_card_starts_from_the_card_again():
    js = CHAT_UI.read_text(encoding="utf-8")
    listener = js.split('document.addEventListener("portal:pending-input-changed"', 1)[1].split("});", 1)[0]

    assert 'composerMode = "card"' in listener, "the last card's preference must not carry to the next"


def test_the_card_arrives_with_an_entrance():
    css = CSS.read_text(encoding="utf-8")
    rules = [part.split("}", 1)[0] for part in css.split(".portal-interactive-card {")[1:]]

    assert any("animation: portal-interactive-in" in rule for rule in rules)
    assert "@keyframes portal-interactive-in" in css
    assert ".portal-interactive-card { animation: none; }" in css, "respect prefers-reduced-motion"


def test_the_switch_bar_keeps_the_foot_of_the_conversation_in_shape():
    # It stands where the composer would, so the transcript does not jump when
    # the card appears -- and the bottom reserve follows it down, giving the
    # card the room the composer was using.
    css = CSS.read_text(encoding="utf-8")
    rule = css.split(".portal-composer-switch {", 1)[1].split("}", 1)[0]

    assert "border-radius: 999px" in rule
    assert "var(--portal-" in rule


# ------------------------------- one surface at a time, and one place to type


def test_starting_a_new_chat_takes_the_card_state_with_it():
    """New chat wiped the list without going near this module.

    The card element went, but its state stayed -- and with it a switch bar
    still saying "Answer above to continue" about a card that was no longer on
    screen. A welcome means an empty conversation, and an empty conversation
    cannot be blocked on anything.
    """
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
const before = !!window.portalPendingComposerIntent();
list.children = [];
dispatch("portal:welcome-rendered", { agentId: "a1" });
console.log(JSON.stringify({ before, after: !!window.portalPendingComposerIntent() }));
""")

    assert result == {"before": True, "after": False}


def test_the_card_goes_away_while_the_composer_answers_for_it():
    # Both surfaces feed the same question, so showing both invites answering
    # twice -- and leaves two rounded boxes competing for the foot of the page.
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "syncComposerMode")

    assert "window.portalCollapsePendingCard(showingComposer)" in fn
    # And it comes back when there is no card in play, or the next one is hidden.
    assert "window.portalCollapsePendingCard(false)" in fn


def test_the_switch_wears_the_composer_s_own_pill():
    """It sits among Attach and Run settings, so it should look like them.

    Height, radius, border and hover lift all come from `.composer-pill-btn`;
    only the emphasis is added, because this is the one control the member is
    being invited to press.
    """
    html = APP_HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    rule = css.split(".portal-composer-switch-btn {", 1)[1].split("}", 1)[0]

    assert 'class="composer-pill-btn portal-composer-switch-btn' in html
    for reinvented in ("border-radius", "font:", "font-weight", "padding"):
        assert reinvented not in rule, f"{reinvented} already comes from the shared pill"


def test_the_capsule_hugs_its_content_rather_than_spanning_the_composer():
    # It stands in for the composer but holds one sentence and one control;
    # at the composer's full width it read as a second, empty box.
    css = CSS.read_text(encoding="utf-8")
    rule = css.split(".portal-composer-switch {", 1)[1].split("}", 1)[0]

    assert "display: inline-flex" in rule
    assert (NL + "  width:") not in rule, "a fixed width is the composer's, not this"
    assert "max-width: min(100%, 1000px)" in rule, "but it must not outgrow the composer either"
    assert "border-radius: 999px" in rule, "the pills' radius, not the composer's corner"
    # Same surface, blur and shadow as the composer, so it reads as that object.
    assert "backdrop-filter: blur(10px)" in rule
    assert "box-shadow:" in rule


def test_the_bar_is_a_caption_on_the_composer_rather_than_a_second_panel():
    js = CHAT_UI.read_text(encoding="utf-8")
    fn = _extract_js_function(js, "syncComposerMode")
    css = CSS.read_text(encoding="utf-8")
    inline = css.split(".portal-composer-switch.is-inline {", 1)[1].split("}", 1)[0]

    assert 'bar.classList.toggle("is-inline", showingComposer)' in fn
    assert "border: 0" in inline
    assert "background: transparent" in inline
    assert "backdrop-filter: none" in inline
    assert "box-shadow: none" in inline
    # Aligned to the composer it captions, rather than hugging its own text.
    assert "width: min(100%, 1000px)" in inline


def test_the_composer_wrap_stacks_its_children():
    # It was a flex row, so the bar sat *beside* the composer instead of above
    # it -- which is what made the foot of the page look split in two.
    css = CSS.read_text(encoding="utf-8")
    rule = css.split(".portal-composer-wrap {", 1)[1].split("}", 1)[0]

    assert "flex-direction: column" in rule
    assert "align-items: center" in rule


def test_collapsing_hides_the_row_that_holds_the_card():
    # The class is toggled on the row; a rule aimed at the card inside it does
    # nothing, which is how this shipped hidden-but-visible the first time.
    css = CSS.read_text(encoding="utf-8")

    assert ".portal-interactive-row.is-collapsed {" in css
    assert ".portal-interactive-card.is-collapsed {" not in css


@pytest.mark.parametrize(
    "questions,expected",
    [
        ([{"question": "Which project?", "header": "Project", "custom": True}], "Answering “Project”."),
        ([{"question": "Which project?", "custom": False, "options": [{"label": "EFP"}]}],
         "Answering “Which project?” in your own words."),
        ([{"question": "Which project?", "header": "Project", "custom": True},
          {"question": "Which type?", "custom": True}],
         "Answering “Project”. The other 1 stay open."),
    ],
    ids=["single", "options-only", "two"],
)
def test_the_note_names_the_question_it_is_answering(questions, expected):
    # The card is off screen while the composer answers, so a note that says
    # "the question above" is pointing at nothing.
    intent = _intent({"question_request": {"request_id": "q", "questions": questions}})["intent"]

    assert intent["note"] == expected
