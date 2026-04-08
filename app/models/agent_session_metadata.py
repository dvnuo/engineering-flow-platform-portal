from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class AgentSessionMetadata(Base):
    __tablename__ = "agent_session_metadata"
    __table_args__ = (
        UniqueConstraint("agent_id", "session_id", name="uq_agent_session_metadata_agent_session"),
        Index("ix_agent_session_metadata_agent_id", "agent_id"),
        Index("ix_agent_session_metadata_agent_updated_at", "agent_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    group_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    current_task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    current_delegation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    current_coordination_run_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    latest_event_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    latest_event_state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    snapshot_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pending_delegations_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_events_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
