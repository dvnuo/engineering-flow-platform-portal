"""Is an assistant in the middle of something right now?

Used when a member saves a connector: an idle assistant is restarted at once so
it picks the new settings up, a busy one is left alone and shown as "restart to
apply" instead of having its work cut off.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.agent_execution import AgentExecution
from app.repositories.agent_execution_repo import ACTIVE_EXECUTION_STATUSES
from app.repositories.agent_task_repo import AgentTaskRepository

# A chat or task execution that has not reported for this long is treated as
# abandoned (a Portal restart mid-stream never records the finish), so it does
# not keep an assistant "busy" forever.
ACTIVE_EXECUTION_STALE_AFTER = timedelta(hours=2)


def agent_is_busy(db: Session, agent_id: str, *, now: datetime | None = None) -> bool:
    if AgentTaskRepository(db).has_active_task(agent_id):
        return True
    cutoff = (now or datetime.utcnow()) - ACTIVE_EXECUTION_STALE_AFTER
    stmt = (
        select(AgentExecution.id)
        .where(
            and_(
                AgentExecution.agent_id == agent_id,
                AgentExecution.status.in_(ACTIVE_EXECUTION_STATUSES),
                AgentExecution.updated_at >= cutoff,
            )
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None



def profile_restart_pending(agent, profile_revision: int | None) -> bool:
    """True when a running assistant still carries older connector settings.

    ``profile_revision_applied`` is recorded whenever the Portal (re)starts the
    pod; None means unknown and never shows a prompt.
    """

    if (getattr(agent, "status", "") or "").lower() != "running":
        return False
    applied = getattr(agent, "profile_revision_applied", None)
    if applied is None or profile_revision is None:
        return False
    return int(applied) < int(profile_revision)


__all__ = ["ACTIVE_EXECUTION_STALE_AFTER", "agent_is_busy", "profile_restart_pending"]
