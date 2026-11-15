"""Background worker that removes the data of apps deleted long enough ago.

Deleting an app takes it off the host and, unless its owner asked otherwise,
leaves its data there: a deletion by mistake can be undone by hand for a
while. Once an app has been deleted for ``APPS_DELETED_RETENTION_DAYS`` (30;
0 keeps the data for good) this worker asks the host to remove the app with
its data (``DELETE /v1/apps/{id}?purge_data=true``, which the host accepts
for an app it no longer runs) and stamps ``apps.data_purged_at``. A host
that cannot be reached, or that refuses, is asked again on the next round.
The app's row stays: its address is never given to another app.

Started with the Portal when ``APPS_ENABLED`` is on
(app/services/apps/member_hooks.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import threading
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.models.app import App
from app.services.apps import audit
from app.services.apps import settings as apps_settings
from app.services.apps.errors import AppHostError
from app.services.apps.host_client import HOST_APP_NOT_FOUND, AppHostClient

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 300
INTERVAL_SECONDS = 3600
BATCH_SIZE = 200


class AppsPurgeWorker:
    """``session_factory`` and ``host_factory`` make the database session and the host client (tests pass fakes)."""

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        *,
        host_factory: Optional[Callable[[], Any]] = None,
        initial_delay_seconds: float = INITIAL_DELAY_SECONDS,
        interval_seconds: float = INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._host_factory = host_factory or AppHostClient
        self._initial_delay = initial_delay_seconds
        self._interval = interval_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="apps-purge-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _run_loop(self) -> None:
        if self._initial_delay and self._stop_event.wait(self._initial_delay):
            return
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive a bad iteration
                logger.exception("apps purge worker iteration failed")
            if self._stop_event.wait(max(60.0, float(self._interval))):
                return

    def _session(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()
        from app.db import SessionLocal

        return SessionLocal()

    def run_once(self, *, now: Optional[datetime] = None) -> dict[str, int]:
        """One pass: purge what is due; ``{"due", "purged", "failed"}``."""
        summary = {"due": 0, "purged": 0, "failed": 0}
        if not apps_settings.apps_enabled():
            return summary
        retention = apps_settings.deleted_retention_days()
        if retention <= 0:
            return summary
        moment = now or datetime.utcnow()
        cutoff = moment - timedelta(days=retention)
        db = self._session()
        try:
            due = list(
                db.scalars(
                    select(App)
                    .where(App.deleted_at.is_not(None), App.deleted_at <= cutoff, App.data_purged_at.is_(None))
                    .order_by(App.deleted_at.asc())
                    .limit(BATCH_SIZE)
                ).all()
            )
            summary["due"] = len(due)
            if not due:
                return summary
            host = self._host_factory()
            for app in due:
                app_id = app.id
                try:
                    host.delete_app(app_id, purge_data=True)
                except AppHostError as exc:
                    if exc.host_code != HOST_APP_NOT_FOUND:
                        # Unreachable, or it refused: the data is still there, so nothing is stamped.
                        summary["failed"] += 1
                        logger.info("apps: the data of a deleted app could not be removed app_id=%s code=%s", app_id, exc.code)
                        continue
                except Exception:  # noqa: BLE001 - one app must not stop the others
                    summary["failed"] += 1
                    logger.warning("apps: removing the data of a deleted app failed app_id=%s", app_id, exc_info=True)
                    continue
                app.data_purged_at = moment
                db.add(app)
                db.commit()
                audit.record_app_event(db, app_id, audit.ACTION_DATA_PURGED, detail={"deleted_days": retention})
                summary["purged"] += 1
            if summary["purged"] or summary["failed"]:
                logger.info("apps: data of deleted apps: purged=%s failed=%s", summary["purged"], summary["failed"])
            return summary
        finally:
            db.close()


apps_purge_worker_singleton = AppsPurgeWorker()
