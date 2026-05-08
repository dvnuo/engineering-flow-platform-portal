import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.agent import Agent
from app.models.runtime_profile_sync_job import RuntimeProfileSyncJob
from app.repositories.agent_repo import AgentRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.repositories.runtime_profile_sync_job_repo import RuntimeProfileSyncJobRepository
from app.services.k8s_service import K8sService
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService
from app.utils.state_machine import is_valid_status

logger = logging.getLogger(__name__)


class RuntimeProfileSyncQueueService:
    def __init__(self, *, runtime_profile_sync_service: RuntimeProfileSyncService | None = None, k8s_service: K8sService | None = None) -> None:
        self.settings = get_settings()
        self.runtime_profile_sync_service = runtime_profile_sync_service or RuntimeProfileSyncService()
        self.k8s_service = k8s_service or K8sService()

    def _retry_delay_seconds(self, attempts: int) -> int:
        return min(300, max(5, 2 ** min(attempts, 6)))

    def enqueue_agent_runtime_profile_sync(self, db: Session, agent, *, reason: str) -> RuntimeProfileSyncJob | None:
        if not getattr(agent, "runtime_profile_id", None):
            return None
        profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
        if not profile:
            logger.warning("runtime profile missing while enqueueing sync agent_id=%s profile_id=%s", agent.id, agent.runtime_profile_id)
            return None
        return RuntimeProfileSyncJobRepository(db).enqueue(
            agent_id=agent.id,
            runtime_profile_id=profile.id,
            requested_revision=profile.revision,
            action="apply",
            reason=reason,
            max_attempts=int(self.settings.runtime_profile_sync_job_max_attempts),
        )

    def enqueue_profile_to_bound_agents(self, db: Session, runtime_profile, *, reason: str) -> dict:
        agents = list(db.query(Agent).filter(Agent.runtime_profile_id == runtime_profile.id).all())
        queued_agent_ids = []
        skipped = 0
        for agent in agents:
            job = self.enqueue_agent_runtime_profile_sync(db, agent, reason=reason)
            if job:
                queued_agent_ids.append(agent.id)
            else:
                skipped += 1
        return {"queued_agent_count": len(queued_agent_ids), "skipped_agent_count": skipped, "queued_agent_ids": queued_agent_ids}

    async def run_job(self, db: Session, job: RuntimeProfileSyncJob) -> None:
        now = datetime.utcnow()
        repo = RuntimeProfileSyncJobRepository(db)
        job = repo.get(job.id)
        if not job:
            return
        agent = AgentRepository(db).get_by_id(job.agent_id)
        if not agent:
            repo.mark_skipped(job, now=now, reason="agent not found")
            return
        if not job.runtime_profile_id:
            repo.mark_skipped(job, now=now, reason="runtime_profile_id missing")
            return
        if str(agent.runtime_profile_id or "") != str(job.runtime_profile_id):
            repo.mark_skipped(job, now=now, reason="stale job: agent runtime_profile changed")
            return
        profile = RuntimeProfileRepository(db).get_by_id(job.runtime_profile_id)
        if not profile:
            repo.mark_skipped(job, now=now, reason="runtime profile not found")
            return
        try:
            runtime = self.k8s_service.get_agent_runtime_status(agent)
            agent.status = runtime.status if is_valid_status(runtime.status) else "failed"
            agent.last_error = runtime.message
            AgentRepository(db).save(agent)
        except Exception as exc:
            repo.mark_retry(job, now=now, delay_seconds=self._retry_delay_seconds(job.attempts), error_message=str(exc))
            return
        if (agent.status or "").lower() != "running":
            repo.mark_retry(job, now=now, delay_seconds=self._retry_delay_seconds(job.attempts), error_message=f"agent not running: {agent.status}")
            return
        if profile.revision != job.requested_revision:
            repo.mark_skipped(job, now=now, reason="stale job: profile revision changed")
            return
        payload = self.runtime_profile_sync_service.build_apply_payload_for_agent(db, agent, profile)
        try:
            push_result = await asyncio.wait_for(
                self.runtime_profile_sync_service.push_payload_to_agent(agent, payload),
                timeout=float(self.settings.runtime_profile_sync_push_timeout_seconds),
            )
        except Exception as exc:
            repo.mark_retry(job, now=datetime.utcnow(), delay_seconds=self._retry_delay_seconds(job.attempts), error_message=str(exc))
            return
        if push_result.ok:
            warning = None
            if push_result.pending_restart:
                warning = "pending_restart"
            elif push_result.partially_applied:
                warning = "partially_applied"
            repo.mark_succeeded(job, now=datetime.utcnow(), message=warning)
            return
        repo.mark_retry(
            job,
            now=datetime.utcnow(),
            delay_seconds=self._retry_delay_seconds(job.attempts),
            error_message=push_result.apply_status or push_result.message or "runtime profile push failed",
        )
