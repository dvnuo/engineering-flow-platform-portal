from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.config import Settings, get_settings
from app.contracts.llm_catalog import AI_PLATFORM_PROVIDER, normalize_provider


PROFILE_AUTH_FIELDS: tuple[str, ...] = ("username", "password", "usercase")


def profile_ai_platform_config(value: Any) -> dict[str, Any]:
    """Return the sparse, user-managed part of an AI Platform profile."""
    source = value if isinstance(value, dict) else {}
    source_auth = source.get("auth") if isinstance(source.get("auth"), dict) else {}
    auth = {
        key: cleaned
        for key in PROFILE_AUTH_FIELDS
        if (cleaned := str(source_auth.get(key) or "").strip())
    }
    return {"auth": auth} if auth else {}


def configured_ai_platform_config(settings: Settings | None = None) -> dict[str, Any]:
    """Build the deployment-managed part of the runtime AI Platform config."""
    settings = settings or get_settings()

    def clean(name: str) -> str:
        return str(getattr(settings, name, "") or "").strip()

    chat = {
        key: value
        for key, value in (
            ("host", clean("ai_platform_chat_host").rstrip("/")),
            ("uri", clean("ai_platform_chat_uri")),
        )
        if value
    }
    ib2b = {
        key: value
        for key, value in (
            ("host", clean("ai_platform_ib2b_host").rstrip("/")),
            ("uri", clean("ai_platform_ib2b_uri")),
        )
        if value
    }
    auth = {
        key: value
        for key, value in (
            ("trust_token_header", clean("ai_platform_trust_token_header")),
            ("tracking_prefix", clean("ai_platform_tracking_prefix")),
        )
        if value
    }
    configured: dict[str, Any] = {}
    if chat:
        configured["chat"] = chat
    if ib2b:
        configured["ib2b"] = ib2b
    if auth:
        configured["auth"] = auth
    return configured


def materialize_ai_platform_llm_config(
    llm: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Inject fixed Portal settings while retaining only profile credentials."""
    materialized = deepcopy(llm) if isinstance(llm, dict) else {}
    if normalize_provider(materialized.get("provider")) != AI_PLATFORM_PROVIDER:
        materialized.pop("ai_platform", None)
        return materialized

    profile_config = profile_ai_platform_config(materialized.get("ai_platform"))
    configured = configured_ai_platform_config(settings)
    auth = configured.get("auth") if isinstance(configured.get("auth"), dict) else {}
    profile_auth = profile_config.get("auth") if isinstance(profile_config.get("auth"), dict) else {}
    merged_auth = {**auth, **profile_auth}
    if merged_auth:
        configured["auth"] = merged_auth
    else:
        configured.pop("auth", None)

    if configured:
        materialized["ai_platform"] = configured
    else:
        materialized.pop("ai_platform", None)
    return materialized
