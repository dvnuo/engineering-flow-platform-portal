"""Apps: a member's web app, hosted by the app host and registered here.

The Portal is the registry: what an app is (a directory of a GitHub
repository at a commit), who owns it, who may open it, what it may use of
its owner's model provider and connectors, and the history of its
deployments. The app host (a separate service) runs the code; the rows here
are the source of truth it is brought back from. See APPS_PLAN.md and the
contracts in the app repository (docs/CONTRACTS.md).
"""

from __future__ import annotations

from datetime import date, datetime
import json
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _new_id() -> str:
    return str(uuid4())


def _json_object(text: Any) -> dict:
    try:
        value = json.loads(text) if isinstance(text, str) and text.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(text: Any) -> list:
    try:
        value = json.loads(text) if isinstance(text, str) and text.strip() else []
    except ValueError:
        return []
    return value if isinstance(value, list) else []


class App(Base):
    """One app.

    ``slug`` is the URL path segment: unique, never changed and never given
    to another app, which is why a deleted app keeps its row (``deleted_at``)
    and the unique constraint covers it. ``visibility`` uses the Portal's
    words (``private``, ``public`` = the owner's group, ``platform``) plus
    ``anyone``, which counts only while the platform allows it and
    ``anyone_approved_by`` names the system administrator who approved this
    app. ``token_value`` is kept next to its hash because the owner may read
    it and every deployment sends it to the host; ``visitor_key`` signs the
    visitor header the gateway hands the app. ``disabled_reason`` is set when
    an administrator takes the app down; an app whose owner lost access is
    disabled without it (app/services/apps/access.py).
    """

    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_apps_slug"),
        UniqueConstraint("token_hash", name="uq_apps_token_hash"),
        Index("ix_apps_owner_user_id", "owner_user_id"),
        Index("ix_apps_visibility", "visibility"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    slug: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    # The manifest's type as of the last successful deployment; None before it.
    type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    repo_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    entry_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # app_releases.id of the release the host runs; no foreign key, the two
    # tables would otherwise need each other to exist first.
    current_release_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # "running" or "stopped": what the owner last asked for.
    desired_state: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_value: Mapped[str] = mapped_column(String(64), nullable=False)
    token_last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # "cluster" or "external".
    token_last_used_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    visitor_key: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Connector types the app may call, and those of them it may write to.
    connectors_enabled: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    connectors_write: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # The owner's own environment variables: a JSON object of strings.
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    template_app_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    template_version_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    anyone_approved_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    disabled_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    icon_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    icon_content_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # What the app may spend a day of its owner's provider; None = the platform default.
    daily_credits: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    daily_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # When the data of a deleted app was removed from the host; None while it is still kept there.
    data_purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def env(self) -> dict[str, str]:
        """The owner's environment variables as a dict of strings."""
        return {str(key): str(value) for key, value in _json_object(self.env_json).items()}

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AppRelease(Base):
    """One deployment or rollback of an app, with its steps as they went.

    ``steps_json`` is a list of ``{"name", "status", "started_at",
    "finished_at"}``: the Portal's own steps (``resolve``, ``manifest``,
    ``fetch``) followed by the host's. ``status`` is ``queued``, ``running``,
    ``succeeded`` or ``failed``; ``kind`` is ``deploy`` or ``rollback``.
    """

    __tablename__ = "app_releases"
    __table_args__ = (
        Index("ix_app_releases_app_started", "app_id", "started_at"),
        Index("ix_app_releases_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    app_id: Mapped[str] = mapped_column(ForeignKey("apps.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="deploy")
    commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commit_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    host_job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # The template version this deployment moves the app to (an upgrade of an
    # app created from a template); the app takes it when the release succeeds.
    template_version_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # The end of the host job's log as it was when the job finished.
    log_tail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def steps(self) -> list[dict]:
        return [item for item in _json_list(self.steps_json) if isinstance(item, dict)]


class AppTemplateVersion(Base):
    """A tag of a template app's repository published as a version others create apps from."""

    __tablename__ = "app_template_versions"
    __table_args__ = (
        UniqueConstraint("app_id", "tag", name="uq_app_template_versions_app_tag"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    app_id: Mapped[str] = mapped_column(ForeignKey("apps.id"), nullable=False)
    tag: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class AppExecution(Base):
    """One SDK call of an app: a model completion or a connector request.

    What the daily budget is counted from and what the usage pages read.
    ``visitor_user_id`` is the member the app said caused the call (None for
    an anonymous visitor or the app's own work); ``source`` is where the call
    came from (``cluster`` or ``external``).
    """

    __tablename__ = "app_executions"
    __table_args__ = (
        Index("ix_app_executions_app_created", "app_id", "created_at"),
        Index("ix_app_executions_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    visitor_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "llm" or "connector".
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    connector_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    credits: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="succeeded")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class AppTrafficDaily(Base):
    """One UTC day of an app's traffic, as the gateway counted it.

    The host posts running totals for the day; each post is taken into the
    row without lowering a count (the host's counters start again from zero
    when its state is new). ``visitors`` is the number of distinct signed-in
    members that day; a visitor of an ``anyone`` app who is not signed in is
    counted in ``requests`` only.
    """

    __tablename__ = "app_traffic_daily"
    __table_args__ = (
        UniqueConstraint("day", "app_id", name="uq_app_traffic_daily_day_app"),
        Index("ix_app_traffic_daily_app_day", "app_id", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visitors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_4xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_5xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    p95_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppVisit(Base):
    """A member's use of an app: when they last opened it and how many requests they made.

    ``visits`` is all-time. The host reports a member's requests as a running
    total for a day, so ``counted_day`` and ``counted_day_requests`` remember
    what of that day is already in ``visits``: the same report twice adds
    nothing.
    """

    __tablename__ = "app_visits"
    __table_args__ = (
        UniqueConstraint("app_id", "user_id", name="uq_app_visits_app_user"),
        Index("ix_app_visits_user_last", "user_id", "last_visited_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_visited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    visits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    counted_day: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    counted_day_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AppAuditEvent(Base):
    """Something that happened to an app: who did what, or what the host reported.

    ``actor_user_id`` is None for an event the host sent (``host.crashed``,
    ``host.killed``, ...) and for the Portal's own housekeeping.
    """

    __tablename__ = "app_audit_events"
    __table_args__ = (
        Index("ix_app_audit_events_app_created", "app_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    @property
    def detail(self) -> dict:
        return _json_object(self.detail_json)
