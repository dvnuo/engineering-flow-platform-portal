from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.agent_execution import AgentExecution


ACTIVE_EXECUTION_STATUSES = ("queued", "running", "cancelling")
TERMINAL_EXECUTION_STATUSES = ("succeeded", "failed", "blocked", "cancelled", "stale")
# What a run cost, as the runtime reported it; see agent_execution_registry.usage_fields_from_payload.
USAGE_COLUMNS = (
    "model",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "estimated_credits",
    "actual_credits",
    "credits_source",
    "terminal_reason",
)


# A run that ends in one of these is recorded in the error log (warning for stale).
ERROR_LOG_STATUSES = ("failed", "stale")


class AgentExecutionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentExecution:
        execution = AgentExecution(**kwargs)
        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)
        return execution

    def get_by_id(self, execution_id: str) -> Optional[AgentExecution]:
        return self.db.get(AgentExecution, execution_id)

    def get_latest_by_task_id(self, task_id: str) -> Optional[AgentExecution]:
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.task_id == task_id)
            .order_by(AgentExecution.created_at.desc(), AgentExecution.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def get_latest_by_request_id(self, request_id: str) -> Optional[AgentExecution]:
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.request_id == request_id)
            .order_by(AgentExecution.created_at.desc(), AgentExecution.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def count_active_for_agent_session(
        self,
        *,
        agent_id: str,
        session_id: str | None,
        exclude_execution_id: str | None = None,
    ) -> int:
        filters = [
            AgentExecution.agent_id == agent_id,
            AgentExecution.status.in_(ACTIVE_EXECUTION_STATUSES),
        ]
        if session_id:
            filters.append(AgentExecution.session_id == session_id)
        if exclude_execution_id:
            filters.append(AgentExecution.id != exclude_execution_id)
        stmt = select(AgentExecution.id).where(and_(*filters))
        return len(list(self.db.scalars(stmt).all()))

    def list_active_for_agent(self, agent_id: str) -> list[AgentExecution]:
        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.agent_id == agent_id,
                AgentExecution.status.in_(ACTIVE_EXECUTION_STATUSES),
            )
            .order_by(AgentExecution.created_at.asc(), AgentExecution.id.asc())
        )
        return list(self.db.scalars(stmt).all())

    def save(self, execution: AgentExecution) -> AgentExecution:
        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)
        return execution

    def mark_status(
        self,
        execution: AgentExecution,
        *,
        status: str,
        request_id: str | None = None,
        runtime_task_id: str | None = None,
        runtime_status_code: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        result_summary: str | None = None,
        metadata_json: str | None = None,
        usage: dict | None = None,
    ) -> AgentExecution:
        now = datetime.utcnow()
        previous_status = execution.status
        execution.status = status
        for column, value in (usage or {}).items():
            if column in USAGE_COLUMNS and value is not None:
                setattr(execution, column, value)
        if request_id:
            execution.request_id = request_id
        if runtime_task_id:
            execution.runtime_task_id = runtime_task_id
        if runtime_status_code is not None:
            execution.runtime_status_code = runtime_status_code
        if error_code is not None:
            execution.error_code = error_code
        if error_message is not None:
            execution.error_message = error_message
        if result_summary is not None:
            execution.result_summary = result_summary
        if metadata_json is not None:
            execution.metadata_json = metadata_json
        execution.last_event_at = now
        execution.heartbeat_at = now
        if status == "running" and execution.started_at is None:
            execution.started_at = now
        if status in TERMINAL_EXECUTION_STATUSES and execution.finished_at is None:
            execution.finished_at = now
        if status in ERROR_LOG_STATUSES and previous_status != status:
            self._record_failure(execution)
        return self.save(execution)

    def _record_failure(self, execution: AgentExecution) -> None:
        """A run that ends failed or stale goes to Administration > Errors, committed with the row (app/services/error_log_service.py)."""
        from app.services.error_log_service import record_error  # local: the service imports the models, not the repositories

        context = (
            ("terminal_reason", execution.terminal_reason),
            ("runtime_status_code", execution.runtime_status_code),
            ("source", execution.source),
            ("execution_path", execution.execution_path),
            ("request_id", execution.request_id),
            ("runtime_task_id", execution.runtime_task_id),
            ("result_summary", execution.result_summary),
        )
        detail = "\n".join("%s: %s" % (key, value) for key, value in context if value not in (None, ""))
        record_error(
            self.db,
            source="execution",
            severity="error" if execution.status == "failed" else "warning",
            code=execution.error_code or execution.terminal_reason,
            message=execution.error_message or "Run ended %s" % execution.status,
            detail=detail or None,
            execution_id=execution.id,
            task_id=execution.task_id,
            session_id=execution.session_id,
            agent_id=execution.agent_id,
            user_id=execution.owner_user_id or execution.created_by_user_id,
            runtime_type=execution.runtime_type,
            model=execution.model,
            kind=execution.kind,
        )
