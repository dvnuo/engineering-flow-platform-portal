"""What the app host last said about its apps, kept in memory for the pages.

Two sources feed it: the metrics the host posts every few minutes
(``POST /api/app-host/v1/metrics/ingest`` carries every app's status and the
host object) and the Portal's own ``GET /v1/apps`` when a page asks for
something fresher. A list of apps never calls the host once per app: it reads
this, refreshed at most every few seconds, and falls back to the last report
when the host does not answer. Nothing here survives a Portal restart; the
next report or the next page fills it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import threading
import time
from typing import Any, Callable, Optional

from app.services.apps.errors import AppHostError

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 5.0
# After a failed refresh the host is left alone this long, so a host that is
# down costs a page one timeout rather than one per request.
FAILURE_BACKOFF_SECONDS = 20.0


@dataclass(frozen=True)
class StatusView:
    """A status object (docs/CONTRACTS.md, section 4.6) and how fresh it is."""

    status: Optional[dict]
    reported_at: Optional[datetime]
    live: bool


class HostState:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._statuses: dict[str, tuple[dict, datetime]] = {}
        self._host: Optional[dict] = None
        self._host_reported_at: Optional[datetime] = None
        self._refreshed_at: Optional[float] = None
        self._failed_at: Optional[float] = None

    # ------------------------------------------------------------------ writing

    def record_statuses(self, statuses: Any, *, replace: bool = False) -> None:
        """Remember status objects; ``replace`` drops apps the host no longer lists."""
        now = datetime.utcnow()
        fresh: dict[str, tuple[dict, datetime]] = {}
        for item in statuses if isinstance(statuses, list) else []:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                fresh[str(item["id"])] = (dict(item), now)
        with self._lock:
            if replace:
                self._statuses = fresh
            else:
                self._statuses.update(fresh)

    def record_status(self, status: Any) -> None:
        if isinstance(status, dict):
            self.record_statuses([status])

    def record_host(self, host: Any) -> None:
        if not isinstance(host, dict):
            return
        with self._lock:
            self._host = dict(host)
            self._host_reported_at = datetime.utcnow()

    def forget(self, app_id: str) -> None:
        with self._lock:
            self._statuses.pop(str(app_id), None)

    def clear(self) -> None:
        with self._lock:
            self._statuses.clear()
            self._host = None
            self._host_reported_at = None
            self._refreshed_at = None
            self._failed_at = None

    # ------------------------------------------------------------------ reading

    def status_of(self, app_id: str) -> StatusView:
        with self._lock:
            entry = self._statuses.get(str(app_id))
        if entry is None:
            return StatusView(status=None, reported_at=None, live=False)
        return StatusView(status=dict(entry[0]), reported_at=entry[1], live=False)

    def host(self) -> tuple[Optional[dict], Optional[datetime]]:
        with self._lock:
            return (dict(self._host) if self._host is not None else None), self._host_reported_at

    def refresh(self, client: Any, *, max_age_seconds: float = REFRESH_SECONDS) -> bool:
        """Ask the host for every app's status unless that happened a moment ago; whether what is held is fresh."""
        now = self._clock()
        with self._lock:
            if self._refreshed_at is not None and now - self._refreshed_at < max_age_seconds:
                return True
            if self._failed_at is not None and now - self._failed_at < FAILURE_BACKOFF_SECONDS:
                return False
        try:
            statuses = client.list_apps()
        except AppHostError:
            with self._lock:
                self._failed_at = self._clock()
            return False
        except Exception:  # noqa: BLE001 - a page must render whatever the host does
            logger.warning("apps: reading the host's app list failed", exc_info=True)
            with self._lock:
                self._failed_at = self._clock()
            return False
        self.record_statuses(statuses, replace=True)
        with self._lock:
            self._refreshed_at = self._clock()
            self._failed_at = None
        return True


host_state = HostState()
