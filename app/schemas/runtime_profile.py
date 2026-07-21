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
    "aws",
    "jenkins",
    "mobile-auto",
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
        "temperature": True,
        "max_tokens": True,
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
    "aws": {
        "enabled": True,
        "username": True,
        "password": True,
        "domain": True,
    },
    "jenkins": {
        "enabled": True,
        "url": True,
        "username": True,
        "password": True,
    },
    "mobile-auto": {
        "enabled": True,
        "default_provider": True,
        "state_dir": True,
        "artifacts_dir": True,
        "retention_hours": True,
        "defaults": {
            "platform": True,
            "network_mode": True,
            "idle_timeout_seconds": True,
            "new_command_timeout_seconds": True,
            "interactive_debugging": True,
            "video": True,
        },
        "browserstack": {
            "api_base_url": True,
            "appium_base_url": True,
            "username_env": True,
            "access_key_env": True,
            "username": True,
            "access_key": True,
            "verify_ssl": True,
            "ca_cert": True,
            "http_proxy": {
                "proxy_host": True,
                "proxy_port": True,
                "proxy_user_env": True,
                "proxy_pass_env": True,
                "no_proxy_hosts": True,
                "disable_proxy_discovery": True,
                "force_proxy": True,
            },
            "local": {
                "mode": True,
                "binary": True,
                "binary_env": True,
                "default_hold_minutes": True,
                "max_hold_minutes": True,
                "ready_timeout_seconds": True,
                "heartbeat_seconds": True,
                "force_local": True,
                "disable_proxy_discovery": True,
                "force_proxy": True,
                "proxy_host": True,
                "proxy_port": True,
                "proxy_user_env": True,
                "proxy_pass_env": True,
                "only_automate": True,
                "force": True,
                "include_hosts": True,
                "exclude_hosts": True,
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



def _first_nonblank_string(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        cleaned = str(item.get(key) or "").strip()
        if cleaned:
            return cleaned
    return ""


def sanitize_runtime_profile_external_instances(value, *, kind: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    sanitized_instances: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        sanitized_item: dict = {}
        name = str(item.get("name") or "").strip()
        url = _first_nonblank_string(item, ("url", "base_url", "baseUrl", "uri")).rstrip("/")
        username = str(item.get("username") or item.get("email") or "").strip()
        password = str(item.get("password") or "").strip()
        token = _first_nonblank_string(item, ("token", "api_token", "access_token"))
        if not url:
            continue
        if name:
            sanitized_item["name"] = name
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


def sanitize_runtime_profile_jenkins(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    url = str(value.get("url") or value.get("base_url") or "").strip().rstrip("/")
    if url:
        out["url"] = url
    for key in ("username", "password"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
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


def sanitize_runtime_profile_aws(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    for key in ("domain", "username", "password"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
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


def _sanitize_runtime_profile_positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _sanitize_runtime_profile_string_list(value) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    items: list[str] = []
    for item in raw_items:
        cleaned = str(item or "").strip()
        if cleaned:
            items.append(cleaned)
    return items


def _sanitize_mobile_http_proxy(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for key in ("proxy_host", "proxy_user_env", "proxy_pass_env"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
    proxy_port = _sanitize_runtime_profile_positive_int(value.get("proxy_port"))
    if proxy_port is not None:
        out["proxy_port"] = proxy_port
    no_proxy_hosts = _sanitize_runtime_profile_string_list(value.get("no_proxy_hosts"))
    if no_proxy_hosts:
        out["no_proxy_hosts"] = no_proxy_hosts
    for key in ("disable_proxy_discovery", "force_proxy"):
        if key in value:
            out[key] = _runtime_profile_bool(value.get(key))
    return out


def _sanitize_mobile_local(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    mode = str(value.get("mode") or "").strip().lower()
    if mode in {"managed", "external"}:
        out["mode"] = mode
    for key in ("binary", "binary_env", "proxy_host", "proxy_user_env", "proxy_pass_env"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
    for key in ("default_hold_minutes", "max_hold_minutes", "ready_timeout_seconds", "heartbeat_seconds", "proxy_port"):
        parsed = _sanitize_runtime_profile_positive_int(value.get(key))
        if parsed is not None:
            out[key] = parsed
    for key in ("force_local", "disable_proxy_discovery", "force_proxy", "only_automate", "force"):
        if key in value:
            out[key] = _runtime_profile_bool(value.get(key))
    for key in ("include_hosts", "exclude_hosts"):
        hosts = _sanitize_runtime_profile_string_list(value.get(key))
        if hosts:
            out[key] = hosts
    return out


def sanitize_runtime_profile_mobile(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = _runtime_profile_bool(value.get("enabled"))
    default_provider = str(value.get("default_provider") or "").strip().lower()
    if default_provider:
        out["default_provider"] = default_provider
    for key in ("state_dir", "artifacts_dir"):
        cleaned = str(value.get(key) or "").strip()
        if cleaned:
            out[key] = cleaned
    retention = _sanitize_runtime_profile_positive_int(value.get("retention_hours"))
    if retention is not None:
        out["retention_hours"] = retention

    defaults = value.get("defaults")
    if isinstance(defaults, dict):
        defaults_out: dict = {}
        platform = str(defaults.get("platform") or "").strip().lower()
        if platform in {"android", "ios"}:
            defaults_out["platform"] = platform
        network_mode = str(defaults.get("network_mode") or "").strip().lower()
        if network_mode in {"public", "private-managed", "private-external"}:
            defaults_out["network_mode"] = network_mode
        for key in ("idle_timeout_seconds", "new_command_timeout_seconds"):
            parsed = _sanitize_runtime_profile_positive_int(defaults.get(key))
            if parsed is not None:
                defaults_out[key] = parsed
        for key in ("interactive_debugging", "video"):
            if key in defaults:
                defaults_out[key] = _runtime_profile_bool(defaults.get(key))
        if defaults_out:
            out["defaults"] = defaults_out

    browserstack = value.get("browserstack")
    if isinstance(browserstack, dict):
        bs_out: dict = {}
        for key in ("api_base_url", "appium_base_url", "username_env", "access_key_env", "username", "access_key", "ca_cert"):
            cleaned = str(browserstack.get(key) or "").strip()
            if cleaned:
                bs_out[key] = cleaned.rstrip("/") if key.endswith("_url") else cleaned
        if "verify_ssl" in browserstack:
            bs_out["verify_ssl"] = _runtime_profile_bool(browserstack.get("verify_ssl"))
        http_proxy = _sanitize_mobile_http_proxy(browserstack.get("http_proxy"))
        if http_proxy:
            bs_out["http_proxy"] = http_proxy
        local = _sanitize_mobile_local(browserstack.get("local"))
        if local:
            bs_out["local"] = local
        if bs_out:
            out["browserstack"] = bs_out
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

def sanitize_runtime_profile_config_dict(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    top_level = {key: value for key, value in data.items() if key in ALLOWED_RUNTIME_PROFILE_SECTIONS}
    sanitized = _filter_by_field_tree(top_level, PORTAL_MANAGED_FIELD_TREE) or {}
    llm = sanitized.get("llm")

    if isinstance(llm, dict):
        llm_copy = llm.copy()
        for key in ("base_url", "api_base", "baseURL", "endpoint"):
            if key in llm_copy:
                cleaned = str(llm_copy.get(key) or "").strip()
                if cleaned:
                    llm_copy[key] = cleaned
                else:
                    llm_copy.pop(key, None)
        # Temperature is not a Copilot-managed knob; never persist it.
        llm_copy.pop("temperature", None)
        api_key = str(llm_copy.get("api_key") or "").strip()
        if api_key:
            llm_copy["api_key"] = api_key
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
    if "aws" in sanitized:
        sanitized["aws"] = sanitize_runtime_profile_aws(sanitized.get("aws"))
    if "jenkins" in sanitized:
        sanitized["jenkins"] = sanitize_runtime_profile_jenkins(sanitized.get("jenkins"))
    if "mobile-auto" in sanitized:
        sanitized["mobile-auto"] = sanitize_runtime_profile_mobile(sanitized.get("mobile-auto"))
    if "proxy" in sanitized:
        sanitized["proxy"] = sanitize_runtime_profile_proxy(sanitized.get("proxy"))
    if "git" in sanitized:
        sanitized["git"] = sanitize_runtime_profile_git(sanitized.get("git"))
    if "debug" in sanitized:
        sanitized["debug"] = sanitize_runtime_profile_debug(sanitized.get("debug"))
    return {key: value for key, value in sanitized.items() if not (isinstance(value, dict) and not value)}


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
        token_values = [str(github.pop(key, "")).strip() for key in ("api_token", "token", "access_token")]
        token_present = any(token_values)
        github["api_token_present"] = token_present
    aws = redacted.get("aws")
    if isinstance(aws, dict):
        aws["password_present"] = bool(str(aws.pop("password", "")).strip())
    jenkins = redacted.get("jenkins")
    if isinstance(jenkins, dict):
        jenkins["password_present"] = bool(str(jenkins.pop("password", "")).strip())
    mobile = redacted.get("mobile-auto")
    if isinstance(mobile, dict):
        browserstack = mobile.get("browserstack")
        if isinstance(browserstack, dict):
            browserstack["access_key_present"] = bool(str(browserstack.pop("access_key", "")).strip())
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
            token_values = [str(inst_copy.pop(key, "")).strip() for key in ("token", "api_token", "access_token")]
            token_present = any(token_values)
            inst_copy["token_present"] = token_present
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
