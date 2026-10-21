"""POST/GET/DELETE /api/agents/{agent}/sessions/{session}/share."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import Agent, AuditLog, SessionShare, User
from app.services.auth_service import hash_password


def _agent(owner_id, name, visibility="private"):
    return Agent(
        name=name,
        description=name,
        owner_user_id=owner_id,
        visibility=visibility,
        status="running",
        image="example/image:latest",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name=f"dep-{name}",
        service_name=f"svc-{name}",
        pvc_name=f"pvc-{name}",
        endpoint_path="/",
        agent_type="workspace",
    )


@pytest.fixture
def harness(monkeypatch):
    from app.main import app
    import app.api.session_shares as api_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    users = {
        "owner": User(username="owner", password_hash=hash_password("pw"), role="user", is_active=True),
        "other": User(username="other", password_hash=hash_password("pw"), role="user", is_active=True),
        "admin": User(username="admin", password_hash=hash_password("pw"), role="admin", is_active=True),
    }
    db.add_all(users.values())
    db.commit()
    for user in users.values():
        db.refresh(user)
    private_agent = _agent(users["owner"].id, "private")
    public_agent = _agent(users["owner"].id, "public", visibility="public")
    db.add_all([private_agent, public_agent])
    db.commit()
    db.refresh(private_agent)
    db.refresh(public_agent)

    current = {"user": users["owner"]}

    def override_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    monkeypatch.setattr(api_module, "get_settings", lambda: SimpleNamespace(base_uri="https://portal.example.com", session_share_anyone_enabled=True))
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, db=db, users=users, private_agent=private_agent, public_agent=public_agent, current=current, api_module=api_module)
    finally:
        app.dependency_overrides.clear()
        db.close()


def _url(agent_id, session_id="chat-1"):
    return f"/api/agents/{agent_id}/sessions/{session_id}/share"


def test_owner_shares_reads_updates_and_revokes(harness):
    client = harness.client
    url = _url(harness.private_agent.id)

    assert client.get(url).status_code == 404

    created = client.post(url, json={"scope": "members", "title": "Release pipeline permissions"})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["scope"] == "members"
    assert body["title"] == "Release pipeline permissions"
    assert body["url"] == f"https://portal.example.com/share/{body['token']}"
    assert body["anyone_enabled"] is True
    assert len(body["token"]) >= 32

    fetched = client.get(url).json()
    assert fetched["token"] == body["token"]

    updated = client.post(url, json={"scope": "anyone"})
    assert updated.status_code == 200
    assert updated.json()["token"] == body["token"]  # the link survives an audience change
    assert updated.json()["scope"] == "anyone"
    assert updated.json()["title"] == "Release pipeline permissions"  # kept when not resent

    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 404

    actions = [row.action for row in harness.db.query(AuditLog).order_by(AuditLog.id).all()]
    assert actions == ["share_session", "update_session_share", "unshare_session"]
    assert harness.db.query(SessionShare).count() == 0


def test_only_the_owner_or_an_admin_manages_shares(harness):
    client = harness.client
    harness.current["user"] = harness.users["other"]
    # A public assistant is readable by every member, but its sessions are
    # the owner's to publish.
    assert client.post(_url(harness.public_agent.id), json={"scope": "members"}).status_code == 403
    assert client.get(_url(harness.public_agent.id)).status_code == 403
    assert client.post(_url(harness.private_agent.id), json={"scope": "members"}).status_code == 403

    harness.current["user"] = harness.users["admin"]
    assert client.post(_url(harness.private_agent.id), json={"scope": "members"}).status_code == 200


def test_unknown_agent_is_404(harness):
    assert harness.client.post(_url("missing"), json={"scope": "members"}).status_code == 404


@pytest.mark.parametrize("payload, detail", [
    ({"scope": "public"}, "scope must be one of"),
])
def test_bad_scope_is_400(harness, payload, detail):
    response = harness.client.post(_url(harness.private_agent.id), json=payload)
    assert response.status_code == 400
    assert detail in response.json()["detail"]


@pytest.mark.parametrize("session_id", ["task-123", "agent-task:abc", "agent-task-abc", "generic-task:1", "delegation:9", "delegation-9"])
def test_task_sessions_cannot_be_shared(harness, session_id):
    response = harness.client.post(_url(harness.private_agent.id, session_id), json={"scope": "members"})
    assert response.status_code == 400
    assert "Task sessions" in response.json()["detail"]


def test_a_long_session_name_is_cut_to_the_column_not_refused(harness):
    response = harness.client.post(_url(harness.private_agent.id), json={"scope": "members", "title": "  x" + "y" * 400})
    assert response.status_code == 200, response.text
    assert len(response.json()["title"]) == 255
    assert response.json()["title"].startswith("xy")


def test_revoking_forgets_the_cached_transcript(harness, monkeypatch):
    forgotten = []
    monkeypatch.setattr(harness.api_module, "invalidate_share_transcript", forgotten.append)
    url = _url(harness.private_agent.id)
    token = harness.client.post(url, json={"scope": "members"}).json()["token"]
    assert harness.client.delete(url).status_code == 204
    assert forgotten == [token]


def test_anyone_can_be_turned_off_per_deployment(harness, monkeypatch):
    monkeypatch.setattr(harness.api_module, "get_settings", lambda: SimpleNamespace(base_uri="", session_share_anyone_enabled=False))
    import app.services.session_share_service as service

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(session_share_anyone_enabled=False))
    response = harness.client.post(_url(harness.private_agent.id), json={"scope": "anyone"})
    assert response.status_code == 400
    members = harness.client.post(_url(harness.private_agent.id), json={"scope": "members"})
    assert members.status_code == 200
    assert members.json()["anyone_enabled"] is False
    # Without a configured base URI the link is built from the request.
    assert members.json()["url"].startswith("http://testserver/share/")
