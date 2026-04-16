import pytest

from app.web import _settings_llm_tools_view, _settings_merge_payload


def test_settings_merge_github_base_url_blank_removes_existing_value():
    config_payload = {
        "github": {
            "enabled": True,
            "api_token": "keep-token",
            "base_url": "https://github.company.com/api/v3",
        }
    }
    form = {
        "github_enabled": "on",
        "github_api_token": "",
        "github_base_url": "",
    }

    merged, error = _settings_merge_payload(config_payload, form)

    assert error is None
    assert merged["github"]["api_token"] == "keep-token"
    assert "base_url" not in merged["github"]


def test_settings_merge_automation_payloads_parse_lists_and_flags():
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
    assert merged["github"]["automation"]["review_requests"] == {
        "enabled": True,
        "repos": ["org/a", "org/b"],
    }
    assert merged["github"]["automation"]["mentions"] == {
        "enabled": True,
        "repos": ["org/b", "org/c", "org/d"],
        "include_review_comments": True,
    }
    assert merged["jira"]["automation"]["assignments"] == {
        "enabled": True,
        "projects": ["ENG", "QA"],
    }
    assert merged["jira"]["automation"]["mentions"] == {
        "enabled": True,
        "projects": ["OPS", "ENG"],
    }
    assert merged["confluence"]["automation"]["mentions"] == {
        "enabled": True,
        "spaces": ["DEV", "DOCS"],
    }


def test_settings_merge_llm_tools_all_mode():
    merged, error = _settings_merge_payload({}, {"llm_tools_mode": "all", "llm_tools_count": "0"})
    assert error is None
    assert merged["llm"]["tools"] == ["*"]


def test_settings_merge_llm_tools_none_mode():
    merged, error = _settings_merge_payload({}, {"llm_tools_mode": "none", "llm_tools_count": "0"})
    assert error is None
    assert merged["llm"]["tools"] == []


def test_settings_merge_llm_tools_custom_mode_dedupes_and_preserves_system_prompt():
    merged, error = _settings_merge_payload(
        {"llm": {"system-prompt": {"tools": {"enabled": True}}}},
        {
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


@pytest.mark.parametrize(
    ("llm", "expected"),
    [
        ({}, ("all", [])),
        ({"tools": ["*"]}, ("all", [])),
        ({"tools": []}, ("none", [])),
        ({"tools": None}, ("none", [])),
        ({"tools": ""}, ("none", [])),
        ({"tools": ["git_clone", "jira_*"]}, ("custom", ["git_clone", "jira_*"])),
    ],
)
def test_settings_llm_tools_view_modes(llm, expected):
    assert _settings_llm_tools_view(llm) == expected
