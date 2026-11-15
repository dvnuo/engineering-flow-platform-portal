"""/api/internal/* is for an assistant's own runtime, calling from inside the cluster."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.models import Agent, User
from app.security import internal_api
from app.security.internal_api import ClusterOnlyApiMiddleware
from app.services.agent_trust import agent_trust_token
from app.services.auth_service import hash_password


def _agent(owner_id: int, name: str) -> Agent:
    return Agent(
        name=name, description="", owner_user_id=owner_id, visibility="private", status="running", image="x",
        cpu="1", memory="1Gi", disk_size_gi=20, mount_path="/root/.efp", namespace="n", deployment_name=f"d-{name}",
        service_name=f"s-{name}", pvc_name=f"p-{name}", endpoint_path="/", agent_type="workspace",
    )


@pytest.fixture
def harness(monkeypatch):
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent, other = _agent(user.id, "a"), _agent(user.id, "b")
    db.add_all([agent, other])
    db.commit()
    db.refresh(agent)
    db.refresh(other)

    def override_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    # The token is derived from the key get_settings() answers with; the two
    # switches are read from the object the module captured at import.
    monkeypatch.setattr(get_settings(), "secret_key", "unit-test-secret-key")
    settings = internal_api.settings
    monkeypatch.setattr(settings, "internal_api_auth_required", False)
    monkeypatch.setattr(settings, "internal_api_block_forwarded", True)
    monkeypatch.setattr(internal_api, "_unauthenticated_logged", set())
    monkeypatch.setattr(internal_api, "_forwarded_block_logged", False)
    app.dependency_overrides[get_db] = override_db
    try:
        yield SimpleNamespace(client=TestClient(app), agent=agent, other=other, settings=settings)
    finally:
        app.dependency_overrides.clear()
        db.close()


def _title_url(agent_id: str) -> str:
    return f"/api/internal/agents/{agent_id}/sessions/chat-1/title"


def _metadata_url(agent_id: str) -> str:
    return f"/api/internal/agents/{agent_id}/sessions/chat-1/metadata"


def _token_header(agent_id: str) -> dict:
    return {"X-Portal-Trust-Token": agent_trust_token(agent_id)}


# --- the assistant's token ----------------------------------------------------------


def test_defaults():
    settings = Settings(_env_file=None)
    assert settings.internal_api_auth_required is False
    assert settings.internal_api_block_forwarded is True


def test_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INTERNAL_API_BLOCK_FORWARDED", "false")
    settings = Settings(_env_file=None)
    assert settings.internal_api_auth_required is True
    assert settings.internal_api_block_forwarded is False


def test_the_assistants_own_token_is_accepted(harness):
    response = harness.client.patch(_title_url(harness.agent.id), json={"title": "Named"}, headers=_token_header(harness.agent.id))
    assert response.status_code == 200
    assert response.json()["title"] == "Named"


@pytest.mark.parametrize("required", [False, True])
def test_a_wrong_token_is_rejected_whether_or_not_one_is_required(harness, monkeypatch, required):
    monkeypatch.setattr(harness.settings, "internal_api_auth_required", required)
    for headers in ({"X-Portal-Trust-Token": "not-a-token"}, _token_header(harness.other.id)):
        for method, url, body in (
            ("PATCH", _title_url(harness.agent.id), {"title": "Forged"}),
            ("PUT", _metadata_url(harness.agent.id), {"latest_event_state": "running"}),
            ("GET", _metadata_url(harness.agent.id), None),
            ("GET", f"/api/internal/agents/{harness.agent.id}/sessions/metadata", None),
            ("DELETE", _metadata_url(harness.agent.id), None),
        ):
            response = harness.client.request(method, url, json=body, headers=headers)
            assert response.status_code == 401, (method, url)
            assert response.json() == {"detail": "Invalid Portal trust token"}


def test_one_assistant_cannot_write_another_assistants_sessions(harness):
    """The token names the assistant in the path: knowing another assistant's id is not enough."""
    response = harness.client.patch(
        _title_url(harness.other.id), json={"title": "Not mine"}, headers=_token_header(harness.agent.id)
    )
    assert response.status_code == 401
    listed = harness.client.get(
        f"/api/internal/agents/{harness.other.id}/sessions/metadata", headers=_token_header(harness.other.id)
    )
    assert listed.status_code == 200 and listed.json() == []


def test_a_missing_token_is_served_and_logged_once_per_assistant_while_not_required(harness, caplog):
    with caplog.at_level(logging.WARNING, logger="app.security.internal_api"):
        for _ in range(3):
            assert harness.client.patch(_title_url(harness.agent.id), json={"title": "Old image"}).status_code == 200
        assert harness.client.patch(_title_url(harness.other.id), json={"title": "Old image"}).status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert harness.agent.id in messages[0] and harness.other.id in messages[1]
    assert "INTERNAL_API_AUTH_REQUIRED" in messages[0]


def test_a_missing_token_is_rejected_once_required(harness, monkeypatch):
    monkeypatch.setattr(harness.settings, "internal_api_auth_required", True)
    response = harness.client.patch(_title_url(harness.agent.id), json={"title": "Old image"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Portal trust token required"}
    assert harness.client.get(_metadata_url(harness.agent.id)).status_code == 401
    accepted = harness.client.patch(
        _title_url(harness.agent.id), json={"title": "New image"}, headers=_token_header(harness.agent.id)
    )
    assert accepted.status_code == 200


def test_an_unknown_assistant_is_not_told_apart_by_a_caller_with_a_wrong_token(harness):
    response = harness.client.get(_metadata_url("no-such-agent"), headers={"X-Portal-Trust-Token": "guess"})
    assert response.status_code == 401
    # With its own (derivable only with SECRET_KEY) token the answer is the usual 404.
    assert harness.client.get(_metadata_url("no-such-agent"), headers=_token_header("no-such-agent")).status_code == 404


def test_the_unauthenticated_log_is_bounded(monkeypatch, caplog):
    monkeypatch.setattr(internal_api, "_unauthenticated_logged", set())
    monkeypatch.setattr(internal_api, "_UNAUTHENTICATED_LOG_LIMIT", 3)
    with caplog.at_level(logging.WARNING, logger="app.security.internal_api"):
        for index in range(10):
            internal_api._note_unauthenticated_call(f"agent-{index}")
    assert len(caplog.records) == 3
    assert len(internal_api._unauthenticated_logged) == 3


def test_every_internal_route_is_behind_the_token_check():
    from app.api.internal_session_metadata import router
    from app.security.internal_api import require_agent_trust_token

    assert router.routes
    for route in router.routes:
        assert route.path.startswith("/api/internal/agents/{agent_id}/"), route.path
        assert require_agent_trust_token in [dependency.call for dependency in route.dependant.dependencies], route.path


# --- not through the ingress --------------------------------------------------------


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Real-IP", "x-forwarded-for"])
def test_a_forwarded_request_to_the_internal_api_is_answered_404(harness, header):
    headers = {header: "203.0.113.9", **_token_header(harness.agent.id)}
    for method, url, body in (
        ("PATCH", _title_url(harness.agent.id), {"title": "From outside"}),
        ("GET", _metadata_url(harness.agent.id), None),
    ):
        response = harness.client.request(method, url, json=body, headers=headers)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_a_direct_request_is_served(harness):
    assert harness.client.patch(_title_url(harness.agent.id), json={"title": "From the pod"}).status_code == 200


def test_the_forwarded_block_can_be_switched_off(harness, monkeypatch):
    monkeypatch.setattr(harness.settings, "internal_api_block_forwarded", False)
    response = harness.client.patch(
        _title_url(harness.agent.id), json={"title": "Via a proxy"}, headers={"X-Forwarded-For": "10.1.2.3"}
    )
    assert response.status_code == 200


def test_forwarded_requests_to_the_rest_of_the_portal_are_served(harness):
    # Everything a browser reaches comes through the ingress.
    response = harness.client.get("/health", headers={"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9"})
    assert response.status_code == 200


def test_the_forwarded_block_is_logged_once(harness, caplog):
    with caplog.at_level(logging.WARNING, logger="app.security.internal_api"):
        for _ in range(3):
            harness.client.get(_metadata_url(harness.agent.id), headers={"X-Forwarded-For": "203.0.113.9"})
    assert len(caplog.records) == 1
    assert "INTERNAL_API_BLOCK_FORWARDED=false" in caplog.records[0].getMessage()


def _guarded_app(settings):
    app = FastAPI()
    seen = []

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(path: str):
        seen.append(path)
        return {"ok": True}

    app.add_middleware(ClusterOnlyApiMiddleware, settings_source=lambda: settings)
    return TestClient(app), seen


@pytest.mark.parametrize(
    "path, blocked",
    [
        ("/api/internal/agents/a/sessions/s/title", True),
        ("/api/app-host/v1/exchange", True),
        ("/api/app-host/", True),
        # Percent-encoding does not get around the prefix: the path is compared decoded.
        ("/api/%69nternal/agents/a/sessions/s/title", True),
        ("/api/app-sdk/v1/llm/complete", False),
        ("/api/internalx", False),
        ("/api/agents", False),
        ("/a/agent-1/api/internal/x", False),
    ],
)
def test_cluster_only_prefixes(monkeypatch, path, blocked):
    monkeypatch.setattr(internal_api, "_forwarded_block_logged", False)
    client, seen = _guarded_app(SimpleNamespace(internal_api_block_forwarded=True))
    response = client.post(path, headers={"X-Forwarded-For": "203.0.113.9"})
    assert response.status_code == (404 if blocked else 200)
    assert bool(seen) is not blocked
    # And without the proxy headers each of them reaches the app.
    assert client.post(path).status_code == 200
