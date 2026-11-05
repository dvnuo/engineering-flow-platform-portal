"""Single source of truth for the supported LLM catalog.

Two providers are supported: GitHub Copilot (default) and AI Platform (an
enterprise gateway reached via an iB2B credential->JWT exchange). Any other /
blank / unknown provider is normalized to the default (Copilot). The selectable
models are mirrored in the frontend picker (app/static/js/chat_ui.js
managedProviderModels) and in each runtime's projection.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

COPILOT_PROVIDER = "github_copilot"
AI_PLATFORM_PROVIDER = "ai_platform"

DEFAULT_PROVIDER = COPILOT_PROVIDER
SUPPORTED_PROVIDERS: tuple[str, ...] = (COPILOT_PROVIDER, AI_PLATFORM_PROVIDER)

# Selectable models per provider. Copilot offers the GPT-6 line (Astra: the
# flagship reasoning model; Sol: the balanced default for interactive and
# agentic coding; Luna: fast and cheap) plus GPT-6.1 Sol (GA in Copilot since
# 2026-09-29). gpt-5.4 and gpt-5.5 were dropped from Copilot when GPT-6
# arrived, and the GPT-5.6 line on 2026-10-19 (ahead of Terra's 2026-11-16
# retirement); a profile still holding one of them is repaired to
# DEFAULT_COPILOT_MODEL by coerce_to_provider_model. Copilot's own catalog
# reports a 400,000-token window (272k prompt + 128k output) for the whole
# line, whatever OpenAI publishes; the runtimes budget from that, so the 1M
# preset below is clamped by them.
#
# The auto ids are Copilot's auto model selection, not model ids: the
# runtimes post each turn's prompt and a tier to Copilot's ``POST /auto`` (the
# way VS Code's Auto picker works) and send the model Copilot picked with the
# session token. "auto" is the Balance tier, the two others VS Code's
# selectable alternatives; the tier is part of the id so nothing else has to
# carry it (native: efp_runtime/llm/models.py COPILOT_AUTO_TIERS; opencode:
# the adapter's copilot_auto_catalog). Copilot-only.
COPILOT_AUTO_MODEL = "auto"
COPILOT_AUTO_TIERS: dict[str, str] = {
    COPILOT_AUTO_MODEL: "balance",
    "auto-efficiency": "efficiency",
    "auto-intelligence": "intelligence",
}
COPILOT_AUTO_MODELS: tuple[str, ...] = tuple(COPILOT_AUTO_TIERS)
COPILOT_MODELS: tuple[str, ...] = (
    *COPILOT_AUTO_MODELS,
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
)
# The gateway fronts gpt-5.4 and the GPT-5.6 line and does not serve GPT-6,
# so the two providers no longer share a model: switching a profile's
# provider always coerces its model to the target provider's default
# (coerce_to_provider_model only keeps a model the target provider has).
AI_PLATFORM_MODELS: tuple[str, ...] = (
    "gpt-5.4",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)

# Since 2026-10-20 a Copilot profile defaults to auto (Balance): Copilot picks
# the model per turn (the runtimes fall back to gpt-6-sol when the route
# fails or they cannot drive the pick). It is also the repair value
# coerce_to_provider_model lands a retired or unknown Copilot model on.
DEFAULT_COPILOT_MODEL = COPILOT_AUTO_MODEL
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
#
# Copilot bills per token (GitHub AI Credits, since 2026-06-01): reasoning
# tokens are output tokens, so "high" as the default made every agentic turn
# pay for thinking it rarely needed. Medium is the default; a session or a
# profile picks high (or more) on purpose.
SUPPORTED_REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "medium"
# A request whose prompt exceeds this many input tokens is billed on the GPT-6
# line's "long context" tier, at twice the default price (GitHub Docs, Models
# and pricing for GitHub Copilot). The 1M preset is above it, so the picker
# says so; the runtimes report the threshold on context_usage snapshots.
COPILOT_LONG_CONTEXT_THRESHOLD_TOKENS = 272_000
CONTEXT_SIZE_PRESETS: tuple[int, ...] = (64_000, 128_000, 256_000, 1_000_000)
DEFAULT_CONTEXT_SIZE = 256_000
MAX_CONTEXT_SIZE = CONTEXT_SIZE_PRESETS[-1]


def context_size_label(size: int) -> str:
    """The picker label for a context preset: ``64K``, ``1M \u00b7 Long context 2\u00d7``.

    Shared by every form that lists the presets (admin defaults, a member's
    Model provider connector, the composer mirrors it in chat_ui.js) so the
    long-context warning cannot be present in one place and missing in another.
    """
    value = int(size)
    base = "1M" if value >= 1_000_000 else f"{value // 1000}K"
    if value > COPILOT_LONG_CONTEXT_THRESHOLD_TOKENS:
        # The same key chat_ui.js uses; called while a page renders, so t()
        # sees the member's language.
        from app.i18n import t

        return t("{size} \u00b7 Long context 2\u00d7", size=base)
    return base


def context_size_choices(provider: str | None = None, model: str | None = None) -> list[tuple[int, str]]:
    """The presets with their labels: the whole ladder, or what ``model`` takes when one is named."""
    sizes = context_size_options(provider, model) if model else CONTEXT_SIZE_PRESETS
    return [(size, context_size_label(size)) for size in sizes]

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

# Copilot's own catalog (GET /models, which the runtimes surface as
# GET /api/models) is where the picker's list comes from at run time; the
# tuple above is the offline default and the labels. A model id the catalog
# lists but this file does not is therefore legitimate, so validation accepts
# any plausible Copilot id and leaves the final word to the runtime, which
# rejects what Copilot does not serve (`invalid_model`). Ids GitHub retired
# from Copilot (the GPT-4 and GPT-5 lines) are the exception: a profile still
# holding one is repaired to the default rather than sent.
RETIRED_COPILOT_MODEL_PREFIXES: tuple[str, ...] = ("gpt-4", "gpt-5")
_COPILOT_MODEL_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")

# The Portal-wide copy of Copilot's catalog (app/services/model_catalog_service.py)
# registers the ids it lists here. Once it has, the catalog is the rule: a
# Copilot id is selectable when the catalog lists it (so GPT-5.3-Codex is fine
# while Copilot still serves it) and nothing else is, which is exactly what the
# pickers offer. Until a runtime has answered, the plausibility rule stands in.
_CATALOG_COPILOT_MODELS: set[str] = set()
# Model id -> context window (``capabilities.limits.max_context_window_tokens``
# as the runtime reports it). The context-size presets a member may pick for a
# model follow it (context_size_options); a model without one gets the ladder.
_CATALOG_CONTEXT_WINDOWS: dict[str, int] = {}
_CATALOG_STATE = {"known": False}


def register_catalog_models(model_ids, context_windows: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Make ``model_ids`` the catalog's list (replacing the previous one); returns it normalized.

    ``context_windows`` (model id -> window in tokens) is what
    ``model_context_window`` and the context-size presets read for a model
    the catalog lists; it is replaced along with the list.
    """
    normalized = tuple(sorted({str(item).strip().lower() for item in (model_ids or ()) if str(item or "").strip()}))
    _CATALOG_COPILOT_MODELS.clear()
    _CATALOG_COPILOT_MODELS.update(normalized)
    _CATALOG_CONTEXT_WINDOWS.clear()
    for model_id, window in (context_windows or {}).items():
        key = str(model_id or "").strip().lower()
        if key and isinstance(window, int) and not isinstance(window, bool) and window > 0:
            _CATALOG_CONTEXT_WINDOWS[key] = window
    _CATALOG_STATE["known"] = bool(normalized)
    return normalized


def catalog_models_known() -> bool:
    return _CATALOG_STATE["known"]


def catalog_model_ids() -> tuple[str, ...]:
    return tuple(sorted(_CATALOG_COPILOT_MODELS))


def catalog_context_window(model: str | None) -> int | None:
    """The context window the catalog reports for ``model`` (tokens), or None when it reports none."""
    return _CATALOG_CONTEXT_WINDOWS.get(str(model or "").strip().lower())


def clear_catalog_models() -> None:
    _CATALOG_COPILOT_MODELS.clear()
    _CATALOG_CONTEXT_WINDOWS.clear()
    _CATALOG_STATE["known"] = False


def is_retired_copilot_model(model: str | None) -> bool:
    """Whether ``model`` belongs to a line GitHub retired from Copilot."""
    text = str(model or "").strip().lower()
    return bool(text) and text.startswith(RETIRED_COPILOT_MODEL_PREFIXES)


def is_plausible_copilot_model_id(model: str | None) -> bool:
    """A well-formed, lower-case Copilot model id (``gpt-6.2-nova``) that GitHub has not retired."""
    text = str(model or "").strip()
    if not text or len(text) > 64 or text != text.lower():
        return False
    return bool(_COPILOT_MODEL_ID_RE.match(text)) and not is_retired_copilot_model(text)


def is_selectable_model(provider: str | None, model: str | None) -> bool:
    """Whether a member may pick ``model`` on ``provider``: the catalog's list, or on Copilot any plausible id."""
    canon = normalize_provider(provider)
    trimmed = str(model or "").strip()
    if not trimmed:
        return False
    if trimmed in PROVIDER_MODELS.get(canon, ()):
        return True
    if canon != COPILOT_PROVIDER:
        return False
    if catalog_models_known():
        return trimmed.lower() in _CATALOG_COPILOT_MODELS
    return is_plausible_copilot_model_id(trimmed)


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
    """The largest context ``model`` takes: the catalog's window when it reports one, else the ladder's top for a listed model."""
    normalized_provider = normalize_provider(provider)
    normalized_model = str(model or "").strip()
    if normalized_provider == COPILOT_PROVIDER:
        window = catalog_context_window(normalized_model)
        if window is not None:
            return window
    if normalized_model not in PROVIDER_MODELS.get(normalized_provider, ()):
        return None
    return MAX_CONTEXT_SIZE


def context_size_options(provider: str | None, model: str | None) -> tuple[int, ...]:
    """The context sizes a member may pick for ``model``: the presets up to its window, the window itself closing the list.

    A model whose window no catalog has reported gets the whole ladder, as
    before the catalog existed (the runtime clamps to what the model takes).
    """
    window = model_context_window(provider, model)
    if window is None or window >= MAX_CONTEXT_SIZE:
        return CONTEXT_SIZE_PRESETS
    options = [size for size in CONTEXT_SIZE_PRESETS if size <= window]
    if not options or options[-1] < window:
        options.append(window)
    return tuple(options)


def default_context_size(provider: str | None, model: str | None) -> int:
    """``DEFAULT_CONTEXT_SIZE`` when ``model`` offers it, else the largest option below it, else the smallest."""
    options = context_size_options(provider, model)
    if DEFAULT_CONTEXT_SIZE in options:
        return DEFAULT_CONTEXT_SIZE
    below = [size for size in options if size < DEFAULT_CONTEXT_SIZE]
    return below[-1] if below else options[0]


def effective_context_size(provider: str | None, model: str | None, requested: Any) -> int:
    """What a stored ``max_context_tokens`` means for ``model``.

    The value itself when the model offers it; the model's largest option
    when the value is above it (the runtime clamps to the window anyway);
    the managed default otherwise.
    """
    options = context_size_options(provider, model)
    if isinstance(requested, bool) or not isinstance(requested, int):
        return default_context_size(provider, model)
    if requested in options:
        return requested
    if requested > options[-1]:
        return options[-1]
    return default_context_size(provider, model)


def coerce_to_provider_model(provider: str | None, model: str | None) -> str:
    """Return a valid model id for the provider, falling back to its default."""
    canon = normalize_provider(provider)
    trimmed = str(model or "").strip()
    if trimmed in PROVIDER_MODELS.get(canon, ()):
        return trimmed
    # A Copilot id this file does not list may still be one the Portal-wide
    # catalog offers (is_selectable_model); only an id the catalog does not
    # list, or a retired / malformed one before any catalog, is repaired.
    if canon == COPILOT_PROVIDER and is_selectable_model(canon, trimmed):
        return trimmed
    return PROVIDER_DEFAULT_MODEL.get(canon, DEFAULT_COPILOT_MODEL)


def coerce_to_copilot_model(model: str | None) -> str:
    """Backwards-compatible helper: coerce to a valid Copilot model."""
    return coerce_to_provider_model(COPILOT_PROVIDER, model)


def is_copilot_auto_model(model: str | None) -> bool:
    """Whether ``model`` is one of the Copilot auto ids (bare or ``provider/`` prefixed)."""
    text = str(model or "").strip().lower()
    if "/" in text:
        text = text.rsplit("/", 1)[1]
    return text in COPILOT_AUTO_TIERS


def copilot_auto_tier(model: str | None) -> str | None:
    """The ``POST /auto`` tier an auto id asks for, or None for a concrete model."""
    text = str(model or "").strip().lower()
    if "/" in text:
        text = text.rsplit("/", 1)[1]
    return COPILOT_AUTO_TIERS.get(text)


_TRUE_FLAGS = frozenset({"1", "true", "on", "yes"})


def flag_enabled(value: object) -> bool:
    """A profile flag as a bool; form posts and stored JSON both reach here."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in _TRUE_FLAGS


def uses_ai_platform_credentials(llm: dict | None) -> bool:
    """Whether llm.ai_platform.auth is needed: only when AI Platform is the chat provider.

    Image analysis no longer runs on AI Platform (the inspect-image path was
    retired): attached images and documents go to Productivity Suite through
    the member's browser, which needs no credential here.
    """
    if not isinstance(llm, dict):
        return False
    return normalize_provider(llm.get("provider")) == AI_PLATFORM_PROVIDER
