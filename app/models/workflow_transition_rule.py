from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class WorkflowTransitionRule(Base):
    __tablename__ = "workflow_transition_rules"
    __table_args__ = (
        Index(
            "ix_wtr_system_project_issue_status",
            "system_type",
            "project_key",
            "issue_type",
            "trigger_status",
        ),
        Index("ix_wtr_target_agent_enabled", "target_agent_id", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_status: Mapped[str] = mapped_column(String(128), nullable=False)
    assignee_binding: Mapped[Optional[str]] = mapped_column(String(255))
    target_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    skill_name: Mapped[Optional[str]] = mapped_column(String(128))
    success_transition: Mapped[Optional[str]] = mapped_column(String(128))
    failure_transition: Mapped[Optional[str]] = mapped_column(String(128))
    success_reassign_to: Mapped[Optional[str]] = mapped_column(String(32))
    failure_reassign_to: Mapped[Optional[str]] = mapped_column(String(32))
    explicit_success_assignee: Mapped[Optional[str]] = mapped_column(String(255))
    explicit_failure_assignee: Mapped[Optional[str]] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
