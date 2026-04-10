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

    def create(self, **kwargs) -> ExternalEventSubscription:
        if "source_type" in kwargs:
            kwargs["source_type"] = self._normalize_source_type(kwargs["source_type"])
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

    def save(self, subscription: ExternalEventSubscription) -> ExternalEventSubscription:
        self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def delete(self, subscription: ExternalEventSubscription) -> None:
        self.db.delete(subscription)
        self.db.commit()
