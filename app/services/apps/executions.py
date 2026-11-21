"""``app_executions``: one row per SDK call an app was allowed to make.

A model completion (``kind = "llm"``) or a connector request (``kind =
"connector"``), with what it used, how it ended, where it came from and which
member the app said caused it. The daily budget is counted from these rows
(app/services/apps/budget.py), the app's usage page reads them
(app/services/apps/usage.py) and the daily rollups fold the model calls into
the Portal's usage reporting (app/services/usage_rollup_service.py).

Statuses: ``succeeded``; ``failed`` with the error code of docs/CONTRACTS.md
section 9; ``cancelled`` for a stream the caller abandoned.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.app import AppExecution
from app.services.apps import budget

logger = logging.getLogger(__name__)

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
ERROR_CLIENT_CLOSED = "client_closed"

# A rough figure for text whose token count the provider never reported (a
# stream that did not reach its end): about four characters to a token.
CHARS_PER_TOKEN = 4


def estimate_tokens(characters: int) -> int:
    """About how many tokens ``characters`` of text are."""
    return int(math.ceil(max(0, int(characters)) / CHARS_PER_TOKEN))


def _count(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _credits(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def record_execution(
    db: Session,
    *,
    app_id: str,
    owner_user_id: int,
    kind: str,
    status: str,
    visitor_user_id: Optional[int] = None,
    model: Optional[str] = None,
    connector_type: Optional[str] = None,
    input_tokens: Any = 0,
    output_tokens: Any = 0,
    credits: Any = None,
    error_code: Optional[str] = None,
    source: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> Optional[AppExecution]:
    """Add and commit one row. Never raises: a call that happened is not failed by its bookkeeping."""
    try:
        row = AppExecution(
            app_id=str(app_id),
            owner_user_id=int(owner_user_id),
            visitor_user_id=int(visitor_user_id) if visitor_user_id is not None else None,
            kind=str(kind)[:16],
            model=str(model)[:128] if model else None,
            connector_type=str(connector_type)[:32] if connector_type else None,
            input_tokens=_count(input_tokens),
            output_tokens=_count(output_tokens),
            credits=_credits(credits),
            status=str(status)[:16],
            error_code=str(error_code)[:64] if error_code else None,
            source=str(source)[:16] if source else None,
            duration_ms=_count(duration_ms) if duration_ms is not None else None,
            created_at=budget.utcnow(),
        )
        db.add(row)
        db.commit()
        return row
    except Exception:  # noqa: BLE001
        logger.warning("apps: recording an SDK call failed app_id=%s kind=%s", app_id, kind, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
