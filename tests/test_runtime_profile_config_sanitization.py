import json

from app.schemas.runtime_profile import (
    RUNTIME_V2_CONFIG_FIELD_NAMES,
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_config_dict,
    validate_runtime_profile_config_json,
)
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.runtime_profile_runtime_v2_projection import build_trusted_runtime_v2_config


def test_external_sections_sanitized_and_secrets_preserved_for_persisted_config():
    raw = {
        "jira": {"enabled": True, "instances": [{"name": "  J1 ", "url": " https://a.atlassian.net/ ", "username": " u ", "password": " p ", "token": " t ", "project": " PRJ ", "x": "bad"}, {"name": "", "url": "", "password": "drop"}]},
        "confluence": {"enabled": 1, "instances": [{"name": " C1 ", "url": " https://a.atlassian.net/wiki/ ", "username": " u ", "password": " p2 ", "token": " t2 ", "space": " DOCS ", "bad": "x"}]},
        "github": {"enabled": True, "api_token": " ghp_1 ", "base_url": " https://api.github.com/ ", "x": "bad"},
        "proxy": {"enabled": True, "url": " http://proxy ", "username": " me ", "password": " secret "},
        "git": {"user": {"name": " Bot ", "email": " bot@example.com ", "x": "bad"}},
        "debug": {"enabled": True, "log_level": "info", "x": "bad"},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["instances"] == [{"name": "J1", "url": "https://a.atlassian.net", "username": "u", "password": "p", "token": "t", "project": "PRJ"}]
    assert s["confluence"]["instances"][0]["url"] == "https://a.atlassian.net/wiki"
    assert s["github"] == {"enabled": True, "api_token": "ghp_1", "base_url": "https://api.github.com"}
    assert s["proxy"]["password"] == "secret"
    assert s["git"] == {"user": {"name": "Bot", "email": "bot@example.com"}}
    assert s["debug"]["log_level"] == "INFO"


def test_proxy_no_proxy_is_sanitized_and_persisted_in_config_json():
    raw = {
        "proxy": {
            "enabled": True,
            "url": " http://proxy.local:8080 ",
            "username": " u ",
            "password": " p ",
            "no_proxy": " 127.0.0.1,localhost,.svc,.cluster.local ",
            "token": "drop",
            "unknown": "drop",
        }
    }

    sanitized = sanitize_runtime_profile_config_dict(raw)
    assert sanitized["proxy"] == {
        "enabled": True,
        "url": "http://proxy.local:8080",
        "username": "u",
        "password": "p",
        "no_proxy": "127.0.0.1,localhost,.svc,.cluster.local",
    }

    persisted = json.loads(dump_runtime_profile_config_json(raw))
    assert persisted["proxy"]["no_proxy"] == "127.0.0.1,localhost,.svc,.cluster.local"
    assert "token" not in persisted["proxy"]
    assert "unknown" not in persisted["proxy"]


def test_proxy_no_proxy_alias_is_normalized_and_canonical_key_takes_priority():
    alias_only = sanitize_runtime_profile_config_dict(
        {"proxy": {"noProxy": " localhost, .internal "}}
    )
    assert alias_only["proxy"]["no_proxy"] == "localhost, .internal"
    assert "noProxy" not in alias_only["proxy"]

    with_both = sanitize_runtime_profile_config_dict(
        {"proxy": {"no_proxy": " canonical.local ", "noProxy": "alias.local"}}
    )
    assert with_both["proxy"]["no_proxy"] == "canonical.local"
    assert "noProxy" not in with_both["proxy"]


def test_proxy_no_proxy_rejects_non_string_and_blank_values():
    non_string = sanitize_runtime_profile_config_dict(
        {"proxy": {"enabled": True, "no_proxy": ["localhost"], "noProxy": "alias.local"}}
    )
    assert "no_proxy" not in non_string["proxy"]

    blank = sanitize_runtime_profile_config_dict(
        {"proxy": {"enabled": True, "noProxy": "   "}}
    )
    assert "no_proxy" not in blank["proxy"]


def test_public_redaction_removes_all_secrets_and_sets_presence_flags():
    cfg = {
        "llm": {
            "api_key": "sk-1",
            "oauth": {"type": "oauth", "access": "a", "refresh": "r", "expires": 1, "accountId": "id"},
            "oauth_by_runtime": {"opencode": {"type": "oauth", "access": "oa", "refresh": "or", "expires": 2}},
        },
        "github": {"api_token": "ghp_1"},
        "proxy": {"password": "pw"},
        "jira": {"instances": [{"name": "x", "password": "p", "token": "t"}]},
        "confluence": {"instances": [{"name": "x", "password": "p2", "token": "t2"}]},
    }
    red = redact_runtime_profile_config_for_public_response(cfg)
    assert "api_key" not in red["llm"] and red["llm"]["api_key_present"] is True
    assert "oauth" not in red["llm"]
    assert "oauth_by_runtime" not in red["llm"]
    assert "api_token" not in red["github"] and red["github"]["api_token_present"] is True
    assert "password" not in red["proxy"] and red["proxy"]["password_present"] is True
    assert "password" not in red["jira"]["instances"][0] and red["jira"]["instances"][0]["password_present"] is True
    assert "token" not in red["confluence"]["instances"][0] and red["confluence"]["instances"][0]["token_present"] is True


def test_public_redaction_keeps_proxy_no_proxy_and_hides_password():
    cfg = {
        "proxy": {
            "password": "pw",
            "no_proxy": "127.0.0.1,localhost",
        }
    }

    red = redact_runtime_profile_config_for_public_response(cfg)

    assert red["proxy"]["no_proxy"] == "127.0.0.1,localhost"
    assert "password" not in red["proxy"]
    assert red["proxy"]["password_present"] is True


def test_alias_fields_are_normalized_to_canonical_shape():
    s = sanitize_runtime_profile_config_dict(
        {
            "jira": {"instances": [{"name": "J", "url": "https://a/", "email": "u@x", "api_token": "jt", "project_key": "ENG", "enabled": "1"}]},
            "confluence": {"instances": [{"name": "C", "url": "https://a/wiki/", "email": "c@x", "api_token": "ct", "space_key": "DOCS", "enabled": True}]},
            "github": {"token": "gh", "api_base_url": "https://api.github.com/"},
        }
    )
    assert s["jira"]["instances"][0] == {"name": "J", "url": "https://a", "username": "u@x", "token": "jt", "project": "ENG", "enabled": True}
    assert s["confluence"]["instances"][0] == {"name": "C", "url": "https://a/wiki", "username": "c@x", "token": "ct", "space": "DOCS", "enabled": True}
    assert s["github"] == {"api_token": "gh", "base_url": "https://api.github.com"}

def test_external_enabled_string_false_values_remain_disabled():
    raw = {
        "jira": {
            "enabled": "false",
            "instances": [
                {"name": "J1", "url": "https://jira.example", "enabled": "0", "token": "jt"},
                {"name": "J2", "url": "https://jira2.example", "enabled": "off", "token": "jt2"},
            ],
        },
        "confluence": {
            "enabled": "no",
            "instances": [
                {"name": "C1", "url": "https://conf.example", "enabled": "disabled", "token": "ct"},
            ],
        },
        "github": {"enabled": "false", "api_token": "gh"},
        "proxy": {"enabled": "0", "url": "http://proxy", "password": "pw"},
        "debug": {"enabled": "off", "log_level": "debug"},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["enabled"] is False
    assert s["jira"]["instances"][0]["enabled"] is False
    assert s["jira"]["instances"][1]["enabled"] is False
    assert s["confluence"]["enabled"] is False
    assert s["confluence"]["instances"][0]["enabled"] is False
    assert s["github"]["enabled"] is False
    assert s["proxy"]["enabled"] is False
    assert s["debug"]["enabled"] is False


def test_external_enabled_true_strings_are_respected():
    raw = {
        "jira": {"enabled": "true", "instances": [{"name": "J", "url": "https://jira", "enabled": "1"}]},
        "github": {"enabled": "on", "api_token": "gh"},
        "proxy": {"enabled": "yes", "url": "http://proxy"},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["enabled"] is True
    assert s["jira"]["instances"][0]["enabled"] is True
    assert s["github"]["enabled"] is True
    assert s["proxy"]["enabled"] is True


def test_external_enabled_json_booleans_are_preserved():
    raw = {
        "jira": {"enabled": False, "instances": [{"name": "J", "url": "https://jira", "enabled": False}]},
        "confluence": {"enabled": True, "instances": [{"name": "C", "url": "https://conf", "enabled": True}]},
        "github": {"enabled": False, "api_token": "gh"},
        "proxy": {"enabled": True, "url": "http://proxy"},
        "debug": {"enabled": False},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["enabled"] is False
    assert s["jira"]["instances"][0]["enabled"] is False
    assert s["confluence"]["enabled"] is True
    assert s["confluence"]["instances"][0]["enabled"] is True
    assert s["github"]["enabled"] is False
    assert s["proxy"]["enabled"] is True
    assert s["debug"]["enabled"] is False


def test_public_redaction_never_exposes_raw_secret_literals():
    cfg = {
        "llm": {"api_key": "sk-secret"},
        "github": {"api_token": "gh-secret"},
        "proxy": {"password": "proxy-secret"},
        "jira": {"instances": [{"password": "jira-pass", "token": "jira-token"}]},
        "confluence": {"instances": [{"password": "conf-pass", "token": "conf-token"}]},
    }
    red = redact_runtime_profile_config_for_public_response(cfg)
    dumped = str(red)
    for secret in ["sk-secret", "gh-secret", "proxy-secret", "jira-pass", "jira-token", "conf-pass", "conf-token"]:
        assert secret not in dumped

    assert red["llm"]["api_key_present"] is True
    assert red["github"]["api_token_present"] is True
    assert red["proxy"]["password_present"] is True
    assert red["jira"]["instances"][0]["password_present"] is True
    assert red["jira"]["instances"][0]["token_present"] is True
    assert red["confluence"]["instances"][0]["password_present"] is True
    assert red["confluence"]["instances"][0]["token_present"] is True


def test_copilot_legacy_oauth_by_runtime_is_dropped_not_migrated():
    raw = {
        "llm": {
            "provider": "github_copilot",
            "oauth_by_runtime": {
                "opencode": {"type": "oauth", "access": "O", "refresh": "O", "expires": 0},
                "native": {"type": "oauth", "access": "N", "refresh": "N", "expires": 0},
            },
        }
    }
    sanitized = sanitize_runtime_profile_config_dict(raw)
    assert sanitized["llm"] == {"provider": "github_copilot"}
    assert "oauth" not in sanitized["llm"]
    assert "oauth_by_runtime" not in sanitized["llm"]


def test_non_copilot_provider_does_not_migrate_stale_oauth_token():
    raw = {
        "llm": {
            "provider": "openai",
            "model": "gpt-4",
            "oauth_by_runtime": {
                "opencode": {"type": "oauth", "access": "COPILOT_TOKEN", "refresh": "COPILOT_TOKEN", "expires": 0}
            },
        }
    }
    sanitized = sanitize_runtime_profile_config_dict(raw)
    assert sanitized["llm"]["provider"] == "openai"
    assert "api_key" not in sanitized["llm"]
    assert "oauth" not in sanitized["llm"]
    assert "oauth_by_runtime" not in sanitized["llm"]


def test_runtime_profile_sanitizer_preserves_jira_api_version_only_for_jira():
    cfg = sanitize_runtime_profile_config_dict(
        {
            "jira": {
                "instances": [
                    {"name": "J", "url": "https://j", "username": "u", "password": "pw", "api_version": "2"}
                ]
            },
            "confluence": {
                "instances": [
                    {"name": "C", "url": "https://c", "username": "u", "password": "pw", "api_version": "2"}
                ]
            },
        }
    )
    assert cfg["jira"]["instances"][0]["api_version"] == "2"
    assert "api_version" not in cfg["confluence"]["instances"][0]


def test_runtime_profile_sanitizer_drops_invalid_jira_api_version():
    cfg = sanitize_runtime_profile_config_dict(
        {"jira": {"instances": [{"name": "J", "url": "https://j", "api_version": "4"}]}}
    )
    assert "api_version" not in cfg["jira"]["instances"][0]


def test_runtime_v2_config_fields_survive_parse_dump_and_canonicalization():
    raw = {
        "llm": {"provider": "github_copilot", "model": "gpt-5-mini"},
        "enabled_tools": [" bash ", "read", "BASH"],
        "disabled_tools": ["webfetch"],
        "tool_permissions": {"bash": "ask", "write": {"mode": "deny"}},
        "max_iterations": "6",
        "doom_loop_threshold": None,
        "active_skills": ["review"],
        "skill_directories": ["/app/skills"],
        "command_directories": ["/workspace/.efp/commands"],
        "enable_command_expansion": True,
        "max_context_parts": "12",
        "max_context_chars": 200000,
        "max_context_tokens": 64000,
        "context_reserve_chars": 4000,
        "context_reserve_tokens": 1200,
        "compaction_auto": True,
        "compaction_prune": False,
        "compaction_tail_turns": 8,
        "compaction_preserve_recent_chars": 12000,
        "compaction_preserve_recent_tokens": 4800,
        "compaction_reserved_chars": 6000,
        "compaction_tool_output_max_chars": 24000,
        "compaction_prune_min_chars": 20000,
        "compaction_prune_protect_chars": 40000,
        "enable_compaction_summarizer": True,
        "enable_context_overflow_retry": True,
        "enable_session_revert_snapshots": True,
        "include_default_system_prompt": True,
        "include_environment_context": False,
        "include_runtime_reminders": True,
        "system_prompt_texts": ["system text"],
        "system_prompt_paths": ["/workspace/SYSTEM.md"],
        "max_system_prompt_chars": 30000,
        "include_default_instructions": True,
        "attach_read_instructions": False,
        "instruction_texts": ["instruction text"],
        "instruction_paths": ["/workspace/AGENTS.md"],
        "max_instruction_chars": 28000,
        "include_skill_sidecar_content": True,
        "max_skill_sidecar_chars": 7000,
        "max_command_chars": 25000,
        "resolve_prompt_references": True,
        "max_prompt_reference_chars": 18000,
        "max_prompt_directory_entries": 300,
        "inject_background_task_results": False,
        "emit_llm_stream_events": True,
        "track_usage": False,
        "tool_output_max_lines": 500,
        "tool_output_max_bytes": 131072,
        "tool_output_truncation_direction": "tail",
        "archive_truncated_tool_outputs": True,
        "tool_output_dir": "/workspace/.efp/tool-output",
        "runtime_mode": "plan",
        "enable_plan_tool": True,
        "plan_mode_read_only": False,
        "enable_question_tool": True,
        "enable_lsp_tool": False,
        "model_aware_tool_selection": True,
        "structured_output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    }
    runtime_v2_keys = set(raw) - {"llm"}
    unsupported_key = "compaction_preserve_recent_" "turns"

    assert runtime_v2_keys <= RUNTIME_V2_CONFIG_FIELD_NAMES
    assert unsupported_key not in RUNTIME_V2_CONFIG_FIELD_NAMES

    parsed = parse_runtime_profile_config_json(json.dumps(raw))
    dumped = json.loads(dump_runtime_profile_config_json(parsed))
    validated = json.loads(validate_runtime_profile_config_json(json.dumps(raw)))
    canonical = canonicalize_portal_runtime_profile_config(parsed)

    for key in raw:
        assert key in parsed
        assert key in dumped
        assert key in validated
        assert key in canonical
    assert parsed["enabled_tools"] == ["bash", "read"]
    assert parsed["max_iterations"] == 6
    assert parsed["doom_loop_threshold"] is None
    assert parsed["max_context_parts"] == 12
    assert parsed["compaction_preserve_recent_tokens"] == 4800
    assert parsed["include_environment_context"] is False
    assert parsed["max_prompt_directory_entries"] == 300
    assert parsed["inject_background_task_results"] is False
    assert parsed["tool_permissions"] == {"bash": "ask", "write": {"mode": "deny"}}
    assert "tools" not in canonical["llm"]

    unsupported = {unsupported_key: 4}
    parsed_unsupported = parse_runtime_profile_config_json(json.dumps({**raw, **unsupported}))
    dumped_unsupported = json.loads(dump_runtime_profile_config_json({**raw, **unsupported}))
    assert unsupported_key not in parsed_unsupported
    assert unsupported_key not in dumped_unsupported


def test_runtime_v2_config_sanitizer_drops_runtime_rejected_values_and_preserves_nullable_meaning():
    sanitized = sanitize_runtime_profile_config_dict(
        {
            "enabled_tools": None,
            "enable_plan_tool": None,
            "tool_output_max_lines": None,
            "structured_output_schema": None,
            "runtime_mode": "agent",
            "tool_output_truncation_direction": "middle",
            "max_iterations": 0,
            "doom_loop_threshold": 1,
            "max_context_parts": 0,
            "max_context_chars": -1,
            "context_reserve_chars": None,
        }
    )

    assert sanitized["enabled_tools"] is None
    assert sanitized["enable_plan_tool"] is None
    assert sanitized["tool_output_max_lines"] is None
    assert sanitized["structured_output_schema"] is None
    assert "runtime_mode" not in sanitized
    assert "tool_output_truncation_direction" not in sanitized
    assert "max_iterations" not in sanitized
    assert "doom_loop_threshold" not in sanitized
    assert "max_context_parts" not in sanitized
    assert "max_context_chars" not in sanitized
    assert "context_reserve_chars" not in sanitized


def test_runtime_v2_projection_sanitizes_raw_config_before_trusting_it():
    projected = build_trusted_runtime_v2_config(
        {
            "enabled_tools": None,
            "runtime_mode": "agent",
            "tool_output_truncation_direction": "middle",
            "compaction_preserve_recent_turns": 4,
            "workspace_root": "/portal/workspace",
            "mcp_servers": {"filesystem": {}},
            "llm": {"provider": "github_copilot", "model": "gpt-5-mini"},
        },
        runtime_type="opencode",
        include_portal_sections=False,
        include_llm_credentials=False,
    )

    assert projected["enabled_tools"] is None
    assert projected["llm"]["provider"] == "github-copilot"
    assert projected["llm"]["model"] == "github-copilot/gpt-5-mini"
    assert "runtime_mode" not in projected
    assert "tool_output_truncation_direction" not in projected
    assert "compaction_preserve_recent_turns" not in projected
    assert "workspace_root" not in projected
    assert "mcp_servers" not in projected
