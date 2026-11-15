"""GitHub Copilot, called the way the runtimes call it.

Ported from the native runtime (``efp_runtime/llm/provider.py``:
``GitHubCopilotHTTPTransport``, ``exchange_github_token_for_copilot_token``,
``_sanitize_copilot_responses_payload``) and the OpenCode adapter
(``copilot_plugin_auth.py``, ``copilot_proxy.py``):

1. The member's GitHub token (``llm.api_key``, a ``ghu_`` token from the
   device flow) is exchanged at ``GET https://api.github.com/copilot_internal/v2/token``
   for a short-lived Copilot token. The answer's ``proxy-ep`` field names the
   API host of the member's plan (``proxy.`` becomes ``api.``). The token is
   kept until five minutes before ``expires_at``.
2. The completion is ``POST {api}/responses`` with that token, the editor
   headers and ``X-GitHub-Api-Version``, always as a stream: that is the one
   shape the runtimes use in production, and the final ``response.completed``
   event is where the usage and the charge (``copilot_usage.total_nano_aiu``)
   arrive.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping
from urllib.parse import unquote, urlparse

import httpx

from app.services.app_sdk._outbound import CONNECT_TIMEOUT_SECONDS, OutboundClients, exchange_lock, scrub
from app.services.app_sdk.llm_types import LlmGatewayError, LlmRequest, WireDone
from app.services.app_sdk.llm_wire import (
    RESPONSES_CAP_PARAMETER,
    cap_accepted,
    cap_refused,
    error_body,
    read_responses,
    responses_body,
    status_error,
    transport_error,
)
from app.services.proxy_plan import hostname_of

LABEL = "GitHub Copilot"
# The provider's id, as the Portal names it and as the refused-cap memory keys it.
PROVIDER = "github_copilot"
DEFAULT_API_BASE_URL = "https://api.githubcopilot.com"
# Copilot's sign-in is always public github.com (copilot_auth_service.py).
GITHUB_API_BASE_URL = "https://api.github.com"
TOKEN_EXCHANGE_PATH = "/copilot_internal/v2/token"
RESPONSES_PATH = "/responses"
# The editor Copilot believes it is talking to (COPILOT_PLUGIN_HEADERS).
EDITOR_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.41.0",
    "Editor-Version": "vscode/1.133.0",
    "Editor-Plugin-Version": "copilot-chat/0.41.0",
    "Copilot-Integration-Id": "vscode-chat",
}
# VS Code's API version for chat requests (COPILOT_CHAT_API_VERSION).
CHAT_API_VERSION = "2026-01-09"
# An app's call is not a member typing a message: the runtimes label such
# calls "agent" (it only matters to the legacy request-based plans).
INITIATOR = "agent"
TOKEN_REFRESH_MARGIN_SECONDS = 300
# A credential with one of these prefixes is a GitHub token to exchange; any
# other value is taken to be a Copilot token already (is_github_source_token).
SOURCE_TOKEN_PREFIXES = ("ghp_", "ghu_", "gho_", "ghs_", "ghr_", "github_pat_")

# The runtimes never send either of these to Copilot, so neither is proven
# against it. max_output_tokens is the Responses API's own name for the cap
# an app asks for and is sent until Copilot refuses it by name: the request is
# then repeated once without it and it is left out for the rest of the process
# (llm_wire.cap_refused). temperature is not sent: the GPT-5 and GPT-6 lines
# Copilot serves are reasoning models, which refuse a temperature, and the
# Portal treats it as "not a Copilot-managed knob" (schemas/runtime_profile.py).
FORWARD_MAX_OUTPUT_TOKENS = True
FORWARD_TEMPERATURE = False

COMPLETION_TIMEOUT = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=120.0, write=30.0, pool=CONNECT_TIMEOUT_SECONDS)
EXCHANGE_TIMEOUT = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=30.0, write=30.0, pool=CONNECT_TIMEOUT_SECONDS)

_PROXY_ENDPOINT_PATTERN = re.compile(r"(?:^|[;&,\s])proxy-ep=([^;&,\s]+)")
_MAX_CACHED_TOKENS = 1024


@dataclass(frozen=True)
class CopilotToken:
    token: str = field(repr=False)
    expires_at: int
    api_base_url: str | None


# sha256 of the GitHub token -> the Copilot token it was exchanged for.
_tokens: dict[str, CopilotToken] = {}


def _now() -> float:
    return time.time()


def reset_token_cache() -> None:
    """Forget every exchanged token (tests)."""
    _tokens.clear()


def credential(llm: Mapping[str, Any]) -> str:
    """The member's Copilot authorization as the Portal stores it."""
    return str(llm.get("api_key") or "").strip()


def is_source_token(value: str) -> bool:
    return value.strip().lower().startswith(SOURCE_TOKEN_PREFIXES)


def explicit_api_base_url(llm: Mapping[str, Any]) -> str | None:
    """A base URL the member's settings name; it wins over the one the token names."""
    for key in ("base_url", "api_base", "baseURL", "endpoint"):
        value = str(llm.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    return None


def parse_api_base_url(token: str) -> str | None:
    """The API host a Copilot token names in ``proxy-ep`` (parse_copilot_api_base_url)."""
    match = _PROXY_ENDPOINT_PATTERN.search(token or "")
    if match is None:
        return None
    raw = unquote(match.group(1).strip())
    parsed = urlparse(raw)
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).strip()
    if not host:
        return None
    if host.startswith("proxy."):
        host = "api." + host[len("proxy.") :]
    return "https://{0}".format(host.rstrip("/"))


def request_headers(token: str) -> dict[str, str]:
    """The headers of a streamed ``/responses`` call (GitHubCopilotHTTPTransport._headers)."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **EDITOR_HEADERS,
        "X-GitHub-Api-Version": CHAT_API_VERSION,
        "Openai-Intent": "conversation-edits",
        "x-initiator": INITIATOR,
    }


def request_body(request: LlmRequest) -> dict:
    return responses_body(
        request,
        forward_max_output_tokens=FORWARD_MAX_OUTPUT_TOKENS and cap_accepted(PROVIDER, RESPONSES_CAP_PARAMETER),
        forward_temperature=FORWARD_TEMPERATURE,
    )


async def run(llm: Mapping[str, Any], request: LlmRequest, clients: OutboundClients) -> AsyncIterator[str | WireDone]:
    """One completion: the text deltas, then how it ended."""
    source = credential(llm)
    if not source:
        raise LlmGatewayError(
            "provider_auth_failed",
            "GitHub Copilot is not connected: the owner's Model provider connector has no authorization.",
        )
    exchanged = await _copilot_token(source, clients) if is_source_token(source) else None
    bearer = exchanged.token if exchanged is not None else source
    secrets = [source, bearer]
    body = request_body(request)
    explicit = explicit_api_base_url(llm)

    token_renewed = False
    # At most three requests: the first, one more with a renewed token, one
    # more without the output cap.
    while True:
        base_url = explicit or (exchanged.api_base_url if exchanged is not None else None) or DEFAULT_API_BASE_URL
        url = base_url + RESPONSES_PATH
        failure: LlmGatewayError | None = None
        try:
            async with clients.for_url(url).stream(
                "POST",
                url,
                headers=request_headers(bearer),
                json=body,
                timeout=COMPLETION_TIMEOUT,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    text = await error_body(response)
                    if response.status_code == 401 and exchanged is not None:
                        # The token Copilot just refused is not offered again.
                        _forget(source, exchanged)
                        said = f"{response.reason_phrase} {text}".lower()
                        if not token_renewed and "expired" in said:
                            token_renewed = True
                            exchanged = await _copilot_token(source, clients)
                            bearer = exchanged.token
                            secrets.append(bearer)
                            continue
                    if RESPONSES_CAP_PARAMETER in body and cap_refused(
                        PROVIDER, RESPONSES_CAP_PARAMETER, response.status_code, text
                    ):
                        # Copilot does not take the cap: once more without it.
                        body = {key: value for key, value in body.items() if key != RESPONSES_CAP_PARAMETER}
                        continue
                    raise status_error(LABEL, response.status_code, text, response.headers, secrets)
                async for item in read_responses(response, LABEL):
                    yield item
                return
        except LlmGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001 - every transport failure becomes a gateway error
            failure = transport_error(LABEL, exc, host=hostname_of(url), secrets=secrets)
        if failure is not None:
            # Raised out here so the error does not drag the failed request,
            # and the Authorization header in it, along as its context.
            raise failure


# ---------------------------------------------------------------- token exchange


def _fingerprint(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _fresh(entry: CopilotToken | None) -> bool:
    return entry is not None and entry.expires_at - _now() >= TOKEN_REFRESH_MARGIN_SECONDS


def _forget(source: str, entry: CopilotToken) -> None:
    key = _fingerprint(source)
    if _tokens.get(key) is entry:
        _tokens.pop(key, None)


def _remember(key: str, entry: CopilotToken) -> None:
    if len(_tokens) >= _MAX_CACHED_TOKENS:
        for stale in [name for name, value in _tokens.items() if not _fresh(value)]:
            _tokens.pop(stale, None)
        while len(_tokens) >= _MAX_CACHED_TOKENS:
            _tokens.pop(next(iter(_tokens)))
    _tokens[key] = entry


async def _copilot_token(source: str, clients: OutboundClients) -> CopilotToken:
    """The Copilot token for a GitHub token: the kept one while it is fresh, else a new exchange."""
    key = _fingerprint(source)
    cached = _tokens.get(key)
    if _fresh(cached):
        return cached  # type: ignore[return-value]
    async with exchange_lock("copilot:" + key):
        cached = _tokens.get(key)
        if _fresh(cached):
            return cached  # type: ignore[return-value]
        entry = await _exchange(source, clients)
        _remember(key, entry)
        return entry


async def _exchange(source: str, clients: OutboundClients) -> CopilotToken:
    label = "GitHub Copilot sign-in"
    url = GITHUB_API_BASE_URL + TOKEN_EXCHANGE_PATH
    headers = {"Authorization": f"Bearer {source}", "Accept": "application/json", **EDITOR_HEADERS}
    response: httpx.Response | None = None
    failure: LlmGatewayError | None = None
    try:
        response = await clients.for_url(url).get(url, headers=headers, timeout=EXCHANGE_TIMEOUT, follow_redirects=False)
    except Exception as exc:  # noqa: BLE001
        failure = transport_error(label, exc, host=hostname_of(url), secrets=[source])
    if failure is not None or response is None:
        raise failure or LlmGatewayError("provider_error", f"{label} gave no answer.")

    status = response.status_code
    if status in (401, 403, 404):
        # 404 is GitHub's answer to a token that may not ask for a Copilot token at all.
        raise LlmGatewayError(
            "provider_auth_failed",
            f"GitHub did not accept the owner's Copilot authorization (HTTP {status}). "
            "It has to be renewed in the Model provider connector.",
        )
    if not 200 <= status < 300:
        raise status_error(label, status, response.text[:4096], response.headers, [source])
    try:
        data = json.loads(response.text)
    except ValueError:
        raise LlmGatewayError("provider_error", f"{label} answered with something that is not JSON.") from None
    token = data.get("token") if isinstance(data, Mapping) else None
    expires_at = data.get("expires_at") if isinstance(data, Mapping) else None
    if not isinstance(token, str) or not token.strip():
        detail = scrub(data.get("message") if isinstance(data, Mapping) else "", [source], limit=160)
        raise LlmGatewayError("provider_error", f"{label} returned no token" + (f": {detail}" if detail else "."))
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(expires_at)
        or int(expires_at) <= 0
    ):
        raise LlmGatewayError("provider_error", f"{label} returned a token without a usable expiry.")
    return CopilotToken(token=token.strip(), expires_at=int(expires_at), api_base_url=parse_api_base_url(token))


__all__ = [
    "CHAT_API_VERSION",
    "DEFAULT_API_BASE_URL",
    "EDITOR_HEADERS",
    "FORWARD_MAX_OUTPUT_TOKENS",
    "FORWARD_TEMPERATURE",
    "GITHUB_API_BASE_URL",
    "LABEL",
    "PROVIDER",
    "TOKEN_REFRESH_MARGIN_SECONDS",
    "credential",
    "explicit_api_base_url",
    "is_source_token",
    "parse_api_base_url",
    "request_body",
    "request_headers",
    "reset_token_cache",
    "run",
]
