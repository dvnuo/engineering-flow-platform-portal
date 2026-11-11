from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Group(Base):
    """A team of members who share one set of Default connectors.

    Every member belongs to exactly one group (``users.group_id``; NULL is
    the default group). A group's Default connectors are the platform's
    (``platform_settings.runtime_profile_seed``) with the connectors the
    group sets itself replaced by its own values (``group_service``), and
    its members follow those the way they used to follow the platform's.

    ``connector_modes_json`` is ``{connector type: "custom"}`` for the
    connectors the group sets itself; an absent type follows the platform.
    A custom connector whose sections are empty in ``config_json`` is one
    the group does not provide.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Exactly one: where the env allowlist, the bootstrap admin and accounts
    # created before anyone allowlisted them land. It cannot be deleted.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    connector_modes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # No foreign key: users.group_id points here, and a cycle between the two
    # tables would make neither creatable first.
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
