"""Whether a browser request was made by one of the Portal's own pages.

Member-written pages are served from a sibling subdomain of the Portal
(Portal ``efp.example.com``, apps ``app.example.com``). Browsers treat the
two as the same *site*, so the Portal's SameSite=Lax session cookie rides
along on requests those pages make. What still tells them apart is the
*origin*, which the browser states itself and a page cannot forge:

- ``Sec-Fetch-Site`` (every current browser): ``same-origin`` for the
  Portal's own pages, ``same-site`` for a sibling subdomain, ``cross-site``
  for anything else, ``none`` for a request the user made directly (address
  bar, bookmark).
- ``Origin`` (sent with every state-changing request and every WebSocket
  handshake): scheme, host and port of the page.

A client that sends neither header is not a browser page (curl, a script,
a runtime), and a cross-site page cannot make a browser drop them.

The same rule serves the HTTP guard (app/security/origin_guard.py) and the
handshake of the events WebSocket (app/api/proxy.py). ``Sec-Fetch-Site`` is
asked first because nothing between the browser and the Portal changes what
it says; ``Origin`` has to be compared with the ``Host`` the Portal received,
which a reverse proxy may have replaced with its own.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}
_LOG_VALUE_LIMIT = 200


def loggable_header(value: object) -> str:
    """A header value made safe for one ``key=value`` log line: printable ASCII without spaces, cut.

    What a refusal logs comes straight from the caller; a space or a line
    break in it would let the caller write fields of its own into the line.
    """
    text = "".join(ch if 32 < ord(ch) < 127 else "_" for ch in str(value or ""))
    return text[:_LOG_VALUE_LIMIT] or "-"


def _normalized(host: Optional[str], port: Optional[int], scheme: str) -> Optional[tuple[str, int]]:
    name = (host or "").rstrip(".").lower()
    if not name:
        return None
    return name, port if port is not None else _DEFAULT_PORTS.get(scheme, 0)


def _origin_authority(origin: object) -> Optional[tuple[str, tuple[str, int]]]:
    """(scheme, (host, port)) of an ``Origin`` value; None for ``null`` or anything that is not a web origin."""
    text = str(origin or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        scheme = (parts.scheme or "").lower()
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if scheme not in ("http", "https"):
        return None
    if parts.username is not None or parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    authority = _normalized(host, port, scheme)
    return (scheme, authority) if authority else None


def _host_authority(host_header: object, scheme: str) -> Optional[tuple[str, int]]:
    """(host, port) of a ``Host`` header, read with ``scheme``'s default port when it names none."""
    text = str(host_header or "").strip()
    if not text or any(ch in text for ch in "/\\@?# \t,"):
        return None
    try:
        parts = urlsplit("//" + text)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    return _normalized(host, port, scheme)


def _base_uri_authority(base_uri: object) -> Optional[tuple[str, int]]:
    text = str(base_uri or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        scheme = (parts.scheme or "").lower()
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if scheme not in ("http", "https"):
        return None
    return _normalized(host, port, scheme)


def origin_is_portal(origin: object, host_header: object, base_uri: object = "") -> bool:
    """True when ``origin`` names the host[:port] this request was sent to, or the one in ``BASE_URI``.

    The scheme is not compared: behind a TLS-terminating ingress the Portal
    sees plain http for a page the browser loaded over https.
    """
    parsed = _origin_authority(origin)
    if parsed is None:
        return False
    scheme, authority = parsed
    if authority == _host_authority(host_header, scheme):
        return True
    return authority == _base_uri_authority(base_uri)


def browser_request_is_same_origin(
    *,
    sec_fetch_site: object,
    origin: object,
    host_header: object,
    base_uri: object = "",
) -> bool:
    """The rule for a state-changing request that carries the session cookie.

    ``Sec-Fetch-Site`` decides when present. Without it (an older browser)
    ``Origin`` decides. With neither the caller is not a browser page.
    """
    fetch_site = str(sec_fetch_site or "").strip().lower()
    if fetch_site:
        return fetch_site in SAME_ORIGIN_FETCH_SITES
    if str(origin or "").strip():
        return origin_is_portal(origin, host_header, base_uri)
    return True
