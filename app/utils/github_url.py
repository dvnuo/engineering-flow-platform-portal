from __future__ import annotations

# Keep this logic in sync with runtime src/github/url_utils.py
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

PUBLIC_GITHUB_WEB_HOST = "github.com"
PUBLIC_GITHUB_API_BASE = "https://api.github.com"
PUBLIC_GITHUB_API_HOST = "api.github.com"


def normalize_github_api_base_url(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if not value:
        return PUBLIC_GITHUB_API_BASE

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if not host:
        return PUBLIC_GITHUB_API_BASE

    if host in {PUBLIC_GITHUB_WEB_HOST, PUBLIC_GITHUB_API_HOST}:
        return PUBLIC_GITHUB_API_BASE

    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"

    path = (parsed.path or "").rstrip("/")
    if not path:
        path = "/api/v3"
    elif path.lower() == "/api/v3":
        path = "/api/v3"

    return urlunsplit(("https", netloc, path, "", ""))
