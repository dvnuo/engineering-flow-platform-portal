from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MobileRecording(Base):
    """A member recording segments in the hosted Appium Inspector.

    The assistant holds a BrowserStack session and publishes it in its
    workspace; Portal binds that one session to this row and proxies the
    Inspector's WebDriver traffic for it, logging the commands that change the
    app. The token in the proxy URL is what the Inspector page presents; it is
    only good for this session and this member.
    """

    __tablename__ = "mobile_recordings"
    __table_args__ = (Index("ix_mobile_recordings_owner_agent_status", "owner_user_id", "agent_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hub_url: Mapped[str] = mapped_column(String(512), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    device: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    segment: Mapped[str] = mapped_column(String(128), nullable=False, default="segment-1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    viewport_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    viewport_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class MobileRecordingEvent(Base):
    """One WebDriver command of a recording that the segment compiler needs:
    finds (to know which element an action hit) and actions. Typed values of
    password fields are never stored."""

    __tablename__ = "mobile_recording_events"
    __table_args__ = (Index("ix_mobile_recording_events_recording_segment", "recording_id", "segment", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("mobile_recordings.id"), nullable=False, index=True)
    segment: Mapped[str] = mapped_column(String(128), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    request_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    element_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    element_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    element_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
