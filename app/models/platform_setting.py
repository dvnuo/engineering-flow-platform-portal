from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from typing import Optional


class PlatformSetting(Base):
    """Admin-owned platform values that are data rather than deployment config.

    Used for the runtime-profile seed: the non-secret shape (instance URLs, API
    versions, project keys) an admin fills in once so every new member starts
    with those prefilled and only has to supply their own credentials.
    """

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


RUNTIME_PROFILE_SEED_KEY = "runtime_profile_seed"
