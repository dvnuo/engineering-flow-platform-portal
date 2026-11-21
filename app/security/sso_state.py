"""The ``state`` of the single sign-on round trip.

``/login/sso`` makes up a random value, keeps it in a short-lived HttpOnly
cookie and sends it to the identity provider as ``state``; the provider
hands it back to ``/auth`` with the code. A callback whose ``state`` is not
the one in this browser's cookie did not start here: someone is trying to
finish *their* sign-in in the member's browser (login CSRF), and it is sent
back to the login page.

Over https the cookie is ``__Host-`` prefixed (app/security/session_cookie.py),
so a page on a sibling subdomain cannot plant a state of its own choosing.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Mapping, Optional

from fastapi import Response

from app.security.session_cookie import cookies_are_hardened, host_bound_cookie_name

SSO_STATE_COOKIE = "portal_sso_state"
SSO_STATE_MAX_AGE_SECONDS = 600


def sso_state_cookie_name() -> str:
    return host_bound_cookie_name(SSO_STATE_COOKIE)


def new_sso_state() -> str:
    return secrets.token_urlsafe(32)


def set_sso_state_cookie(response: Response, state: str) -> None:
    # SameSite=Lax: the provider's redirect back to /auth is a cross-site
    # top-level navigation, which is exactly what Lax still sends cookies on.
    response.set_cookie(
        sso_state_cookie_name(),
        state,
        max_age=SSO_STATE_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookies_are_hardened(),
    )


def clear_sso_state_cookie(response: Response) -> None:
    response.delete_cookie(sso_state_cookie_name(), path="/", httponly=True, secure=cookies_are_hardened())


def sso_state_is_valid(cookies: Optional[Mapping[str, str]], presented: object) -> bool:
    """Whether the callback's ``state`` is the one this browser was given at ``/login/sso``."""
    expected = (cookies or {}).get(sso_state_cookie_name()) or ""
    given = presented if isinstance(presented, str) else ""
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))
