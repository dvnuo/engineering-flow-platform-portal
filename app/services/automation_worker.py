import asyncio
import logging
import threading
from datetime import datetime

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.automation_rule_repo import AutomationRuleRepository
from app.services.automation_rule_service import AutomationRuleService

logger = logging.getLogger(__name__)


class AutomationWorker:
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

    def _run_loop(self) -> None:
        interval = max(1, int(self.settings.automation_rules_worker_interval_seconds))
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception:
                logger.exception("automation worker iteration failed")
            self._stop_event.wait(interval)

    async def _run_once(self) -> None:
        now = datetime.utcnow()
        db = SessionLocal()
        try:
            repo = AutomationRuleRepository(db)
            due_rules = repo.list_due_rules(now=now, limit=20)
            for due_rule in due_rules:
                locked = repo.acquire_due_rule_lock(
                    due_rule.id,
                    now=now,
                    lease_seconds=int(self.settings.automation_rule_lock_lease_seconds),
                )
                if not locked:
                    continue
                try:
                    await AutomationRuleService(db).run_rule_once(locked.id, triggered_by="worker")
                except Exception:
                    logger.exception("automation worker rule execution failed rule_id=%s", locked.id)
                    repo.update(locked, {"locked_until": None})
        finally:
            db.close()


worker_singleton = AutomationWorker()
