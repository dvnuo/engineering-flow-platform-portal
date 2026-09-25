from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class RuntimeProfile(Base):
    """A member's one settings store; each connector edits its own sections.

    Exactly one row per member (``uq_runtime_profiles_owner``). Every assistant
    the member owns points at it, so the pod Secret and env wiring keyed by
    ``runtime_profile_id`` stay as they were when members had several.
    """

    __tablename__ = "runtime_profiles"

    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_runtime_profiles_owner_name"),
        Index("uq_runtime_profiles_owner", "owner_user_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
