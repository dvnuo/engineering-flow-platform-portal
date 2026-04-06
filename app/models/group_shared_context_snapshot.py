from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GroupSharedContextSnapshot(Base):
    __tablename__ = "group_shared_context_snapshots"
    __table_args__ = (
        Index("ix_group_shared_context_group_ref", "group_id", "context_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    context_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    source_delegation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
