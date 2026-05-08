import asyncio
import logging
import threading
from datetime import datetime

from app.config import get_settings
from app.db import SessionLocal
from app.redaction import sanitize_exception_message
from app.repositories.runtime_profile_sync_job_repo import RuntimeProfileSyncJobRepository
from app.services.runtime_profile_sync_queue_service import RuntimeProfileSyncQueueService

logger = logging.getLogger(__name__)


class RuntimeProfileSyncWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _run_loop(self) -> None:
        interval = max(1, int(self.settings.runtime_profile_sync_worker_interval_seconds))
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception:
                logger.exception("runtime profile sync worker iteration failed")
            self._stop_event.wait(interval)

    async def _run_once(self) -> None:
        now = datetime.utcnow()
        db = SessionLocal()
        try:
            repo = RuntimeProfileSyncJobRepository(db)
            queue_service = RuntimeProfileSyncQueueService()
            due_jobs = repo.list_due_jobs(now=now, limit=int(self.settings.runtime_profile_sync_worker_batch_size))
            for due_job in due_jobs:
                locked = repo.acquire_lock(
                    due_job.id,
                    now=datetime.utcnow(),
                    lease_seconds=int(self.settings.runtime_profile_sync_job_lock_lease_seconds),
                )
                if not locked:
                    continue
                try:
                    await queue_service.run_job(db, locked)
                except Exception as exc:
                    repo.mark_retry(
                        locked,
                        now=datetime.utcnow(),
                        delay_seconds=queue_service._retry_delay_seconds(locked.attempts),
                        error_message=sanitize_exception_message(exc),
                    )
        finally:
            db.close()


runtime_profile_sync_worker_singleton = RuntimeProfileSyncWorker()
