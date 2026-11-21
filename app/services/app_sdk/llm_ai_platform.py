"""AI Platform, called the way the runtimes and the Portal's own Test button call it.

Ported from the native runtime (``efp_runtime/llm/provider.py``:
``AIPlatformHTTPTransport``, ``AIPlatformProvider``), the OpenCode adapter
(``ai_platform_proxy.py``) and ``RuntimeProfileTestService._test_ai_platform``:

1. The member's username and password are exchanged at the iB2B endpoint for
   a JWT that lives about thirty seconds; it is kept until five seconds
   before that and exchanged again on a 401 or 403.
2. The JWT travels in the trust-token header with two tracking ids. Which
   endpoint is called follows the deployment, as for a native assistant: the
   Responses path when ``AI_PLATFORM_RESPONSES_URI`` is set (the use case is
   then part of the URL and the JWT is also sent as a Bearer token),
   otherwise chat/completions (the use case is the body's ``user``).

``llm`` is the member's model provider section after
``materialize_ai_platform_llm_config``: the deployment's endpoints with the
member's credentials.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit

import httpx

from app.services.app_sdk._outbound import CONNECT_TIMEOUT_SECONDS, OutboundClients, exchange_lock
from app.services.app_sdk.llm_types import LlmGatewayError, LlmRequest, WireDone
from app.services.app_sdk.llm_wire import (
    CHAT_CAP_PARAMETER,
    RESPONSES_CAP_PARAMETER,
    cap_accepted,
    cap_refused,
    chat_body,
    error_body,
    read_chat,
    read_responses,
    responses_body,
    status_error,
    transport_error,
)
from app.services.proxy_plan import hostname_of

LABEL = "AI Platform"
# The provider's id, as the Portal names it and as the refused-cap warning names it.
PROVIDER = "ai_platform"
DEFAULT_CHAT_URI = "/v1/api/v1/chat/completions"
DEFAULT_TRUST_TOKEN_HEADER = "X-XXXX-E2E-Trust-Token"
DEFAULT_TRACKING_PREFIX = "EFP"
TOKEN_TTL_SECONDS = 30
TOKEN_REFRESH_MARGIN_SECONDS = 5

# The Portal's Test button caps its chat/completions probe with
# max_completion_tokens, so the gateway is known to take that key; the
# Responses name for it (max_output_tokens) is not proven there. Either is
# sent until the gateway refuses it by name: the request is then repeated once
# without it and it is left out for that endpoint for the rest of the process
# (llm_wire.cap_refused). A temperature is sent by neither runtime, and the
# gpt-5 line refuses one.
FORWARD_MAX_OUTPUT_TOKENS = True
FORWARD_TEMPERATURE = False
# A streamed chat completion reports usage only when asked to. Neither runtime
# asks (the native runtime sends no stream_options), so this key is not
# proven against the gateway; without it a streamed answer counts no tokens.
CHAT_STREAM_INCLUDE_USAGE = True

COMPLETION_TIMEOUT = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=120.0, write=30.0, pool=CONNECT_TIMEOUT_SECONDS)
EXCHANGE_TIMEOUT = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=30.0, write=30.0, pool=CONNECT_TIMEOUT_SECONDS)

_MAX_CACHED_TOKENS = 1024


@dataclass(frozen=True)
class AiPlatformTarget:
    """Where one member's AI Platform calls go and what they sign in with."""

    endpoint: str
    is_responses: bool
    ib2b_url: str
    username: str
    password: str = field(repr=False)
    usercase: str = ""
    trust_token_header: str = DEFAULT_TRUST_TOKEN_HEADER
    tracking_prefix: str = DEFAULT_TRACKING_PREFIX


# sha256 of (iB2B address, username, password) -> (JWT, the time it stops being used).
_tokens: dict[str, tuple[str, float]] = {}


def _now() -> float:
    return time.time()


def reset_token_cache() -> None:
    """Forget every exchanged JWT (tests)."""
    _tokens.clear()


def join_url(host: str, uri: str) -> str:
    host = str(host or "").rstrip("/")
    uri = str(uri or "").strip()
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    if uri and not uri.startswith("/"):
        uri = "/" + uri
    return host + uri


def endpoint_is_responses(endpoint: str) -> bool:
    path = urlsplit(str(endpoint or "")).path.rstrip("/").lower()
    return path.endswith("/responses") or path.endswith("/responses/compact")


def target_of(llm: Mapping[str, Any]) -> AiPlatformTarget:
    """The endpoint and credentials of a materialized ``llm`` section, or the reason there are none."""
    platform = _block(llm, "ai_platform")
    chat, responses = _block(platform, "chat"), _block(platform, "responses")
    ib2b, auth = _block(platform, "ib2b"), _block(platform, "auth")
    username = str(auth.get("username") or "").strip()
    password = str(auth.get("password") or "").strip()
    usercase = str(auth.get("usercase") or "").strip()
    if not (username and password and usercase):
        raise LlmGatewayError(
            "provider_auth_failed",
            "AI Platform is not connected: the owner's Model provider connector needs a username, a password and a usercase.",
        )
    # A configured Responses path wins, as it does for a native assistant.
    endpoint_config = responses if str(responses.get("uri") or "").strip() else chat
    host = str(endpoint_config.get("host") or chat.get("host") or "").strip()
    if not host:
        raise LlmGatewayError("provider_error", "The AI Platform chat host is not configured in the Portal.")
    ib2b_host, ib2b_uri = str(ib2b.get("host") or "").strip(), str(ib2b.get("uri") or "").strip()
    if not (ib2b_host and ib2b_uri):
        raise LlmGatewayError("provider_error", "The AI Platform iB2B host and URI are not configured in the Portal.")
    endpoint = join_url(host, str(endpoint_config.get("uri") or DEFAULT_CHAT_URI))
    return AiPlatformTarget(
        endpoint=endpoint,
        is_responses=endpoint_is_responses(endpoint),
        ib2b_url=join_url(ib2b_host, ib2b_uri),
        username=username,
        password=password,
        usercase=usercase,
        trust_token_header=str(auth.get("trust_token_header") or "").strip() or DEFAULT_TRUST_TOKEN_HEADER,
        tracking_prefix=str(auth.get("tracking_prefix") or "").strip() or DEFAULT_TRACKING_PREFIX,
    )


def request_headers(target: AiPlatformTarget, token: str, *, stream: bool) -> dict[str, str]:
    """The headers of a model call (AIPlatformHTTPTransport._headers)."""
    tracking = "{0}-{1}".format(target.tracking_prefix, time.strftime("%Y%m%d%H%M%S", time.gmtime(_now())))
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        target.trust_token_header: token,
        "x-correlation-id": tracking,
        "x-usersession-id": tracking,
    }
    if target.is_responses:
        # The Responses gateway also expects the JWT as a Bearer token.
        headers["Authorization"] = f"Bearer {token}"
    return headers


def cap_parameter(target: AiPlatformTarget) -> str:
    """The name the endpoint gives the cap on the answer's length."""
    return RESPONSES_CAP_PARAMETER if target.is_responses else CHAT_CAP_PARAMETER


def request_body(target: AiPlatformTarget, request: LlmRequest, *, stream: bool) -> dict:
    forward_cap = FORWARD_MAX_OUTPUT_TOKENS and cap_accepted(target.endpoint, cap_parameter(target))
    if target.is_responses:
        # Streamed like Copilot's; the use case is in the URL, never a ``user`` field.
        return responses_body(
            request,
            forward_max_output_tokens=forward_cap,
            forward_temperature=FORWARD_TEMPERATURE,
        )
    return chat_body(
        request,
        stream=stream,
        user=target.usercase,
        include_usage=CHAT_STREAM_INCLUDE_USAGE,
        forward_max_output_tokens=forward_cap,
        forward_temperature=FORWARD_TEMPERATURE,
    )


async def run(
    llm: Mapping[str, Any],
    request: LlmRequest,
    clients: OutboundClients,
    *,
    streaming: bool,
) -> AsyncIterator[str | WireDone]:
    """One completion: the text deltas, then how it ended.

    A Responses endpoint is always streamed (its final event carries the
    usage). chat/completions is streamed only when the caller wants deltas:
    the plain call is the one the Test button proves and its JSON answer
    always carries the usage.
    """
    target = target_of(llm)
    stream = True if target.is_responses else streaming
    body = request_body(target, request, stream=stream)
    token = await _token(target, clients)
    secrets = [target.password, token]
    cap = cap_parameter(target)
    token_renewed = False

    # At most three requests: the first, one more with a new JWT, one more
    # without the output cap.
    while True:
        failure: LlmGatewayError | None = None
        try:
            async with clients.for_url(target.endpoint).stream(
                "POST",
                target.endpoint,
                headers=request_headers(target, token, stream=stream),
                json=body,
                timeout=COMPLETION_TIMEOUT,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    text = await error_body(response)
                    if response.status_code in (401, 403):
                        _forget(target, token)
                        if not token_renewed:
                            token_renewed = True
                            token = await _token(target, clients)
                            secrets.append(token)
                            continue
                    if cap in body and cap_refused(target.endpoint, cap, response.status_code, text, provider=PROVIDER):
                        # The gateway does not take the cap: once more without it.
                        body = {key: value for key, value in body.items() if key != cap}
                        continue
                    raise status_error(LABEL, response.status_code, text, response.headers, secrets)
                reader = read_responses if target.is_responses else read_chat
                async for item in reader(response, LABEL):
                    yield item
                return
        except LlmGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001 - every transport failure becomes a gateway error
            failure = transport_error(LABEL, exc, host=hostname_of(target.endpoint), secrets=secrets)
        if failure is not None:
            # Raised out here so the error does not drag the failed request,
            # and the token in its headers, along as its context.
            raise failure


# ---------------------------------------------------------------- token exchange


def _block(source: Any, key: str) -> Mapping[str, Any]:
    value = source.get(key) if isinstance(source, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _fingerprint(target: AiPlatformTarget) -> str:
    material = "\x00".join([target.ib2b_url, target.username, target.password])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _fresh(entry: tuple[str, float] | None) -> bool:
    return entry is not None and entry[1] - _now() >= TOKEN_REFRESH_MARGIN_SECONDS


def _forget(target: AiPlatformTarget, token: str) -> None:
    key = _fingerprint(target)
    entry = _tokens.get(key)
    if entry is not None and entry[0] == token:
        _tokens.pop(key, None)


def _remember(key: str, token: str) -> None:
    if len(_tokens) >= _MAX_CACHED_TOKENS:
        for stale in [name for name, value in _tokens.items() if not _fresh(value)]:
            _tokens.pop(stale, None)
        while len(_tokens) >= _MAX_CACHED_TOKENS:
            _tokens.pop(next(iter(_tokens)))
    _tokens[key] = (token, _now() + TOKEN_TTL_SECONDS)


async def _token(target: AiPlatformTarget, clients: OutboundClients) -> str:
    """The JWT for a member: the kept one while it has five seconds left, else a new exchange."""
    key = _fingerprint(target)
    cached = _tokens.get(key)
    if _fresh(cached):
        return cached[0]  # type: ignore[index]
    async with exchange_lock("ai_platform:" + key):
        cached = _tokens.get(key)
        if _fresh(cached):
            return cached[0]  # type: ignore[index]
        token = await _exchange(target, clients)
        _remember(key, token)
        return token


async def _exchange(target: AiPlatformTarget, clients: OutboundClients) -> str:
    label = "AI Platform sign-in"
    secrets = [target.password]
    payload = {
        "input_token_state": {"token_type": "CREDENTIAL", "username": target.username, "password": target.password},
        "output_token_state": {"token_type": "JWT"},
    }
    response: httpx.Response | None = None
    failure: LlmGatewayError | None = None
    try:
        response = await clients.for_url(target.ib2b_url).post(
            target.ib2b_url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=EXCHANGE_TIMEOUT,
            follow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001
        # The failed request carries the password in its body: the error is
        # raised below, without it as context.
        failure = transport_error(label, exc, host=hostname_of(target.ib2b_url), secrets=secrets)
    if failure is not None or response is None:
        raise failure or LlmGatewayError("provider_error", f"{label} gave no answer.")

    status = response.status_code
    if status in (400, 401, 403):
        # The request is the same for everyone; only the credentials in it can be wrong.
        raise LlmGatewayError(
            "provider_auth_failed",
            f"AI Platform did not accept the owner's username and password (HTTP {status}). "
            "They have to be corrected in the Model provider connector.",
        )
    if not 200 <= status < 300:
        raise status_error(label, status, response.text[:4096], response.headers, secrets)
    try:
        data = json.loads(response.text)
    except ValueError:
        raise LlmGatewayError("provider_error", f"{label} answered with something that is not JSON.") from None
    token = str(data.get("issued_token") or "").strip() if isinstance(data, Mapping) else ""
    if not token:
        raise LlmGatewayError("provider_error", f"{label} did not return issued_token.")
    return token


__all__ = [
    "AiPlatformTarget",
    "CHAT_STREAM_INCLUDE_USAGE",
    "DEFAULT_CHAT_URI",
    "FORWARD_MAX_OUTPUT_TOKENS",
    "FORWARD_TEMPERATURE",
    "LABEL",
    "PROVIDER",
    "TOKEN_REFRESH_MARGIN_SECONDS",
    "TOKEN_TTL_SECONDS",
    "cap_parameter",
    "endpoint_is_responses",
    "join_url",
    "request_body",
    "request_headers",
    "reset_token_cache",
    "run",
    "target_of",
]
