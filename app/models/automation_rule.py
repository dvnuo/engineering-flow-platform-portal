from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class AutomationRule(Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        Index("ix_automation_rules_enabled_next_run_at", "enabled", "next_run_at"),
        Index("ix_automation_rules_source_trigger_enabled", "source_type", "trigger_type", "enabled"),
        Index("ix_automation_rules_target_agent_enabled", "target_agent_id", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(128), nullable=False)
    target_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    trigger_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    task_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    schedule_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    owner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AutomationRuleRun(Base):
    __tablename__ = "automation_rule_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    rule_id: Mapped[str] = mapped_column(ForeignKey("automation_rules.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    found_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class AutomationRuleEvent(Base):
    __tablename__ = "automation_rule_events"
    __table_args__ = (
        UniqueConstraint("rule_id", "dedupe_key", name="uq_automation_rule_events_rule_dedupe"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    rule_id: Mapped[str] = mapped_column(ForeignKey("automation_rules.id"), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    normalized_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agent_tasks.id"), nullable=True, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
