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
    assert merged["llm"]["tools"] == ["*"]


def test_settings_merge_llm_tools_custom_mode_dedupes_and_preserves_system_prompt():
    merged, error = _settings_merge_payload(
        {"llm": {"system-prompt": {"tools": {"enabled": True}}}},
        {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "4",
            "llm_tools_0_pattern": " bash ",
            "llm_tools_1_pattern": "webfetch",
            "llm_tools_2_pattern": "",
            "llm_tools_3_pattern": "BASH",
        },
    )
    assert error is None
    assert merged["llm"]["tools"] == ["*"]
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
    assert merged["llm"]["tools"] == ["*"]
    assert merged["llm"]["system-prompt"]["tools"]["enabled"] is True


@pytest.mark.parametrize(
    ("llm", "expected"),
    [
        ({}, ("inherit", [])),
        ({"tools": ["*"]}, ("all", [])),
        ({"tools": []}, ("none", [])),
        ({"tools": None}, ("none", [])),
        ({"tools": ""}, ("none", [])),
        ({"tools": ["bash", "webfetch"]}, ("custom", ["bash", "webfetch"])),
    ],
)
def test_settings_llm_tools_view_modes(llm, expected):
    assert _settings_llm_tools_view(llm) == expected


def test_settings_parse_llm_tools_patterns_handles_plain_dict_invalid_count_and_dedupes():
    parsed = _settings_parse_llm_tools_patterns(
        {
            "llm_tools_count": "invalid",
            "llm_tools_0_pattern": "bash",
            "llm_tools_1_pattern": " BASH ",
            "llm_tools_2_pattern": "webfetch",
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
    assert "response_flow" not in merged["llm"]


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
    assert "response_flow" not in merged["llm"]


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
    assert error is None
    assert merged["llm"]["tools"] == ["*"]
    assert "response_flow" not in merged["llm"]


def test_settings_merge_llm_response_flow_invalid_min_tokens_returns_error():
    merged, error = _settings_merge_payload(
        {"llm": {"provider": "openai"}},
        {
            "__touch_llm": "1",
            "llm_response_flow_complexity_min_request_tokens": "0",
        },
    )
    assert error is None
    assert merged["llm"]["tools"] == ["*"]
    assert "response_flow" not in merged["llm"]


def test_settings_merge_copilot_uses_llm_api_key_only():
    merged, error = _settings_merge_payload({}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_model":"gpt-5.4-mini","llm_api_key":"TOKEN"})
    assert error is None
    assert merged["llm"]["api_key"] == "TOKEN"
    assert "oauth" not in merged["llm"]
    assert "oauth_by_runtime" not in merged["llm"]

def test_settings_merge_blank_github_token_clears_existing():
    merged, error = _settings_merge_payload({"github": {"api_token": "old"}}, {"__touch_github": "1", "github_enabled": "on", "github_api_token": ""})
    assert error is None
    assert "api_token" not in merged["github"]


def test_settings_merge_blank_proxy_password_clears_existing():
    merged, error = _settings_merge_payload({"proxy": {"password": "old"}}, {"__touch_proxy": "1", "proxy_enabled": "on", "proxy_password": ""})
    assert error is None
    assert "password" not in merged["proxy"]


def test_settings_merge_blank_llm_api_key_clears_existing():
    merged, error = _settings_merge_payload({"llm": {"provider": "openai", "api_key": "old"}}, {"__touch_llm": "1", "llm_provider": "openai", "llm_api_key": ""})
    assert error is None
    assert "api_key" not in merged["llm"]


def test_settings_merge_clear_flags_remove_secrets():
    merged, error = _settings_merge_payload(
        {"llm": {"provider": "openai", "api_key": "old"}, "github": {"api_token": "gh"}, "proxy": {"password": "pw"}},
        {
            "__touch_llm": "1", "llm_provider": "openai", "llm_api_key": "", "llm_api_key_clear": "1",
            "__touch_github": "1", "github_enabled": "on", "github_api_token": "", "github_api_token_clear": "1",
            "__touch_proxy": "1", "proxy_enabled": "on", "proxy_password": "", "proxy_password_clear": "1",
        },
    )
    assert error is None
    assert "api_key" not in merged["llm"]
    assert "api_token" not in merged["github"]
    assert "password" not in merged["proxy"]


def test_settings_merge_jira_instance_blank_secret_clears_existing():
    merged, error = _settings_merge_payload(
        {"jira": {"instances": [{"name": "J", "url": "https://a", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_jira": "1", "jira_enabled": "on", "jira_instance_count": "1",
            "jira_instances_0_original_name": "J", "jira_instances_0_original_url": "https://a",
            "jira_instances_0_name": "J", "jira_instances_0_url": "https://a",
            "jira_instances_0_password": "", "jira_instances_0_token": "",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["password"] == ""
    assert merged["jira"]["instances"][0]["token"] == ""


def test_settings_merge_jira_instance_clear_secret_removes_existing():
    merged, error = _settings_merge_payload(
        {"jira": {"instances": [{"name": "J", "url": "https://a", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_jira": "1", "jira_enabled": "on", "jira_instance_count": "1",
            "jira_instances_0_original_name": "J", "jira_instances_0_original_url": "https://a",
            "jira_instances_0_name": "J", "jira_instances_0_url": "https://a",
            "jira_instances_0_password": "", "jira_instances_0_token": "",
            "jira_instances_0_password_clear": "1", "jira_instances_0_token_clear": "1",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["password"] == ""
    assert merged["jira"]["instances"][0]["token"] == ""

def test_settings_merge_confluence_instance_blank_secret_clears_existing():
    merged, error = _settings_merge_payload(
        {"confluence": {"instances": [{"name": "C", "url": "https://a/wiki", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_confluence": "1", "confluence_enabled": "on", "confluence_instance_count": "1",
            "confluence_instances_0_original_name": "C", "confluence_instances_0_original_url": "https://a/wiki",
            "confluence_instances_0_name": "C", "confluence_instances_0_url": "https://a/wiki",
            "confluence_instances_0_password": "", "confluence_instances_0_token": "",
        },
    )
    assert error is None
    assert merged["confluence"]["instances"][0]["password"] == ""
    assert merged["confluence"]["instances"][0]["token"] == ""


def test_settings_merge_confluence_instance_clear_secret_removes_existing():
    merged, error = _settings_merge_payload(
        {"confluence": {"instances": [{"name": "C", "url": "https://a/wiki", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_confluence": "1", "confluence_enabled": "on", "confluence_instance_count": "1",
            "confluence_instances_0_original_name": "C", "confluence_instances_0_original_url": "https://a/wiki",
            "confluence_instances_0_name": "C", "confluence_instances_0_url": "https://a/wiki",
            "confluence_instances_0_password": "", "confluence_instances_0_token": "",
            "confluence_instances_0_password_clear": "1", "confluence_instances_0_token_clear": "1",
        },
    )
    assert error is None
    assert merged["confluence"]["instances"][0]["password"] == ""
    assert merged["confluence"]["instances"][0]["token"] == ""

def test_settings_merge_jira_instance_enabled_false_is_preserved_from_unchecked_checkbox():
    merged, error = _settings_merge_payload(
        {"jira": {"enabled": True, "instances": [{"name": "off", "url": "https://j", "username": "u", "token": "tok", "enabled": False}]}},
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_original_name": "off",
            "jira_instances_0_original_url": "https://j",
            "jira_instances_0_name": "off",
            "jira_instances_0_url": "https://j",
            "jira_instances_0_username": "u",
            "jira_instances_0_project": "ENG",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["enabled"] is False
    assert merged["jira"]["instances"][0]["token"] == "tok"


def test_settings_merge_jira_instance_enabled_true_from_checkbox():
    merged, error = _settings_merge_payload(
        {"jira": {"enabled": True, "instances": [{"name": "on", "url": "https://j", "username": "u", "token": "tok", "enabled": False}]}},
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_name": "on",
            "jira_instances_0_url": "https://j",
            "jira_instances_0_username": "u",
            "jira_instances_0_enabled": "1",
            "jira_instances_0_token": "",
            "jira_instances_0_project": "ENG",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["enabled"] is True


def test_settings_merge_jira_instance_api_version_is_merged():
    merged, error = _settings_merge_payload(
        {"jira": {"enabled": True, "instances": [{"name": "J", "url": "https://j", "username": "u"}]}},
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_original_name": "J",
            "jira_instances_0_original_url": "https://j",
            "jira_instances_0_name": "J",
            "jira_instances_0_url": "https://j",
            "jira_instances_0_username": "u",
            "jira_instances_0_api_version": "2",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["api_version"] == "2"


def test_settings_merge_confluence_instance_enabled_false_from_unchecked_checkbox():
    merged, error = _settings_merge_payload(
        {"confluence": {"enabled": True, "instances": [{"name": "off", "url": "https://c", "username": "u", "token": "tok", "enabled": False}]}},
        {
            "__touch_confluence": "1",
            "confluence_enabled": "on",
            "confluence_instance_count": "1",
            "confluence_instances_0_original_name": "off",
            "confluence_instances_0_original_url": "https://c",
            "confluence_instances_0_name": "off",
            "confluence_instances_0_url": "https://c",
            "confluence_instances_0_username": "u",
            "confluence_instances_0_space": "DOCS",
        },
    )
    assert error is None
    assert merged["confluence"]["instances"][0]["enabled"] is False
    assert merged["confluence"]["instances"][0]["token"] == "tok"

def test_settings_merge_jira_instance_preserves_secret_by_original_identity_not_index():
    merged, error = _settings_merge_payload(
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {"name": "A", "url": "https://a", "username": "u", "token": "A_TOKEN"},
                    {"name": "B", "url": "https://b", "username": "u", "token": "B_TOKEN"},
                ],
            }
        },
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_original_name": "B",
            "jira_instances_0_original_url": "https://b",
            "jira_instances_0_name": "B",
            "jira_instances_0_url": "https://b",
            "jira_instances_0_username": "u",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["token"] == "B_TOKEN"


def test_settings_merge_confluence_instance_preserves_secret_by_original_identity_not_index():
    merged, error = _settings_merge_payload(
        {
            "confluence": {
                "enabled": True,
                "instances": [
                    {"name": "A", "url": "https://a", "username": "u", "token": "A_TOKEN"},
                    {"name": "B", "url": "https://b", "username": "u", "token": "B_TOKEN"},
                ],
            }
        },
        {
            "__touch_confluence": "1",
            "confluence_enabled": "on",
            "confluence_instance_count": "1",
            "confluence_instances_0_original_name": "B",
            "confluence_instances_0_original_url": "https://b",
            "confluence_instances_0_name": "B",
            "confluence_instances_0_url": "https://b",
            "confluence_instances_0_username": "u",
        },
    )
    assert error is None
    assert merged["confluence"]["instances"][0]["token"] == "B_TOKEN"


def test_settings_merge_new_jira_instance_blank_secret_does_not_inherit_old_index_secret():
    merged, error = _settings_merge_payload(
        {"jira": {"enabled": True, "instances": [{"name": "A", "url": "https://a", "token": "A_TOKEN"}]}},
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_original_name": "",
            "jira_instances_0_original_url": "",
            "jira_instances_0_name": "NEW",
            "jira_instances_0_url": "https://new",
        },
    )
    assert error is None
    assert "token" not in merged["jira"]["instances"][0] or merged["jira"]["instances"][0]["token"] == ""


def test_settings_merge_jira_instance_rename_preserves_secret_by_original_identity():
    merged, error = _settings_merge_payload(
        {"jira": {"enabled": True, "instances": [{"name": "Old", "url": "https://old", "token": "TOK"}]}},
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_original_name": "Old",
            "jira_instances_0_original_url": "https://old",
            "jira_instances_0_name": "New",
            "jira_instances_0_url": "https://new",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["token"] == "TOK"

def test_settings_merge_new_jira_instance_same_current_identity_does_not_inherit_old_secret():
    merged, error = _settings_merge_payload(
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {"name": "Old", "url": "https://old", "username": "u", "token": "OLD_TOKEN"}
                ],
            }
        },
        {
            "__touch_jira": "1",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_original_name": "",
            "jira_instances_0_original_url": "",
            "jira_instances_0_name": "Old",
            "jira_instances_0_url": "https://old",
            "jira_instances_0_username": "u",
            "jira_instances_0_token": "",
        },
    )
    assert error is None
    row = merged["jira"]["instances"][0]
    assert row.get("token", "") == ""


def test_settings_merge_new_confluence_instance_same_current_identity_does_not_inherit_old_secret():
    merged, error = _settings_merge_payload(
        {
            "confluence": {
                "enabled": True,
                "instances": [
                    {"name": "Old", "url": "https://old", "username": "u", "token": "OLD_TOKEN"}
                ],
            }
        },
        {
            "__touch_confluence": "1",
            "confluence_enabled": "on",
            "confluence_instance_count": "1",
            "confluence_instances_0_original_name": "",
            "confluence_instances_0_original_url": "",
            "confluence_instances_0_name": "Old",
            "confluence_instances_0_url": "https://old",
            "confluence_instances_0_username": "u",
            "confluence_instances_0_token": "",
        },
    )
    assert error is None
    row = merged["confluence"]["instances"][0]
    assert row.get("token", "") == ""
