"""A small per-process cache for picker answers.

Opening a card's picker, typing in it and reopening it a minute later would
each cost a round trip to GitHub or Jira. The answers change slowly and belong
to one member, so they are kept briefly, keyed by that member. Two requests
for the same thing at the same moment (a hover that warms the picker, then the
click that opens it) share one upstream call instead of racing.

Everything here runs on the event loop, so plain dicts are enough.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Hashable

MAX_ENTRIES = 512


class TTLCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future] = {}

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def clear(self) -> None:
        self._entries.clear()
        self._inflight.clear()

    def get(self, key: Hashable) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[0] <= self._now():
            self._entries.pop(key, None)
            return None
        return entry[1]

    def set(self, key: Hashable, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        if len(self._entries) >= self._max_entries:
            self._evict()
        self._entries[key] = (self._now() + ttl, value)

    def _evict(self) -> None:
        now = self._now()
        for key in [key for key, entry in self._entries.items() if entry[0] <= now]:
            self._entries.pop(key, None)
        # Still full: drop the entries closest to expiry.
        overflow = len(self._entries) - self._max_entries + 1
        if overflow > 0:
            for key, _entry in sorted(self._entries.items(), key=lambda pair: pair[1][0])[:overflow]:
                self._entries.pop(key, None)

    async def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], Awaitable[Any]],
        ttl_of: Callable[[Any], float],
    ) -> Any:
        """The cached value, or the loader's, kept for ``ttl_of(value)`` seconds.

        A loader that raises is not cached, and every caller waiting on it
        sees the same exception.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await loader()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                # Nobody may be waiting; do not let the loop log it as unretrieved.
                future.exception()
            raise
        else:
            self.set(key, value, ttl_of(value))
            if not future.done():
                future.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)
