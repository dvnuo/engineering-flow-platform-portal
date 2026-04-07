import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.capability_profile import CapabilityProfile
from app.repositories.capability_profile_repo import CapabilityProfileRepository
from app.repositories.runtime_capability_catalog_snapshot_repo import RuntimeCapabilityCatalogSnapshotRepository
from app.schemas.capability_profile import CapabilityProfileResolvedData
from app.services.runtime_capability_catalog import (
    RuntimeCapabilityCatalogProvider,
    build_runtime_capability_catalog_provider,
    build_runtime_capability_catalog_provider_from_settings,
)


@dataclass
class CapabilityProfileValidationError(Exception):
    detail: str


@dataclass
class SkillAllowanceDetail:
    allowed: bool
    reason: str
    normalized_skill_name: str


class CapabilityContextService:
    JSON_FIELDS = (
        "tool_set_json",
        "channel_set_json",
        "skill_set_json",
        "allowed_external_systems_json",
        "allowed_webhook_triggers_json",
        "allowed_actions_json",
    )

    def __init__(
        self,
        runtime_capability_provider: RuntimeCapabilityCatalogProvider | None = None,
        runtime_catalog_snapshot_payload: list[dict] | None = None,
    ) -> None:
        # Provider precedence is explicit:
        # 1) explicit provider injection
        # 2) explicit runtime snapshot payload
        # 3) settings-backed loader path (runtime snapshot JSON with seed fallback)
        if runtime_capability_provider:
            self.runtime_capability_provider = runtime_capability_provider
        elif runtime_catalog_snapshot_payload is not None:
            self.runtime_capability_provider = build_runtime_capability_catalog_provider(
                runtime_catalog_snapshot_payload=runtime_catalog_snapshot_payload
            )
        else:
            self.runtime_capability_provider = build_runtime_capability_catalog_provider_from_settings()
        self.catalog_validation_mode = "full_snapshot" if self.runtime_capability_provider.has_full_catalog() else "seed_fallback"

    def _provider_for_db(self, db: Session | None, agent_id: str | None = None) -> RuntimeCapabilityCatalogProvider:
        if not db:
            return self.runtime_capability_provider
        repo = RuntimeCapabilityCatalogSnapshotRepository(db)
        latest = repo.get_latest_for_agent(agent_id) if agent_id else repo.get_latest()
        if not latest:
            return self.runtime_capability_provider
        try:
            payload = json.loads(latest.payload_json)
        except Exception:
            return self.runtime_capability_provider
        return RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(payload, source=latest.catalog_source or "runtime_api")

    @staticmethod
    def _parse_string_list(raw_value: str | None, field_name: str) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str) and not raw_value.strip():
            return []

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CapabilityProfileValidationError(detail=f"{field_name} must be valid JSON") from exc

        if not isinstance(parsed, list):
            raise CapabilityProfileValidationError(detail=f"{field_name} must decode to a JSON array")

        invalid_items = [item for item in parsed if not isinstance(item, str)]
        if invalid_items:
            raise CapabilityProfileValidationError(detail=f"{field_name} must contain only string values")

        return [item.strip() for item in parsed if item.strip()]

    @staticmethod
    def _normalize_name(value: str | None) -> str:
        return (value or "").strip().lower()

    def _normalize_tool_capability_id(self, name: str, provider: RuntimeCapabilityCatalogProvider | None = None) -> str | None:
        provider = provider or self.runtime_capability_provider
        resolved = provider.resolve_tool_name_to_capability_id(name)
        if resolved:
            return resolved
        if provider.get_catalog_source() == "seed_fallback":
            normalized = self._normalize_name(name)
            return f"tool:{normalized}" if normalized else None
        return None

    def _normalize_skill_capability_id(self, name: str, provider: RuntimeCapabilityCatalogProvider | None = None) -> str | None:
        provider = provider or self.runtime_capability_provider
        resolved = provider.resolve_skill_name_to_capability_id(name)
        if resolved:
            return resolved
        if provider.get_catalog_source() == "seed_fallback":
            normalized = self._normalize_name(name)
            return f"skill:{normalized}" if normalized else None
        return None

    def _normalize_channel_capability_id(self, name: str, provider: RuntimeCapabilityCatalogProvider | None = None) -> str | None:
        provider = provider or self.runtime_capability_provider
        resolved = provider.resolve_channel_name_to_capability_id(name)
        if resolved:
            return resolved
        if provider.get_catalog_source() == "seed_fallback":
            normalized = self._normalize_name(name)
            return f"channel_action:{normalized}" if normalized else None
        return None

    def _normalize_action_capability_id(self, name: str, provider: RuntimeCapabilityCatalogProvider | None = None) -> str | None:
        return (provider or self.runtime_capability_provider).resolve_action_to_capability_id(name)

    def validate_profile_payload(self, payload: dict, db: Session | None = None) -> None:
        provider = self._provider_for_db(db)
        parsed_map: dict[str, list[str]] = {}
        for field_name in self.JSON_FIELDS:
            if field_name in payload:
                parsed_map[field_name] = self._parse_string_list(payload.get(field_name), field_name)

        self._validate_allowed_actions(parsed_map.get("allowed_actions_json", []), provider)
        if provider.has_full_catalog():
            self._validate_tool_set(parsed_map.get("tool_set_json", []), provider)
            self._validate_skill_set(parsed_map.get("skill_set_json", []), provider)
            self._validate_channel_set(parsed_map.get("channel_set_json", []), provider)

    def _validate_allowed_actions(self, actions: list[str], provider: RuntimeCapabilityCatalogProvider) -> None:
        seen_action_ids: set[str] = set()
        for action_name in actions:
            normalized_name = self._normalize_name(action_name)
            if not normalized_name:
                raise CapabilityProfileValidationError(detail="allowed_actions_json must not contain blank action names")

            normalized_action_id = self._normalize_action_capability_id(action_name, provider)
            if not normalized_action_id:
                raise CapabilityProfileValidationError(
                    detail=f"allowed_actions_json contains unknown or ambiguous action: {action_name}"
                )
            if normalized_action_id in seen_action_ids:
                raise CapabilityProfileValidationError(
                    detail=f"allowed_actions_json contains duplicate logical action: {action_name}"
                )
            seen_action_ids.add(normalized_action_id)

    def _validate_tool_set(self, tools: list[str], provider: RuntimeCapabilityCatalogProvider) -> None:
        for tool_name in tools:
            if not self._normalize_tool_capability_id(tool_name, provider):
                raise CapabilityProfileValidationError(detail=f"tool_set_json contains unknown tool: {tool_name}")

    def _validate_skill_set(self, skills: list[str], provider: RuntimeCapabilityCatalogProvider) -> None:
        for skill_name in skills:
            if not self._normalize_skill_capability_id(skill_name, provider):
                raise CapabilityProfileValidationError(detail=f"skill_set_json contains unknown skill: {skill_name}")

    def _validate_channel_set(self, channels: list[str], provider: RuntimeCapabilityCatalogProvider) -> None:
        for channel_name in channels:
            if not self._normalize_channel_capability_id(channel_name, provider):
                raise CapabilityProfileValidationError(detail=f"channel_set_json contains unknown channel_action: {channel_name}")

    def resolve_profile(self, profile: CapabilityProfile | None) -> CapabilityProfileResolvedData:
        if not profile:
            return CapabilityProfileResolvedData()

        return CapabilityProfileResolvedData(
            tool_set=self._parse_string_list(profile.tool_set_json, "tool_set_json"),
            channel_set=self._parse_string_list(profile.channel_set_json, "channel_set_json"),
            skill_set=self._parse_string_list(profile.skill_set_json, "skill_set_json"),
            allowed_external_systems=self._parse_string_list(profile.allowed_external_systems_json, "allowed_external_systems_json"),
            allowed_webhook_triggers=self._parse_string_list(profile.allowed_webhook_triggers_json, "allowed_webhook_triggers_json"),
            allowed_actions=self._parse_string_list(profile.allowed_actions_json, "allowed_actions_json"),
        )

    def resolve_for_agent(self, db: Session, agent: Agent | None) -> tuple[str | None, CapabilityProfileResolvedData]:
        if not agent or not agent.capability_profile_id:
            return None, CapabilityProfileResolvedData()

        profile = CapabilityProfileRepository(db).get_by_id(agent.capability_profile_id)
        return agent.capability_profile_id, self.resolve_profile(profile)

    def build_runtime_capability_context(
        self,
        capability_profile_id: str | None,
        resolved: CapabilityProfileResolvedData,
        db: Session | None = None,
        agent_id: str | None = None,
    ) -> dict:
        provider = self._provider_for_db(db, agent_id=agent_id)
        allowed_capability_ids: list[str] = []
        allowed_capability_types: list[str] = []
        allowed_adapter_actions: list[str] = []
        unresolved_tools: list[str] = []
        unresolved_skills: list[str] = []
        unresolved_channels: list[str] = []
        unresolved_actions: list[str] = []
        resolved_action_mappings: dict[str, str] = {}

        for tool_name in resolved.tool_set:
            normalized_id = self._normalize_tool_capability_id(tool_name, provider)
            if not normalized_id:
                unresolved_tools.append(tool_name)
            elif normalized_id not in allowed_capability_ids:
                allowed_capability_ids.append(normalized_id)
        if resolved.tool_set:
            allowed_capability_types.append("tool")

        for skill_name in resolved.skill_set:
            normalized_id = self._normalize_skill_capability_id(skill_name, provider)
            if not normalized_id:
                unresolved_skills.append(skill_name)
            elif normalized_id not in allowed_capability_ids:
                allowed_capability_ids.append(normalized_id)
        if resolved.skill_set:
            allowed_capability_types.append("skill")

        for channel_name in resolved.channel_set:
            normalized_id = self._normalize_channel_capability_id(channel_name, provider)
            if not normalized_id:
                unresolved_channels.append(channel_name)
            elif normalized_id not in allowed_capability_ids:
                allowed_capability_ids.append(normalized_id)
        if resolved.channel_set:
            allowed_capability_types.append("channel_action")

        for action_name in resolved.allowed_actions:
            normalized_action_id = self._normalize_action_capability_id(action_name, provider)
            if not normalized_action_id:
                unresolved_actions.append(action_name)
                continue
            if normalized_action_id not in allowed_capability_ids:
                allowed_capability_ids.append(normalized_action_id)
            if normalized_action_id not in allowed_adapter_actions:
                allowed_adapter_actions.append(normalized_action_id)
            resolved_action_mappings[action_name] = normalized_action_id
        if allowed_adapter_actions:
            allowed_capability_types.append("adapter_action")

        return {
            "capability_profile_id": capability_profile_id,
            "tool_set": resolved.tool_set,
            "channel_set": resolved.channel_set,
            "skill_set": resolved.skill_set,
            "allowed_external_systems": resolved.allowed_external_systems,
            "allowed_webhook_triggers": resolved.allowed_webhook_triggers,
            "allowed_actions": resolved.allowed_actions,
            "allowed_capability_ids": allowed_capability_ids,
            "allowed_capability_types": allowed_capability_types,
            "allowed_adapter_actions": allowed_adapter_actions,
            "unresolved_tools": unresolved_tools,
            "unresolved_skills": unresolved_skills,
            "unresolved_channels": unresolved_channels,
            "unresolved_actions": unresolved_actions,
            "resolved_action_mappings": resolved_action_mappings,
            "runtime_capability_catalog_version": provider.get_catalog_version(),
            "runtime_capability_catalog_source": provider.get_catalog_source(),
            "catalog_validation_mode": "full_snapshot" if provider.has_full_catalog() else "seed_fallback",
        }

    def get_skill_allowance_detail(self, db: Session, agent: Agent | None, skill_name: str | None) -> SkillAllowanceDetail:
        normalized_skill_name = self._normalize_name(skill_name)
        if not normalized_skill_name:
            return SkillAllowanceDetail(allowed=True, reason="allowed", normalized_skill_name=normalized_skill_name)

        profile_id, resolved = self.resolve_for_agent(db, agent)
        if not profile_id:
            return SkillAllowanceDetail(allowed=True, reason="no_profile", normalized_skill_name=normalized_skill_name)
        if not resolved.skill_set:
            return SkillAllowanceDetail(allowed=False, reason="empty_skill_set", normalized_skill_name=normalized_skill_name)

        normalized_skill_set = {self._normalize_name(item) for item in resolved.skill_set}
        if normalized_skill_name in normalized_skill_set:
            return SkillAllowanceDetail(allowed=True, reason="allowed", normalized_skill_name=normalized_skill_name)
        return SkillAllowanceDetail(allowed=False, reason="skill_not_allowed", normalized_skill_name=normalized_skill_name)

    def is_skill_allowed(self, db: Session, agent: Agent | None, skill_name: str | None) -> bool:
        return self.get_skill_allowance_detail(db, agent, skill_name).allowed
