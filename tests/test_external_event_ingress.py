import json
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
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.runtime_router import RuntimeRoutingDecisionResponse
from app.services.task_dispatcher import AgentTaskDispatchResult
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.external_event_ingress as ingress_api

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
        assert tasks[0].task_type == "github_review_task"
        assert tasks[0].status == "done"
        payload = json.loads(tasks[0].input_payload_json)
        assert payload["owner"] == "octo"
        assert payload["repo"] == "portal"
        assert payload["pull_number"] == 15
        assert payload["subscription_id"] in body["matched_subscription_ids"]
    finally:
        cleanup()


def test_dedupe_key_prevents_duplicate_task_creation():
    client, db, agent, cleanup = _build_client_with_overrides()
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
    client, db, agent, cleanup = _build_client_with_overrides()
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


def test_jira_ingest_rejects_bad_persisted_rule_config():
    client, db, agent, cleanup = _build_client_with_overrides()
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
    client, db, agent, cleanup = _build_client_with_overrides()
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
    client, db, agent, cleanup = _build_client_with_overrides()
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
    client, db, agent, cleanup = _build_client_with_overrides()
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
    client, db, agent, cleanup = _build_client_with_overrides()
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


def test_github_review_event_dispatch_failed_returns_dispatch_failed(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
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

        async def _failed_dispatch(*args, **kwargs):
            return AgentTaskDispatchResult(
                dispatched=True,
                task_id="task-1",
                runtime_status_code=500,
                task_status="failed",
                message="Runtime execution reported failure",
                result_payload_json='{"ok": false}',
            )

        monkeypatch.setattr(ingress_api.service.task_dispatcher, "dispatch_task", _failed_dispatch)

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
        assert body["accepted"] is False
        assert body["routing_reason"] == "dispatch_failed"
        assert body["created_task_id"] is not None
    finally:
        cleanup()


def test_github_review_event_dedupe_hint_prevents_duplicate_without_dedupe_key():
    client, db, agent, cleanup = _build_client_with_overrides()
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


def test_github_review_event_rejected_when_repo_not_allowed():
    client, db, agent, cleanup = _build_client_with_overrides()
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


def test_jira_ingress_returns_dispatch_failed_when_dispatch_fails(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
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

        async def _failed_dispatch(*args, **kwargs):
            return AgentTaskDispatchResult(
                dispatched=True,
                task_id="task-1",
                runtime_status_code=500,
                task_status="failed",
                message="Runtime returned non-2xx status",
                result_payload_json='{"ok": false}',
            )

        monkeypatch.setattr(ingress_api.service.task_dispatcher, "dispatch_task", _failed_dispatch)

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
        assert body["routing_reason"] == "dispatch_failed"
        assert body["created_task_id"] is not None
    finally:
        cleanup()
