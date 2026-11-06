from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentExecution(Base):
    __tablename__ = "agent_executions"
    __table_args__ = (
        # Per-member sums over a period (Administration > User Management).
        Index("ix_agent_executions_created_by_created_at", "created_by_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    runtime_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    runtime_task_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agent_tasks.id"), nullable=True, index=True)
    execution_path: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    owner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    runtime_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    would_conflict_same_session: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # What the run cost, from the runtime's final payload (chat) or task result:
    # the model that answered, the token counts and the AI-credits estimate.
    # NULL when the runtime reported none (an older image, a failed run).
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimated_credits: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
