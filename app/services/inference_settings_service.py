from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.contracts.llm_catalog import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_REASONING_EFFORT,
    SUPPORTED_REASONING_EFFORTS,
    context_size_options,
    default_model_for_provider,
    model_context_window,
)
from app.contracts.provider_projection import normalize_model_for_runtime
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.runtime_profile_service import RuntimeProfileService


@dataclass(frozen=True)
class AgentInferenceProfile:
    runtime_profile_id: str = ""
    revision: int | None = None
    runtime_type: str = "native"
    provider: str = ""
    current_model: str = ""
    current_reasoning_effort: str = DEFAULT_REASONING_EFFORT
    current_max_context_tokens: int | None = None

    @property
    def available_models(self) -> tuple[str, ...]:
        return RuntimeProfileService.managed_model_values_for_provider(self.provider)

    @property
    def supports_reasoning_effort(self) -> bool:
        return bool(self.provider)

    @property
    def supports_context_size(self) -> bool:
        return bool(self.provider) and self.runtime_type != "opencode"

    @property
    def context_sizes(self) -> tuple[int, ...]:
        model = self.current_model or default_model_for_provider(self.provider)
        return context_size_options(self.provider, model)


def resolve_agent_inference_profile(db: Session, agent) -> AgentInferenceProfile:
    runtime_type = str(getattr(agent, "runtime_type", None) or "native").strip().lower() or "native"
    runtime_profile_id = str(getattr(agent, "runtime_profile_id", None) or "").strip()
    if not runtime_profile_id:
        return AgentInferenceProfile(runtime_type=runtime_type)

    profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
    if not profile:
        return AgentInferenceProfile(runtime_type=runtime_type)

    parsed = parse_runtime_profile_config_json(getattr(profile, "config_json", None), fallback_to_empty=True)
    llm = parsed.get("llm") if isinstance(parsed, dict) else {}
    if not isinstance(llm, dict):
        llm = {}
    provider = RuntimeProfileService.normalize_managed_llm_provider(llm.get("provider"))
    current_model = str(llm.get("model") or "").strip()
    reasoning_effort = str(llm.get("reasoning_effort") or DEFAULT_REASONING_EFFORT).strip().lower()
    if reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        reasoning_effort = DEFAULT_REASONING_EFFORT
    raw_context = llm.get("max_context_tokens")
    current_context = None
    if (
        isinstance(raw_context, int)
        and not isinstance(raw_context, bool)
        and raw_context in context_size_options(provider, current_model)
    ):
        current_context = raw_context
    elif provider and runtime_type != "opencode":
        current_context = DEFAULT_CONTEXT_SIZE
    return AgentInferenceProfile(
        runtime_profile_id=runtime_profile_id,
        revision=getattr(profile, "revision", None),
        runtime_type=runtime_type,
        provider=provider,
        current_model=current_model,
        current_reasoning_effort=reasoning_effort,
        current_max_context_tokens=current_context,
    )


def normalize_agent_inference_overrides(
    db: Session,
    agent,
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(value or {})
    profile = resolve_agent_inference_profile(db, agent)
    normalized: dict[str, Any] = {}

    model_value = raw.get("model") if "model" in raw else raw.get("model_override")
    if model_value is not None:
        if not isinstance(model_value, str):
            raise ValueError("model_override must be a string")
        model = model_value.strip()
        if model:
            if not RuntimeProfileService.is_managed_model_allowed(profile.provider, model):
                raise ValueError("model_override is not allowed for the agent's current runtime profile provider")
            normalized["model"] = model

    reasoning_value = raw.get("reasoning_effort")
    if reasoning_value is not None:
        if not isinstance(reasoning_value, str):
            raise ValueError("reasoning_effort must be a string")
        reasoning_effort = reasoning_value.strip().lower()
        if reasoning_effort:
            if not profile.supports_reasoning_effort:
                raise ValueError("reasoning_effort is not supported by the agent's current runtime profile")
            if reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
                supported = ", ".join(SUPPORTED_REASONING_EFFORTS)
                raise ValueError(f"reasoning_effort must be one of: {supported}")
            normalized["reasoning_effort"] = reasoning_effort

    context_value = raw.get("max_context_tokens")
    if context_value is not None and context_value != "":
        if isinstance(context_value, bool) or not isinstance(context_value, int):
            raise ValueError("max_context_tokens must be an integer")
        if not profile.supports_context_size:
            raise ValueError("max_context_tokens is not supported by this agent runtime")
        selected_model = normalized.get("model") or profile.current_model or default_model_for_provider(profile.provider)
        supported_context_sizes = context_size_options(profile.provider, selected_model)
        if context_value not in supported_context_sizes:
            supported = ", ".join(str(value) for value in supported_context_sizes)
            raise ValueError(f"max_context_tokens must be one of: {supported}")
        max_window = model_context_window(profile.provider, selected_model)
        if max_window is not None and context_value > max_window:
            raise ValueError(f"max_context_tokens cannot exceed {max_window} for {selected_model}")
        normalized["max_context_tokens"] = context_value

    return normalized


def apply_inference_overrides_to_runtime_metadata(
    metadata: Mapping[str, Any] | None,
    overrides: Mapping[str, Any] | None,
    *,
    runtime_type: str,
    provider: str | None,
) -> dict[str, Any]:
    projected = deepcopy(dict(metadata or {}))
    inference = dict(overrides or {})
    if not inference:
        return projected

    runtime_profile = projected.get("runtime_profile")
    runtime_profile = deepcopy(runtime_profile) if isinstance(runtime_profile, dict) else {}
    config = runtime_profile.get("config")
    config = deepcopy(config) if isinstance(config, dict) else {}
    llm = config.get("llm")
    llm = deepcopy(llm) if isinstance(llm, dict) else {}

    model = str(inference.get("model") or "").strip()
    if model:
        runtime_model = normalize_model_for_runtime(runtime_type, provider, model) or model
        projected["model"] = runtime_model
        runtime_profile["model"] = runtime_model
        llm["model"] = runtime_model
        inference["model"] = runtime_model

    reasoning_effort = str(inference.get("reasoning_effort") or "").strip().lower()
    if reasoning_effort:
        llm["reasoning_effort"] = reasoning_effort
        projected["reasoning_effort"] = reasoning_effort

    max_context_tokens = inference.get("max_context_tokens")
    if isinstance(max_context_tokens, int) and not isinstance(max_context_tokens, bool):
        config["max_context_tokens"] = max_context_tokens
        projected["max_context_tokens"] = max_context_tokens

    if llm:
        config["llm"] = llm
    if config:
        runtime_profile["config"] = config
    if runtime_profile:
        projected["runtime_profile"] = runtime_profile
    projected["inference"] = inference
    return projected
