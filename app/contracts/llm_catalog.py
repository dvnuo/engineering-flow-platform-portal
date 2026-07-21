"""Single source of truth for the supported LLM catalog.

GitHub Copilot is the ONLY supported LLM provider. Any other provider
(openai, anthropic, ...) is coerced to Copilot at persist time
(canonicalize_portal_runtime_profile_config) and at projection time
(normalize_provider_for_portal). The selectable models are mirrored in the
frontend picker (app/static/js/chat_ui.js managedProviderModels) and in each
runtime's projection port.
"""
from __future__ import annotations

COPILOT_PROVIDER = "github_copilot"

# Selectable GitHub Copilot models.
COPILOT_MODELS: tuple[str, ...] = (
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)

DEFAULT_COPILOT_MODEL = "gpt-5.6-terra"


def coerce_to_copilot_model(model: str | None) -> str:
    """Return a valid Copilot model id, falling back to the default."""
    trimmed = str(model or "").strip()
    return trimmed if trimmed in COPILOT_MODELS else DEFAULT_COPILOT_MODEL
