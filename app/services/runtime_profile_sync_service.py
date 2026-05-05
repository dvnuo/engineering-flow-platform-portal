import json
import logging
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.proxy_service import ProxyService
from app.services.runtime_execution_context_service import RuntimeExecutionContextService

logger = logging.getLogger(__name__)


class RuntimeProfileSyncService:
    def __init__(self, proxy_service: ProxyService | None = None) -> None:
        self.proxy_service = proxy_service or ProxyService()
        self.execution_context_service = RuntimeExecutionContextService()

    @staticmethod
    def _portal_trusted_headers() -> dict[str, str]:
        return {"X-Portal-Author-Source": "portal"}

    @staticmethod
    def build_apply_payload_from_profile(runtime_profile) -> dict:
        parsed_config = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        return {
            "runtime_profile_id": runtime_profile.id,
            "revision": runtime_profile.revision,
            "config": parsed_config,
        }

    @staticmethod
    def build_clear_payload() -> dict:
        return {"runtime_profile_id": None, "revision": None, "config": {}}

    def build_clear_payload_for_agent(self, _db: Session, _agent) -> dict:
        return self.build_clear_payload()

    @staticmethod
    def _skill_aliases(skill_name: str) -> set[str]:
        raw = str(skill_name or "").strip().lower()
        if not raw:
            return set()
        hyphen = raw.replace("_", "-")
        return {raw, hyphen, f"skill:{raw}", f"skill:{hyphen}", f"opencode.skill.{raw}", f"opencode.skill.{hyphen}"}

    def _merge_agent_runtime_context_into_config(self, config: dict, execution_context: dict) -> dict:
        merged = deepcopy(config) if isinstance(config, dict) else {}
        capability_context = dict((execution_context or {}).get("capability_context") or {})
        policy_context = dict((execution_context or {}).get("policy_context") or {})

        allowed_ids = set(capability_context.get("allowed_capability_ids") or [])
        for skill_name in capability_context.get("skill_set") or []:
            allowed_ids.update(self._skill_aliases(skill_name))
        filtered_types = [
            item for item in (capability_context.get("allowed_capability_types") or [])
            if str(item).strip().lower() not in {"skill", "tool"}
        ]

        merged["capability_context"] = capability_context
        merged["policy_context"] = policy_context
        merged["allowed_capability_ids"] = sorted(allowed_ids)
        merged["allowed_capability_types"] = filtered_types
        merged["allowed_external_systems"] = capability_context.get("allowed_external_systems") or []
        merged["allowed_actions"] = capability_context.get("allowed_actions") or []
        merged["allowed_adapter_actions"] = capability_context.get("allowed_adapter_actions") or []
        merged["derived_runtime_rules"] = policy_context.get("derived_runtime_rules") or {}
        return merged

    def build_apply_payload_for_agent(self, db: Session, agent, runtime_profile) -> dict:
        payload = self.build_apply_payload_from_profile(runtime_profile)
        execution_context = self.execution_context_service.build_for_agent(db, agent)
        payload["config"] = self._merge_agent_runtime_context_into_config(payload["config"], execution_context)
        return payload

    async def sync_profile_to_bound_agents(self, db: Session, runtime_profile) -> dict:
        agents = list(db.scalars(select(Agent).where(Agent.runtime_profile_id == runtime_profile.id)).all())
        updated_running_count = 0
        skipped_not_running_count = 0
        failed_agent_ids: list[str] = []

        for agent in agents:
            if (agent.status or "").lower() != "running":
                skipped_not_running_count += 1
                continue
            payload = self.build_apply_payload_for_agent(db, agent, runtime_profile)
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
