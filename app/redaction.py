"""Utilities for redacting sensitive data from logs and error messages."""

from __future__ import annotations

from typing import Any
import re

REDACTED = "[REDACTED]"
REDACTED_PRIVATE_KEY = "[REDACTED_PRIVATE_KEY]"

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apitoken",
    "api_token",
    "secret",
    "secret_key",
    "private_key",
    "ssh_key",
    "ssh_private_key",
    "authorization",
    "cookie",
    "session",
    "github_token",
    "github_api_token",
    "openai_api_key",
    "llm_api_key",
    "proxy_password",
}

_TEXT_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization\s*:\s*basic\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(cookie\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(token|access_token|refresh_token|password|api_key|secret|secret_key)\s*=\s*([^&\s]+)"), r"\1=[REDACTED]"),
    (re.compile(r"\bghp_[A-Za-z0-9_]+"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]+"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]+"), REDACTED),
    (re.compile(r"\bxoxb-[A-Za-z0-9-]+"), REDACTED),
    (re.compile(r"(?i)\b(https?://)([^\s/@:]+):([^@\s/]+)@"), r"\1[REDACTED]:[REDACTED]@"),
    (
        re.compile(
            r"-----BEGIN (?:PRIVATE KEY|RSA PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END (?:PRIVATE KEY|RSA PRIVATE KEY|OPENSSH PRIVATE KEY)-----"
        ),
        REDACTED_PRIVATE_KEY,
    ),
]


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "_", str(key).strip().lower())


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    compact = normalized.replace("_", "")
    return compact in _SENSITIVE_KEYS


def redact_text(text: str) -> str:
    """Redact sensitive values from free-form text."""
    redacted = text
    for pattern, replacement in _TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive values in Python objects."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, set):
        return {redact_value(item) for item in value}
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_preview(value: Any, limit: int = 200) -> str:
    """Create a sanitized, truncated preview string for logging."""
    text = str(redact_value(value))
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def sanitize_exception_message(value: Any) -> str:
    """Convert exception/details to safe text for API errors and logs."""
    if value is None:
        return ""
    return redact_text(str(redact_value(value)))
