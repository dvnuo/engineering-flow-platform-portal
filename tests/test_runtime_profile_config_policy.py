from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config


def test_canonicalize_none_and_empty():
    expected = {
        "llm": {"tools": ["*"]},
    }

    assert canonicalize_portal_runtime_profile_config(None) == expected
    assert canonicalize_portal_runtime_profile_config({}) == expected


def test_canonicalize_strips_hidden_llm_fields_and_preserves_other_llm_fields():
    raw = {
        "llm": {
            "provider": "openai",
            "model": "gpt-5",
            "temperature": 0.1,
            "tools": [],
            "response_flow": {"plan_policy": "always"},
            "tool_loop": {"one_tool_per_turn": True},
        },
        "capability" + "_" + "profile": {
            "skill_set": ["old-skill"],
            "allowed_external_systems": ["jira"],
            "tool_set": ["*"],
            "denied_skills": ["dangerous-skill"],
        },
        "policy" + "_" + "context": {"rules": {"old": True}},
        "jira": {"enabled": True},
    }
    result = canonicalize_portal_runtime_profile_config(raw)
    assert result["llm"]["provider"] == "openai"
    assert result["llm"]["model"] == "gpt-5"
    assert result["llm"]["tool_loop"] == {"one_tool_per_turn": True}
    assert result["llm"]["tools"] == ["*"]
    assert "temperature" not in result["llm"]
    assert "response_flow" not in result["llm"]
    assert "capability" + "_" + "profile" not in result
    assert "policy" + "_" + "context" not in result
    assert result["jira"] == {"enabled": True}


def test_canonicalize_does_not_mutate_input():
    raw = {
        "llm": {
            "tools": [],
            "temperature": 0.1,
        },
        "capability" + "_" + "profile": {
            "skill_set": ["old-skill"],
        },
    }

    result = canonicalize_portal_runtime_profile_config(raw)

    assert raw["llm"]["tools"] == []
    assert raw["llm"]["temperature"] == 0.1
    assert raw["capability" + "_" + "profile"]["skill_set"] == ["old-skill"]

    assert result["llm"]["tools"] == ["*"]
    assert "temperature" not in result["llm"]
    assert "capability" + "_" + "profile" not in result
