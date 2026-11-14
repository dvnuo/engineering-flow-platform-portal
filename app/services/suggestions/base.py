"""What a picker source returns, and what it is given to work with.

A source answers one question about one member -- "which branches did I push
lately", "which issues am I looking at" -- with a few grouped rows. A row has
two halves that must not be confused: ``value`` is the identifier that goes
into the prompt (an issue key, a pull request URL), everything else is only
shown. Titles and summaries come from systems other people write into, so they
stay on the display side and never reach the model through a picker.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from app.services import connector_http
from app.services.proxy_plan import hostname_of
from app.services.tls_trust import describe_verification_failure

SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")

STATE_OK = "ok"
STATE_NOT_CONFIGURED = "not_configured"
STATE_UNAVAILABLE = "unavailable"

IDENTITY_PERSONAL = "personal"
IDENTITY_SHARED = "shared"
IDENTITY_UNKNOWN = "unknown"

# One request to a connected system. A picker is something a member waits on
# with their hand on the mouse, so this is far below the Test button's budget.
REQUEST_TIMEOUT_SECONDS = 4.0
# A whole source, however many requests it fans out to.
SOURCE_TIMEOUT_SECONDS = 8.0
# Connections kept open to one way out while a source is being answered.
POOL_CONNECTIONS = 4

MAX_QUERY_CHARS = 200

# httpx logs every request it sends, address and query string included. For a
# picker that address carries what the member typed and who they are (a JQL
# with their search words, their username), and none of that belongs in the
# Portal's log. Requests made through ConnectorHttp are therefore left out of
# httpx's own log; the source's outcome is logged once, without the query
# (app/services/suggestions/__init__.py).
_unlogged_request: contextvars.ContextVar[bool] = contextvars.ContextVar("suggestions_unlogged_request", default=False)


class _UnloggedRequests(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _unlogged_request.get()


logging.getLogger("httpx").addFilter(_UnloggedRequests())
# A picker is a short list; a source that could return hundreds (every Jira
# project, every path of a repository) is narrowed by typing, not by scrolling.
MAX_ITEMS_PER_GROUP = 30


class SourceNotConfigured(Exception):
    """The connector this source reads is off or has no credentials."""

    def __init__(self, connector: str) -> None:
        super().__init__(connector)
        self.connector = connector


class SourceUnavailable(Exception):
    """The system did not answer in a usable way. ``reason`` is a short code
    the page turns into words; it never carries the server's text, a URL or a
    credential."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Item:
    value: str
    title: str
    subtitle: str = ""
    # An ISO-8601 instant the page shows as "2 hours ago", or "".
    meta: str = ""
    # Data from the system, shown as is: a Jira status, "#531".
    badge: str = ""
    # A state from a fixed list the page translates: open, draft, failed,
    # unstable, ahead, uncommitted, and the task states.
    status: str = ""
    count: int | None = None
    url: str = ""
    # Named values a form can hand to its other fields (repo, branch, base).
    fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"value": self.value, "title": self.title}
        for key in ("subtitle", "meta", "badge", "status", "url"):
            text = getattr(self, key)
            if text:
                payload[key] = text
        if self.count is not None:
            payload["count"] = self.count
        if self.fields:
            payload["fields"] = dict(self.fields)
        return payload


@dataclass
class Group:
    # A fixed id the page has words for: viewed, assigned, reported,
    # review_requested, mine, no_pr, has_pr, pushed, workspace, planned,
    # recent, all, accounts, pr_checks, jenkins, failed, results.
    id: str
    items: list[Item] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "items": [item.to_dict() for item in self.items[:MAX_ITEMS_PER_GROUP]]}


@dataclass
class Result:
    source: str
    state: str = STATE_OK
    groups: list[Group] = field(default_factory=list)
    connector: str = ""
    reason: str = ""
    identity: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "state": self.state,
            "groups": [group.to_dict() for group in self.groups if group.items],
        }
        if self.connector:
            payload["connector"] = self.connector
        if self.reason:
            payload["reason"] = self.reason
        if self.identity:
            payload["identity"] = self.identity
        return payload


class ConnectorHttp:
    """Requests to a member's connected systems, along the path their
    assistant would take (app/services/connector_http.py).

    ``transport`` is for tests: with one set, nothing leaves the process, and
    ``egress_log`` still records which way each request would have gone.
    """

    def __init__(
        self,
        config: dict,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config if isinstance(config, dict) else {}
        self.timeout = timeout
        self.transport = transport
        self.egress_log: list[tuple[str, str, str]] = []
        self._clients: dict[tuple, httpx.AsyncClient] = {}

    def _client(self, kwargs: dict) -> httpx.AsyncClient:
        """One client per way out, kept until ``aclose``.

        A source asks the same system several times in a row. A connection
        made for each request spends three round trips (TCP, the proxy, TLS)
        before the request itself leaves, which on a slow link is most of
        the wait; a kept connection spends one. The pool is small on
        purpose: six requests over four warm connections finish sooner than
        six cold ones side by side.
        """
        key = (kwargs.get("proxy"), kwargs.get("trust_env", True))
        client = self._clients.get(key)
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=POOL_CONNECTIONS, max_keepalive_connections=POOL_CONNECTIONS),
                **kwargs,
            )
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        """Let the kept connections go. Safe to call twice, and a request made
        afterwards simply opens a new one."""
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - nothing useful to do about a close that failed
                pass

    async def request(
        self,
        connector: str,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        params: Any = None,
        json_body: Any = None,
        instance_setting: str | None = None,
    ) -> httpx.Response:
        host = hostname_of(url)
        choice, plan = connector_http.egress(self.config, connector, host, instance_setting)
        self.egress_log.append((connector, host, choice.kind))
        kwargs = connector_http.client_kwargs(choice, plan)
        if self.transport is not None:
            kwargs = {"transport": self.transport}
        unlogged = _unlogged_request.set(True)
        try:
            response = await self._client(kwargs).request(method, url, headers=headers, params=params, json=json_body)
        except httpx.TimeoutException as exc:
            raise SourceUnavailable("timeout") from exc
        except Exception as exc:
            if describe_verification_failure(exc, host=host or url):
                raise SourceUnavailable("tls") from exc
            raise SourceUnavailable("unreachable") from exc
        finally:
            _unlogged_request.reset(unlogged)

        status = response.status_code
        if status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
            raise SourceUnavailable("rate_limited")
        if status in (401, 403):
            raise SourceUnavailable("unauthorized")
        if status >= 500:
            raise SourceUnavailable("error")
        # A sign-in page answered instead of the API: the address or the
        # credential is wrong, and following the redirect would only fetch HTML.
        if 300 <= status < 400:
            raise SourceUnavailable("unauthorized")
        return response

    async def get(self, connector: str, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request(connector, "GET", url, **kwargs)


def json_of(response: httpx.Response) -> Any:
    """The parsed body, or None for anything that is not JSON."""
    try:
        return response.json()
    except Exception:
        return None


RuntimeGet = Callable[[str, list[tuple[str, str]]], Awaitable[tuple[int, Any]]]


@dataclass
class SourceContext:
    """One member's request to one source."""

    user: Any
    config: dict
    http: ConnectorHttp
    db: Any = None
    settings: Any = None
    revision: int = 0
    q: str = ""
    params: dict[str, list[str]] = field(default_factory=dict)
    # Values the member used with this card before, newest first.
    recent: list[str] = field(default_factory=list)
    agent: Any = None
    # GET on the selected assistant's runtime, or None when no assistant was
    # named, it is not running, or the member may not see it.
    runtime_get: RuntimeGet | None = None

    def param(self, name: str) -> str:
        values = self.params.get(name) or []
        return values[0] if values else ""

    def param_list(self, name: str) -> list[str]:
        return list(self.params.get(name) or [])


async def gather_groups(*loaders: Awaitable[Group | None]) -> list[Group]:
    """Run group queries side by side and keep the ones that answered.

    One group failing (a JQL function this server lacks, a slow search) must
    not cost the member the others, so a failure here is a missing group.
    When nothing answered and something failed, the source as a whole is
    unavailable and the first reason is raised, so an outage is not shown as
    "you have no recent work".
    """
    results = await asyncio.gather(*loaders, return_exceptions=True)
    groups: list[Group] = []
    failures: list[SourceUnavailable] = []
    for result in results:
        if isinstance(result, SourceUnavailable):
            failures.append(result)
        elif isinstance(result, SourceNotConfigured):
            raise result
        elif isinstance(result, BaseException):
            failures.append(SourceUnavailable("error"))
        elif result is not None and result.items:
            groups.append(result)
    if failures and not groups:
        raise failures[0]
    return groups


def search_words(text: str, *, limit: int = 6) -> list[str]:
    """The words of a typed search, safe to put inside a quoted JQL or CQL
    string: letters, digits and underscores only, so no quote, backslash or
    query operator survives."""
    return re.findall(r"\w+", text or "", flags=re.UNICODE)[:limit]
