# Kept self-contained (no imports) so the native/opencode runtime projection
# ports can copy this module's functions verbatim.
_AI_PLATFORM_ALIASES = {"ai_platform", "ai-platform", "ai platform"}


def normalize_provider_for_portal(value: str | None) -> str:
    # Two supported providers: github_copilot (default) and ai_platform.
    # Anything blank/unknown (incl. copilot aliases and legacy openai/anthropic)
    # falls back to github_copilot.
    raw = (value or "").strip().lower()
    if raw in _AI_PLATFORM_ALIASES:
        return "ai_platform"
    return "github_copilot"


def normalize_provider_for_runtime(runtime_type: str, provider: str | None) -> str:
    portal_provider = normalize_provider_for_portal(provider)
    if (runtime_type or "").strip().lower() == "opencode":
        if portal_provider == "github_copilot":
            return "github-copilot"
        if portal_provider == "ai_platform":
            return "ai-platform"
    return portal_provider


def normalize_model_for_runtime(runtime_type: str, provider: str | None, model: str | None) -> str | None:
    if not model:
        return None
    model = str(model).strip()
    runtime_provider = normalize_provider_for_runtime(runtime_type, provider)
    if "/" in model:
        prefix, rest = model.split("/", 1)
        normalized_prefix = normalize_provider_for_runtime(runtime_type, prefix)
        return f"{normalized_prefix}/{rest}"
    if (runtime_type or "").strip().lower() == "opencode" and runtime_provider:
        return f"{runtime_provider}/{model}"
    return model
