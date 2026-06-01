from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.runtime_profile_llm_projection import project_llm_for_runtime


PORTAL_RUNTIME_PROFILE_SECTIONS = (
    "llm",
    "proxy",
    "jira",
    "confluence",
    "github",
    "git",
    "debug",
)

OPENCODE_RUNTIME_RESTRICTION_FIELDS = frozenset(
    {
        "enabled_tools",
        "disabled_tools",
        "tool_permissions",
        "allowed_external_systems",
        "allowed_actions",
        "allowed_adapter_actions",
        "allowed_capability_ids",
        "allowed_capability_types",
        "resolved_action_mappings",
        "unresolved_tools",
        "unresolved_skills",
        "unresolved_channels",
        "unresolved_actions",
        "skill_details",
        "allowed_skills",
        "denied_skills",
        "denied_actions",
        "denied_capability_types",
        "skill_set",
        "policy_context",
        "derived_runtime_rules",
    }
)


def is_opencode_runtime_type(runtime_type: str | None) -> bool:
    return str(runtime_type or "").strip().lower() == "opencode"


def strip_opencode_runtime_restrictions(
    config: dict[str, Any] | None,
    runtime_type: str | None,
) -> dict[str, Any]:
    projected = deepcopy(config) if isinstance(config, dict) else {}
    if not is_opencode_runtime_type(runtime_type):
        return projected
    for key in OPENCODE_RUNTIME_RESTRICTION_FIELDS:
        projected.pop(key, None)
    return projected


def _strip_runtime_owned_llm_fields(config: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(config)
    llm = projected.get("llm")
    if isinstance(llm, dict):
        llm_projected = deepcopy(llm)
        llm_projected.pop("tools", None)
        llm_projected.pop("tool_loop", None)
        if llm_projected:
            projected["llm"] = llm_projected
        else:
            projected.pop("llm", None)
    return projected


def _with_default_llm_fields(llm: dict[str, Any], default_llm: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(default_llm, dict):
        return llm

    projected = deepcopy(llm)
    for key in ("provider", "model"):
        if not str(projected.get(key) or "").strip() and str(default_llm.get(key) or "").strip():
            projected[key] = default_llm.get(key)
    return projected


def build_runtime_profile_context_config(
    config: dict[str, Any] | None,
    *,
    runtime_type: str = "native",
    default_llm: dict[str, Any] | None = None,
    include_portal_sections: bool = True,
    include_llm_credentials: bool = True,
) -> dict[str, Any]:
    sanitized = sanitize_runtime_profile_config_dict(config or {})
    canonical = canonicalize_portal_runtime_profile_config(sanitized)

    if not include_portal_sections:
        canonical = {
            key: deepcopy(canonical[key])
            for key in PORTAL_RUNTIME_PROFILE_SECTIONS
            if key in canonical
        }

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

    canonical = _strip_runtime_owned_llm_fields(canonical)
    return strip_opencode_runtime_restrictions(canonical, runtime_type)
