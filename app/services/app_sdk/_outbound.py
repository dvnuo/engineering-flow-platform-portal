"""How the app SDK services leave the Portal, and what they never say.

Both services call other systems with a member's own settings, so they leave
the way that member's assistant would: through the proxy the Proxy connector
assigns the connector (or directly), verifying TLS against the Portal's shared
trust (system store, certifi, PORTAL_CA_BUNDLE). Those rules live once, in
app/services/connector_http.py, and are the ones the connector Test buttons
use; this module only keeps one client per way out for the length of a call
and scrubs that call's credentials from anything that becomes a message.
"""
from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import logging
import re
import weakref
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from app.redaction import sanitize_exception_message
from app.services import connector_http
from app.services.proxy_plan import build_proxy_plan, hostname_of
from app.services.tls_trust import describe_verification_failure

CONNECT_TIMEOUT_SECONDS = 10.0
REDACTED = "[redacted]"
# Shorter than this a value cannot be told from ordinary text.
MIN_SECRET_LENGTH = 4

# httpx logs every request line at INFO, query string included. What an app
# puts in a connector request's query (a search, sometimes a token) is not
# something to keep in the Portal's log, so requests made inside ``unlogged``
# are left out of httpx's own log, the way the starter-card pickers do it
# (app/services/suggestions/base.py).
_unlogged_request: contextvars.ContextVar[bool] = contextvars.ContextVar("app_sdk_unlogged_request", default=False)


class _UnloggedRequests(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _unlogged_request.get()


logging.getLogger("httpx").addFilter(_UnloggedRequests())


class unlogged:
    """Context manager: httpx does not log the requests made inside it."""

    def __enter__(self) -> None:
        self._token = _unlogged_request.set(True)

    def __exit__(self, *exc_info: Any) -> None:
        _unlogged_request.reset(self._token)


# ---------------------------------------------------------------- where a call may not go
#
# The address of a connector and a model provider's base URL are a member's
# own settings, and an app's owner decides what the app asks of them. Called
# from the Portal they would reach what only the Portal's own machine and its
# network neighbourhood can: the Portal's own port (and with it the APIs that
# are served to callers inside the cluster only), and the metadata service of
# the cloud the Portal runs in, which hands out the machine's credentials. So
# an address that means "this machine" (loopback, the unspecified address) or
# is link-local is never called. Private ranges stay allowed: the company's
# Jira, Jenkins and Nexus live there.
#
# Only the address as written is judged, in every spelling a resolver takes
# for an address, plus the few names that mean one of them by definition. A
# name is not looked up: the request may leave through a proxy that resolves
# it its own way, and a lookup made here is not the one the connection makes
# a moment later, so it would not bind whoever controls the name.

# The metadata service of AWS over IPv6: a unique-local address, so the
# link-local rule does not cover it.
_METADATA_ADDRESSES = frozenset({ipaddress.ip_address("fd00:ec2::254")})
# Names that are loopback or the metadata service wherever they resolve.
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_METADATA_NAMES = frozenset({"metadata.google.internal", "instance-data.ec2.internal"})
_IPV4_PART = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)$")

TARGET_LOOPBACK = "the Portal's own machine"
TARGET_LINK_LOCAL = "a link-local address"
TARGET_UNSPECIFIED = "the unspecified address"
TARGET_METADATA = "a cloud metadata service"


def _old_style_ipv4(text: str) -> ipaddress.IPv4Address | None:
    """``127.1``, ``0x7f.0.0.1``, ``017700000001``, ``2130706433``: what the C library reads as an IPv4 address.

    One to four numbers, each decimal, octal (a leading 0) or hexadecimal
    (0x); the last one fills the bytes the others leave. A resolver handed
    such a host connects to that address without asking any name server.
    """
    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or not all(_IPV4_PART.match(part) for part in parts):
        return None
    try:
        numbers = [int(part, 16 if part[:2].lower() == "0x" else 8 if len(part) > 1 and part[0] == "0" else 10) for part in parts]
    except ValueError:
        return None
    *leading, last = numbers
    if any(number > 0xFF for number in leading) or last >= 256 ** (4 - len(leading)):
        return None
    value = last
    for index, number in enumerate(leading):
        value += number << (8 * (3 - index))
    return ipaddress.IPv4Address(value)


def _address_of(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address ``host`` is when it is written as one, an IPv4 address inside an IPv6 one unwrapped."""
    text = host.strip("[]").split("%", 1)[0]
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(text)
    except ValueError:
        address = _old_style_ipv4(text)
    if address is None:
        return None
    return getattr(address, "ipv4_mapped", None) or address


def refused_target(url: Any) -> str | None:
    """What ``url`` points at when it is somewhere the SDK services never call, in words for a message; None when it may be called.

    The host is read the way httpx reads it, since httpx makes the request.
    An address httpx cannot read is not judged here: the request fails on it.
    """
    try:
        host = httpx.URL(str(url or "")).raw_host.decode("ascii").rstrip(".").lower()
    except Exception:  # noqa: BLE001 - not an address httpx would call
        return None
    if not host:
        return None
    if host in _LOCAL_NAMES or host.endswith(".localhost"):
        return TARGET_LOOPBACK
    if host in _METADATA_NAMES:
        return TARGET_METADATA
    address = _address_of(host)
    if address is None:
        return None
    if address.is_loopback:
        return TARGET_LOOPBACK
    if address.is_unspecified:
        return TARGET_UNSPECIFIED
    if address in _METADATA_ADDRESSES:
        return TARGET_METADATA
    if address.is_link_local:
        return TARGET_LINK_LOCAL
    return None


class OutboundClients:
    """The httpx clients of one call: the injected one, or one per way out.

    ``connector`` is the Proxy connector's name for what is being called
    (``llm``, ``jira``, ...). Two hosts of one call may leave differently (a
    NO_PROXY entry exempts one of them), so the way out is decided per URL,
    as the connector tests decide it. An injected client is used for
    everything and never closed here.
    """

    def __init__(
        self,
        config: dict | None,
        connector: str,
        *,
        injected: httpx.AsyncClient | None = None,
        instance_setting: str | None = None,
    ) -> None:
        self._config = config if isinstance(config, dict) else {}
        self._connector = connector
        self._injected = injected
        self._instance_setting = instance_setting
        self._clients: dict[tuple, httpx.AsyncClient] = {}

    def for_url(self, url: str) -> httpx.AsyncClient:
        if self._injected is not None:
            return self._injected
        choice, plan = connector_http.egress(self._config, self._connector, hostname_of(url), self._instance_setting)
        kwargs = connector_http.client_kwargs(choice, plan)
        key = (kwargs.get("proxy"), kwargs.get("trust_env", True))
        client = self._clients.get(key)
        if client is None:
            client = httpx.AsyncClient(follow_redirects=False, **kwargs)
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - a close that failed leaves nothing to do
                pass


def proxy_secrets(config: dict | None) -> list[str]:
    """The passwords of the member's proxies, as typed and as a proxy URL carries them."""
    section = config.get("proxy") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return []
    try:
        plan = build_proxy_plan(section)
    except Exception:  # noqa: BLE001 - a section the plan cannot read has no secrets to name
        return []
    found: list[str] = []
    for entry in plan.entries:
        if entry.password:
            found.extend([entry.password, quote(entry.password, safe="")])
    return found


def scrub(text: Any, secrets: Iterable[Any] = (), *, limit: int = 300) -> str:
    """``text`` on one line, without ``secrets`` or anything shaped like a credential, cut at ``limit``."""
    out = "" if text is None else str(text)
    known = {secret for secret in secrets if isinstance(secret, str) and len(secret) >= MIN_SECRET_LENGTH}
    for secret in sorted(known, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    out = " ".join(sanitize_exception_message(out).split())
    if len(out) > limit:
        out = out[:limit].rstrip() + "..."
    return out


def failure_text(exc: BaseException, *, host: str, secrets: Iterable[Any] = ()) -> str:
    """Why a request did not get an answer, in words safe to hand to a caller."""
    verification = describe_verification_failure(exc, host=host or "the server")
    if verification:
        return verification
    detail = scrub(str(exc), secrets, limit=200)
    name = type(exc).__name__
    return f"{name}: {detail}" if detail else name


_exchange_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]]" = weakref.WeakKeyDictionary()


def exchange_lock(key: str) -> asyncio.Lock:
    """One lock per credential and event loop, so a burst of calls exchanges a token once.

    An ``asyncio.Lock`` belongs to the loop that first waits on it; the
    Portal has one loop, the tests one per case, hence the per-loop table.
    A loop that cannot be weakly referenced gets a lock of its own each
    time: the exchange then merely happens more than once.
    """
    loop = asyncio.get_running_loop()
    try:
        per_loop = _exchange_locks.get(loop)
        if per_loop is None:
            per_loop = {}
            _exchange_locks[loop] = per_loop
    except TypeError:
        return asyncio.Lock()
    lock = per_loop.get(key)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[key] = lock
    return lock


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "OutboundClients",
    "REDACTED",
    "exchange_lock",
    "failure_text",
    "proxy_secrets",
    "refused_target",
    "scrub",
    "unlogged",
]
