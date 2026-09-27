"""Result cards in assistant responses (mobile scenario testing).

A skill ends a reply with fenced blocks whose language names a card and whose
body is JSON:

    ```efp-review    a checklist to approve (scenarios, a dry run)
    ```efp-evidence  one scenario run's result, screenshots, and video
    ```efp-matrix    every scenario run of a suite, live while it runs

Chat renders them in the browser (static/js/efp_cards.js). The task page
takes them out of the response text here and places card placeholders that
the same script fills in, reading any referenced files from the assistant's
workspace through the Portal proxy.
"""
from __future__ import annotations

import json
import re
from typing import Any

CARD_KINDS = ("review", "evidence", "matrix")
MAX_CARDS = 20
MAX_PAYLOAD_CHARS = 200_000
REVIEW_DECISIONS = ("approve", "changes")

# Skills whose task runs keep a live matrix at live_matrix_path(task id).
MOBILE_RUN_SKILLS = frozenset({"run-mobile-scenarios"})

_CARD_FENCE = re.compile(
    r"(?P<lead>^|\n)(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>efp-(?:review|evidence|matrix))[ \t]*\n"
    r"(?P<body>.*?)\n(?P=fence)[ \t]*(?=\n|$)",
    re.S,
)


def extract_cards(text: str | None) -> tuple[str, list[dict[str, Any]]]:
    """Return the text without its card blocks, and the cards in order.

    A block whose body is not JSON (a skill's mistake) stays in the text,
    where the reader can at least see it.
    """
    cards: list[dict[str, Any]] = []

    def replace(match: re.Match) -> str:
        if len(cards) >= MAX_CARDS:
            return match.group(0)
        body = match.group("body").strip()
        if not body or len(body) > MAX_PAYLOAD_CHARS:
            return match.group(0)
        try:
            payload = json.loads(body)
        except ValueError:
            return match.group(0)
        if not isinstance(payload, (dict, list)):
            return match.group(0)
        cards.append({"kind": match.group("lang")[len("efp-"):], "payload_json": json.dumps(payload)})
        return match.group("lead")

    cleaned = _CARD_FENCE.sub(replace, text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, cards


def live_matrix_path(task_id: str) -> str:
    """Where a run skill keeps the matrix of the task it runs for."""
    return f"mobile/runs/{task_id}/matrix.json"


def portal_task_id_sentence(task_id: str) -> str:
    """Told to every background task so skills can name task-scoped files."""
    return f"Portal task id: {task_id}."


def _clean_ids(values: Any) -> list[str]:
    out: list[str] = []
    for value in values or []:
        text = " ".join(str(value or "").split())[:64]
        if text and text not in out:
            out.append(text)
        if len(out) >= 200:
            break
    return out


def review_decision_text(*, decision: str, kind: str, title: str, approved: Any, declined: Any, notes: str) -> str:
    """The follow-up an assistant receives for a review; efp_cards.js sends
    the same text from chat. The first line is fixed so skills recognise it."""
    kind = " ".join(str(kind or "review").split())[:64] or "review"
    title = " ".join(str(title or "").split())[:200]
    approved_ids = _clean_ids(approved)
    declined_ids = _clean_ids(declined)
    label = "approved" if decision == "approve" else "changes requested"
    lines = [
        f"REVIEW DECISION: {label} ({kind}{': ' + title if title else ''})",
        f"Approved: {', '.join(approved_ids) if approved_ids else 'none'}",
        f"Not approved: {', '.join(declined_ids) if declined_ids else 'none'}",
    ]
    notes = str(notes or "").strip()[:4000]
    if notes:
        lines.append(f"Notes: {notes}")
    return "\n".join(lines)
