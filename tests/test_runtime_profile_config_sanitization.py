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
    assert "access" not in red["llm"]["oauth"] and "refresh" not in red["llm"]["oauth"]
    assert "access" not in red["llm"]["oauth_by_runtime"]["opencode"]
    assert "api_token" not in red["github"] and red["github"]["api_token_present"] is True
    assert "password" not in red["proxy"] and red["proxy"]["password_present"] is True
    assert "password" not in red["jira"]["instances"][0] and red["jira"]["instances"][0]["password_present"] is True
    assert "token" not in red["confluence"]["instances"][0] and red["confluence"]["instances"][0]["token_present"] is True
