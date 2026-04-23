import json
from copy import deepcopy
from datetime import datetime

from pydantic import BaseModel, field_validator
from typing import Optional

ALLOWED_RUNTIME_PROFILE_SECTIONS = {
    "llm",
    "proxy",
    "jira",
    "confluence",
    "github",
    "git",
    "debug",
}

PORTAL_MANAGED_FIELD_TREE = {
    "llm": {
        "provider": True,
        "model": True,
        "api_key": True,
        "temperature": True,
        "max_tokens": True,
        "tools": True,
        "context_budget": True,
        "context_projection": True,
        "response_flow": True,
    },
    "proxy": {
        "enabled": True,
        "url": True,
        "username": True,
        "password": True,
    },
    "jira": {
        "enabled": True,
        "instances": True,
    },
    "confluence": {
        "enabled": True,
        "instances": True,
    },
    "github": {
        "enabled": True,
        "api_token": True,
        "base_url": True,
    },
    "git": {
        "user": {
            "name": True,
            "email": True,
        },
    },
    "debug": {
        "enabled": True,
        "log_level": True,
    },
}

_RESPONSE_FLOW_PLAN_POLICIES = {"explicit_or_complex", "always", "never"}
_RESPONSE_FLOW_STAGING_POLICIES = {"explicit_or_complex", "always", "never"}
_RESPONSE_FLOW_DEFAULT_SKILL_EXECUTION_STYLES = {"direct", "stepwise"}
_RESPONSE_FLOW_ASK_USER_POLICIES = {"blocked_only", "permissive"}
_RESPONSE_FLOW_ACTIVE_SKILL_CONFLICT_POLICIES = {"auto_switch_direct", "always_ask"}


def sanitize_runtime_profile_response_flow(value) -> dict:
    if not isinstance(value, dict):
        return {}

    sanitized: dict = {}

    plan_policy = value.get("plan_policy")
    if isinstance(plan_policy, str) and plan_policy in _RESPONSE_FLOW_PLAN_POLICIES:
        sanitized["plan_policy"] = plan_policy

    staging_policy = value.get("staging_policy")
    if isinstance(staging_policy, str) and staging_policy in _RESPONSE_FLOW_STAGING_POLICIES:
        sanitized["staging_policy"] = staging_policy

    default_skill_execution_style = value.get("default_skill_execution_style")
    if isinstance(default_skill_execution_style, str) and default_skill_execution_style in _RESPONSE_FLOW_DEFAULT_SKILL_EXECUTION_STYLES:
        sanitized["default_skill_execution_style"] = default_skill_execution_style

    ask_user_policy = value.get("ask_user_policy")
    if isinstance(ask_user_policy, str) and ask_user_policy in _RESPONSE_FLOW_ASK_USER_POLICIES:
        sanitized["ask_user_policy"] = ask_user_policy

    active_skill_conflict_policy = value.get("active_skill_conflict_policy")
    if isinstance(active_skill_conflict_policy, str) and active_skill_conflict_policy in _RESPONSE_FLOW_ACTIVE_SKILL_CONFLICT_POLICIES:
        sanitized["active_skill_conflict_policy"] = active_skill_conflict_policy

    ratio = value.get("complexity_prompt_budget_ratio")
    try:
        parsed_ratio = float(ratio)
        if 0 < parsed_ratio <= 1:
            sanitized["complexity_prompt_budget_ratio"] = parsed_ratio
    except (TypeError, ValueError):
        pass

    min_tokens = value.get("complexity_min_request_tokens")
    try:
        parsed_min_tokens = int(min_tokens)
        if parsed_min_tokens > 0:
            sanitized["complexity_min_request_tokens"] = parsed_min_tokens
    except (TypeError, ValueError):
        pass

    return sanitized


def _filter_by_field_tree(data, field_tree):
    if field_tree is True:
        return deepcopy(data)
    if not isinstance(field_tree, dict) or not isinstance(data, dict):
        return None
    filtered: dict = {}
    for key, subtree in field_tree.items():
        if key not in data:
            continue
        value = _filter_by_field_tree(data[key], subtree)
        if value is None:
            continue
        filtered[key] = value
    return filtered


def normalize_runtime_profile_llm_tools(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return ["*"] if text == "*" else [text]

    if not isinstance(value, list):
        raise ValueError("llm.tools must be a string or list of strings")

    normalized: list[str] = []
    seen_lower: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("llm.tools must be a string or list of strings")
        cleaned = item.strip()
        if not cleaned:
            continue
        if cleaned == "*":
            return ["*"]
        dedupe_key = cleaned.lower()
        if dedupe_key in seen_lower:
            continue
        seen_lower.add(dedupe_key)
        normalized.append(cleaned)
    return normalized


def sanitize_runtime_profile_config_dict(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    top_level = {key: value for key, value in data.items() if key in ALLOWED_RUNTIME_PROFILE_SECTIONS}
    sanitized = _filter_by_field_tree(top_level, PORTAL_MANAGED_FIELD_TREE) or {}
    llm = sanitized.get("llm")
    if isinstance(llm, dict) and "tools" in llm:
        llm_copy = llm.copy()
        llm_copy["tools"] = normalize_runtime_profile_llm_tools(llm_copy.get("tools"))
        llm = llm_copy

    if isinstance(llm, dict) and "response_flow" in llm:
        llm_copy = llm.copy()
        sanitized_response_flow = sanitize_runtime_profile_response_flow(llm_copy.get("response_flow"))
        if sanitized_response_flow:
            llm_copy["response_flow"] = sanitized_response_flow
        else:
            llm_copy.pop("response_flow", None)
        llm = llm_copy

    if isinstance(llm, dict):
        sanitized["llm"] = llm
    return sanitized


def parse_runtime_profile_config_json(raw: str | None, *, fallback_to_empty: bool = False) -> dict:
    text = (raw or "").strip() or "{}"
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        if fallback_to_empty:
            return {}
        raise ValueError("config_json must be valid JSON")

    if not isinstance(decoded, dict):
        if fallback_to_empty:
            return {}
        raise ValueError("config_json must decode to a JSON object")

    return sanitize_runtime_profile_config_dict(decoded)


def dump_runtime_profile_config_json(data: dict) -> str:
    return json.dumps(sanitize_runtime_profile_config_dict(data))


def validate_runtime_profile_config_json(value: str | None) -> str:
    raw = (value or "{}").strip() or "{}"
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("config_json must be valid JSON") from exc

    if not isinstance(decoded, dict):
        raise ValueError("config_json must decode to a JSON object")

    invalid_keys = sorted([key for key in decoded.keys() if key not in ALLOWED_RUNTIME_PROFILE_SECTIONS])
    if invalid_keys:
        raise ValueError(f"config_json has unsupported top-level sections: {', '.join(invalid_keys)}")

    return dump_runtime_profile_config_json(decoded)


class RuntimeProfileCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    config_json: str = "{}"
    is_default: bool = False

    _validate_config = field_validator("config_json", mode="before")(validate_runtime_profile_config_json)


class RuntimeProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config_json: Optional[str] = None
    is_default: Optional[bool] = None

    @field_validator("config_json", mode="before")
    @classmethod
    def _validate_optional_config(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_runtime_profile_config_json(value)


class RuntimeProfileResponse(BaseModel):
    id: str
    owner_user_id: int
    name: str
    description: Optional[str] = None
    config_json: str
    revision: int
    is_default: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RuntimeProfileOptionResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    revision: int
    is_default: bool
