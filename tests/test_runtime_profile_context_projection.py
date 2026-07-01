from app.services.runtime_profile_context_projection import (
    DEFAULT_LLM_REQUEST_TIMEOUT_MS,
    build_runtime_profile_context_config,
)


def test_runtime_profile_context_config_uses_default_llm_timeout_for_native_runtime():
    projected = build_runtime_profile_context_config(
        {"llm": {"provider": "github_copilot", "model": "gpt-5-mini", "timeout_ms": 10000}},
        runtime_type="native",
    )

    assert projected["llm"]["provider"] == "github_copilot"
    assert projected["llm"]["model"] == "gpt-5-mini"
    assert projected["llm"]["timeout_ms"] == DEFAULT_LLM_REQUEST_TIMEOUT_MS


def test_runtime_profile_context_config_uses_default_llm_timeout_for_opencode_runtime():
    projected = build_runtime_profile_context_config(
        {"llm": {"provider": "github_copilot", "model": "gpt-5-mini", "timeout_ms": 10000}},
        runtime_type="opencode",
    )

    assert projected["llm"]["provider"] == "github-copilot"
    assert projected["llm"]["model"] == "github-copilot/gpt-5-mini"
    assert projected["llm"]["timeout_ms"] == DEFAULT_LLM_REQUEST_TIMEOUT_MS
