from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ErrorEvent(Base):
    """One failure the Portal saw, kept for troubleshooting (Administration > Errors).

    Written by ``app/services/error_log_service.py`` from five places: a run
    that ended failed or stale, an assistant whose runtime failed, a connector
    Test that failed, a Portal response of 500 or more (or an unhandled
    exception), and a warning or error from the Portal's own loggers. Messages
    and details pass through ``app.redaction`` first. ``fingerprint`` groups
    the same problem across members and assistants; a Portal or log event that
    repeats within a short window for the same member, assistant and path
    bumps ``count`` instead of adding a row. Members and assistants are
    referenced by id without foreign keys, so a row outlives the assistant it
    is about.
    """

    __tablename__ = "error_events"
    __table_args__ = (
        Index("ix_error_events_last_seen", "last_seen_at"),
        Index("ix_error_events_fingerprint_seen", "fingerprint", "last_seen_at"),
        Index("ix_error_events_source_seen", "source", "last_seen_at"),
        Index("ix_error_events_user", "user_id"),
        Index("ix_error_events_agent", "agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="error")
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    execution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    runtime_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    kind: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    request_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    logger_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
