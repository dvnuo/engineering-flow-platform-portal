"""A text completion on a member's own model provider.

``config`` is the member's effective settings
(``RuntimeProfileService.effective_config_for(profile)``); the provider is
read from ``config["llm"]["provider"]`` the way the rest of the Portal reads
it (``normalize_provider``: GitHub Copilot unless it says AI Platform), and
the call leaves through the proxy the Proxy connector assigns the model
provider, verified against the Portal's CA trust, like the Model provider
Test button.

Text in, text out: no tools, no images. The providers' protocols are in
``llm_copilot.py`` and ``llm_ai_platform.py``, the wire formats in
``llm_wire.py``. Nothing here knows about apps, tokens, budgets or the
database; the caller records ``LlmResult.usage`` and turns ``LlmGatewayError``
into the response of docs/CONTRACTS.md section 9.

The models are the Portal-wide catalog's (``model_catalog_service``), read
from memory: a caller that holds a database session warms it first with
``model_catalog_service.current_catalog(db)``.
"""
from __future__ import annotations

import math
from contextlib import aclosing
from typing import Any, AsyncIterator

import httpx

from app.config import get_settings
from app.contracts.llm_catalog import (
    AI_PLATFORM_MODELS,
    AI_PLATFORM_PROVIDER,
    DEFAULT_AI_PLATFORM_MODEL,
    effective_reasoning_effort,
    is_copilot_auto_model,
    normalize_provider,
)
from app.services import model_catalog_service
from app.services.ai_platform_config import materialize_ai_platform_llm_config
from app.services.app_sdk import llm_ai_platform, llm_copilot
from app.services.app_sdk._outbound import OutboundClients
from app.services.app_sdk.llm_types import ERROR_STATUS, LlmGatewayError, LlmRequest, LlmResult, LlmUsage, WireDone

# The concrete model an app gets when its owner's Copilot setting is one of
# the "auto" ids: the model the runtimes fall back to when Copilot's own
# routing cannot be used (the native runtime's DEFAULT_MODEL_ID).
COPILOT_APP_DEFAULT_MODEL = "gpt-6-sol"

ROLES = ("user", "assistant")
MAX_TEMPERATURE = 2.0
# The thinking levels an app may ask for, and the one a call without one gets.
# An app call never inherits the level its owner chose for their assistants
# (``llm.reasoning_effort``): that one is tuned for long agent runs and would
# make every visitor's request slow and dear.
APP_REASONING_EFFORTS = ("low", "medium", "high")
DEFAULT_APP_REASONING_EFFORT = "low"


# ---------------------------------------------------------------- models


def provider_id(config: dict) -> str:
    """``github_copilot`` or ``ai_platform``: the provider the owner's settings choose."""
    return normalize_provider(_llm_section(config).get("provider"))


def is_configured(config: dict) -> bool:
    """Whether a call can be attempted: the owner's settings carry a credential
    (and, for AI Platform, the deployment names its endpoints).

    It says nothing about whether the provider accepts the credential; a call
    that is refused ends in ``provider_auth_failed``.
    """
    if provider_id(config) == AI_PLATFORM_PROVIDER:
        try:
            llm_ai_platform.target_of(_materialized_llm(config))
        except LlmGatewayError:
            return False
        return True
    return bool(llm_copilot.credential(_llm_section(config)))


def available_models(config: dict) -> list[str]:
    """The concrete models an app may name on the owner's provider, in the pickers' order.

    For Copilot that is the Portal-wide catalog without its "auto" entries
    (the static list until a runtime has reported the catalog); for AI
    Platform the fixed list. It does not depend on the owner's credentials.
    """
    provider = provider_id(config)
    if provider == AI_PLATFORM_PROVIDER:
        return list(AI_PLATFORM_MODELS)
    models: list[str] = []
    for entry in model_catalog_service.current_catalog().models:
        model = str(entry.get("id") or "").strip()
        if not model or entry.get("kind") == "auto" or is_copilot_auto_model(model) or model in models:
            continue
        if model_catalog_service.is_model_allowed(provider, model):
            models.append(model)
    return models


def default_model(config: dict) -> str | None:
    """The model a call without one uses: the owner's own when it is concrete, else a concrete fallback.

    None only when the provider offers no model at all.
    """
    provider = provider_id(config)
    models = available_models(config)
    configured = str(_llm_section(config).get("model") or "").strip()
    if "/" in configured:
        configured = configured.rsplit("/", 1)[1].strip()
    configured = configured.lower()
    if configured and not is_copilot_auto_model(configured) and model_catalog_service.is_model_allowed(provider, configured):
        return configured
    fallback = DEFAULT_AI_PLATFORM_MODEL if provider == AI_PLATFORM_PROVIDER else COPILOT_APP_DEFAULT_MODEL
    if fallback in models:
        return fallback
    return models[0] if models else None


def resolve_model(config: dict, requested: str | None) -> str:
    """The concrete model for a request: ``requested`` when the owner's provider offers it, the default when none is named.

    Raises ``invalid_request`` for a value that is not text and
    ``model_not_allowed`` for a model the catalog does not list for the
    provider, or an "auto" id (those are not offered to apps).
    """
    if requested is not None and not isinstance(requested, str):
        raise LlmGatewayError("invalid_request", "model must be a string.")
    wanted = (requested or "").strip().lower()
    if not wanted:
        model = default_model(config)
        if model is None:
            raise LlmGatewayError("model_not_allowed", "The owner's model provider offers no model.")
        return model
    if is_copilot_auto_model(wanted):
        raise LlmGatewayError(
            "model_not_allowed",
            f'"{wanted}" is not offered to apps; name a model, or leave the model out to use the default.',
        )
    if not model_catalog_service.is_model_allowed(provider_id(config), wanted):
        raise LlmGatewayError("model_not_allowed", f'The model "{wanted[:80]}" is not available on the owner\'s model provider.')
    return wanted


# ---------------------------------------------------------------- completions


async def complete(
    *,
    config: dict,
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> LlmResult:
    """One completion, whole. Raises ``LlmGatewayError``.

    ``reasoning_effort`` is ``low``, ``medium`` or ``high``; left out it is
    ``low``, and either way it is lowered to what the model takes.
    """
    request = _request(config, model, messages, system, max_output_tokens, temperature, reasoning_effort)
    parts: list[str] = []
    async with aclosing(_events(config, request, client, streaming=False)) as events:
        async for item in events:
            if isinstance(item, WireDone):
                return LlmResult(
                    text="".join(parts) or item.text,
                    model=request.model,
                    finish_reason=item.finish_reason,
                    usage=item.usage,
                )
            parts.append(item)
    raise LlmGatewayError("provider_error", "The model provider ended the answer without saying how.")


async def stream(
    *,
    config: dict,
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict]:
    """One completion as it arrives.

    Yields ``{"type": "delta", "text": ...}`` for each piece of text, then
    exactly one ``{"type": "done", "model": ..., "finish_reason": ...,
    "usage": {...}}``. A failure before the first delta raises
    ``LlmGatewayError`` from the first iteration, so the caller can still
    answer with an error status; one after it is yielded as ``{"type":
    "error", "error": {"code": ..., "message": ...}}`` and ends the stream.
    Closing the iterator early closes the provider's stream with it.
    """
    request = _request(config, model, messages, system, max_output_tokens, temperature, reasoning_effort)
    sent = False
    try:
        async with aclosing(_events(config, request, client, streaming=True)) as events:
            async for item in events:
                if isinstance(item, WireDone):
                    if item.text and not sent:
                        yield {"type": "delta", "text": item.text}
                    yield {
                        "type": "done",
                        "model": request.model,
                        "finish_reason": item.finish_reason,
                        "usage": item.usage.as_dict(),
                    }
                    return
                sent = True
                yield {"type": "delta", "text": item}
        raise LlmGatewayError("provider_error", "The model provider ended the answer without saying how.")
    except LlmGatewayError as exc:
        if not sent:
            raise
        yield {"type": "error", "error": exc.as_error()}


# ---------------------------------------------------------------- internals


def _llm_section(config: Any) -> dict:
    llm = config.get("llm") if isinstance(config, dict) else None
    return llm if isinstance(llm, dict) else {}


def _materialized_llm(config: Any) -> dict:
    """The model provider section with the deployment's AI Platform endpoints filled in, as the Test button reads it."""
    return materialize_ai_platform_llm_config(_llm_section(config), settings=get_settings())


def _request(
    config: dict,
    model: Any,
    messages: Any,
    system: Any,
    max_output_tokens: Any,
    temperature: Any,
    reasoning_effort: Any = None,
) -> LlmRequest:
    """The request, validated; ``invalid_request`` or ``model_not_allowed`` otherwise."""
    if not isinstance(messages, list) or not messages:
        raise LlmGatewayError("invalid_request", "messages must be a non-empty list.")
    turns: list[dict] = []
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            raise LlmGatewayError("invalid_request", f"messages[{index}] must be an object with role and content.")
        if item.get("role") not in ROLES:
            raise LlmGatewayError("invalid_request", f'messages[{index}].role must be "user" or "assistant".')
        if not isinstance(item.get("content"), str):
            raise LlmGatewayError("invalid_request", f"messages[{index}].content must be a string.")
        turns.append({"role": item["role"], "content": item["content"]})
    if not any(turn["content"].strip() for turn in turns):
        raise LlmGatewayError("invalid_request", "messages carry no text.")
    if system is not None and not isinstance(system, str):
        raise LlmGatewayError("invalid_request", "system must be a string.")
    if max_output_tokens is not None and (
        isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1
    ):
        raise LlmGatewayError("invalid_request", "max_output_tokens must be a positive whole number.")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0 <= temperature <= MAX_TEMPERATURE
    ):
        raise LlmGatewayError("invalid_request", "temperature must be a number from 0 to 2.")
    if reasoning_effort is not None and (
        not isinstance(reasoning_effort, str) or reasoning_effort.strip().lower() not in APP_REASONING_EFFORTS
    ):
        raise LlmGatewayError("invalid_request", "reasoning_effort must be low, medium or high.")
    wanted_effort = reasoning_effort.strip().lower() if reasoning_effort is not None else DEFAULT_APP_REASONING_EFFORT
    resolved = resolve_model(config, model)
    return LlmRequest(
        model=resolved,
        messages=tuple(turns),
        system=system if system and system.strip() else None,
        max_output_tokens=max_output_tokens,
        temperature=float(temperature) if temperature is not None else None,
        # The level the app asked for (low when it asked for none), clamped to
        # what the model takes the way an assistant's is. Never the owner's own
        # ``llm.reasoning_effort``.
        reasoning_effort=effective_reasoning_effort(provider_id(config), resolved, wanted_effort),
    )


async def _events(
    config: dict,
    request: LlmRequest,
    client: httpx.AsyncClient | None,
    *,
    streaming: bool,
) -> AsyncIterator[str | WireDone]:
    """The provider's answer to ``request``: text deltas, then one ``WireDone``."""
    llm = _materialized_llm(config)
    clients = OutboundClients(config, "llm", injected=client)
    try:
        if normalize_provider(llm.get("provider")) == AI_PLATFORM_PROVIDER:
            answer = llm_ai_platform.run(llm, request, clients, streaming=streaming)
        else:
            answer = llm_copilot.run(llm, request, clients)
        async with aclosing(answer) as items:
            async for item in items:
                yield item
    finally:
        await clients.aclose()


__all__ = [
    "APP_REASONING_EFFORTS",
    "COPILOT_APP_DEFAULT_MODEL",
    "DEFAULT_APP_REASONING_EFFORT",
    "ERROR_STATUS",
    "LlmGatewayError",
    "LlmResult",
    "LlmUsage",
    "available_models",
    "complete",
    "default_model",
    "is_configured",
    "provider_id",
    "resolve_model",
    "stream",
]
