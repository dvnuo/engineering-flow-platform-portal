import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.k8s_service import K8sService
from app.services.runtime_profile_context_projection import (
    build_canonical_profile_config,
)

logger = logging.getLogger(__name__)

NONE_SECRET_NAME = "efp-profile-none"
PROFILE_SECRET_CONFIG_KEY = "config.json"


def profile_secret_name(profile_id: str) -> str:
    return f"efp-profile-{profile_id}"


def render_profile_secret_data(profile) -> dict[str, str]:
    """Render the single runtime-agnostic canonical payload plus the revision.

    Each runtime applies its own projection (LLM provider/model form, opencode
    field stripping, native CLI tool instructions) to this config at boot.
    """
    parsed_config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    payload = {
        "runtime_profile_id": profile.id,
        "name": getattr(profile, "name", "") or "",
        "revision": profile.revision,
        "config": build_canonical_profile_config(parsed_config),
    }
    return {
        PROFILE_SECRET_CONFIG_KEY: json.dumps(payload),
        "revision": str(profile.revision or 0),
    }


def render_none_secret_data() -> dict[str, str]:
    """Shared empty payload for agents without a bound runtime profile."""
    payload = {
        "runtime_profile_id": None,
        "name": "",
        "revision": None,
        "config": {},
    }
    return {
        PROFILE_SECRET_CONFIG_KEY: json.dumps(payload),
        "revision": "0",
    }


class RuntimeProfileSecretService:
    """Manages per-profile Kubernetes Secrets that carry rendered runtime profile config.

    Pods reference these Secrets via env secretKeyRef (EFP_PROFILE_CONFIG /
    EFP_PROFILE_REVISION); config changes therefore only reach a pod through a
    portal-triggered restart with an updated Secret.
    """

    def __init__(self, k8s_service: K8sService | None = None) -> None:
        self.k8s_service = k8s_service or K8sService()

    def sync_profile_secret(self, profile) -> None:
        if not self.k8s_service.enabled:
            return
        self.k8s_service.upsert_secret(profile_secret_name(profile.id), render_profile_secret_data(profile))

    def ensure_none_secret(self) -> None:
        if not self.k8s_service.enabled:
            return
        self.k8s_service.upsert_secret(NONE_SECRET_NAME, render_none_secret_data())

    def delete_profile_secret(self, profile_id: str) -> None:
        if not self.k8s_service.enabled:
            return
        self.k8s_service.delete_secret(profile_secret_name(profile_id))

    def apply_profile_save(self, db: Session, profile) -> dict:
        """Update the profile Secret, then restart bound running agents to apply it."""
        self.sync_profile_secret(profile)

        agents = list(db.scalars(select(Agent).where(Agent.runtime_profile_id == profile.id)).all())
        running = [agent for agent in agents if (agent.status or "").lower() == "running"]
        restarted_agent_ids: list[str] = []
        failed_agent_ids: list[str] = []

        if self.k8s_service.enabled:
            for agent in running:
                result = self.k8s_service.restart_agent(agent)
                if result.status == "failed":
                    failed_agent_ids.append(agent.id)
                    agent.last_error = result.message
                    logger.warning(
                        "runtime profile save restart failed agent_id=%s profile_id=%s message=%s",
                        agent.id,
                        profile.id,
                        result.message,
                    )
                else:
                    restarted_agent_ids.append(agent.id)
                    agent.status = "restarting"
                    agent.last_error = result.message
                db.add(agent)
            if running:
                db.commit()

        return {
            "bound_agent_count": len(agents),
            "running_agent_count": len(running),
            "restarted_agent_ids": restarted_agent_ids,
            "failed_agent_ids": failed_agent_ids,
        }
