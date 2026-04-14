from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class ExternalEventSubscription(Base):
    __tablename__ = "external_event_subscriptions"
    __table_args__ = (
        Index("ix_external_sub_source_event", "source_type", "event_type"),
        Index("ix_external_sub_agent_enabled", "agent_id", "enabled"),
        Index("ix_external_sub_agent_enabled_mode", "agent_id", "enabled", "mode"),
        Index("ix_external_sub_source_kind_enabled", "source_kind", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    target_ref: Mapped[Optional[str]] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_json: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key_template: Mapped[Optional[str]] = mapped_column(String(255))
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default="push")
    source_kind: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    binding_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    scope_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    matcher_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    routing_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    poll_profile_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
