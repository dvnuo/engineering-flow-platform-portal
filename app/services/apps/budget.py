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

A call that has not ended yet has no row. Until it has one it is counted
here, in memory, as the tokens it may come to (``in_flight``): calls that
start together would otherwise all read the same spent figure and all go
through, however little of the day's tokens is left. Credits cannot be
counted ahead (what a call costs in credits depends on the model and is
known only from the provider's answer), so the tokens are what holds calls
in flight back.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import threading
import time
from typing import Any, Callable, Optional

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


# --- calls in flight -----------------------------------------------------------------

# A call that names no ``max_output_tokens`` is counted as an answer of this
# many tokens until its row says what it really used: a few pages of text.
# The model may write more than that; a call that says how long its answer
# may be is counted as exactly what it says.
RESERVED_OUTPUT_TOKENS = 4_096
# A reservation nobody gave back (a request torn down before its answer began
# to be sent) is forgotten after this long, so that it cannot hold the app's
# budget for the rest of the day. No call is in flight for an hour.
RESERVATION_MAX_SECONDS = 3600.0
# What a caller is told to wait when the calls in flight take up what is
# left: long enough for some of them to end, short enough to try again.
IN_FLIGHT_RETRY_SECONDS = 5


def reservation_tokens(input_tokens: Any, max_output_tokens: Any) -> int:
    """What a call in flight is counted as: its request, and the answer it asked for (``RESERVED_OUTPUT_TOKENS`` when it named none)."""
    named = isinstance(max_output_tokens, int) and not isinstance(max_output_tokens, bool) and max_output_tokens > 0
    try:
        request = max(0, int(input_tokens or 0))
    except (TypeError, ValueError):
        request = 0
    return request + (int(max_output_tokens) if named else RESERVED_OUTPUT_TOKENS)


class Reservation:
    """The tokens one call in flight is counted as. ``release`` gives them back, once."""

    __slots__ = ("app_id", "tokens", "_ledger", "expires_at")

    def __init__(self, ledger: "InFlightLedger", app_id: str, tokens: int, expires_at: float) -> None:
        self._ledger = ledger
        self.app_id = app_id
        self.tokens = tokens
        self.expires_at = expires_at

    def release(self) -> None:
        self._ledger.release(self)


class InFlightLedger:
    """Per app, what the model calls that have not ended yet are counted as.

    In memory, like the rate limiter (app/services/apps/rate_limit.py): the
    Portal is one process, and a restart only ever makes the budget more
    generous for a moment.
    """

    def __init__(self, *, max_seconds: float = RESERVATION_MAX_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_seconds = float(max_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._held: dict[str, list[Reservation]] = {}

    def reserve(self, app_id: str, tokens: int) -> Reservation:
        """Count a call that is about to be made; the caller gives the reservation back when the call's row is written."""
        reservation = Reservation(self, str(app_id), max(0, int(tokens)), self._clock() + self._max_seconds)
        with self._lock:
            self._held.setdefault(reservation.app_id, []).append(reservation)
        return reservation

    def release(self, reservation: Reservation) -> None:
        with self._lock:
            held = self._held.get(reservation.app_id)
            if not held:
                return
            # By identity: two calls of one app may be counted as the same figure.
            self._held[reservation.app_id] = [item for item in held if item is not reservation]
            if not self._held[reservation.app_id]:
                self._held.pop(reservation.app_id, None)

    def tokens(self, app_id: str, *, before: Optional[Reservation] = None) -> int:
        """The tokens the app's calls in flight are counted as; with ``before``, only those reserved earlier than it.

        A call asks about the ones ahead of it. Were it to count every
        other call, two that arrive together with room for one would each
        see the other and both be refused, and again when they came back.
        """
        now = self._clock()
        with self._lock:
            held = [item for item in self._held.get(str(app_id), []) if item.expires_at > now]
            if held:
                self._held[str(app_id)] = held
            else:
                self._held.pop(str(app_id), None)
            total = 0
            # The list is in the order the reservations were made.
            for item in held:
                if item is before:
                    break
                total += item.tokens
            return total

    def calls(self, app_id: str) -> int:
        """How many of the app's calls are counted at this moment."""
        now = self._clock()
        with self._lock:
            return sum(1 for item in self._held.get(str(app_id), []) if item.expires_at > now)

    def reset(self) -> None:
        with self._lock:
            self._held.clear()


in_flight = InFlightLedger()


def crowded(budget: dict[str, Any], in_flight_tokens: int) -> bool:
    """Whether the calls already in flight take up what is left of the day's tokens.

    The same rule as ``exceeded``, with those calls counted as spent: a
    call goes through while what is spent and what is under way are below
    the limit, whatever it turns out to cost itself.
    """
    pending = max(0, int(in_flight_tokens or 0))
    return pending > 0 and int(budget["used_tokens"]) + pending >= int(budget["daily_tokens"])
