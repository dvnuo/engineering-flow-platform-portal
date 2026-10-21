"""The runtime tells the Portal a session's name; the Portal shows it where the live name cannot be read."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.models import Agent, AgentSessionMetadata, User
from app.services.auth_service import hash_password
from app.services.session_context_preview import extract_context_preview, merge_runtime_sessions_with_metadata


@pytest.fixture
def harness():
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = Agent(
        name="A", description="", owner_user_id=user.id, visibility="private", status="running", image="x",
        cpu="1", memory="1Gi", disk_size_gi=20, mount_path="/root/.efp", namespace="n", deployment_name="d",
        service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    def override_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, db=db, agent=agent)
    finally:
        app.dependency_overrides.clear()
        db.close()


def _url(agent_id, session_id="chat-1"):
    return f"/api/internal/agents/{agent_id}/sessions/{session_id}/title"


def test_patch_creates_the_row_with_only_the_title(harness):
    response = harness.client.patch(_url(harness.agent.id), json={"title": "  Release pipeline permissions "})
    assert response.status_code == 200
    assert response.json() == {"success": True, "agent_id": harness.agent.id, "session_id": "chat-1", "title": "Release pipeline permissions"}
    row = harness.db.query(AgentSessionMetadata).filter_by(agent_id=harness.agent.id, session_id="chat-1").one()
    assert json.loads(row.metadata_json) == {"session_title": "Release pipeline permissions"}
    assert row.latest_event_state is None


def test_patch_keeps_the_rest_of_the_metadata(harness):
    harness.db.add(AgentSessionMetadata(
        agent_id=harness.agent.id, session_id="chat-1", latest_event_state="success",
        metadata_json=json.dumps({"context_state": {"objective": "o"}, "session_title": "old"}),
    ))
    harness.db.commit()
    assert harness.client.patch(_url(harness.agent.id), json={"title": "new"}).status_code == 200
    harness.db.expire_all()
    row = harness.db.query(AgentSessionMetadata).filter_by(agent_id=harness.agent.id, session_id="chat-1").one()
    assert json.loads(row.metadata_json) == {"context_state": {"objective": "o"}, "session_title": "new"}
    assert row.latest_event_state == "success"


def test_patch_rejects_an_empty_title_and_an_unknown_agent(harness):
    assert harness.client.patch(_url(harness.agent.id), json={"title": "   "}).status_code == 400
    assert harness.client.patch(_url("missing"), json={"title": "x"}).status_code == 404


def _metadata_url(agent_id, session_id="chat-1"):
    return f"/api/internal/agents/{agent_id}/sessions/{session_id}/metadata"


def _row(harness, session_id="chat-1"):
    harness.db.expire_all()
    return harness.db.query(AgentSessionMetadata).filter_by(agent_id=harness.agent.id, session_id=session_id).one()


def test_full_publish_keeps_a_title_it_does_not_carry(harness):
    assert harness.client.patch(_url(harness.agent.id), json={"title": "Named"}).status_code == 200
    # chat.started / chat.failed publish without session_title: the name
    # the runtime sent on its own must survive them.
    response = harness.client.put(
        _metadata_url(harness.agent.id),
        json={"latest_event_state": "running", "metadata_json": json.dumps({"context_state": {"objective": "o"}})},
    )
    assert response.status_code == 200
    row = _row(harness)
    assert json.loads(row.metadata_json) == {"context_state": {"objective": "o"}, "session_title": "Named"}
    assert row.latest_event_state == "running"


def test_full_publish_with_its_own_title_replaces_the_stored_one(harness):
    assert harness.client.patch(_url(harness.agent.id), json={"title": "Old"}).status_code == 200
    response = harness.client.put(_metadata_url(harness.agent.id), json={"metadata_json": json.dumps({"session_title": "New"})})
    assert response.status_code == 200
    assert json.loads(_row(harness).metadata_json) == {"session_title": "New"}


def test_full_publish_without_metadata_keeps_the_title(harness):
    assert harness.client.patch(_url(harness.agent.id), json={"title": "Named"}).status_code == 200
    response = harness.client.put(_metadata_url(harness.agent.id), json={"latest_event_state": "success"})
    assert response.status_code == 200
    assert json.loads(_row(harness).metadata_json) == {"session_title": "Named"}


def test_full_publish_of_an_unnamed_session_stores_what_it_carries(harness):
    response = harness.client.put(_metadata_url(harness.agent.id), json={"metadata_json": json.dumps({"context_state": {}})})
    assert response.status_code == 200
    assert json.loads(_row(harness).metadata_json) == {"context_state": {}}
    response = harness.client.put(_metadata_url(harness.agent.id, "chat-2"), json={"latest_event_state": "success"})
    assert response.status_code == 200
    assert _row(harness, "chat-2").metadata_json is None


def test_preview_and_metadata_only_rows_carry_the_title():
    record = SimpleNamespace(
        session_id="chat-9", metadata_json=json.dumps({"session_title": " Title from runtime "}),
        latest_event_state="success", snapshot_version=None, deleted_at=None, updated_at=None,
    )
    assert extract_context_preview(record)["session_title"] == "Title from runtime"

    rows = merge_runtime_sessions_with_metadata([], [record], include_metadata_only=True)
    assert rows[0]["name"] == "Title from runtime"
    assert rows[0]["is_metadata_only"] is True

    unnamed = SimpleNamespace(session_id="chat-10", metadata_json="{}", latest_event_state=None, snapshot_version=None, deleted_at=None, updated_at=None)
    assert merge_runtime_sessions_with_metadata([], [unnamed], include_metadata_only=True)[0]["name"] == "chat-10"
