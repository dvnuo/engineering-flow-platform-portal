from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.external_event_subscription import ExternalEventSubscription
from typing import Optional


class ExternalEventSubscriptionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _normalize_source_type(source_type: str) -> str:
        return (source_type or "").strip().lower()

    @staticmethod
    def _normalize_mode(mode: str | None) -> str | None:
        cleaned = (mode or "").strip().lower()
        return cleaned or None

    @staticmethod
    def _derive_source_kind(*, source_type: str | None, event_type: str | None, source_kind: str | None) -> str | None:
        if source_kind and source_kind.strip():
            return source_kind.strip()
        normalized_source = (source_type or "").strip().lower()
        cleaned_event = (event_type or "").strip()
        if not normalized_source or not cleaned_event:
            return None
        return f"{normalized_source}.{cleaned_event}"

    def create(self, **kwargs) -> ExternalEventSubscription:
        if "source_type" in kwargs:
            kwargs["source_type"] = self._normalize_source_type(kwargs["source_type"])
        kwargs["mode"] = self._normalize_mode(kwargs.get("mode")) or "push"
        kwargs["source_kind"] = self._derive_source_kind(
            source_type=kwargs.get("source_type"),
            event_type=kwargs.get("event_type"),
            source_kind=kwargs.get("source_kind"),
        )
        subscription = ExternalEventSubscription(**kwargs)
        self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def get_by_id(self, subscription_id: str) -> Optional[ExternalEventSubscription]:
        return self.db.get(ExternalEventSubscription, subscription_id)

    def list_all(self) -> list[ExternalEventSubscription]:
        return list(self.db.scalars(select(ExternalEventSubscription).order_by(ExternalEventSubscription.created_at.desc())).all())

    def list_by_agent(self, agent_id: str) -> list[ExternalEventSubscription]:
        stmt = (
            select(ExternalEventSubscription)
            .where(ExternalEventSubscription.agent_id == agent_id)
            .order_by(ExternalEventSubscription.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_filtered(
        self,
        *,
        agent_id: str | None = None,
        source_type: str | None = None,
        event_type: str | None = None,
        enabled: bool | None = None,
        mode: str | None = None,
        source_kind: str | None = None,
    ) -> list[ExternalEventSubscription]:
        normalized_source_type = self._normalize_source_type(source_type)
        normalized_mode = self._normalize_mode(mode)
        normalized_source_kind = (source_kind or "").strip()
        stmt = select(ExternalEventSubscription)
        if agent_id:
            stmt = stmt.where(ExternalEventSubscription.agent_id == agent_id)
        if normalized_source_type:
            stmt = stmt.where(ExternalEventSubscription.source_type == normalized_source_type)
        if event_type:
            stmt = stmt.where(ExternalEventSubscription.event_type == event_type)
        if enabled is not None:
            stmt = stmt.where(ExternalEventSubscription.enabled.is_(enabled))
        if normalized_mode:
            stmt = stmt.where(ExternalEventSubscription.mode == normalized_mode)
        if normalized_source_kind:
            stmt = stmt.where(ExternalEventSubscription.source_kind == normalized_source_kind)
        stmt = stmt.order_by(ExternalEventSubscription.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def list_enabled_for_source(self, source_type: str, event_type: str | None = None) -> list[ExternalEventSubscription]:
        normalized_source_type = self._normalize_source_type(source_type)
        filters = [
            ExternalEventSubscription.source_type == normalized_source_type,
            ExternalEventSubscription.enabled.is_(True),
        ]
        if event_type is not None:
            filters.append(ExternalEventSubscription.event_type == event_type)

        stmt = select(ExternalEventSubscription).where(and_(*filters)).order_by(ExternalEventSubscription.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def list_enabled_for_agent_modes(self, agent_id: str, modes: list[str]) -> list[ExternalEventSubscription]:
        normalized_modes = [normalized for mode in modes for normalized in [self._normalize_mode(mode)] if normalized]
        if not normalized_modes:
            return []
        stmt = (
            select(ExternalEventSubscription)
            .where(
                and_(
                    ExternalEventSubscription.agent_id == agent_id,
                    ExternalEventSubscription.enabled.is_(True),
                    ExternalEventSubscription.mode.in_(normalized_modes),
                )
            )
            .order_by(ExternalEventSubscription.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def save(self, subscription: ExternalEventSubscription) -> ExternalEventSubscription:
        if subscription.source_type:
            subscription.source_type = self._normalize_source_type(subscription.source_type)
        subscription.mode = self._normalize_mode(subscription.mode) or "push"
        subscription.source_kind = self._derive_source_kind(
            source_type=subscription.source_type,
            event_type=subscription.event_type,
            source_kind=subscription.source_kind,
        )
        self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def delete(self, subscription: ExternalEventSubscription) -> None:
        self.db.delete(subscription)
        self.db.commit()
