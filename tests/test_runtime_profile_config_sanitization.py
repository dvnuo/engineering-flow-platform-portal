import json

from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_config_dict,
    validate_runtime_profile_config_json,
)
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.runtime_profile_context_projection import (
    build_runtime_profile_context_config,
)


def _assert_cli_instruction_texts(instruction_texts):
    assert isinstance(instruction_texts, list)
    assert len(instruction_texts) == 1
    text = instruction_texts[0]
    for expected in ["bash", "jira", "confluence", "gh", "aws", "jenkins", "mobile-auto", "git", "--json", "--dry-run", "--yes", "auth_failed"]:
        assert expected in text


def test_external_sections_sanitized_and_secrets_preserved_for_persisted_config():
    raw = {
        "jira": {"enabled": True, "instances": [{"name": "  J1 ", "url": " https://a.atlassian.net/ ", "username": " u ", "password": " p ", "token": " t ", "project": " PRJ ", "x": "bad"}, {"name": "", "url": "", "password": "drop"}]},
        "confluence": {"enabled": 1, "instances": [{"name": " C1 ", "url": " https://a.atlassian.net/wiki/ ", "username": " u ", "password": " p2 ", "token": " t2 ", "space": " DOCS ", "api_version": " 2 ", "bad": "x"}]},
        "github": {"enabled": True, "api_token": " ghp_1 ", "base_url": " https://api.github.com/ ", "x": "bad"},
        "aws": {
            "enabled": True,
            "domain": " HBEU ",
            "username": " adfs-user ",
            "password": " adfs-password ",
            "account": " 123456 ",
            "role": " ADFS-ReadOnly ",
            "x": "bad",
        },
        "jenkins": {
            "enabled": True,
            "username": " build ",
            "password": " jenkins-password ",
            "url": " https://jenkins.example.com/ ",
            "instances": [{"name": "drop"}],
        },
        "proxy": {"enabled": True, "url": " http://proxy ", "username": " me ", "password": " secret "},
        "git": {"user": {"name": " Bot ", "email": " bot@example.com ", "x": "bad"}},
        "debug": {"enabled": True, "log_level": "info", "x": "bad"},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["instances"] == [{"name": "J1", "url": "https://a.atlassian.net", "username": "u", "password": "p", "token": "t", "project": "PRJ"}]
    assert s["confluence"]["instances"][0]["url"] == "https://a.atlassian.net/wiki"
    assert "api_version" not in s["confluence"]["instances"][0]
    assert s["github"] == {"enabled": True, "api_token": "ghp_1", "base_url": "https://api.github.com"}
    assert s["aws"] == {
        "enabled": True,
        "domain": "HBEU",
        "username": "adfs-user",
        "password": "adfs-password",
    }
    assert s["jenkins"] == {
        "enabled": True,
        "url": "https://jenkins.example.com",
        "username": "build",
        "password": "jenkins-password",
    }
    assert s["proxy"]["password"] == "secret"
    assert s["git"] == {"user": {"name": "Bot", "email": "bot@example.com"}}
    assert s["debug"]["log_level"] == "INFO"


def test_aws_config_keeps_only_portal_fields():
    raw = {
        "aws": {
            "enabled": True,
            "domain": " HBEU ",
            "username": " adfs-user ",
            "password": " adfs-password ",
            "account": " 123456 ",
            "role": " ADFS-ReadOnly ",
            "unknown": "drop",
        }
    }

    s = sanitize_runtime_profile_config_dict(raw)

    assert s["aws"] == {
        "enabled": True,
        "domain": "HBEU",
        "username": "adfs-user",
        "password": "adfs-password",
    }


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
        "aws": {"username": "aws-user", "password": "aws-password"},
        "proxy": {"password": "pw"},
        "jira": {"instances": [{"name": "x", "password": "p", "token": "t"}]},
        "confluence": {"instances": [{"name": "x", "password": "p2", "token": "t2"}]},
        "jenkins": {"username": "build", "password": "jenkins-password"},
        "mobile-auto": {"browserstack": {"username": "bs-user", "access_key": "bs-access-key"}},
    }
    red = redact_runtime_profile_config_for_public_response(cfg)
    assert "api_key" not in red["llm"] and red["llm"]["api_key_present"] is True
    assert "oauth" not in red["llm"]
    assert "oauth_by_runtime" not in red["llm"]
    assert "api_token" not in red["github"] and red["github"]["api_token_present"] is True
    assert "password" not in red["aws"] and red["aws"]["password_present"] is True
    assert "password" not in red["proxy"] and red["proxy"]["password_present"] is True
    assert "password" not in red["jira"]["instances"][0] and red["jira"]["instances"][0]["password_present"] is True
    assert "token" not in red["confluence"]["instances"][0] and red["confluence"]["instances"][0]["token_present"] is True
    assert "password" not in red["jenkins"] and red["jenkins"]["password_present"] is True
    assert "access_key" not in red["mobile-auto"]["browserstack"]
    assert red["mobile-auto"]["browserstack"]["access_key_present"] is True


def test_public_redaction_removes_token_aliases():
    cfg = {
        "github": {"api_token": "gh-api", "token": "gh-token", "access_token": "gh-access"},
        "aws": {"password": "aws-password"},
        "jira": {"instances": [{"api_token": "jira-api", "token": "jira-token", "access_token": "jira-access"}]},
        "confluence": {"instances": [{"api_token": "conf-api", "token": "conf-token", "access_token": "conf-access"}]},
    }

    red = redact_runtime_profile_config_for_public_response(cfg)
    dumped = json.dumps(red)

    for secret in ("gh-api", "gh-token", "gh-access", "aws-password", "jira-api", "jira-token", "jira-access", "conf-api", "conf-token", "conf-access"):
        assert secret not in dumped
    assert red["github"]["api_token_present"] is True
    assert red["aws"]["password_present"] is True
    assert red["jira"]["instances"][0]["token_present"] is True
    assert red["confluence"]["instances"][0]["token_present"] is True


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
            "jira": {"instances": [{"name": "J", "base_url": "https://a/", "email": "u@x", "api_token": "jt", "project_key": "ENG", "enabled": "1"}]},
            "confluence": {"instances": [{"name": "C", "base_url": "https://a/wiki/", "email": "c@x", "api_token": "ct", "space_key": "DOCS", "enabled": True}]},
            "github": {"access_token": "gh", "api_base_url": "https://api.github.com/"},
            "aws": {"domain": "HBEU", "username": "alice", "password": "pw"},
        }
    )
    assert s["jira"]["instances"][0] == {"name": "J", "url": "https://a", "username": "u@x", "token": "jt", "project": "ENG", "enabled": True}
    assert s["confluence"]["instances"][0] == {"name": "C", "url": "https://a/wiki", "username": "c@x", "token": "ct", "space": "DOCS", "enabled": True}
    assert s["github"] == {"api_token": "gh", "base_url": "https://api.github.com"}
    assert s["aws"] == {"domain": "HBEU", "username": "alice", "password": "pw"}


def test_mobile_profile_config_is_sanitized_to_runtime_shape():
    s = sanitize_runtime_profile_config_dict(
        {
            "mobile-auto": {
                "enabled": "yes",
                "default_provider": " BrowserStack ",
                "state_dir": " /workspace/.efp/mobile-auto/runs ",
                "artifacts_dir": " /workspace/.efp/mobile-auto/artifacts ",
                "retention_hours": "48",
                "defaults": {
                    "platform": "Android",
                    "network_mode": "private-external",
                    "idle_timeout_seconds": "60",
                    "new_command_timeout_seconds": "120",
                    "interactive_debugging": "true",
                    "video": "false",
                },
                "browserstack": {
                    "api_base_url": " https://api.browserstack.com/ ",
                    "appium_base_url": " https://hub-cloud.browserstack.com/wd/hub/ ",
                    "username_env": " BROWSERSTACK_USERNAME ",
                    "access_key_env": " BROWSERSTACK_ACCESS_KEY ",
                    "username": " user ",
                    "access_key": " key ",
                    "verify_ssl": "true",
                    "http_proxy": {
                        "proxy_host": " proxy.example ",
                        "proxy_port": "8080",
                        "proxy_user_env": " BS_PROXY_USER ",
                        "proxy_pass_env": " BS_PROXY_PASS ",
                        "no_proxy_hosts": "localhost, 127.0.0.1",
                        "force_proxy": "1",
                    },
                    "local": {
                        "mode": "External",
                        "binary": " /usr/local/bin/BrowserStackLocal ",
                        "ready_timeout_seconds": "90",
                        "force_local": "0",
                        "proxy_port": "8081",
                        "include_hosts": [" internal.example ", ""],
                    },
                },
            }
        }
    )

    assert s["mobile-auto"]["enabled"] is True
    assert s["mobile-auto"]["default_provider"] == "browserstack"
    assert s["mobile-auto"]["retention_hours"] == 48
    assert s["mobile-auto"]["defaults"]["platform"] == "android"
    assert s["mobile-auto"]["defaults"]["network_mode"] == "private-external"
    assert s["mobile-auto"]["browserstack"]["api_base_url"] == "https://api.browserstack.com"
    assert s["mobile-auto"]["browserstack"]["appium_base_url"] == "https://hub-cloud.browserstack.com/wd/hub"
    assert s["mobile-auto"]["browserstack"]["access_key"] == "key"
    assert s["mobile-auto"]["browserstack"]["http_proxy"]["proxy_port"] == 8080
    assert s["mobile-auto"]["browserstack"]["http_proxy"]["no_proxy_hosts"] == ["localhost", "127.0.0.1"]
    assert s["mobile-auto"]["browserstack"]["local"]["mode"] == "external"
    assert s["mobile-auto"]["browserstack"]["local"]["include_hosts"] == ["internal.example"]


def test_external_instances_require_endpoint_and_normalize_url_aliases():
    s = sanitize_runtime_profile_config_dict(
        {
            "jira": {
                "instances": [
                    {"name": "name-only", "token": "drop"},
                    {"name": "empty-uri", "uri": "   ", "token": "drop"},
                    ["bad"],
                    {"name": "Uri", "uri": " https://jira.example.com/ ", "access_token": "jt"},
                    {"name": "BaseUrl", "baseUrl": " https://jira-base-url.example.com/ "},
                    {"name": "BaseURL", "base_url": " https://jira-base-url-2.example.com/ "},
                ]
            },
            "confluence": {
                "instances": [
                    {"name": "name-only", "password": "drop"},
                    {"name": "empty-uri", "uri": ""},
                    {"name": "Uri", "uri": " https://conf.example.com/wiki/ "},
                    {"name": "BaseUrl", "baseUrl": " https://conf-base-url.example.com/wiki/ "},
                    {"name": "BaseURL", "base_url": " https://conf-base-url-2.example.com/wiki/ "},
                ]
            },
        }
    )

    assert s["jira"]["instances"] == [
        {"name": "Uri", "url": "https://jira.example.com", "token": "jt"},
        {"name": "BaseUrl", "url": "https://jira-base-url.example.com"},
        {"name": "BaseURL", "url": "https://jira-base-url-2.example.com"},
    ]
    assert s["confluence"]["instances"] == [
        {"name": "Uri", "url": "https://conf.example.com/wiki"},
        {"name": "BaseUrl", "url": "https://conf-base-url.example.com/wiki"},
        {"name": "BaseURL", "url": "https://conf-base-url-2.example.com/wiki"},
    ]


def test_external_integration_contract_keeps_cli_mapping_inputs_and_drops_runtime_internal_fields():
    raw = {
        "jira": {
            "enabled": True,
            "instances": [
                {
                    "name": "main",
                    "base_url": "https://jira.example.com/",
                    "email": "jira@example.com",
                    "password": "jira-password",
                    "api_token": "jira-token",
                    "project_key": "ENG",
                    "api_version": "3",
                    "enabled": True,
                    "rest_path": "/rest/api/3",
                }
            ],
        },
        "confluence": {
            "enabled": True,
            "instances": [
                {
                    "name": "docs",
                    "base_url": "https://confluence.example.com/wiki/",
                    "email": "conf@example.com",
                    "api_token": "conf-token",
                    "space_key": "DOCS",
                    "api_version": "2",
                    "enabled": "on",
                    "rest_path": "/rest/api",
                }
            ],
        },
        "github": {
            "enabled": True,
            "token": "github-token",
            "base_url": "https://github.example.com/api/v3/",
            "hosts": {"github.example.com": {"oauth_token": "browser-forged"}},
        },
        "aws": {
            "enabled": True,
            "domain": "HBEU",
            "username": "adfs-user",
            "password": "adfs-password",
            "account": "123456",
            "role": "ADFS-ReadOnly",
            "policy": {"drop": True},
        },
        "git": {
            "user": {
                "name": "EFP Bot",
                "email": "efp-bot@example.com",
                "signingkey": "drop",
            },
        },
        "tool_loop": {"max_iterations": 12},
        "context_budget": {"max_prompt_tokens": 32000},
        "runtime_mode": "plan",
    }

    s = sanitize_runtime_profile_config_dict(raw)

    assert s == {
        "jira": {
            "enabled": True,
            "instances": [
                {
                    "name": "main",
                    "url": "https://jira.example.com",
                    "username": "jira@example.com",
                    "password": "jira-password",
                    "token": "jira-token",
                    "enabled": True,
                    "project": "ENG",
                    "api_version": "3",
                }
            ],
        },
        "confluence": {
            "enabled": True,
            "instances": [
                {
                    "name": "docs",
                    "url": "https://confluence.example.com/wiki",
                    "username": "conf@example.com",
                    "token": "conf-token",
                    "enabled": True,
                    "space": "DOCS",
                }
            ],
        },
        "github": {
            "enabled": True,
            "api_token": "github-token",
            "base_url": "https://github.example.com/api/v3",
        },
        "aws": {
            "enabled": True,
            "domain": "HBEU",
            "username": "adfs-user",
            "password": "adfs-password",
        },
        "git": {"user": {"name": "EFP Bot", "email": "efp-bot@example.com"}},
    }


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
        "aws": {"enabled": "false", "domain": "HBEU", "username": "user", "password": "pw"},
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
    assert s["aws"]["enabled"] is False
    assert s["proxy"]["enabled"] is False
    assert s["debug"]["enabled"] is False


def test_external_enabled_true_strings_are_respected():
    raw = {
        "jira": {"enabled": "true", "instances": [{"name": "J", "url": "https://jira", "enabled": "1"}]},
        "github": {"enabled": "on", "api_token": "gh"},
        "aws": {"enabled": "yes", "domain": "HBEU", "username": "user", "password": "pw"},
        "proxy": {"enabled": "yes", "url": "http://proxy"},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["enabled"] is True
    assert s["jira"]["instances"][0]["enabled"] is True
    assert s["github"]["enabled"] is True
    assert s["aws"]["enabled"] is True
    assert s["proxy"]["enabled"] is True


def test_external_enabled_json_booleans_are_preserved():
    raw = {
        "jira": {"enabled": False, "instances": [{"name": "J", "url": "https://jira", "enabled": False}]},
        "confluence": {"enabled": True, "instances": [{"name": "C", "url": "https://conf", "enabled": True}]},
        "github": {"enabled": False, "api_token": "gh"},
        "aws": {"enabled": True, "domain": "HBEU", "username": "user", "password": "pw"},
        "proxy": {"enabled": True, "url": "http://proxy"},
        "debug": {"enabled": False},
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["jira"]["enabled"] is False
    assert s["jira"]["instances"][0]["enabled"] is False
    assert s["confluence"]["enabled"] is True
    assert s["confluence"]["instances"][0]["enabled"] is True
    assert s["github"]["enabled"] is False
    assert s["aws"]["enabled"] is True
    assert s["proxy"]["enabled"] is True
    assert s["debug"]["enabled"] is False


def test_public_redaction_never_exposes_raw_secret_literals():
    cfg = {
        "llm": {"api_key": "sk-secret"},
        "github": {"api_token": "gh-secret"},
        "aws": {"username": "aws-user", "password": "aws-password"},
        "proxy": {"password": "proxy-secret"},
        "jira": {"instances": [{"password": "jira-pass", "token": "jira-token"}]},
        "confluence": {"instances": [{"password": "conf-pass", "token": "conf-token"}]},
        "jenkins": {"password": "jenkins-pass"},
    }
    red = redact_runtime_profile_config_for_public_response(cfg)
    dumped = str(red)
    for secret in ["sk-secret", "gh-secret", "aws-password", "proxy-secret", "jira-pass", "jira-token", "conf-pass", "conf-token", "jenkins-pass"]:
        assert secret not in dumped

    assert red["llm"]["api_key_present"] is True
    assert red["github"]["api_token_present"] is True
    assert red["aws"]["password_present"] is True
    assert red["proxy"]["password_present"] is True
    assert red["jira"]["instances"][0]["password_present"] is True
    assert red["jira"]["instances"][0]["token_present"] is True
    assert red["confluence"]["instances"][0]["password_present"] is True
    assert red["confluence"]["instances"][0]["token_present"] is True
    assert red["jenkins"]["password_present"] is True


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


def test_runtime_profile_sanitizer_drops_llm_timeout_overrides():
    sanitized = sanitize_runtime_profile_config_dict(
        {
            "llm": {
                "provider": "github_copilot",
                "timeout": 10000,
                "timeout_ms": 10000,
                "chunk_timeout_ms": 10000,
                "chunkTimeout": 10000,
            }
        }
    )

    assert sanitized["llm"] == {"provider": "github_copilot"}


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


def test_runtime_profile_sanitizer_preserves_only_jira_api_version():
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


def test_old_low_level_runtime_fields_are_dropped_from_profile_config():
    old_fields = {
        "enabled" + "_tools": ["bash"],
        "disabled" + "_tools": ["write"],
        "tool" + "_permissions": {"bash": "ask"},
        "max_iterations": 6,
        "compaction_auto": True,
        "system_prompt_texts": ["system text"],
        "instruction_texts": ["user supplied instruction"],
        "active_skills": ["review"],
        "runtime_mode": "plan",
        "structured_output_schema": {"type": "object"},
    }
    raw = {
        "llm": {
            "provider": "github_copilot",
            "model": "gpt-5-mini",
            "system_prompt_texts": ["nested system"],
            "instruction_texts": ["nested instruction"],
        },
        **old_fields,
    }

    parsed = parse_runtime_profile_config_json(json.dumps(raw))
    dumped = json.loads(dump_runtime_profile_config_json(parsed))
    validated = json.loads(validate_runtime_profile_config_json(json.dumps(raw)))
    canonical = canonicalize_portal_runtime_profile_config(parsed)

    for key in old_fields:
        assert key not in parsed
        assert key not in dumped
        assert key not in validated
        assert key not in canonical
    assert parsed["llm"] == {"provider": "github_copilot", "model": "gpt-5-mini"}
    assert "system_prompt_texts" not in json.dumps(parsed)
    assert "instruction_texts" not in json.dumps(parsed)


def test_runtime_profile_context_projection_drops_tool_restriction_fields():
    projected = build_runtime_profile_context_config(
        {
            "enabled" + "_tools": None,
            "disabled" + "_tools": ["write"],
            "tool" + "_permissions": {"bash": "ask"},
            "llm": {"provider": "github_copilot", "model": "gpt-5-mini", "tools": ["bash"]},
            "github": {"enabled": True, "api_token": "ghp"},
            "aws": {"enabled": True, "domain": "HBEU", "username": "user", "password": "pw"},
        },
        runtime_type="native",
        include_portal_sections=False,
        include_llm_credentials=False,
    )

    assert "enabled" + "_tools" not in projected
    assert "disabled" + "_tools" not in projected
    assert "tool" + "_permissions" not in projected
    assert "tools" not in projected["llm"]
    assert projected["llm"]["provider"] == "github_copilot"
    assert projected["llm"]["model"] == "gpt-5-mini"
    assert projected["github"]["api_token"] == "ghp"
    assert projected["aws"]["domain"] == "HBEU"
    assert projected["aws"]["username"] == "user"
    _assert_cli_instruction_texts(projected["instruction_texts"])


def test_native_runtime_profile_context_config_adds_external_cli_instruction_texts():
    projected = build_runtime_profile_context_config(
        {
            "jira": {
                "enabled": True,
                "instances": [{"name": "Jira", "url": "https://jira.example", "enabled": True}],
            },
            "confluence": {
                "enabled": True,
                "instances": [{"name": "Docs", "url": "https://conf.example/wiki", "enabled": True}],
            },
            "github": {"enabled": True, "api_token": "ghp"},
            "aws": {"enabled": True, "domain": "HBEU", "username": "aws-user", "password": "aws-password"},
            "git": {"user": {"name": "Bot", "email": "bot@example.com"}},
        },
        runtime_type="native",
    )

    _assert_cli_instruction_texts(projected["instruction_texts"])
    assert projected["jira"]["instances"][0]["url"] == "https://jira.example"
    assert projected["confluence"]["instances"][0]["url"] == "https://conf.example/wiki"
    assert projected["github"]["api_token"] == "ghp"
    assert projected["aws"]["domain"] == "HBEU"
    assert projected["git"]["user"]["email"] == "bot@example.com"


def test_native_runtime_profile_context_config_projects_mobile_profile():
    projected = build_runtime_profile_context_config(
        {
            "mobile-auto": {
                "enabled": True,
                "defaults": {"platform": "android", "network_mode": "private-external"},
                "browserstack": {
                    "username": "bs-user",
                    "access_key": "bs-key",
                    "local": {"mode": "external", "binary": "/usr/local/bin/BrowserStackLocal"},
                },
            }
        },
        runtime_type="native",
    )

    _assert_cli_instruction_texts(projected["instruction_texts"])
    assert projected["mobile-auto"]["enabled"] is True
    assert projected["mobile-auto"]["browserstack"]["username"] == "bs-user"
    assert projected["mobile-auto"]["browserstack"]["access_key"] == "bs-key"
    assert projected["mobile-auto"]["browserstack"]["local"]["binary"] == "/usr/local/bin/BrowserStackLocal"


def test_opencode_runtime_profile_context_config_omits_efp_instruction_texts():
    projected = build_runtime_profile_context_config(
        {
            "jira": {
                "enabled": True,
                "instances": [{"name": "Jira", "url": "https://jira.example", "enabled": True}],
            },
            "github": {"enabled": True, "api_token": "ghp"},
            "aws": {"enabled": True, "domain": "HBEU"},
            "instruction_texts": ["user supplied instruction"],
        },
        runtime_type="opencode",
    )

    assert "instruction_texts" not in projected
    assert projected["jira"]["enabled"] is True
    assert projected["github"]["api_token"] == "ghp"
    assert projected["aws"]["domain"] == "HBEU"
