"""What an app may spend of its owner's model provider in a day, and what it has spent.

The budget is per app and per UTC day. The limits are the app's own figures
(``apps.daily_credits``, ``apps.daily_tokens``, set by a system administrator)
or the platform's (``APPS_DEFAULT_DAILY_CREDITS``, ``APPS_DEFAULT_DAILY_TOKENS``).
What was spent is summed from the day's ``app_executions``: the credits the
provider reported (a provider that reports none spends none of them) and the
tokens, input plus output. Connector requests cost nothing here.

A call is refused once either figure has reached its limit; the call that
crosses a limit still goes through, since what it costs is known only
afterwards. A limit of zero therefore refuses every call.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.app import App, AppExecution
from app.services.apps import settings as apps_settings

KIND_LLM = "llm"
KIND_CONNECTOR = "connector"


def utcnow() -> datetime:
    """The current UTC time; a function of its own so a test can move the clock."""
    return datetime.utcnow()


def day_start(moment: Optional[datetime] = None) -> datetime:
    """Midnight (UTC) of the day ``moment`` is in."""
    now = moment or utcnow()
    return datetime(now.year, now.month, now.day)


def seconds_to_reset(moment: Optional[datetime] = None) -> int:
    """Whole seconds until the next UTC midnight, at least one."""
    now = moment or utcnow()
    remaining = (day_start(now) + timedelta(days=1) - now).total_seconds()
    return max(1, int(remaining) + (0 if remaining == int(remaining) else 1))


def limits(app: App) -> tuple[float, int]:
    """``(daily credits, daily tokens)`` in force for the app."""
    credits = app.daily_credits if app.daily_credits is not None else apps_settings.default_daily_credits()
    tokens = app.daily_tokens if app.daily_tokens is not None else apps_settings.default_daily_tokens()
    return max(0.0, float(credits)), max(0, int(tokens))


def used(db: Session, app_id: str, moment: Optional[datetime] = None) -> tuple[float, int]:
    """``(credits, tokens)`` the app's model calls have used since the last UTC midnight."""
    start = day_start(moment)
    row = db.execute(
        select(
            func.coalesce(func.sum(AppExecution.credits), 0.0),
            func.coalesce(func.sum(AppExecution.input_tokens + AppExecution.output_tokens), 0),
        ).where(
            AppExecution.app_id == app_id,
            AppExecution.kind == KIND_LLM,
            AppExecution.created_at >= start,
            AppExecution.created_at < start + timedelta(days=1),
        )
    ).one()
    return float(row[0] or 0.0), int(row[1] or 0)


def snapshot(db: Session, app: App, moment: Optional[datetime] = None) -> dict[str, Any]:
    """The budget object of docs/CONTRACTS.md section 9.1 (and of the usage page)."""
    daily_credits, daily_tokens = limits(app)
    used_credits, used_tokens = used(db, app.id, moment)
    return {
        "daily_credits": daily_credits,
        "used_credits": round(used_credits, 6),
        "daily_tokens": daily_tokens,
        "used_tokens": used_tokens,
    }


def exceeded(budget: dict[str, Any]) -> Optional[str]:
    """``"credits"`` or ``"tokens"`` when that limit has been reached, else None."""
    if float(budget["used_credits"]) >= float(budget["daily_credits"]):
        return "credits"
    if int(budget["used_tokens"]) >= int(budget["daily_tokens"]):
        return "tokens"
    return None
