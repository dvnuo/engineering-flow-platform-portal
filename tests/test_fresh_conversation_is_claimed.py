"""A greeting is a conversation that has not begun, and it is claimed as one.

Reported: a newly created assistant was watched until it came up, a message
was sent, and the reply never appeared. The row said Thinking, a toast at the
top carried the reply's first words, and reloading the page showed the whole
exchange.

The reply had arrived. What dropped it was the transcript's ownership check
(transcriptAcceptsLiveWrite): the start restored no session -- a new assistant
has none -- so the greeting was painted without claiming the transcript, which
stayed owned by whatever was shown before: nothing at all on a fresh page, or
another assistant's conversation. Every live writer for the first message then
answered "not this conversation" and routed the reply to the background, as a
run finishing out of view. The same unclaimed greeting followed a selection
that found nothing to restore, and deleting the open session.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function

CHAT_UI = Path("app/static/js/chat_ui.js")


def _chat_ui() -> str:
    return CHAT_UI.read_text(encoding="utf-8")


def _run_node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping transcript ownership test")
    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _transcript_bundle(js: str) -> str:
    """The ownership functions, plus the two that paint a greeting after a start."""
    functions = (
        "beginTranscript",
        "transcriptShowsConversation",
        "transcriptAcceptsLiveWrite",
        "transcriptShowsPausedState",
        "settleTranscriptAfterStart",
        "showFreshConversationForSelectedAgent",
    )
    decl = js.split("const transcript = {", 1)[1].split("};", 1)[0]
    return "const transcript = {" + decl + "};\n" + "\n".join(
        _extract_js_function(js, name) for name in functions
    )


# A message list that only knows whether it holds rows, and whether the row it
# holds is the paused placeholder; that is all the settle step asks of it.
FAKE_PAGE = """
const screen = { rows: 0, paused: false };
const dom = { messageList: { querySelector(selector) {
  if (selector === ".message-row") return screen.rows ? {} : null;
  if (selector === '[data-assistant-paused="1"]') return screen.paused ? {} : null;
  return null;
} } };
const state = { selectedAgentId: "a1" };
let greetings = 0;
function clearMessageListToWelcome() { greetings += 1; screen.rows = 1; screen.paused = false; }
"""


def test_the_greeting_is_claimed_before_it_is_painted():
    body = _extract_js_function(_chat_ui(), "showFreshConversationForSelectedAgent")

    claim = body.index('beginTranscript(state.selectedAgentId, "");')
    paint = body.index("clearMessageListToWelcome();")
    assert claim < paint


def test_a_start_that_restored_nothing_claims_the_greeting_it_paints():
    # The reported case: the assistant came up while the page watched it. No
    # session came back, the paused row gave way to the greeting, and the
    # first message went into a transcript nobody had claimed.
    result = _run_node(f"""
{_transcript_bundle(_chat_ui())}
{FAKE_PAGE}
screen.rows = 1; screen.paused = true;
const before = transcriptAcceptsLiveWrite("a1", "webchat_first");
settleTranscriptAfterStart("a1");
const after = transcriptAcceptsLiveWrite("a1", "webchat_first");
console.log(JSON.stringify({{ before, after, greetings }}));
""")

    assert result == {"before": False, "after": True, "greetings": 1}


def test_a_greeting_takes_the_transcript_from_another_assistants_conversation():
    # a0's conversation was on screen when a1 was created and selected. The
    # greeting is a1's, whatever was shown before it.
    result = _run_node(f"""
{_transcript_bundle(_chat_ui())}
{FAKE_PAGE}
beginTranscript("a0", "s0");
screen.rows = 0;
settleTranscriptAfterStart("a1");
console.log(JSON.stringify({{
  firstMessage: transcriptAcceptsLiveWrite("a1", "webchat_first"),
  oldRun: transcriptShowsConversation("a0", "s0"),
}}));
""")

    assert result == {"firstMessage": True, "oldRun": False}


def test_a_conversation_on_screen_keeps_its_claim_through_a_start():
    # The settle step paints only when nothing is on screen. A restored history
    # holds the claim its load made, and a greeting claim must not supersede it.
    result = _run_node(f"""
{_transcript_bundle(_chat_ui())}
{FAKE_PAGE}
const token = beginTranscript("a1", "s1");
screen.rows = 1;
settleTranscriptAfterStart("a1");
console.log(JSON.stringify({{
  sameClaim: transcript.generation === token.generation,
  session: transcript.sessionId,
  greetings,
}}));
""")

    assert result == {"sameClaim": True, "session": "s1", "greetings": 0}


def test_a_start_for_an_assistant_not_on_screen_claims_nothing():
    result = _run_node(f"""
{_transcript_bundle(_chat_ui())}
{FAKE_PAGE}
screen.rows = 0;
settleTranscriptAfterStart("a2");
console.log(JSON.stringify({{ owner: transcript.agentId, greetings }}));
""")

    assert result == {"owner": None, "greetings": 0}


def test_a_selection_that_found_nothing_to_restore_claims_the_greeting():
    body = _extract_js_function(_chat_ui(), "performAgentSelection")

    assert "if (conversationIsLoading()) showFreshConversationForSelectedAgent();" in body


def test_deleting_the_open_conversation_claims_the_greeting():
    # The claim for the deleted session outlived it, and the next message got
    # a fresh session id that the stale claim refused.
    body = _extract_js_function(_chat_ui(), "deleteSessionForAgent")

    assert "showFreshConversationForSelectedAgent();" in body
    assert "clearMessageListToWelcome();" not in body


# Every greeting painted outside a load goes through the claim. The exceptions
# are the painters that already hold a claim for what they paint.
GREETING_PAINTERS_WITH_A_CLAIM = {
    "showFreshConversationForSelectedAgent",  # the claim itself
    "startNewChatForSelectedAgent",  # claims before it clears
    "renderChatHistory",  # inside loadSessionForAgent, claimed for the session it fetched
    "truncateDomFromUserArticle",  # an edit within the conversation already claimed
}


def test_no_greeting_is_painted_without_a_claim():
    js = _chat_ui()
    painters = set()
    for match in re.finditer(r"clearMessageListToWelcome\(\);", js):
        declared = list(re.finditer(r"^(?:async )?function (\w+)\(", js[: match.start()], re.MULTILINE))
        painters.add(declared[-1].group(1) if declared else "<top level>")

    assert painters == GREETING_PAINTERS_WITH_A_CLAIM
