"""One HTTP request to one of a member's connectors, signed with the connector's credentials.

``config`` is the member's effective settings
(``RuntimeProfileService.effective_config_for(profile)``). The address and the
Authorization header of each connector are read exactly the way the
connector's Test button reads them (``runtime_profile_test_service.py`` and
``connector_http.py``), and the request leaves through the proxy the Proxy
connector assigns that connector, verified against the Portal's CA trust.

The caller chooses a method, a path below the connector's address, a query, a
few headers and a body; it can never choose the host, the credentials or
where a redirect leads. The connector's account is a shared service account,
not the member: nothing here says who "me" is. The address itself is the
owner's setting; one that means the Portal's own machine or a link-local
address is never called (``_outbound.refused_target``), and a row the owner
switched off is never used.

Nothing here knows about apps, tokens or the database; the caller decides
whether the app may use the connector and whether it may write
(``allow_write``), and turns ``ConnectorProxyError`` into the response of
docs/CONTRACTS.md section 9.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

import httpx

from app.services import connector_http
from app.services.app_sdk._outbound import (
    CONNECT_TIMEOUT_SECONDS,
    MIN_SECRET_LENGTH,
    OutboundClients,
    REDACTED,
    failure_text,
    proxy_secrets,
    refused_target,
    unlogged,
)

SUPPORTED = ("jira", "confluence", "github", "jenkins", "nexus", "splunk")

READ_METHODS = frozenset({"GET", "HEAD"})
METHODS = READ_METHODS | frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_BODY_BYTES = 5 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 60.0
MAX_PATH_LENGTH = 8192
GITHUB_DEFAULT_API_BASE = "https://api.github.com"

# The codes and statuses of docs/CONTRACTS.md section 9 that a request can end in.
ERROR_STATUS: dict[str, int] = {
    "invalid_request": 400,
    "invalid_path": 400,
    "method_not_allowed": 403,
    "connector_not_configured": 424,
    "upstream_error": 502,
    "upstream_timeout": 504,
}

# Request headers. Accept and Content-Type always travel; an X- header does
# unless it is one a server may read as who is calling, as a credential, or
# as an instruction to treat the request as another method, host or path.
_ALWAYS_FORWARDED = frozenset({"accept", "content-type"})
# Atlassian's XSRF opt-out ("no-check"), needed for uploads; not a credential.
_X_ALLOWED = frozenset({"x-atlassian-token"})
_X_BLOCKED = frozenset({"x-http-method", "x-real-ip", "x-client-ip", "x-host", "x-scheme"})
_X_BLOCKED_PREFIXES = ("x-forwarded", "x-original-", "x-rewrite-", "x-envoy-", "x-amz-", "x-amzn-")
_X_BLOCKED_WORDS = (
    "auth",
    "token",
    "secret",
    "passw",
    "credential",
    "cookie",
    "session",
    "key",
    "otp",
    "jwt",
    "signature",
    "user",
    "sudo",
    "impersonat",
    "remote",
    "sso",
    "csrf",
    "xsrf",
    "crumb",
    "override",
)
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")

# Response headers. Everything else stays behind, Set-Cookie and anything
# that names or echoes the connector's account included.
_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-disposition",
        "content-range",
        "etag",
        "last-modified",
        "link",
        "retry-after",
        "x-total",
        "x-total-count",
        "x-total-pages",
        "x-page",
        "x-per-page",
        "x-next-page",
        "x-prev-page",
        # Jenkins' progressive console output.
        "x-text-size",
        "x-more-data",
    }
)
_RESPONSE_HEADER_PREFIXES = ("x-ratelimit-", "x-rate-limit-")
# A credential shorter than this is not looked for in a response body: it
# could not be told from ordinary content.
_BODY_SECRET_MIN_LENGTH = 8
_MAX_DECODE_ROUNDS = 5


class ConnectorProxyError(Exception):
    """A request that was not made, or got no answer: ``code`` and ``status`` are those of CONTRACTS section 9.

    An upstream that answered, with whatever status, is not an error. The
    message never carries a credential.
    """

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else ERROR_STATUS.get(code, 502)


@dataclass(frozen=True)
class _Target:
    type: str
    base_url: str
    auth_headers: dict[str, str] = field(default_factory=dict, repr=False)
    # The instance row's own proxy setting, when it has one.
    instance_proxy: str | None = None
    secrets: tuple[str, ...] = field(default=(), repr=False)


# ---------------------------------------------------------------- public


def describe(config: dict, connector_type: str) -> dict | None:
    """``{"type": ..., "base_url": ...}`` of a connector the owner has set up, else None. Never a credential."""
    kind = str(connector_type or "").strip().lower()
    if kind not in SUPPORTED:
        return None
    target = _resolve(config, kind)
    # A connector whose address is never called (see _outbound.refused_target)
    # is not offered to the app either.
    if target is None or refused_target(target.base_url) is not None:
        return None
    return {"type": kind, "base_url": target.base_url}


async def request(
    *,
    config: dict,
    connector_type: str,
    method: str,
    path: str,
    query: dict | None = None,
    headers: dict | None = None,
    json_body: Any = None,
    body: bytes | None = None,
    allow_write: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Make the request and return the response object of CONTRACTS section 9.3.

    ``{"status", "headers", "json", "body_base64", "truncated"}``: the
    upstream's own status (3xx included, redirects are never followed), a
    few of its headers, a JSON body in ``json`` and anything else in
    ``body_base64``, cut at 5 MiB. Raises ``ConnectorProxyError``.
    """
    kind = str(connector_type or "").strip().lower() if isinstance(connector_type, str) else ""
    if kind not in SUPPORTED:
        raise ConnectorProxyError("invalid_request", "connector type must be one of: " + ", ".join(SUPPORTED) + ".")
    verb = _checked_method(method, allow_write)
    raw_path, inline_query = _checked_path(path)
    query_text = _query_text(inline_query, query)
    content, content_type = _content(json_body, body)

    target = _resolve(config, kind)
    if target is None:
        raise ConnectorProxyError(
            "connector_not_configured",
            f"The owner's {kind} connector is not set up: it needs to be on, with an address and credentials.",
        )
    off_limits = refused_target(target.base_url)
    if off_limits is not None:
        # The address is the owner's setting, so this is theirs to correct,
        # like a connector that is not set up.
        raise ConnectorProxyError(
            "connector_not_configured",
            f"The owner's {kind} connector points at {off_limits}, which apps may not call. "
            "The owner has to give the connector the address of the system itself.",
        )
    url = _target_url(target, raw_path, query_text)
    send_headers = _request_headers(headers, target.auth_headers, default_content_type=content_type)
    secrets = [*target.secrets, *proxy_secrets(config)]

    clients = OutboundClients(config, kind, injected=client, instance_setting=target.instance_proxy)
    try:
        with unlogged():
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                async with clients.for_url(str(url)).stream(
                    verb,
                    url,
                    headers=send_headers,
                    content=content,
                    timeout=httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
                    follow_redirects=False,
                ) as response:
                    raw, truncated = await _read_capped(response, verb)
                    return _result(response, raw, truncated, target, url)
    except ConnectorProxyError:
        raise
    except (httpx.TimeoutException, TimeoutError):
        failure = ConnectorProxyError("upstream_timeout", f"The {kind} connector did not answer in time.")
    except Exception as exc:  # noqa: BLE001 - every transport failure becomes an upstream error
        reason = failure_text(exc, host=url.host, secrets=secrets)
        failure = ConnectorProxyError("upstream_error", f"The {kind} connector could not be reached: {reason}")
    finally:
        await clients.aclose()
    # Raised out here so the error does not drag the failed request, and the
    # Authorization header in it, along as its context.
    raise failure


# ---------------------------------------------------------------- the connector


def _resolve(config: Any, kind: str) -> _Target | None:
    """The address and sign-in of a connector, read the way its Test button reads them; None when it is not set up."""
    section = config.get(kind) if isinstance(config, dict) else None
    if not isinstance(section, dict) or not section.get("enabled"):
        return None

    if kind == "github":
        token = str(section.get("api_token") or "").strip()
        base_url = _clean_base_url(section.get("base_url") or GITHUB_DEFAULT_API_BASE)
        if not token or not base_url:
            return None
        return _target(kind, base_url, {"Authorization": f"Bearer {token}"}, None, [token])

    if kind in ("jira", "confluence"):
        # A row that is switched off is not the assistant's either.
        rows = [row for row in (section.get("instances") or []) if isinstance(row, dict) and row.get("enabled") is not False]
        instance = connector_http.first_auth_instance(rows)
        if instance is None:
            return None
        auth = connector_http.instance_auth_headers(instance)
        if not auth:
            return None
    else:
        instance = connector_http.default_instance(section)
        # With no row switched on, default_instance still answers the first
        # row (the Test button names it). A row that is off is not the
        # assistant's and not an app's: its credentials are never sent.
        if instance is None or instance.get("enabled") is False:
            return None
        username = str(instance.get("username") or "").strip()
        token = str(instance.get("token") or "").strip()
        password = str(instance.get("password") or "").strip()
        if kind == "splunk":
            if token:
                auth = {"Authorization": f"Bearer {token}"}
            elif username and password:
                auth = connector_http.basic_auth_header(username, password)
            else:
                return None
        else:
            # Jenkins and Nexus may be read anonymously: no credential, no header.
            secret = token or password
            auth = connector_http.basic_auth_header(username, secret) if username and secret else {}

    base_url = _clean_base_url(instance.get("url"))
    if not base_url:
        return None
    setting = instance.get("proxy")
    return _target(
        kind,
        base_url,
        auth,
        str(setting).strip() or None if setting is not None else None,
        [str(instance.get("token") or "").strip(), str(instance.get("password") or "").strip()],
    )


def _target(kind: str, base_url: str, auth: dict[str, str], instance_proxy: str | None, secrets: list[str]) -> _Target:
    known = [secret for secret in secrets if secret]
    for value in auth.values():
        known.append(value)
        # "Basic <base64>" / "Bearer <token>": the part after the scheme is the credential.
        known.append(value.split(" ", 1)[-1])
    return _Target(kind, base_url, dict(auth), instance_proxy, tuple(dict.fromkeys(known)))


def _clean_base_url(raw: Any) -> str | None:
    """An http(s) address without a trailing slash, a query or a login in it; None for anything else."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = parts.hostname or ""
    if scheme not in ("http", "https") or not host:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), "", ""))


# ---------------------------------------------------------------- the request


def _checked_method(method: Any, allow_write: bool) -> str:
    verb = method.strip().upper() if isinstance(method, str) else ""
    if verb not in METHODS:
        raise ConnectorProxyError("method_not_allowed", "method must be GET, HEAD, POST, PUT, PATCH or DELETE.")
    if verb not in READ_METHODS and not allow_write:
        raise ConnectorProxyError("method_not_allowed", f"{verb} is not allowed: this connector is read-only for the app.")
    return verb


def _is_control(char: str) -> bool:
    return unicodedata.category(char) == "Cc"


def _invalid_path(reason: str) -> ConnectorProxyError:
    return ConnectorProxyError("invalid_path", f"path {reason}.")


def _checked_path(path: Any) -> tuple[str, str]:
    """``(path, query written into it)`` of a caller's path, or ``invalid_path``.

    The path must start with one "/" and stay a path: no scheme, no host, no
    backslash, no control character, no fragment and no ".." segment, in the
    text as given and in every layer of percent-decoding (a server behind a
    proxy may decode twice).
    """
    if not isinstance(path, str) or not path.startswith("/"):
        raise _invalid_path('must start with "/"')
    if len(path) > MAX_PATH_LENGTH:
        raise _invalid_path("is too long")
    if "#" in path:
        raise _invalid_path("must not carry a fragment")
    if "\\" in path or any(_is_control(char) for char in path):
        raise _invalid_path("must not contain a backslash or a control character")
    raw_path, _, inline_query = path.partition("?")

    layer = raw_path
    for _ in range(_MAX_DECODE_ROUNDS):
        _check_path_layer(layer)
        decoded = unquote(layer)
        if decoded == layer:
            return raw_path, inline_query
        layer = decoded
    _check_path_layer(layer)
    if unquote(layer) != layer:
        raise _invalid_path("is percent-encoded too many times")
    return raw_path, inline_query


def _check_path_layer(text: str) -> None:
    if "\\" in text or any(_is_control(char) for char in text):
        raise _invalid_path("must not contain a backslash or a control character")
    forms = [text]
    if not text.isascii():
        # Full-width dots and slashes are ".." and "/" to a server that
        # normalizes what it decoded.
        forms.append(unicodedata.normalize("NFKC", text))
    for form in forms:
        if form.startswith("//"):
            raise _invalid_path("must not name a host")
        for segment in re.split(r"[/\\]", form):
            # "..;x" is ".." to a server that strips path parameters (Tomcat).
            if segment.split(";", 1)[0] == "..":
                raise _invalid_path('must not contain a ".." segment')


def _query_text(inline_query: str, query: Any) -> str:
    parts = [inline_query] if inline_query else []
    if query is not None:
        if not isinstance(query, dict):
            raise ConnectorProxyError("invalid_request", "query must be an object.")
        pairs: list[tuple[str, str]] = []
        for key, value in query.items():
            if not isinstance(key, str) or not key:
                raise ConnectorProxyError("invalid_request", "query names must be non-empty text.")
            for item in value if isinstance(value, (list, tuple)) else [value]:
                if item is None:
                    continue
                if isinstance(item, bool):
                    pairs.append((key, "true" if item else "false"))
                elif isinstance(item, (str, int, float)):
                    pairs.append((key, str(item)))
                else:
                    raise ConnectorProxyError(
                        "invalid_request", "query values must be text, numbers, booleans or lists of those."
                    )
        if pairs:
            parts.append(urlencode(pairs, quote_via=quote))
    return "&".join(parts)


def _content(json_body: Any, body: Any) -> tuple[bytes | None, str | None]:
    """The bytes to send and the Content-Type they get when the caller names none."""
    if json_body is not None and body is not None:
        raise ConnectorProxyError("invalid_request", "send a JSON body or a raw body, not both.")
    if json_body is not None:
        try:
            return json.dumps(json_body, allow_nan=False).encode("utf-8"), "application/json"
        except (TypeError, ValueError):
            raise ConnectorProxyError("invalid_request", "the JSON body cannot be written as JSON.") from None
    if body is not None:
        if not isinstance(body, (bytes, bytearray)):
            raise ConnectorProxyError("invalid_request", "the raw body must be bytes.")
        return bytes(body), None
    return None, None


def _target_url(target: _Target, raw_path: str, query_text: str) -> httpx.URL:
    """The address the request goes to: the connector's address with the path joined on, checked after joining."""
    text = target.base_url + raw_path + (f"?{query_text}" if query_text else "")
    try:
        url, base = httpx.URL(text), httpx.URL(target.base_url)
    except Exception:  # noqa: BLE001 - httpx refuses what is not a URL
        raise _invalid_path("is not a valid path") from None
    prefix = base.path.rstrip("/")
    inside = not prefix or url.path == prefix or url.path.startswith(prefix + "/")
    if not (_same_origin(url, base) and inside):
        raise _invalid_path("leaves the connector's address")
    return url


def _same_origin(url: httpx.URL, base: httpx.URL) -> bool:
    return url.scheme == base.scheme and url.host == base.host and url.port == base.port and not url.userinfo


def _may_forward(name: str) -> bool:
    if name in _ALWAYS_FORWARDED:
        return True
    if not name.startswith("x-"):
        return False
    if name in _X_ALLOWED:
        return True
    if name in _X_BLOCKED or name.startswith(_X_BLOCKED_PREFIXES):
        return False
    return not any(word in name for word in _X_BLOCKED_WORDS)


def _request_headers(headers: Any, auth_headers: dict[str, str], *, default_content_type: str | None = None) -> dict[str, str]:
    """The caller's forwardable headers, then the connector's own: what signs the request is never the caller's."""
    if headers is not None and not isinstance(headers, dict):
        raise ConnectorProxyError("invalid_request", "headers must be an object.")
    reserved = {name.lower() for name in auth_headers}
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        lowered = name.strip().lower() if isinstance(name, str) else ""
        if not lowered or lowered in reserved or not _may_forward(lowered):
            continue
        # A header travels as ASCII: httpx refuses anything else when it sends,
        # which would read as the connector being unreachable.
        if (
            not _HEADER_NAME.match(lowered)
            or not isinstance(value, str)
            or not value.isascii()
            or any(_is_control(char) for char in value)
        ):
            raise ConnectorProxyError("invalid_request", f"the {lowered[:60]} header must be plain ASCII text.")
        out[lowered] = value.strip()
    if default_content_type and "content-type" not in out:
        out["content-type"] = default_content_type
    out.update(auth_headers)
    return out


# ---------------------------------------------------------------- the response


async def _read_capped(response: httpx.Response, verb: str) -> tuple[bytes, bool]:
    """The body up to the cap, and whether there was more."""
    if verb == "HEAD":
        return b"", False
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        room = MAX_BODY_BYTES - size
        if len(chunk) > room:
            chunks.append(chunk[:room])
            return b"".join(chunks), True
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks), False


def _result(response: httpx.Response, raw: bytes, truncated: bool, target: _Target, url: httpx.URL) -> dict:
    for secret in target.secrets:
        if len(secret) >= _BODY_SECRET_MIN_LENGTH:
            raw = raw.replace(secret.encode("utf-8"), REDACTED.encode("ascii"))
    headers = _response_headers(response, target, url)
    parsed: Any = None
    encoded: str | None = None
    if raw:
        is_json = not truncated and _is_json_type(headers.get("content-type", ""))
        if is_json:
            try:
                parsed = json.loads(raw.decode("utf-8-sig"), parse_constant=_not_json)
            except (UnicodeDecodeError, ValueError):
                is_json = False
        if not is_json:
            encoded = base64.b64encode(raw).decode("ascii")
    return {
        "status": response.status_code,
        "headers": headers,
        "json": parsed,
        "body_base64": encoded,
        "truncated": truncated,
    }


def _not_json(token: str) -> Any:
    # NaN and Infinity are not JSON; a body with them goes back as bytes.
    raise ValueError(token)


def _is_json_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type in ("application/json", "text/json") or media_type.endswith("+json")


def _response_headers(response: httpx.Response, target: _Target, url: httpx.URL) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in response.headers.items():
        lowered = name.lower()
        if lowered not in _RESPONSE_HEADERS and not lowered.startswith(_RESPONSE_HEADER_PREFIXES):
            continue
        if _carries_secret(value, target):
            continue
        out[lowered] = value
    # Where a redirect leads is told only when it stays on the connector: a
    # link to another host is often a signed download address.
    location = response.headers.get("location")
    if location and 300 <= response.status_code < 400 and not _carries_secret(location, target):
        try:
            there = url.join(location)
        except Exception:  # noqa: BLE001 - an address httpx cannot read is not passed on
            there = None
        if there is not None and _same_origin(there, httpx.URL(target.base_url)):
            out["location"] = location
    return out


def _carries_secret(value: str, target: _Target) -> bool:
    return any(len(secret) >= MIN_SECRET_LENGTH and secret in value for secret in target.secrets)


__all__ = [
    "ConnectorProxyError",
    "ERROR_STATUS",
    "MAX_BODY_BYTES",
    "METHODS",
    "READ_METHODS",
    "SUPPORTED",
    "TOTAL_TIMEOUT_SECONDS",
    "describe",
    "request",
]
