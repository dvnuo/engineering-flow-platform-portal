import json
import logging
from copy import deepcopy
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.proxy_service import ProxyService
from app.services.runtime_execution_context_service import RuntimeExecutionContextService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeProfilePushResult:
    agent_id: str
    ok: bool
    status_code: int | None
    apply_status: str
    pending_restart: bool = False
    partially_applied: bool = False
    message: str = ""
    raw_body_preview: str = ""


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

    @staticmethod
    def _as_string_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                normalized = item.strip()
                if normalized:
                    result.append(normalized)
        return result

    @staticmethod
    def _merge_string_lists(*values) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            for item in RuntimeProfileSyncService._as_string_list(value):
                key = item.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    result.append(item)
        return result

    @staticmethod
    def _filter_broad_capability_types(values) -> list[str]:
        return [
            item for item in RuntimeProfileSyncService._as_string_list(values)
            if item.strip().lower() not in {"skill", "tool"}
        ]

    @staticmethod
    def _as_dict(value) -> dict:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _raw_profile_config(runtime_profile) -> dict:
        raw = getattr(runtime_profile, "config_json", None)
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _merge_agent_runtime_context_into_config(self, config: dict, execution_context: dict) -> dict:
        merged = deepcopy(config) if isinstance(config, dict) else {}
        capability_context = dict((execution_context or {}).get("capability_context") or {})
        policy_context = dict((execution_context or {}).get("policy_context") or {})

        skill_aliases: list[str] = []
        for skill_name in self._as_string_list(capability_context.get("skill_set")):
            skill_aliases.extend(sorted(self._skill_aliases(skill_name)))

        merged["capability_context"] = capability_context
        merged["policy_context"] = policy_context

        merged["allowed_capability_ids"] = self._merge_string_lists(
            merged.get("allowed_capability_ids"),
            capability_context.get("allowed_capability_ids"),
            skill_aliases,
        )
        merged["allowed_capability_types"] = self._filter_broad_capability_types(
            self._merge_string_lists(
                merged.get("allowed_capability_types"),
                capability_context.get("allowed_capability_types"),
            )
        )
        merged["allowed_external_systems"] = self._merge_string_lists(
            merged.get("allowed_external_systems"),
            capability_context.get("allowed_external_systems"),
        )
        merged["allowed_actions"] = self._merge_string_lists(
            merged.get("allowed_actions"),
            capability_context.get("allowed_actions"),
        )
        merged["allowed_adapter_actions"] = self._merge_string_lists(
            merged.get("allowed_adapter_actions"),
            capability_context.get("allowed_adapter_actions"),
        )
        existing_rules = self._as_dict(merged.get("derived_runtime_rules"))
        policy_rules = self._as_dict(policy_context.get("derived_runtime_rules"))
        merged["derived_runtime_rules"] = {**existing_rules, **policy_rules}
        for key in ["unresolved_tools", "unresolved_skills", "unresolved_channels", "unresolved_actions"]:
            merged[key] = capability_context.get(key) or []
        merged["resolved_action_mappings"] = capability_context.get("resolved_action_mappings") or {}
        merged["runtime_capability_catalog_version"] = capability_context.get("runtime_capability_catalog_version")
        merged["runtime_capability_catalog_source"] = capability_context.get("runtime_capability_catalog_source")
        merged["catalog_validation_mode"] = capability_context.get("catalog_validation_mode")
        return merged

    def build_apply_payload_for_agent(self, db: Session, agent, runtime_profile) -> dict:
        payload = self.build_apply_payload_from_profile(runtime_profile)
        raw_config = self._raw_profile_config(runtime_profile)
        for key in [
            "allowed_capability_ids",
            "allowed_capability_types",
            "allowed_external_systems",
            "allowed_actions",
            "allowed_adapter_actions",
            "derived_runtime_rules",
        ]:
            if key in raw_config and key not in payload["config"]:
                payload["config"][key] = deepcopy(raw_config.get(key))
        execution_context = self.execution_context_service.build_for_agent(db, agent)
        payload["config"] = self._merge_agent_runtime_context_into_config(payload["config"], execution_context)
        return payload

    async def sync_profile_to_bound_agents(self, db: Session, runtime_profile) -> dict:
        agents = list(db.scalars(select(Agent).where(Agent.runtime_profile_id == runtime_profile.id)).all())
        updated_running_count = 0
        applied_running_count = 0
        skipped_not_running_count = 0
        failed_agent_ids: list[str] = []
        pending_restart_agent_ids: list[str] = []
        partially_applied_agent_ids: list[str] = []
        results: list[dict] = []

        for agent in agents:
            if (agent.status or "").lower() != "running":
                skipped_not_running_count += 1
                continue
            payload = self.build_apply_payload_for_agent(db, agent, runtime_profile)
            result = await self.push_payload_to_agent(agent, payload)
            if isinstance(result, bool):
                result = RuntimeProfilePushResult(
                    agent_id=agent.id,
                    ok=bool(result),
                    status_code=None,
                    apply_status="applied" if result else "failed",
                )
            results.append(asdict(result))
            if result.ok:
                updated_running_count += 1
                if result.pending_restart:
                    pending_restart_agent_ids.append(agent.id)
                if result.partially_applied:
                    partially_applied_agent_ids.append(agent.id)
                if not result.pending_restart and not result.partially_applied:
                    applied_running_count += 1
            else:
                failed_agent_ids.append(agent.id)

        return {
            "updated_running_count": updated_running_count,
            "applied_running_count": applied_running_count,
            "pending_restart_agent_ids": pending_restart_agent_ids,
            "partially_applied_agent_ids": partially_applied_agent_ids,
            "skipped_not_running_count": skipped_not_running_count,
            "failed_agent_ids": failed_agent_ids,
            "results": results,
        }

    async def push_payload_to_agent(self, agent, payload: dict) -> RuntimeProfilePushResult:
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
            return RuntimeProfilePushResult(getattr(agent, "id", "-"), False, None, "error", message=str(exc))

        result = self._parse_apply_response(getattr(agent, "id", "-"), status_code, content)
        if not result.ok:
            logger.warning("runtime profile sync failed agent_id=%s status=%s body=%s", getattr(agent, "id", "-"), status_code, result.raw_body_preview)
        return result
    @staticmethod
    def _safe_body_preview(content: bytes | str | None, limit: int = 800) -> str:
        if content is None:
            return ""
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="ignore")
        else:
            text = str(content)
        return text[:limit]

    @classmethod
    def _parse_apply_response(cls, agent_id: str, status_code: int, content: bytes) -> RuntimeProfilePushResult:
        preview = cls._safe_body_preview(content)
        parsed = None
        try:
            parsed = json.loads(preview) if preview else None
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            if status_code >= 400:
                return RuntimeProfilePushResult(agent_id, False, status_code, "failed", message=preview, raw_body_preview=preview)
            return RuntimeProfilePushResult(agent_id, True, status_code, "applied", raw_body_preview=preview)
        apply_status = str(parsed.get("status") or parsed.get("apply_status") or ("applied" if parsed.get("ok") is not False else "failed")).lower()
        pending_restart = bool(parsed.get("pending_restart")) or apply_status == "pending_restart"
        partially_applied = bool(parsed.get("partially_applied")) or apply_status in {"partially_applied", "partial"}
        ok = status_code < 400 and parsed.get("ok") is not False and apply_status not in {"failed", "error"}
        return RuntimeProfilePushResult(
            agent_id=agent_id,
            ok=ok,
            status_code=status_code,
            apply_status=apply_status,
            pending_restart=pending_restart,
            partially_applied=partially_applied,
            message=str(parsed.get("message") or parsed.get("detail") or ""),
            raw_body_preview=preview,
        )
