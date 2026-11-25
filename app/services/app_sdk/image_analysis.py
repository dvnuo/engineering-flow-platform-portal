"""A question about one to four images, answered on a member's Image analysis connector.

docs/CONTRACTS.md section 9.4 of the app repository. ``config`` is the
member's effective settings (``RuntimeProfileService.effective_config_for``),
so a member who follows the Default connectors uses the shared account. The
connector is the section ``image_analysis``: a switch, an AI Platform account
and a model; where AI Platform lives is the deployment's to say
(``materialize_image_analysis_config``). Nothing of ``llm`` is read here:
image analysis works whichever provider the member chats with, and whether
or not an app may use the model provider.

One request is sent, to chat/completions (never the Responses endpoint):
the system text when there is one, then one user message whose content is
the prompt as a text part followed by one ``image_url`` part per image, a
``data:`` URI each, in the order given. Nothing is added to it: no
instruction of the Portal's, no response format. That is the request
``RuntimeProfileTestService._test_image_analysis`` proves with one tiny
image.

The account is exchanged, the request signed, its output cap handled and
its answer read by ``llm_ai_platform`` and ``llm_wire``, the code a model
call on AI Platform runs through; the two share the JWT an account was
given. The call leaves through the proxy the Proxy connector assigns to
``image_analysis``.

Like the model gateway, this module knows nothing about apps, tokens,
budgets or the database. Every failure is an ``LlmGatewayError`` whose code
and status are those of docs/CONTRACTS.md section 9; no message carries a
credential, and none carries image data.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.contracts.llm_catalog import AI_PLATFORM_PROVIDER, effective_reasoning_effort
from app.services.ai_platform_config import (
    IMAGE_ANALYSIS_SECTION,
    image_analysis_configured,
    materialize_image_analysis_config,
)
from app.services.app_sdk import llm_ai_platform, llm_gateway
from app.services.app_sdk._outbound import CONNECT_TIMEOUT_SECONDS, OutboundClients
from app.services.app_sdk.llm_ai_platform import AiPlatformTarget
from app.services.app_sdk.llm_types import LlmGatewayError, LlmRequest, LlmResult

# The config section, and the name the Proxy connector assigns a way out to.
CONNECTOR = IMAGE_ANALYSIS_SECTION
# The connector as a message names it.
CONNECTOR_LABEL = "Image analysis"

# What one call may carry (section 9.4; the SDK checks the same before it sends).
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
# ``prompt`` and ``system`` together, in characters.
MAX_TEXT_CHARS = 200_000
MAX_OUTPUT_TOKENS = 16_000
# The answer a call that names no ``max_output_tokens`` may get.
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
# What one image is counted as while its call is under way (the daily budget
# counts a call in flight before its row says what it used).
IN_FLIGHT_TOKENS_PER_IMAGE = 1_500
# The largest request body the route reads. Four images of 5 MiB are about
# 28 MB of base64, and 200,000 characters of text are at most 1.2 MB of JSON:
# nothing the contract allows comes near this, and anything over it is
# refused before it is held in memory.
MAX_BODY_BYTES = 40 * 1024 * 1024

# The answer comes whole, so the read timeout covers all of the model's work
# on up to four images, and the request itself may be 28 MB: longer than a
# model call's (``llm_ai_platform.COMPLETION_TIMEOUT``), shorter than the ten
# minutes the SDK waits for the Portal.
INSPECT_TIMEOUT = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=300.0, write=60.0, pool=CONNECT_TIMEOUT_SECONDS)

NOT_CONFIGURED_STATUS = 424
NOT_CONFIGURED = (
    "The owner's Image analysis connector is not set up: it is switched off, its AI Platform account is "
    "incomplete, or this Portal names no AI Platform address. The owner sets it up under Connectors."
)

# The base64 text of the largest image: four characters for every three bytes, padded.
_MAX_IMAGE_BASE64_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
_KINDS = "JPEG, PNG, WebP or GIF"


@dataclass(frozen=True)
class InspectImage:
    """One image of a request: the kind its first bytes give it away as, and how the model is handed it.

    ``url`` is the ``data:`` URI of the base64 text that was sent. The image
    is kept in that one form only: megabytes held twice for as long as the
    model thinks are megabytes the Portal does not have for anything else.
    """

    media_type: str
    url: str = field(repr=False)
    # The bytes of the image itself, decoded.
    size: int


@dataclass(frozen=True)
class InspectRequest:
    """One validated request of section 9.4."""

    images: tuple[InspectImage, ...]
    prompt: str = field(repr=False)
    # None when no system text was given, or only a blank one.
    system: str | None = field(repr=False)
    # The characters of ``prompt`` and ``system`` as they were sent.
    text_chars: int
    max_output_tokens: int
    # low, medium or high, as the app asked (low when it asked for none); not yet lowered to what the model takes.
    reasoning_effort: str

    @property
    def image_bytes(self) -> int:
        return sum(image.size for image in self.images)


# ---------------------------------------------------------------- the connector


def limits() -> dict[str, int]:
    """The three limits ``GET /app`` tells an app (section 9.1), whether or not image analysis is on."""
    return {"max_images": MAX_IMAGES, "max_image_bytes": MAX_IMAGE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES}


def section_of(config: Any) -> dict | None:
    """The member's ``image_analysis`` section with the deployment's endpoints, or None when it is switched off."""
    stored = config.get(CONNECTOR) if isinstance(config, dict) else None
    return materialize_image_analysis_config(stored, settings=get_settings())


def configured_model(config: Any) -> str | None:
    """The model that reads the images when a call would reach AI Platform, else None.

    This is what ``GET /app`` reports, so it must agree with the call: the
    same ``target_of`` decides both, and an app is never told that image
    analysis is on while every call answers ``connector_not_configured``
    (a deployment that names the iB2B host without its path, for one). It
    says nothing about whether AI Platform accepts the account.
    """
    try:
        return target_of(config)[1]
    except LlmGatewayError:
        return None


def target_of(config: Any) -> tuple[AiPlatformTarget, str]:
    """``(where the call goes and what it signs in with, the model)``; ``connector_not_configured`` when there is no such thing."""
    section = section_of(config)
    model = _model_of(section)
    if section is None or model is None:
        raise _not_configured()
    try:
        # chat/completions only: the section carries no Responses endpoint,
        # and the chat endpoint is never taken for one.
        return llm_ai_platform.target_of(section, chat_only=True, connector=CONNECTOR_LABEL), model
    except LlmGatewayError:
        # What ``image_analysis_configured`` does not ask for and a call
        # needs: the iB2B path next to its host.
        raise _not_configured() from None


# ---------------------------------------------------------------- the request


def media_type_of(data: bytes) -> str | None:
    """The media type the first bytes of ``data`` give it away as, or None (the SDK's rule, byte for byte).

    ``FF D8 FF`` is JPEG; the eight bytes ``89 50 4E 47 0D 0A 1A 0A`` are
    PNG; ``RIFF``, four bytes of any value and ``WEBP`` are WebP; ``GIF87a``
    and ``GIF89a`` are GIF. Nothing after the twelfth byte is looked at.
    """
    head = bytes(data[:12])
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def parse_body(raw: bytes | bytearray) -> InspectRequest:
    """``parse`` for a request body as it arrived; ``invalid_request`` when it is not JSON at all."""
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        raise _invalid("The request body is not what this endpoint expects.") from None
    return parse(body)


def parse(body: Any) -> InspectRequest:
    """The request of section 9.4, checked; ``invalid_request`` for one the contract does not allow.

    A field the contract does not name is ignored. The text and the numbers
    are checked first, the images last: a request that is refused for a
    mistyped option has not had 20 MiB decoded for it.
    """
    if not isinstance(body, dict):
        raise _invalid("The request body must be a JSON object.")

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _invalid("prompt must be a string that is not blank: the question about the images.")
    system = body.get("system")
    if system is not None and not isinstance(system, str):
        raise _invalid("system must be a string or null.")
    text_chars = len(prompt) + len(system or "")
    if text_chars > MAX_TEXT_CHARS:
        raise _invalid(
            f"prompt and system may hold {MAX_TEXT_CHARS} characters together at most; these hold {text_chars}."
        )

    max_output_tokens = body.get("max_output_tokens")
    if max_output_tokens is None:
        max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    elif (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS
    ):
        raise _invalid(f"max_output_tokens must be a whole number from 1 to {MAX_OUTPUT_TOKENS}.")
    reasoning_effort = llm_gateway.app_reasoning_effort(body.get("reasoning_effort"))

    items = body.get("images")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_IMAGES:
        raise _invalid(f"images must be a list of 1 to {MAX_IMAGES} images.")
    images: list[InspectImage] = []
    total = 0
    for index, item in enumerate(items):
        image = _image(index, item)
        total += image.size
        if total > MAX_TOTAL_BYTES:
            raise _invalid(f"The images may hold {MAX_TOTAL_BYTES} bytes together at most.")
        images.append(image)

    return InspectRequest(
        images=tuple(images),
        prompt=prompt,
        system=system if system and system.strip() else None,
        text_chars=text_chars,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
    )


def wire_request(request: InspectRequest, model: str) -> LlmRequest:
    """The request as the wire format takes it: one user turn, the prompt and then the images in order."""
    content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
    content.extend({"type": "image_url", "image_url": {"url": image.url}} for image in request.images)
    return LlmRequest(
        model=model,
        messages=({"role": "user", "content": content},),
        system=request.system,
        max_output_tokens=request.max_output_tokens,
        # Lowered to what the model takes, the way a model call's level is.
        reasoning_effort=effective_reasoning_effort(AI_PLATFORM_PROVIDER, model, request.reasoning_effort),
    )


# ---------------------------------------------------------------- the call


async def inspect(
    *,
    config: dict,
    request: InspectRequest,
    client: httpx.AsyncClient | None = None,
) -> LlmResult:
    """One question about the images of ``request``, answered whole. Raises ``LlmGatewayError``.

    ``connector_not_configured`` when the member's connector cannot run,
    ``provider_auth_failed`` when iB2B or AI Platform refuses its account,
    ``rate_limited`` and ``upstream_timeout`` as for a model call, and
    ``provider_error`` for every other failure of AI Platform. ``client``
    replaces the HTTP client (tests); without one the call leaves the way
    the Proxy connector says ``image_analysis`` does.
    """
    target, model = target_of(config)
    clients = OutboundClients(config, CONNECTOR, injected=client)
    try:
        return await llm_gateway.collect(
            llm_ai_platform.run_target(
                target,
                wire_request(request, model),
                clients,
                streaming=False,
                timeout=INSPECT_TIMEOUT,
            ),
            model,
        )
    except LlmGatewayError as exc:
        if exc.code == "model_not_allowed":
            # The app named no model: one AI Platform does not serve is the connector's, and the owner's to change.
            raise LlmGatewayError("provider_error", exc.message) from None
        raise
    finally:
        await clients.aclose()


# ---------------------------------------------------------------- internals


def _invalid(message: str) -> LlmGatewayError:
    return LlmGatewayError("invalid_request", message)


def _not_configured() -> LlmGatewayError:
    return LlmGatewayError("connector_not_configured", NOT_CONFIGURED, NOT_CONFIGURED_STATUS)


def _model_of(section: dict | None) -> str | None:
    """The model of a materialized section that can run; None for one that is off or incomplete."""
    if section is None or not image_analysis_configured(section):
        return None
    # Always a model the connector offers: the section is coerced when it is materialized.
    return str(section.get("model") or "").strip() or None


def _image(index: int, item: Any) -> InspectImage:
    """``images[index]``, checked. A message names the image by its place and never quotes its data."""
    where = f"images[{index}]"
    if not isinstance(item, dict):
        raise _invalid(f"{where} must be an object with media_type and data_base64.")
    media_type = item.get("media_type")
    if not isinstance(media_type, str) or media_type not in MEDIA_TYPES:
        raise _invalid(f"{where}.media_type must be one of: {', '.join(MEDIA_TYPES)}.")
    encoded = item.get("data_base64")
    if not isinstance(encoded, str):
        raise _invalid(f"{where}.data_base64 must be a string of standard base64.")
    if not encoded:
        raise _invalid(f"{where} holds no bytes.")
    if len(encoded) > _MAX_IMAGE_BASE64_CHARS:
        # Not decoded to find that out.
        raise _invalid(f"{where} holds more than {MAX_IMAGE_BYTES} bytes, the most one image may hold.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid(f"{where}.data_base64 is not standard base64.") from None
    if not data:
        raise _invalid(f"{where} holds no bytes.")
    if len(data) > MAX_IMAGE_BYTES:
        raise _invalid(f"{where} holds more than {MAX_IMAGE_BYTES} bytes, the most one image may hold.")
    kind = media_type_of(data)
    if kind is None:
        raise _invalid(f"{where} is not a {_KINDS} image, going by its first bytes.")
    if kind != media_type:
        raise _invalid(f"{where} is given as {media_type}, but its first bytes are those of {kind}.")
    # The decoded bytes are let go here: what travels on is the text that was sent.
    return InspectImage(media_type=kind, url=f"data:{kind};base64,{encoded}", size=len(data))


__all__ = [
    "CONNECTOR",
    "CONNECTOR_LABEL",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "INSPECT_TIMEOUT",
    "IN_FLIGHT_TOKENS_PER_IMAGE",
    "InspectImage",
    "InspectRequest",
    "MAX_BODY_BYTES",
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "MAX_OUTPUT_TOKENS",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_BYTES",
    "MEDIA_TYPES",
    "configured_model",
    "inspect",
    "limits",
    "media_type_of",
    "parse",
    "parse_body",
    "section_of",
    "target_of",
    "wire_request",
]
