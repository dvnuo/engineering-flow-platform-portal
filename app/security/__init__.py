"""Request guards and startup checks the Portal needs before member-written apps share its site.

Member apps are served from a sibling subdomain of the Portal and their
code runs inside the cluster. That makes three things forgeable that were
not before: a state-changing request that carries a member's cookie, a call
to the Portal's cluster-only APIs, and the cookie itself. The modules here
close them:

- ``origin_guard``: state-changing requests with the session cookie must come
  from the Portal's own pages.
- ``internal_api``: ``/api/internal/`` and ``/api/app-host/`` are not served
  through a reverse proxy, and ``/api/internal/`` wants the assistant's token.
- ``session_cookie``: the cookie's name and flags (``__Host-`` + ``Secure``
  over https).
- ``sso_state``: the ``state`` of the single sign-on round trip.
- ``startup_checks``: no start with the built-in ``SECRET_KEY``.

``install_portal_security`` is the only thing app/main.py calls.
"""

from __future__ import annotations

from typing import Any

from app.security.internal_api import ClusterOnlyApiMiddleware
from app.security.origin_guard import OriginGuardMiddleware
from app.security.startup_checks import run_startup_checks


def install_portal_security(app: Any, settings: Any) -> None:
    """Add the request guards to ``app`` and run the startup checks before its other startup work.

    Call it before the request-scoped middlewares are declared: a middleware
    added later wraps the ones added earlier, so the guards then run inside
    the log-context middleware and a refused request is logged with its
    trace id like any other.
    """
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(ClusterOnlyApiMiddleware)

    def portal_security_startup_checks() -> None:
        run_startup_checks(settings)

    app.add_event_handler("startup", portal_security_startup_checks)
