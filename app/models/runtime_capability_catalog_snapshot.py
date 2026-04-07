from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class RuntimeCapabilityCatalogSnapshot(Base):
    __tablename__ = "runtime_capability_catalog_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_agent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    catalog_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    catalog_source: Mapped[str] = mapped_column(String(64), nullable=False, default="runtime_api")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
