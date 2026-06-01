def normalize_provider_for_portal(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"github", "github-copilot", "copilot", "github_copilot"}:
        return "github_copilot"
    if raw in {"claude", "anthropic"}:
        return "anthropic"
    return raw


def normalize_provider_for_runtime(runtime_type: str, provider: str | None) -> str:
    portal_provider = normalize_provider_for_portal(provider)
    if (runtime_type or "").strip().lower() == "opencode" and portal_provider == "github_copilot":
        return "github-copilot"
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
