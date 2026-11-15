"""What an app was used for: the figures of its usage page and of the administration's list of apps.

Three sources: ``app_traffic_daily`` and ``app_visits`` (what the gateway
counted and the host reported), ``app_executions`` (the SDK calls) and the
host's last word on the app's process (app/services/apps/host_state.py).

Days are UTC days. The host reports a member's requests as one running total
(``app_visits.visits`` is all-time), so the per-member list names the members
who used the app within the period with their all-time request count.

Visitors are signed-in members, everywhere: a day's ``visitors`` is the number
of distinct signed-in members the gateway saw that day (docs/CONTRACTS.md,
section 5.4; a visitor of an ``anyone`` app who is not signed in is counted in
``requests`` only), and a period's is the number of distinct signed-in members
seen in it, see ``members_in_period``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.app import App, AppExecution, AppTrafficDaily, AppVisit
from app.models.user import User
from app.services.apps import budget as app_budget
from app.services.apps.access import display_name_of
from app.services.apps.executions import STATUS_FAILED
from app.services.apps.host_state import host_state

DEFAULT_DAYS = 30
MAX_DAYS = 90
TOP_VISITORS = 50
ADMIN_METRIC_DAYS = 7


def _period(days: int, today: Optional[date]) -> tuple[date, date, datetime, datetime]:
    """``(first day, last day, start, end)`` of the last ``days`` UTC days, today included."""
    last = today or app_budget.utcnow().date()
    first = last - timedelta(days=max(1, int(days)) - 1)
    start = datetime(first.year, first.month, first.day)
    end = datetime(last.year, last.month, last.day) + timedelta(days=1)
    return first, last, start, end


def _as_day(value: Any) -> Optional[date]:
    """The day a SQL ``date()`` gave back: a date on some databases, ISO text on SQLite."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def members_in_period(members_seen: int, daily_visitors: Iterable[int]) -> int:
    """How many distinct signed-in members used an app in a period.

    ``members_seen`` is the count of ``app_visits`` rows whose last visit
    falls in the period: a member is in the period exactly when their last
    visit is. The days' own counts are of the same members, a day at a time,
    so the busiest day can never be more than the period's figure; it is used
    only as a floor, for a host report whose member list was cut short or
    came without times.
    """
    return max(int(members_seen), max((int(value or 0) for value in daily_visitors), default=0))


def _failed():
    return func.coalesce(func.sum(case((AppExecution.status == STATUS_FAILED, 1), else_=0)), 0)


def process_of(app_id: str) -> Optional[dict[str, Any]]:
    """The app's process as the host last reported it; None when the host has said nothing about it."""
    view = host_state.status_of(app_id)
    if view.status is None:
        return None
    status = view.status

    def number(name: str) -> Optional[float]:
        value = status.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
            return None
        return float(value)

    def whole(name: str) -> Optional[int]:
        value = number(name)
        return int(value) if value is not None else None

    return {
        "state": str(status.get("state")) if status.get("state") else None,
        "rss_bytes": whole("rss_bytes"),
        "cpu_percent": number("cpu_percent"),
        "data_bytes": whole("data_bytes"),
        "restarts": whole("restarts"),
        "reported_at": view.reported_at,
    }


def app_usage(db: Session, app: App, days: int = DEFAULT_DAYS, *, today: Optional[date] = None) -> dict[str, Any]:
    """The usage page of one app over the last ``days`` days: every day present, oldest first."""
    days = max(1, min(int(days), MAX_DAYS))
    first, last, start, end = _period(days, today)
    all_days = [first + timedelta(days=offset) for offset in range(days)]

    traffic_rows = {
        row.day: row
        for row in db.scalars(
            select(AppTrafficDaily).where(AppTrafficDaily.app_id == app.id, AppTrafficDaily.day >= first, AppTrafficDaily.day <= last)
        ).all()
    }
    traffic = []
    for day in all_days:
        row = traffic_rows.get(day)
        traffic.append(
            {
                "day": day.isoformat(),
                "requests": int(row.requests or 0) if row else 0,
                "visitors": int(row.visitors or 0) if row else 0,
                "errors_4xx": int(row.errors_4xx or 0) if row else 0,
                "errors_5xx": int(row.errors_5xx or 0) if row else 0,
                "p95_ms": int(row.p95_ms or 0) if row else 0,
            }
        )

    in_period = (AppExecution.app_id == app.id, AppExecution.created_at >= start, AppExecution.created_at < end)
    day_of = func.date(AppExecution.created_at)
    llm_rows = db.execute(
        select(
            day_of,
            func.count(AppExecution.id),
            _failed(),
            func.coalesce(func.sum(AppExecution.input_tokens), 0),
            func.coalesce(func.sum(AppExecution.output_tokens), 0),
            func.coalesce(func.sum(AppExecution.credits), 0.0),
        )
        .where(*in_period, AppExecution.kind == app_budget.KIND_LLM)
        .group_by(day_of)
    ).all()
    llm_by_day = {_as_day(row[0]): row for row in llm_rows}
    llm = []
    for day in all_days:
        row = llm_by_day.get(day)
        llm.append(
            {
                "day": day.isoformat(),
                "calls": int(row[1]) if row else 0,
                "failed": int(row[2]) if row else 0,
                "input_tokens": int(row[3]) if row else 0,
                "output_tokens": int(row[4]) if row else 0,
                "credits": round(float(row[5]), 6) if row else 0.0,
            }
        )

    connector_rows = db.execute(
        select(AppExecution.connector_type, func.count(AppExecution.id), _failed())
        .where(*in_period, AppExecution.kind == app_budget.KIND_CONNECTOR)
        .group_by(AppExecution.connector_type)
    ).all()
    connector_counts = {str(kind or ""): (int(calls), int(failed)) for kind, calls, failed in connector_rows}
    # A connector the app may call is listed even before its first call.
    for kind in app.connectors_enabled or []:
        connector_counts.setdefault(str(kind), (0, 0))
    connectors = [
        {"type": kind, "calls": calls, "failed": failed}
        for kind, (calls, failed) in sorted(connector_counts.items(), key=lambda item: (-item[1][0], item[0]))
        if kind
    ]

    visit_rows = db.execute(
        select(AppVisit, User)
        .select_from(AppVisit)
        .outerjoin(User, User.id == AppVisit.user_id)
        .where(AppVisit.app_id == app.id, AppVisit.last_visited_at >= start, AppVisit.last_visited_at < end)
        .order_by(AppVisit.visits.desc(), AppVisit.last_visited_at.desc())
        .limit(TOP_VISITORS)
    ).all()
    visitors = [
        {
            "user": {
                "id": int(visit.user_id),
                "username": str(user.username) if user is not None else "",
                "display_name": display_name_of(user) if user is not None else "",
            },
            "requests": int(visit.visits or 0),
            "last_visited_at": visit.last_visited_at,
        }
        for visit, user in visit_rows
    ]
    members_seen = int(
        db.scalar(
            select(func.count(AppVisit.id)).where(
                AppVisit.app_id == app.id, AppVisit.last_visited_at >= start, AppVisit.last_visited_at < end
            )
        )
        or 0
    )

    return {
        "days": days,
        "traffic": traffic,
        "llm": llm,
        "connectors": connectors,
        "visitors": visitors,
        "budget": app_budget.snapshot(db, app),
        "totals": {
            "requests": sum(item["requests"] for item in traffic),
            # Distinct signed-in members in the period.
            "visitors": members_in_period(members_seen, (item["visitors"] for item in traffic)),
            "llm_calls": sum(item["calls"] for item in llm),
            "credits": round(sum(item["credits"] for item in llm), 6),
            "connector_calls": sum(item["calls"] for item in connectors),
        },
        "process": process_of(app.id),
    }


def admin_metrics(db: Session, app_ids: Iterable[str], *, days: int = ADMIN_METRIC_DAYS, today: Optional[date] = None) -> dict[str, dict[str, Any]]:
    """``{app id: metrics}`` for the administration's list: the last seven days and the process's size, a few queries for all apps."""
    ids = [str(app_id) for app_id in app_ids]
    metrics: dict[str, dict[str, Any]] = {
        app_id: {
            "requests_7d": 0,
            "visitors_7d": 0,
            "llm_calls_7d": 0,
            "credits_7d": 0.0,
            "rss_bytes": None,
            "data_bytes": None,
        }
        for app_id in ids
    }
    if not ids:
        return metrics
    first, last, start, end = _period(days, today)

    for app_id, requests, busiest in db.execute(
        select(AppTrafficDaily.app_id, func.coalesce(func.sum(AppTrafficDaily.requests), 0), func.coalesce(func.max(AppTrafficDaily.visitors), 0))
        .where(AppTrafficDaily.day >= first, AppTrafficDaily.day <= last)
        .group_by(AppTrafficDaily.app_id)
    ).all():
        if app_id in metrics:
            metrics[app_id]["requests_7d"] = int(requests)
            metrics[app_id]["visitors_7d"] = int(busiest)

    for app_id, members in db.execute(
        select(AppVisit.app_id, func.count(AppVisit.id))
        .where(AppVisit.last_visited_at >= start, AppVisit.last_visited_at < end)
        .group_by(AppVisit.app_id)
    ).all():
        if app_id in metrics:
            # Distinct signed-in members in the week (the busiest day, taken above, is only a floor).
            metrics[app_id]["visitors_7d"] = members_in_period(int(members), [metrics[app_id]["visitors_7d"]])

    for app_id, calls, credits in db.execute(
        select(AppExecution.app_id, func.count(AppExecution.id), func.coalesce(func.sum(AppExecution.credits), 0.0))
        .where(AppExecution.kind == app_budget.KIND_LLM, AppExecution.created_at >= start, AppExecution.created_at < end)
        .group_by(AppExecution.app_id)
    ).all():
        if app_id in metrics:
            metrics[app_id]["llm_calls_7d"] = int(calls)
            metrics[app_id]["credits_7d"] = round(float(credits or 0.0), 6)

    for app_id in ids:
        process = process_of(app_id)
        if process is not None:
            metrics[app_id]["rss_bytes"] = process["rss_bytes"]
            metrics[app_id]["data_bytes"] = process["data_bytes"]
    return metrics
