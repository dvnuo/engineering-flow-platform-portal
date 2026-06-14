import asyncio
import logging
import threading

from app.config import get_settings
from app.db import SessionLocal
from app.redaction import sanitize_exception_message
from app.repositories.agent_task_repo import AgentTaskRepository
from app.services.task_dispatcher import TaskDispatcherService

logger = logging.getLogger(__name__)


class AgentTaskReconcileWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.dispatcher = TaskDispatcherService()
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
        interval = max(1, int(self.settings.agent_task_reconcile_worker_interval_seconds))
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception:
                logger.exception("agent task reconcile worker iteration failed")
            self._stop_event.wait(interval)

    async def _run_once(self) -> None:
        db = SessionLocal()
        try:
            repo = AgentTaskRepository(db)
            tasks = repo.list_active_agent_async_tasks(limit=int(self.settings.agent_task_reconcile_worker_batch_size))
            for task in tasks:
                status = (getattr(task, "status", None) or "").strip().lower()
                try:
                    if status == "queued":
                        self.dispatcher.dispatch_task_in_background(task.id)
                    elif status == "running":
                        await self.dispatcher.reconcile_running_task(task.id, db)
                except Exception as exc:
                    db.rollback()
                    logger.warning(
                        "agent task reconcile failed task_id=%s status=%s message=%s",
                        getattr(task, "id", "-"),
                        status or "-",
                        sanitize_exception_message(exc),
                        exc_info=True,
                    )
        finally:
            db.close()


agent_task_reconcile_worker_singleton = AgentTaskReconcileWorker()
