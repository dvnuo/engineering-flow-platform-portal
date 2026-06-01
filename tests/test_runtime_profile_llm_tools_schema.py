import json

from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
    validate_runtime_profile_config_json,
)


def test_llm_tools_string_wildcard_is_dropped():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": "*"}}')
    assert parsed == {}


def test_llm_tools_string_pattern_is_dropped():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": "webfetch"}}')
    assert parsed == {}


def test_llm_tools_list_is_dropped():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": [" bash ", "webfetch", "", "BASH"]}}')
    assert parsed == {}


def test_llm_tools_none_and_blank_string_are_dropped():
    parsed_none = parse_runtime_profile_config_json('{"llm": {"tools": null}}')
    parsed_blank = parse_runtime_profile_config_json('{"llm": {"tools": ""}}')
    assert parsed_none == {}
    assert parsed_blank == {}


def test_llm_tools_non_string_in_list_is_dropped_without_validation():
    parsed = parse_runtime_profile_config_json('{"llm": {"tools": ["webfetch", 123]}}')
    assert parsed == {}


def test_validate_and_dump_drop_llm_tools():
    normalized = validate_runtime_profile_config_json('{"llm": {"tools": "webfetch"}}')
    dumped = dump_runtime_profile_config_json({"llm": {"tools": "*"}})
    assert json.loads(normalized) == {}
    assert json.loads(dumped) == {}
