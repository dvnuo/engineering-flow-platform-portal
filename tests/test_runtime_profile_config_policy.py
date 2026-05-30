from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config


def test_canonicalize_none_and_empty():
    assert canonicalize_portal_runtime_profile_config(None) == {}
    assert canonicalize_portal_runtime_profile_config({}) == {}


def test_canonicalize_strips_hidden_llm_fields_and_preserves_other_llm_fields():
    raw = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5",
            "temperature": 0.1,
            "response_flow": {"plan_policy": "always"},
            "tool_loop": {"one_tool_per_turn": True},
            "tools": ["read"],
            "context_budget": {"max_tokens": 32000},
            "context_projection": {"strategy": "summary"},
        },
        "jira": {"enabled": True},
    }
    result = canonicalize_portal_runtime_profile_config(raw)
    assert result["llm"]["provider"] == "openai"
    assert result["llm"]["model"] == "gpt-5"
    assert "temperature" not in result["llm"]
    assert "response_flow" not in result["llm"]
    assert "tool_loop" not in result["llm"]
    assert "tools" not in result["llm"]
    assert "context_budget" not in result["llm"]
    assert "context_projection" not in result["llm"]
    assert result["jira"] == {"enabled": True}


def test_canonicalize_preserves_existing_non_hidden_fields_without_runtime_tool_defaults():
    raw = {
        "llm": {
            "provider": "github_copilot",
            "model": "gpt-5-mini",
        },
        "github": {"enabled": True},
    }

    result = canonicalize_portal_runtime_profile_config(raw)

    assert "tools" not in result["llm"]
    assert result["github"] == {"enabled": True}


def test_canonicalize_strips_explicit_llm_tools():
    raw = {
        "llm": {"tools": ["read"]},
    }

    result = canonicalize_portal_runtime_profile_config(raw)

    assert result == {}


def test_canonicalize_does_not_mutate_input():
    raw = {
        "llm": {
            "tools": [],
            "temperature": 0.1,
        },
    }

    result = canonicalize_portal_runtime_profile_config(raw)

    assert raw["llm"]["tools"] == []
    assert raw["llm"]["temperature"] == 0.1
    assert "llm" not in result
