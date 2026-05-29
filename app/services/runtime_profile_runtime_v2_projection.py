from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.schemas.runtime_profile import (
    RUNTIME_V2_CONFIG_FIELD_NAMES,
    sanitize_runtime_profile_config_dict,
    sanitize_runtime_profile_tool_loop,
)
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.runtime_profile_llm_projection import project_llm_for_runtime


def _copy_runtime_v2_fields(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(config[key])
        for key in sorted(RUNTIME_V2_CONFIG_FIELD_NAMES)
        if key in config
    }


def _with_default_llm_fields(llm: dict[str, Any], default_llm: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(default_llm, dict):
        return llm

    projected = deepcopy(llm)
    for key in ("provider", "model"):
        if not str(projected.get(key) or "").strip() and str(default_llm.get(key) or "").strip():
            projected[key] = default_llm.get(key)

    if "tool_loop" not in projected and isinstance(default_llm.get("tool_loop"), dict):
        projected["tool_loop"] = deepcopy(default_llm.get("tool_loop"))

    if isinstance(projected.get("tool_loop"), dict):
        sanitized_tool_loop = sanitize_runtime_profile_tool_loop(projected.get("tool_loop"))
        if sanitized_tool_loop:
            projected["tool_loop"] = sanitized_tool_loop
        else:
            projected.pop("tool_loop", None)

    return projected


def build_trusted_runtime_v2_config(
    config: dict[str, Any] | None,
    *,
    runtime_type: str = "",
    default_llm: dict[str, Any] | None = None,
    include_portal_sections: bool = True,
    include_llm_credentials: bool = True,
) -> dict[str, Any]:
    """Project a sanitized profile into the Runtime v2 config shape Portal trusts."""
    sanitized = sanitize_runtime_profile_config_dict(config or {})
    canonical = canonicalize_portal_runtime_profile_config(sanitized)
    if not include_portal_sections:
        llm = canonical.get("llm")
        canonical = _copy_runtime_v2_fields(canonical)
        if isinstance(llm, dict):
            canonical["llm"] = deepcopy(llm)
        elif isinstance(default_llm, dict):
            canonical["llm"] = {}

    llm = canonical.get("llm")
    if not isinstance(llm, dict) and isinstance(default_llm, dict):
        llm = {}
        canonical["llm"] = llm
    if isinstance(llm, dict):
        llm = _with_default_llm_fields(llm, default_llm)
        projected_llm = project_llm_for_runtime(llm, runtime_type)
        if not include_llm_credentials:
            projected_llm.pop("api_key", None)
            projected_llm.pop("oauth", None)
            projected_llm.pop("oauth_by_runtime", None)
        if projected_llm:
            canonical["llm"] = projected_llm
        else:
            canonical.pop("llm", None)

    return canonical
