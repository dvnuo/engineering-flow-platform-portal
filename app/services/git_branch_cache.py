"""Short-lived cache for remote branch listings.

Prefilling the Assistant Types panel's branch selects means two `git ls-remote`
calls to a remote host every time the panel opens — measured at 4-8 seconds,
which is worse than the "Load branches" button it replaced. Branch lists barely
change, so a brief cache makes the common case instant while still picking up a
new branch within a minute.

The two lookups also run concurrently rather than one after the other, so even a
cold load costs one round trip instead of two.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Long enough that opening the panel, editing, and saving does not re-fetch;
# short enough that a branch pushed a minute ago shows up.
BRANCH_CACHE_TTL_SECONDS = 60.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, list[str], str]] = {}


def _now() -> float:
    return time.monotonic()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def cached_branches(repo_url: str | None) -> tuple[list[str], str]:
    """Branch names plus an error string, memoized for a short window.

    Failures are cached too, and deliberately: a repository Portal cannot reach
    would otherwise make every panel open pay the full timeout.
    """
    if not repo_url:
        return [], ""

    with _lock:
        entry = _cache.get(repo_url)
        if entry and _now() - entry[0] < BRANCH_CACHE_TTL_SECONDS:
            return list(entry[1]), entry[2]

    from app.api.git_repos import safe_fetch_repo_branches

    branches, error = safe_fetch_repo_branches(repo_url)
    with _lock:
        _cache[repo_url] = (_now(), list(branches), error)
    return branches, error


def cached_branches_for(repo_urls: list[str | None]) -> list[tuple[list[str], str]]:
    """Look several repositories up at once, preserving input order."""

    if not repo_urls:
        return []
    if len(repo_urls) == 1:
        return [cached_branches(repo_urls[0])]

    with ThreadPoolExecutor(max_workers=min(4, len(repo_urls))) as pool:
        return list(pool.map(cached_branches, repo_urls))
