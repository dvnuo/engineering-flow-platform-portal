from datetime import date, datetime
import json
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.app import App, AppAuditEvent, AppRelease, AppTemplateVersion, AppTrafficDaily, AppVisit

RELEASE_ACTIVE_STATUSES = ("queued", "running")


class AppRepository:
    """``apps`` rows. A deleted app keeps its row (its slug is never reused) and is left out unless asked for."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, app_id: str, *, include_deleted: bool = False) -> Optional[App]:
        app = self.db.get(App, str(app_id or ""))
        if app is None or (app.deleted_at is not None and not include_deleted):
            return None
        return app

    def get_by_slug(self, slug: str, *, include_deleted: bool = False) -> Optional[App]:
        normalized = str(slug or "").strip()
        if not normalized:
            return None
        app = self.db.scalar(select(App).where(App.slug == normalized))
        if app is None or (app.deleted_at is not None and not include_deleted):
            return None
        return app

    def slug_exists(self, slug: str) -> bool:
        """Whether any app, deleted ones included, has ever had this slug."""
        return self.db.scalar(select(func.count(App.id)).where(App.slug == str(slug or "").strip())) > 0

    def get_by_token_hash(self, token_hash: str) -> Optional[App]:
        if not token_hash:
            return None
        return self.db.scalar(select(App).where(App.token_hash == token_hash, App.deleted_at.is_(None)))

    def _search(self, statement, q: Optional[str]):
        needle = str(q or "").strip().lower()
        if not needle:
            return statement
        pattern = f"%{needle}%"
        return statement.where(
            or_(
                func.lower(App.name).like(pattern),
                func.lower(App.slug).like(pattern),
                func.lower(func.coalesce(App.description, "")).like(pattern),
            )
        )

    def list_by_owner(self, owner_user_id: int, *, q: Optional[str] = None) -> list[App]:
        statement = select(App).where(App.owner_user_id == owner_user_id, App.deleted_at.is_(None))
        return list(self.db.scalars(self._search(statement, q).order_by(App.updated_at.desc(), App.name.asc())).all())

    def list_where(self, clause=None, *, q: Optional[str] = None) -> list[App]:
        """Every app that is not deleted, narrowed by ``clause`` (a SQLAlchemy filter or None) and the search text."""
        statement = select(App).where(App.deleted_at.is_(None))
        if clause is not None:
            statement = statement.where(clause)
        return list(self.db.scalars(self._search(statement, q).order_by(func.lower(App.name).asc(), App.created_at.asc())).all())

    def list_all(self, *, include_deleted: bool = False) -> list[App]:
        statement = select(App)
        if not include_deleted:
            statement = statement.where(App.deleted_at.is_(None))
        return list(self.db.scalars(statement.order_by(App.created_at.asc())).all())

    def save(self, app: App) -> App:
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        return app


class AppReleaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, release_id: str) -> Optional[AppRelease]:
        return self.db.get(AppRelease, str(release_id or ""))

    def get_for_app(self, app_id: str, release_id: str) -> Optional[AppRelease]:
        release = self.get(release_id)
        return release if release is not None and release.app_id == app_id else None

    def list_for_app(self, app_id: str, *, limit: int = 50) -> list[AppRelease]:
        return list(
            self.db.scalars(
                select(AppRelease)
                .where(AppRelease.app_id == app_id)
                .order_by(AppRelease.started_at.desc(), AppRelease.id.desc())
                .limit(max(1, int(limit)))
            ).all()
        )

    def latest_for_app(self, app_id: str) -> Optional[AppRelease]:
        rows = self.list_for_app(app_id, limit=1)
        return rows[0] if rows else None

    def active_for_app(self, app_id: str) -> Optional[AppRelease]:
        """The row of this app that is marked queued or running, if there is one.

        The row as it is stored: whether anybody still works on it is the
        deployments' question (``app.services.apps.deploy.active_release``).
        """
        return self.db.scalars(
            select(AppRelease).where(AppRelease.app_id == app_id, AppRelease.status.in_(RELEASE_ACTIVE_STATUSES))
        ).first()

    def latest_with_sha(self, app_id: str, commit_sha: str) -> Optional[AppRelease]:
        """The newest release of this app that was for ``commit_sha``, whatever its outcome."""
        return self.db.scalars(
            select(AppRelease)
            .where(AppRelease.app_id == app_id, AppRelease.commit_sha == commit_sha)
            .order_by(AppRelease.started_at.desc(), AppRelease.id.desc())
        ).first()

    def list_active(self) -> list[AppRelease]:
        return list(self.db.scalars(select(AppRelease).where(AppRelease.status.in_(RELEASE_ACTIVE_STATUSES))).all())

    def has_any(self, app_id: str) -> bool:
        return self.db.scalar(select(func.count(AppRelease.id)).where(AppRelease.app_id == app_id)) > 0

    def previous_succeeded(self, app_id: str, *, current_release_id: Optional[str], current_sha: Optional[str]) -> Optional[AppRelease]:
        """The last successful release before the current one that ran other code: what a rollback returns to."""
        statement = select(AppRelease).where(
            AppRelease.app_id == app_id,
            AppRelease.status == "succeeded",
            AppRelease.commit_sha.is_not(None),
        )
        if current_release_id:
            statement = statement.where(AppRelease.id != current_release_id)
        if current_sha:
            statement = statement.where(AppRelease.commit_sha != current_sha)
        return self.db.scalars(statement.order_by(AppRelease.finished_at.desc(), AppRelease.started_at.desc())).first()

    def create(self, **fields: Any) -> AppRelease:
        release = AppRelease(**fields)
        self.db.add(release)
        self.db.commit()
        self.db.refresh(release)
        return release

    def save(self, release: AppRelease) -> AppRelease:
        self.db.add(release)
        self.db.commit()
        self.db.refresh(release)
        return release


class AppTemplateVersionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, version_id: str) -> Optional[AppTemplateVersion]:
        return self.db.get(AppTemplateVersion, str(version_id or ""))

    def list_for_app(self, app_id: str) -> list[AppTemplateVersion]:
        return list(
            self.db.scalars(
                select(AppTemplateVersion)
                .where(AppTemplateVersion.app_id == app_id)
                .order_by(AppTemplateVersion.published_at.desc())
            ).all()
        )


class AppAuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        app_id: str,
        action: str,
        *,
        actor_user_id: Optional[int] = None,
        detail: Optional[dict] = None,
        commit: bool = True,
    ) -> AppAuditEvent:
        event = AppAuditEvent(
            app_id=app_id,
            actor_user_id=actor_user_id,
            action=str(action)[:64],
            detail_json=json.dumps(detail, ensure_ascii=True, default=str) if detail else None,
        )
        self.db.add(event)
        if commit:
            self.db.commit()
            self.db.refresh(event)
        return event

    def list_for_app(self, app_id: str, *, limit: int = 100) -> list[AppAuditEvent]:
        return list(
            self.db.scalars(
                select(AppAuditEvent)
                .where(AppAuditEvent.app_id == app_id)
                .order_by(AppAuditEvent.created_at.desc(), AppAuditEvent.id.desc())
                .limit(max(1, int(limit)))
            ).all()
        )


class AppTrafficRepository:
    """``app_traffic_daily`` and ``app_visits``: what the gateway counted. Nothing here commits; the caller does, once."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_day(
        self,
        day: date,
        app_id: str,
        *,
        requests: int,
        visitors: int,
        errors_4xx: int,
        errors_5xx: int,
        p95_ms: int,
    ) -> AppTrafficDaily:
        """Take the day's totals as the host sent them, without ever lowering a count.

        The host sends running totals for the day, so a later post normally
        carries larger figures. Its counters start again from zero when its
        state file is new (a new volume, a reset), and the day's totals must
        not fall with them: each count keeps the larger of what is stored and
        what arrived. The 95th percentile is the incoming one, unless that is
        zero (nothing measured since the restart).
        """
        row = self.db.scalar(select(AppTrafficDaily).where(AppTrafficDaily.day == day, AppTrafficDaily.app_id == app_id))
        if row is None:
            row = AppTrafficDaily(day=day, app_id=app_id, requests=0, visitors=0, errors_4xx=0, errors_5xx=0, p95_ms=0)
        row.requests = max(int(row.requests or 0), int(requests))
        row.visitors = max(int(row.visitors or 0), int(visitors))
        row.errors_4xx = max(int(row.errors_4xx or 0), int(errors_4xx))
        row.errors_5xx = max(int(row.errors_5xx or 0), int(errors_5xx))
        row.p95_ms = int(p95_ms) if p95_ms else int(row.p95_ms or 0)
        row.updated_at = datetime.utcnow()
        self.db.add(row)
        # The session does not autoflush: the next lookup of the same key must see this row.
        self.db.flush()
        return row

    def upsert_visit(self, app_id: str, user_id: int, *, day: date, requests: int, last_at: Optional[datetime]) -> AppVisit:
        """Count a member's requests of ``day`` (a running total) into their all-time figure, once.

        The newest day a row has seen is the one it tracks: a later total for
        that day adds only the difference, the same total again adds nothing,
        and a report for an older day only moves ``last_visited_at``.
        """
        row = self.db.scalar(select(AppVisit).where(AppVisit.app_id == app_id, AppVisit.user_id == user_id))
        if row is None:
            row = AppVisit(app_id=app_id, user_id=user_id, visits=0, counted_day=None, counted_day_requests=0)
        requests = max(0, int(requests))
        if row.counted_day is None or day > row.counted_day:
            row.visits = int(row.visits or 0) + requests
            row.counted_day = day
            row.counted_day_requests = requests
        elif day == row.counted_day and requests > int(row.counted_day_requests or 0):
            row.visits = int(row.visits or 0) + requests - int(row.counted_day_requests or 0)
            row.counted_day_requests = requests
        if last_at is not None and (row.last_visited_at is None or last_at > row.last_visited_at):
            row.last_visited_at = last_at
        self.db.add(row)
        self.db.flush()
        return row

    def last_visits_of_user(self, user_id: int, app_ids: Optional[Iterable[str]] = None) -> dict[str, datetime]:
        """``{app id: when the member last used it}``."""
        statement = select(AppVisit.app_id, AppVisit.last_visited_at).where(
            AppVisit.user_id == user_id, AppVisit.last_visited_at.is_not(None)
        )
        if app_ids is not None:
            ids = list(app_ids)
            if not ids:
                return {}
            statement = statement.where(AppVisit.app_id.in_(ids))
        return {app_id: last_at for app_id, last_at in self.db.execute(statement).all()}
