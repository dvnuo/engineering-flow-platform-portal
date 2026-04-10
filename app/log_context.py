"""Request/dispatch-scoped logging context helpers."""

from __future__ import annotations

from contextvars import ContextVar, Token
import secrets

_DEFAULT_CONTEXT = {
    "trace_id": "-",
    "span_id": "-",
    "parent_span_id": "-",
    "portal_dispatch_id": "-",
    "portal_task_id": "-",
    "agent_id": "-",
    "path": "-",
}

_log_context: ContextVar[dict[str, str]] = ContextVar("log_context", default=_DEFAULT_CONTEXT.copy())


def get_log_context() -> dict[str, str]:
    current = _log_context.get()
    merged = _DEFAULT_CONTEXT.copy()
    merged.update({k: v for k, v in current.items() if v is not None and str(v).strip()})
    return merged


def bind_log_context(**kwargs) -> Token:
    current = get_log_context()
    updates = {
        key: str(value).strip()
        for key, value in kwargs.items()
        if value is not None and str(value).strip()
    }
    current.update(updates)
    return _log_context.set(current)


def reset_log_context(token: Token) -> None:
    _log_context.reset(token)


def generate_trace_id() -> str:
    return secrets.token_hex(16)


def generate_span_id() -> str:
    return secrets.token_hex(8)
