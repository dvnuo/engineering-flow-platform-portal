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

GITHUB_REVIEW_ACTION = "review_pull_request"
GITHUB_REVIEW_ADAPTER_ACTION = "adapter:github:review_pull_request"


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


def _merge_string_lists(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _as_string_list(value):
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _filter_broad_capability_types(values: Any) -> list[str]:
    return [
        item for item in _as_string_list(values)
        if item.strip().lower() not in {"skill", "tool"}
    ]


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


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


def grant_github_pr_review_from_runtime_profile(config: dict) -> dict:
    github = config.get("github") if isinstance(config, dict) else None
    if not isinstance(github, dict) or not github.get("enabled"):
        return config
    if not str(github.get("api_token") or "").strip():
        return config

    config["allowed_external_systems"] = _merge_string_lists(
        config.get("allowed_external_systems"),
        ["github"],
    )
    config["allowed_actions"] = _merge_string_lists(
        config.get("allowed_actions"),
        [GITHUB_REVIEW_ACTION],
    )
    config["allowed_adapter_actions"] = _merge_string_lists(
        config.get("allowed_adapter_actions"),
        [GITHUB_REVIEW_ADAPTER_ACTION],
    )
    config["allowed_capability_ids"] = _merge_string_lists(
        config.get("allowed_capability_ids"),
        [GITHUB_REVIEW_ADAPTER_ACTION],
    )
    config["allowed_capability_types"] = _filter_broad_capability_types(
        _merge_string_lists(config.get("allowed_capability_types"), ["adapter_action"])
    )
    resolved_mappings = _as_dict(config.get("resolved_action_mappings"))
    resolved_mappings[GITHUB_REVIEW_ACTION] = GITHUB_REVIEW_ADAPTER_ACTION
    config["resolved_action_mappings"] = resolved_mappings
    return config


def apply_runtime_profile_authorization(config: dict | None, raw_config: dict | None = None) -> dict:
    authorized_config = config if isinstance(config, dict) else {}
    preserve_raw_runtime_profile_authorization(authorized_config, raw_config)
    grant_github_pr_review_from_runtime_profile(authorized_config)
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
