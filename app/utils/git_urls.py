import re
from typing import Optional
from urllib.parse import urlparse, urlunparse


_SCP_LIKE_GIT_URL = re.compile(r"^[^@]+@(?P<host>[^:]+):(?P<path>.+)$")


def normalize_git_repo_url(url: Optional[str]) -> Optional[str]:
    value = (url or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "ssh"}:
        host = parsed.hostname
        port = parsed.port
        path = parsed.path or ""
        if not host or not path:
            return value
        netloc = f"{host}:{port}" if port else host
        return urlunparse(("https", netloc, path, "", "", ""))

    scp_match = _SCP_LIKE_GIT_URL.match(value)
    if scp_match:
        host = scp_match.group("host")
        path = scp_match.group("path")
        if host and path:
            return f"https://{host}/{path}"

    return value
