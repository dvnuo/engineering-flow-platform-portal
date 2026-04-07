from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.runtime_capability_catalog_snapshot import RuntimeCapabilityCatalogSnapshot


class RuntimeCapabilityCatalogSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> RuntimeCapabilityCatalogSnapshot:
        snapshot = RuntimeCapabilityCatalogSnapshot(**kwargs)
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def get_latest(self) -> RuntimeCapabilityCatalogSnapshot | None:
        stmt = select(RuntimeCapabilityCatalogSnapshot).order_by(RuntimeCapabilityCatalogSnapshot.fetched_at.desc())
        return self.db.scalars(stmt).first()

    def get_latest_for_agent(self, agent_id: str) -> RuntimeCapabilityCatalogSnapshot | None:
        stmt = (
            select(RuntimeCapabilityCatalogSnapshot)
            .where(RuntimeCapabilityCatalogSnapshot.source_agent_id == agent_id)
            .order_by(RuntimeCapabilityCatalogSnapshot.fetched_at.desc())
        )
        return self.db.scalars(stmt).first()
