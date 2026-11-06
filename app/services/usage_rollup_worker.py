"""Background worker that keeps the daily usage rollups current and prunes old executions.

Every ``usage_rollup_worker_interval_seconds`` (an hour by default) it
recomputes the last ``usage_rollup_window_days`` days of
``usage_daily_rollups`` from ``agent_executions`` and then deletes terminal
executions older than ``execution_retention_days`` (365; 0 keeps them all)
after rolling their days up, so the Usage panel's sums survive the rows. The
panel also refreshes today and yesterday on demand, so a run that finished a
minute ago shows without waiting for this loop.
"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.db import SessionLocal
from app.services.usage_rollup_service import DEFAULT_RETENTION_DAYS, DEFAULT_ROLLUP_WINDOW_DAYS, UsageRollupService

logger = logging.getLogger(__name__)


def _non_negative_int(value, *, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(0, normalized)


class UsageRollupWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="usage-rollup-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _interval_seconds(self) -> int:
        return max(60, _non_negative_int(getattr(self.settings, "usage_rollup_worker_interval_seconds", 3600), default=3600))

    def _run_loop(self) -> None:
        initial_delay = _non_negative_int(getattr(self.settings, "usage_rollup_worker_initial_delay_seconds", 60), default=60)
        if initial_delay and self._stop_event.wait(initial_delay):
            return
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive a bad iteration
                logger.exception("usage rollup worker iteration failed")
            if self._stop_event.wait(self._interval_seconds()):
                return

    def run_once(self) -> dict:
        """One pass: roll the recent window up, then prune; returns what it did."""
        window = max(1, _non_negative_int(getattr(self.settings, "usage_rollup_window_days", DEFAULT_ROLLUP_WINDOW_DAYS), default=DEFAULT_ROLLUP_WINDOW_DAYS))
        retention = _non_negative_int(getattr(self.settings, "execution_retention_days", DEFAULT_RETENTION_DAYS), default=DEFAULT_RETENTION_DAYS)
        db = SessionLocal()
        try:
            service = UsageRollupService(db)
            rolled = service.rollup_recent(window)
            pruned = service.prune_executions(retention)
            logger.info("usage rollups: refreshed %s days, pruned %s executions", len(rolled), pruned.get("deleted", 0))
            return {"rolled_up": rolled, **pruned}
        finally:
            db.close()


usage_rollup_worker_singleton = UsageRollupWorker()
