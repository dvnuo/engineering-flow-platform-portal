from __future__ import annotations

from copy import deepcopy

from app.contracts.opencode_provider import normalize_provider_for_portal, normalize_provider_for_runtime


def _is_copilot_provider(provider: str | None) -> bool:
    return normalize_provider_for_portal(provider) == "github_copilot"


def _selected_copilot_oauth_for_runtime(llm: dict, runtime_type: str) -> dict | None:
    by_runtime = llm.get("oauth_by_runtime") if isinstance(llm.get("oauth_by_runtime"), dict) else {}
    if runtime_type == "opencode":
        if isinstance(by_runtime.get("opencode"), dict):
            return deepcopy(by_runtime["opencode"])
        if isinstance(llm.get("oauth"), dict):
            return deepcopy(llm["oauth"])
        return None
    if isinstance(by_runtime.get("native"), dict):
        return deepcopy(by_runtime["native"])
    return None



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
    has_oauth_by_runtime = isinstance(llm.get("oauth_by_runtime"), dict) and bool(llm.get("oauth_by_runtime"))
    if provider_hint:
        projected["provider"] = normalize_provider_for_runtime(runtime_type, provider_hint)
    if not _is_copilot_provider(provider_hint):
        projected.pop("oauth_by_runtime", None)
        return projected
    oauth = _selected_copilot_oauth_for_runtime(llm, runtime_type)
    if runtime_type == "opencode":
        if oauth:
            projected["oauth"] = oauth
            projected.pop("api_key", None)
        elif has_oauth_by_runtime:
            projected.pop("api_key", None)
            projected.pop("oauth", None)
        projected.pop("oauth_by_runtime", None)
        return projected
    if oauth:
        token = str(oauth.get("access") or oauth.get("refresh") or "").strip()
        if token:
            projected["api_key"] = token
    elif has_oauth_by_runtime:
        projected.pop("api_key", None)
    projected.pop("oauth", None)
    projected.pop("oauth_by_runtime", None)
    return projected
