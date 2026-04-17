import json

import pytest

from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
    validate_runtime_profile_config_json,
)


def test_llm_tools_string_wildcard_normalizes_to_list():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": "*"}}')
    assert parsed["llm"]["tools"] == ["*"]


def test_llm_tools_string_pattern_normalizes_to_single_item_list():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": "jira_*"}}')
    assert parsed["llm"]["tools"] == ["jira_*"]


def test_llm_tools_list_trim_drop_empty_and_dedupe():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": [" git_clone ", "jira_*", "", "GIT_CLONE"]}}')
    assert parsed["llm"]["tools"] == ["git_clone", "jira_*"]


def test_llm_tools_none_and_blank_string_normalize_to_empty_list():
    parsed_none = parse_runtime_profile_config_json('{"llm": {"tools": null}}')
    parsed_blank = parse_runtime_profile_config_json('{"llm": {"tools": ""}}')
    assert parsed_none["llm"]["tools"] == []
    assert parsed_blank["llm"]["tools"] == []


def test_llm_tools_non_string_in_list_raises_value_error():
    with pytest.raises(ValueError, match=r"llm\.tools must be a string or list of strings"):
        parse_runtime_profile_config_json('{"llm": {"tools": ["jira_*", 123]}}')


def test_validate_and_dump_use_llm_tools_normalization():
    normalized = validate_runtime_profile_config_json('{"llm": {"tools": "jira_*"}}')
    dumped = dump_runtime_profile_config_json({"llm": {"tools": "*"}})
    assert json.loads(normalized)["llm"]["tools"] == ["jira_*"]
    assert json.loads(dumped)["llm"]["tools"] == ["*"]
