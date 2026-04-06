from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.schemas.runtime_router import RuntimeRoutingDecisionResponse
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.external_event_ingress as ingress_api

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent = Agent(
        name="Router Agent",
        description="router",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-router",
        service_name="svc-router",
        pvc_name="pvc-router",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    def _override_user():
        return SimpleNamespace(id=user.id, role="admin", username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[ingress_api.get_current_user] = _override_user
    app.dependency_overrides[ingress_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, agent, _cleanup


def test_ingest_no_matching_subscription_returns_rejected():
    client, _db, _agent, cleanup = _build_client_with_overrides()
    try:
        response = client.post(
            "/api/external-events/ingest",
            json={"source_type": "github", "event_type": "push", "external_account_id": "acct-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "no_matching_subscription"
        assert body["created_task_id"] is None
    finally:
        cleanup()


def test_ingest_matching_subscription_without_binding_returns_rejected():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="push",
            target_ref="repo:main",
            enabled=True,
        )
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "push",
                "external_account_id": "missing-acct",
                "target_ref": "repo:main",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "no_enabled_binding"
        assert body["created_task_id"] is None
    finally:
        cleanup()


def test_ingest_matching_subscription_and_binding_creates_task():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            target_ref="repo:main",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-2",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-2",
                "target_ref": "repo:main",
                "payload_json": '{"pr": 15}',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["matched_agent_id"] == agent.id
        assert body["deduped"] is False
        assert body["created_task_id"] is not None

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 1
        assert tasks[0].assignee_agent_id == agent.id
        assert tasks[0].task_type == "github_review_task"
        assert tasks[0].status == "queued"
    finally:
        cleanup()


def test_dedupe_key_prevents_duplicate_task_creation():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="issue_updated",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="jira",
            external_account_id="acct-3",
            enabled=True,
        )

        payload = {
            "source_type": "jira",
            "event_type": "issue_updated",
            "external_account_id": "acct-3",
            "dedupe_key": "jira:ISSUE-1:updated",
            "payload_json": '{"issue":"ISSUE-1"}',
        }
        first = client.post("/api/external-events/ingest", json=payload)
        second = client.post("/api/external-events/ingest", json=payload)
        assert first.status_code == 200
        assert second.status_code == 200

        assert first.json()["accepted"] is True
        assert second.json()["accepted"] is True
        assert second.json()["deduped"] is True

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 1
    finally:
        cleanup()


def test_target_ref_filtering_blocks_non_matching_target():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="push",
            target_ref="repo:main",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-4",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "push",
                "external_account_id": "acct-4",
                "target_ref": "repo:dev",
            },
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is False
        assert response.json()["routing_reason"] == "no_matching_subscription"
    finally:
        cleanup()


def test_runtime_router_is_used_for_agent_resolution(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        import app.api.external_event_ingress as ingress_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="portal",
            event_type="manual_trigger",
            enabled=True,
        )

        calls = []

        def _fake_resolve_binding_decision(system_type: str, external_account_id: str, db: Session):
            calls.append((system_type, external_account_id))
            return RuntimeRoutingDecisionResponse(
                matched_agent_id=agent.id,
                matched_agent_type="workspace",
                policy_profile_id=None,
                capability_profile_id=None,
                reason="matched_enabled_binding",
                execution_mode="sync",
                runtime_target=None,
            )

        monkeypatch.setattr(ingress_api.service.runtime_router, "resolve_binding_decision", _fake_resolve_binding_decision)

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "portal",
                "event_type": "manual_trigger",
                "external_account_id": "acct-5",
            },
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert calls == [("portal", "acct-5")]
    finally:
        cleanup()
