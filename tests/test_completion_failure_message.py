"""A failed run should say what happened, once.

Reported: a transient Copilot 500 rendered as three lines -- `completion_state:
error`, `incomplete_reason: <provider string>`, and the same provider string
again as the assistant response. Nothing in that told the reader the right move
was simply to send the message again.
"""
import re
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


def _chat_ui() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _hint_patterns() -> list[tuple[str, str]]:
    """(regex source, headline) for each entry in COMPLETION_FAILURE_HINTS."""
    js = _chat_ui()
    block = js[js.index("const COMPLETION_FAILURE_HINTS = ["):]
    block = block[: block.index("\n];")]
    return re.findall(r"match:\s*/(.+?)/i,\s*\n\s*headline:\s*\"(.+?)\"", block)


def _matches(reason: str) -> list[str]:
    """Headlines whose pattern matches, mirroring the JS first-match order."""
    hits = []
    for pattern, headline in _hint_patterns():
        # JS and Python agree on these constructs; \d and alternation only.
        if re.search(pattern.replace("\\\\", "\\"), reason, re.IGNORECASE):
            hits.append(headline)
    return hits


@pytest.mark.parametrize(
    "reason,expected",
    [
        (
            "GitHub Copilot HTTP transport failed with status 500 (Internal Server Error) "
            "response: Internal Server Error",
            "The model provider had a temporary failure",
        ),
        ("AI Platform HTTP transport failed (503): upstream", "The model provider had a temporary failure"),
        ("Rate limit exceeded", "The model provider is rate limiting"),
        ("GitHub Copilot HTTP transport failed with status 401 (Unauthorized)", "The model provider rejected your credentials"),
        ("GitHub Copilot HTTP transport timed out after 120s", "The model provider took too long to respond"),
        ("loop.max_iterations reached", "The assistant hit its step limit"),
    ],
)
def test_known_failures_get_a_headline(reason, expected):
    assert _matches(reason), f"no hint matched: {reason}"
    assert _matches(reason)[0] == expected


def test_the_reported_message_is_recognized():
    # The exact string from the report, so a future rewording of the provider
    # message cannot quietly stop matching.
    reported = (
        "GitHub Copilot HTTP transport failed with status 500 (Internal Server Error) "
        "response: Internal Server Error"
    )

    assert _matches(reported)[0] == "The model provider had a temporary failure"


def test_an_unrecognized_reason_falls_back_to_the_raw_fields():
    body = _extract_js_function(_chat_ui(), "renderCompletionDiagnosticFields")

    assert "if (!hint) return rawFields;" in body


def test_the_raw_fields_move_behind_a_disclosure():
    body = _extract_js_function(_chat_ui(), "renderCompletionDiagnosticFields")

    assert "Technical details" in body
    assert "chat-completion-raw" in body


def test_the_response_is_not_repeated_when_it_is_just_the_reason():
    # This is what produced the third identical line.
    body = _extract_js_function(_chat_ui(), "finalizeIncompleteAssistantRow")

    assert "responseIsJustTheReason" in body
    assert "responseText === reasonText" in body


def test_a_real_response_is_still_shown_alongside_the_failure():
    # A run that produced partial output before failing must not lose it.
    body = _extract_js_function(_chat_ui(), "finalizeIncompleteAssistantRow")

    assert "if (!responseIsJustTheReason) {" in body
    assert "No final assistant response was returned." in body


def test_every_hint_has_both_a_headline_and_an_action():
    js = _chat_ui()
    block = js[js.index("const COMPLETION_FAILURE_HINTS = ["):]
    block = block[: block.index("\n];")]

    headlines = re.findall(r"headline:\s*\"", block)
    details = re.findall(r"detail:\s*\"", block)
    assert len(headlines) == len(details) >= 5
    # A headline without a next step leaves the reader exactly where they were.
    assert "detail:" in block


def test_the_hint_block_is_styled():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".chat-completion-hint" in css
    assert ".chat-completion-raw" in css
