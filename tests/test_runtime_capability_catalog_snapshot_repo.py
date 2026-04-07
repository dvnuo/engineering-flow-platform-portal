from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.repositories.runtime_capability_catalog_snapshot_repo import RuntimeCapabilityCatalogSnapshotRepository


def test_get_latest_for_agent_returns_latest_matching_agent_snapshot():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        repo = RuntimeCapabilityCatalogSnapshotRepository(db)
        older = repo.create(
            source_agent_id="agent-1",
            catalog_version="v1",
            catalog_source="runtime_api",
            payload_json="[]",
            fetched_at=datetime.utcnow() - timedelta(hours=1),
        )
        latest = repo.create(
            source_agent_id="agent-1",
            catalog_version="v2",
            catalog_source="runtime_api",
            payload_json="[]",
            fetched_at=datetime.utcnow(),
        )
        repo.create(
            source_agent_id="agent-2",
            catalog_version="v-other",
            catalog_source="runtime_api",
            payload_json="[]",
            fetched_at=datetime.utcnow() + timedelta(minutes=1),
        )
        assert repo.get_latest_for_agent("agent-1").id == latest.id
        assert repo.get_latest_for_agent("agent-1").id != older.id
    finally:
        db.close()
