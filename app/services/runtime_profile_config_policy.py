from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.contracts.llm_catalog import coerce_to_provider_model, normalize_provider

HIDDEN_PORTAL_LLM_FIELDS: tuple[str, ...] = (
    "temperature",
    "response_flow",
    "tools",
    "tool_loop",
    "context_budget",
    "context_projection",
)
REMOVED_PORTAL_LLM_CREDENTIAL_FIELDS: tuple[str, ...] = ("oauth", "oauth_by_runtime")


def canonicalize_portal_runtime_profile_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return a sparse Portal-managed runtime profile config with hidden LLM knobs normalized."""
    canonical = deepcopy(config) if isinstance(config, dict) else {}

    llm = canonical.get("llm")
    if isinstance(llm, dict):
        llm = deepcopy(llm)
    else:
        llm = {}

    for key in HIDDEN_PORTAL_LLM_FIELDS:
        llm.pop(key, None)
    for key in REMOVED_PORTAL_LLM_CREDENTIAL_FIELDS:
        llm.pop(key, None)

    # Normalize the provider to a supported one (github_copilot or ai_platform;
    # blank/unknown/legacy -> github_copilot) and coerce the model to a valid one
    # for that provider. Keeps the persisted canonical config — and everything
    # projected from it into the runtime Secret — on a supported provider/model
    # pair. Runs on every save and via sanitize_all_persisted_runtime_profiles.
    if llm.get("provider") or llm.get("model"):
        provider = normalize_provider(llm.get("provider"))
        llm["provider"] = provider
        if llm.get("model") is not None:
            llm["model"] = coerce_to_provider_model(provider, llm.get("model"))

    if llm:
        canonical["llm"] = llm
    else:
        canonical.pop("llm", None)

    return canonical
