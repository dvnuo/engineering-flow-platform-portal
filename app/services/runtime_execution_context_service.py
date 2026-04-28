import json

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.repositories.policy_profile_repo import PolicyProfileRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import (
    parse_runtime_profile_config_json,
    sanitize_runtime_profile_tool_loop,
)
from app.services.capability_context_service import CapabilityContextService
from app.services.runtime_profile_service import RuntimeProfileService


class RuntimeExecutionContextService:
    def __init__(self) -> None:
        self.capability_context_service = CapabilityContextService()

    @staticmethod
    def _parse_json_object(raw: str | None) -> dict:
        if raw is None or not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _as_string_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized:
                result.append(normalized)
        return result

    def _build_policy_context(self, db: Session, agent: Agent | None) -> tuple[str | None, dict]:
        policy_profile_id = agent.policy_profile_id if agent else None
        profile = PolicyProfileRepository(db).get_by_id(policy_profile_id) if policy_profile_id else None

        auto_run_rules = self._parse_json_object(getattr(profile, "auto_run_rules_json", None))
        permission_rules = self._parse_json_object(getattr(profile, "permission_rules_json", None))
        audit_rules = self._parse_json_object(getattr(profile, "audit_rules_json", None))
        transition_rules = self._parse_json_object(getattr(profile, "transition_rules_json", None))
        escalation_rules = self._parse_json_object(getattr(profile, "escalation_rules_json", None))

        derived_runtime_rules: dict = {}

        if isinstance(auto_run_rules.get("require_explicit_allow"), bool):
            derived_runtime_rules["governance_require_explicit_allow"] = auto_run_rules.get("require_explicit_allow")
        if isinstance(auto_run_rules.get("allow_auto_run"), bool):
            derived_runtime_rules["governance_allow_auto_run"] = auto_run_rules.get("allow_auto_run")

        external_allowlist = self._as_string_list(transition_rules.get("external_trigger_allowlist"))
        if external_allowlist:
            derived_runtime_rules["governance_external_allowlist"] = external_allowlist
        external_blocklist = self._as_string_list(transition_rules.get("external_trigger_blocklist"))
        if external_blocklist:
            derived_runtime_rules["governance_external_blocklist"] = external_blocklist

        denied_capability_ids = self._as_string_list(permission_rules.get("denied_capability_ids"))
        if denied_capability_ids:
            derived_runtime_rules["denied_capability_ids"] = denied_capability_ids

        denied_capability_types = self._as_string_list(permission_rules.get("denied_capability_types"))
        if denied_capability_types:
            derived_runtime_rules["denied_capability_types"] = denied_capability_types

        denied_adapter_actions = self._as_string_list(permission_rules.get("denied_adapter_actions"))
        if not denied_adapter_actions:
            denied_adapter_actions = self._as_string_list(permission_rules.get("denied_actions"))
        if denied_adapter_actions:
            derived_runtime_rules["denied_adapter_actions"] = denied_adapter_actions

        return policy_profile_id, {
            "policy_profile_id": policy_profile_id,
            "auto_run_rules": auto_run_rules,
            "permission_rules": permission_rules,
            "audit_rules": audit_rules,
            "transition_rules": transition_rules,
            "max_parallel_tasks": getattr(profile, "max_parallel_tasks", None) if profile else None,
            "escalation_rules": escalation_rules,
            "derived_runtime_rules": derived_runtime_rules,
        }


    def _build_runtime_profile_context(self, db: Session, agent: Agent | None) -> tuple[str | None, dict]:
        runtime_profile_id = getattr(agent, "runtime_profile_id", None) if agent else None
        if not runtime_profile_id:
            return None, {}

        profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
        if not profile:
            return runtime_profile_id, {}

        try:
            parsed_config = parse_runtime_profile_config_json(
                getattr(profile, "config_json", None),
                fallback_to_empty=True,
            )
        except TypeError:
            parsed_config = parse_runtime_profile_config_json(getattr(profile, "config_json", None))
        except ValueError:
            return runtime_profile_id, {}

        materialized_config = RuntimeProfileService.merge_with_managed_defaults(parsed_config)
        llm = materialized_config.get("llm") if isinstance(materialized_config, dict) else {}
        raw_tool_loop = llm.get("tool_loop") if isinstance(llm, dict) else {}

        try:
            tool_loop = sanitize_runtime_profile_tool_loop(raw_tool_loop)
        except ValueError:
            return runtime_profile_id, {}

        return runtime_profile_id, dict(tool_loop)

    def build_for_agent(self, db: Session, agent: Agent | None) -> dict:
        capability_profile_id, resolved_profile = self.capability_context_service.resolve_for_agent(db, agent)
        capability_context = self.capability_context_service.build_runtime_capability_context(
            capability_profile_id=capability_profile_id,
            resolved=resolved_profile,
            db=db,
            agent_id=agent.id if agent else None,
        )

        policy_profile_id, policy_context = self._build_policy_context(db, agent)
        runtime_profile_id, runtime_profile_context = self._build_runtime_profile_context(db, agent)

        return {
            "capability_profile_id": capability_profile_id,
            "policy_profile_id": policy_profile_id,
            "capability_context": capability_context,
            "policy_context": policy_context,
            "runtime_profile_id": runtime_profile_id,
            "runtime_profile_context": runtime_profile_context,
        }

    def build_runtime_metadata(self, db: Session, agent: Agent | None, base_metadata: dict | None = None) -> dict:
        metadata = dict(base_metadata or {})
        context = self.build_for_agent(db, agent)
        capability_context = context["capability_context"]
        policy_context = context["policy_context"]

        metadata["capability_profile_id"] = context["capability_profile_id"]
        metadata["policy_profile_id"] = context["policy_profile_id"]
        metadata["runtime_profile_id"] = context.get("runtime_profile_id")
        runtime_profile_context = context.get("runtime_profile_context") or {}
        if isinstance(runtime_profile_context, dict) and runtime_profile_context:
            metadata["llm_tool_loop"] = runtime_profile_context

        metadata["allowed_capability_ids"] = capability_context.get("allowed_capability_ids", [])
        metadata["allowed_capability_types"] = capability_context.get("allowed_capability_types", [])
        metadata["allowed_external_systems"] = capability_context.get("allowed_external_systems", [])
        metadata["allowed_webhook_triggers"] = capability_context.get("allowed_webhook_triggers", [])
        metadata["allowed_actions"] = capability_context.get("allowed_actions", [])
        metadata["allowed_adapter_actions"] = capability_context.get("allowed_adapter_actions", [])
        metadata["unresolved_tools"] = capability_context.get("unresolved_tools", [])
        metadata["unresolved_skills"] = capability_context.get("unresolved_skills", [])
        metadata["unresolved_channels"] = capability_context.get("unresolved_channels", [])
        metadata["unresolved_actions"] = capability_context.get("unresolved_actions", [])
        metadata["resolved_action_mappings"] = capability_context.get("resolved_action_mappings", {})
        metadata["runtime_capability_catalog_version"] = capability_context.get("runtime_capability_catalog_version")
        metadata["runtime_capability_catalog_source"] = capability_context.get("runtime_capability_catalog_source")
        metadata["catalog_validation_mode"] = capability_context.get("catalog_validation_mode")

        metadata["policy_context"] = policy_context
        metadata.update(policy_context.get("derived_runtime_rules") or {})
        return metadata
