from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

from app.config import Settings, get_settings
from app.contracts.llm_catalog import (
    AI_PLATFORM_PROVIDER,
    coerce_to_provider_model,
    flag_enabled,
    normalize_provider,
    uses_ai_platform_credentials,
)


PROFILE_AUTH_FIELDS: tuple[str, ...] = ("username", "password", "usercase")
USERCASE_PLACEHOLDER = "{usercase}"
# The Image analysis connector's config section (and its proxy assignment
# key and Test target): inspect-image on AI Platform, independent of ``llm``.
IMAGE_ANALYSIS_SECTION = "image_analysis"


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
    responses = {
        key: value
        for key, value in (
            ("host", clean("ai_platform_chat_host").rstrip("/")),
            ("uri", clean("ai_platform_responses_uri")),
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
    # Only a real path selects the Responses endpoint; a host alone is just
    # the chat host repeated.
    if responses.get("uri"):
        configured["responses"] = responses
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
    # The endpoints are needed only for AI Platform chat.
    if not uses_ai_platform_credentials(materialized):
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

    # Endpoint paths may carry a ``{usercase}`` placeholder (the Responses
    # gateway routes by use case). Fill it from the profile credential; without
    # a usercase the endpoint cannot be addressed, so drop it rather than send
    # a literal placeholder.
    usercase = str(profile_auth.get("usercase") or "").strip()
    for endpoint_name in ("chat", "responses"):
        endpoint = configured.get(endpoint_name)
        if not isinstance(endpoint, dict):
            continue
        uri = str(endpoint.get("uri") or "")
        if USERCASE_PLACEHOLDER not in uri:
            continue
        if usercase:
            endpoint["uri"] = uri.replace(USERCASE_PLACEHOLDER, quote(usercase, safe=""))
        else:
            configured.pop(endpoint_name, None)

    if configured:
        materialized["ai_platform"] = configured
    else:
        materialized.pop("ai_platform", None)
    return materialized


def materialize_image_analysis_config(section: Any, *, settings: Any = None) -> dict[str, Any] | None:
    """The canonical image_analysis section with the deployment's endpoints, or None when it is off.

    The stored section holds the switch, the model and the account
    (``ai_platform.auth``: username, password, usercase). What a runtime, a
    connection test or an app's image call needs on top is where AI Platform
    lives, which is the deployment's to say: the chat/completions endpoint,
    the iB2B endpoint, the trust-token header and the tracking prefix. The
    Responses endpoint is never carried: inspect-image speaks
    chat/completions only. ``settings`` defaults to the Portal's own.

    A section that is on comes back even when its account or the deployment's
    endpoints are incomplete; ``image_analysis_configured`` says whether it
    can run.
    """
    source = section if isinstance(section, dict) else {}
    if not flag_enabled(source.get("enabled")):
        return None

    configured = configured_ai_platform_config(settings)
    profile_auth = profile_ai_platform_config(source.get("ai_platform")).get("auth", {})
    ai_platform: dict[str, Any] = {}
    chat = dict(configured.get("chat") or {})
    # The same ``{usercase}`` rule as the chat provider's endpoints: filled
    # from the account, and without a usercase the endpoint cannot be
    # addressed, so it is left out rather than sent with the placeholder.
    uri = str(chat.get("uri") or "")
    if USERCASE_PLACEHOLDER in uri:
        usercase = str(profile_auth.get("usercase") or "").strip()
        if usercase:
            chat["uri"] = uri.replace(USERCASE_PLACEHOLDER, quote(usercase, safe=""))
        else:
            chat = {}
    if chat:
        ai_platform["chat"] = chat
    if configured.get("ib2b"):
        ai_platform["ib2b"] = dict(configured["ib2b"])
    auth = {**profile_auth, **(configured.get("auth") or {})}
    if auth:
        ai_platform["auth"] = auth

    materialized: dict[str, Any] = {
        "enabled": True,
        "model": coerce_to_provider_model(AI_PLATFORM_PROVIDER, source.get("model")),
    }
    if ai_platform:
        materialized["ai_platform"] = ai_platform
    return materialized


def image_analysis_configured(section: Any) -> bool:
    """Whether a materialized image_analysis section can run inspect-image.

    The rule the native runtime applies to the same section: it is on, the
    account is complete (username, password, usercase) and the deployment
    names both the chat host and the iB2B host. A stored section, which
    carries no endpoints, is never "configured"; materialize it first.
    """
    if not isinstance(section, dict) or not flag_enabled(section.get("enabled")):
        return False
    ai_platform = section.get("ai_platform") if isinstance(section.get("ai_platform"), dict) else {}

    def text(block: str, key: str) -> str:
        value = ai_platform.get(block)
        return str(value.get(key) or "").strip() if isinstance(value, dict) else ""

    if not all(text("auth", key) for key in PROFILE_AUTH_FIELDS):
        return False
    return bool(text("chat", "host")) and bool(text("ib2b", "host"))
