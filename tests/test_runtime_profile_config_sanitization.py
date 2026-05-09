from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict, redact_runtime_profile_config_for_public_response


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


def test_copilot_legacy_oauth_by_runtime_migrates_to_api_key():
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
    assert sanitized["llm"]["api_key"] == "O"
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
