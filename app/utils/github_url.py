from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse


_GITHUB_PUBLIC_HOSTS = {"github.com", "www.github.com", "api.github.com"}


def normalize_github_api_base_url(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if not value:
        return "https://api.github.com"

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    scheme = parsed.scheme or "https"
    host = (parsed.netloc or parsed.path or "").strip().lower()
    path = parsed.path if parsed.netloc else ""

    if not host:
        return "https://api.github.com"

    path = (path or "").strip()
    path = path.rstrip("/")

    if host in _GITHUB_PUBLIC_HOSTS:
        return "https://api.github.com"

    if not path:
        path = "/api/v3"

    return f"{scheme}://{host}{path}".rstrip("/")
