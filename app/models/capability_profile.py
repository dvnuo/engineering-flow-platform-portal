from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class CapabilityProfile(Base):
    __tablename__ = "capability_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    tool_set_json: Mapped[Optional[str]] = mapped_column(Text)
    channel_set_json: Mapped[Optional[str]] = mapped_column(Text)
    skill_set_json: Mapped[Optional[str]] = mapped_column(Text)
    allowed_external_systems_json: Mapped[Optional[str]] = mapped_column(Text)
    allowed_webhook_triggers_json: Mapped[Optional[str]] = mapped_column(Text)
    allowed_actions_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
