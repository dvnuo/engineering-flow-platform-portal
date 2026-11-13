"""The picker cache: one upstream call for the same question asked at once."""
from __future__ import annotations

import asyncio

import pytest

from app.services.suggestions.cache import TTLCache


def test_callers_asking_at_the_same_moment_share_one_load_and_later_ones_read_the_cache():
    async def scenario():
        cache = TTLCache()
        calls = []
        release = asyncio.Event()

        async def loader():
            calls.append(1)
            await release.wait()
            return "rows"

        first = asyncio.ensure_future(cache.get_or_load("k", loader, lambda value: 60.0))
        second = asyncio.ensure_future(cache.get_or_load("k", loader, lambda value: 60.0))
        await asyncio.sleep(0)
        release.set()
        answers = await asyncio.gather(first, second)
        again = await cache.get_or_load("k", loader, lambda value: 60.0)
        return answers, again, len(calls)

    assert asyncio.run(scenario()) == (["rows", "rows"], "rows", 1)


def test_a_caller_that_goes_away_takes_nothing_from_the_others():
    async def scenario():
        cache = TTLCache()
        release = asyncio.Event()
        calls = []

        async def loader():
            calls.append(1)
            await release.wait()
            return "rows"

        impatient = asyncio.ensure_future(cache.get_or_load("k", loader, lambda value: 60.0))
        patient = asyncio.ensure_future(cache.get_or_load("k", loader, lambda value: 60.0))
        await asyncio.sleep(0)
        impatient.cancel()
        await asyncio.sleep(0)
        release.set()
        answer = await patient
        return impatient.cancelled(), answer, len(calls), cache.get("k")

    assert asyncio.run(scenario()) == (True, "rows", 1, "rows")


def test_a_failed_load_reaches_every_waiter_and_is_tried_again_next_time():
    async def scenario():
        cache = TTLCache()
        outcomes = iter([RuntimeError("down"), "rows"])

        async def loader():
            await asyncio.sleep(0)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        results = await asyncio.gather(
            cache.get_or_load("k", loader, lambda value: 60.0),
            cache.get_or_load("k", loader, lambda value: 60.0),
            return_exceptions=True,
        )
        retry = await cache.get_or_load("k", loader, lambda value: 60.0)
        return [type(result).__name__ for result in results], retry

    assert asyncio.run(scenario()) == (["RuntimeError", "RuntimeError"], "rows")


def test_an_answer_with_no_lifetime_is_not_kept_and_an_empty_one_still_counts():
    async def scenario():
        cache = TTLCache()
        calls = []

        async def loader():
            calls.append(1)
            return []

        await cache.get_or_load("gone", loader, lambda value: 0.0)
        await cache.get_or_load("gone", loader, lambda value: 0.0)
        await cache.get_or_load("kept", loader, lambda value: 60.0)
        await cache.get_or_load("kept", loader, lambda value: 60.0)
        return len(calls)

    # Twice for the key that is never kept, once for the empty list that is.
    assert asyncio.run(scenario()) == 3


def test_entries_expire_and_a_full_cache_drops_the_ones_closest_to_expiry(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(TTLCache, "_now", staticmethod(lambda: clock["now"]))
    cache = TTLCache(max_entries=3)
    cache.set("a", 1, 10)
    cache.set("b", 2, 20)
    cache.set("c", 3, 30)
    assert cache.get("a") == 1

    cache.set("d", 4, 40)
    assert cache.get("a") is None and cache.get("b") == 2 and cache.get("d") == 4

    clock["now"] = 125.0
    assert cache.get("b") is None and cache.get("c") == 3
    cache.clear()
    assert cache.get("c") is None


@pytest.mark.parametrize("ttl", [0, -1])
def test_a_non_positive_lifetime_stores_nothing(ttl):
    cache = TTLCache()
    cache.set("k", "v", ttl)
    assert cache.get("k") is None


# ------------------------------------------------- connections to the systems


def test_a_source_keeps_one_connection_pool_for_all_its_requests_and_lets_it_go_afterwards():
    """A connection per request spends three round trips (TCP, the proxy,
    TLS) before the request leaves. On a slow link that was most of the wait."""
    import httpx

    from app.services.suggestions import clear_cache, suggest
    from app.services.suggestions.base import ConnectorHttp
    from tests._suggestions_helpers import GITHUB_CONFIG, Recorder, make_context

    made: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    class Counted(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    github = Recorder({("GET", "/user"): {"login": "alice"}, ("GET", "/search/issues"): {"items": []}})
    context = make_context(github, GITHUB_CONFIG)
    assert isinstance(context.http, ConnectorHttp)

    clear_cache()
    httpx.AsyncClient = Counted
    try:
        result = asyncio.run(suggest("github.pull_requests", context))
    finally:
        httpx.AsyncClient = real
        clear_cache()

    assert result.state == "ok" and len(github.requests) == 3
    assert len(made) == 1 and made[0].is_closed
    # Asking again later is not an error: it opens a new pool.
    assert context.http._clients == {}
