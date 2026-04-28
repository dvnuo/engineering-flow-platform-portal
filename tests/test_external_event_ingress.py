import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.capability_profile import CapabilityProfile
from app.models.runtime_profile import RuntimeProfile
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.automation_rule_repo import AutomationRuleRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.services.auth_service import hash_password


def _build_client_with_overrides(monkeypatch):
    from app.main import app
    import app.api.external_event_ingress as ingress_api
    import app.services.automation_rule_service as automation_rule_service
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
    monkeypatch.setattr(automation_rule_service.TaskDispatcherService, "dispatch_task_in_background", lambda _self, task_id: _track_dispatch(task_id))

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


def _create_automation_rule(
    db: Session,
    *,
    owner_user_id: int,
    target_agent_id: str,
    owner: str,
    repo: str,
    review_target_type: str,
    review_target: str,
):
    return AutomationRuleRepository(db).create(
        {
            "name": f"rule-{review_target_type}-{review_target}",
            "enabled": True,
            "source_type": "github",
            "trigger_type": "github_pr_review_requested",
            "target_agent_id": target_agent_id,
            "task_type": "github_review_task",
            "task_template_id": "github_pr_review",
            "scope_json": json.dumps({"owner": owner, "repo": repo}),
            "trigger_config_json": json.dumps({"review_target_type": review_target_type, "review_target": review_target}),
            "task_config_json": json.dumps({"skill_name": "review-pull-request", "review_event": "COMMENT"}),
            "schedule_json": json.dumps({"interval_seconds": 60}),
            "state_json": "{}",
            "owner_user_id": owner_user_id,
            "created_by_user_id": owner_user_id,
        }
    )


def test_binding_driven_events_create_expected_tasks_and_dispatch(monkeypatch):
    client, db, admin_user, state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, _base_automation_config())
        _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="octo",
            repo="portal",
            review_target_type="user",
            review_target="alice",
        )
        repo = AgentIdentityBindingRepository(db)
        repo.create(agent_id=agent.id, system_type="github", external_account_id="gh-1", enabled=True)

        cases = [
            (
                {
                    "source_type": "github",
                    "event_type": "pull_request_review_requested",
                    "external_account_id": "alice",
                    "payload_json": json.dumps({"owner": "octo", "repo": "portal", "pull_number": 1, "reviewer": "alice", "head_sha": "abc"}),
                },
                "github_review_task",
                None,
            ),
        ]
        for payload, expected_task_type, expected_triggered in cases:
            resp = client.post("/api/external-events/ingest", json=payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["accepted"] is True
            assert body["resolved_task_type"] == expected_task_type
            assert body["matched_subscription_ids"] == []
            assert body["created_task_id"] in state["dispatches"]
            assert AgentTaskRepository(db).get_by_id(body["created_task_id"])

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 1
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_matches_automation_rule_without_runtime_profile_automation(monkeypatch):
    client, db, admin_user, state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(
            db,
            admin_user.id,
            {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}},
        )
        rule = _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="acme",
            repo="portal",
            review_target_type="user",
            review_target="alice",
        )

        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "alice",
                "target_ref": "acme/portal",
                "payload_json": json.dumps({"owner": "acme", "repo": "portal", "pull_number": 3, "reviewer": "alice", "head_sha": "sha1"}),
                "metadata_json": json.dumps({"trigger_mode": "manual-debug"}),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        assert body["routing_reason"] == "matched_automation_rule"
        task = AgentTaskRepository(db).get_by_id(body["created_task_id"])
        assert task
        assert task.source == "automation_rule"
        assert task.provider == "github"
        assert task.trigger == "github_pr_review_requested"
        assert task.task_type == "github_review_task"
        assert task.assignee_agent_id == rule.target_agent_id
        payload = json.loads(task.input_payload_json)
        assert payload["rule_id"] == rule.id
        assert payload["automation_rule_id"] == rule.id
        assert payload["owner"] == "acme"
        assert payload["repo"] == "portal"
        assert payload["pull_number"] == 3
        assert payload["head_sha"] == "sha1"
        assert payload["review_target"] == {"type": "user", "name": "alice"}
        assert body["created_task_id"] in state["dispatches"]
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_matches_without_identity_binding(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(
            db,
            admin_user.id,
            {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}},
        )
        _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="acme",
            repo="portal",
            review_target_type="team",
            review_target="acme/reviewers",
        )

        # no AgentIdentityBinding created on purpose
        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acme/reviewers",
                "payload_json": json.dumps(
                    {
                        "owner": "acme",
                        "repo": "portal",
                        "pull_number": 55,
                        "head_sha": "sha-no-binding",
                        "review_target": {"type": "team", "name": "acme/reviewers"},
                    }
                ),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        assert body["routing_reason"] == "matched_automation_rule"
        assert body["routing_reason"] != "no_enabled_binding"
        assert body["routing_reason"] != "automation_not_enabled_or_scope_mismatch"
        task = AgentTaskRepository(db).get_by_id(body["created_task_id"])
        assert task
        assert task.task_type == "github_review_task"
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_no_matching_rule(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        _create_agent(db, admin_user.id, {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}})
        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "alice",
                "target_ref": "acme/portal",
                "payload_json": json.dumps({"owner": "acme", "repo": "portal", "pull_number": 3, "reviewer": "alice", "head_sha": "sha1"}),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "no_matching_automation_rule"
        assert body["resolved_task_type"] == "github_review_task"
        assert body["routing_reason"] != "no_enabled_binding"
        assert body["routing_reason"] != "automation_not_enabled_or_scope_mismatch"
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_team_target(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}})
        _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="acme",
            repo="portal",
            review_target_type="team",
            review_target="acme/reviewers",
        )

        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acme/reviewers",
                "target_ref": "acme/portal",
                "payload_json": json.dumps({"owner": "acme", "repo": "portal", "pull_number": 9, "review_team": "acme/reviewers", "head_sha": "sha-t"}),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        task = AgentTaskRepository(db).get_by_id(body["created_task_id"])
        payload = json.loads(task.input_payload_json)
        assert payload["review_target"] == {"type": "team", "name": "acme/reviewers"}
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_case_insensitive_user_match(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}})
        _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="Acme",
            repo="Portal",
            review_target_type="user",
            review_target="Alice",
        )
        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "alice",
                "payload_json": json.dumps({"owner": "acme", "repo": "portal", "pull_number": 11, "reviewer": "alice", "head_sha": "sha-u"}),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        task = AgentTaskRepository(db).get_by_id(body["created_task_id"])
        payload = json.loads(task.input_payload_json)
        assert payload["owner"] == "Acme"
        assert payload["repo"] == "Portal"
        assert payload["review_target"] == {"type": "user", "name": "Alice"}
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_case_insensitive_team_match(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}})
        _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="Acme",
            repo="Portal",
            review_target_type="team",
            review_target="Acme/Reviewers",
        )
        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acme/reviewers",
                "payload_json": json.dumps({"owner": "acme", "repo": "portal", "pull_number": 12, "review_team": "acme/reviewers", "head_sha": "sha-t2"}),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        task = AgentTaskRepository(db).get_by_id(body["created_task_id"])
        payload = json.loads(task.input_payload_json)
        assert payload["owner"] == "Acme"
        assert payload["repo"] == "Portal"
        assert payload["review_target"] == {"type": "team", "name": "Acme/Reviewers"}
    finally:
        cleanup()


def test_github_pr_review_requested_ingress_capability_profile_blocked(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, {"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "token"}})
        cp = CapabilityProfile(name="cap-jira-only-ingress", allowed_external_systems_json='["jira"]')
        db.add(cp); db.commit(); db.refresh(cp)
        agent.capability_profile_id = cp.id
        db.add(agent); db.commit()

        _create_automation_rule(
            db,
            owner_user_id=admin_user.id,
            target_agent_id=agent.id,
            owner="acme",
            repo="portal",
            review_target_type="user",
            review_target="alice",
        )

        resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "alice",
                "payload_json": json.dumps({"owner": "acme", "repo": "portal", "pull_number": 3, "reviewer": "alice", "head_sha": "sha1"}),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "capability_profile_blocked"
        assert body["matched_agent_id"] == agent.id
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
        assert resp.json()["routing_reason"] == "legacy_provider_automation_removed"

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
        assert resp2.json()["routing_reason"] == "legacy_provider_automation_removed"
    finally:
        cleanup()


def test_invalid_triggered_event_payloads_are_rejected(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, _base_automation_config())
        repo = AgentIdentityBindingRepository(db)
        repo.create(agent_id=agent.id, system_type="jira", external_account_id="jira-1", enabled=True)
        repo.create(agent_id=agent.id, system_type="confluence", external_account_id="conf-1", enabled=True)

        initial_tasks = len(AgentTaskRepository(db).list_all())

        missing_payload_resp = client.post(
            "/api/external-events/ingest",
            json={"source_type": "jira", "event_type": "assigned", "external_account_id": "jira-1", "project_key": "ENG"},
        )
        assert missing_payload_resp.status_code == 200
        assert missing_payload_resp.json()["accepted"] is False
        assert missing_payload_resp.json()["routing_reason"] == "legacy_provider_automation_removed"

        non_object_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "mention",
                "external_account_id": "jira-1",
                "payload_json": "[]",
                "project_key": "ENG",
            },
        )
        assert non_object_resp.status_code == 200
        assert non_object_resp.json()["accepted"] is False
        assert non_object_resp.json()["routing_reason"] == "legacy_provider_automation_removed"

        missing_required_field_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "confluence",
                "event_type": "mention",
                "external_account_id": "conf-1",
                "payload_json": json.dumps({"space_key": "DEV", "comment_id": "c-9"}),
            },
        )
        assert missing_required_field_resp.status_code == 200
        assert missing_required_field_resp.json()["accepted"] is False
        assert missing_required_field_resp.json()["routing_reason"] == "legacy_provider_automation_removed"

        assert len(AgentTaskRepository(db).list_all()) == initial_tasks
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


def test_legacy_provider_automation_routing_removed(monkeypatch):
    client, db, admin_user, _state, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        agent = _create_agent(db, admin_user.id, _base_automation_config())
        repo = AgentIdentityBindingRepository(db)
        repo.create(agent_id=agent.id, system_type="github", external_account_id="gh-1", enabled=True)
        repo.create(agent_id=agent.id, system_type="jira", external_account_id="jira-1", enabled=True)
        repo.create(agent_id=agent.id, system_type="confluence", external_account_id="conf-1", enabled=True)

        cases = [
            {
                "source_type": "github",
                "event_type": "mention",
                "external_account_id": "gh-1",
                "payload_json": json.dumps({"owner": "octo", "repo": "portal", "issue_number": 2}),
            },
            {
                "source_type": "jira",
                "event_type": "assigned",
                "external_account_id": "jira-1",
                "project_key": "ENG",
                "payload_json": json.dumps({"issue_key": "ENG-1", "project_key": "ENG"}),
            },
            {
                "source_type": "confluence",
                "event_type": "mention",
                "external_account_id": "conf-1",
                "payload_json": json.dumps({"page_id": "12345", "space_key": "DEV"}),
            },
        ]
        for payload in cases:
            resp = client.post("/api/external-events/ingest", json=payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["accepted"] is False
            assert body["routing_reason"] == "legacy_provider_automation_removed"
    finally:
        cleanup()
