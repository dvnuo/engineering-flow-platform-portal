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
    querySelector: () => null,
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


def test_rebuilding_the_transcript_brings_a_still_pending_card_back():
    result = _run_node("""
setPending({ question_request: QUESTION });
runtimeEvent("question.requested", { question_request: QUESTION });
list.children = [];   // renderChatHistory wipes the list
dispatch("portal:history-rendered", { agentId: "a1" });
const afterWipe = shown();
drainTimers(); await settle();
console.log(JSON.stringify({ afterWipe, restored: shown() }));
""")

    assert result["afterWipe"] is False
    assert result["restored"] is True


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


def test_the_rebuild_announces_itself():
    js = CHAT_UI.read_text(encoding="utf-8")
    tail = js.split("function renderChatHistory(", 1)[1].split("\n}\n", 1)[0]

    assert 'dom.messageList.innerHTML = ""' in tail, "this is the wipe the event exists for"
    assert "portal:history-rendered" in tail
