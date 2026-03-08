from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    cpu: Mapped[Optional[str]] = mapped_column(String(32))
    memory: Mapped[Optional[str]] = mapped_column(String(32))
    disk_size_gi: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    mount_path: Mapped[str] = mapped_column(String(255), nullable=False, default="/data")
    namespace: Mapped[str] = mapped_column(String(63), nullable=False, default="agents")
    deployment_name: Mapped[str] = mapped_column(String(128), nullable=False)
    service_name: Mapped[str] = mapped_column(String(128), nullable=False)
    pvc_name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint_path: Mapped[Optional[str]] = mapped_column(String(255))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
