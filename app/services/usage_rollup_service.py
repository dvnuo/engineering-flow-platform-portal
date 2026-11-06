"""Daily usage rollups: the per-day sums the Usage panel reads, and retention for executions.

``agent_executions`` keeps every chat turn and task run with what it cost.
The panel does not want every row: it wants a day's sums per member, model
and runtime, over 7, 30 or 90 days. ``UsageRollupService`` recomputes
``usage_daily_rollups`` for a day from the executions of that day (delete the
day's rows, insert fresh sums, one transaction per day, so a rerun is
harmless), the worker does this hourly for the last few days, and
``prune_executions`` rolls old days up before deleting their executions, so
the sums outlive the rows. Credits are Copilot's own charge where the runtime
reported one, the estimate otherwise (``credit_value``), which is also how the
member table counts them.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_execution import AgentExecution
from app.models.usage_daily_rollup import UsageDailyRollup
from app.models.user import User
from app.repositories.agent_execution_repo import TERMINAL_EXECUTION_STATUSES

logger = logging.getLogger(__name__)

FAILED_EXECUTION_STATUSES = ("failed", "stale", "blocked")
DEFAULT_ROLLUP_WINDOW_DAYS = 3
DEFAULT_RETENTION_DAYS = 365
PERIOD_DAYS_OPTIONS = (7, 30, 90)
RECENT_REFRESH_SECONDS = 300

_recent_refresh_lock = threading.Lock()
_recent_refreshed_at: float = 0.0


def credit_value():
    """The credits of one execution: Copilot's charge when known, else the estimate."""
    return func.coalesce(AgentExecution.actual_credits, AgentExecution.estimated_credits)


def normalize_period_days(value: Any, *, default: int = 30) -> int:
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return days if days in PERIOD_DAYS_OPTIONS else default


def _float(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class _Bucket:
    executions: int = 0
    succeeded: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_credits: float = 0.0
    actual_credits: float = 0.0
    credits: float = 0.0
    duration_seconds: float = 0.0
    extra: dict = field(default_factory=dict)


class UsageRollupService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------ building

    def rollup_day(self, day: date) -> int:
        """Recompute the rollup rows of ``day`` from its executions; returns the row count."""
        start = datetime(day.year, day.month, day.day)
        end = start + timedelta(days=1)
        rows = self.db.execute(
            select(
                AgentExecution.created_by_user_id,
                AgentExecution.agent_id,
                AgentExecution.runtime_type,
                AgentExecution.kind,
                AgentExecution.model,
                AgentExecution.status,
                AgentExecution.input_tokens,
                AgentExecution.output_tokens,
                AgentExecution.cached_input_tokens,
                AgentExecution.reasoning_tokens,
                AgentExecution.estimated_credits,
                AgentExecution.actual_credits,
                AgentExecution.started_at,
                AgentExecution.finished_at,
            ).where(AgentExecution.created_at >= start, AgentExecution.created_at < end)
        ).all()
        buckets: dict[tuple, _Bucket] = defaultdict(_Bucket)
        for row in rows:
            key = (
                int(row.created_by_user_id or 0),
                str(row.agent_id or ""),
                str(row.runtime_type or "")[:32],
                str(row.kind or "")[:32],
                str(row.model or "")[:128],
            )
            bucket = buckets[key]
            bucket.executions += 1
            status = str(row.status or "").lower()
            if status == "succeeded":
                bucket.succeeded += 1
            elif status in FAILED_EXECUTION_STATUSES:
                bucket.failed += 1
            bucket.input_tokens += _int(row.input_tokens)
            bucket.output_tokens += _int(row.output_tokens)
            bucket.cached_input_tokens += _int(row.cached_input_tokens)
            bucket.reasoning_tokens += _int(row.reasoning_tokens)
            estimated = _float(row.estimated_credits) if row.estimated_credits is not None else None
            actual = _float(row.actual_credits) if row.actual_credits is not None else None
            bucket.estimated_credits += estimated or 0.0
            bucket.actual_credits += actual or 0.0
            bucket.credits += actual if actual is not None else (estimated or 0.0)
            if row.started_at and row.finished_at and row.finished_at >= row.started_at:
                bucket.duration_seconds += (row.finished_at - row.started_at).total_seconds()

        self.db.execute(delete(UsageDailyRollup).where(UsageDailyRollup.day == day))
        now = datetime.utcnow()
        for (user_id, agent_id, runtime_type, kind, model), bucket in buckets.items():
            self.db.add(
                UsageDailyRollup(
                    day=day,
                    user_id=user_id,
                    agent_id=agent_id,
                    runtime_type=runtime_type,
                    kind=kind,
                    model=model,
                    executions=bucket.executions,
                    succeeded=bucket.succeeded,
                    failed=bucket.failed,
                    input_tokens=bucket.input_tokens,
                    output_tokens=bucket.output_tokens,
                    cached_input_tokens=bucket.cached_input_tokens,
                    reasoning_tokens=bucket.reasoning_tokens,
                    estimated_credits=round(bucket.estimated_credits, 6),
                    actual_credits=round(bucket.actual_credits, 6),
                    credits=round(bucket.credits, 6),
                    duration_seconds=round(bucket.duration_seconds, 3),
                    updated_at=now,
                )
            )
        self.db.commit()
        return len(buckets)

    def rollup_days(self, days: Iterable[date]) -> dict[str, int]:
        return {day.isoformat(): self.rollup_day(day) for day in sorted(set(days))}

    def rollup_recent(self, window_days: int = DEFAULT_ROLLUP_WINDOW_DAYS, *, today: date | None = None) -> dict[str, int]:
        """Today and the ``window_days - 1`` days before it (late finishes land on their start day)."""
        today = today or datetime.utcnow().date()
        window = max(1, int(window_days or 1))
        return self.rollup_days(today - timedelta(days=offset) for offset in range(window))

    def refresh_recent(self, *, max_age_seconds: int = RECENT_REFRESH_SECONDS, today: date | None = None) -> bool:
        """Roll today and yesterday up unless a refresh ran in the last ``max_age_seconds``; used by the panel."""
        global _recent_refreshed_at
        now = time.monotonic()
        with _recent_refresh_lock:
            if max_age_seconds > 0 and _recent_refreshed_at and now - _recent_refreshed_at < max_age_seconds:
                return False
            _recent_refreshed_at = now
        self.rollup_recent(2, today=today)
        return True

    def prune_executions(self, retention_days: int = DEFAULT_RETENTION_DAYS, *, today: date | None = None) -> dict[str, int]:
        """Delete terminal executions older than ``retention_days`` after rolling their days up.

        0 keeps everything. Running or queued rows are never deleted, however old.
        """
        retention = int(retention_days or 0)
        if retention <= 0:
            return {"rolled_up_days": 0, "deleted": 0}
        today = today or datetime.utcnow().date()
        cutoff_day = today - timedelta(days=retention)
        cutoff = datetime(cutoff_day.year, cutoff_day.month, cutoff_day.day)
        oldest = self.db.execute(select(func.min(AgentExecution.created_at)).where(AgentExecution.created_at < cutoff)).scalar()
        if oldest is None:
            return {"rolled_up_days": 0, "deleted": 0}
        days = []
        day = oldest.date()
        while day < cutoff_day:
            days.append(day)
            day += timedelta(days=1)
        self.rollup_days(days)
        result = self.db.execute(
            delete(AgentExecution).where(
                and_(AgentExecution.created_at < cutoff, AgentExecution.status.in_(TERMINAL_EXECUTION_STATUSES))
            )
        )
        self.db.commit()
        deleted = int(result.rowcount or 0)
        logger.info("usage rollups: pruned %s executions older than %s after rolling up %s days", deleted, cutoff_day, len(days))
        return {"rolled_up_days": len(days), "deleted": deleted}

    # ------------------------------------------------------------- reading

    @staticmethod
    def period_start(days: int, *, today: date | None = None) -> date:
        today = today or datetime.utcnow().date()
        return today - timedelta(days=max(1, int(days)) - 1)

    def overview(self, days: int, *, today: date | None = None) -> dict[str, Any]:
        """Totals, the daily series and the breakdowns of the last ``days`` days, from the rollups."""
        today = today or datetime.utcnow().date()
        start = self.period_start(days, today=today)
        in_period = and_(UsageDailyRollup.day >= start, UsageDailyRollup.day <= today)
        R = UsageDailyRollup
        sums = (
            func.coalesce(func.sum(R.executions), 0),
            func.coalesce(func.sum(R.succeeded), 0),
            func.coalesce(func.sum(R.failed), 0),
            func.coalesce(func.sum(R.input_tokens), 0),
            func.coalesce(func.sum(R.output_tokens), 0),
            func.coalesce(func.sum(R.cached_input_tokens), 0),
            func.coalesce(func.sum(R.reasoning_tokens), 0),
            func.coalesce(func.sum(R.estimated_credits), 0.0),
            func.coalesce(func.sum(R.actual_credits), 0.0),
            func.coalesce(func.sum(R.credits), 0.0),
            func.coalesce(func.sum(R.duration_seconds), 0.0),
        )

        def _row_dict(row) -> dict[str, Any]:
            return {
                "executions": int(row[0]),
                "succeeded": int(row[1]),
                "failed": int(row[2]),
                "input_tokens": int(row[3]),
                "output_tokens": int(row[4]),
                "cached_input_tokens": int(row[5]),
                "reasoning_tokens": int(row[6]),
                "estimated_credits": round(float(row[7]), 2),
                "actual_credits": round(float(row[8]), 2),
                "credits": round(float(row[9]), 2),
                "duration_seconds": round(float(row[10]), 1),
            }

        totals = _row_dict(self.db.execute(select(*sums).where(in_period)).one())
        totals["cache_hit_ratio"] = round(totals["cached_input_tokens"] / totals["input_tokens"], 4) if totals["input_tokens"] else None
        totals["failure_rate"] = round(totals["failed"] / totals["executions"], 4) if totals["executions"] else None
        totals["members_active"] = int(
            self.db.execute(select(func.count(func.distinct(R.user_id))).where(in_period, R.user_id != 0)).scalar() or 0
        )

        daily_rows = self.db.execute(select(R.day, *sums).where(in_period).group_by(R.day).order_by(R.day)).all()
        by_day = {row[0]: _row_dict(row[1:]) for row in daily_rows}
        daily = []
        for offset in range(max(1, int(days))):
            day = start + timedelta(days=offset)
            entry = by_day.get(day) or _row_dict((0,) * 11)
            daily.append({"day": day.isoformat(), **entry})

        def _breakdown(column, *, label: str, limit: int | None = None) -> list[dict[str, Any]]:
            stmt = select(column, *sums).where(in_period).group_by(column).order_by(func.sum(R.credits).desc(), func.sum(R.executions).desc())
            if limit:
                stmt = stmt.limit(limit)
            return [{label: row[0] or "", **_row_dict(row[1:])} for row in self.db.execute(stmt).all()]

        by_kind = _breakdown(R.kind, label="kind")
        by_model = _breakdown(R.model, label="model", limit=12)
        by_runtime = _breakdown(R.runtime_type, label="runtime_type")

        member_rows = self.db.execute(
            select(R.user_id, User.username, User.nickname, *sums)
            .select_from(R)
            .outerjoin(User, User.id == R.user_id)
            .where(in_period, R.user_id != 0)
            .group_by(R.user_id, User.username, User.nickname)
            .order_by(func.sum(R.credits).desc(), func.sum(R.executions).desc())
            .limit(10)
        ).all()
        top_members = [
            {
                "user_id": int(row[0]),
                "username": row[1] or "",
                "display_name": (row[2] or row[1] or f"User {row[0]}"),
                **_row_dict(row[3:]),
            }
            for row in member_rows
        ]

        agent_rows = self.db.execute(
            select(R.agent_id, Agent.name, Agent.agent_type, *sums)
            .select_from(R)
            .outerjoin(Agent, Agent.id == R.agent_id)
            .where(in_period, R.agent_id != "")
            .group_by(R.agent_id, Agent.name, Agent.agent_type)
            .order_by(func.sum(R.credits).desc(), func.sum(R.executions).desc())
            .limit(10)
        ).all()
        top_assistants = [
            {"agent_id": row[0], "name": row[1] or row[0], "agent_type": row[2] or "", **_row_dict(row[3:])}
            for row in agent_rows
        ]

        return {
            "period_days": int(days),
            "period_start": start.isoformat(),
            "period_end": today.isoformat(),
            "totals": totals,
            "daily": daily,
            "by_kind": by_kind,
            "by_model": by_model,
            "by_runtime": by_runtime,
            "top_members": top_members,
            "top_assistants": top_assistants,
        }

    def daily_rows(self, days: int, *, today: date | None = None) -> list[dict[str, Any]]:
        """Every rollup row of the period, for the CSV export."""
        today = today or datetime.utcnow().date()
        start = self.period_start(days, today=today)
        R = UsageDailyRollup
        rows = self.db.execute(
            select(R, User.username)
            .select_from(R)
            .outerjoin(User, User.id == R.user_id)
            .where(R.day >= start, R.day <= today)
            .order_by(R.day, R.user_id, R.kind, R.model)
        ).all()
        out = []
        for rollup, username in rows:
            out.append(
                {
                    "day": rollup.day.isoformat(),
                    "user_id": rollup.user_id or "",
                    "username": username or "",
                    "agent_id": rollup.agent_id,
                    "runtime_type": rollup.runtime_type,
                    "kind": rollup.kind,
                    "model": rollup.model,
                    "executions": rollup.executions,
                    "succeeded": rollup.succeeded,
                    "failed": rollup.failed,
                    "input_tokens": rollup.input_tokens,
                    "output_tokens": rollup.output_tokens,
                    "cached_input_tokens": rollup.cached_input_tokens,
                    "reasoning_tokens": rollup.reasoning_tokens,
                    "estimated_credits": rollup.estimated_credits,
                    "actual_credits": rollup.actual_credits,
                    "credits": rollup.credits,
                    "duration_seconds": rollup.duration_seconds,
                }
            )
        return out

    def execution_rows(self, days: int, *, today: date | None = None, limit: int = 50000) -> list[dict[str, Any]]:
        """The executions of the period, one dict each, for the CSV export (newest first, capped)."""
        today = today or datetime.utcnow().date()
        start_day = self.period_start(days, today=today)
        start = datetime(start_day.year, start_day.month, start_day.day)
        rows = self.db.execute(
            select(AgentExecution, User.username, Agent.name)
            .select_from(AgentExecution)
            .outerjoin(User, User.id == AgentExecution.created_by_user_id)
            .outerjoin(Agent, Agent.id == AgentExecution.agent_id)
            .where(AgentExecution.created_at >= start)
            .order_by(AgentExecution.created_at.desc())
            .limit(limit)
        ).all()
        out = []
        for execution, username, agent_name in rows:
            duration = ""
            if execution.started_at and execution.finished_at and execution.finished_at >= execution.started_at:
                duration = round((execution.finished_at - execution.started_at).total_seconds(), 1)
            credits = execution.actual_credits if execution.actual_credits is not None else execution.estimated_credits
            out.append(
                {
                    "created_at": execution.created_at.isoformat(sep=" ", timespec="seconds") if execution.created_at else "",
                    "execution_id": execution.id,
                    "username": username or "",
                    "user_id": execution.created_by_user_id or "",
                    "agent": agent_name or execution.agent_id,
                    "runtime_type": execution.runtime_type or "",
                    "kind": execution.kind,
                    "status": execution.status,
                    "terminal_reason": execution.terminal_reason or "",
                    "model": execution.model or "",
                    "input_tokens": execution.input_tokens if execution.input_tokens is not None else "",
                    "output_tokens": execution.output_tokens if execution.output_tokens is not None else "",
                    "cached_input_tokens": execution.cached_input_tokens if execution.cached_input_tokens is not None else "",
                    "reasoning_tokens": execution.reasoning_tokens if execution.reasoning_tokens is not None else "",
                    "estimated_credits": execution.estimated_credits if execution.estimated_credits is not None else "",
                    "actual_credits": execution.actual_credits if execution.actual_credits is not None else "",
                    "credits": credits if credits is not None else "",
                    "credits_source": execution.credits_source or "",
                    "duration_seconds": duration,
                    "session_id": execution.session_id or "",
                    "task_id": execution.task_id or "",
                }
            )
        return out
