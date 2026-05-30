import json
import logging
import asyncio
import time
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.redaction import redact_text, sanitize_exception_message
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.proxy_service import ProxyService
from app.services.runtime_profile_authorization import (
    apply_runtime_profile_authorization,
    raw_runtime_profile_config,
)
from app.services.runtime_profile_runtime_v2_projection import (
    build_trusted_runtime_v2_config,
    project_config_for_runtime_type,
)

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

    @staticmethod
    def _portal_trusted_headers() -> dict[str, str]:
        return {"X-Portal-Author-Source": "portal"}

    @staticmethod
    def build_apply_payload_from_profile(runtime_profile) -> dict:
        parsed_config = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        parsed_config = build_trusted_runtime_v2_config(parsed_config)
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

    def build_apply_payload_for_agent(self, db: Session, agent, runtime_profile) -> dict:
        _ = db
        payload = self.build_apply_payload_from_profile(runtime_profile)
        runtime_type = getattr(agent, "runtime_type", "") or "native"
        payload["runtime_type"] = runtime_type
        payload["agent_id"] = getattr(agent, "id", None)
        payload["config"]["runtime_type"] = runtime_type
        payload["config"] = build_trusted_runtime_v2_config(
            payload.get("config"),
            runtime_type=runtime_type,
        )
        raw_config = raw_runtime_profile_config(runtime_profile)
        apply_runtime_profile_authorization(payload["config"], raw_config)
        payload["config"] = canonicalize_portal_runtime_profile_config(payload.get("config"))
        payload["config"]["runtime_type"] = runtime_type
        payload["config"] = project_config_for_runtime_type(payload.get("config"), runtime_type)
        return payload

    async def push_payload_to_agent_with_retry(self, agent, payload: dict, timeout_seconds: int = 180, interval_seconds: float = 3.0) -> RuntimeProfilePushResult:
        deadline = time.monotonic() + timeout_seconds
        last_result = None
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            result = await self.push_payload_to_agent(agent, payload)
            if result.ok:
                return RuntimeProfilePushResult(**{**asdict(result), "message": f"{result.message or 'ok'}; attempts={attempt}"})
            last_result = result
            await asyncio.sleep(interval_seconds)
        if last_result:
            return RuntimeProfilePushResult(**{**asdict(last_result), "message": f"{last_result.message or 'failed'}; attempts={attempt}"})
        return RuntimeProfilePushResult(agent_id=getattr(agent, "id", "-"), ok=False, status_code=None, apply_status="failed", message=f"runtime profile push failed; attempts={attempt}")

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
                sanitize_exception_message(exc),
            )
            return RuntimeProfilePushResult(getattr(agent, "id", "-"), False, None, "error", message=sanitize_exception_message(exc))

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
        return redact_text(text)[:limit]

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
                return RuntimeProfilePushResult(agent_id, False, status_code, "failed", message=redact_text(preview), raw_body_preview=preview)
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
            message=redact_text(str(parsed.get("message") or parsed.get("detail") or "")),
            raw_body_preview=preview,
        )
