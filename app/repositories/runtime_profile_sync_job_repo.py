from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.models.runtime_profile_sync_job import RuntimeProfileSyncJob
from app.redaction import sanitize_exception_message


class RuntimeProfileSyncJobRepository:
    ACTIVE_STATUSES = ("pending", "running", "retrying")

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, job_id: str) -> RuntimeProfileSyncJob | None:
        return self.db.get(RuntimeProfileSyncJob, job_id)

    def enqueue(self, *, agent_id: str, runtime_profile_id: str | None, requested_revision: int | None, action: str = "apply", reason: str | None = None, max_attempts: int = 40, next_run_at: datetime | None = None) -> RuntimeProfileSyncJob:
        existing = self.db.scalar(
            select(RuntimeProfileSyncJob).where(
                and_(
                    RuntimeProfileSyncJob.agent_id == agent_id,
                    RuntimeProfileSyncJob.runtime_profile_id == runtime_profile_id,
                    RuntimeProfileSyncJob.requested_revision == requested_revision,
                    RuntimeProfileSyncJob.action == action,
                    RuntimeProfileSyncJob.status.in_(self.ACTIVE_STATUSES),
                )
            ).order_by(RuntimeProfileSyncJob.created_at.desc())
        )
        if existing:
            return existing
        now = datetime.utcnow()
        job = RuntimeProfileSyncJob(
            agent_id=agent_id,
            runtime_profile_id=runtime_profile_id,
            requested_revision=requested_revision,
            action=action,
            reason=reason,
            max_attempts=max_attempts,
            next_run_at=next_run_at or now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def list_due_jobs(self, *, now: datetime, limit: int) -> list[RuntimeProfileSyncJob]:
        q = (
            select(RuntimeProfileSyncJob)
            .where(RuntimeProfileSyncJob.status.in_(("pending", "retrying")))
            .where(RuntimeProfileSyncJob.next_run_at <= now)
            .where(or_(RuntimeProfileSyncJob.locked_until.is_(None), RuntimeProfileSyncJob.locked_until < now))
            .order_by(RuntimeProfileSyncJob.next_run_at.asc())
            .limit(limit)
        )
        return list(self.db.scalars(q).all())

    def acquire_lock(self, job_id: str, *, now: datetime, lease_seconds: int) -> RuntimeProfileSyncJob | None:
        stmt = (
            update(RuntimeProfileSyncJob)
            .where(RuntimeProfileSyncJob.id == job_id)
            .where(RuntimeProfileSyncJob.status.in_(("pending", "retrying")))
            .where(RuntimeProfileSyncJob.next_run_at <= now)
            .where(or_(RuntimeProfileSyncJob.locked_until.is_(None), RuntimeProfileSyncJob.locked_until < now))
            .values(
                status="running",
                locked_until=now + timedelta(seconds=max(1, int(lease_seconds))),
                attempts=RuntimeProfileSyncJob.attempts + 1,
                updated_at=now,
            )
        )
        result = self.db.execute(stmt)
        if result.rowcount != 1:
            self.db.rollback()
            return None
        self.db.commit()
        return self.get(job_id)

    def mark_succeeded(self, job: RuntimeProfileSyncJob, *, now: datetime, message: str | None = None) -> RuntimeProfileSyncJob:
        job.status = "succeeded"
        job.locked_until = None
        job.last_error = message
        job.updated_at = now
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_failed(self, job: RuntimeProfileSyncJob, *, now: datetime, error_message: str) -> RuntimeProfileSyncJob:
        job.status = "failed"
        job.locked_until = None
        job.last_error = sanitize_exception_message(error_message)
        job.updated_at = now
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_retry(self, job: RuntimeProfileSyncJob, *, now: datetime, delay_seconds: int, error_message: str) -> RuntimeProfileSyncJob:
        if job.attempts >= job.max_attempts:
            return self.mark_failed(job, now=now, error_message=error_message)
        job.status = "retrying"
        job.locked_until = None
        job.next_run_at = now + timedelta(seconds=max(1, int(delay_seconds)))
        job.last_error = sanitize_exception_message(error_message)
        job.updated_at = now
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def mark_skipped(self, job: RuntimeProfileSyncJob, *, now: datetime, reason: str) -> RuntimeProfileSyncJob:
        job.status = "skipped"
        job.locked_until = None
        job.last_error = sanitize_exception_message(reason)
        job.updated_at = now
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job
