from typing import Optional
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # "admin" (system administrator), "group_admin" or "user"
    # (app/services/authorization.py).
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    # The member's one group (app/models/group.py); NULL is the default group.
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # JSON object: the Portal language and the assistant's reply language
    # (app/services/locale_service.py). Server-side on purpose: the pages are
    # rendered with it and the chat proxy reads it on every request.
    preferences_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
