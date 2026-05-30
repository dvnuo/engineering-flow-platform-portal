from __future__ import annotations

from copy import deepcopy
from typing import Any

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

    if llm:
        canonical["llm"] = llm
    else:
        canonical.pop("llm", None)

    return canonical
