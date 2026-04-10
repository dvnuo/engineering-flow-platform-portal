from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentCoordinationRun(Base):
    __tablename__ = "agent_coordination_runs"
    __table_args__ = (
        Index("ix_agent_coordination_run_group", "group_id"),
        Index("ix_agent_coordination_run_leader", "leader_agent_id"),
        Index("ix_agent_coordination_run_coordination", "coordination_run_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    leader_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    origin_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    coordination_run_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    latest_round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
