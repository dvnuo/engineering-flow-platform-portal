"""Throttled agent activity tracking that drives idle auto-stop.

The Portal proxy is the single choke point for all user traffic to an agent
(chat, files, everything), so bumping ``agents.last_activity_at`` there is the
most reliable idle signal. Writes are throttled per-process and best-effort so
they can never slow down or break a request.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from sqlalchemy import update

from app.db import SessionLocal
from app.models.agent import Agent

logger = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 60.0
_lock = threading.Lock()
_last_bump: dict[str, float] = {}


def touch_agent_activity(agent_id: str) -> None:
    """Record that ``agent_id`` was just used (throttled, best-effort)."""
    if not agent_id:
        return
    now = time.monotonic()
    with _lock:
        last = _last_bump.get(agent_id, 0.0)
        if now - last < _MIN_INTERVAL_SECONDS:
            return
        _last_bump[agent_id] = now
    try:
        db = SessionLocal()
        try:
            db.execute(
                update(Agent).where(Agent.id == agent_id).values(last_activity_at=datetime.utcnow())
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.debug("failed to record agent activity for %s", agent_id, exc_info=True)


def reset_throttle_cache() -> None:
    """Test helper: clear the per-process throttle cache."""
    with _lock:
        _last_bump.clear()
