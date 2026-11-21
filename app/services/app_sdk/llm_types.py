"""The plain values the LLM gateway takes and returns (see llm_gateway.py)."""
from __future__ import annotations

from dataclasses import dataclass, field

# The codes and statuses of docs/CONTRACTS.md section 9 that a completion can end in.
ERROR_STATUS: dict[str, int] = {
    "invalid_request": 400,
    "model_not_allowed": 400,
    "provider_auth_failed": 424,
    "rate_limited": 429,
    "provider_error": 502,
    "upstream_timeout": 504,
}


@dataclass
class LlmUsage:
    input_tokens: int
    output_tokens: int
    # GitHub AI credits the provider says it charged; None when it reports none.
    credits: float | None

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "credits": self.credits}


@dataclass
class LlmResult:
    text: str
    model: str
    # "stop" or "length".
    finish_reason: str
    usage: LlmUsage


class LlmGatewayError(Exception):
    """A completion that did not happen: ``code`` and ``status`` are those of CONTRACTS section 9.

    ``retry_after`` (seconds) is set on ``rate_limited`` when the provider
    named a delay. The message never carries a credential.
    """

    def __init__(self, code: str, message: str, status: int | None = None, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else ERROR_STATUS.get(code, 502)
        self.retry_after = retry_after

    def as_error(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class LlmRequest:
    """One validated completion request, the same for every provider."""

    model: str
    messages: tuple[dict, ...]
    system: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    # low / medium / high / xhigh / max, already clamped to what the model takes.
    reasoning_effort: str | None = None


@dataclass
class WireDone:
    """How a provider's answer ended."""

    finish_reason: str
    usage: LlmUsage
    # The whole answer, for a provider that sent it in its final object rather
    # than as deltas; the gateway uses it only when no delta arrived.
    text: str = field(default="")


__all__ = ["ERROR_STATUS", "LlmGatewayError", "LlmRequest", "LlmResult", "LlmUsage", "WireDone"]
