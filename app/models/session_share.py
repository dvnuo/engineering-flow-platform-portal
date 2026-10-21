from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SessionShare(Base):
    """A read-only share of one chat session.

    The page at ``/share/{token}`` reads the session live from the runtime
    each time it loads (there is no snapshot), so the row only says who may
    open it: ``scope`` is ``members`` (any signed-in, allowlisted member) or
    ``anyone`` (whoever has the link, no sign-in). One share per session;
    revoking deletes the row. ``title`` is the session's name when it was
    shared, shown only while the assistant's pod is stopped and the live name
    cannot be read.
    """

    __tablename__ = "session_shares"
    __table_args__ = (
        UniqueConstraint("agent_id", "session_id", name="uq_session_shares_agent_session"),
        Index("ix_session_shares_owner_user_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="members")
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
