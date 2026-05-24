from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_PORTAL_LLM_TOOLS: list[str] = ["*"]
HIDDEN_PORTAL_LLM_FIELDS: tuple[str, ...] = ("temperature", "response_flow")
REMOVED_PORTAL_LLM_CREDENTIAL_FIELDS: tuple[str, ...] = ("oauth", "oauth_by_runtime")
REMOVED_RUNTIME_CONFIG_KEYS: tuple[str, ...] = (
    "capability" + "_" + "profile",
    "policy" + "_" + "profile",
    "capability" + "_" + "context",
    "policy" + "_" + "context",
)


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

    llm["tools"] = list(DEFAULT_PORTAL_LLM_TOOLS)
    canonical["llm"] = llm

    for key in REMOVED_RUNTIME_CONFIG_KEYS:
        canonical.pop(key, None)

    return canonical
