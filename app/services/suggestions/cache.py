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
        self._inflight: dict[Hashable, asyncio.Future] = {}  # key -> the load under way

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

        The load runs as a task of its own, shared by everyone who asks for
        the same key while it is under way. A caller that goes away (the
        member closed the dialog, the browser dropped the request) therefore
        takes nothing from the others: the load finishes and is cached. A
        loader that raises is not cached, and every caller waiting on it sees
        the same exception.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key, loader, ttl_of))
            # If every caller left before it failed, nobody reads the
            # exception; reading it here keeps the loop from reporting it.
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            self._inflight[key] = task
        return await asyncio.shield(task)

    async def _load(self, key: Hashable, loader: Callable[[], Awaitable[Any]], ttl_of: Callable[[Any], float]) -> Any:
        try:
            value = await loader()
            self.set(key, value, ttl_of(value))
            return value
        finally:
            self._inflight.pop(key, None)
