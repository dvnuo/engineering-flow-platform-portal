import pytest

from app.web import _settings_llm_tools_view, _settings_merge_payload, _settings_parse_llm_tools_patterns


def test_settings_merge_github_base_url_blank_removes_existing_value():
    config_payload = {
        "github": {
            "enabled": True,
            "api_token": "keep-token",
            "base_url": "https://github.company.com/api/v3",
        }
    }
    form = {
        "__touch_github": "1",
        "github_enabled": "on",
        "github_api_token": "",
        "github_base_url": "",
    }

    merged, error = _settings_merge_payload(config_payload, form)

    assert error is None
    assert "api_token" not in merged["github"]
    assert "base_url" not in merged["github"]


def test_settings_merge_legacy_automation_payloads_are_ignored():
    merged, error = _settings_merge_payload(
        {},
        {
            "github_review_requests_enabled": "on",
            "github_review_requests_repos": " org/a ,\norg/b\n\norg/a",
            "github_mentions_enabled": "on",
            "github_mentions_repos": "org/b\norg/c, org/d",
            "github_mentions_include_review_comments": "on",
            "jira_assignments_enabled": "on",
            "jira_assignments_projects": " ENG,\n QA, ENG",
            "jira_mentions_enabled": "on",
            "jira_mentions_projects": "OPS\n\nENG",
            "confluence_mentions_enabled": "on",
            "confluence_mentions_spaces": " DEV\nDOCS, DEV ",
        },
    )

    assert error is None
    assert "github" not in merged
    assert "jira" not in merged
    assert "confluence" not in merged


def test_settings_merge_llm_tools_all_mode():
    merged, error = _settings_merge_payload({}, {"__touch_llm": "1", "llm_tools_mode": "all", "llm_tools_count": "0"})
    assert error is None
    assert merged["llm"]["tools"] == ["*"]


def test_settings_merge_llm_tools_none_mode():
    merged, error = _settings_merge_payload({}, {"__touch_llm": "1", "llm_tools_mode": "none", "llm_tools_count": "0"})
    assert error is None
    assert merged["llm"]["tools"] == []


def test_settings_merge_llm_tools_custom_mode_dedupes_and_preserves_system_prompt():
    merged, error = _settings_merge_payload(
        {"llm": {"system-prompt": {"tools": {"enabled": True}}}},
        {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "4",
            "llm_tools_0_pattern": " git_clone ",
            "llm_tools_1_pattern": "jira_*",
            "llm_tools_2_pattern": "",
            "llm_tools_3_pattern": "GIT_CLONE",
        },
    )
    assert error is None
    assert merged["llm"]["tools"] == ["git_clone", "jira_*"]
    assert merged["llm"]["system-prompt"]["tools"]["enabled"] is True


def test_settings_merge_llm_tools_custom_mode_all_blank_patterns_saves_empty_list_and_preserves_system_prompt():
    merged, error = _settings_merge_payload(
        {"llm": {"system-prompt": {"tools": {"enabled": True}}}},
        {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "3",
            "llm_tools_0_pattern": "",
            "llm_tools_1_pattern": "   ",
            "llm_tools_2_pattern": "",
        },
    )
    assert error is None
    assert merged["llm"]["tools"] == []
    assert merged["llm"]["system-prompt"]["tools"]["enabled"] is True


@pytest.mark.parametrize(
    ("llm", "expected"),
    [
        ({}, ("inherit", [])),
        ({"tools": ["*"]}, ("all", [])),
        ({"tools": []}, ("none", [])),
        ({"tools": None}, ("none", [])),
        ({"tools": ""}, ("none", [])),
        ({"tools": ["git_clone", "jira_*"]}, ("custom", ["git_clone", "jira_*"])),
    ],
)
def test_settings_llm_tools_view_modes(llm, expected):
    assert _settings_llm_tools_view(llm) == expected


def test_settings_parse_llm_tools_patterns_handles_plain_dict_invalid_count_and_dedupes():
    parsed = _settings_parse_llm_tools_patterns(
        {
            "llm_tools_count": "invalid",
            "llm_tools_0_pattern": "git_clone",
            "llm_tools_1_pattern": " GIT_CLONE ",
            "llm_tools_2_pattern": "jira_*",
        }
    )
    assert parsed == []


def test_settings_merge_llm_response_flow_writes_nested_dict():
    merged, error = _settings_merge_payload(
        {"llm": {"provider": "openai", "tools": ["*"]}},
        {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_response_flow_plan_policy": "explicit_or_complex",
            "llm_response_flow_staging_policy": "always",
            "llm_response_flow_default_skill_execution_style": "direct",
            "llm_response_flow_ask_user_policy": "blocked_only",
            "llm_response_flow_active_skill_conflict_policy": "always_ask",
            "llm_response_flow_complexity_prompt_budget_ratio": "0.85",
            "llm_response_flow_complexity_min_request_tokens": "24000",
        },
    )
    assert error is None
    assert merged["llm"]["provider"] == "openai"
    assert merged["llm"]["tools"] == ["*"]
    assert merged["llm"]["response_flow"] == {
        "plan_policy": "explicit_or_complex",
        "staging_policy": "always",
        "default_skill_execution_style": "direct",
        "ask_user_policy": "blocked_only",
        "active_skill_conflict_policy": "always_ask",
        "complexity_prompt_budget_ratio": 0.85,
        "complexity_min_request_tokens": 24000,
    }


def test_settings_merge_llm_response_flow_blank_values_omit_subtree():
    merged, error = _settings_merge_payload(
        {
            "llm": {
                "provider": "openai",
                "response_flow": {
                    "plan_policy": "always",
                    "complexity_prompt_budget_ratio": 0.2,
                },
            }
        },
        {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_response_flow_plan_policy": "",
            "llm_response_flow_staging_policy": "",
            "llm_response_flow_default_skill_execution_style": "",
            "llm_response_flow_ask_user_policy": "",
            "llm_response_flow_active_skill_conflict_policy": "",
            "llm_response_flow_complexity_prompt_budget_ratio": "",
            "llm_response_flow_complexity_min_request_tokens": "",
        },
    )
    assert error is None
    assert "response_flow" not in merged["llm"]
    assert merged["llm"]["provider"] == "openai"


def test_settings_merge_llm_response_flow_active_skill_conflict_policy_valid_value_writes_nested_key():
    merged, error = _settings_merge_payload(
        {"llm": {"provider": "openai"}},
        {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_response_flow_active_skill_conflict_policy": "auto_switch_direct",
        },
    )
    assert error is None
    assert merged["llm"]["response_flow"]["active_skill_conflict_policy"] == "auto_switch_direct"


def test_settings_merge_llm_response_flow_active_skill_conflict_policy_blank_value_removes_key():
    merged, error = _settings_merge_payload(
        {
            "llm": {
                "provider": "openai",
                "response_flow": {"active_skill_conflict_policy": "always_ask"},
            }
        },
        {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_response_flow_active_skill_conflict_policy": "",
        },
    )
    assert error is None
    assert "response_flow" not in merged["llm"]


def test_settings_merge_llm_response_flow_invalid_ratio_returns_error():
    merged, error = _settings_merge_payload(
        {"llm": {"provider": "openai"}},
        {
            "__touch_llm": "1",
            "llm_response_flow_complexity_prompt_budget_ratio": "1.5",
        },
    )
    assert merged == {"llm": {"provider": "openai"}}
    assert error == "Response flow complexity ratio must be a number between 0 and 1."


def test_settings_merge_llm_response_flow_invalid_min_tokens_returns_error():
    merged, error = _settings_merge_payload(
        {"llm": {"provider": "openai"}},
        {
            "__touch_llm": "1",
            "llm_response_flow_complexity_min_request_tokens": "0",
        },
    )
    assert merged == {"llm": {"provider": "openai"}}
    assert error == "Response flow complexity minimum tokens must be a positive integer."
