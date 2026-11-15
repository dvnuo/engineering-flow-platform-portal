"""The Portal's cluster-only APIs: who may call them, and from where.

``/api/internal/*`` is what an assistant's runtime calls back into (session
metadata and titles). ``/api/app-host/*`` is what the apps host calls (it
arrives with the Apps feature). Two rules, both enforced here:

1. Not from outside the cluster. A request that came through a reverse
   proxy carries ``X-Forwarded-For`` or ``X-Real-IP`` (ingress-nginx sets
   both); a caller inside the cluster talks to the Portal's Service directly
   and carries neither. Forwarded requests to these prefixes are answered
   404, as if the route did not exist. ``INTERNAL_API_BLOCK_FORWARDED=false``
   switches this off, for a cluster whose runtimes can only reach the Portal
   through a proxy that adds those headers.

2. ``/api/internal/agents/{agent_id}/...`` only by that assistant. The
   runtime presents the token the Portal put into its pod
   (``EFP_PORTAL_TRUST_TOKEN``, app/services/agent_trust.py) as
   ``X-Portal-Trust-Token``. A wrong token is always 401. A missing one is
   401 only once ``INTERNAL_API_AUTH_REQUIRED=true``; until then it is served
   and logged once per assistant, so runtime images from before the token
   keep working while assistants are restarted onto new ones.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from fastapi import HTTPException, Request, status
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app.config import get_settings
from app.services.agent_trust import TRUST_TOKEN_HEADER, trust_token_matches

logger = logging.getLogger(__name__)
settings = get_settings()

CLUSTER_ONLY_PREFIXES = ("/api/internal/", "/api/app-host/")
FORWARDED_HEADERS = ("x-forwarded-for", "x-real-ip")

_UNAUTHENTICATED_LOG_LIMIT = 1024
_unauthenticated_logged: set[str] = set()
_unauthenticated_lock = threading.Lock()
_forwarded_block_logged = False


class ClusterOnlyApiMiddleware:
    """Answer 404 for a cluster-only prefix when the request arrived through a reverse proxy."""

    def __init__(self, app, *, settings_source: Optional[Callable[[], Any]] = None) -> None:
        self.app = app
        self._settings_source = settings_source

    def _current_settings(self) -> Any:
        return self._settings_source() if self._settings_source is not None else settings

    def _blocked(self, scope) -> bool:
        path = str(scope.get("path") or "")
        if not path.startswith(CLUSTER_ONLY_PREFIXES):
            return False
        if not bool(getattr(self._current_settings(), "internal_api_block_forwarded", True)):
            return False
        headers = Headers(scope=scope)
        forwarded = [name for name in FORWARDED_HEADERS if headers.get(name)]
        if not forwarded:
            return False
        global _forwarded_block_logged
        if not _forwarded_block_logged:
            _forwarded_block_logged = True
            # Once: an ingress on the internet is probed all day. If this is a
            # runtime behind a proxy that adds the header, the operator needs
            # to see why its session metadata stopped arriving.
            logger.warning(
                "A request to a cluster-only Portal API arrived through a reverse proxy (%s present) and was "
                "answered 404. Runtimes must reach PORTAL_INTERNAL_BASE_URL directly; if yours can only go "
                "through a proxy that adds this header, set INTERNAL_API_BLOCK_FORWARDED=false. "
                "Further blocked requests are not logged.",
                ", ".join(forwarded),
            )
        return True

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and self._blocked(scope):
            response = JSONResponse({"detail": "Not Found"}, status_code=404)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _note_unauthenticated_call(agent_id: str) -> None:
    with _unauthenticated_lock:
        if agent_id in _unauthenticated_logged or len(_unauthenticated_logged) >= _UNAUTHENTICATED_LOG_LIMIT:
            return
        _unauthenticated_logged.add(agent_id)
    logger.warning(
        "Internal API call for agent_id=%s carried no %s header and was served because "
        "INTERNAL_API_AUTH_REQUIRED is false. Restart the assistant onto a runtime image that sends the "
        "token, then set INTERNAL_API_AUTH_REQUIRED=true. Logged once per assistant.",
        "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in agent_id)[:80] or "-",
        TRUST_TOKEN_HEADER,
    )


def require_agent_trust_token(agent_id: str, request: Request) -> None:
    """Dependency for ``/api/internal/agents/{agent_id}/...``: the caller is that assistant's runtime."""
    presented = (request.headers.get(TRUST_TOKEN_HEADER) or "").strip()
    if presented:
        if not trust_token_matches(agent_id, presented):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Portal trust token")
        return
    if bool(getattr(settings, "internal_api_auth_required", False)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Portal trust token required")
    _note_unauthenticated_call(str(agent_id))
