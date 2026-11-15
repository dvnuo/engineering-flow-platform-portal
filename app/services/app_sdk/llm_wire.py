"""The two wire formats the model providers speak: OpenAI Responses and Chat Completions.

Text in, text out. The request bodies are built from a whitelist of keys and
the answers are read the way the runtimes read them (the native runtime's
``efp_runtime/llm/adapter.py`` and ``provider.py``): a ``text/event-stream``
of JSON events, or one JSON object when the provider answers without
streaming. Every reader yields the text deltas as ``str`` and ends with one
``WireDone``; anything else is a ``provider_error``.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator, Iterable, Mapping

import httpx

from app.services.app_sdk._outbound import failure_text, scrub
from app.services.app_sdk.llm_types import LlmGatewayError, LlmRequest, LlmUsage, WireDone

FINISH_STOP = "stop"
FINISH_LENGTH = "length"
# 1 GitHub AI credit = 10^9 nano-AIU (the native runtime's efp_runtime/usage.py).
NANO_AIU_PER_CREDIT = 1_000_000_000

# The keys a Responses request may carry. The first five are the ones the
# native runtime's sanitiser keeps for a request without tools
# (_sanitize_copilot_responses_payload); Copilot refuses a body with keys it
# does not know (invalid_request_body). max_output_tokens and temperature are
# the two the runtimes never send: see FORWARD_* in the provider modules.
RESPONSES_BODY_KEYS = ("model", "input", "stream", "instructions", "reasoning", "max_output_tokens", "temperature")
CHAT_BODY_KEYS = (
    "model",
    "messages",
    "stream",
    "stream_options",
    "reasoning_effort",
    "user",
    "max_completion_tokens",
    "temperature",
)

_LENGTH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})
_FILTER_REASONS = frozenset({"content_filter"})
# What a provider says when it does not serve the model that was asked for
# (the native runtime's _is_model_unavailable_message).
_MODEL_UNAVAILABLE_MARKERS = ("requested model is not available", "model is not available", "not available for session")
# What a provider says when the prompt is larger than the model's context
# window (the native runtime's _CONTEXT_OVERFLOW_MESSAGE_MARKERS / _ERROR_CODES).
_CONTEXT_OVERFLOW_MARKERS = (
    "exceeds the context window",
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
)
_CONTEXT_OVERFLOW_CODES = ("context_length_exceeded",)
_ERROR_BODY_LIMIT = 4096


# ---------------------------------------------------------------- request bodies


def responses_body(
    request: LlmRequest,
    *,
    forward_max_output_tokens: bool,
    forward_temperature: bool,
) -> dict:
    """A streaming Responses request: typed text items, nothing else."""
    body: dict[str, Any] = {
        "model": request.model,
        "input": [
            {
                "role": message["role"],
                "content": [
                    {
                        "type": "output_text" if message["role"] == "assistant" else "input_text",
                        "text": message["content"],
                    }
                ],
            }
            for message in request.messages
        ],
        "stream": True,
    }
    if request.system:
        body["instructions"] = request.system
    if request.reasoning_effort:
        body["reasoning"] = {"effort": request.reasoning_effort}
    if forward_max_output_tokens and request.max_output_tokens is not None:
        body["max_output_tokens"] = request.max_output_tokens
    if forward_temperature and request.temperature is not None:
        body["temperature"] = request.temperature
    return {key: body[key] for key in RESPONSES_BODY_KEYS if key in body}


def chat_body(
    request: LlmRequest,
    *,
    stream: bool,
    user: str = "",
    include_usage: bool,
    forward_max_output_tokens: bool,
    forward_temperature: bool,
) -> dict:
    """A Chat Completions request: the system text first, then the turns."""
    messages: list[dict[str, str]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.extend({"role": message["role"], "content": message["content"]} for message in request.messages)
    body: dict[str, Any] = {"model": request.model, "messages": messages, "stream": stream}
    if stream and include_usage:
        # Without it a streamed chat completion carries no usage at all.
        body["stream_options"] = {"include_usage": True}
    if request.reasoning_effort:
        body["reasoning_effort"] = request.reasoning_effort
    if user:
        body["user"] = user
    if forward_max_output_tokens and request.max_output_tokens is not None:
        body["max_completion_tokens"] = request.max_output_tokens
    if forward_temperature and request.temperature is not None:
        body["temperature"] = request.temperature
    return {key: body[key] for key in CHAT_BODY_KEYS if key in body}


# ---------------------------------------------------------------- reading answers


async def read_responses(response: httpx.Response, label: str) -> AsyncIterator[str | WireDone]:
    """The deltas of a Responses answer, then how it ended."""
    if _is_json(response):
        yield responses_done(_json_object(await response.aread(), label), label)
        return
    async for event in _sse_events(response, label):
        kind = event.get("type")
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                yield delta
        elif kind in ("response.completed", "response.incomplete"):
            inner = event.get("response")
            yield responses_done(inner if isinstance(inner, Mapping) else {}, label, event=event)
            return
        elif kind in ("response.failed", "error"):
            raise LlmGatewayError("provider_error", f"{label} failed the request: {_event_error_text(event)}")
    raise LlmGatewayError("provider_error", f"{label} ended the stream before the answer was complete.")


def responses_done(response: Mapping[str, Any], label: str, *, event: Mapping[str, Any] | None = None) -> WireDone:
    """The end of a Responses answer from its response object (and the event that carried it)."""
    if response.get("error"):
        raise LlmGatewayError("provider_error", f"{label} failed the request: {_event_error_text(response)}")
    details = response.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, Mapping) else None
    usage = response.get("usage")
    if not isinstance(usage, Mapping) and event is not None:
        usage = event.get("usage")
    # Copilot reports the charge on the response.completed event itself, next
    # to ``response``; a usage object or a response object carrying it is read too.
    return WireDone(
        finish_reason=_finish_reason(reason, label),
        usage=usage_of(usage, event, response),
        text=_responses_text(response),
    )


async def read_chat(response: httpx.Response, label: str) -> AsyncIterator[str | WireDone]:
    """The deltas of a Chat Completions answer, then how it ended."""
    if _is_json(response):
        yield chat_done(_json_object(await response.aread(), label), label)
        return
    finish: str | None = None
    usage: Any = None
    async for event in _sse_events(response, label):
        if event.get("error"):
            raise LlmGatewayError("provider_error", f"{label} failed the request: {_event_error_text(event)}")
        choice = _first_choice(event)
        if choice is not None:
            delta = choice.get("delta") if isinstance(choice.get("delta"), Mapping) else choice.get("message")
            content = delta.get("content") if isinstance(delta, Mapping) else None
            if isinstance(content, str) and content:
                yield content
            if choice.get("finish_reason"):
                finish = str(choice.get("finish_reason"))
        if isinstance(event.get("usage"), Mapping):
            usage = event.get("usage")
    if finish is None:
        raise LlmGatewayError("provider_error", f"{label} ended the stream before the answer was complete.")
    yield WireDone(finish_reason=_finish_reason(finish, label), usage=usage_of(usage))


def chat_done(body: Mapping[str, Any], label: str) -> WireDone:
    """The end of a Chat Completions answer that came as one JSON object."""
    if body.get("error"):
        raise LlmGatewayError("provider_error", f"{label} failed the request: {_event_error_text(body)}")
    choice = _first_choice(body)
    message = choice.get("message") if choice is not None else None
    if not isinstance(message, Mapping):
        raise LlmGatewayError("provider_error", f"{label} answered without a message.")
    return WireDone(
        finish_reason=_finish_reason(choice.get("finish_reason"), label),
        usage=usage_of(body.get("usage")),
        text=_text_of(message.get("content")),
    )


def usage_of(usage: Any, *around: Any) -> LlmUsage:
    """Tokens in either spelling, and the credits Copilot says it charged (``copilot_usage.total_nano_aiu``)."""
    counts = usage if isinstance(usage, Mapping) else {}
    credits: float | None = None
    for source in (counts, *around):
        charge = source.get("copilot_usage") if isinstance(source, Mapping) else None
        nano = _number(charge.get("total_nano_aiu")) if isinstance(charge, Mapping) else None
        if nano is not None and nano >= 0:
            credits = round(nano / NANO_AIU_PER_CREDIT, 6)
            break
    return LlmUsage(
        input_tokens=_first_count(counts, "input_tokens", "prompt_tokens"),
        output_tokens=_first_count(counts, "output_tokens", "completion_tokens"),
        credits=credits,
    )


# ---------------------------------------------------------------- failures


async def error_body(response: httpx.Response) -> str:
    """The start of an error answer's body, as text."""
    try:
        raw = await response.aread()
    except Exception:  # noqa: BLE001 - the status alone still says what happened
        return ""
    return raw[:_ERROR_BODY_LIMIT].decode("utf-8", errors="replace").strip()


def status_error(
    label: str,
    status: int,
    body_text: str,
    headers: Mapping[str, str] | None = None,
    secrets: Iterable[Any] = (),
) -> LlmGatewayError:
    """The gateway error for a provider that answered ``status``."""
    body = _json_mapping(body_text)
    said = _json_error_message(body)
    detail = scrub(said or body_text, secrets, limit=240)
    message = f"{label} answered HTTP {status}" + (f": {detail}" if detail else ".")
    if status in (401, 403):
        return LlmGatewayError("provider_auth_failed", message)
    if status == 429:
        return LlmGatewayError("rate_limited", message, retry_after=retry_after_seconds(headers))
    if status in (408, 504):
        return LlmGatewayError("upstream_timeout", message)
    if status == 400 and said and any(marker in said.lower() for marker in _MODEL_UNAVAILABLE_MARKERS):
        return LlmGatewayError("model_not_allowed", message)
    if _is_context_overflow(status, body, said, body_text):
        return LlmGatewayError("invalid_request", f"The messages are larger than the model takes. {message}")
    return LlmGatewayError("provider_error", message)


def _is_context_overflow(status: int, body: Mapping[str, Any] | None, said: str | None, body_text: str) -> bool:
    """Whether the provider refused the prompt as too large (the native runtime's _context_overflow_error_from_http_error)."""
    if status == 413:
        return True
    if status != 400:
        return False
    # A JSON body without a message often echoes the request back, and the
    # request's own text must not decide this; only a non-JSON body is read whole.
    text = said if said is not None else (body_text if body is None else "")
    if any(marker in text.lower() for marker in _CONTEXT_OVERFLOW_MARKERS):
        return True
    return _json_error_code(body) in _CONTEXT_OVERFLOW_CODES


def transport_error(label: str, exc: BaseException, *, host: str, secrets: Iterable[Any] = ()) -> LlmGatewayError:
    """The gateway error for a request that got no (complete) answer."""
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return LlmGatewayError("upstream_timeout", f"{label} did not answer in time.")
    return LlmGatewayError("provider_error", f"{label} could not be reached: {failure_text(exc, host=host, secrets=secrets)}")


def retry_after_seconds(headers: Mapping[str, str] | None) -> int | None:
    """``Retry-After`` as whole seconds (it may be a number or an HTTP date); None when absent or unreadable."""
    raw = str((headers or {}).get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0, math.ceil(seconds))


# ---------------------------------------------------------------- internals


def _is_json(response: httpx.Response) -> bool:
    media_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return media_type.endswith("json")


def _json_object(raw: bytes, label: str) -> dict:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LlmGatewayError("provider_error", f"{label} answered with something that is not JSON.") from None
    if not isinstance(parsed, dict):
        raise LlmGatewayError("provider_error", f"{label} answered with JSON that is not an object.")
    return parsed


async def _lines(response: httpx.Response) -> AsyncIterator[str]:
    # Split on line feeds only. httpx's own aiter_lines also breaks at U+2028
    # and friends, which may stand unescaped inside a JSON string and would
    # cut an event in two.
    pending = ""
    async for text in response.aiter_text():
        pending += text
        while True:
            index = pending.find("\n")
            if index < 0:
                break
            line, pending = pending[:index], pending[index + 1 :]
            yield line.rstrip("\r")
    if pending:
        yield pending.rstrip("\r")


async def _sse_events(response: httpx.Response, label: str) -> AsyncIterator[dict]:
    """The JSON objects of a text/event-stream: ``data:`` lines up to a blank line (parse_sse_json_events)."""
    data_lines: list[str] = []

    def flush() -> dict | None:
        raw = "\n".join(data_lines).strip()
        data_lines.clear()
        if not raw or raw == "[DONE]":
            return None
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise LlmGatewayError("provider_error", f"{label} sent a stream event that is not JSON.") from None
        return dict(parsed) if isinstance(parsed, Mapping) else {"data": parsed}

    async for line in _lines(response):
        if not line:
            event = flush()
            if event is not None:
                yield event
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    event = flush()
    if event is not None:
        yield event


def _first_choice(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return choices[0]
    return None


def _finish_reason(reason: Any, label: str) -> str:
    text = str(reason or "").strip().lower()
    if text in _FILTER_REASONS:
        raise LlmGatewayError("provider_error", f"{label} withheld the answer (content filter).")
    return FINISH_LENGTH if text in _LENGTH_REASONS else FINISH_STOP


def _text_of(value: Any) -> str:
    """A content value as text: a string, or a list of text parts (the adapter's _extract_text)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and item.get("type") in ("text", "output_text", "input_text"):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def _responses_text(response: Mapping[str, Any]) -> str:
    """The answer in a Responses object (the adapter's _extract_response_text)."""
    direct = _text_of(response.get("output_text"))
    if direct:
        return direct
    parts: list[str] = []
    output = response.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") in ("message", "assistant_message"):
            parts.append(_text_of(item.get("content")))
        elif item.get("type") in ("text", "output_text"):
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def _event_error_text(event: Mapping[str, Any]) -> str:
    """``code: message`` of an error event or object (the adapter's _provider_error_payload)."""
    nested: Mapping[str, Any] = {}
    inner = event.get("response")
    if isinstance(inner, Mapping) and isinstance(inner.get("error"), Mapping):
        nested = inner["error"]
    top = event.get("error")
    if isinstance(top, Mapping):
        nested = top
    elif isinstance(top, str) and top.strip():
        return scrub(top, limit=240)
    message = _optional_text(event.get("message")) or _optional_text(nested.get("message"))
    code = _optional_text(event.get("code")) or _optional_text(nested.get("code"))
    if message and code:
        return scrub(f"{code}: {message}", limit=240)
    return scrub(message or code or "no reason given", limit=240)


def _json_mapping(body_text: str) -> Mapping[str, Any] | None:
    try:
        data = json.loads(body_text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def _json_error_code(data: Mapping[str, Any] | None) -> str | None:
    """The code of a JSON error body (the native runtime's _json_error_code)."""
    if data is None:
        return None
    error = data.get("error")
    for source in (error if isinstance(error, Mapping) else {}, data):
        for key in ("code", "type"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _json_error_message(data: Mapping[str, Any] | None) -> str | None:
    """The message of a JSON error body (the native runtime's _json_error_message)."""
    if data is None:
        return None
    error = data.get("error")
    if isinstance(error, Mapping):
        for key in ("message", "detail", "error"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    for key in ("message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_count(counts: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        number = _number(counts.get(key))
        if number is not None and number > 0:
            return int(number)
    return 0


__all__ = [
    "CHAT_BODY_KEYS",
    "FINISH_LENGTH",
    "FINISH_STOP",
    "RESPONSES_BODY_KEYS",
    "chat_body",
    "chat_done",
    "error_body",
    "read_chat",
    "read_responses",
    "responses_body",
    "responses_done",
    "retry_after_seconds",
    "status_error",
    "transport_error",
    "usage_of",
]
