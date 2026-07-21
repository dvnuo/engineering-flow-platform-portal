"""Single source of truth for the supported LLM catalog.

Two providers are supported: GitHub Copilot (default) and AI Platform (an
enterprise gateway reached via an iB2B credential->JWT exchange). Any other /
blank / unknown provider is normalized to the default (Copilot). The selectable
models are mirrored in the frontend picker (app/static/js/chat_ui.js
managedProviderModels) and in each runtime's projection.
"""
from __future__ import annotations

COPILOT_PROVIDER = "github_copilot"
AI_PLATFORM_PROVIDER = "ai_platform"

DEFAULT_PROVIDER = COPILOT_PROVIDER
SUPPORTED_PROVIDERS: tuple[str, ...] = (COPILOT_PROVIDER, AI_PLATFORM_PROVIDER)

# Selectable models per provider.
COPILOT_MODELS: tuple[str, ...] = (
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)
AI_PLATFORM_MODELS: tuple[str, ...] = ("gpt-5.4",)

DEFAULT_COPILOT_MODEL = "gpt-5.6-terra"
DEFAULT_AI_PLATFORM_MODEL = "gpt-5.4"

PROVIDER_MODELS: dict[str, tuple[str, ...]] = {
    COPILOT_PROVIDER: COPILOT_MODELS,
    AI_PLATFORM_PROVIDER: AI_PLATFORM_MODELS,
}
PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    COPILOT_PROVIDER: DEFAULT_COPILOT_MODEL,
    AI_PLATFORM_PROVIDER: DEFAULT_AI_PLATFORM_MODEL,
}

# The nested rich-config keys that AI Platform profiles persist under llm.ai_platform
# (mirrors the tools inspect-image ai_platform config shape). Copilot needs none
# of these — only provider/model (+ optional api_key/base_url).
AI_PLATFORM_LLM_SUBTREE = {
    "chat": {"host": True, "uri": True},
    "ib2b": {"host": True, "uri": True},
    "auth": {
        "username": True,
        "password": True,
        "usercase": True,
        "trust_token_header": True,
        "tracking_prefix": True,
    },
}

_PROVIDER_ALIASES = {
    "github_copilot": COPILOT_PROVIDER,
    "github-copilot": COPILOT_PROVIDER,
    "github": COPILOT_PROVIDER,
    "copilot": COPILOT_PROVIDER,
    "ai_platform": AI_PLATFORM_PROVIDER,
    "ai-platform": AI_PLATFORM_PROVIDER,
    "ai platform": AI_PLATFORM_PROVIDER,
}


def normalize_provider(value: str | None) -> str:
    """Canonical provider from any alias; blank/unknown -> DEFAULT_PROVIDER."""
    v = str(value or "").strip().lower()
    if not v:
        return DEFAULT_PROVIDER
    return _PROVIDER_ALIASES.get(v, DEFAULT_PROVIDER)


def models_for_provider(provider: str | None) -> tuple[str, ...]:
    return PROVIDER_MODELS.get(normalize_provider(provider), ())


def default_model_for_provider(provider: str | None) -> str:
    return PROVIDER_DEFAULT_MODEL.get(normalize_provider(provider), DEFAULT_COPILOT_MODEL)


def coerce_to_provider_model(provider: str | None, model: str | None) -> str:
    """Return a valid model id for the provider, falling back to its default."""
    canon = normalize_provider(provider)
    trimmed = str(model or "").strip()
    if trimmed in PROVIDER_MODELS.get(canon, ()):
        return trimmed
    return PROVIDER_DEFAULT_MODEL.get(canon, DEFAULT_COPILOT_MODEL)


def coerce_to_copilot_model(model: str | None) -> str:
    """Backwards-compatible helper: coerce to a valid Copilot model."""
    return coerce_to_provider_model(COPILOT_PROVIDER, model)
