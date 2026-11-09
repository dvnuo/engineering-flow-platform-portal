from app.services.runtime_profile_context_projection import (
    DEFAULT_LLM_REQUEST_TIMEOUT_MS,
    build_runtime_profile_context_config,
)


def test_runtime_profile_context_config_uses_default_llm_timeout_for_native_runtime():
    projected = build_runtime_profile_context_config(
        {
            "llm": {
                "provider": "github_copilot",
                "model": "gpt-6-luna",
                "reasoning_effort": "max",
                "max_context_tokens": 1_000_000,
                "timeout_ms": 10000,
            }
        },
        runtime_type="native",
    )

    assert projected["llm"]["provider"] == "github_copilot"
    assert projected["llm"]["model"] == "gpt-6-luna"
    assert projected["llm"]["reasoning_effort"] == "max"
    assert "max_context_tokens" not in projected["llm"]
    assert projected["max_context_tokens"] == 1_000_000
    assert projected["llm"]["timeout_ms"] == DEFAULT_LLM_REQUEST_TIMEOUT_MS


def test_runtime_profile_context_config_uses_default_llm_timeout_for_opencode_runtime():
    projected = build_runtime_profile_context_config(
        {
            "llm": {
                "provider": "github_copilot",
                "model": "gpt-6-luna",
                "reasoning_effort": "max",
                "max_context_tokens": 1_000_000,
                "timeout_ms": 10000,
            }
        },
        runtime_type="opencode",
    )

    assert projected["llm"]["provider"] == "github-copilot"
    assert projected["llm"]["model"] == "github-copilot/gpt-6-luna"
    assert projected["llm"]["reasoning_effort"] == "max"
    assert "max_context_tokens" not in projected
    assert "max_context_tokens" not in projected["llm"]
    assert projected["llm"]["timeout_ms"] == DEFAULT_LLM_REQUEST_TIMEOUT_MS


def test_runtime_profile_context_config_projects_the_step_limit_for_both_runtimes():
    # The Model provider's Max steps per message leaves the llm block and
    # becomes the loop cap each runtime reads: max_iterations on native,
    # the primary agent's steps on OpenCode (its boot projection maps it).
    for runtime_type in ("native", "opencode"):
        projected = build_runtime_profile_context_config(
            {"llm": {"provider": "github_copilot", "model": "gpt-6-luna", "max_steps": 60}},
            runtime_type=runtime_type,
        )
        assert "max_steps" not in projected["llm"], runtime_type
        assert projected["max_iterations"] == 60, runtime_type

    # No limit stored: nothing projected, so each runtime keeps its own default.
    projected = build_runtime_profile_context_config(
        {"llm": {"provider": "github_copilot", "model": "gpt-6-luna"}}, runtime_type="native"
    )
    assert "max_iterations" not in projected
