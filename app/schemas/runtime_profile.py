import json
import math
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
        "base_url": True,
        "api_base": True,
        "baseURL": True,
        "endpoint": True,
        "timeout": True,
        "timeout_ms": True,
        "chunk_timeout_ms": True,
        "chunkTimeout": True,
        "temperature": True,
        "max_tokens": True,
        "tools": True,
        "context_budget": True,
        "context_projection": True,
        "response_flow": True,
        "oauth": {
            "type": True,
            "refresh": True,
            "access": True,
            "expires": True,
            "enterpriseUrl": True,
            "accountId": True,
        },
        "oauth_by_runtime": {
            "native": {"type": True, "refresh": True, "access": True, "expires": True, "enterpriseUrl": True, "accountId": True},
            "opencode": {"type": True, "refresh": True, "access": True, "expires": True, "enterpriseUrl": True, "accountId": True},
        },
        "tool_loop": {
            "one_tool_per_turn": True,
            "parallel_tool_calls": True,
            "max_repeated_tool_signature": True,
        },
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


def sanitize_runtime_profile_tool_loop(value) -> dict:
    if not isinstance(value, dict):
        return {}

    sanitized: dict = {}

    if "one_tool_per_turn" in value:
        if not isinstance(value.get("one_tool_per_turn"), bool):
            raise ValueError("llm.tool_loop.one_tool_per_turn must be a boolean")
        sanitized["one_tool_per_turn"] = value.get("one_tool_per_turn")

    if "parallel_tool_calls" in value:
        if not isinstance(value.get("parallel_tool_calls"), bool):
            raise ValueError("llm.tool_loop.parallel_tool_calls must be a boolean")
        sanitized["parallel_tool_calls"] = value.get("parallel_tool_calls")

    if "max_repeated_tool_signature" in value:
        raw_repeat = value.get("max_repeated_tool_signature")
        if isinstance(raw_repeat, bool):
            raise ValueError("llm.tool_loop.max_repeated_tool_signature must be an integer")
        try:
            parsed = int(raw_repeat)
        except (TypeError, ValueError):
            raise ValueError("llm.tool_loop.max_repeated_tool_signature must be an integer") from None
        if parsed < 1 or parsed > 10:
            raise ValueError("llm.tool_loop.max_repeated_tool_signature must be between 1 and 10")
        sanitized["max_repeated_tool_signature"] = parsed

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




def normalize_runtime_profile_model_id_for_capabilities(value) -> str:
    """Normalize a runtime-profile model reference for capability checks only.

    Do not use this to rewrite the persisted model field.
    """
    model = str(value or "").strip().lower()
    if not model:
        return ""
    for sep in ("/", ":"):
        if sep in model:
            model = model.rsplit(sep, 1)[-1].strip()
    return model


def runtime_profile_model_supports_temperature(model) -> bool:
    """Only exact gpt-4 may persist/use temperature."""
    return normalize_runtime_profile_model_id_for_capabilities(model) == "gpt-4"


def normalize_runtime_profile_temperature(value) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("llm.temperature must be a number between 0 and 2")

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("llm.temperature must be a number between 0 and 2")
        candidate = stripped
    else:
        candidate = value

    try:
        parsed = float(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError("llm.temperature must be a number between 0 and 2") from exc

    if not math.isfinite(parsed) or parsed < 0 or parsed > 2:
        raise ValueError("llm.temperature must be a number between 0 and 2")

    return parsed

def sanitize_runtime_profile_llm_oauth(value) -> dict:
    if not isinstance(value, dict):
        return {}
    oauth_type = str(value.get("type") or "oauth").strip()
    if oauth_type and oauth_type != "oauth":
        return {}
    access = str(value.get("access") or "").strip()
    refresh = str(value.get("refresh") or "").strip()
    if not refresh and access:
        refresh = access
    if not access and refresh:
        access = refresh
    if not access or not refresh:
        return {}
    try:
        expires = int(value.get("expires", 0))
    except (TypeError, ValueError):
        expires = 0
    if expires < 0:
        expires = 0
    sanitized = {"type": "oauth", "access": access, "refresh": refresh, "expires": expires}
    enterprise_url = str(value.get("enterpriseUrl") or "").strip()
    account_id = str(value.get("accountId") or "").strip()
    if enterprise_url:
        sanitized["enterpriseUrl"] = enterprise_url
    if account_id:
        sanitized["accountId"] = account_id
    return sanitized



def sanitize_runtime_profile_llm_oauth_by_runtime(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for runtime_key in ("native", "opencode"):
        oauth = sanitize_runtime_profile_llm_oauth(value.get(runtime_key))
        if oauth:
            out[runtime_key] = oauth
    return out


def sanitize_runtime_profile_external_instances(value, *, kind: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    allowed = {
        "jira": {"name", "url", "username", "password", "token", "project"},
        "confluence": {"name", "url", "username", "password", "token", "space"},
    }.get(kind, set())
    sanitized_instances: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        sanitized_item: dict = {}
        for key in allowed:
            if key not in item:
                continue
            cleaned = str(item.get(key) or "").strip()
            if key == "url" and cleaned:
                cleaned = cleaned.rstrip("/")
            if cleaned:
                sanitized_item[key] = cleaned
        if not sanitized_item.get("name") and not sanitized_item.get("url"):
            continue
        sanitized_instances.append(sanitized_item)
    return sanitized_instances


def sanitize_runtime_profile_jira(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = bool(value.get("enabled"))
    if "instances" in value:
        out["instances"] = sanitize_runtime_profile_external_instances(value.get("instances"), kind="jira")
    return out


def sanitize_runtime_profile_confluence(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = bool(value.get("enabled"))
    if "instances" in value:
        out["instances"] = sanitize_runtime_profile_external_instances(value.get("instances"), kind="confluence")
    return out


def sanitize_runtime_profile_github(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = bool(value.get("enabled"))
    token = str(value.get("api_token") or "").strip()
    if token:
        out["api_token"] = token
    base_url = str(value.get("base_url") or "").strip().rstrip("/")
    if base_url:
        out["base_url"] = base_url
    return out


def sanitize_runtime_profile_proxy(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = bool(value.get("enabled"))
    for key in ("url", "username", "password"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
    return out


def sanitize_runtime_profile_git(value) -> dict:
    if not isinstance(value, dict):
        return {}
    user = value.get("user")
    if not isinstance(user, dict):
        return {}
    out_user: dict = {}
    for key in ("name", "email"):
        cleaned = str(user.get(key) or "").strip()
        if cleaned:
            out_user[key] = cleaned
    return {"user": out_user} if out_user else {}


def sanitize_runtime_profile_debug(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = bool(value.get("enabled"))
    log_level = str(value.get("log_level") or "").strip().upper()
    if log_level in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        out["log_level"] = log_level
    return out

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

    if isinstance(llm, dict) and "tool_loop" in llm:
        llm_copy = llm.copy()
        sanitized_tool_loop = sanitize_runtime_profile_tool_loop(llm_copy.get("tool_loop"))
        if sanitized_tool_loop:
            llm_copy["tool_loop"] = sanitized_tool_loop
        else:
            llm_copy.pop("tool_loop", None)
        llm = llm_copy

    if isinstance(llm, dict):
        llm_copy = llm.copy()
        for key in ("base_url", "api_base", "baseURL", "endpoint"):
            if key in llm_copy:
                cleaned = str(llm_copy.get(key) or "").strip()
                if cleaned:
                    llm_copy[key] = cleaned
                else:
                    llm_copy.pop(key, None)
        for key in ("timeout", "timeout_ms", "chunk_timeout_ms", "chunkTimeout"):
            if key in llm_copy:
                raw_value = llm_copy.get(key)
                if isinstance(raw_value, bool):
                    llm_copy.pop(key, None)
                    continue
                try:
                    parsed = int(raw_value)
                except (TypeError, ValueError):
                    llm_copy.pop(key, None)
                    continue
                if parsed > 0:
                    llm_copy[key] = parsed
                else:
                    llm_copy.pop(key, None)
        if runtime_profile_model_supports_temperature(llm_copy.get("model")):
            if "temperature" in llm_copy:
                llm_copy["temperature"] = normalize_runtime_profile_temperature(llm_copy.get("temperature"))
        else:
            llm_copy.pop("temperature", None)
        if "oauth" in llm_copy:
            oauth = sanitize_runtime_profile_llm_oauth(llm_copy.get("oauth"))
            if oauth:
                llm_copy["oauth"] = oauth
            else:
                llm_copy.pop("oauth", None)
        oauth_by_runtime = sanitize_runtime_profile_llm_oauth_by_runtime(llm_copy.get("oauth_by_runtime"))
        if oauth_by_runtime:
            llm_copy["oauth_by_runtime"] = oauth_by_runtime
        else:
            llm_copy.pop("oauth_by_runtime", None)
        llm = llm_copy

    if isinstance(llm, dict):
        sanitized["llm"] = llm
    if "jira" in sanitized:
        sanitized["jira"] = sanitize_runtime_profile_jira(sanitized.get("jira"))
    if "confluence" in sanitized:
        sanitized["confluence"] = sanitize_runtime_profile_confluence(sanitized.get("confluence"))
    if "github" in sanitized:
        sanitized["github"] = sanitize_runtime_profile_github(sanitized.get("github"))
    if "proxy" in sanitized:
        sanitized["proxy"] = sanitize_runtime_profile_proxy(sanitized.get("proxy"))
    if "git" in sanitized:
        sanitized["git"] = sanitize_runtime_profile_git(sanitized.get("git"))
    if "debug" in sanitized:
        sanitized["debug"] = sanitize_runtime_profile_debug(sanitized.get("debug"))
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

    try:
        return sanitize_runtime_profile_config_dict(decoded)
    except ValueError:
        if fallback_to_empty:
            return {}
        raise


def dump_runtime_profile_config_json(data: dict) -> str:
    return json.dumps(sanitize_runtime_profile_config_dict(data))


def redact_runtime_profile_config_for_public_response(config: dict) -> dict:
    redacted = deepcopy(config) if isinstance(config, dict) else {}
    llm = redacted.get("llm")
    if isinstance(llm, dict):
        llm["api_key_present"] = bool(str(llm.pop("api_key", "")).strip())
        oauth = llm.get("oauth")
        if isinstance(oauth, dict):
            oauth_copy = {k: oauth.get(k) for k in ("type", "expires", "enterpriseUrl", "accountId") if k in oauth}
            oauth_copy["present"] = bool(str(oauth.get("access") or oauth.get("refresh") or "").strip())
            llm["oauth"] = oauth_copy
        by_runtime = llm.get("oauth_by_runtime")
        if isinstance(by_runtime, dict):
            redacted_by_runtime = {}
            for key in ("native", "opencode"):
                oauth_entry = by_runtime.get(key)
                if isinstance(oauth_entry, dict):
                    cp = {k: oauth_entry.get(k) for k in ("type", "expires", "enterpriseUrl", "accountId") if k in oauth_entry}
                    cp["present"] = bool(str(oauth_entry.get("access") or oauth_entry.get("refresh") or "").strip())
                    redacted_by_runtime[key] = cp
            if redacted_by_runtime:
                llm["oauth_by_runtime"] = redacted_by_runtime
            else:
                llm.pop("oauth_by_runtime", None)
    github = redacted.get("github")
    if isinstance(github, dict):
        github["api_token_present"] = bool(str(github.pop("api_token", "")).strip())
    proxy = redacted.get("proxy")
    if isinstance(proxy, dict):
        proxy["password_present"] = bool(str(proxy.pop("password", "")).strip())
    for section in ("jira", "confluence"):
        cfg = redacted.get(section)
        if not isinstance(cfg, dict):
            continue
        instances = cfg.get("instances")
        if not isinstance(instances, list):
            continue
        redacted_instances = []
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            inst_copy = inst.copy()
            inst_copy["password_present"] = bool(str(inst_copy.pop("password", "")).strip())
            inst_copy["token_present"] = bool(str(inst_copy.pop("token", "")).strip())
            redacted_instances.append(inst_copy)
        cfg["instances"] = redacted_instances
    return redacted


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
