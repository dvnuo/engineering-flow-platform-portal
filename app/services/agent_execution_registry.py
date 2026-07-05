from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.agent_execution import AgentExecution
from app.repositories.agent_execution_repo import AgentExecutionRepository
from app.redaction import sanitize_exception_message

logger = logging.getLogger(__name__)


def _clean_text(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _safe_json_loads(value: str | bytes | None) -> Any:
    if value is None:
        return None
    try:
        raw = value.decode("utf-8") if isinstance(value, bytes) else value
        return json.loads(raw)
    except Exception:
        return None


def _rollback_best_effort(db: Any) -> None:
    rollback = getattr(db, "rollback", None)
    if not callable(rollback):
        return
    try:
        rollback()
    except Exception:
        logger.debug("shadow execution registry rollback failed", exc_info=True)


def _task_status_to_execution_status(task_status: str | None) -> str:
    normalized = str(task_status or "").strip().lower()
    if normalized == "done":
        return "succeeded"
    if normalized in {"failed", "cancel_failed", "pending_restart"}:
        return "failed"
    if normalized == "blocked":
        return "blocked"
    if normalized == "cancelled":
        return "cancelled"
    if normalized == "stale":
        return "stale"
    if normalized in {"queued", "running", "cancelling"}:
        return normalized
    return "failed" if normalized else "stale"


def _chat_payload_to_terminal(
    *,
    status_code: int | None,
    payload: Any,
    fallback_error_code: str | None = None,
) -> tuple[str, str | None, str | None, str | None]:
    if status_code is not None and status_code >= 400:
        return "failed", "runtime_http_error", f"Runtime returned status {status_code}", None
    if not isinstance(payload, dict):
        return "stale", fallback_error_code or "missing_runtime_payload", "Runtime response did not contain a JSON object", None

    completion_state = str(payload.get("completion_state") or payload.get("status") or "").strip().lower()
    ok_value = payload.get("ok")
    incomplete_reason = _clean_text(payload.get("incomplete_reason") or payload.get("reason"), limit=128)
    error_text = _clean_text(payload.get("error") or payload.get("detail") or payload.get("message"))
    summary = _clean_text(payload.get("summary") or payload.get("response") or payload.get("final_response"))

    if ok_value is False or completion_state in {"error", "failed", "failure"}:
        return "failed", incomplete_reason or "chat_failed", error_text or "Chat execution failed", summary
    if completion_state == "incomplete":
        return "failed", incomplete_reason or "chat_incomplete", error_text or "Chat execution ended incomplete", summary
    return "succeeded", None, None, summary


def _metadata_without_prompt(payload: dict[str, Any] | None, *, extra: dict[str, Any] | None = None) -> str:
    source = payload if isinstance(payload, dict) else {}
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    safe = {
        "shadow_mode": True,
        "has_session_id": bool(source.get("session_id")),
        "has_request_id": bool(source.get("request_id")),
        "has_attachments": bool(source.get("attachments")),
        "model_override_present": bool(source.get("model_override")),
        "runtime_profile_id": metadata.get("runtime_profile_id"),
        "profile_version": metadata.get("profile_version"),
        "provider": metadata.get("provider"),
    }
    if extra:
        safe.update(extra)
    return _json_dumps({key: value for key, value in safe.items() if value is not None})


def record_chat_started_best_effort(
    db: Session,
    *,
    agent,
    user,
    payload: dict[str, Any],
    execution_path: str,
) -> AgentExecution | None:
    try:
        repo = AgentExecutionRepository(db)
        session_id = _clean_text(payload.get("session_id"), limit=128)
        active_same_session = repo.count_active_for_agent_session(agent_id=agent.id, session_id=session_id) if session_id else 0
        now = datetime.utcnow()
        return repo.create(
            agent_id=agent.id,
            session_id=session_id,
            request_id=_clean_text(payload.get("request_id"), limit=128),
            kind="chat",
            status="running",
            source="portal",
            runtime_type=_clean_text(getattr(agent, "runtime_type", None), limit=32),
            execution_path=execution_path,
            owner_user_id=getattr(agent, "owner_user_id", None),
            created_by_user_id=getattr(user, "id", None),
            would_conflict_same_session=active_same_session > 0,
            metadata_json=_metadata_without_prompt(
                payload,
                extra={
                    "active_same_session_count": active_same_session,
                    "would_conflict_same_session": active_same_session > 0,
                },
            ),
            started_at=now,
            last_event_at=now,
            heartbeat_at=now,
        )
    except Exception as exc:
        _rollback_best_effort(db)
        logger.warning("shadow execution registry chat start write failed: %s", sanitize_exception_message(exc), exc_info=True)
        return None


def finish_chat_response_best_effort(
    db: Session,
    *,
    execution_id: str | None,
    status_code: int | None,
    content: bytes | str | None,
) -> None:
    if not execution_id:
        return
    try:
        repo = AgentExecutionRepository(db)
        execution = repo.get_by_id(execution_id)
        if not execution:
            return
        payload = _safe_json_loads(content)
        status, error_code, error_message, summary = _chat_payload_to_terminal(status_code=status_code, payload=payload)
        repo.mark_status(
            execution,
            status=status,
            runtime_status_code=status_code,
            error_code=error_code,
            error_message=error_message,
            result_summary=summary,
        )
    except Exception as exc:
        _rollback_best_effort(db)
        logger.warning("shadow execution registry chat finish write failed: %s", sanitize_exception_message(exc), exc_info=True)


def mark_execution_failed_best_effort(
    db: Session,
    *,
    execution_id: str | None,
    error_code: str,
    error_message: str,
    runtime_status_code: int | None = None,
) -> None:
    if not execution_id:
        return
    try:
        repo = AgentExecutionRepository(db)
        execution = repo.get_by_id(execution_id)
        if not execution:
            return
        repo.mark_status(
            execution,
            status="failed",
            runtime_status_code=runtime_status_code,
            error_code=error_code,
            error_message=_clean_text(error_message),
        )
    except Exception as exc:
        _rollback_best_effort(db)
        logger.warning("shadow execution registry failure write failed: %s", sanitize_exception_message(exc), exc_info=True)


def upsert_task_execution_queued_best_effort(db: Session, *, task, agent=None, user=None) -> AgentExecution | None:
    try:
        repo = AgentExecutionRepository(db)
        existing = repo.get_latest_by_task_id(task.id)
        session_id = _clean_text(getattr(task, "task_session_id", None), limit=128)
        active_same_session = (
            repo.count_active_for_agent_session(
                agent_id=task.assignee_agent_id,
                session_id=session_id,
                exclude_execution_id=getattr(existing, "id", None),
            )
            if session_id
            else 0
        )
        metadata_json = _json_dumps(
            {
                "shadow_mode": True,
                "task_type": getattr(task, "task_type", None),
                "task_family": getattr(task, "task_family", None),
                "skill_name": getattr(task, "skill_name", None),
                "trigger": getattr(task, "trigger", None),
                "active_same_session_count": active_same_session,
                "would_conflict_same_session": active_same_session > 0,
            }
        )
        if existing:
            existing.agent_id = task.assignee_agent_id
            existing.session_id = session_id
            existing.kind = "async_task"
            existing.status = "queued"
            existing.source = getattr(task, "source", None)
            existing.runtime_type = _clean_text(getattr(agent, "runtime_type", None), limit=32)
            existing.execution_path = "/api/tasks/execute"
            existing.owner_user_id = getattr(task, "owner_user_id", None)
            existing.created_by_user_id = getattr(user, "id", None) or getattr(task, "created_by_user_id", None)
            existing.runtime_task_id = task.id
            existing.would_conflict_same_session = active_same_session > 0
            existing.error_code = None
            existing.error_message = None
            existing.result_summary = None
            existing.metadata_json = metadata_json
            existing.started_at = None
            existing.finished_at = None
            existing.last_event_at = datetime.utcnow()
            existing.heartbeat_at = datetime.utcnow()
            return repo.save(existing)
        return repo.create(
            agent_id=task.assignee_agent_id,
            session_id=session_id,
            request_id=_clean_text(getattr(task, "runtime_request_id", None), limit=128),
            kind="async_task",
            status="queued",
            source=getattr(task, "source", None),
            runtime_type=_clean_text(getattr(agent, "runtime_type", None), limit=32),
            runtime_task_id=task.id,
            task_id=task.id,
            execution_path="/api/tasks/execute",
            owner_user_id=getattr(task, "owner_user_id", None),
            created_by_user_id=getattr(user, "id", None) or getattr(task, "created_by_user_id", None),
            would_conflict_same_session=active_same_session > 0,
            metadata_json=metadata_json,
            last_event_at=datetime.utcnow(),
            heartbeat_at=datetime.utcnow(),
        )
    except Exception as exc:
        _rollback_best_effort(db)
        logger.warning("shadow execution registry task queued write failed: %s", sanitize_exception_message(exc), exc_info=True)
        return None


def mark_task_execution_status_best_effort(
    db: Session,
    *,
    task,
    status: str,
    runtime_status_code: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    result_summary: str | None = None,
) -> None:
    try:
        repo = AgentExecutionRepository(db)
        execution = repo.get_latest_by_task_id(task.id)
        if not execution:
            return
        repo.mark_status(
            execution,
            status=_task_status_to_execution_status(status),
            request_id=_clean_text(getattr(task, "runtime_request_id", None), limit=128),
            runtime_task_id=task.id,
            runtime_status_code=runtime_status_code,
            error_code=error_code,
            error_message=_clean_text(error_message),
            result_summary=_clean_text(result_summary, limit=1000),
        )
    except Exception as exc:
        _rollback_best_effort(db)
        logger.warning("shadow execution registry task status write failed: %s", sanitize_exception_message(exc), exc_info=True)


class ChatStreamExecutionObserver:
    def __init__(self, execution_id: str | None):
        self.execution_id = execution_id
        self._buffer = ""
        self.event_count = 0
        self.final_payload: dict[str, Any] | None = None
        self.error_payload: dict[str, Any] | None = None
        self.done_payload: dict[str, Any] | None = None

    def feed(self, chunk: bytes) -> None:
        if not self.execution_id or not chunk:
            return
        try:
            self._buffer += chunk.decode("utf-8", errors="ignore").replace("\r\n", "\n")
            while "\n\n" in self._buffer:
                block, self._buffer = self._buffer.split("\n\n", 1)
                self._handle_event_block(block)
        except Exception:
            logger.debug("shadow execution registry stream parse failed", exc_info=True)

    def _handle_event_block(self, block: str) -> None:
        event_name = "message"
        has_event_field = False
        data_lines: list[str] = []
        for raw_line in block.split("\n"):
            line = raw_line.strip("\r")
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                has_event_field = True
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if not has_event_field and not data_lines:
            # Pure comment blocks (runtime SSE keepalives) are not events.
            return
        if not event_name:
            return
        self.event_count += 1
        data = _safe_json_loads("\n".join(data_lines)) if data_lines else None
        if event_name == "final" and isinstance(data, dict):
            self.final_payload = data
        elif event_name == "error" and isinstance(data, dict):
            self.error_payload = data
        elif event_name == "done" and isinstance(data, dict):
            self.done_payload = data

    def finish(self, *, status_code: int | None, stream_error: str | None = None) -> None:
        if not self.execution_id:
            return
        db = SessionLocal()
        try:
            repo = AgentExecutionRepository(db)
            execution = repo.get_by_id(self.execution_id)
            if not execution:
                return
            payload = self.error_payload or self.final_payload or self.done_payload
            status, error_code, error_message, summary = _chat_payload_to_terminal(
                status_code=status_code,
                payload=payload,
                fallback_error_code="stream_closed_without_terminal_event",
            )
            if self.error_payload:
                status = "failed"
                error_code = error_code or _clean_text(self.error_payload.get("error"), limit=128) or "chat_stream_error"
                error_message = error_message or _clean_text(self.error_payload.get("detail") or self.error_payload.get("message"))
            elif stream_error:
                status = "failed"
                error_code = "chat_stream_exception"
                error_message = _clean_text(stream_error)
            elif not self.final_payload and not self.error_payload:
                status = "stale"
                error_code = "stream_closed_without_terminal_event"
                error_message = "Chat stream closed before final or error event"
            repo.mark_status(
                execution,
                status=status,
                runtime_status_code=status_code,
                error_code=error_code,
                error_message=error_message,
                result_summary=summary,
                metadata_json=_json_dumps(
                    {
                        "shadow_mode": True,
                        "stream_event_count": self.event_count,
                        "saw_final_event": self.final_payload is not None,
                        "saw_error_event": self.error_payload is not None,
                        "saw_done_event": self.done_payload is not None,
                    }
                ),
            )
        except Exception as exc:
            _rollback_best_effort(db)
            logger.warning("shadow execution registry stream finish write failed: %s", sanitize_exception_message(exc), exc_info=True)
        finally:
            db.close()
