from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


AUTHORIZATION_ALLOWLIST_KEYS: tuple[str, ...] = (
    "allowed_external_systems",
    "allowed_actions",
    "allowed_adapter_actions",
    "allowed_capability_ids",
    "allowed_capability_types",
    "resolved_action_mappings",
)

RUNTIME_AUTHORIZATION_EMPTY_LIST_KEYS: tuple[str, ...] = (
    "unresolved_tools",
    "unresolved_skills",
    "unresolved_channels",
    "unresolved_actions",
    "skill_details",
)


def raw_runtime_profile_config(runtime_profile: Any) -> dict:
    raw = getattr(runtime_profile, "config_json", None)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                result.append(normalized)
    return result


def _as_string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            continue
        normalized_key = key.strip()
        normalized_item = item.strip()
        if normalized_key and normalized_item:
            result[normalized_key] = normalized_item
    return result


def preserve_raw_runtime_profile_authorization(config: dict, raw_config: dict | None) -> dict:
    if not isinstance(raw_config, dict):
        return config
    for key in AUTHORIZATION_ALLOWLIST_KEYS:
        if key in raw_config and key not in config:
            config[key] = deepcopy(raw_config.get(key))
    return config


def apply_runtime_profile_authorization(config: dict | None, raw_config: dict | None = None) -> dict:
    authorized_config = config if isinstance(config, dict) else {}
    preserve_raw_runtime_profile_authorization(authorized_config, raw_config)
    return authorized_config


def runtime_profile_authorization_metadata(config: dict | None, raw_config: dict | None = None) -> dict:
    authorized_config = apply_runtime_profile_authorization(deepcopy(config) if isinstance(config, dict) else {}, raw_config)

    metadata: dict[str, Any] = {}
    for key in AUTHORIZATION_ALLOWLIST_KEYS:
        value = authorized_config.get(key)
        if key == "resolved_action_mappings":
            mappings = _as_string_dict(value)
            if mappings:
                metadata[key] = mappings
            continue
        values = _as_string_list(value)
        if values:
            metadata[key] = values

    if not metadata:
        return {}

    metadata["authorization_source"] = "runtime_profile"
    for key in RUNTIME_AUTHORIZATION_EMPTY_LIST_KEYS:
        metadata.setdefault(key, [])
    return metadata
