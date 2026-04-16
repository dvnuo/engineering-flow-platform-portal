import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.services.auth_service import hash_password


def _build_client_with_overrides(monkeypatch):
    from app.main import app
    import app.api.external_event_ingress as ingress_api
    import app.deps as deps_module
    from app.db import get_db as shared_get_db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    admin_user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)

    state = {"user": admin_user, "dispatches": []}

    def _override_user():
        user = state["user"]
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname="Owner")

    def _override_db():
        yield db

    def _track_dispatch(task_id: str):
        state["dispatches"].append(task_id)

    monkeypatch.setattr(ingress_api.service, "_dispatch_task_in_background", _track_dispatch)

    app.dependency_overrides[shared_get_db] = _override_db
    app.dependency_overrides[deps_module.get_current_user] = _override_user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, admin_user, state, _cleanup


def _create_agent(db: Session, owner_id: int, config: dict | None = None) -> Agent:
    agent = Agent(
        name="Router Agent",
        owner_user_id=owner_id,
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
    if config is not None:
        rp = RuntimeProfile(
            owner_user_id=owner_id,
            name=f"rp-{agent.id}",
            config_json=json.dumps(config),
            revision=1,
            is_default=True,
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)
        agent.runtime_profile_id = rp.id
        db.add(agent)
        db.commit()
    return agent


def _base_automation_config() -> dict:
    return {
        "github": {
            "enabled": True,
            "automation": {
                "review_requests": {"enabled": True, "repos": ["octo/portal"]},
                "mentions": {"enabled": True, "repos": ["octo/portal"], "include_review_comments": True},
            },
        },
        "jira": {
            "enabled": True,
            "instances": [],
            "automation": {
                "assignments": {"enabled": True, "projects": ["ENG"]},
                "mentions": {"enabled": True, "projects": ["ENG"]},
            },
        },
        "confluence": {
            "enabled": True,
            "instances": [],
            "automation": {"mentions": {"enabled": True, "spaces": ["DEV"]}},
        },
    }


def test_binding_driven_events_create_expected_tasks_and_dispatch(monkeypatch):
    client, db, admin_user, state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, _base_automation_config())
        repo = AgentIdentityBindingRepository(db)
        repo.create(agent_id=agent.id, system_type="github", external_account_id="gh-1", enabled=True)
        repo.create(agent_id=agent.id, system_type="jira", external_account_id="jira-1", enabled=True)
        repo.create(agent_id=agent.id, system_type="confluence", external_account_id="conf-1", enabled=True)

        cases = [
            ({"source_type": "github", "event_type": "pull_request_review_requested", "external_account_id": "gh-1", "payload_json": json.dumps({"owner": "octo", "repo": "portal", "pull_number": 1, "head_sha": "abc"})}, "github_review_task"),
            ({"source_type": "github", "event_type": "mention", "external_account_id": "gh-1", "payload_json": json.dumps({"owner": "octo", "repo": "portal", "issue_number": 2})}, "triggered_event_task"),
            ({"source_type": "jira", "event_type": "assigned", "external_account_id": "jira-1", "project_key": "ENG"}, "triggered_event_task"),
            ({"source_type": "jira", "event_type": "mention", "external_account_id": "jira-1", "project_key": "ENG"}, "triggered_event_task"),
            ({"source_type": "confluence", "event_type": "mention", "external_account_id": "conf-1", "target_ref": "DEV"}, "triggered_event_task"),
        ]
        for payload, expected_task_type in cases:
            resp = client.post("/api/external-events/ingest", json=payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["accepted"] is True
            assert body["resolved_task_type"] == expected_task_type
            assert body["matched_subscription_ids"] == []
            assert body["created_task_id"] in state["dispatches"]

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 5
    finally:
        cleanup()


def test_automation_disabled_or_scope_mismatch_rejects(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cfg = _base_automation_config()
        cfg["github"]["automation"]["mentions"]["enabled"] = False
        agent = _create_agent(db, admin_user.id, cfg)
        AgentIdentityBindingRepository(db).create(agent_id=agent.id, system_type="github", external_account_id="gh-1", enabled=True)

        resp = client.post(
            "/api/external-events/ingest",
            json={"source_type": "github", "event_type": "mention", "external_account_id": "gh-1", "payload_json": json.dumps({"owner": "octo", "repo": "portal"})},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is False

        cfg2 = _base_automation_config()
        agent2 = _create_agent(db, admin_user.id, cfg2)
        AgentIdentityBindingRepository(db).create(
            agent_id=agent2.id,
            system_type="jira",
            external_account_id="jira-2",
            scope_json='{"projects":["OPS"]}',
            enabled=True,
        )
        resp2 = client.post(
            "/api/external-events/ingest",
            json={"source_type": "jira", "event_type": "assigned", "external_account_id": "jira-2", "project_key": "ENG"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["accepted"] is False
    finally:
        cleanup()


def test_workflow_rule_routes_without_subscription(monkeypatch):
    client, db, admin_user, state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, _base_automation_config())
        rule = WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="ENG",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding="jira-user",
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
            config_json="{}",
        )
        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "ENG",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_assignee": "jira-user",
                "issue_key": "ENG-1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        assert body["matched_workflow_rule_id"] == rule.id
        assert body["matched_subscription_ids"] == []
        assert body["created_task_id"] in state["dispatches"]
    finally:
        cleanup()
