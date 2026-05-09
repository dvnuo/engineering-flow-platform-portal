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


def project_llm_for_runtime(llm: dict, runtime_type: str) -> dict:
    projected = deepcopy(llm)
    provider = projected.get("provider")
    runtime_type = "opencode" if str(runtime_type or "").strip().lower() == "opencode" else "native"
    has_oauth_by_runtime = isinstance(llm.get("oauth_by_runtime"), dict) and bool(llm.get("oauth_by_runtime"))
    if provider:
        projected["provider"] = normalize_provider_for_runtime(runtime_type, provider)
    if not _is_copilot_provider(provider):
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
