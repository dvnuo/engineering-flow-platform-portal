import asyncio
import logging
import threading
from datetime import datetime

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.delegation_rule_repo import DelegationRuleRepository
from app.services.delegation_rule_service import DelegationRuleService

logger = logging.getLogger(__name__)


class DelegationWorker:
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
        interval = max(1, int(self.settings.delegation_rules_worker_interval_seconds))
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception:
                logger.exception("delegation worker iteration failed")
            self._stop_event.wait(interval)

    async def _run_once(self) -> None:
        now = datetime.utcnow()
        db = SessionLocal()
        try:
            repo = DelegationRuleRepository(db)
            due_rules = repo.list_due_rules(now=now, limit=20)
            for due_rule in due_rules:
                locked = repo.acquire_due_rule_lock(
                    due_rule.id,
                    now=now,
                    lease_seconds=int(self.settings.delegation_rule_lock_lease_seconds),
                )
                if not locked:
                    continue
                try:
                    await DelegationRuleService(db).run_rule_once(locked.id, triggered_by="worker")
                except Exception:
                    logger.exception("delegation worker rule execution failed rule_id=%s", locked.id)
                    repo.update(locked, {"locked_until": None})
        finally:
            db.close()


worker_singleton = DelegationWorker()
