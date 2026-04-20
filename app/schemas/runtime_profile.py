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
        "automation": {
            "assignments": {
                "enabled": True,
                "projects": True,
            },
            "mentions": {
                "enabled": True,
                "projects": True,
            },
        },
    },
    "confluence": {
        "enabled": True,
        "instances": True,
        "automation": {
            "mentions": {
                "enabled": True,
                "spaces": True,
            },
        },
    },
    "github": {
        "enabled": True,
        "api_token": True,
        "base_url": True,
        "automation": {
            "review_requests": {
                "enabled": True,
                "repos": True,
            },
            "mentions": {
                "enabled": True,
                "repos": True,
                "include_review_comments": True,
            },
        },
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
        sanitized["llm"] = llm_copy
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
