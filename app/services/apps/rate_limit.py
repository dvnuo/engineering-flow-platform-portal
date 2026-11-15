"""A sliding one-minute window of SDK calls per app.

The Portal is one process, so the windows live in memory: per app, the times
of the calls that were let through in the last minute. A call is refused when
the window is full and is told how long until the oldest call leaves it.
Refused calls do not count. Nothing survives a restart, which only ever makes
the limit more generous for a moment.
"""

from __future__ import annotations

from collections import deque
import math
import threading
import time
from typing import Callable, Optional

WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(self, *, window_seconds: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._window = float(window_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int) -> Optional[int]:
        """Count a call for ``key``. None when it may go ahead, else the seconds to wait.

        ``limit`` is the most calls in one window; zero or less means no limit.
        """
        if limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            calls = self._calls.get(key)
            if calls is None:
                calls = self._calls[key] = deque()
            while calls and now - calls[0] >= self._window:
                calls.popleft()
            # A limit that was lowered leaves more calls in the window than it allows.
            if len(calls) >= limit:
                oldest = calls[len(calls) - limit]
                return max(1, int(math.ceil(oldest + self._window - now)))
            calls.append(now)
            if len(self._calls) > 4096:
                self._forget_idle(now)
        return None

    def _forget_idle(self, now: float) -> None:
        for key in [key for key, calls in self._calls.items() if not calls or now - calls[-1] >= self._window]:
            self._calls.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


sdk_rate_limiter = SlidingWindowLimiter()
