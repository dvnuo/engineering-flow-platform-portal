"""What the Portal refuses to start with, and what it warns about, before serving anything.

``SECRET_KEY`` signs the session cookie and is the key every assistant's
trust token is derived from (app/services/agent_trust.py). With the built-in
default anyone who has read this repository can forge a session for any
member and the token of any assistant, so a Portal that is not in debug mode
does not start with it. ``ALLOW_INSECURE_SECRET_KEY=true`` keeps an existing
installation running until its operator has set a key.
"""

from __future__ import annotations

import logging
from typing import Any

from app.security.session_cookie import (
    DEFAULT_SESSION_COOKIE_NAME,
    base_uri_is_https,
    configured_session_cookie_name,
    cookies_are_hardened,
    session_cookie_name,
)

logger = logging.getLogger(__name__)

DEFAULT_SECRET_KEY = "change-me-in-production"


class InsecureSecretKeyError(RuntimeError):
    """The Portal was started outside debug mode with the built-in ``SECRET_KEY``."""


def secret_key_is_default(settings: Any) -> bool:
    key = str(getattr(settings, "secret_key", "") or "").strip()
    return key == "" or key == DEFAULT_SECRET_KEY


def check_secret_key(settings: Any) -> None:
    if not secret_key_is_default(settings):
        return
    if bool(getattr(settings, "debug", False)):
        logger.info("SECRET_KEY is the built-in default; accepted because DEBUG is on.")
        return
    if bool(getattr(settings, "allow_insecure_secret_key", False)):
        logger.warning(
            "INSECURE: SECRET_KEY is the built-in default and ALLOW_INSECURE_SECRET_KEY=true lets the Portal "
            "start anyway. Anyone can forge a session for any member and the trust token of any assistant. "
            "Set SECRET_KEY to a long random value (every member signs in again and every assistant must be "
            "restarted), then remove ALLOW_INSECURE_SECRET_KEY."
        )
        return
    raise InsecureSecretKeyError(
        "SECRET_KEY is not set (it is still the built-in default), so the Portal refuses to start: the key "
        "signs member sessions and the trust token of every assistant. Set the SECRET_KEY environment "
        "variable to a long random value, for example the output of "
        "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`, and keep it stable across "
        "restarts (in Kubernetes: a SECRET_KEY entry in efp-portal-secret, see k8s/README.md). "
        "To keep an existing installation running with the default key for now, set "
        "ALLOW_INSECURE_SECRET_KEY=true."
    )


def warn_about_session_cookie(settings: Any) -> None:
    if cookies_are_hardened(settings):
        return
    if not base_uri_is_https(settings):
        reason = "BASE_URI is not an https:// address"
    else:
        reason = (
            f"SESSION_COOKIE_NAME is set to {configured_session_cookie_name(settings)!r} "
            f"instead of the default {DEFAULT_SESSION_COOKIE_NAME!r}"
        )
    logger.warning(
        "The session cookie %r is sent without Secure and without the __Host- prefix because %s. "
        "A page on a sibling subdomain can then overwrite it, and it travels over plain http. "
        "Serve the Portal over https, set BASE_URI to that address and leave SESSION_COOKIE_NAME unset.",
        session_cookie_name(settings),
        reason,
    )


def run_startup_checks(settings: Any) -> None:
    check_secret_key(settings)
    warn_about_session_cookie(settings)
