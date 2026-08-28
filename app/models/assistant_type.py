from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class AssistantType(Base):
    """An admin-curated assistant preset offered in the simple create flow.

    Simple mode asks the user for a name and one of these; everything else the
    runtime needs (engine, behavior branch, skill branch) comes from here, so a
    non-engineer never has to answer a repository question.
    """

    __tablename__ = "assistant_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    icon: Mapped[str] = mapped_column(String(64), nullable=False, default="bot")
    runtime_type: Mapped[str] = mapped_column(String(32), nullable=False, default="native")
    # Branch-only by design: the repository URLs stay on Portal configuration so
    # an admin picks from real branches instead of retyping a URL.
    agent_settings_branch: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    skill_branch: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
