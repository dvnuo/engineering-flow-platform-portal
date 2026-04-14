import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.proxy_service import ProxyService

logger = logging.getLogger(__name__)


class RuntimeProfileSyncService:
    def __init__(self, proxy_service: ProxyService | None = None) -> None:
        self.proxy_service = proxy_service or ProxyService()

    @staticmethod
    def _portal_trusted_headers() -> dict[str, str]:
        return {"X-Portal-Author-Source": "portal"}

    @staticmethod
    def build_apply_payload_from_profile(runtime_profile) -> dict:
        return {
            "runtime_profile_id": runtime_profile.id,
            "revision": runtime_profile.revision,
            "config": parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True),
        }

    @staticmethod
    def build_clear_payload() -> dict:
        return {"runtime_profile_id": None, "revision": None, "config": {}}

    async def sync_profile_to_bound_agents(self, db: Session, runtime_profile) -> dict:
        payload = self.build_apply_payload_from_profile(runtime_profile)

        agents = list(db.scalars(select(Agent).where(Agent.runtime_profile_id == runtime_profile.id)).all())
        updated_running_count = 0
        skipped_not_running_count = 0
        failed_agent_ids: list[str] = []

        for agent in agents:
            if (agent.status or "").lower() != "running":
                skipped_not_running_count += 1
                continue
            ok = await self.push_payload_to_agent(agent, payload)
            if ok:
                updated_running_count += 1
            else:
                failed_agent_ids.append(agent.id)

        return {
            "updated_running_count": updated_running_count,
            "skipped_not_running_count": skipped_not_running_count,
            "failed_agent_ids": failed_agent_ids,
        }

    async def push_payload_to_agent(self, agent, payload: dict) -> bool:
        try:
            headers = {"content-type": "application/json"}
            status_code, content, _ = await self.proxy_service.forward(
                agent=agent,
                method="POST",
                subpath="api/internal/runtime-profile/apply",
                query_items=[],
                body=json.dumps(payload).encode("utf-8"),
                headers=headers,
                extra_headers=self._portal_trusted_headers(),
            )
        except Exception as exc:
            logger.warning(
                "runtime profile sync exception agent_id=%s exception=%s",
                getattr(agent, "id", "-"),
                exc,
            )
            return False

        if status_code >= 400:
            logger.warning(
                "runtime profile sync failed agent_id=%s status=%s body=%s",
                getattr(agent, "id", "-"),
                status_code,
                content.decode("utf-8", errors="ignore"),
            )
            return False
        return True
