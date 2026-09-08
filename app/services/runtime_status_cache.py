"""Short-lived cache of agent runtime status readings.

Every status reading is a live Kubernetes API call. One member polling one
assistant is fine; every open tab polling every visible assistant every half
minute is not, and that is what keeping the sidebar honest costs. The batch
status endpoint reads through this cache; the single-agent endpoint stays live
(it is what a member watches while their assistant starts) and refreshes the
cache on the way past.

Lifecycle actions invalidate their agent, so a refresh right after Stop never
reports the "running" it read a few seconds earlier.

Process-local by design: the portal runs as one process, and a stale reading
here costs at most the TTL, never correctness, because the single endpoint and
every action go to the cluster directly.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

DEFAULT_TTL_SECONDS = 10.0


class RuntimeStatusCache:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, clock=time.monotonic) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, agent_id: str) -> Optional[Any]:
        """The cached reading, or None when there is none or it has expired."""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(agent_id)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(agent_id, None)
                return None
            return value

    def put(self, agent_id: str, value: Any) -> None:
        with self._lock:
            self._entries[agent_id] = (self._clock() + self.ttl_seconds, value)

    def invalidate(self, agent_id: str) -> None:
        with self._lock:
            self._entries.pop(agent_id, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


runtime_status_cache = RuntimeStatusCache()
