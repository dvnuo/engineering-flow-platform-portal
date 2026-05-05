import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, CapabilityProfile, PolicyProfile, User
from app.models.runtime_profile import RuntimeProfile
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService


def _build_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    rp = RuntimeProfile(owner_user_id=owner.id, name="rp-sync-service", config_json='{"llm": {"provider": "openai"}, "ssh": {"x":1}}', revision=3, is_default=True)
    db.add(rp)
    db.commit()
    db.refresh(rp)

    running = Agent(
        name="running-agent",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-running",
        service_name="svc-running",
        pvc_name="pvc-running",
        endpoint_path="/",
        agent_type="workspace",
        runtime_profile_id=rp.id,
    )
    stopped = Agent(
        name="stopped-agent",
        owner_user_id=owner.id,
        visibility="private",
        status="stopped",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-stopped",
        service_name="svc-stopped",
        pvc_name="pvc-stopped",
        endpoint_path="/",
        agent_type="workspace",
        runtime_profile_id=rp.id,
    )
    db.add_all([running, stopped])
    db.commit()
    db.refresh(running)
    db.refresh(stopped)

    return db, rp, running, stopped


def test_push_payload_to_agent_swallows_forward_exception(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))

        async def _raise_forward(**_kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(service.proxy_service, "forward", _raise_forward)

        result = asyncio.run(service.push_payload_to_agent(running, service.build_apply_payload_from_profile(rp)))
        assert result.ok is False
    finally:
        db.close()


def test_sync_profile_to_bound_agents_collects_failures_without_raising(monkeypatch):
    db, rp, running, stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))

        async def _raise_forward(**_kwargs):
            raise RuntimeError("runtime unreachable")

        monkeypatch.setattr(service.proxy_service, "forward", _raise_forward)

        result = asyncio.run(service.sync_profile_to_bound_agents(db, rp))
        assert result["updated_running_count"] == 0
        assert result["skipped_not_running_count"] == 1
        assert running.id in result["failed_agent_ids"]
        assert stopped.id not in result["failed_agent_ids"]
    finally:
        db.close()


def test_push_payload_to_agent_uses_content_type_and_portal_trusted_headers(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        captured = {}

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(service.proxy_service, "forward", _fake_forward)

        result = asyncio.run(service.push_payload_to_agent(running, service.build_apply_payload_from_profile(rp)))
        assert result.ok is True
        assert captured["headers"] == {"content-type": "application/json"}
        assert captured["extra_headers"] == {"X-Portal-Author-Source": "portal"}
    finally:
        db.close()


def test_push_payload_to_agent_pending_restart_is_not_failure(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        async def _fake_forward(**_kwargs):
            return 200, b'{"ok": true, "status": "pending_restart", "pending_restart": true}', "application/json"
        monkeypatch.setattr(service.proxy_service, "forward", _fake_forward)
        result = asyncio.run(service.push_payload_to_agent(running, service.build_apply_payload_from_profile(rp)))
        assert result.ok is True
        assert result.pending_restart is True
    finally:
        db.close()


def test_sync_profile_to_bound_agents_collects_pending_and_partial(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        async def _fake_push(_agent, _payload):
            from app.services.runtime_profile_sync_service import RuntimeProfilePushResult
            return RuntimeProfilePushResult(agent_id=running.id, ok=True, status_code=200, apply_status="partially_applied", partially_applied=True)
        monkeypatch.setattr(service, "push_payload_to_agent", _fake_push)
        result = asyncio.run(service.sync_profile_to_bound_agents(db, rp))
        assert result["failed_agent_ids"] == []
        assert result["partially_applied_agent_ids"] == [running.id]
        assert result["updated_running_count"] == 1
    finally:
        db.close()


def test_build_apply_payload_from_profile_keeps_shape_and_uses_raw_profile_config():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm": {"provider": "openai"}}'
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert set(payload.keys()) == {"runtime_profile_id", "revision", "config"}
        assert payload["runtime_profile_id"] == rp.id
        assert payload["revision"] == rp.revision
        assert payload["config"] == {"llm": {"provider": "openai"}}
    finally:
        db.close()


def test_build_apply_payload_from_sparse_legacy_profile_does_not_backfill_creation_seed():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert set(payload.keys()) == {"runtime_profile_id", "revision", "config"}
        assert payload["config"] == {}
    finally:
        db.close()


def test_build_apply_payload_from_profile_includes_context_budget_when_present():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"openai","context_budget":{"tool_loop":{"max_prompt_tokens":32000}}}}'
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert payload["config"]["llm"]["context_budget"]["tool_loop"]["max_prompt_tokens"] == 32000
    finally:
        db.close()


def test_build_apply_payload_from_profile_includes_response_flow_when_present():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = (
            '{"llm":{"provider":"openai","response_flow":{"plan_policy":"explicit_or_complex","staging_policy":"always",'
            '"default_skill_execution_style":"direct","ask_user_policy":"blocked_only","active_skill_conflict_policy":"always_ask","complexity_prompt_budget_ratio":0.85,'
            '"complexity_min_request_tokens":24000}}}'
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert payload["config"]["llm"]["response_flow"]["plan_policy"] == "explicit_or_complex"
        assert payload["config"]["llm"]["response_flow"]["staging_policy"] == "always"
        assert payload["config"]["llm"]["response_flow"]["default_skill_execution_style"] == "direct"
        assert payload["config"]["llm"]["response_flow"]["ask_user_policy"] == "blocked_only"
        assert payload["config"]["llm"]["response_flow"]["active_skill_conflict_policy"] == "always_ask"
        assert payload["config"]["llm"]["response_flow"]["complexity_prompt_budget_ratio"] == 0.85
        assert payload["config"]["llm"]["response_flow"]["complexity_min_request_tokens"] == 24000
    finally:
        db.close()


def test_build_apply_payload_for_agent_adds_skill_aliases_and_filters_broad_types():
    db, rp, running, _stopped = _build_db()
    try:
        capability = CapabilityProfile(name="cp1", skill_set_json='["review-pull-request"]')
        policy = PolicyProfile(name="pp1", permission_rules_json='{"denied_capability_types":["tool"]}')
        db.add_all([capability, policy])
        db.commit()
        db.refresh(capability)
        db.refresh(policy)
        running.capability_profile_id = capability.id
        running.policy_profile_id = policy.id
        db.add(running)
        db.commit()
        db.refresh(running)

        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        original_config = rp.config_json
        payload = service.build_apply_payload_for_agent(db, running, rp)
        assert rp.config_json == original_config
        assert "skill:review-pull-request" in payload["config"]["allowed_capability_ids"]
        assert "opencode.skill.review-pull-request" in payload["config"]["allowed_capability_ids"]
        assert "skill" not in payload["config"]["allowed_capability_types"]
        assert "review-pull-request" in payload["config"]["capability_context"]["skill_set"]
    finally:
        db.close()


def test_sync_profile_to_bound_agents_builds_payload_per_running_agent(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        running_two = Agent(
            name="running-agent-2",
            owner_user_id=running.owner_user_id,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url=None,
            branch=None,
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-running-2",
            service_name="svc-running-2",
            pvc_name="pvc-running-2",
            endpoint_path="/",
            agent_type="workspace",
            runtime_profile_id=rp.id,
        )
        db.add(running_two)
        db.commit()
        db.refresh(running_two)
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        built_for = []

        def _build(db_s, agent_s, rp_s):
            built_for.append(agent_s.id)
            return {"runtime_profile_id": rp_s.id, "revision": rp_s.revision, "config": {"agent_id": agent_s.id}}

        async def _push(_agent, _payload):
            return True

        monkeypatch.setattr(service, "build_apply_payload_for_agent", _build)
        monkeypatch.setattr(service, "push_payload_to_agent", _push)
        result = asyncio.run(service.sync_profile_to_bound_agents(db, rp))
        assert set(built_for) == {running.id, running_two.id}
        assert result["updated_running_count"] == 2
    finally:
        db.close()


def test_build_apply_payload_for_agent_preserves_runtime_profile_allowlists_while_merging_context():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = (
            '{"llm":{"provider":"openai"},'
            '"allowed_capability_ids":["tool:runtime-only","opencode.skill.existing-skill"],'
            '"allowed_capability_types":["adapter_action","skill","tool"],'
            '"allowed_external_systems":["github"],'
            '"allowed_actions":["runtime.action"],'
            '"allowed_adapter_actions":["runtime.adapter"],'
            '"derived_runtime_rules":{"from_runtime_profile":true}}'
        )
        capability = CapabilityProfile(
            name="cp-merge",
            skill_set_json='["review-pull-request"]',
            allowed_external_systems_json='["jira"]',
            allowed_actions_json='["jira.transition"]',
        )
        policy = PolicyProfile(
            name="pp-merge",
            auto_run_rules_json='{"require_explicit_allow": true}',
            permission_rules_json="{}",
        )
        db.add_all([capability, policy, rp])
        db.commit()
        db.refresh(rp)
        running.capability_profile_id = capability.id
        running.policy_profile_id = policy.id
        db.add(running)
        db.commit()
        db.refresh(running)

        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        original_config = rp.config_json
        payload = service.build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]
        assert rp.config_json == original_config
        assert "tool:runtime-only" in cfg["allowed_capability_ids"]
        assert "opencode.skill.existing-skill" in cfg["allowed_capability_ids"]
        assert "skill:review-pull-request" in cfg["allowed_capability_ids"]
        assert "opencode.skill.review-pull-request" in cfg["allowed_capability_ids"]
        assert "adapter_action" in cfg["allowed_capability_types"]
        assert "skill" not in cfg["allowed_capability_types"]
        assert "tool" not in cfg["allowed_capability_types"]
        assert "github" in cfg["allowed_external_systems"]
        assert "jira" in cfg["allowed_external_systems"]
        assert "runtime.action" in cfg["allowed_actions"]
        assert "runtime.adapter" in cfg["allowed_adapter_actions"]
        assert cfg["derived_runtime_rules"]["from_runtime_profile"] is True
        assert cfg["derived_runtime_rules"]["governance_require_explicit_allow"] is True
        assert "review-pull-request" in cfg["capability_context"]["skill_set"]
        assert isinstance(cfg["policy_context"], dict)
    finally:
        db.close()
