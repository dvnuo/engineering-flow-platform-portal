"""The error log: failures the Portal saw, kept for Administration > Errors.

Five things write to ``error_events`` through :func:`record_error`:

* a run that ends ``failed`` or ``stale``
  (``AgentExecutionRepository.mark_status``), with the runtime's error code and
  message;
* an assistant whose runtime failed to create, start, stop, restart, delete or
  report its status (``app/api/agents.py``,
  ``runtime_profile_secret_service``);
* a connector **Test** that failed (``app_connector_test`` in ``app/web.py``);
* a Portal response of 500 or more, or an unhandled exception
  (:class:`ErrorCaptureMiddleware`);
* a warning or error from the Portal's own loggers (``app.*``), through
  :class:`ErrorLogHandler`, with the traceback when there is one.

Messages and details pass through ``app.redaction`` before they are stored and
are cut to a few kilobytes. ``fingerprint`` hashes the source, the code and
the message with ids and numbers blanked, so the same problem across members
and assistants shows as one group. Portal, log, assistant and connector events
that repeat within ``DEDUP_WINDOW_SECONDS`` for the same member, assistant and
path bump ``count`` instead of adding a row, so a runtime that is down for a
minute does not fill the table. :meth:`ErrorLogService.prune` keeps
``ERROR_LOG_RETENTION_DAYS`` days and at most ``ERROR_LOG_MAX_ROWS`` rows; the
handler's writer thread runs it hourly.

Nothing here raises into the caller: a failure to record a failure is counted
and dropped, and the handler ignores records emitted while it is writing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.log_context import get_log_context
from app.models.agent import Agent
from app.models.error_event import ErrorEvent
from app.models.user import User
from app.redaction import redact_text

SOURCE_EXECUTION = "execution"
SOURCE_ASSISTANT = "assistant"
SOURCE_CONNECTOR_TEST = "connector_test"
SOURCE_PORTAL = "portal"
SOURCE_LOG = "log"
SOURCES = (SOURCE_EXECUTION, SOURCE_ASSISTANT, SOURCE_CONNECTOR_TEST, SOURCE_PORTAL, SOURCE_LOG)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITIES = (SEVERITY_ERROR, SEVERITY_WARNING)

PERIOD_DAYS_OPTIONS = (1, 7, 30, 90)
DEFAULT_PERIOD_DAYS = 7
MESSAGE_LIMIT = 2000
DETAIL_LIMIT = 16000
DEDUP_WINDOW_SECONDS = 300
GROUP_LIMIT = 20
RECENT_LIMIT = 100
RECENT_LIMIT_MAX = 1000
EXPORT_LIMIT = 10000
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_ROWS = 50000
APP_LOGGER_NAME = "app"

EXPORT_COLUMNS = (
    "id", "occurred_at", "last_seen_at", "count", "severity", "source", "code", "message",
    "member", "username", "agent_name", "agent_id", "execution_id", "task_id", "session_id",
    "runtime_type", "model", "kind", "request_path", "logger_name", "trace_id", "detail",
)
CONTEXT_FIELDS = (
    ("Member", "member"), ("Assistant", "agent_name"), ("Runtime", "runtime_type"), ("Model", "model"),
    ("Kind", "kind"), ("Session", "session_id"), ("Execution", "execution_id"), ("Task", "task_id"),
    ("Request", "request_path"), ("Logger", "logger_name"), ("Trace", "trace_id"),
)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_HEX_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+")
_SPACE_RE = re.compile(r"\s+")

_suppressed = threading.local()
write_failures = 0


# ---------------------------------------------------------------------------
# Normalising input
# ---------------------------------------------------------------------------


def normalize_period_days(value: Any, default: int = DEFAULT_PERIOD_DAYS) -> int:
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return days if days in PERIOD_DAYS_OPTIONS else default


def normalize_source(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in SOURCES else None


def normalize_limit(value: Any, default: int = RECENT_LIMIT) -> int:
    try:
        limit = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(10, min(RECENT_LIMIT_MAX, limit))


def clean_text(value: Any, limit: int) -> str:
    """Redacted, stripped and cut to ``limit`` characters; empty for None."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = redact_text(text).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 3)].rstrip() + "..."
    return text


def normalize_for_fingerprint(message: Any) -> str:
    text = _UUID_RE.sub("#", str(message or "").lower())
    text = _HEX_RE.sub("#", text)
    text = _NUMBER_RE.sub("#", text)
    return _SPACE_RE.sub(" ", text).strip()[:300]


def fingerprint_for(source: Any, code: Any, message: Any) -> str:
    raw = "|".join([str(source or ""), str(code or "").strip().lower(), normalize_for_fingerprint(message)])
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def _trace_id_from_context() -> str | None:
    try:
        value = str(get_log_context().get("trace_id") or "").strip()
    except Exception:  # noqa: BLE001 - context is best effort
        return None
    return value if value and value != "-" else None


def _context_value(context: dict, key: str) -> str | None:
    value = str(context.get(key) or "").strip()
    return value if value and value != "-" else None


def _cut(value: Any, limit: int) -> str | None:
    text = clean_text(value, limit)
    return text or None


def _iso(value: datetime | None) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds") if value else ""


class _Suppress:
    """While active on this thread, :func:`record_error` and the handler do nothing (no recursion)."""

    def __enter__(self):
        self.previous = getattr(_suppressed, "active", False)
        _suppressed.active = True
        return self

    def __exit__(self, *exc_info):
        _suppressed.active = self.previous
        return False


def _suppress_enabled() -> bool:
    return bool(getattr(_suppressed, "active", False))


def _same(column, value):
    return column.is_(None) if value is None else column == value


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record_error(
    db: Session,
    *,
    source: str,
    message: Any,
    severity: str = SEVERITY_ERROR,
    code: Any = None,
    detail: Any = None,
    user_id: int | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    execution_id: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    runtime_type: str | None = None,
    model: str | None = None,
    kind: str | None = None,
    request_path: str | None = None,
    logger_name: str | None = None,
    trace_id: str | None = None,
    occurred_at: datetime | None = None,
    dedup_window_seconds: int = 0,
    commit: bool = False,
) -> ErrorEvent | None:
    """Add one event to ``db``: pending in the caller's transaction, or committed with ``commit=True``.

    With ``dedup_window_seconds``, a row with the same fingerprint, member,
    assistant and request path seen within the window is bumped instead.
    Never raises; a failure to record is counted in ``write_failures``.
    """
    global write_failures
    try:
        # Suppressed while writing: a log line the write itself emits must not
        # come back through the handler as another event.
        with _Suppress():
            return _record(
                db, source=source, message=message, severity=severity, code=code, detail=detail,
                user_id=user_id, agent_id=agent_id, agent_name=agent_name, execution_id=execution_id,
                task_id=task_id, session_id=session_id, runtime_type=runtime_type, model=model, kind=kind,
                request_path=request_path, logger_name=logger_name, trace_id=trace_id, occurred_at=occurred_at,
                dedup_window_seconds=dedup_window_seconds, commit=commit,
            )
    except Exception:  # noqa: BLE001 - the error log must never break what it observes
        write_failures += 1
        if commit:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None


def _record(db: Session, *, source, message, severity, code, detail, user_id, agent_id, agent_name, execution_id,
            task_id, session_id, runtime_type, model, kind, request_path, logger_name, trace_id, occurred_at,
            dedup_window_seconds, commit) -> ErrorEvent:
    source = normalize_source(source) or SOURCE_PORTAL
    severity = severity if severity in SEVERITIES else SEVERITY_ERROR
    code = _cut(code, 128)
    message = clean_text(message, MESSAGE_LIMIT) or code or "Unknown error"
    detail = _cut(detail, DETAIL_LIMIT)
    now = occurred_at or datetime.utcnow()
    fingerprint = fingerprint_for(source, code, message)
    user_id = int(user_id) if user_id is not None else None
    agent_id = _cut(agent_id, 36)
    request_path = _cut(request_path, 255)
    trace_id = _cut(trace_id, 64) or _trace_id_from_context()

    if dedup_window_seconds > 0:
        since = now - timedelta(seconds=dedup_window_seconds)
        existing = db.scalars(
            select(ErrorEvent)
            .where(
                ErrorEvent.fingerprint == fingerprint,
                ErrorEvent.last_seen_at >= since,
                _same(ErrorEvent.user_id, user_id),
                _same(ErrorEvent.agent_id, agent_id),
                _same(ErrorEvent.request_path, request_path),
            )
            .order_by(ErrorEvent.last_seen_at.desc(), ErrorEvent.id.desc())
            .limit(1)
        ).first()
        if existing is not None:
            existing.count = int(existing.count or 1) + 1
            existing.last_seen_at = max(existing.last_seen_at or now, now)
            if detail and not existing.detail:
                existing.detail = detail
            if trace_id and not existing.trace_id:
                existing.trace_id = trace_id
            db.add(existing)
            if commit:
                db.commit()
            return existing

    event = ErrorEvent(
        occurred_at=now,
        last_seen_at=now,
        count=1,
        severity=severity,
        source=source,
        code=code,
        message=message,
        detail=detail,
        fingerprint=fingerprint,
        user_id=user_id,
        agent_id=agent_id,
        agent_name=_cut(agent_name, 255),
        execution_id=_cut(execution_id, 36),
        task_id=_cut(task_id, 36),
        session_id=_cut(session_id, 128),
        runtime_type=_cut(runtime_type, 32),
        model=_cut(model, 128),
        kind=_cut(kind, 64),
        request_path=request_path,
        logger_name=_cut(logger_name, 128),
        trace_id=trace_id,
    )
    db.add(event)
    if commit:
        db.commit()
    return event


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class ErrorLogService:
    """What Administration > Errors shows: totals, the same problem grouped once, the recent rows, one event, CSV rows, and pruning."""

    def __init__(self, db: Session, *, now: datetime | None = None) -> None:
        self.db = db
        self.now = now or datetime.utcnow()

    def period_start(self, days: int) -> datetime:
        return self.now - timedelta(days=days)

    # -- filters -----------------------------------------------------------

    def _filters(self, days: int, source: str | None, query: str | None, fingerprint: str | None) -> list:
        filters = [ErrorEvent.last_seen_at >= self.period_start(days)]
        if source:
            filters.append(ErrorEvent.source == source)
        if fingerprint:
            filters.append(ErrorEvent.fingerprint == fingerprint)
        text = str(query or "").strip()
        if text:
            like = "%" + text + "%"
            member_ids = select(User.id).where(or_(User.username.ilike(like), User.nickname.ilike(like)))
            filters.append(
                or_(
                    ErrorEvent.message.ilike(like),
                    ErrorEvent.code.ilike(like),
                    ErrorEvent.detail.ilike(like),
                    ErrorEvent.agent_name.ilike(like),
                    ErrorEvent.request_path.ilike(like),
                    ErrorEvent.logger_name.ilike(like),
                    ErrorEvent.agent_id == text,
                    ErrorEvent.execution_id == text,
                    ErrorEvent.task_id == text,
                    ErrorEvent.session_id == text,
                    ErrorEvent.trace_id == text,
                    ErrorEvent.user_id.in_(member_ids),
                )
            )
        return filters

    # -- reads -------------------------------------------------------------

    def overview(self, days: int, *, source: str | None = None, query: str | None = None,
                 fingerprint: str | None = None, limit: int = RECENT_LIMIT) -> dict:
        filters = self._filters(days, source, query, fingerprint)
        limit = max(1, min(RECENT_LIMIT_MAX, int(limit or RECENT_LIMIT)))
        return {
            "days": days,
            "period_start": _iso(self.period_start(days)),
            "period_end": _iso(self.now),
            "source": source or "",
            "query": str(query or "").strip(),
            "fingerprint": fingerprint or "",
            "limit": limit,
            "totals": self._totals(filters),
            "by_source": self._by_source(filters),
            "groups": self._groups(filters),
            **self._recent(filters, limit),
        }

    def _totals(self, filters: list) -> dict:
        errors = case((ErrorEvent.severity == SEVERITY_ERROR, ErrorEvent.count), else_=0)
        warnings = case((ErrorEvent.severity == SEVERITY_WARNING, ErrorEvent.count), else_=0)
        failed_runs = case((ErrorEvent.source == SOURCE_EXECUTION, ErrorEvent.count), else_=0)
        row = self.db.execute(
            select(
                func.count(ErrorEvent.id),
                func.coalesce(func.sum(ErrorEvent.count), 0),
                func.coalesce(func.sum(errors), 0),
                func.coalesce(func.sum(warnings), 0),
                func.coalesce(func.sum(failed_runs), 0),
                func.count(ErrorEvent.user_id.distinct()),
                func.count(ErrorEvent.agent_id.distinct()),
                func.max(ErrorEvent.last_seen_at),
            ).where(*filters)
        ).one()
        return {
            "rows": int(row[0] or 0),
            "events": int(row[1] or 0),
            "errors": int(row[2] or 0),
            "warnings": int(row[3] or 0),
            "failed_runs": int(row[4] or 0),
            "members": int(row[5] or 0),
            "assistants": int(row[6] or 0),
            "last_seen_at": _iso(row[7]),
        }

    def _by_source(self, filters: list) -> list[dict]:
        rows = self.db.execute(
            select(ErrorEvent.source, func.coalesce(func.sum(ErrorEvent.count), 0)).where(*filters).group_by(ErrorEvent.source)
        ).all()
        counts = {name: 0 for name in SOURCES}
        for name, events in rows:
            counts[name] = int(events or 0)
        return [{"source": name, "events": counts[name]} for name in SOURCES]

    def _groups(self, filters: list) -> list[dict]:
        events = func.coalesce(func.sum(ErrorEvent.count), 0).label("events")
        last_seen = func.max(ErrorEvent.last_seen_at).label("last_seen")
        rows = self.db.execute(
            select(
                ErrorEvent.fingerprint,
                func.max(ErrorEvent.source),
                func.min(ErrorEvent.severity),
                func.max(ErrorEvent.code),
                events,
                func.min(ErrorEvent.occurred_at),
                last_seen,
                func.count(ErrorEvent.user_id.distinct()),
                func.count(ErrorEvent.agent_id.distinct()),
                func.max(ErrorEvent.id),
            )
            .where(*filters)
            .group_by(ErrorEvent.fingerprint)
            .order_by(events.desc(), last_seen.desc())
            .limit(GROUP_LIMIT)
        ).all()
        latest_ids = [row[9] for row in rows if row[9] is not None]
        messages: dict[int, str] = {}
        if latest_ids:
            messages = dict(self.db.execute(select(ErrorEvent.id, ErrorEvent.message).where(ErrorEvent.id.in_(latest_ids))).all())
        return [
            {
                "fingerprint": row[0],
                "source": row[1],
                "severity": row[2] or SEVERITY_ERROR,
                "code": row[3] or "",
                "events": int(row[4] or 0),
                "first_seen_at": _iso(row[5]),
                "last_seen_at": _iso(row[6]),
                "members": int(row[7] or 0),
                "assistants": int(row[8] or 0),
                "latest_id": row[9],
                "message": messages.get(row[9], ""),
            }
            for row in rows
        ]

    def _joined(self):
        return (
            select(ErrorEvent, User.username, User.nickname, Agent.name)
            .outerjoin(User, User.id == ErrorEvent.user_id)
            .outerjoin(Agent, Agent.id == ErrorEvent.agent_id)
        )

    def _recent(self, filters: list, limit: int) -> dict:
        rows = self.db.execute(
            self._joined().where(*filters).order_by(ErrorEvent.last_seen_at.desc(), ErrorEvent.id.desc()).limit(limit + 1)
        ).all()
        return {
            "recent": [self._row_dict(*row) for row in rows[:limit]],
            "has_more": len(rows) > limit,
        }

    def get(self, event_id: int) -> dict | None:
        row = self.db.execute(self._joined().where(ErrorEvent.id == int(event_id))).first()
        return self._row_dict(*row) if row else None

    def export_rows(self, days: int, *, source: str | None = None, query: str | None = None,
                    fingerprint: str | None = None) -> list[dict]:
        filters = self._filters(days, source, query, fingerprint)
        rows = self.db.execute(
            self._joined().where(*filters).order_by(ErrorEvent.last_seen_at.desc(), ErrorEvent.id.desc()).limit(EXPORT_LIMIT)
        ).all()
        records = []
        for row in rows:
            item = self._row_dict(*row)
            item["detail"] = item["detail"][:4000]
            records.append({column: item.get(column, "") for column in EXPORT_COLUMNS})
        return records

    @staticmethod
    def _row_dict(event: ErrorEvent, username: str | None = None, nickname: str | None = None,
                  agent_name: str | None = None) -> dict:
        member = str(nickname or "").strip() or str(username or "").strip()
        if not member and event.user_id:
            member = "#%s" % event.user_id
        return {
            "id": event.id,
            "occurred_at": _iso(event.occurred_at),
            "last_seen_at": _iso(event.last_seen_at),
            "count": int(event.count or 1),
            "severity": event.severity,
            "source": event.source,
            "code": event.code or "",
            "message": event.message or "",
            "detail": event.detail or "",
            "fingerprint": event.fingerprint,
            "user_id": event.user_id,
            "username": username or "",
            "member": member,
            "agent_id": event.agent_id or "",
            "agent_name": agent_name or event.agent_name or "",
            "agent_exists": bool(agent_name),
            "execution_id": event.execution_id or "",
            "task_id": event.task_id or "",
            "session_id": event.session_id or "",
            "runtime_type": event.runtime_type or "",
            "model": event.model or "",
            "kind": event.kind or "",
            "request_path": event.request_path or "",
            "logger_name": event.logger_name or "",
            "trace_id": event.trace_id or "",
        }

    @staticmethod
    def describe(event: dict) -> str:
        """The event as plain text, for a ticket or a chat message."""
        head = " ".join(part for part in (event.get("source", ""), event.get("code", "")) if part)
        when = event.get("occurred_at", "").replace("T", " ")
        if int(event.get("count") or 1) > 1:
            when = "%s (seen %s times, last %s)" % (when, event.get("count"), event.get("last_seen_at", "").replace("T", " "))
        lines = [head, "When: %s" % when, "", event.get("message", ""), ""]
        for label, key in CONTEXT_FIELDS:
            value = event.get(key)
            if value:
                lines.append("%s: %s" % (label, value))
        if event.get("detail"):
            lines.extend(["", event["detail"]])
        return "\n".join(lines).strip() + "\n"

    # -- retention ---------------------------------------------------------

    def prune(self, retention_days: int, max_rows: int) -> dict:
        """Delete rows last seen more than ``retention_days`` ago (0 keeps them), then the oldest beyond ``max_rows`` (0 = no cap)."""
        deleted_old = 0
        deleted_over_cap = 0
        if retention_days and retention_days > 0:
            cutoff = self.now - timedelta(days=int(retention_days))
            deleted_old = int(self.db.execute(delete(ErrorEvent).where(ErrorEvent.last_seen_at < cutoff)).rowcount or 0)
        if max_rows and max_rows > 0:
            total = int(self.db.scalar(select(func.count(ErrorEvent.id))) or 0)
            excess = total - int(max_rows)
            if excess > 0:
                ids = self.db.scalars(
                    select(ErrorEvent.id).order_by(ErrorEvent.last_seen_at.asc(), ErrorEvent.id.asc()).limit(excess)
                ).all()
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    deleted_over_cap += int(self.db.execute(delete(ErrorEvent).where(ErrorEvent.id.in_(chunk))).rowcount or 0)
        self.db.commit()
        return {"deleted_old": deleted_old, "deleted_over_cap": deleted_over_cap}


# ---------------------------------------------------------------------------
# Portal responses and exceptions
# ---------------------------------------------------------------------------


def _default_session_factory() -> Callable[[], Session]:
    from app.db import SessionLocal  # the engine is built from settings; resolved on first use

    return SessionLocal


class ErrorCaptureMiddleware:
    """Pure ASGI: records a response of 500 or more, or an unhandled exception, and lets both continue as before.

    Nothing is buffered beyond the first two kilobytes of a 5xx body (for the
    ``detail``), so streamed responses pass through untouched. The exception is
    re-raised unchanged, so the server still logs it and returns its usual 500.
    """

    def __init__(self, app, *, session_factory: Callable[[], Session] | None = None,
                 enabled: bool | Callable[[], bool] = True) -> None:
        self.app = app
        self._session_factory = session_factory
        self._enabled = enabled

    def _is_enabled(self) -> bool:
        try:
            return bool(self._enabled() if callable(self._enabled) else self._enabled)
        except Exception:  # noqa: BLE001
            return False

    def _sessions(self) -> Callable[[], Session]:
        if self._session_factory is None:
            self._session_factory = _default_session_factory()
        return self._session_factory

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self._is_enabled():
            await self.app(scope, receive, send)
            return

        captured: dict[str, Any] = {"status": None, "body": b""}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                captured["status"] = int(message.get("status") or 0)
            elif message["type"] == "http.response.body" and (captured["status"] or 0) >= 500 and len(captured["body"]) < 2048:
                captured["body"] += bytes(message.get("body") or b"")[:2048]
            await send(message)

        trace_id = _trace_id_from_context()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            await run_in_threadpool(self._record_exception, scope, exc, trace_id)
            raise
        if (captured["status"] or 0) >= 500:
            await run_in_threadpool(self._record_response, scope, captured["status"], captured["body"], trace_id)

    @staticmethod
    def _request_path(scope) -> str:
        return ("%s %s" % (scope.get("method", ""), scope.get("path", ""))).strip()

    def _record_exception(self, scope, exc: BaseException, trace_id: str | None) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._write(
            code=type(exc).__name__,
            message="%s: %s" % (type(exc).__name__, exc) if str(exc) else type(exc).__name__,
            detail=detail[-DETAIL_LIMIT:],
            request_path=self._request_path(scope),
            trace_id=trace_id,
        )

    def _record_response(self, scope, status: int, body: bytes, trace_id: str | None) -> None:
        text = body.decode("utf-8", errors="replace").strip()
        message = ""
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, dict):
                detail = data.get("detail")
                if isinstance(detail, str):
                    message = detail
                elif detail is not None:
                    message = json.dumps(detail, ensure_ascii=True)[:300]
        if not message:
            message = text[:300]
        path = self._request_path(scope)
        summary = "HTTP %s on %s" % (status, path)
        if message:
            summary = "%s: %s" % (summary, message)
        self._write(code="http_%s" % status, message=summary, detail=None, request_path=path, trace_id=trace_id)

    def _write(self, **fields) -> None:
        db = None
        try:
            db = self._sessions()()
            record_error(db, source=SOURCE_PORTAL, severity=SEVERITY_ERROR, dedup_window_seconds=DEDUP_WINDOW_SECONDS, commit=True, **fields)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# The Portal's own log
# ---------------------------------------------------------------------------


class ErrorLogHandler(logging.Handler):
    """Writes WARNING-and-above records of the Portal's own loggers (``app.*``) to ``error_events``.

    Records go through a bounded queue to one writer thread with its own
    session, so a request thread never waits on the error log; ``asynchronous=False``
    writes inline (tests). A per-minute budget and the dedup window keep a
    flapping component from filling the table; what the budget drops is
    counted in ``dropped``. The writer also prunes the table hourly.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        level: int = logging.WARNING,
        asynchronous: bool = True,
        dedup_window_seconds: int = DEDUP_WINDOW_SECONDS,
        max_per_minute: int = 120,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_rows: int = DEFAULT_MAX_ROWS,
        prune_interval_seconds: int = 3600,
        first_prune_after_seconds: int = 120,
    ) -> None:
        super().__init__(level=level)
        self._session_factory = session_factory
        self._asynchronous = asynchronous
        self._dedup = dedup_window_seconds
        self._max_per_minute = max_per_minute
        self._retention_days = retention_days
        self._max_rows = max_rows
        self._prune_interval = prune_interval_seconds
        self._queue: queue.Queue = queue.Queue(maxsize=2000)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._minute: int | None = None
        self._minute_count = 0
        self._last_prune_at = time.monotonic() - prune_interval_seconds + first_prune_after_seconds
        self.dropped = 0
        self.written = 0

    # -- logging.Handler ---------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if _suppress_enabled() or not self._wanted(record) or not self._allow():
                return
            payload = self._payload(record)
            if self._asynchronous:
                self._enqueue(payload)
            else:
                self._write(payload)
        except Exception:  # noqa: BLE001 - logging must never fail the caller
            pass

    @staticmethod
    def _wanted(record: logging.LogRecord) -> bool:
        return record.name == APP_LOGGER_NAME or record.name.startswith(APP_LOGGER_NAME + ".")

    def _allow(self) -> bool:
        minute = int(time.monotonic() // 60)
        if minute != self._minute:
            self._minute = minute
            self._minute_count = 0
        self._minute_count += 1
        if self._minute_count > self._max_per_minute:
            self.dropped += 1
            return False
        return True

    def _payload(self, record: logging.LogRecord) -> dict:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        detail = None
        code = None
        exc_info = record.exc_info
        if exc_info and isinstance(exc_info, tuple) and exc_info[0] is not None:
            code = exc_info[0].__name__
            detail = "".join(traceback.format_exception(*exc_info))
        elif record.exc_text:
            detail = record.exc_text
        if not detail:
            detail = "%s:%s in %s" % (record.pathname, record.lineno, record.funcName)
        context = get_log_context()
        return {
            "source": SOURCE_LOG,
            "severity": SEVERITY_ERROR if record.levelno >= logging.ERROR else SEVERITY_WARNING,
            "code": code,
            "message": message,
            "detail": detail,
            "logger_name": record.name,
            "trace_id": _context_value(context, "trace_id"),
            "agent_id": _context_value(context, "agent_id"),
            "task_id": _context_value(context, "portal_task_id"),
            "request_path": _context_value(context, "path"),
            "occurred_at": datetime.utcfromtimestamp(record.created),
        }

    # -- writer thread -----------------------------------------------------

    def _sessions(self) -> Callable[[], Session]:
        if self._session_factory is None:
            self._session_factory = _default_session_factory()
        return self._session_factory

    def _enqueue(self, payload: dict) -> None:
        self._ensure_thread()
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self.dropped += 1

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="error-log-writer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=1.0)
            except queue.Empty:
                payload = None
            if payload is not None:
                try:
                    self._write(payload)
                finally:
                    self._queue.task_done()
            self._maybe_prune()
        while True:
            try:
                payload = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._write(payload)
            finally:
                self._queue.task_done()

    def _write(self, payload: dict) -> None:
        with _Suppress():
            db = None
            try:
                db = self._sessions()()
                if record_error(db, dedup_window_seconds=self._dedup, commit=True, **payload) is not None:
                    self.written += 1
            except Exception:  # noqa: BLE001
                pass
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:  # noqa: BLE001
                        pass

    def _maybe_prune(self, force: bool = False) -> dict | None:
        now = time.monotonic()
        if not force and now - self._last_prune_at < self._prune_interval:
            return None
        self._last_prune_at = now
        with _Suppress():
            db = None
            try:
                db = self._sessions()()
                return ErrorLogService(db).prune(self._retention_days, self._max_rows)
            except Exception:  # noqa: BLE001
                return None
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:  # noqa: BLE001
                        pass

    def flush(self) -> None:
        if self._asynchronous and self._thread is not None and self._thread.is_alive():
            self._queue.join()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        super().close()


_installed_handler: ErrorLogHandler | None = None


def _capture_level(value: Any) -> int:
    name = str(value or "WARNING").strip().upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.WARNING


def install_error_log_handler(settings=None, *, session_factory: Callable[[], Session] | None = None,
                              asynchronous: bool = True) -> ErrorLogHandler:
    """Attach one :class:`ErrorLogHandler` to the ``app`` logger (idempotent)."""
    global _installed_handler
    if _installed_handler is not None:
        return _installed_handler
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    handler = ErrorLogHandler(
        session_factory=session_factory,
        level=_capture_level(getattr(settings, "error_log_capture_level", "WARNING")),
        asynchronous=asynchronous,
        retention_days=int(getattr(settings, "error_log_retention_days", DEFAULT_RETENTION_DAYS) or 0),
        max_rows=int(getattr(settings, "error_log_max_rows", DEFAULT_MAX_ROWS) or 0),
    )
    logging.getLogger(APP_LOGGER_NAME).addHandler(handler)
    _installed_handler = handler
    return handler


def installed_error_log_handler() -> ErrorLogHandler | None:
    return _installed_handler


def uninstall_error_log_handler() -> None:
    global _installed_handler
    handler = _installed_handler
    _installed_handler = None
    if handler is None:
        return
    logging.getLogger(APP_LOGGER_NAME).removeHandler(handler)
    handler.close()
