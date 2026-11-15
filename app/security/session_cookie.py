"""The one place that knows what the Portal's session cookie is called.

When ``BASE_URI`` is https and ``SESSION_COOKIE_NAME`` is still the default,
the cookie is ``__Host-portal_session`` and carries ``Secure``. A browser
accepts a ``__Host-`` cookie only from the host itself, over https, with
``Path=/`` and no ``Domain``, so a page on a sibling subdomain can neither
read it nor plant one of its own for the Portal ("cookie tossing"). The old
name is not read as a fallback in that mode: a cookie the prefix does not
protect would defeat the purpose, so members sign in once more after the
switch.

Otherwise (plain http, local development, or a custom cookie name) the name
and flags stay what they were, and the startup checks say so
(app/security/startup_checks.py).

Every reader and writer goes through these helpers; nothing else reads
``settings.session_cookie_name`` (tests/test_session_cookie_hardening.py).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.config import get_settings

DEFAULT_SESSION_COOKIE_NAME = "portal_session"
HOST_COOKIE_PREFIX = "__Host-"

# Captured at import like the ``settings`` of app.web, app.deps and
# app.api.proxy, so all of them answer from the same object.
settings = get_settings()


def _settings(override: Any = None) -> Any:
    return override if override is not None else settings


def base_uri_is_https(settings: Any = None) -> bool:
    base_uri = str(getattr(_settings(settings), "base_uri", "") or "").strip().lower()
    return base_uri.startswith("https://")


def configured_session_cookie_name(settings: Any = None) -> str:
    name = str(getattr(_settings(settings), "session_cookie_name", "") or "").strip()
    return name or DEFAULT_SESSION_COOKIE_NAME


def cookies_are_hardened(settings: Any = None) -> bool:
    """Whether the Portal's cookies are ``Secure`` and host-bound: https and the default cookie name."""
    return base_uri_is_https(settings) and configured_session_cookie_name(settings) == DEFAULT_SESSION_COOKIE_NAME


def session_cookie_name(settings: Any = None) -> str:
    """The cookie the session token is stored in for this deployment."""
    if cookies_are_hardened(settings):
        return HOST_COOKIE_PREFIX + DEFAULT_SESSION_COOKIE_NAME
    return configured_session_cookie_name(settings)


def host_bound_cookie_name(name: str, settings: Any = None) -> str:
    """``name`` for another Portal cookie that must not be planted from a sibling subdomain."""
    return HOST_COOKIE_PREFIX + name if cookies_are_hardened(settings) else name


def read_session_token(cookies: Optional[Mapping[str, str]], settings: Any = None) -> Optional[str]:
    """The session token a request or WebSocket handshake carries, if any."""
    if not cookies:
        return None
    return cookies.get(session_cookie_name(settings)) or None
