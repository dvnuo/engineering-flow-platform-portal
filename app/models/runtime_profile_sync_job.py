from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RuntimeProfileSyncJob(Base):
    __tablename__ = "runtime_profile_sync_jobs"
    __table_args__ = (
        Index("ix_runtime_profile_sync_jobs_due", "status", "next_run_at"),
        Index("ix_runtime_profile_sync_jobs_agent_status", "agent_id", "status"),
        Index("ix_runtime_profile_sync_jobs_lock", "locked_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    runtime_profile_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    requested_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="apply")
    reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
