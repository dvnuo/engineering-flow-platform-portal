from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit

_DEFAULT_PUBLIC_API_BASE = "https://api.github.com"
_ENTERPRISE_API_PATH = "/api/v3"
_PUBLIC_HOSTS = {"github.com", "www.github.com", "api.github.com"}


def _normalize_path(path: str) -> str:
    value = (path or "").strip().rstrip("/")
    if not value:
        return ""
    if value.lower() == _ENTERPRISE_API_PATH:
        return _ENTERPRISE_API_PATH
    return value


def normalize_github_api_base_url(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if not value:
        return _DEFAULT_PUBLIC_API_BASE

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlsplit(value)
    host = (parsed.netloc or "").strip().lower()
    if not host:
        return _DEFAULT_PUBLIC_API_BASE

    if host in _PUBLIC_HOSTS:
        return _DEFAULT_PUBLIC_API_BASE

    path = _normalize_path(parsed.path)
    if not path:
        path = _ENTERPRISE_API_PATH

    normalized = urlunsplit(("https", host, path, "", ""))
    return normalized.rstrip("/")
