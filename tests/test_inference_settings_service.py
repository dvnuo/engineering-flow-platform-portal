import json
from types import SimpleNamespace

import pytest

import app.services.inference_settings_service as inference_module
from app.services.inference_settings_service import (
    apply_inference_overrides_to_runtime_metadata,
    normalize_agent_inference_overrides,
    resolve_agent_inference_profile,
)


def _install_profile(monkeypatch, *, runtime_type="native"):
    profile = SimpleNamespace(
        id="profile-1",
        revision=7,
        config_json=json.dumps(
            {
                "llm": {
                    "provider": "github_copilot",
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "medium",
                    "max_context_tokens": 256_000,
                }
            }
        ),
    )
    monkeypatch.setattr(
        inference_module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )
    return SimpleNamespace(runtime_profile_id=profile.id, runtime_type=runtime_type)


def test_resolve_agent_inference_profile_exposes_capabilities(monkeypatch):
    agent = _install_profile(monkeypatch)

    resolved = resolve_agent_inference_profile(object(), agent)

    assert resolved.runtime_profile_id == "profile-1"
    assert resolved.revision == 7
    assert resolved.provider == "github_copilot"
    assert resolved.current_model == "gpt-5.6-terra"
    assert resolved.current_reasoning_effort == "medium"
    assert resolved.current_max_context_tokens == 256_000
    assert resolved.supports_reasoning_effort is True
    assert resolved.supports_context_size is True
    assert resolved.context_sizes == (64_000, 256_000, 1_000_000)


def test_normalize_and_project_native_request_overrides(monkeypatch):
    agent = _install_profile(monkeypatch)
    overrides = normalize_agent_inference_overrides(
        object(),
        agent,
        {
            "model_override": "gpt-5.6-sol",
            "reasoning_effort": "MAX",
            "max_context_tokens": 256_000,
        },
    )

    assert overrides == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "max",
        "max_context_tokens": 256_000,
    }

    metadata = apply_inference_overrides_to_runtime_metadata(
        {"provider": "github_copilot", "runtime_profile": {"config": {"llm": {"provider": "github_copilot"}}}},
        overrides,
        runtime_type="native",
        provider="github_copilot",
    )
    assert metadata["model"] == "gpt-5.6-sol"
    assert metadata["reasoning_effort"] == "max"
    assert metadata["max_context_tokens"] == 256_000
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "gpt-5.6-sol"
    assert metadata["runtime_profile"]["config"]["llm"]["reasoning_effort"] == "max"
    assert metadata["runtime_profile"]["config"]["max_context_tokens"] == 256_000


def test_opencode_rejects_context_override_but_projects_model_and_reasoning(monkeypatch):
    agent = _install_profile(monkeypatch, runtime_type="opencode")

    with pytest.raises(ValueError, match="not supported"):
        normalize_agent_inference_overrides(object(), agent, {"max_context_tokens": 64_000})

    overrides = normalize_agent_inference_overrides(
        object(),
        agent,
        {"model_override": "gpt-5.6-luna", "reasoning_effort": "low"},
    )
    metadata = apply_inference_overrides_to_runtime_metadata(
        {},
        overrides,
        runtime_type="opencode",
        provider="github_copilot",
    )
    assert metadata["model"] == "github-copilot/gpt-5.6-luna"
    assert metadata["reasoning_effort"] == "low"
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "github-copilot/gpt-5.6-luna"


def test_rejects_unknown_model_and_context_outside_supported_presets(monkeypatch):
    agent = _install_profile(monkeypatch)

    with pytest.raises(ValueError, match="not allowed"):
        normalize_agent_inference_overrides(object(), agent, {"model_override": "gpt-unknown"})
    with pytest.raises(ValueError, match="must be one of"):
        normalize_agent_inference_overrides(
            object(),
            agent,
            {"model_override": "gpt-5.6-luna", "max_context_tokens": 400_000},
        )

    normalized = normalize_agent_inference_overrides(
        object(),
        agent,
        {"model_override": "gpt-5.6-luna", "max_context_tokens": 1_000_000},
    )
    assert normalized["max_context_tokens"] == 1_000_000
