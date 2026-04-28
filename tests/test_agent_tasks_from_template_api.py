import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User


def _client():
    from app.main import app
    import app.api.agent_tasks as api_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash="pw", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = Agent(name="a", owner_user_id=user.id, visibility="private", status="running", image="img", runtime_profile_id=rp.id, disk_size_gi=20, mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace")
    db.add(agent); db.commit(); db.refresh(agent)

    def _override_user():
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)

    def _override_db():
        yield db

    app.dependency_overrides[api_module.get_current_user] = _override_user
    app.dependency_overrides[api_module.get_db] = _override_db
    return TestClient(app), db, agent, app


def test_create_task_from_template_dispatch_toggle(monkeypatch):
    client, _db, agent, app = _client()
    calls = []
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: calls.append(task_id))
    try:
        payload = {
            "template_id": "github_pr_review",
            "assignee_agent_id": agent.id,
            "dispatch_immediately": False,
            "input": {"owner": "acme", "repo": "portal", "pull_number": 1},
        }
        resp = client.post("/api/agent-tasks/from-template", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["template_id"] == "github_pr_review"
        assert body["task_type"] == "github_review_task"
        assert calls == []

        payload["dispatch_immediately"] = True
        resp2 = client.post("/api/agent-tasks/from-template", json=payload)
        assert resp2.status_code == 200
        assert len(calls) == 1
    finally:
        app.dependency_overrides.clear()


def test_create_bundle_task_from_template_contains_bundle_template_id(monkeypatch):
    client, _db, agent, app = _client()
    calls = []
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: calls.append(task_id))
    try:
        payload = {
            "template_id": "collect_requirements_to_bundle",
            "assignee_agent_id": agent.id,
            "dispatch_immediately": False,
            "input": {
                "bundle_template_id": "requirement.v1",
                "bundle_ref": {"repo": "octo/assets", "path": "bundles/rb", "branch": "main"},
                "manifest_ref": {"repo": "octo/assets", "path": "bundles/rb", "branch": "main"},
                "sources": {"jira": ["ABC-1"]},
            },
        }
        resp = client.post("/api/agent-tasks/from-template", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["template_id"] == "collect_requirements_to_bundle"
        assert body["task_type"] == "bundle_action_task"
        assert calls == []
        task_input = json.loads(body["input_payload_json"])
        assert task_input["task_template_id"] == "collect_requirements_to_bundle"
        assert task_input["task_type"] == "bundle_action_task"
        assert task_input["bundle_template_id"] == "requirement.v1"
    finally:
        app.dependency_overrides.clear()


def test_create_github_review_task_from_template_contains_task_template_id(monkeypatch):
    client, _db, agent, app = _client()
    calls = []
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: calls.append(task_id))
    try:
        payload = {
            "template_id": "github_pr_review",
            "assignee_agent_id": agent.id,
            "dispatch_immediately": False,
            "input": {"owner": "acme", "repo": "portal", "pull_number": 7},
        }
        resp = client.post("/api/agent-tasks/from-template", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        task_input = json.loads(body["input_payload_json"])
        assert task_input["task_template_id"] == "github_pr_review"
        assert task_input["task_type"] == "github_review_task"
        assert task_input["trigger"] == "github_pr_review_requested"
        assert calls == []
    finally:
        app.dependency_overrides.clear()
