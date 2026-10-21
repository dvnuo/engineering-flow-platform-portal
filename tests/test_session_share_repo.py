from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.repositories.session_share_repo import SessionShareRepository, new_share_token


def _repo():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    return SessionShareRepository(db), db


def test_tokens_are_long_and_distinct():
    tokens = {new_share_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(token) >= 32 for token in tokens)


def test_create_lookup_update_and_revoke():
    repo, db = _repo()
    try:
        share = repo.create(agent_id="a1", session_id="s1", owner_user_id=7, scope="members", title="First turn")
        assert share.id and share.token
        assert share.scope == "members"

        assert repo.get_by_token(share.token).id == share.id
        assert repo.get_by_token("  ") is None
        assert repo.get_by_token("missing") is None
        assert repo.get_by_agent_and_session("a1", "s1").id == share.id
        assert repo.get_by_agent_and_session("a1", "other") is None
        assert repo.shared_session_ids("a1") == {"s1"}
        assert repo.shared_session_ids("a2") == set()

        updated = repo.update(share, scope="anyone", title="Renamed")
        assert updated.scope == "anyone"
        assert updated.title == "Renamed"
        repo.update(share, scope="members")
        assert repo.get_by_token(share.token).title == "Renamed"

        # The revoked token comes back so the caller can drop its cached transcript.
        assert repo.delete_for_session("a1", "s1") == share.token
        assert repo.delete_for_session("a1", "s1") is None
        assert repo.get_by_token(share.token) is None
    finally:
        db.close()


def test_delete_for_agent_drops_every_share_of_that_assistant():
    repo, db = _repo()
    try:
        first = repo.create(agent_id="a1", session_id="s1", owner_user_id=1, scope="members")
        second = repo.create(agent_id="a1", session_id="s2", owner_user_id=1, scope="anyone")
        keep = repo.create(agent_id="a2", session_id="s1", owner_user_id=1, scope="members")
        assert sorted(repo.delete_for_agent("a1")) == sorted([first.token, second.token])
        assert repo.delete_for_agent("a1") == []
        assert repo.list_by_agent("a1") == []
        assert [share.id for share in repo.list_by_agent("a2")] == [keep.id]
    finally:
        db.close()
