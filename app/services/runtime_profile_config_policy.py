from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.schemas.runtime_profile import RUNTIME_V2_TOOL_SELECTION_FIELDS

DEFAULT_PORTAL_LLM_TOOLS: list[str] = ["*"]
HIDDEN_PORTAL_LLM_FIELDS: tuple[str, ...] = ("temperature", "response_flow")
REMOVED_PORTAL_LLM_CREDENTIAL_FIELDS: tuple[str, ...] = ("oauth", "oauth_by_runtime")


def has_runtime_v2_tool_selection(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    return any(key in config for key in RUNTIME_V2_TOOL_SELECTION_FIELDS)


def canonicalize_portal_runtime_profile_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return a sparse Portal-managed runtime profile config with hidden LLM knobs normalized."""
    canonical = deepcopy(config) if isinstance(config, dict) else {}
    has_explicit_runtime_v2_tools = has_runtime_v2_tool_selection(canonical)

    llm = canonical.get("llm")
    if isinstance(llm, dict):
        llm = deepcopy(llm)
    else:
        llm = {}

    for key in HIDDEN_PORTAL_LLM_FIELDS:
        llm.pop(key, None)
    for key in REMOVED_PORTAL_LLM_CREDENTIAL_FIELDS:
        llm.pop(key, None)

    if not has_explicit_runtime_v2_tools:
        llm["tools"] = list(DEFAULT_PORTAL_LLM_TOOLS)

    if llm or not has_explicit_runtime_v2_tools or isinstance(canonical.get("llm"), dict):
        canonical["llm"] = llm
    else:
        canonical.pop("llm", None)

    return canonical
