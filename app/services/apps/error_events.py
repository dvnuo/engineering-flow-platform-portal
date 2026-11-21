"""What goes wrong with apps, written to the error log (Administration > Errors).

Two things: a deployment (or a rollback) that failed, once per release, and
an app that crashed or was killed on the host. A crashing app restarts and
crashes again, so its events are thinned out: one entry per app in ten
minutes, and whatever the host reports about that app in between is counted
on that entry instead of adding one.

The events have the source ``app``, the app's owner as the member, and the
app's name and address in the message (``error_events`` has no column for an
app). Nothing here raises: the error log never fails what it observes.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.models.app import App, AppRelease
from app.models.error_event import ErrorEvent
from app.models.user import User
from app.redaction import redact_value
from app.services.error_log_service import SOURCE_APP, record_error

logger = logging.getLogger(__name__)

RUNTIME_TYPE_APP = "app"
CRASH_KINDS = ("crashed", "killed")
CRASH_THROTTLE_SECONDS = 600.0
LOG_TAIL_CHARS = 6000
LOG_LINES = 80

_crash_lock = threading.Lock()
# app id -> (when its last entry was written, that entry's id)
_crash_noted: dict[str, tuple[float, Optional[int]]] = {}
_clock: Callable[[], float] = time.monotonic


def reset_crash_throttle() -> None:
    """Forget which apps have a recent crash entry (tests)."""
    with _crash_lock:
        _crash_noted.clear()


def _label(app: Optional[App], app_id: str) -> str:
    if app is None:
        return "App %s" % app_id
    return 'App "%s" (%s)' % (app.name, app.slug)


def _lines(pairs: list[tuple[str, Any]], tail: str = "") -> str:
    text = "\n".join("%s: %s" % (name, value) for name, value in pairs if value not in (None, ""))
    return text + ("\n\n" + tail if tail else "")


def record_deploy_failure(db: Session, release: AppRelease, app: Optional[App], exc: Any) -> None:
    """One entry for a release that ended failed: which app, which commit, why, and the end of the host's log."""
    try:
        if app is None:
            app = db.get(App, release.app_id)
        requester = db.get(User, release.requested_by_user_id) if release.requested_by_user_id else None
        what = "rollback" if release.kind == "rollback" else "deployment"
        reason = str(getattr(exc, "message", "") or exc or "").strip() or "no reason given"
        record_error(
            db,
            source=SOURCE_APP,
            code=str(getattr(exc, "code", "") or "deploy_failed"),
            message="%s: %s failed: %s" % (_label(app, release.app_id), what, reason),
            detail=_lines(
                [
                    ("app_id", release.app_id),
                    ("release_id", release.id),
                    ("commit", release.commit_sha),
                    ("ref", release.ref),
                    ("requested_by", requester.username if requester is not None else None),
                ],
                (release.log_tail or "")[-LOG_TAIL_CHARS:],
            ),
            user_id=app.owner_user_id if app is not None else None,
            execution_id=release.id,
            runtime_type=RUNTIME_TYPE_APP,
            kind=str(release.kind or "deploy"),
            commit=True,
        )
    except Exception:  # noqa: BLE001
        logger.info("apps: the failed release could not be added to the error log release_id=%s", release.id, exc_info=True)


def _crash_reason(kind: str, detail: Any) -> str:
    info = detail if isinstance(detail, dict) else {}
    if kind == "killed":
        reason = str(info.get("reason") or "").strip()
        return "was killed by the app host" + (" (%s)" % reason[:80] if reason else "")
    exit_code = info.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return "crashed (exit code %s)" % exit_code
    return "crashed"


def _bump(db: Session, event_id: Optional[int]) -> None:
    """Count one more occurrence on the entry already written for this app."""
    if event_id is None:
        return
    event = db.get(ErrorEvent, event_id)
    if event is None:
        return
    event.count = int(event.count or 1) + 1
    event.last_seen_at = datetime.utcnow()
    db.add(event)
    db.commit()


def record_host_crash(db: Session, app: App, kind: str, detail: Any = None, at: Any = None) -> bool:
    """An entry for a ``crashed`` or ``killed`` event of the host; whether a new entry was written.

    At most one entry per app in ten minutes: an event inside that time is
    counted on the entry that is there. Other kinds of host event are not
    errors and are ignored.
    """
    if kind not in CRASH_KINDS:
        return False
    try:
        now = _clock()
        with _crash_lock:
            noted = _crash_noted.get(app.id)
            recent = noted is not None and now - noted[0] < CRASH_THROTTLE_SECONDS
            if not recent:
                # Claimed before the write, so two events at once write one entry.
                _crash_noted[app.id] = (now, None)
        if recent:
            _bump(db, noted[1])
            return False
        info = redact_value(detail) if isinstance(detail, dict) else {}
        log = info.get("log") if isinstance(info, dict) else None
        tail = "\n".join(str(line) for line in log[-LOG_LINES:]) if isinstance(log, list) else ""
        event = record_error(
            db,
            source=SOURCE_APP,
            code="app_%s" % kind,
            message="%s %s." % (_label(app, app.id), _crash_reason(kind, detail)),
            detail=_lines([("app_id", app.id), ("reported_at", str(at)[:40] if at else None)], tail[-LOG_TAIL_CHARS:]),
            user_id=app.owner_user_id,
            runtime_type=RUNTIME_TYPE_APP,
            kind=kind,
            commit=True,
        )
        with _crash_lock:
            if event is None:
                # Nothing was written: the next event may try again.
                _crash_noted.pop(app.id, None)
            else:
                _crash_noted[app.id] = (now, event.id)
        return event is not None
    except Exception:  # noqa: BLE001
        logger.info("apps: the host event could not be added to the error log app_id=%s", getattr(app, "id", None), exc_info=True)
        return False
