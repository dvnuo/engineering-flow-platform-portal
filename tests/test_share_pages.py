"""/share/{token}: the page, its live data and its image attachments."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentSessionMetadata, SessionShare, User
from app.services.auth_service import hash_password


RUNTIME_SESSION = {
    "session_id": "chat-1",
    "name": "Release pipeline permissions",
    "messages": [
        {"id": "u-1", "role": "user", "content": "Why does the deploy stage fail?", "attachments": [
            {"file_id": "f-img", "name": "error.png", "content_type": "image/png", "type": "image"},
        ]},
        {"id": "a-1", "role": "assistant", "content": "The jenkins user lacks the deploy role."},
    ],
    "metadata": {"last_runtime_status": "completed", "system_prompt": "hidden"},
}


def _agent(owner_id, status="running"):
    return Agent(
        name="Deploy helper",
        description="d",
        owner_user_id=owner_id,
        visibility="private",
        status=status,
        image="example/image:latest",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-1",
        service_name="svc-1",
        pvc_name="pvc-1",
        endpoint_path="/",
        agent_type="workspace",
    )


@pytest.fixture
def harness(monkeypatch):
    from app.main import app
    import app.share_pages as pages
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    owner = User(username="owner", nickname="Jin", password_hash=hash_password("pw"), role="user", is_active=True)
    other = User(username="other", password_hash=hash_password("pw"), role="user", is_active=True)
    db.add_all([owner, other])
    db.commit()
    db.refresh(owner)
    db.refresh(other)
    agent = _agent(owner.id)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    share = SessionShare(token="tok_members_12345678901234567890", agent_id=agent.id, session_id="chat-1", owner_user_id=owner.id, scope="members", title="Shared at share time")
    anyone = SessionShare(token="tok_anyone_123456789012345678901", agent_id=agent.id, session_id="chat-2", owner_user_id=owner.id, scope="anyone", title="Anyone title")
    db.add_all([share, anyone])
    db.commit()
    db.refresh(share)
    db.refresh(anyone)

    monkeypatch.setattr(pages, "SessionLocal", TestingSessionLocal)
    pages.clear_runtime_cache()

    viewer = {"value": (None, "missing_session")}
    monkeypatch.setattr(web_module, "_session_user_access", lambda _request: viewer["value"])

    forwarded = []
    runtime = {"status": 200, "payload": RUNTIME_SESSION, "raise": None, "file": (200, b"PNGDATA", "image/png", {"Content-Disposition": "inline; filename=\"error.png\""})}

    async def fake_forward(*, user, agent, method, subpath, query_items, body, headers=None, return_response_headers=False):
        forwarded.append({"user": user, "agent_id": agent.id, "method": method, "subpath": subpath, "query": list(query_items), "headers": return_response_headers})
        if runtime["raise"] is not None:
            raise runtime["raise"]
        if subpath.startswith("api/files/"):
            return runtime["file"]
        content = json.dumps(runtime["payload"]).encode("utf-8") if isinstance(runtime["payload"], dict) else runtime["payload"]
        return runtime["status"], content, "application/json"

    monkeypatch.setattr(web_module, "_forward_runtime", fake_forward)

    import app.services.session_share_service as service

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(session_share_anyone_enabled=True))

    client = TestClient(app)
    try:
        yield SimpleNamespace(
            client=client, db=db, owner=owner, other=other, agent=agent, share=share, anyone=anyone,
            viewer=viewer, forwarded=forwarded, runtime=runtime, pages=pages, service=service,
        )
    finally:
        pages.clear_runtime_cache()
        db.close()


def _sign_in(harness, user):
    harness.viewer["value"] = (user, None)


# --- the page ------------------------------------------------------------------


def test_unknown_token_is_a_404_page(harness):
    response = harness.client.get("/share/nope", follow_redirects=False)
    assert response.status_code == 404
    assert "no longer available" in response.text


def test_members_share_sends_a_visitor_to_sign_in_and_back(harness):
    response = harness.client.get(f"/share/{harness.share.token}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"/login?next=/share/{harness.share.token}"


def test_members_share_blocks_a_member_who_is_not_allowlisted(harness):
    harness.viewer["value"] = (harness.other, "not_allowlisted")
    response = harness.client.get(f"/share/{harness.share.token}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/unauthorized"


def test_members_share_opens_for_a_signed_in_member(harness):
    _sign_in(harness, harness.other)
    response = harness.client.get(f"/share/{harness.share.token}")
    assert response.status_code == 200
    assert "Shared at share time" in response.text  # the stored name until the live one loads
    assert f"/share/{harness.share.token}/data.json" in response.text
    assert "Deploy helper" in response.text
    assert "Jin" in response.text
    assert "share_view.js" in response.text


def test_anyone_share_opens_without_signing_in(harness):
    response = harness.client.get(f"/share/{harness.anyone.token}")
    assert response.status_code == 200
    assert "Anyone title" in response.text


def test_anyone_share_needs_sign_in_when_the_deployment_turns_it_off(harness, monkeypatch):
    monkeypatch.setattr(harness.service, "get_settings", lambda: SimpleNamespace(session_share_anyone_enabled=False))
    response = harness.client.get(f"/share/{harness.anyone.token}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=")


def test_page_prefers_the_runtimes_published_title(harness):
    harness.db.add(AgentSessionMetadata(agent_id=harness.agent.id, session_id="chat-2", metadata_json=json.dumps({"session_title": "Published title"})))
    harness.db.commit()
    response = harness.client.get(f"/share/{harness.anyone.token}")
    assert "Published title" in response.text


# --- the data -----------------------------------------------------------------


def test_data_reads_the_session_live_through_the_agent_context(harness):
    _sign_in(harness, harness.owner)
    response = harness.client.get(f"/share/{harness.share.token}/data.json")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["title"] == "Release pipeline permissions"
    assert body["assistant_name"] == "Deploy helper"
    assert body["owner_display_name"] == "Jin"
    assert body["scope"] == "members"
    assert body["running"] is False
    assert body["can_open_in_portal"] is True
    assert body["portal_url"] == f"/app#/assistants/{harness.agent.id}"
    assert body["message_count"] == 2
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert "system_prompt" not in response.text
    assert response.headers["cache-control"] == "no-store"

    assert harness.forwarded == [{"user": None, "agent_id": harness.agent.id, "method": "GET", "subpath": "api/sessions/chat-1", "query": [], "headers": False}]
    harness.db.refresh(harness.share)
    assert harness.share.view_count == 1
    assert harness.share.last_viewed_at is not None


def test_data_for_a_visitor_on_an_anyone_share(harness):
    response = harness.client.get(f"/share/{harness.anyone.token}/data.json")
    assert response.status_code == 200
    body = response.json()
    assert body["can_open_in_portal"] is False
    assert body["portal_url"] is None
    assert harness.forwarded[0]["subpath"] == "api/sessions/chat-2"


def test_data_requires_sign_in_on_a_members_share(harness):
    response = harness.client.get(f"/share/{harness.share.token}/data.json")
    assert response.status_code == 401
    assert response.json()["login_url"] == f"/login?next=/share/{harness.share.token}"
    assert harness.forwarded == []


def test_data_reports_a_stopped_assistant_without_calling_it(harness):
    harness.agent.status = "stopped"
    harness.db.commit()
    response = harness.client.get(f"/share/{harness.anyone.token}/data.json")
    body = response.json()
    assert body["status"] == "assistant_stopped"
    assert body["title"] == "Anyone title"
    assert body["messages"] == []
    assert harness.forwarded == []


def test_data_reports_a_deleted_session_and_a_runtime_error(harness):
    harness.runtime["status"] = 404
    assert harness.client.get(f"/share/{harness.anyone.token}/data.json").json()["status"] == "session_missing"

    harness.pages.clear_runtime_cache()
    harness.runtime["status"] = 500
    harness.runtime["payload"] = b'{"error": "boom"}'
    body = harness.client.get(f"/share/{harness.anyone.token}/data.json").json()
    assert body["status"] == "runtime_unreachable"
    assert "boom" in body["error"]

    harness.runtime["raise"] = RuntimeError("connection refused")
    body = harness.client.get(f"/share/{harness.anyone.token}/data.json").json()
    assert body["status"] == "runtime_unreachable"


def test_data_marks_a_session_still_running(harness):
    harness.runtime["payload"] = {**RUNTIME_SESSION, "metadata": {"last_runtime_status": "running"}}
    body = harness.client.get(f"/share/{harness.anyone.token}/data.json").json()
    assert body["running"] is True


def test_data_is_served_from_a_short_cache_unless_fresh(harness):
    url = f"/share/{harness.anyone.token}/data.json"
    harness.client.get(url)
    harness.client.get(url)
    assert len(harness.forwarded) == 1
    harness.client.get(url + "?fresh=1")
    assert len(harness.forwarded) == 2


# --- image attachments ---------------------------------------------------------


def test_file_route_serves_an_image_the_transcript_shows(harness):
    response = harness.client.get(f"/share/{harness.anyone.token}/files/f-img")
    assert response.status_code == 200
    assert response.content == b"PNGDATA"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["x-content-type-options"] == "nosniff"
    file_call = [call for call in harness.forwarded if call["subpath"] == "api/files/f-img"][0]
    assert file_call["query"] == [("session_id", "chat-2")]
    assert file_call["headers"] is True


def test_file_route_refuses_anything_else(harness):
    assert harness.client.get(f"/share/{harness.anyone.token}/files/not-in-transcript").status_code == 404

    harness.runtime["file"] = (200, b"<html>", "text/html", {})
    assert harness.client.get(f"/share/{harness.anyone.token}/files/f-img").status_code == 404

    harness.runtime["file"] = (404, b"", "text/plain", {})
    assert harness.client.get(f"/share/{harness.anyone.token}/files/f-img").status_code == 404

    assert harness.client.get(f"/share/{harness.share.token}/files/f-img").status_code == 401
    assert harness.client.get("/share/nope/files/f-img").status_code == 404


def test_file_route_is_404_while_the_assistant_is_stopped(harness):
    harness.agent.status = "stopped"
    harness.db.commit()
    assert harness.client.get(f"/share/{harness.anyone.token}/files/f-img").status_code == 404
