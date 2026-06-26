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
    "aws",
    "jenkins",
    "mobile-auto",
    "git",
    "debug",
)

RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS = (
    "Use bash for runtime profile CLI tools: jira/confluence for Atlassian, "
    "gh for GitHub issues, PRs, and api calls, aws for AWS operations, "
    "jenkins for Jenkins controller operations, mobile-auto for BrowserStack/Appium device automation, "
    "and git for clone, fetch, push, and status. "
    "For every jira, confluence, jenkins, and mobile-auto command add --json. For complex jira/confluence/jenkins/mobile-auto calls, "
    "inspect commands, schema, or help llm first, for example `jira commands --json`, "
    "`jira schema <command> --json`, `jira help llm --json`, and the matching confluence/jenkins/mobile-auto commands. "
    "For mobile work, start with `mobile-auto doctor --json` and `mobile-auto auth test --json`; use BrowserStackLocal through "
    "`private-external` with a supplied local identifier or `private-managed` only when the runtime image has BrowserStackLocal installed. "
    "Jenkins runtime profile credentials are available as EFP_JENKINS_USERNAME and EFP_JENKINS_PASSWORD; "
    "when the user provides a Jenkins controller URL or pipeline/job, configure or log in to that controller at that time and pass the password through stdin, never by echoing it. "
    "For AWS, prefer `aws --output json` for inspection and avoid changing cloud resources unless the user asks. "
    "Run write operations with --dry-run before executing them. Use --yes only for destructive "
    "operations after the user explicitly confirms. Runtime profile credentials are applied in "
    "the runtime container through CLIs or environment variables; if a CLI returns auth_failed, report a runtime profile "
    "configuration problem instead of guessing or inventing tokens."
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


def runtime_profile_managed_sections(runtime_type: str | None = "native") -> list[str]:
    sections = list(PORTAL_RUNTIME_PROFILE_SECTIONS)
    if not is_opencode_runtime_type(runtime_type):
        sections.append("instruction_texts")
    return sections


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


def _has_enabled_instance_section(config: dict[str, Any], section: str) -> bool:
    section_config = config.get(section)
    if not isinstance(section_config, dict) or section_config.get("enabled") is not True:
        return False
    instances = section_config.get("instances")
    if not isinstance(instances, list):
        return False
    for item in instances:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        endpoint = ""
        for key in ("url", "base_url", "baseUrl", "uri"):
            endpoint = str(item.get(key) or "").strip()
            if endpoint:
                break
        if endpoint:
            return True
    return False


def _has_enabled_github_config(config: dict[str, Any]) -> bool:
    github = config.get("github")
    if not isinstance(github, dict) or github.get("enabled") is not True:
        return False
    return bool(str(github.get("api_token") or github.get("base_url") or "").strip())


def _has_git_config(config: dict[str, Any]) -> bool:
    git = config.get("git")
    if not isinstance(git, dict):
        return False
    user = git.get("user")
    if not isinstance(user, dict):
        return False
    return bool(str(user.get("name") or user.get("email") or "").strip())


def _has_enabled_aws_config(config: dict[str, Any]) -> bool:
    aws = config.get("aws")
    if not isinstance(aws, dict) or aws.get("enabled") is not True:
        return False
    domain = str(aws.get("domain") or "").strip()
    username = str(aws.get("username") or "").strip()
    password = str(aws.get("password") or "").strip()
    return bool(domain and username and password)


def _has_enabled_jenkins_config(config: dict[str, Any]) -> bool:
    jenkins = config.get("jenkins")
    if not isinstance(jenkins, dict) or jenkins.get("enabled") is not True:
        return False
    username = str(jenkins.get("username") or "").strip()
    password = str(jenkins.get("password") or "").strip()
    return bool(username and password)


def _has_enabled_mobile_config(config: dict[str, Any]) -> bool:
    mobile = config.get("mobile-auto")
    if not isinstance(mobile, dict) or mobile.get("enabled") is not True:
        return False
    browserstack = mobile.get("browserstack")
    if not isinstance(browserstack, dict):
        return False
    return bool(
        str(browserstack.get("username") or browserstack.get("username_env") or "").strip()
        or str(browserstack.get("access_key") or browserstack.get("access_key_env") or "").strip()
        or str(browserstack.get("api_base_url") or browserstack.get("appium_base_url") or "").strip()
    )


def _has_enabled_external_cli_config(config: dict[str, Any]) -> bool:
    return (
        _has_enabled_instance_section(config, "jira")
        or _has_enabled_instance_section(config, "confluence")
        or _has_enabled_jenkins_config(config)
        or _has_enabled_mobile_config(config)
        or _has_enabled_github_config(config)
        or _has_enabled_aws_config(config)
        or _has_git_config(config)
    )


def _with_native_cli_tool_instructions(
    config: dict[str, Any],
    runtime_type: str | None,
) -> dict[str, Any]:
    if is_opencode_runtime_type(runtime_type) or not _has_enabled_external_cli_config(config):
        return config
    projected = deepcopy(config)
    instruction_texts = projected.get("instruction_texts")
    if not isinstance(instruction_texts, list):
        instruction_texts = []
    if RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS not in instruction_texts:
        instruction_texts.append(RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS)
    projected["instruction_texts"] = instruction_texts
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
    canonical = strip_opencode_runtime_restrictions(canonical, runtime_type)
    return _with_native_cli_tool_instructions(canonical, runtime_type)
