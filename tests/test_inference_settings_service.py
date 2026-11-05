import json
from types import SimpleNamespace

import pytest

import app.services.inference_settings_service as inference_module
from app.contracts.llm_catalog import register_catalog_models
from app.services import model_catalog_service as catalog_service
from app.services.inference_settings_service import (
    apply_inference_overrides_to_runtime_metadata,
    normalize_agent_inference_overrides,
    resolve_agent_inference_profile,
)


@pytest.fixture(autouse=True)
def _no_catalog():
    catalog_service.reset_catalog_state()
    yield
    catalog_service.reset_catalog_state()


def _install_profile(monkeypatch, *, runtime_type="native"):
    profile = SimpleNamespace(
        id="profile-1",
        revision=7,
        config_json=json.dumps(
            {
                "llm": {
                    "provider": "github_copilot",
                    "model": "gpt-6-luna",
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
    assert resolved.current_model == "gpt-6-luna"
    assert resolved.current_reasoning_effort == "medium"
    assert resolved.current_max_context_tokens == 256_000
    assert resolved.supports_reasoning_effort is True
    assert resolved.supports_context_size is True
    assert resolved.context_sizes == (64_000, 128_000, 256_000, 1_000_000)


def test_normalize_and_project_native_request_overrides(monkeypatch):
    agent = _install_profile(monkeypatch)
    overrides = normalize_agent_inference_overrides(
        object(),
        agent,
        {
            "model_override": "gpt-6.1-sol",
            "reasoning_effort": "MAX",
            "max_context_tokens": 256_000,
        },
    )

    assert overrides == {
        "model": "gpt-6.1-sol",
        "reasoning_effort": "max",
        "max_context_tokens": 256_000,
    }

    metadata = apply_inference_overrides_to_runtime_metadata(
        {"provider": "github_copilot", "runtime_profile": {"config": {"llm": {"provider": "github_copilot"}}}},
        overrides,
        runtime_type="native",
        provider="github_copilot",
    )
    assert metadata["model"] == "gpt-6.1-sol"
    assert metadata["reasoning_effort"] == "max"
    assert metadata["max_context_tokens"] == 256_000
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "gpt-6.1-sol"
    assert metadata["runtime_profile"]["config"]["llm"]["reasoning_effort"] == "max"
    assert metadata["runtime_profile"]["config"]["max_context_tokens"] == 256_000


def test_opencode_rejects_context_override_but_projects_model_and_reasoning(monkeypatch):
    agent = _install_profile(monkeypatch, runtime_type="opencode")

    with pytest.raises(ValueError, match="not supported"):
        normalize_agent_inference_overrides(object(), agent, {"max_context_tokens": 64_000})

    overrides = normalize_agent_inference_overrides(
        object(),
        agent,
        {"model_override": "gpt-6-astra", "reasoning_effort": "low"},
    )
    metadata = apply_inference_overrides_to_runtime_metadata(
        {},
        overrides,
        runtime_type="opencode",
        provider="github_copilot",
    )
    assert metadata["model"] == "github-copilot/gpt-6-astra"
    assert metadata["reasoning_effort"] == "low"
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "github-copilot/gpt-6-astra"


def test_rejects_unknown_model_and_context_outside_supported_presets(monkeypatch):
    agent = _install_profile(monkeypatch)

    # A malformed id and a retired line are refused; a plausible id the
    # member's runtime catalog may list (GET /api/models) is passed through,
    # the runtime being the one that knows what Copilot serves.
    with pytest.raises(ValueError, match="not allowed"):
        normalize_agent_inference_overrides(object(), agent, {"model_override": "gpt unknown"})
    with pytest.raises(ValueError, match="not allowed"):
        normalize_agent_inference_overrides(object(), agent, {"model_override": "gpt-5.4"})
    assert normalize_agent_inference_overrides(object(), agent, {"model_override": "gpt-6.2-nova"}) == {"model": "gpt-6.2-nova"}
    with pytest.raises(ValueError, match="must be one of"):
        normalize_agent_inference_overrides(
            object(),
            agent,
            {"model_override": "gpt-6-astra", "max_context_tokens": 400_000},
        )

    normalized = normalize_agent_inference_overrides(
        object(),
        agent,
        {"model_override": "gpt-6-astra", "max_context_tokens": 1_000_000},
    )
    assert normalized["max_context_tokens"] == 1_000_000


def test_context_sizes_follow_the_models_catalog_window(monkeypatch):
    # The catalog says Luna takes 128k, MAI 256k, Codex 400k; Sol's 1.05M
    # covers the ladder. The profile stores 256k for Luna.
    register_catalog_models(
        ("gpt-6.1-sol", "gpt-6-luna", "mai-code-1.1-flash", "gpt-5.3-codex"),
        {"gpt-6.1-sol": 1_050_000, "gpt-6-luna": 128_000, "mai-code-1.1-flash": 256_000, "gpt-5.3-codex": 400_000},
    )
    agent = _install_profile(monkeypatch)

    resolved = resolve_agent_inference_profile(object(), agent)
    # The presets stop at the model's window, and a stored size above it
    # means the window (the runtime clamps to it anyway).
    assert resolved.context_sizes == (64_000, 128_000)
    assert resolved.current_max_context_tokens == 128_000

    # Overrides are validated against the chosen model's own list: the
    # window closes it, so Codex offers 400k and refuses 1M.
    assert normalize_agent_inference_overrides(
        object(), agent, {"model_override": "gpt-5.3-codex", "max_context_tokens": 400_000}
    ) == {"model": "gpt-5.3-codex", "max_context_tokens": 400_000}
    with pytest.raises(ValueError, match="must be one of: 64000, 128000, 256000, 400000$"):
        normalize_agent_inference_overrides(object(), agent, {"model_override": "gpt-5.3-codex", "max_context_tokens": 1_000_000})
    with pytest.raises(ValueError, match="must be one of: 64000, 128000, 256000$"):
        normalize_agent_inference_overrides(object(), agent, {"model_override": "mai-code-1.1-flash", "max_context_tokens": 1_000_000})
    assert normalize_agent_inference_overrides(
        object(), agent, {"model_override": "gpt-6.1-sol", "max_context_tokens": 1_000_000}
    )["max_context_tokens"] == 1_000_000
    # A model the catalog lists without a window keeps the whole ladder.
    register_catalog_models(("gpt-6-luna", "gpt-9-zeta"), {"gpt-6-luna": 128_000})
    assert normalize_agent_inference_overrides(
        object(), agent, {"model_override": "gpt-9-zeta", "max_context_tokens": 1_000_000}
    )["max_context_tokens"] == 1_000_000


def test_thinking_levels_follow_the_models_catalog_row(monkeypatch):
    # Copilot refused xhigh for MAI: "Supported values are: minimal, low,
    # medium, and high." The picker offers the levels the row lists that the
    # runtimes send; a stored or requested level the model lacks reads as the
    # nearest level it takes.
    register_catalog_models(
        ("gpt-6.1-sol", "gpt-6-luna", "mai-code-1.1-flash"),
        {},
        {"gpt-6-luna": ["low", "medium", "high"], "mai-code-1.1-flash": ["minimal", "low", "medium", "high"]},
    )
    agent = _install_profile(monkeypatch)  # Luna, stored medium

    resolved = resolve_agent_inference_profile(object(), agent)
    assert resolved.reasoning_efforts == ("low", "medium", "high")
    assert resolved.current_reasoning_effort == "medium"

    assert normalize_agent_inference_overrides(object(), agent, {"reasoning_effort": "high"}) == {"reasoning_effort": "high"}
    with pytest.raises(ValueError, match="reasoning_effort must be one of: low, medium, high$"):
        normalize_agent_inference_overrides(object(), agent, {"reasoning_effort": "xhigh"})
    with pytest.raises(ValueError, match="must be one of: low, medium, high$"):
        normalize_agent_inference_overrides(object(), agent, {"model_override": "mai-code-1.1-flash", "reasoning_effort": "max"})
    # A model without a list keeps all five.
    assert normalize_agent_inference_overrides(
        object(), agent, {"model_override": "gpt-6.1-sol", "reasoning_effort": "max"}
    ) == {"model": "gpt-6.1-sol", "reasoning_effort": "max"}

    from app.contracts.llm_catalog import effective_reasoning_effort, reasoning_effort_options

    assert reasoning_effort_options("github_copilot", "gpt-6.1-sol") == ("low", "medium", "high", "xhigh", "max")
    assert effective_reasoning_effort("github_copilot", "gpt-6-luna", "max") == "high"
    assert effective_reasoning_effort("github_copilot", "gpt-6-luna", "nonsense") == "medium"
    register_catalog_models(("tiny",), {}, {"tiny": ["xhigh", "max"]})
    assert effective_reasoning_effort("github_copilot", "tiny", "low") == "xhigh"
    assert effective_reasoning_effort("github_copilot", "tiny", None) == "xhigh"
    # A row listing only levels the runtimes do not send offers all five.
    register_catalog_models(("odd",), {}, {"odd": ["minimal", "none"]})
    assert reasoning_effort_options("github_copilot", "odd") == ("low", "medium", "high", "xhigh", "max")
