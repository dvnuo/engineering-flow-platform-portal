from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository


def _repo():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    return AgentSessionMetadataRepository(db), db


def test_mark_deleted_and_include_deleted_filters():
    repo, db = _repo()
    try:
        repo.upsert(agent_id="a1", session_id="s1", latest_event_state="running")
        record, already = repo.mark_deleted("a1", "s1")
        assert already is False
        assert record.deleted_at is not None
        assert repo.get_by_agent_and_session("a1", "s1") is None
        assert repo.get_by_agent_and_session("a1", "s1", include_deleted=True) is not None
        assert all(item.session_id != "s1" for item in repo.list_by_agent("a1"))
    finally:
        db.close()


def test_mark_deleted_missing_creates_tombstone_and_upsert_does_not_revive():
    repo, db = _repo()
    try:
        record, already = repo.mark_deleted("a1", "missing")
        assert already is False
        assert record.deleted_at is not None
        assert repo.list_by_agent("a1") == []
        repo.upsert(agent_id="a1", session_id="missing", latest_event_state="running")
        assert repo.list_by_agent("a1") == []
        included = repo.list_by_agent_and_session_ids("a1", ["missing"], include_deleted=True)
        assert len(included) == 1 and included[0].deleted_at is not None
    finally:
        db.close()


def test_upsert_deleted_at_none_does_not_revive_tombstone():
    repo, db = _repo()
    try:
        repo.upsert(agent_id="a1", session_id="s1", latest_event_state="running")
        repo.mark_deleted("a1", "s1")
        repo.upsert(agent_id="a1", session_id="s1", deleted_at=None, latest_event_state="running")
        assert repo.list_by_agent("a1") == []
        item = repo.get_by_agent_and_session("a1", "s1", include_deleted=True)
        assert item is not None and item.deleted_at is not None
    finally:
        db.close()

def test_upsert_allow_reactivate_explicitly_restores_only_when_requested():
    repo, db = _repo()
    try:
        repo.upsert(agent_id="a1", session_id="s2", latest_event_state="running")
        repo.mark_deleted("a1", "s2")
        repo.upsert(agent_id="a1", session_id="s2", deleted_at=None, allow_reactivate=True, latest_event_state="running")
        assert any(item.session_id == "s2" for item in repo.list_by_agent("a1"))
    finally:
        db.close()
