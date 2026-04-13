import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.services.proxy_service import ProxyService

logger = logging.getLogger(__name__)


class RuntimeProfileSyncService:
    def __init__(self, proxy_service: ProxyService | None = None) -> None:
        self.proxy_service = proxy_service or ProxyService()

    @staticmethod
    def _portal_trusted_headers() -> dict[str, str]:
        return {"X-Portal-Author-Source": "portal"}

    async def sync_profile_to_bound_agents(self, db: Session, runtime_profile) -> dict:
        try:
            config = json.loads(runtime_profile.config_json or "{}")
            if not isinstance(config, dict):
                config = {}
        except Exception:
            config = {}

        payload = {
            "runtime_profile_id": runtime_profile.id,
            "revision": runtime_profile.revision,
            "config": config,
        }

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
        status_code, content, _ = await self.proxy_service.forward(
            agent=agent,
            method="POST",
            subpath="api/internal/runtime-profile/apply",
            query_items=[],
            body=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            extra_headers=self._portal_trusted_headers(),
        )
        if status_code >= 400:
            logger.warning(
                "runtime profile sync failed agent_id=%s status=%s body=%s",
                getattr(agent, "id", "-"),
                status_code,
                content.decode("utf-8", errors="ignore"),
            )
            return False
        return True
