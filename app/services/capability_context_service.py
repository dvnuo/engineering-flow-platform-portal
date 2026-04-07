import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.capability_profile import CapabilityProfile
from app.repositories.capability_profile_repo import CapabilityProfileRepository
from app.schemas.capability_profile import CapabilityProfileResolvedData


@dataclass
class CapabilityProfileValidationError(Exception):
    detail: str


class CapabilityContextService:
    JSON_FIELDS = (
        "tool_set_json",
        "channel_set_json",
        "skill_set_json",
        "allowed_external_systems_json",
        "allowed_webhook_triggers_json",
        "allowed_actions_json",
    )

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

    def validate_profile_payload(self, payload: dict) -> None:
        for field_name in self.JSON_FIELDS:
            if field_name in payload:
                self._parse_string_list(payload.get(field_name), field_name)

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

    @staticmethod
    def build_runtime_capability_context(capability_profile_id: str | None, resolved: CapabilityProfileResolvedData) -> dict:
        allowed_capability_ids = list(
            dict.fromkeys(resolved.tool_set + resolved.channel_set + resolved.skill_set + resolved.allowed_actions)
        )

        allowed_capability_types: list[str] = []
        if resolved.tool_set:
            allowed_capability_types.append("tool")
        if resolved.channel_set:
            allowed_capability_types.append("channel")
        if resolved.skill_set:
            allowed_capability_types.append("skill")
        if resolved.allowed_actions:
            allowed_capability_types.append("action")

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
        }

    def is_skill_allowed(self, db: Session, agent: Agent | None, skill_name: str | None) -> bool:
        if not skill_name:
            return True

        profile_id, resolved = self.resolve_for_agent(db, agent)
        if not profile_id:
            return True
        if not resolved.skill_set:
            return False
        return skill_name in resolved.skill_set
