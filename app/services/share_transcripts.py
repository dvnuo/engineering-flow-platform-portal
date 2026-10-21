"""The live transcript behind a share link, read from the runtime and cached briefly.

A page left open on auto-refresh, or several viewers of one link, must not
turn into a stream of session reads against the pod: one runtime read per
share per window serves them all, and viewers who arrive while a read is in
flight wait for that read instead of starting their own.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

from app.services.session_share_service import (
    ShareTranscriptCache,
    project_shared_transcript,
    session_run_in_progress,
    transcript_file_ids,
)

logger = logging.getLogger(__name__)

RUNTIME_CACHE_SECONDS = 5.0

STATUS_OK = "ok"
STATUS_ASSISTANT_STOPPED = "assistant_stopped"
STATUS_SESSION_MISSING = "session_missing"
STATUS_RUNTIME_UNREACHABLE = "runtime_unreachable"
RUNTIME_UNREACHABLE_MESSAGE = "The assistant did not answer."

_cache = ShareTranscriptCache(ttl_seconds=RUNTIME_CACHE_SECONDS, max_entries=128)
_inflight: dict[str, "asyncio.Future[dict[str, Any]]"] = {}

Forwarder = Callable[..., Awaitable[Any]]


def _unavailable(status: str, error: Optional[str] = None) -> dict[str, Any]:
    return {"status": status, "messages": [], "name": None, "running": False, "file_ids": set(), "error": error}


def invalidate_share_transcript(token: str) -> None:
    _cache.invalidate(token)


def clear_share_transcripts() -> None:
    _cache.clear()
    _inflight.clear()


async def load_share_transcript(share, agent, forward: Forwarder, *, fresh: bool = False) -> dict[str, Any]:
    """The live transcript of the shared session, or why it cannot be read right now.

    ``forward`` is ``app.web._forward_runtime``; it is called with the agent
    context only, never with a viewer identity. A stopped assistant is
    reported without being called (and never started).
    """

    if str(getattr(agent, "status", "") or "").strip().lower() != "running":
        return _unavailable(STATUS_ASSISTANT_STOPPED)

    token = str(share.token)
    if not fresh:
        cached = _cache.get(token)
        if cached is not None:
            return cached
        pending = _inflight.get(token)
        if pending is not None:
            return await asyncio.shield(pending)

    loop = asyncio.get_running_loop()
    future: "asyncio.Future[dict[str, Any]]" = loop.create_future()
    _inflight[token] = future
    try:
        result = await _read_transcript(share, agent, forward)
        _cache.put(token, result)
        future.set_result(result)
        return result
    except BaseException as exc:  # noqa: BLE001 - waiters must be released whatever happened
        if not future.done():
            future.set_exception(exc)
        raise
    finally:
        if _inflight.get(token) is future:
            _inflight.pop(token, None)


async def _read_transcript(share, agent, forward: Forwarder) -> dict[str, Any]:
    try:
        status_code, content, _content_type = await forward(
            user=None,
            agent=agent,
            method="GET",
            subpath=f"api/sessions/{quote(share.session_id, safe='')}",
            query_items=[],
            body=None,
        )
    except Exception as exc:  # noqa: BLE001 - the runtime being down is a page state, not a 500
        logger.info("Shared session %s could not reach its runtime: %s", share.token, exc)
        return _unavailable(STATUS_RUNTIME_UNREACHABLE, RUNTIME_UNREACHABLE_MESSAGE)

    if status_code == 404:
        return _unavailable(STATUS_SESSION_MISSING)
    if status_code >= 400:
        # The body may carry node addresses or exception text; viewers get a
        # fixed sentence and the log gets the detail.
        logger.info("Shared session %s: runtime answered %s: %s", share.token, status_code, (content or b"")[:300])
        return _unavailable(STATUS_RUNTIME_UNREACHABLE, RUNTIME_UNREACHABLE_MESSAGE)

    try:
        payload = json.loads(content.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.info("Shared session %s: runtime answer was not JSON", share.token)
        return _unavailable(STATUS_RUNTIME_UNREACHABLE, RUNTIME_UNREACHABLE_MESSAGE)
    if not isinstance(payload, dict):
        payload = {}

    messages = project_shared_transcript(payload, assistant_name=str(getattr(agent, "name", "") or ""))
    name = payload.get("name")
    return {
        "status": STATUS_OK,
        "messages": messages,
        "name": name.strip() if isinstance(name, str) and name.strip() else None,
        "running": session_run_in_progress(payload.get("metadata")),
        "file_ids": transcript_file_ids(messages),
        "error": None,
    }
