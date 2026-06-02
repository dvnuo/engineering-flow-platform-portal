from app.services.runtime_profile_context_projection import build_runtime_profile_context_config


def test_runtime_profile_context_config_preserves_llm_timeout_ms_for_native_runtime():
    projected = build_runtime_profile_context_config(
        {"llm": {"provider": "github_copilot", "model": "gpt-5-mini", "timeout_ms": 300000}},
        runtime_type="native",
    )

    assert projected["llm"]["provider"] == "github_copilot"
    assert projected["llm"]["model"] == "gpt-5-mini"
    assert projected["llm"]["timeout_ms"] == 300000


def test_runtime_profile_context_config_preserves_llm_timeout_ms_for_opencode_runtime():
    projected = build_runtime_profile_context_config(
        {"llm": {"provider": "github_copilot", "model": "gpt-5-mini", "timeout_ms": 300000}},
        runtime_type="opencode",
    )

    assert projected["llm"]["provider"] == "github-copilot"
    assert projected["llm"]["model"] == "github-copilot/gpt-5-mini"
    assert projected["llm"]["timeout_ms"] == 300000
