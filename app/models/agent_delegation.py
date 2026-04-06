from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentDelegation(Base):
    __tablename__ = "agent_delegations"
    __table_args__ = (
        Index("ix_agent_delegation_group_status", "group_id", "status"),
        Index("ix_agent_delegation_leader", "leader_agent_id"),
        Index("ix_agent_delegation_assignee", "assignee_agent_id"),
        Index("ix_agent_delegation_task", "agent_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    leader_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    assignee_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    agent_task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id"), nullable=True, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    leader_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    scoped_context_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_artifacts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_output_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_policy_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="leader_only")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_artifacts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    blockers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
