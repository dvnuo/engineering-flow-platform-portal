from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    parent_agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    assignee_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    input_payload_json: Mapped[Optional[str]] = mapped_column(Text)
    shared_context_ref: Mapped[Optional[str]] = mapped_column(String(255))
    task_family: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trigger: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bundle_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    version_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    owner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    runtime_request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    result_payload_json: Mapped[Optional[str]] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
