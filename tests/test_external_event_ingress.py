import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, CapabilityProfile, User
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.runtime_router import RuntimeRoutingDecisionResponse
from app.services.task_dispatcher import AgentTaskDispatchResult
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.external_event_ingress as ingress_api
    import app.api.provider_webhooks as provider_api
    import app.deps as deps_module
    from app.db import get_db as shared_get_db

    class _FakeRuntimeSuccessResponse:
        status_code = 200
        text = (
            '{"ok": true, "task_id": "rt-1", "execution_type": "task", "request_id": "req-1",'
            ' "status": "success", "output_payload": {"handled": true}}'
        )

        @staticmethod
        def json():
            return {
                "ok": True,
                "task_id": "rt-1",
                "execution_type": "task",
                "request_id": "req-1",
                "status": "success",
                "output_payload": {"handled": True},
            }

    async def _fake_post_to_runtime(_url: str, _body: dict):
        return _FakeRuntimeSuccessResponse()

    ingress_api.service.task_dispatcher._post_to_runtime = _fake_post_to_runtime
    provider_api.service.task_dispatcher._post_to_runtime = _fake_post_to_runtime

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    admin_user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    viewer_user = User(username="viewer", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add_all([admin_user, viewer_user])
    db.commit()
    db.refresh(admin_user)
    db.refresh(viewer_user)

    agent = Agent(
        name="Router Agent",
        description="router",
        owner_user_id=admin_user.id,
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

    state = {"user": admin_user}

    def _override_user():
        user = state["user"]
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname="Owner")

    def _override_db():
        yield db

    def _dispatch_immediately(task_id: str):
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        coroutine = ingress_api.service.task_dispatcher.dispatch_task(task_id, db)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coroutine)).result()

    ingress_api.service._dispatch_task_in_background = _dispatch_immediately
    provider_api.service._dispatch_task_in_background = _dispatch_immediately

    app.dependency_overrides[ingress_api.get_db] = _override_db
    app.dependency_overrides[shared_get_db] = _override_db
    app.dependency_overrides[deps_module.get_current_user] = _override_user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user_obj):
        state["user"] = user_obj

    return TestClient(app), db, agent, admin_user, viewer_user, _set_user, _cleanup


def test_ingest_no_matching_subscription_returns_rejected():
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
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


def test_public_ingest_is_admin_only():
    client, _db, _agent, _admin_user, viewer_user, set_user, cleanup = _build_client_with_overrides()
    try:
        set_user(viewer_user)
        response = client.post(
            "/api/external-events/ingest",
            json={"source_type": "github", "event_type": "push", "external_account_id": "acct-1"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Admin only"
    finally:
        cleanup()


def test_internal_ingest_accepts_standard_internal_requests():
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        missing = client.post(
            "/api/internal/external-events/ingest",
            json={"source_type": "github", "event_type": "push", "external_account_id": "acct-1"},
        )
        assert missing.status_code == 200
    finally:
        cleanup()


def test_internal_ingest_reuses_routing_flow():
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        response = client.post(
            "/api/internal/external-events/ingest",
            json={"source_type": "github", "event_type": "push", "external_account_id": "acct-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "no_matching_subscription"
    finally:
        cleanup()


def test_ingest_matching_subscription_without_binding_returns_rejected():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
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
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
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
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":15,"reviewer":"alice","head_sha":"abc123"}',
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
        assert tasks[0].owner_user_id == agent.owner_user_id
        assert tasks[0].created_by_user_id is None
        assert tasks[0].task_type == "github_review_task"
        assert tasks[0].status == "done"
        payload = json.loads(tasks[0].input_payload_json)
        assert payload["owner"] == "octo"
        assert payload["repo"] == "portal"
        assert payload["pull_number"] == 15
        assert payload["subscription_id"] in body["matched_subscription_ids"]
    finally:
        cleanup()


def test_ingest_rejects_when_external_system_not_allowed():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(name="cap-gate-source", allowed_external_systems_json='["jira"]')
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-source-gate",
            enabled=True,
        )
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-source-gate",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":15}',
            },
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is False
        assert response.json()["routing_reason"] == "external_system_not_allowed"
    finally:
        cleanup()


def test_ingest_rejects_when_webhook_trigger_not_allowed():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(
            name="cap-gate-trigger",
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["issue_updated"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-trigger-gate",
            enabled=True,
        )
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-trigger-gate",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":15}',
            },
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is False
        assert response.json()["routing_reason"] == "webhook_trigger_not_allowed"
    finally:
        cleanup()


def test_dedupe_key_prevents_duplicate_task_creation():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="portal",
            event_type="manual_trigger",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="portal",
            external_account_id="acct-3",
            enabled=True,
        )

        payload = {
            "source_type": "portal",
            "event_type": "manual_trigger",
            "external_account_id": "acct-3",
            "dedupe_key": "portal:manual:1",
            "payload_json": '{"action":"run"}',
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


def test_jira_ingest_with_matching_rule_creates_workflow_review_task():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            success_transition="Done",
            failure_transition="Needs Changes",
            success_reassign_to="reporter",
            failure_reassign_to="requester",
            explicit_success_assignee=None,
            explicit_failure_assignee=None,
            enabled=True,
            config_json='{"strict": true}',
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-123",
                "issue_assignee": "assignee-1",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["resolved_task_type"] == "jira_workflow_review_task"
        assert body["matched_workflow_rule_id"] is not None

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 1
        assert tasks[0].task_type == "jira_workflow_review_task"
        task_payload = json.loads(tasks[0].input_payload_json)
        assert task_payload["skill_name"] == "workflow-review"
        assert task_payload["success_transition"] == "Done"
        assert task_payload["failure_reassign_to"] == "requester"
        assert isinstance(task_payload["workflow_context"], dict)
        assert task_payload["workflow_context"]["strict"] is True
        assert tasks[0].shared_context_ref == "EFP-123"
    finally:
        cleanup()


def test_jira_workflow_ingest_rejects_when_external_system_not_allowed():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(
            name="cap-jira-source-gate",
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["workflow_review_requested"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        rule = WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-777",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "external_system_not_allowed"
        assert body["matched_agent_id"] == agent.id
        assert body["matched_workflow_rule_id"] == rule.id
    finally:
        cleanup()


def test_jira_workflow_ingest_rejects_when_webhook_trigger_not_allowed():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(
            name="cap-jira-trigger-gate",
            allowed_external_systems_json='["jira"]',
            allowed_webhook_triggers_json='["issue_updated"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        rule = WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-778",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "webhook_trigger_not_allowed"
        assert body["matched_agent_id"] == agent.id
        assert body["matched_workflow_rule_id"] == rule.id
    finally:
        cleanup()


def test_jira_workflow_ingest_without_capability_profile_remains_permissive():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        agent.capability_profile_id = None
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-779",
            },
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is True
    finally:
        cleanup()


def test_authorized_capability_profile_allows_github_and_jira_events():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(
            name="cap-allow-both",
            allowed_external_systems_json='["github","jira"]',
            allowed_webhook_triggers_json='["pull_request_review_requested","workflow_review_requested"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-allow-github",
            enabled=True,
        )
        github_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-allow-github",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":15}',
            },
        )
        assert github_resp.status_code == 200
        assert github_resp.json()["accepted"] is True

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
            config_json='{"strict": true}',
        )
        jira_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-456",
            },
        )
        assert jira_resp.status_code == 200
        assert jira_resp.json()["accepted"] is True
    finally:
        cleanup()


def test_single_profile_allows_github_but_rejects_jira_workflow():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(
            name="cap-github-only",
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["pull_request_review_requested"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-combo-gh",
            enabled=True,
        )
        github_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-combo-gh",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":15}',
            },
        )
        assert github_resp.status_code == 200
        assert github_resp.json()["accepted"] is True

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
        )
        jira_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-880",
            },
        )
        assert jira_resp.status_code == 200
        assert jira_resp.json()["accepted"] is False
        assert jira_resp.json()["routing_reason"] == "external_system_not_allowed"
    finally:
        cleanup()


def test_single_profile_allows_jira_workflow_but_rejects_github():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        profile = CapabilityProfile(
            name="cap-jira-only",
            allowed_external_systems_json='["jira"]',
            allowed_webhook_triggers_json='["workflow_review_requested"]',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        agent.capability_profile_id = profile.id
        db.add(agent)
        db.commit()

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="workflow-review",
            enabled=True,
        )
        jira_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-881",
            },
        )
        assert jira_resp.status_code == 200
        assert jira_resp.json()["accepted"] is True

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-combo-gh-denied",
            enabled=True,
        )
        github_resp = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-combo-gh-denied",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":15}',
            },
        )
        assert github_resp.status_code == 200
        assert github_resp.json()["accepted"] is False
        assert github_resp.json()["routing_reason"] == "external_system_not_allowed"
    finally:
        cleanup()


def test_jira_ingest_rejects_bad_persisted_rule_config():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )

        bad_rule = WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Task",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            enabled=True,
            config_json='{"ok": true}',
        )
        bad_rule.config_json = "not-json"
        WorkflowTransitionRuleRepository(db).save(bad_rule)

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Task",
                "trigger_status": "In Review",
                "issue_key": "EFP-999",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "invalid_workflow_rule_config"
        assert body["message"] == "Matched workflow rule has invalid config_json"

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 0
    finally:
        cleanup()


def test_jira_rule_assignee_specific_beats_wildcard():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        wildcard_rule = WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Bug",
            trigger_status="In Review",
            assignee_binding=None,
            target_agent_id=agent.id,
            skill_name="wildcard-skill",
            enabled=True,
        )
        specific_rule = WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Bug",
            trigger_status="In Review",
            assignee_binding="user-42",
            target_agent_id=agent.id,
            skill_name="specific-skill",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Bug",
                "trigger_status": "In Review",
                "issue_key": "EFP-456",
                "issue_assignee": "user-42",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["matched_workflow_rule_id"] == specific_rule.id
        assert body["matched_workflow_rule_id"] != wildcard_rule.id

        task = AgentTaskRepository(db).list_all()[0]
        assert '"skill_name": "specific-skill"' in task.input_payload_json
    finally:
        cleanup()


def test_jira_no_matching_rule_returns_rejected():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "NOPE",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "NOPE-1",
                "issue_assignee": "someone",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "no_matching_workflow_rule"
        assert body["created_task_id"] is None
    finally:
        cleanup()


def test_target_ref_filtering_blocks_non_matching_target():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
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
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        import app.api.external_event_ingress as ingress_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="portal",
            event_type="manual_trigger",
            enabled=True,
        )

        calls = []

        def _fake_resolve_binding_decision_for_event(system_type: str, external_account_id: str, db: Session):
            calls.append((system_type, external_account_id))
            return RuntimeRoutingDecisionResponse(
                matched_agent_id=agent.id,
                matched_agent_type="workspace",
                policy_profile_id=None,
                capability_profile_id=None,
                reason="matched_enabled_binding",
                execution_mode="async_task",
                runtime_target=None,
            )

        monkeypatch.setattr(
            ingress_api.service.runtime_router,
            "resolve_binding_decision_for_event",
            _fake_resolve_binding_decision_for_event,
        )

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


def test_github_review_event_requires_external_account_id():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":10}',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "missing_external_account_id"
    finally:
        cleanup()


def test_github_review_event_requires_owner_repo_pull_number():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-req-fields",
            enabled=True,
        )
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-req-fields",
                "payload_json": '{"owner":"octo","repo":"portal"}',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "invalid_github_event_payload"
    finally:
        cleanup()


def test_github_review_event_dispatch_failure_still_accepts_and_schedules(monkeypatch):
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        import app.api.external_event_ingress as ingress_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-dispatch-fail",
            enabled=True,
        )

        scheduled_task_ids = []
        monkeypatch.setattr(
            ingress_api.service,
            "_dispatch_task_in_background",
            lambda task_id: scheduled_task_ids.append(task_id),
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-dispatch-fail",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":101}',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["routing_reason"] == "matched_enabled_binding"
        assert body["created_task_id"] is not None
        assert scheduled_task_ids == [body["created_task_id"]]
    finally:
        cleanup()


def test_github_review_event_dedupe_hint_prevents_duplicate_without_dedupe_key():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-gh-dedupe",
            enabled=True,
        )
        payload = {
            "source_type": "github",
            "event_type": "pull_request_review_requested",
            "external_account_id": "acct-gh-dedupe",
            "payload_json": '{"owner":"octo","repo":"portal","pull_number":55,"head_sha":"sha-1"}',
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


def test_github_review_event_new_head_sha_supersedes_previous_active_task(monkeypatch):
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        import app.api.external_event_ingress as ingress_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-gh-stale",
            enabled=True,
        )

        async def _no_op_dispatch(task_id: str, _db):
            return AgentTaskDispatchResult(
                dispatched=True,
                task_id=task_id,
                runtime_status_code=202,
                task_status="queued",
                message="queued for test",
                result_payload_json=None,
            )

        monkeypatch.setattr(ingress_api.service.task_dispatcher, "dispatch_task", _no_op_dispatch)

        first = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-gh-stale",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":55,"head_sha":"sha-1"}',
            },
        )
        second = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-gh-stale",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":55,"head_sha":"sha-2"}',
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["accepted"] is True
        assert second.json()["accepted"] is True
        assert second.json()["deduped"] is False

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 2
        tasks_by_id = {task.id: task for task in tasks}
        first_task = tasks_by_id[first.json()["created_task_id"]]
        second_task = tasks_by_id[second.json()["created_task_id"]]
        assert first_task.status == "stale"
        stale_payload = json.loads(first_task.result_payload_json or "{}")
        assert stale_payload["error_code"] == "superseded_by_new_head_sha"
        assert stale_payload["superseded_by_task_id"] == second_task.id
        assert stale_payload["superseded_by_head_sha"] == "sha-2"
        assert second_task.status == "queued"
    finally:
        cleanup()


def test_github_review_event_new_head_sha_does_not_stale_done_task():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-gh-done",
            enabled=True,
        )
        first = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-gh-done",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":77,"head_sha":"sha-1"}',
            },
        )
        assert first.status_code == 200
        assert first.json()["accepted"] is True

        second = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-gh-done",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":77,"head_sha":"sha-2"}',
            },
        )
        assert second.status_code == 200
        assert second.json()["accepted"] is True
        assert second.json()["deduped"] is False

        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 2
        tasks_by_id = {task.id: task for task in tasks}
        first_task = tasks_by_id[first.json()["created_task_id"]]
        second_task = tasks_by_id[second.json()["created_task_id"]]
        assert first_task.status == "done"
        assert second_task.status == "done"
    finally:
        cleanup()


def test_github_review_event_rejected_when_repo_not_allowed():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
            config_json='{"allowed_repos": ["octo/other"]}',
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-repo-scope",
            enabled=True,
        )
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-repo-scope",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":7}',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["routing_reason"] == "repo_not_allowed"
    finally:
        cleanup()


def test_jira_ingress_accepts_and_schedules_background_dispatch(monkeypatch):
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        import app.api.external_event_ingress as ingress_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            target_agent_id=agent.id,
            enabled=True,
            config_json='{"strict": true}',
        )

        scheduled_task_ids = []
        monkeypatch.setattr(
            ingress_api.service,
            "_dispatch_task_in_background",
            lambda task_id: scheduled_task_ids.append(task_id),
        )

        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "jira",
                "event_type": "workflow_review_requested",
                "project_key": "EFP",
                "issue_type": "Story",
                "trigger_status": "In Review",
                "issue_key": "EFP-777",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["routing_reason"] == "matched_workflow_rule"
        assert body["created_task_id"] is not None
        assert scheduled_task_ids == [body["created_task_id"]]
    finally:
        cleanup()


def test_failed_task_status_is_not_used_for_dedupe_candidate():
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-failed-dedupe",
            enabled=True,
        )
        payload = {
            "source_type": "github",
            "event_type": "pull_request_review_requested",
            "external_account_id": "acct-failed-dedupe",
            "payload_json": '{"owner":"octo","repo":"portal","pull_number":56,"head_sha":"sha-1"}',
        }
        first = client.post("/api/external-events/ingest", json=payload)
        assert first.status_code == 200
        first_task_id = first.json()["created_task_id"]
        first_task = AgentTaskRepository(db).get_by_id(first_task_id)
        first_task.status = "failed"
        first_task.result_payload_json = '{"ok":false,"error_code":"runtime_request_error"}'
        AgentTaskRepository(db).save(first_task)

        second = client.post("/api/external-events/ingest", json=payload)
        assert second.status_code == 200
        assert second.json()["accepted"] is True
        assert second.json()["deduped"] is False
        assert second.json()["created_task_id"] != first_task_id
    finally:
        cleanup()


def test_ingress_accepts_without_waiting_for_runtime_completion(monkeypatch):
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    try:
        import app.api.external_event_ingress as ingress_api
        from app.services.external_event_router import ExternalEventRouterService

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-non-blocking",
            enabled=True,
        )

        async def _slow_dispatch(*_args, **_kwargs):
            await asyncio.sleep(0.25)
            return AgentTaskDispatchResult(
                dispatched=True,
                task_id="task-slow",
                runtime_status_code=200,
                task_status="done",
                message="ok",
                result_payload_json='{"ok":true}',
            )

        import asyncio

        monkeypatch.setattr(ingress_api.service.task_dispatcher, "dispatch_task", _slow_dispatch)
        monkeypatch.setattr(
            ingress_api.service,
            "_dispatch_task_in_background",
            lambda task_id: ExternalEventRouterService._dispatch_task_in_background(ingress_api.service, task_id),
        )

        start = time.perf_counter()
        response = client.post(
            "/api/external-events/ingest",
            json={
                "source_type": "github",
                "event_type": "pull_request_review_requested",
                "external_account_id": "acct-non-blocking",
                "payload_json": '{"owner":"octo","repo":"portal","pull_number":88,"head_sha":"sha-1"}',
            },
        )
        elapsed = time.perf_counter() - start
        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert elapsed < 0.2
    finally:
        cleanup()


def test_github_provider_webhook_review_requested_routes_to_existing_router(monkeypatch):
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    original_secret = None
    original_allow_insecure = None
    try:
        import app.api.provider_webhooks as provider_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request_review_requested",
            target_ref="octo/portal",
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="alice",
            enabled=True,
        )
        original_secret = provider_api.settings.github_webhook_secret
        original_allow_insecure = provider_api.settings.allow_insecure_provider_webhooks
        provider_api.settings.github_webhook_secret = ""
        provider_api.settings.allow_insecure_provider_webhooks = True

        response = client.post(
            "/api/webhooks/github",
            json={
                "action": "review_requested",
                "pull_request": {"number": 15, "head": {"sha": "abc123"}},
                "repository": {"name": "portal", "owner": {"login": "octo"}},
                "requested_reviewer": {"login": "alice"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["resolved_task_type"] == "github_review_task"
        tasks = AgentTaskRepository(db).list_all()
        assert len(tasks) == 1
        assert tasks[0].task_type == "github_review_task"
    finally:
        if original_secret is not None:
            import app.api.provider_webhooks as provider_api

            provider_api.settings.github_webhook_secret = original_secret
            provider_api.settings.allow_insecure_provider_webhooks = original_allow_insecure
        cleanup()


def test_github_provider_webhook_invalid_signature_returns_401(monkeypatch):
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    original_secret = None
    try:
        import app.api.provider_webhooks as provider_api

        original_secret = provider_api.settings.github_webhook_secret
        provider_api.settings.github_webhook_secret = "top-secret"
        response = client.post(
            "/api/webhooks/github",
            headers={"X-Hub-Signature-256": "sha256=bad"},
            json={"action": "review_requested"},
        )
        assert response.status_code == 401
    finally:
        if original_secret is not None:
            import app.api.provider_webhooks as provider_api

            provider_api.settings.github_webhook_secret = original_secret
        cleanup()


def test_jira_provider_webhook_routes_to_workflow_review_task(monkeypatch):
    client, db, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    original_secret = None
    original_allow_insecure = None
    try:
        import app.api.provider_webhooks as provider_api

        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="workflow_review_requested",
            target_ref="EFP",
            enabled=True,
        )
        WorkflowTransitionRuleRepository(db).create(
            system_type="jira",
            project_key="EFP",
            issue_type="Story",
            trigger_status="In Review",
            assignee_binding="jira-assignee-1",
            target_agent_id=agent.id,
            enabled=True,
            config_json='{"strict": true}',
        )
        original_secret = provider_api.settings.jira_webhook_shared_secret
        original_allow_insecure = provider_api.settings.allow_insecure_provider_webhooks
        provider_api.settings.jira_webhook_shared_secret = ""
        provider_api.settings.allow_insecure_provider_webhooks = True
        response = client.post(
            "/api/webhooks/jira",
            json={
                "issue": {
                    "key": "EFP-1",
                    "fields": {
                        "project": {"key": "EFP"},
                        "issuetype": {"name": "Story"},
                        "status": {"name": "In Review"},
                        "assignee": {"accountId": "jira-assignee-1"},
                    },
                }
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["resolved_task_type"] == "jira_workflow_review_task"
    finally:
        if original_secret is not None:
            import app.api.provider_webhooks as provider_api

            provider_api.settings.jira_webhook_shared_secret = original_secret
            provider_api.settings.allow_insecure_provider_webhooks = original_allow_insecure
        cleanup()


def test_jira_provider_webhook_invalid_shared_secret_returns_401():
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    original_secret = None
    original_allow_insecure = None
    try:
        import app.api.provider_webhooks as provider_api

        original_secret = provider_api.settings.jira_webhook_shared_secret
        original_allow_insecure = provider_api.settings.allow_insecure_provider_webhooks
        provider_api.settings.jira_webhook_shared_secret = "jira-top-secret"
        provider_api.settings.allow_insecure_provider_webhooks = False
        response = client.post(
            "/api/webhooks/jira",
            headers={"X-Efp-Webhook-Secret": "bad"},
            json={"issue": {"fields": {}}},
        )
        assert response.status_code == 401
    finally:
        if original_secret is not None:
            import app.api.provider_webhooks as provider_api

            provider_api.settings.jira_webhook_shared_secret = original_secret
            provider_api.settings.allow_insecure_provider_webhooks = original_allow_insecure
        cleanup()


def test_provider_webhook_unsupported_events_return_noop_response():
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    original_allow_insecure = None
    try:
        import app.api.provider_webhooks as provider_api

        original_allow_insecure = provider_api.settings.allow_insecure_provider_webhooks
        provider_api.settings.allow_insecure_provider_webhooks = True
        github_resp = client.post("/api/webhooks/github", json={"action": "opened"})
        jira_resp = client.post("/api/webhooks/jira", json={"foo": "bar"})
        assert github_resp.status_code == 200
        assert github_resp.json()["accepted"] is False
        assert github_resp.json()["routing_reason"] == "unsupported_github_event"
        assert jira_resp.status_code == 200
        assert jira_resp.json()["accepted"] is False
        assert jira_resp.json()["routing_reason"] == "unsupported_jira_event"
    finally:
        if original_allow_insecure is not None:
            import app.api.provider_webhooks as provider_api

            provider_api.settings.allow_insecure_provider_webhooks = original_allow_insecure
        cleanup()


def test_provider_webhooks_return_503_when_secrets_are_unset_by_default():
    client, _db, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides()
    original_gh_secret = None
    original_jira_secret = None
    original_allow_insecure = None
    try:
        import app.api.provider_webhooks as provider_api

        original_gh_secret = provider_api.settings.github_webhook_secret
        original_jira_secret = provider_api.settings.jira_webhook_shared_secret
        original_allow_insecure = provider_api.settings.allow_insecure_provider_webhooks
        provider_api.settings.github_webhook_secret = ""
        provider_api.settings.jira_webhook_shared_secret = ""
        provider_api.settings.allow_insecure_provider_webhooks = False

        github_resp = client.post("/api/webhooks/github", json={"action": "opened"})
        jira_resp = client.post("/api/webhooks/jira", json={"foo": "bar"})
        assert github_resp.status_code == 503
        assert github_resp.json()["detail"] == "GitHub webhook secret is not configured"
        assert jira_resp.status_code == 503
        assert jira_resp.json()["detail"] == "Jira webhook secret is not configured"
    finally:
        if original_gh_secret is not None:
            import app.api.provider_webhooks as provider_api

            provider_api.settings.github_webhook_secret = original_gh_secret
            provider_api.settings.jira_webhook_shared_secret = original_jira_secret
            provider_api.settings.allow_insecure_provider_webhooks = original_allow_insecure
        cleanup()
