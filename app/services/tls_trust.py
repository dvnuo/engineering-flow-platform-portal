"""The CA certificates the Portal's own outbound TLS connections trust.

httpx verifies server certificates against ``certifi``'s bundle alone: the
container's system trust store (``/etc/ssl/certs``) is never consulted, and
the ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` variables count only while the client
still reads its environment (``trust_env``), which the connector probes switch
off whenever a Proxy connector decides the path. The runtimes work the other
way round: the Go tools and the native runtime's ``urllib`` trust the system
store, and a corporate CA mounted or baked into the pod image is trusted by
everything in it. So a Jira behind a private CA, or an egress proxy that
re-signs TLS, answered the assistant and failed the Portal's Test button with
``CERTIFICATE_VERIFY_FAILED``.

This module builds the one context the probes verify with: OpenSSL's default
paths (system store, ``SSL_CERT_FILE``, ``SSL_CERT_DIR``), certifi's bundle,
and the PEM file or directory ``PORTAL_CA_BUNDLE`` names, so the Portal trusts
what the runtimes trust without a verification switch.
"""

from __future__ import annotations

import logging
from pathlib import Path
import ssl
import threading

import certifi

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cached: tuple[str, ssl.SSLContext] | None = None

VERIFY_FAILURE_MARKER = "CERTIFICATE_VERIFY_FAILED"


def configured_ca_bundle() -> str:
    """The ``PORTAL_CA_BUNDLE`` setting, stripped; empty when unset."""
    from app.config import get_settings

    return str(getattr(get_settings(), "portal_ca_bundle", "") or "").strip()


def build_trust_context(extra_bundle: str = "") -> ssl.SSLContext:
    """A verifying client context: system store + certifi + ``extra_bundle`` (a PEM file or a directory)."""
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        logger.warning("certifi's CA bundle could not be loaded; relying on the system store", exc_info=True)
    bundle = (extra_bundle or "").strip()
    if bundle:
        path = Path(bundle)
        try:
            if path.is_dir():
                context.load_verify_locations(capath=str(path))
            elif path.is_file():
                context.load_verify_locations(cafile=str(path))
            else:
                logger.warning("PORTAL_CA_BUNDLE=%s is neither a file nor a directory; ignored", bundle)
        except (OSError, ssl.SSLError):
            logger.warning("PORTAL_CA_BUNDLE=%s could not be loaded; ignored", bundle, exc_info=True)
    return context


def trust_context(extra_bundle: str | None = None) -> ssl.SSLContext:
    """The shared context for this process, rebuilt only when the bundle setting changes.

    Loading the stores costs a few milliseconds and a context is safe to share
    between connections, so every probe gets the same one.
    """
    global _cached
    bundle = configured_ca_bundle() if extra_bundle is None else (extra_bundle or "").strip()
    with _lock:
        if _cached is None or _cached[0] != bundle:
            _cached = (bundle, build_trust_context(bundle))
        return _cached[1]


def reset_trust_context() -> None:
    """Forget the cached context (tests, and a setting changed on a running Portal)."""
    global _cached
    with _lock:
        _cached = None


def _verification_error_in(exc: BaseException | None, depth: int = 0) -> ssl.SSLCertVerificationError | None:
    if exc is None or depth > 8:
        return None
    if isinstance(exc, ssl.SSLCertVerificationError):
        return exc
    for linked in (exc.__cause__, exc.__context__):
        found = _verification_error_in(linked, depth + 1)
        if found is not None:
            return found
    return None


def describe_verification_failure(exc: BaseException, *, host: str) -> str | None:
    """Plain words for a TLS verification failure wrapped anywhere in ``exc``; None for any other error.

    The OpenSSL reason ("self-signed certificate in certificate chain",
    "unable to get local issuer certificate") says what is missing; the rest
    says where the Portal looks for it and why the assistant may not have the
    same problem.
    """
    found = _verification_error_in(exc)
    reason = ""
    if found is not None:
        reason = str(getattr(found, "verify_message", "") or "").strip()
        if not reason:
            reason = str(found).strip()
    elif VERIFY_FAILURE_MARKER in str(exc):
        text = str(exc)
        reason = text.split("certificate verify failed:", 1)[1].strip() if "certificate verify failed:" in text else text
    else:
        return None
    reason = reason.split(" (_ssl.c:", 1)[0].strip().rstrip(".")
    where = f"TLS certificate verification failed for {host}" + (f": {reason}" if reason else "")
    return (
        f"{where}. The Portal trusts the CA certificates of its own container (the system store, "
        "certifi and PORTAL_CA_BUNDLE); the assistant's runtime trusts its own image, so it may work "
        "where this probe does not. Install the CA that signed this certificate, or the proxy's, in the "
        "Portal, or point PORTAL_CA_BUNDLE at a PEM file holding it."
    )
