from __future__ import annotations

from copy import deepcopy

from app.contracts.provider_projection import normalize_model_for_runtime, normalize_provider_for_portal, normalize_provider_for_runtime


def _is_copilot_provider(provider: str | None) -> bool:
    return normalize_provider_for_portal(provider) == "github_copilot"


def _provider_hint_from_llm(llm: dict) -> str | None:
    provider = llm.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    model = llm.get("model")
    if isinstance(model, str) and "/" in model:
        prefix = model.split("/", 1)[0].strip()
        if prefix:
            return prefix
    return None


def project_llm_for_runtime(llm: dict, runtime_type: str) -> dict:
    projected = deepcopy(llm)
    provider_hint = _provider_hint_from_llm(projected)
    runtime_type = "opencode" if str(runtime_type or "").strip().lower() == "opencode" else "native"
    if provider_hint:
        projected["provider"] = normalize_provider_for_runtime(runtime_type, provider_hint)
    if projected.get("model"):
        normalized_model = normalize_model_for_runtime(runtime_type, provider_hint, projected.get("model"))
        if normalized_model:
            projected["model"] = normalized_model

    if not _is_copilot_provider(provider_hint):
        projected.pop("oauth", None)
        projected.pop("oauth_by_runtime", None)
        return projected

    token = str(projected.get("api_key") or "").strip()
    projected.pop("oauth", None)
    projected.pop("oauth_by_runtime", None)

    if token:
        projected["api_key"] = token
    else:
        projected.pop("api_key", None)
    return projected
