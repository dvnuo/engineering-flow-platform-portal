"""The APPS_* settings, read when asked for so a test (or a reload) can change them."""

from __future__ import annotations

from urllib.parse import quote

from app.config import get_settings


def apps_enabled() -> bool:
    return bool(getattr(get_settings(), "apps_enabled", False))


def anyone_enabled() -> bool:
    """Whether apps that need no sign-in are offered on this Portal at all."""
    return bool(getattr(get_settings(), "apps_anyone_enabled", False))


def public_base_url() -> str:
    """The app host as browsers reach it, without a trailing slash; empty when unset."""
    return str(getattr(get_settings(), "apps_public_base_url", "") or "").strip().rstrip("/")


def app_public_url(slug: str) -> str:
    """``{APPS_PUBLIC_BASE_URL}/{slug}/``; empty when the public address is not configured."""
    base = public_base_url()
    return f"{base}/{quote(str(slug), safe='')}/" if base else ""


def host_internal_url() -> str:
    return str(getattr(get_settings(), "apps_host_internal_url", "") or "").strip().rstrip("/")


def host_api_token() -> str:
    return str(getattr(get_settings(), "apps_host_api_token", "") or "").strip()


def git_token() -> str:
    """The service account's token for reading app repositories."""
    settings = get_settings()
    own = str(getattr(settings, "apps_git_auth_pat", "") or "").strip()
    return own or str(getattr(settings, "git_repo_auth_pat", "") or "").strip()


def github_api_url() -> str:
    value = str(getattr(get_settings(), "apps_github_api_url", "") or "").strip().rstrip("/")
    return value or "https://api.github.com"


def max_bundle_bytes() -> int:
    try:
        megabytes = int(getattr(get_settings(), "apps_max_bundle_mb", 200) or 0)
    except (TypeError, ValueError):
        megabytes = 200
    return max(1, megabytes) * 1024 * 1024


def default_daily_credits() -> float:
    return float(getattr(get_settings(), "apps_default_daily_credits", 50.0) or 0.0)


def default_daily_tokens() -> int:
    return int(getattr(get_settings(), "apps_default_daily_tokens", 2000000) or 0)


def sdk_rate_per_minute() -> int:
    return int(getattr(get_settings(), "apps_sdk_rate_per_minute", 120) or 0)


def deleted_retention_days() -> int:
    """How long a deleted app's data stays on the host before it is removed; 0 keeps it for good."""
    try:
        days = int(getattr(get_settings(), "apps_deleted_retention_days", 30) or 0)
    except (TypeError, ValueError):
        days = 30
    return max(0, days)
