"""Refuse state-changing requests that another site's page makes with a member's session.

The Portal authenticates its pages with a cookie. A form post, a bodiless
POST or a fetch from a page on a sibling subdomain carries that cookie
(same site), so without this guard such a page could save a member's
connectors, start or stop their assistants or change groups as them. See
app/security/request_origin.py for how a request is told apart.

What is left alone:

- GET / HEAD / OPTIONS: reads, whose answers another origin's page cannot
  see (the Portal sends no CORS headers).
- Requests without the session cookie: there is no member to impersonate
  (sign-in posts, runtimes, scripts with their own credentials).
- ``/api/app-sdk/``, ``/api/app-host/`` and ``/api/internal/``: APIs that
  authenticate with a token and never with the cookie.
- Requests with neither ``Sec-Fetch-Site`` nor ``Origin``: not a browser page.

``ORIGIN_GUARD_ENABLED=false`` switches the guard off (and the same check on
the events WebSocket in app/api/proxy.py).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse

from app.config import get_settings
from app.i18n import N_
from app.security.request_origin import browser_request_is_same_origin, loggable_header
from app.security.session_cookie import read_session_token

logger = logging.getLogger(__name__)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Token-authenticated APIs. /api/app-sdk/ and /api/app-host/ belong to the
# Apps feature and arrive with it.
TOKEN_AUTHENTICATED_PREFIXES = ("/api/app-sdk/", "/api/app-host/", "/api/internal/")
BLOCKED_STATUS = 403
BLOCKED_DETAIL = N_("Cross-site request blocked")


settings = get_settings()


def origin_guard_enabled(override: Any = None) -> bool:
    return bool(getattr(override if override is not None else settings, "origin_guard_enabled", True))


class OriginGuardMiddleware:
    """Pure ASGI, so streamed bodies and responses pass through untouched."""

    def __init__(self, app, *, settings_source: Optional[Callable[[], Any]] = None) -> None:
        self.app = app
        self._settings_source = settings_source

    def _current_settings(self) -> Any:
        return self._settings_source() if self._settings_source is not None else settings

    def _blocked(self, scope) -> bool:
        if str(scope.get("method") or "").upper() not in UNSAFE_METHODS:
            return False
        path = str(scope.get("path") or "")
        if path.startswith(TOKEN_AUTHENTICATED_PREFIXES):
            return False
        current = self._current_settings()
        if not origin_guard_enabled(current):
            return False
        headers = Headers(scope=scope)
        cookie_header = headers.get("cookie")
        if not cookie_header or not read_session_token(cookie_parser(cookie_header), current):
            return False
        sec_fetch_site = headers.get("sec-fetch-site")
        origin = headers.get("origin")
        host = headers.get("host")
        if browser_request_is_same_origin(
            sec_fetch_site=sec_fetch_site,
            origin=origin,
            host_header=host,
            base_uri=getattr(current, "base_uri", ""),
        ):
            return False
        # The Host is logged with the Origin: when the two were compared, an
        # operator behind a proxy that replaces Host sees the mismatch here.
        logger.warning(
            "Cross-site request blocked method=%s path=%s sec_fetch_site=%s origin=%s host=%s",
            loggable_header(scope.get("method")),
            loggable_header(path),
            loggable_header(sec_fetch_site),
            loggable_header(origin),
            loggable_header(host),
        )
        return True

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and self._blocked(scope):
            response = JSONResponse({"detail": BLOCKED_DETAIL}, status_code=BLOCKED_STATUS)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
