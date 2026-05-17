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
        "no_proxy": True,
        "noProxy": True,
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
        "token": True,
        "access_token": True,
        "base_url": True,
        "api_base_url": True,
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
_TRUE_BOOL_VALUES = {"1", "true", "on", "yes", "y", "enabled"}
_FALSE_BOOL_VALUES = {"0", "false", "off", "no", "n", "disabled", ""}


def _runtime_profile_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in _TRUE_BOOL_VALUES:
        return True
    if normalized in _FALSE_BOOL_VALUES:
        return False
    return False


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




def sanitize_runtime_profile_external_instances(value, *, kind: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    sanitized_instances: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        sanitized_item: dict = {}
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip().rstrip("/")
        username = str(item.get("username") or item.get("email") or "").strip()
        password = str(item.get("password") or "").strip()
        token = str(item.get("token") or item.get("api_token") or "").strip()
        if name:
            sanitized_item["name"] = name
        if url:
            sanitized_item["url"] = url
        if username:
            sanitized_item["username"] = username
        if password:
            sanitized_item["password"] = password
        if token:
            sanitized_item["token"] = token
        if "enabled" in item:
            sanitized_item["enabled"] = _runtime_profile_bool(item.get("enabled"))
        if kind == "jira":
            project = str(item.get("project") or item.get("project_key") or "").strip()
            if project:
                sanitized_item["project"] = project
            api_version = str(item.get("api_version") or "").strip()
            if api_version in {"2", "3"}:
                sanitized_item["api_version"] = api_version
        if kind == "confluence":
            space = str(item.get("space") or item.get("space_key") or "").strip()
            if space:
                sanitized_item["space"] = space
        if not sanitized_item.get("name") and not sanitized_item.get("url"):
            continue
        sanitized_instances.append(sanitized_item)
    return sanitized_instances


def sanitize_runtime_profile_jira(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    if "instances" in value:
        out["instances"] = sanitize_runtime_profile_external_instances(value.get("instances"), kind="jira")
    return out


def sanitize_runtime_profile_confluence(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    if "instances" in value:
        out["instances"] = sanitize_runtime_profile_external_instances(value.get("instances"), kind="confluence")
    return out


def sanitize_runtime_profile_github(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    token = str(value.get("api_token") or value.get("token") or value.get("access_token") or "").strip()
    if token:
        out["api_token"] = token
    base_url = str(value.get("base_url") or value.get("api_base_url") or "").strip().rstrip("/")
    if base_url:
        out["base_url"] = base_url
    return out


def sanitize_runtime_profile_proxy(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    for key in ("url", "username", "password"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
    raw_no_proxy = None
    if "no_proxy" in value:
        raw_no_proxy = value.get("no_proxy")
    elif "noProxy" in value:
        raw_no_proxy = value.get("noProxy")
    if isinstance(raw_no_proxy, str):
        cleaned_no_proxy = raw_no_proxy.strip()
        if cleaned_no_proxy:
            out["no_proxy"] = cleaned_no_proxy
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
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    log_level = str(value.get("log_level") or "").strip().upper()
    if log_level in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        out["log_level"] = log_level
    return out





def _runtime_profile_llm_looks_like_copilot(llm: dict) -> bool:
    provider = str((llm or {}).get("provider") or "").strip().lower().replace("-", "_")
    if provider in {"github", "copilot", "github_copilot"}:
        return True
    model = str((llm or {}).get("model") or "").strip().lower()
    return model.startswith("github_copilot/") or model.startswith("github-copilot/")


def _legacy_copilot_token_from_llm(raw_llm: dict) -> str:
    if not isinstance(raw_llm, dict):
        return ""
    api_key = str(raw_llm.get("api_key") or "").strip()
    if api_key:
        return api_key

    by_runtime = raw_llm.get("oauth_by_runtime") if isinstance(raw_llm.get("oauth_by_runtime"), dict) else {}
    for key in ("opencode", "native"):
        oauth = sanitize_runtime_profile_llm_oauth(by_runtime.get(key))
        token = str(oauth.get("access") or oauth.get("refresh") or "").strip() if oauth else ""
        if token:
            return token

    oauth = sanitize_runtime_profile_llm_oauth(raw_llm.get("oauth"))
    return str(oauth.get("access") or oauth.get("refresh") or "").strip() if oauth else ""

def sanitize_runtime_profile_config_dict(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    top_level = {key: value for key, value in data.items() if key in ALLOWED_RUNTIME_PROFILE_SECTIONS}
    sanitized = _filter_by_field_tree(top_level, PORTAL_MANAGED_FIELD_TREE) or {}
    raw_llm = top_level.get("llm") if isinstance(top_level.get("llm"), dict) else {}
    if "tools" in raw_llm:
        sanitized_llm = sanitized.get("llm") if isinstance(sanitized.get("llm"), dict) else {}
        sanitized_llm = sanitized_llm.copy()
        sanitized_llm["tools"] = raw_llm.get("tools")
        sanitized["llm"] = sanitized_llm
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
        api_key = str(llm_copy.get("api_key") or "").strip()
        if api_key:
            llm_copy["api_key"] = api_key
        else:
            migrated = ""
            if _runtime_profile_llm_looks_like_copilot(raw_llm) or _runtime_profile_llm_looks_like_copilot(llm_copy):
                migrated = _legacy_copilot_token_from_llm(raw_llm)
            if migrated:
                llm_copy["api_key"] = migrated
            else:
                llm_copy.pop("api_key", None)
        llm_copy.pop("oauth", None)
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
        llm.pop("oauth", None)
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
