import asyncio
import logging
import threading

from app.config import get_settings
from app.db import SessionLocal
from app.redaction import sanitize_exception_message
from app.repositories.agent_task_repo import AgentTaskRepository
from app.services.task_dispatcher import TaskDispatcherService

logger = logging.getLogger(__name__)


def _coerce_non_negative_int(value, *, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(0, normalized)


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
        initial_delay = self._initial_delay_seconds()
        if initial_delay and self._stop_event.wait(initial_delay):
            return
        interval = self._interval_seconds()
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception:
                logger.exception("agent task reconcile worker iteration failed")
            self._stop_event.wait(interval)

    def _initial_delay_seconds(self) -> int:
        return _coerce_non_negative_int(
            getattr(self.settings, "agent_task_reconcile_worker_initial_delay_seconds", 30),
            default=30,
        )

    def _interval_seconds(self) -> int:
        return max(
            1,
            _coerce_non_negative_int(
                getattr(self.settings, "agent_task_reconcile_worker_interval_seconds", 5),
                default=5,
            ),
        )

    def _batch_size(self) -> int:
        return _coerce_non_negative_int(
            getattr(self.settings, "agent_task_reconcile_worker_batch_size", 50),
            default=50,
        )

    async def _run_once(self) -> None:
        db = SessionLocal()
        try:
            repo = AgentTaskRepository(db)
            tasks = repo.list_active_agent_async_tasks(limit=self._batch_size())
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
