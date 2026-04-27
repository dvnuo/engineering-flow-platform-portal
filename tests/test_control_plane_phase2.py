from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.schemas.runtime_router import RuntimeRoutingDecisionResponse
from app.services.auth_service import hash_password
from app.services.runtime_router import RuntimeRouterService


def _build_client_with_overrides(monkeypatch):
    from app.main import app
    import app.api.agents as agents_api
    import app.api.agent_identity_bindings as bindings_api
    import app.api.capability_profiles as capability_api
    import app.api.internal_agents as internal_agents_api
    import app.api.policy_profiles as policy_api
    import app.api.runtime_router as runtime_router_api
    import app.api.agent_groups as groups_api
    import app.deps as deps_module

    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    monkeypatch.setattr(agents_api.k8s_service, "update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

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
        name="Agent One",
        description="desc",
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
        deployment_name="dep-1",
        service_name="svc-1",
        pvc_name="pvc-1",
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

    app.dependency_overrides[capability_api.get_db] = _override_db
    app.dependency_overrides[policy_api.get_db] = _override_db
    app.dependency_overrides[bindings_api.get_current_user] = _override_user
    app.dependency_overrides[bindings_api.get_db] = _override_db
    app.dependency_overrides[runtime_router_api.get_db] = _override_db
    app.dependency_overrides[groups_api.get_current_user] = _override_user
    app.dependency_overrides[groups_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_current_user] = _override_user
    app.dependency_overrides[agents_api.get_db] = _override_db
    app.dependency_overrides[internal_agents_api.get_db] = _override_db
    app.dependency_overrides[deps_module.get_current_user] = _override_user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user_obj):
        state["user"] = user_obj

    return TestClient(app), agent, admin_user, viewer_user, _set_user, _cleanup


def test_capability_profiles_create_and_list(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            "/api/capability-profiles",
            json={"name": "cap-basic", "description": "Basic profile", "tool_set_json": '["shell"]'},
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["name"] == "cap-basic"

        list_resp = client.get("/api/capability-profiles")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["tool_set_json"] == '["shell"]'
    finally:
        cleanup()


def test_policy_profiles_create_and_list(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            "/api/policy-profiles",
            json={"name": "pol-basic", "description": "Policy", "max_parallel_tasks": 3},
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["max_parallel_tasks"] == 3

        list_resp = client.get("/api/policy-profiles")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["name"] == "pol-basic"
    finally:
        cleanup()


def test_policy_profile_create_rejects_invalid_json_rules(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/policy-profiles",
            json={
                "name": "pol-bad-json",
                "permission_rules_json": "not-json",
            },
        )
        assert response.status_code == 422
        assert "permission_rules_json must be valid JSON" in response.text
    finally:
        cleanup()


def test_policy_profile_create_rejects_non_object_json_rules(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/policy-profiles",
            json={
                "name": "pol-bad-shape",
                "transition_rules_json": '["github"]',
            },
        )
        assert response.status_code == 422
        assert "transition_rules_json must decode to a JSON object" in response.text
    finally:
        cleanup()


def test_policy_profile_create_accepts_valid_json_rule_objects(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/policy-profiles",
            json={
                "name": "pol-valid-rules",
                "permission_rules_json": '{"denied_capability_ids": ["tool:shell"]}',
                "transition_rules_json": '{"external_trigger_allowlist": ["github"]}',
                "max_parallel_tasks": 2,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["permission_rules_json"] == '{"denied_capability_ids": ["tool:shell"]}'
        assert body["transition_rules_json"] == '{"external_trigger_allowlist": ["github"]}'
        assert body["max_parallel_tasks"] == 2
    finally:
        cleanup()


def test_capability_profiles_endpoints_are_admin_only(monkeypatch):
    client, _agent, admin_user, viewer_user, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create = client.post("/api/capability-profiles", json={"name": "cap-admin-gate"})
        assert create.status_code == 200
        profile_id = create.json()["id"]

        set_user(viewer_user)
        forbidden_create = client.post("/api/capability-profiles", json={"name": "cap-viewer-denied"})
        assert forbidden_create.status_code == 403
        assert forbidden_create.json()["detail"] == "Admin only"

        forbidden_list = client.get("/api/capability-profiles")
        assert forbidden_list.status_code == 403
        assert forbidden_list.json()["detail"] == "Admin only"

        forbidden_get = client.get(f"/api/capability-profiles/{profile_id}")
        assert forbidden_get.status_code == 403
        assert forbidden_get.json()["detail"] == "Admin only"

        forbidden_resolved = client.get(f"/api/capability-profiles/{profile_id}/resolved")
        assert forbidden_resolved.status_code == 403
        assert forbidden_resolved.json()["detail"] == "Admin only"

        set_user(admin_user)
        allowed_get = client.get(f"/api/capability-profiles/{profile_id}")
        assert allowed_get.status_code == 200
    finally:
        cleanup()


def test_policy_profiles_endpoints_are_admin_only(monkeypatch):
    client, _agent, admin_user, viewer_user, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create = client.post("/api/policy-profiles", json={"name": "pol-admin-gate"})
        assert create.status_code == 200
        profile_id = create.json()["id"]

        set_user(viewer_user)
        forbidden_create = client.post("/api/policy-profiles", json={"name": "pol-viewer-denied"})
        assert forbidden_create.status_code == 403
        assert forbidden_create.json()["detail"] == "Admin only"

        forbidden_list = client.get("/api/policy-profiles")
        assert forbidden_list.status_code == 403
        assert forbidden_list.json()["detail"] == "Admin only"

        forbidden_get = client.get(f"/api/policy-profiles/{profile_id}")
        assert forbidden_get.status_code == 403
        assert forbidden_get.json()["detail"] == "Admin only"

        set_user(admin_user)
        allowed_get = client.get(f"/api/policy-profiles/{profile_id}")
        assert allowed_get.status_code == 200
    finally:
        cleanup()


def test_identity_bindings_create_and_list_for_agent(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={
                "system_type": "github",
                "external_account_id": "acct-123",
                "username": "octocat",
                "scope_json": '{"repos": ["engineering-flow-platform-portal"]}',
                "enabled": True,
            },
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["system_type"] == "github"

        list_resp = client.get(f"/api/agents/{agent.id}/identity-bindings")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["external_account_id"] == "acct-123"
    finally:
        cleanup()


def test_agent_response_includes_additive_control_plane_fields(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.get(f"/api/agents/{agent.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["agent_type"] == "workspace"
        assert "capability_profile_id" in body
        assert "policy_profile_id" in body
    finally:
        cleanup()


def test_runtime_router_resolves_agent_by_binding(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_binding_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "jira", "external_account_id": "jira-user-7", "enabled": True},
        )
        assert create_binding_resp.status_code == 200

        resolve_resp = client.post(
            "/api/runtime-router/resolve-binding",
            json={"system_type": "JIRA", "external_account_id": "jira-user-7"},
        )
        assert resolve_resp.status_code == 200
        body = resolve_resp.json()
        assert body["matched_agent_id"] == agent.id
        assert body["matched_agent_type"] == "workspace"
        assert body["capability_profile_id"] is None
        assert body["policy_profile_id"] is None
        assert body["execution_mode"] == "sync"
        assert body["reason"] == "matched_enabled_binding"
        assert body["runtime_target"]["agent_id"] == agent.id
        assert body["runtime_target"]["namespace"] == "efp-agents"
    finally:
        cleanup()


def test_runtime_router_resolve_binding_is_admin_only(monkeypatch):
    client, agent, _admin_user, viewer_user, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_binding_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "jira", "external_account_id": "jira-user-7", "enabled": True},
        )
        assert create_binding_resp.status_code == 200

        set_user(viewer_user)
        forbidden = client.post(
            "/api/runtime-router/resolve-binding",
            json={"system_type": "JIRA", "external_account_id": "jira-user-7"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "Admin only"
    finally:
        cleanup()


def test_runtime_router_returns_none_when_no_binding_exists(monkeypatch):
    _client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        service = RuntimeRouterService()
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        try:
            found = service.find_agent_for_identity_binding("slack", "missing", db)
            assert found is None

            decision = service.resolve_binding_decision("slack", "missing", db)
            assert isinstance(decision, RuntimeRoutingDecisionResponse)
            assert decision.matched_agent_id is None
            assert decision.reason == "no_enabled_binding"
            assert decision.runtime_target is None
        finally:
            db.close()
    finally:
        cleanup()


def test_runtime_router_service_returns_typed_response(monkeypatch):
    _client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        try:
            user = User(username="owner2", password_hash=hash_password("pw"), role="admin", is_active=True)
            db.add(user)
            db.commit()
            db.refresh(user)

            routed_agent = Agent(
                name="Agent Routed",
                description="desc",
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
                deployment_name="dep-1",
                service_name="svc-1",
                pvc_name="pvc-1",
                endpoint_path="/",
                agent_type="workspace",
            )
            db.add(routed_agent)
            db.commit()
            db.refresh(routed_agent)

            AgentIdentityBindingRepository(db).create(
                agent_id=routed_agent.id,
                system_type="github",
                external_account_id="acct-typed",
                enabled=True,
            )

            service = RuntimeRouterService()
            decision = service.resolve_binding_decision("GitHub", "acct-typed", db)
            assert isinstance(decision, RuntimeRoutingDecisionResponse)
            assert decision.matched_agent_id == routed_agent.id
            assert decision.runtime_target is not None
            assert decision.runtime_target.agent_id == routed_agent.id
        finally:
            db.close()
    finally:
        _ = agent
        cleanup()


def test_create_agent_rejects_nonexistent_capability_profile(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/agents",
            json={
                "name": "new-agent",
                "image": "example/image:latest",
                "capability_profile_id": "missing-capability",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "CapabilityProfile not found"
    finally:
        cleanup()


def test_create_agent_rejects_nonexistent_policy_profile(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/agents",
            json={
                "name": "new-agent",
                "image": "example/image:latest",
                "policy_profile_id": "missing-policy",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "PolicyProfile not found"
    finally:
        cleanup()


def test_update_agent_rejects_invalid_profile_references(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cap_resp = client.patch(f"/api/agents/{agent.id}", json={"capability_profile_id": "missing-capability"})
        assert cap_resp.status_code == 404
        assert cap_resp.json()["detail"] == "CapabilityProfile not found"

        policy_resp = client.patch(f"/api/agents/{agent.id}", json={"policy_profile_id": "missing-policy"})
        assert policy_resp.status_code == 404
        assert policy_resp.json()["detail"] == "PolicyProfile not found"
    finally:
        cleanup()


def test_update_agent_allows_clearing_profile_references(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cap_create = client.post("/api/capability-profiles", json={"name": "cap-for-update"})
        policy_create = client.post("/api/policy-profiles", json={"name": "policy-for-update"})
        assert cap_create.status_code == 200
        assert policy_create.status_code == 200

        set_resp = client.patch(
            f"/api/agents/{agent.id}",
            json={
                "capability_profile_id": cap_create.json()["id"],
                "policy_profile_id": policy_create.json()["id"],
            },
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["capability_profile_id"] == cap_create.json()["id"]
        assert set_resp.json()["policy_profile_id"] == policy_create.json()["id"]

        clear_resp = client.patch(
            f"/api/agents/{agent.id}",
            json={"capability_profile_id": None, "policy_profile_id": None},
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["capability_profile_id"] is None
        assert clear_resp.json()["policy_profile_id"] is None
    finally:
        cleanup()


def test_capability_profile_create_invalid_json_returns_400(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        resp = client.post(
            "/api/capability-profiles",
            json={"name": "cap-bad-json", "tool_set_json": "not-json"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "tool_set_json must be valid JSON"
    finally:
        cleanup()


def test_capability_profile_create_rejects_unknown_allowed_actions(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        resp = client.post(
            "/api/capability-profiles",
            json={"name": "cap-bad-action", "allowed_actions_json": '["approve"]'},
        )
        assert resp.status_code == 400
        assert "unknown or ambiguous action: approve" in resp.json()["detail"]
    finally:
        cleanup()


def test_capability_profile_create_rejects_ambiguous_allowed_actions(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        resp = client.post(
            "/api/capability-profiles",
            json={"name": "cap-ambiguous-action", "allowed_actions_json": '["add_comment"]'},
        )
        assert resp.status_code == 400
        assert "unknown or ambiguous action: add_comment" in resp.json()["detail"]
    finally:
        cleanup()


def test_capability_profile_update_rejects_logical_duplicate_allowed_actions(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create = client.post("/api/capability-profiles", json={"name": "cap-dup-action"})
        assert create.status_code == 200
        profile_id = create.json()["id"]

        update = client.patch(
            f"/api/capability-profiles/{profile_id}",
            json={"allowed_actions_json": '["review_pull_request","adapter:github:review_pull_request"]'},
        )
        assert update.status_code == 400
        assert "duplicate logical action" in update.json()["detail"]
    finally:
        cleanup()


def test_capability_profile_resolved_endpoint_update_and_delete(monkeypatch):
    client, _agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create = client.post(
            "/api/capability-profiles",
            json={
                "name": "cap-resolve",
                "tool_set_json": '["shell"]',
                "channel_set_json": '["chat"]',
                "skill_set_json": '["review"]',
            },
        )
        assert create.status_code == 200
        profile_id = create.json()["id"]

        resolved = client.get(f"/api/capability-profiles/{profile_id}/resolved")
        assert resolved.status_code == 200
        assert resolved.json()["resolved"]["tool_set"] == ["shell"]
        assert resolved.json()["resolved"]["channel_set"] == ["chat"]
        assert resolved.json()["resolved"]["skill_set"] == ["review"]
        assert resolved.json()["resolved"]["runtime_capability_catalog_source"] in {"seed_fallback", "settings_snapshot", "runtime_api"}
        assert resolved.json()["resolved"]["catalog_validation_mode"] in {"seed_fallback", "full_snapshot"}

        updated = client.patch(
            f"/api/capability-profiles/{profile_id}",
            json={"allowed_actions_json": '["review_pull_request"]'},
        )
        assert updated.status_code == 200
        assert updated.json()["allowed_actions_json"] == '["review_pull_request"]'

        deleted = client.delete(f"/api/capability-profiles/{profile_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}

        missing = client.get(f"/api/capability-profiles/{profile_id}")
        assert missing.status_code == 404
    finally:
        cleanup()


def test_capability_profile_patch_and_delete_are_admin_only(monkeypatch):
    client, _agent, admin_user, viewer_user, set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create = client.post("/api/capability-profiles", json={"name": "cap-admin-only"})
        assert create.status_code == 200
        profile_id = create.json()["id"]

        set_user(viewer_user)
        forbidden_patch = client.patch(f"/api/capability-profiles/{profile_id}", json={"description": "x"})
        assert forbidden_patch.status_code == 403
        assert forbidden_patch.json()["detail"] == "Admin only"

        forbidden_delete = client.delete(f"/api/capability-profiles/{profile_id}")
        assert forbidden_delete.status_code == 403
        assert forbidden_delete.json()["detail"] == "Admin only"

        set_user(admin_user)
        allowed_patch = client.patch(f"/api/capability-profiles/{profile_id}", json={"description": "ok"})
        assert allowed_patch.status_code == 200

        allowed_delete = client.delete(f"/api/capability-profiles/{profile_id}")
        assert allowed_delete.status_code == 200
    finally:
        cleanup()


def test_internal_agent_runtime_context_endpoint_returns_effective_context(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cap = client.post(
            "/api/capability-profiles",
            json={
                "name": "cap-runtime-context",
                "tool_set_json": '["shell"]',
                "channel_set_json": '["jira_get_issue"]',
                "skill_set_json": '["Review"]',
                "allowed_external_systems_json": '["github"]',
                "allowed_webhook_triggers_json": '["pull_request_review_requested"]',
                "allowed_actions_json": '["review_pull_request"]',
            },
        ).json()
        policy = client.post(
            "/api/policy-profiles",
            json={
                "name": "policy-runtime-context",
                "auto_run_rules_json": '{"require_explicit_allow": true, "allow_auto_run": false}',
                "permission_rules_json": '{"denied_capability_ids": [" tool:shell ", ""], "denied_actions": [" adapter:github:add_comment "]}',
                "transition_rules_json": '{"external_trigger_allowlist": [" github ", "   ", "jira"], "external_trigger_blocklist": [" slack "]}',
                "max_parallel_tasks": 2,
            },
        ).json()

        updated = client.patch(
            f"/api/agents/{agent.id}",
            json={"capability_profile_id": cap["id"], "policy_profile_id": policy["id"]},
        )
        assert updated.status_code == 200

        runtime_ctx = client.get(
            f"/api/internal/agents/{agent.id}/runtime-context",
        )
        assert runtime_ctx.status_code == 200
        body = runtime_ctx.json()
        assert body["agent_id"] == agent.id
        assert body["capability_profile_id"] == cap["id"]
        assert body["policy_profile_id"] == policy["id"]
        assert body["capability_context"]["capability_profile_id"] == cap["id"]
        assert "tool:shell" in body["capability_context"]["allowed_capability_ids"]
        assert "skill:review" in body["capability_context"]["allowed_capability_ids"]
        assert "channel_action:jira_get_issue" in body["capability_context"]["allowed_capability_ids"]
        assert "adapter:github:review_pull_request" in body["capability_context"]["allowed_capability_ids"]
        assert body["capability_context"]["allowed_adapter_actions"] == ["adapter:github:review_pull_request"]
        assert body["capability_context"]["unresolved_actions"] == []
        assert body["capability_context"]["resolved_action_mappings"] == {
            "review_pull_request": "adapter:github:review_pull_request"
        }
        assert body["policy_context"]["policy_profile_id"] == policy["id"]
        assert body["policy_context"]["max_parallel_tasks"] == 2
        assert body["policy_context"]["derived_runtime_rules"]["governance_require_explicit_allow"] is True
        assert body["policy_context"]["derived_runtime_rules"]["governance_allow_auto_run"] is False
        assert body["policy_context"]["derived_runtime_rules"]["governance_external_allowlist"] == ["github", "jira"]
        assert body["policy_context"]["derived_runtime_rules"]["governance_external_blocklist"] == ["slack"]
        assert body["policy_context"]["derived_runtime_rules"]["denied_capability_ids"] == ["tool:shell"]
        assert body["policy_context"]["derived_runtime_rules"]["denied_adapter_actions"] == ["adapter:github:add_comment"]
    finally:
        cleanup()


def test_runtime_router_and_internal_runtime_context_expose_consistent_capability_fields(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        cap = client.post(
            "/api/capability-profiles",
            json={
                "name": "cap-consistency",
                "tool_set_json": '["shell"]',
                "skill_set_json": '["review"]',
                "allowed_external_systems_json": '["github"]',
                "allowed_webhook_triggers_json": '["pull_request_review_requested"]',
                "allowed_actions_json": '["review_pull_request"]',
            },
        ).json()

        set_profile = client.patch(f"/api/agents/{agent.id}", json={"capability_profile_id": cap["id"]})
        assert set_profile.status_code == 200

        create_binding = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "github", "external_account_id": "acct-consistency", "enabled": True},
        )
        assert create_binding.status_code == 200

        routing = client.post(
            "/api/runtime-router/resolve-binding",
            json={"system_type": "github", "external_account_id": "acct-consistency"},
        )
        assert routing.status_code == 200
        routing_ctx = routing.json()["capability_context"]

        internal = client.get(
            f"/api/internal/agents/{agent.id}/runtime-context",
        )
        assert internal.status_code == 200
        internal_ctx = internal.json()["capability_context"]

        keys = {
            "allowed_capability_ids",
            "allowed_capability_types",
            "allowed_actions",
            "allowed_adapter_actions",
            "unresolved_actions",
            "resolved_action_mappings",
        }
        for key in keys:
            assert routing_ctx[key] == internal_ctx[key]
    finally:
        cleanup()


def test_identity_bindings_duplicate_enabled_conflict(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        first_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "GitHub", "external_account_id": "acct-123", "enabled": True},
        )
        assert first_resp.status_code == 200
        assert first_resp.json()["system_type"] == "github"

        duplicate_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "github", "external_account_id": "acct-123", "enabled": True},
        )
        assert duplicate_resp.status_code == 409
        assert duplicate_resp.json()["detail"] == "Identity binding already exists for this agent/system/account"
    finally:
        cleanup()


def test_identity_bindings_duplicate_conflict_when_existing_is_disabled(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        first_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "github", "external_account_id": "acct-disabled", "enabled": False},
        )
        assert first_resp.status_code == 200

        duplicate_resp = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "github", "external_account_id": "acct-disabled", "enabled": True},
        )
        assert duplicate_resp.status_code == 409
        assert duplicate_resp.json()["detail"] == "Identity binding already exists for this agent/system/account"
    finally:
        cleanup()


def test_identity_bindings_integrity_error_has_same_duplicate_message(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        import app.api.agent_identity_bindings as bindings_api
        from sqlalchemy.exc import IntegrityError

        def _raise_integrity(*args, **kwargs):
            raise IntegrityError("insert", {}, Exception("duplicate"))

        monkeypatch.setattr(bindings_api.AgentIdentityBindingRepository, "create", _raise_integrity)

        response = client.post(
            f"/api/agents/{agent.id}/identity-bindings",
            json={"system_type": "github", "external_account_id": "acct-race", "enabled": True},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Identity binding already exists for this agent/system/account"
    finally:
        cleanup()


def test_create_agent_group_success(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        response = client.post(
            "/api/agent-groups",
            json={
                "name": "Group Alpha",
                "leader_agent_id": agent.id,
                "shared_context_policy_json": '{"scope":"issue"}',
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Group Alpha"
        assert body["leader_agent_id"] == agent.id
    finally:
        cleanup()


def test_create_group_auto_writes_leader_member_and_detail_has_members(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            "/api/agent-groups",
            json={"name": "Group Beta", "leader_agent_id": agent.id},
        )
        assert create_resp.status_code == 200
        group = create_resp.json()

        leader_members = [m for m in group["members"] if m["role"] == "leader"]
        assert len(leader_members) == 1
        assert leader_members[0]["member_type"] == "agent"
        assert leader_members[0]["agent_id"] == agent.id

        detail_resp = client.get(f"/api/agent-groups/{group['id']}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert any(m["role"] == "leader" for m in detail["members"])
    finally:
        cleanup()


def test_group_cannot_have_second_leader_member(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        create_resp = client.post(
            "/api/agent-groups",
            json={"name": "Group Gamma", "leader_agent_id": agent.id},
        )
        assert create_resp.status_code == 200
        group = create_resp.json()

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": agent.id, "role": "leader"},
        )
        assert add_resp.status_code == 409
        assert add_resp.json()["detail"] == "Group already has a leader member"
    finally:
        cleanup()


def test_internal_runtime_context_includes_runtime_profile_context(monkeypatch):
    client, agent, _admin_user, _viewer_user, _set_user, cleanup = _build_client_with_overrides(monkeypatch)
    try:
        from app.main import app
        import app.api.internal_agents as internal_agents_api
        from app.models.runtime_profile import RuntimeProfile

        db_gen = app.dependency_overrides[internal_agents_api.get_db]()
        db = next(db_gen)
        rp = RuntimeProfile(owner_user_id=agent.owner_user_id, name="rp-ctx", config_json='{"llm": {"provider": "openai", "temperature": 0.4}, "ssh": {"hack": true}}', revision=3, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        agent.runtime_profile_id = rp.id
        db.add(agent)
        db.commit()

        resp = client.get(f"/api/internal/agents/{agent.id}/runtime-context", )
        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_profile_id"] == rp.id
        assert body["runtime_profile_context"]["runtime_profile_id"] == rp.id
        assert body["runtime_profile_context"]["name"] == "rp-ctx"
        assert body["runtime_profile_context"]["revision"] == 3
        assert body["runtime_profile_context"]["managed_sections"] == ["llm", "proxy", "jira", "confluence", "github", "git", "debug"]
        assert body["runtime_profile_context"]["source"] == "portal.runtime_profile"
        assert "config" in body["runtime_profile_context"]
        assert body["runtime_profile_context"]["config"] == {"llm": {"provider": "openai", "temperature": 0.4}}
        assert "ssh" not in body["runtime_profile_context"]["config"]
        assert "capability_context" in body
        assert "policy_context" in body
    finally:
        cleanup()
