"""Background worker that stops long-idle agents to reclaim cluster resources.

An agent's deployment is scaled to 0 (``stop_agent``) once it has had no
user traffic for ``agent_idle_stop_after_seconds`` (default 3 days). Idle is
measured from ``agents.last_activity_at`` (bumped by the proxy on any use and
by start/restart). Agents with an active task, or that were recently used /
started, are left running. Stopped agents are restarted manually via the
existing Start action.
"""

import logging
import threading
from datetime import datetime, timedelta

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.audit_repo import AuditRepository
from app.services.k8s_service import K8sService

logger = logging.getLogger(__name__)


def _coerce_non_negative_int(value, *, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(0, normalized)


class IdleAgentStopWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.k8s = K8sService()
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
        initial_delay = _coerce_non_negative_int(
            getattr(self.settings, "idle_agent_stop_worker_initial_delay_seconds", 120),
            default=120,
        )
        if initial_delay and self._stop_event.wait(initial_delay):
            return
        interval = self._interval_seconds()
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("idle agent stop worker iteration failed")
            self._stop_event.wait(interval)

    def _interval_seconds(self) -> int:
        return _coerce_non_negative_int(
            getattr(self.settings, "idle_agent_stop_worker_interval_seconds", 600),
            default=600,
        ) or 600

    def _batch_size(self) -> int:
        return _coerce_non_negative_int(
            getattr(self.settings, "idle_agent_stop_worker_batch_size", 100),
            default=100,
        ) or 100

    def _idle_after_seconds(self) -> int:
        return _coerce_non_negative_int(
            getattr(self.settings, "agent_idle_stop_after_seconds", 259200),
            default=259200,
        )

    def run_once(self) -> int:
        """Stop idle running agents. Returns the number stopped."""
        if not self.k8s.enabled:
            return 0
        idle_after = self._idle_after_seconds()
        if idle_after <= 0:
            return 0
        cutoff = datetime.utcnow() - timedelta(seconds=idle_after)

        db = SessionLocal()
        stopped = 0
        try:
            agent_repo = AgentRepository(db)
            task_repo = AgentTaskRepository(db)
            for agent in agent_repo.list_by_status("running", limit=self._batch_size()):
                last_activity = agent.last_activity_at or agent.created_at
                if last_activity is None or last_activity > cutoff:
                    continue  # recently used or just started
                if task_repo.has_active_task(agent.id):
                    continue  # busy running a task
                if self._stop_idle_agent(db, agent_repo, agent):
                    stopped += 1
        finally:
            db.close()
        if stopped:
            logger.info("idle agent stop worker: stopped %d idle agent(s)", stopped)
        return stopped

    def _stop_idle_agent(self, db, agent_repo: AgentRepository, agent) -> bool:
        try:
            runtime = self.k8s.stop_agent(agent)
        except Exception:
            logger.exception("idle auto-stop: failed to stop agent %s", agent.id)
            return False
        if getattr(runtime, "status", None) == "failed":
            logger.warning(
                "idle auto-stop: stop returned failed for agent %s: %s",
                agent.id,
                getattr(runtime, "message", None),
            )
            return False
        agent.status = "stopped"
        agent_repo.save(agent)
        try:
            AuditRepository(db).create(
                "auto_stop_agent",
                "agent",
                agent.id,
                None,
                details={"reason": "idle"},
            )
        except Exception:
            db.rollback()
            logger.debug("idle auto-stop: audit write failed for %s", agent.id, exc_info=True)
        return True


idle_agent_stop_worker_singleton = IdleAgentStopWorker()
