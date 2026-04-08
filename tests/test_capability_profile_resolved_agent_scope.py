import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, CapabilityProfile, RuntimeCapabilityCatalogSnapshot, User
from app.services.auth_service import hash_password


def _build_client(monkeypatch):
    from app.main import app
    import app.api.capability_profiles as capability_api
    import app.deps as deps_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    agent_a = Agent(
        name="Agent A",
        description="A",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-a.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-a",
        service_name="svc-a",
        pvc_name="pvc-a",
        endpoint_path="/",
        agent_type="workspace",
    )
    agent_b = Agent(
        name="Agent B",
        description="B",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo-b.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-b",
        service_name="svc-b",
        pvc_name="pvc-b",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent_a)
    db.add(agent_b)
    db.commit()
    db.refresh(agent_a)
    db.refresh(agent_b)

    profile = CapabilityProfile(name="cap-scope", allowed_actions_json='["review_pull_request"]')
    db.add(profile)
    db.commit()
    db.refresh(profile)

    db.add(
        RuntimeCapabilityCatalogSnapshot(
            source_agent_id=agent_a.id,
            catalog_version="cat-a",
            catalog_source="runtime_api",
            payload_json=json.dumps(
                {"catalog_version": "cat-a", "capabilities": [{"capability_id": "adapter:a:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"}]}
            ),
        )
    )
    db.add(
        RuntimeCapabilityCatalogSnapshot(
            source_agent_id=agent_b.id,
            catalog_version="cat-b",
            catalog_source="runtime_api",
            payload_json=json.dumps(
                {"catalog_version": "cat-b", "capabilities": [{"capability_id": "adapter:b:review_pull_request", "capability_type": "adapter_action", "action_alias": "review_pull_request"}]}
            ),
        )
    )
    db.commit()

    def _override_user():
        return SimpleNamespace(id=user.id, role="admin", username=user.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[deps_module.get_current_user] = _override_user
    app.dependency_overrides[capability_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), profile.id, agent_a.id, agent_b.id, _cleanup


def test_resolved_endpoint_uses_agent_scoped_catalog_when_agent_id_is_provided(monkeypatch):
    client, profile_id, agent_a_id, agent_b_id, cleanup = _build_client(monkeypatch)
    try:
        resp_a = client.get(f"/api/capability-profiles/{profile_id}/resolved?agent_id={agent_a_id}")
        assert resp_a.status_code == 200
        assert resp_a.json()["resolved"]["runtime_capability_catalog_version"] == "cat-a"
        assert resp_a.json()["resolved"]["allowed_adapter_actions"] == ["adapter:a:review_pull_request"]

        resp_b = client.get(f"/api/capability-profiles/{profile_id}/resolved?agent_id={agent_b_id}")
        assert resp_b.status_code == 200
        assert resp_b.json()["resolved"]["runtime_capability_catalog_version"] == "cat-b"
        assert resp_b.json()["resolved"]["allowed_adapter_actions"] == ["adapter:b:review_pull_request"]
    finally:
        cleanup()


def test_resolved_endpoint_without_agent_id_remains_backward_compatible(monkeypatch):
    client, profile_id, _agent_a_id, _agent_b_id, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/api/capability-profiles/{profile_id}/resolved")
        assert resp.status_code == 200
        assert resp.json()["resolved"]["runtime_capability_catalog_source"] in {"seed_fallback", "settings_snapshot", "runtime_api"}
    finally:
        cleanup()
