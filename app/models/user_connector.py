from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserConnector(Base):
    """A per-member connector setting (see docs/CONNECTORS_CONTRACT.md).

    Connectors are capabilities that live outside the assistant pod, such as a
    bridge program on the member's own PC. Unlike runtime profiles they are not
    rendered into pod secrets and saving one never restarts an assistant; the
    proxy reads the enabled rows per chat request instead.
    """

    __tablename__ = "user_connectors"

    __table_args__ = (
        UniqueConstraint("owner_user_id", "connector_type", name="uq_user_connectors_owner_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verification_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
