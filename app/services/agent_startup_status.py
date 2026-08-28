"""Turn raw agent status into something a non-engineer can act on.

Starting an assistant is the first thing a new member does after creating one,
and until now it surfaced either a bare "creating" or a Kubernetes error string.
Neither tells someone what is happening or what to do about it.

This module is deliberately pure: it maps (status, last_error) to a phase and a
next step, with no cluster access, so the mapping is testable and the same view
can be produced from a cached status.
"""
from __future__ import annotations

import re
from typing import Any


# The init containers give startup natural phase boundaries. Named for what a
# member would say, not for the Kubernetes object involved.
STARTUP_PHASES = (
    ("scheduling", "Finding capacity"),
    ("image", "Preparing the runtime"),
    ("assets", "Loading skills"),
    ("starting", "Starting up"),
    ("ready", "Ready"),
)

# Typical cold start. Used only to set expectations; the UI shows elapsed time
# alongside it rather than pretending to be a real progress bar.
TYPICAL_STARTUP_SECONDS = 40

STARTING_STATUSES = {"creating", "restarting", "starting", "pending"}


class _Hint:
    __slots__ = ("pattern", "headline", "detail", "action_label", "action")

    def __init__(self, pattern, headline, detail, action_label=None, action=None):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.headline = headline
        self.detail = detail
        self.action_label = action_label
        self.action = action


# Ordered most specific first. Each entry answers two questions a raw error
# never does: what actually went wrong, and whose problem it is to fix.
FAILURE_HINTS = (
    _Hint(
        r"CreateContainerConfigError|secret .*not found|efp-profile-",
        "Your connection settings aren't ready yet",
        "This assistant needs its Connections filled in before it can start.",
        "Open Connections",
        "open_connections",
    ),
    _Hint(
        r"ImagePullBackOff|ErrImagePull|manifest unknown|pull access denied",
        "The platform can't fetch the runtime image",
        "This isn't something you can fix from here. Your administrator needs to look at it.",
        "Contact support",
        "contact_support",
    ),
    _Hint(
        r"Insufficient|FailedScheduling|no nodes available|Unschedulable",
        "Waiting for capacity",
        "The cluster is full right now. This retries on its own.",
        None,
        None,
    ),
    _Hint(
        r"ProgressDeadlineExceeded|timed out|timeout",
        "Startup is taking longer than usual",
        "Something is stuck. Retrying usually clears it.",
        "Retry",
        "retry",
    ),
    _Hint(
        r"CrashLoopBackOff|back-off restarting",
        "The assistant keeps stopping after it starts",
        "This is usually a bad value in Connections. Check them, then retry.",
        "Open Connections",
        "open_connections",
    ),
    _Hint(
        r"401|403|Unauthorized|Forbidden|authentication",
        "A credential was rejected",
        "One of your connections has an invalid or expired credential.",
        "Open Connections",
        "open_connections",
    ),
)


def _phase_for(status: str, message: str) -> str:
    """Best-effort phase from the signals Portal actually has.

    Portal sees deployment-level status, not per-container progress, so this
    reads the error text for the container-level detail when it is present and
    otherwise reports the generic starting phase. Guessing a more precise phase
    than the data supports would be worse than a vague true one.
    """
    haystack = f"{status} {message}".lower()
    if "pull" in haystack or "image" in haystack:
        return "image"
    if "clone" in haystack or "skill" in haystack or "init" in haystack:
        return "assets"
    if "schedul" in haystack or "insufficient" in haystack or "pending" in haystack:
        return "scheduling"
    return "starting"


def _match_hint(message: str) -> _Hint | None:
    if not message:
        return None
    for hint in FAILURE_HINTS:
        if hint.pattern.search(message):
            return hint
    return None


def startup_view(status: str | None, last_error: str | None = None) -> dict[str, Any]:
    """Describe the assistant's startup state in member-facing terms."""

    normalized = str(status or "").strip().lower()
    message = str(last_error or "").strip()
    hint = _match_hint(message)

    if normalized == "running":
        return {
            "is_starting": False,
            "is_failed": False,
            "phase": "ready",
            "phases": [{"key": key, "label": label} for key, label in STARTUP_PHASES],
            "headline": "Ready",
            "detail": "",
            "action_label": None,
            "action": None,
            "typical_seconds": TYPICAL_STARTUP_SECONDS,
            "technical_detail": "",
        }

    if normalized in STARTING_STATUSES:
        phase = _phase_for(normalized, message)
        return {
            "is_starting": True,
            "is_failed": False,
            "phase": phase,
            "phases": [{"key": key, "label": label} for key, label in STARTUP_PHASES],
            "headline": "Starting your assistant",
            "detail": f"This usually takes about {TYPICAL_STARTUP_SECONDS} seconds.",
            "action_label": None,
            "action": None,
            "typical_seconds": TYPICAL_STARTUP_SECONDS,
            "technical_detail": message,
        }

    if normalized == "stopped":
        # Idle auto-stop is invisible otherwise, and "stopped" reads as broken.
        return {
            "is_starting": False,
            "is_failed": False,
            "phase": "stopped",
            "phases": [{"key": key, "label": label} for key, label in STARTUP_PHASES],
            "headline": "Paused to save resources",
            "detail": "It wakes up when you send a message.",
            "action_label": None,
            "action": None,
            "typical_seconds": TYPICAL_STARTUP_SECONDS,
            "technical_detail": message,
        }

    if normalized == "failed":
        return {
            "is_starting": False,
            "is_failed": True,
            "phase": "failed",
            "phases": [{"key": key, "label": label} for key, label in STARTUP_PHASES],
            "headline": hint.headline if hint else "The assistant couldn't start",
            "detail": hint.detail if hint else "Retrying often clears this. If it keeps happening, contact your administrator.",
            "action_label": hint.action_label if hint else "Retry",
            "action": hint.action if hint else "retry",
            "typical_seconds": TYPICAL_STARTUP_SECONDS,
            # Kept, but behind a disclosure: useful to whoever debugs it, noise
            # to whoever just wants to work.
            "technical_detail": message,
        }

    return {
        "is_starting": False,
        "is_failed": False,
        "phase": normalized or "unknown",
        "phases": [{"key": key, "label": label} for key, label in STARTUP_PHASES],
        "headline": normalized.replace("_", " ").title() if normalized else "Unknown",
        "detail": "",
        "action_label": None,
        "action": None,
        "typical_seconds": TYPICAL_STARTUP_SECONDS,
        "technical_detail": message,
    }
