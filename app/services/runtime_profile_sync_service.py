from sqlalchemy.orm import Session

from app.contracts.runtime_type import normalize_runtime_type_or_default
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.runtime_profile_context_projection import (
    build_runtime_profile_context_config,
)


class RuntimeProfileSyncService:
    """Builds runtime-profile apply payloads.

    Config distribution happens exclusively through per-profile Secrets
    (see runtime_profile_secret_service); this class only renders payloads.
    """

    @staticmethod
    def build_apply_payload_from_profile(runtime_profile) -> dict:
        parsed_config = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        parsed_config = build_runtime_profile_context_config(parsed_config)
        return {
            "runtime_profile_id": runtime_profile.id,
            "name": getattr(runtime_profile, "name", "") or "",
            "revision": runtime_profile.revision,
            "config": parsed_config,
        }

    @staticmethod
    def build_clear_payload() -> dict:
        return {"runtime_profile_id": None, "revision": None, "config": {}}

    def build_clear_payload_for_agent(self, _db: Session, _agent) -> dict:
        return self.build_clear_payload()

    def build_apply_payload_for_agent(self, db: Session, agent, runtime_profile) -> dict:
        _ = db
        payload = self.build_apply_payload_from_profile(runtime_profile)
        runtime_type = normalize_runtime_type_or_default(getattr(agent, "runtime_type", None))
        payload["runtime_type"] = runtime_type
        payload["agent_id"] = getattr(agent, "id", None)
        payload["config"] = build_runtime_profile_context_config(
            payload.get("config"),
            runtime_type=runtime_type,
        )
        return payload
