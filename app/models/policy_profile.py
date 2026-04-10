from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class PolicyProfile(Base):
    __tablename__ = "policy_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    auto_run_rules_json: Mapped[Optional[str]] = mapped_column(Text)
    permission_rules_json: Mapped[Optional[str]] = mapped_column(Text)
    audit_rules_json: Mapped[Optional[str]] = mapped_column(Text)
    transition_rules_json: Mapped[Optional[str]] = mapped_column(Text)
    max_parallel_tasks: Mapped[Optional[int]] = mapped_column(Integer)
    escalation_rules_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
