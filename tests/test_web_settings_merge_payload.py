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
    assert merged["github"]["api_token"] == "keep-token"
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


def test_github_copilot_legacy_oauth_hidden_fields_saved_as_opencode_oauth_by_runtime():
    merged, error = _settings_merge_payload({}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_model":"gpt-5.4-mini","llm_api_key":"","llm_oauth_type":"oauth","llm_oauth_access":"gho_A","llm_oauth_refresh":"gho_R","llm_oauth_expires":"0"})
    assert error is None
    assert merged["llm"]["provider"] == "github_copilot"
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["access"] == "gho_A"
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["refresh"] == "gho_R"
    assert "native" not in merged["llm"]["oauth_by_runtime"]
    assert "oauth" not in merged["llm"]
    assert "api_key" not in merged["llm"]

def test_switching_from_copilot_to_openai_clears_oauth_and_uses_api_key():
    merged, error = _settings_merge_payload({"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_A","refresh":"gho_A","expires":0}}}, {"__touch_llm":"1","llm_provider":"openai","llm_api_key":"sk_TEST"})
    assert error is None
    assert "oauth" not in merged["llm"]
    assert "oauth_by_runtime" not in merged["llm"]
    assert merged["llm"]["api_key"] == "sk_TEST"

def test_github_copilot_without_hidden_oauth_can_keep_legacy_api_key():
    merged, error = _settings_merge_payload({}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_api_key":"gho_LEGACY"})
    assert error is None
    assert merged["llm"]["api_key"] == "gho_LEGACY"
    assert "oauth" not in merged["llm"]


def test_github_copilot_preserves_existing_oauth_when_hidden_fields_blank():
    merged, error = _settings_merge_payload({"llm":{"provider":"github_copilot","model":"gpt-5.4-mini","oauth":{"type":"oauth","access":"gho_A","refresh":"gho_R","expires":0}}}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_model":"gpt-5.4-mini","llm_api_key":"","llm_oauth_type":"oauth","llm_oauth_access":"","llm_oauth_refresh":"","llm_oauth_expires":"0"})
    assert error is None
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["access"] == "gho_A"
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["refresh"] == "gho_R"
    assert "oauth" not in merged["llm"]
    assert "api_key" not in merged["llm"]

def test_github_copilot_new_oauth_overrides_existing_oauth():
    merged, error = _settings_merge_payload({"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_OLD","refresh":"gho_OLD","expires":0}}}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_oauth_access":"gho_NEW","llm_oauth_refresh":"","llm_oauth_expires":"0","llm_api_key":""})
    assert error is None
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["access"] == "gho_NEW"
    assert "native" not in merged["llm"]["oauth_by_runtime"]

def test_github_copilot_explicit_oauth_clear_removes_oauth():
    merged, error = _settings_merge_payload({"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_OLD","refresh":"gho_OLD","expires":0}}}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_api_key":"","llm_oauth_access":"","llm_oauth_refresh":"","llm_oauth_clear":"1"})
    assert error is None
    assert "oauth" not in merged["llm"]
    assert "oauth_by_runtime" not in merged["llm"]

def test_github_copilot_native_oauth_new_fields_saved():
    merged, error = _settings_merge_payload({}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_model":"gpt-5.4-mini","llm_api_key":"","llm_oauth_native_type":"oauth","llm_oauth_native_access":"NATIVE_SECRET","llm_oauth_native_refresh":"NATIVE_SECRET","llm_oauth_native_expires":"0"})
    assert error is None
    assert merged["llm"]["oauth_by_runtime"]["native"]["access"] == "NATIVE_SECRET"
    assert "opencode" not in merged["llm"]["oauth_by_runtime"]
    assert "oauth" not in merged["llm"]
    assert "api_key" not in merged["llm"]

def test_github_copilot_opencode_oauth_new_fields_saved():
    merged, error = _settings_merge_payload({}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_model":"gpt-5.4-mini","llm_api_key":"","llm_oauth_opencode_access":"OPENCODE_SECRET"})
    assert error is None
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["access"] == "OPENCODE_SECRET"

def test_github_copilot_clear_native_only_preserves_opencode():
    merged, error = _settings_merge_payload({"llm":{"provider":"github_copilot","oauth_by_runtime":{"native":{"type":"oauth","access":"N","refresh":"N","expires":0},"opencode":{"type":"oauth","access":"O","refresh":"O","expires":0}}}}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_oauth_native_clear":"1"})
    assert error is None
    assert "native" not in merged["llm"]["oauth_by_runtime"]
    assert merged["llm"]["oauth_by_runtime"]["opencode"]["access"] == "O"

def test_github_copilot_clear_opencode_only_preserves_native():
    merged, error = _settings_merge_payload({"llm":{"provider":"github_copilot","oauth_by_runtime":{"native":{"type":"oauth","access":"N","refresh":"N","expires":0},"opencode":{"type":"oauth","access":"O","refresh":"O","expires":0}}}}, {"__touch_llm":"1","llm_provider":"github_copilot","llm_oauth_opencode_clear":"1"})
    assert error is None
    assert "opencode" not in merged["llm"]["oauth_by_runtime"]
    assert merged["llm"]["oauth_by_runtime"]["native"]["access"] == "N"

def test_settings_merge_blank_github_token_keeps_existing_without_clear():
    merged, error = _settings_merge_payload({"github": {"api_token": "old"}}, {"__touch_github": "1", "github_enabled": "on", "github_api_token": ""})
    assert error is None
    assert merged["github"]["api_token"] == "old"


def test_settings_merge_blank_proxy_password_keeps_existing_without_clear():
    merged, error = _settings_merge_payload({"proxy": {"password": "old"}}, {"__touch_proxy": "1", "proxy_enabled": "on", "proxy_password": ""})
    assert error is None
    assert merged["proxy"]["password"] == "old"


def test_settings_merge_blank_llm_api_key_keeps_existing_without_clear():
    merged, error = _settings_merge_payload({"llm": {"provider": "openai", "api_key": "old"}}, {"__touch_llm": "1", "llm_provider": "openai", "llm_api_key": ""})
    assert error is None
    assert merged["llm"]["api_key"] == "old"


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


def test_settings_merge_jira_instance_blank_secret_preserves_existing():
    merged, error = _settings_merge_payload(
        {"jira": {"instances": [{"name": "J", "url": "https://a", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_jira": "1", "jira_enabled": "on", "jira_instance_count": "1",
            "jira_instances_0_name": "J", "jira_instances_0_url": "https://a",
            "jira_instances_0_password": "", "jira_instances_0_token": "",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["password"] == "oldp"
    assert merged["jira"]["instances"][0]["token"] == "oldt"


def test_settings_merge_jira_instance_clear_secret_removes_existing():
    merged, error = _settings_merge_payload(
        {"jira": {"instances": [{"name": "J", "url": "https://a", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_jira": "1", "jira_enabled": "on", "jira_instance_count": "1",
            "jira_instances_0_name": "J", "jira_instances_0_url": "https://a",
            "jira_instances_0_password": "", "jira_instances_0_token": "",
            "jira_instances_0_password_clear": "1", "jira_instances_0_token_clear": "1",
        },
    )
    assert error is None
    assert merged["jira"]["instances"][0]["password"] == ""
    assert merged["jira"]["instances"][0]["token"] == ""

def test_settings_merge_confluence_instance_blank_secret_preserves_existing():
    merged, error = _settings_merge_payload(
        {"confluence": {"instances": [{"name": "C", "url": "https://a/wiki", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_confluence": "1", "confluence_enabled": "on", "confluence_instance_count": "1",
            "confluence_instances_0_name": "C", "confluence_instances_0_url": "https://a/wiki",
            "confluence_instances_0_password": "", "confluence_instances_0_token": "",
        },
    )
    assert error is None
    assert merged["confluence"]["instances"][0]["password"] == "oldp"
    assert merged["confluence"]["instances"][0]["token"] == "oldt"


def test_settings_merge_confluence_instance_clear_secret_removes_existing():
    merged, error = _settings_merge_payload(
        {"confluence": {"instances": [{"name": "C", "url": "https://a/wiki", "password": "oldp", "token": "oldt"}]}},
        {
            "__touch_confluence": "1", "confluence_enabled": "on", "confluence_instance_count": "1",
            "confluence_instances_0_name": "C", "confluence_instances_0_url": "https://a/wiki",
            "confluence_instances_0_password": "", "confluence_instances_0_token": "",
            "confluence_instances_0_password_clear": "1", "confluence_instances_0_token_clear": "1",
        },
    )
    assert error is None
    assert merged["confluence"]["instances"][0]["password"] == ""
    assert merged["confluence"]["instances"][0]["token"] == ""
