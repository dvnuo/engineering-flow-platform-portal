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

# Selectable models per provider. Copilot offers the GPT-6 line (Astra: the
# flagship reasoning model; Sol: the balanced default for interactive and
# agentic coding; Luna: fast and cheap) next to the GPT-5.6 line. gpt-5.4 and
# gpt-5.5 were dropped from Copilot when GPT-6 arrived; a profile still holding
# one of them is repaired to DEFAULT_COPILOT_MODEL by coerce_to_provider_model.
# All three GPT-6 models publish a 1,050,000-token window (922k input + 128k
# output), so the 1M preset below still fits.
COPILOT_MODELS: tuple[str, ...] = (
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
)
# The gateway fronts the GPT-5.6 line, so the variants are offered here too.
# Keeping the shared models aligned means switching a profile between
# providers keeps its model instead of being coerced back to 5.4 -- the
# fallback in coerce_to_provider_model only fires for a model the target
# provider does not have. The GPT-6 line is Copilot-only until the gateway
# serves it.
AI_PLATFORM_MODELS: tuple[str, ...] = (
    "gpt-5.4",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)

DEFAULT_COPILOT_MODEL = "gpt-6-sol"
# Deliberately still 5.4: this is the repair value coerce_to_provider_model
# falls back to for an unrecognized model, not a recommendation, and moving it
# would silently change which model an existing broken profile lands on.
DEFAULT_AI_PLATFORM_MODEL = "gpt-5.4"

PROVIDER_MODELS: dict[str, tuple[str, ...]] = {
    COPILOT_PROVIDER: COPILOT_MODELS,
    AI_PLATFORM_PROVIDER: AI_PLATFORM_MODELS,
}
PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    COPILOT_PROVIDER: DEFAULT_COPILOT_MODEL,
    AI_PLATFORM_PROVIDER: DEFAULT_AI_PLATFORM_MODEL,
}

# Request-level inference controls. Context-size controls are currently
# supported by the native runtime; the OpenCode adapter exposes reasoning as a
# model variant but owns its context window internally.
SUPPORTED_REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "high"
CONTEXT_SIZE_PRESETS: tuple[int, ...] = (64_000, 256_000, 1_000_000)
DEFAULT_CONTEXT_SIZE = 256_000
MAX_CONTEXT_SIZE = CONTEXT_SIZE_PRESETS[-1]

# Runtime Profiles persist only the user-specific AI Platform credentials.
# Endpoints and transport headers are deployment-managed in app/config.py and
# are injected only when Portal builds the effective runtime config.
AI_PLATFORM_LLM_SUBTREE = {
    "auth": {
        "username": True,
        "password": True,
        "usercase": True,
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


def model_context_window(provider: str | None, model: str | None) -> int | None:
    normalized_provider = normalize_provider(provider)
    normalized_model = str(model or "").strip()
    if normalized_model not in PROVIDER_MODELS.get(normalized_provider, ()):
        return None
    return MAX_CONTEXT_SIZE


def context_size_options(provider: str | None, model: str | None) -> tuple[int, ...]:
    _ = provider, model
    return CONTEXT_SIZE_PRESETS


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


# Image analysis ("vision"). GitHub Copilot's models no longer accept image
# input, so images are read by the inspect-image CLI through AI Platform
# whatever the chat provider is. The block lives at llm.vision and reuses the
# AI Platform credentials stored at llm.ai_platform.auth.
VISION_PROVIDER = AI_PLATFORM_PROVIDER
VISION_MODELS = AI_PLATFORM_MODELS
DEFAULT_VISION_MODEL = DEFAULT_AI_PLATFORM_MODEL
LLM_VISION_SUBTREE = {"enabled": True, "model": True}
_TRUE_FLAGS = frozenset({"1", "true", "on", "yes"})


def flag_enabled(value: object) -> bool:
    """A profile flag as a bool; form posts and stored JSON both reach here."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in _TRUE_FLAGS


def coerce_to_vision_model(model: str | None) -> str:
    """Return a valid image-analysis model id, falling back to the AI Platform default."""
    return coerce_to_provider_model(VISION_PROVIDER, model)


def vision_enabled(llm: dict | None) -> bool:
    vision = llm.get("vision") if isinstance(llm, dict) else None
    return isinstance(vision, dict) and flag_enabled(vision.get("enabled"))


def uses_ai_platform_credentials(llm: dict | None) -> bool:
    """Whether llm.ai_platform.auth is needed: AI Platform chat or image analysis."""
    if not isinstance(llm, dict):
        return False
    return normalize_provider(llm.get("provider")) == AI_PLATFORM_PROVIDER or vision_enabled(llm)


def vision_model_for(llm: dict | None) -> str:
    """The inspect-image model: vision.model, else the AI Platform chat model, else the default."""
    if not isinstance(llm, dict):
        return DEFAULT_VISION_MODEL
    vision = llm.get("vision") if isinstance(llm.get("vision"), dict) else {}
    model = str(vision.get("model") or "").strip()
    if not model and normalize_provider(llm.get("provider")) == AI_PLATFORM_PROVIDER:
        model = str(llm.get("model") or "").strip()
    return coerce_to_vision_model(model)
