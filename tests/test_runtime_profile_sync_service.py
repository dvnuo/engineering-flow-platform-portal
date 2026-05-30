import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService


OPENCODE_RESTRICTION_KEYS = {
    "enabled_tools",
    "disabled_tools",
    "tool_permissions",
    "allowed_external_systems",
    "allowed_actions",
    "allowed_adapter_actions",
    "allowed_capability_ids",
    "allowed_capability_types",
    "resolved_action_mappings",
    "unresolved_tools",
    "unresolved_skills",
    "unresolved_channels",
    "unresolved_actions",
    "skill_details",
    "allowed_skills",
    "denied_skills",
    "denied_actions",
    "denied_capability_types",
    "skill_set",
    "policy_context",
    "derived_runtime_rules",
}


def _assert_no_opencode_restriction_keys(config: dict) -> None:
    for key in OPENCODE_RESTRICTION_KEYS:
        assert key not in config


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
        assert payload["config"] == {
            "llm": {"provider": "openai", "tools": ["*"]},
        }
    finally:
        db.close()


def test_build_apply_payload_from_profile_preserves_proxy_no_proxy():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = (
            '{"proxy":{"enabled":true,"url":"http://proxy.local:8080",'
            '"no_proxy":"127.0.0.1,localhost,.svc,.cluster.local","unknown":"drop"}}'
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)

        assert payload["config"]["proxy"] == {
            "enabled": True,
            "url": "http://proxy.local:8080",
            "no_proxy": "127.0.0.1,localhost,.svc,.cluster.local",
        }
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
        assert payload["config"] == {
            "llm": {"tools": ["*"]},
        }
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


def test_build_apply_payload_from_profile_preserves_runtime_v2_config_surface():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = (
            '{"llm":{"provider":"github_copilot","model":"gpt-5-mini"},'
            '"enabled_tools":["bash","read"],'
            '"disabled_tools":["webfetch"],'
            '"tool_permissions":{"bash":"ask"},'
            '"max_iterations":6,'
            '"doom_loop_threshold":null,'
            '"active_skills":["review"],'
            '"skill_directories":["/app/skills"],'
            '"command_directories":["/workspace/.efp/commands"],'
            '"enable_command_expansion":true,'
            '"max_context_parts":12,'
            '"max_context_chars":200000,'
            '"max_context_tokens":64000,'
            '"context_reserve_chars":4000,'
            '"context_reserve_tokens":1200,'
            '"compaction_auto":true,'
            '"compaction_prune":false,'
            '"compaction_tail_turns":8,'
            '"compaction_preserve_recent_chars":12000,'
            '"compaction_preserve_recent_tokens":4800,'
            '"compaction_reserved_chars":6000,'
            '"compaction_tool_output_max_chars":24000,'
            '"compaction_prune_min_chars":20000,'
            '"compaction_prune_protect_chars":40000,'
            '"enable_compaction_summarizer":true,'
            '"enable_context_overflow_retry":true,'
            '"enable_session_revert_snapshots":true,'
            '"include_default_system_prompt":true,'
            '"include_environment_context":false,'
            '"include_runtime_reminders":true,'
            '"system_prompt_texts":["system"],'
            '"system_prompt_paths":["/workspace/SYSTEM.md"],'
            '"max_system_prompt_chars":30000,'
            '"include_default_instructions":true,'
            '"attach_read_instructions":false,'
            '"instruction_texts":["instruction"],'
            '"instruction_paths":["/workspace/AGENTS.md"],'
            '"max_instruction_chars":28000,'
            '"include_skill_sidecar_content":true,'
            '"max_skill_sidecar_chars":7000,'
            '"max_command_chars":25000,'
            '"resolve_prompt_references":true,'
            '"max_prompt_reference_chars":18000,'
            '"max_prompt_directory_entries":300,'
            '"inject_background_task_results":false,'
            '"emit_llm_stream_events":true,'
            '"track_usage":false,'
            '"tool_output_max_lines":500,'
            '"tool_output_max_bytes":131072,'
            '"tool_output_truncation_direction":"tail",'
            '"archive_truncated_tool_outputs":true,'
            '"tool_output_dir":"/workspace/.efp/tool-output",'
            '"runtime_mode":"plan",'
            '"enable_plan_tool":true,'
            '"plan_mode_read_only":false,'
            '"enable_question_tool":true,'
            '"enable_lsp_tool":false,'
            '"model_aware_tool_selection":true,'
            '"structured_output_schema":{"type":"object"}}'
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        cfg = payload["config"]

        assert cfg["llm"]["provider"] == "github_copilot"
        assert cfg["llm"]["model"] == "gpt-5-mini"
        assert "tools" not in cfg["llm"]
        assert cfg["enabled_tools"] == ["bash", "read"]
        assert cfg["disabled_tools"] == ["webfetch"]
        assert cfg["tool_permissions"] == {"bash": "ask"}
        assert cfg["max_iterations"] == 6
        assert cfg["doom_loop_threshold"] is None
        assert cfg["active_skills"] == ["review"]
        assert cfg["skill_directories"] == ["/app/skills"]
        assert cfg["command_directories"] == ["/workspace/.efp/commands"]
        assert cfg["enable_command_expansion"] is True
        assert cfg["max_context_parts"] == 12
        assert cfg["compaction_auto"] is True
        assert cfg["compaction_prune"] is False
        assert cfg["compaction_preserve_recent_tokens"] == 4800
        assert cfg["compaction_prune_min_chars"] == 20000
        assert cfg["enable_session_revert_snapshots"] is True
        assert cfg["include_default_system_prompt"] is True
        assert cfg["include_environment_context"] is False
        assert cfg["include_runtime_reminders"] is True
        assert cfg["system_prompt_paths"] == ["/workspace/SYSTEM.md"]
        assert cfg["max_system_prompt_chars"] == 30000
        assert cfg["include_default_instructions"] is True
        assert cfg["attach_read_instructions"] is False
        assert cfg["instruction_paths"] == ["/workspace/AGENTS.md"]
        assert cfg["max_instruction_chars"] == 28000
        assert cfg["include_skill_sidecar_content"] is True
        assert cfg["max_skill_sidecar_chars"] == 7000
        assert cfg["max_command_chars"] == 25000
        assert cfg["resolve_prompt_references"] is True
        assert cfg["max_prompt_reference_chars"] == 18000
        assert cfg["max_prompt_directory_entries"] == 300
        assert cfg["inject_background_task_results"] is False
        assert cfg["emit_llm_stream_events"] is True
        assert cfg["track_usage"] is False
        assert cfg["tool_output_truncation_direction"] == "tail"
        assert cfg["archive_truncated_tool_outputs"] is True
        assert cfg["runtime_mode"] == "plan"
        assert cfg["structured_output_schema"] == {"type": "object"}
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
        assert "response_flow" not in payload["config"]["llm"]
        assert payload["config"]["llm"]["tools"] == ["*"]
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


def test_build_apply_payload_for_native_agent_preserves_runtime_profile_tool_selection_and_allowlists():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = (
            '{"llm":{"provider":"openai"},'
            '"enabled_tools":["bash","read"],'
            '"disabled_tools":["webfetch"],'
            '"tool_permissions":{"bash":"ask"},'
            '"allowed_capability_ids":["tool:runtime-only","opencode.skill.existing-skill"],'
            '"allowed_capability_types":["adapter_action","skill","tool"],'
            '"allowed_external_systems":["github"],'
            '"allowed_actions":["runtime.action"],'
            '"allowed_adapter_actions":["runtime.adapter"],'
            '"resolved_action_mappings":{"runtime.action":"runtime.adapter"}}'
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)
        db.add(running)
        db.commit()
        db.refresh(running)

        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        original_config = rp.config_json
        payload = service.build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]
        assert rp.config_json == original_config
        assert cfg["runtime_type"] == "native"
        assert cfg["enabled_tools"] == ["bash", "read"]
        assert cfg["disabled_tools"] == ["webfetch"]
        assert cfg["tool_permissions"] == {"bash": "ask"}
        assert "tool:runtime-only" in cfg["allowed_capability_ids"]
        assert "opencode.skill.existing-skill" in cfg["allowed_capability_ids"]
        assert "adapter_action" in cfg["allowed_capability_types"]
        assert "skill" in cfg["allowed_capability_types"]
        assert "tool" in cfg["allowed_capability_types"]
        assert "github" in cfg["allowed_external_systems"]
        assert "runtime.action" in cfg["allowed_actions"]
        assert "runtime.adapter" in cfg["allowed_adapter_actions"]
        assert cfg["resolved_action_mappings"]["runtime.action"] == "runtime.adapter"
    finally:
        db.close()


def test_build_apply_payload_for_agent_grants_github_review_from_runtime_profile():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = (
            '{"llm":{"provider":"openai"},'
            '"github":{"enabled":true,"api_token":"secret","base_url":"https://api.github.com"}}'
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)
        db.add(running)
        db.commit()
        db.refresh(running)

        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        payload = service.build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        assert "github" in cfg["allowed_external_systems"]
        assert "review_pull_request" in cfg["allowed_actions"]
        assert "adapter:github:review_pull_request" in cfg["allowed_adapter_actions"]
        assert "adapter:github:review_pull_request" in cfg["allowed_capability_ids"]
        assert "adapter_action" in cfg["allowed_capability_types"]
        assert cfg["resolved_action_mappings"]["review_pull_request"] == "adapter:github:review_pull_request"
    finally:
        db.close()


def test_build_apply_payload_for_agent_sends_copilot_api_key_for_opencode():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"github_copilot","api_key":"gho_A"}}'
        running.runtime_type = "opencode"
        db.add_all([rp, running]); db.commit(); db.refresh(running)
        payload = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None)).build_apply_payload_for_agent(db, running, rp)
        assert payload["config"]["llm"]["api_key"] == "gho_A"
        assert payload["config"]["llm"]["provider"] == "github-copilot"
        assert "oauth" not in payload["config"]["llm"]
    finally:
        db.close()


def test_build_apply_payload_for_opencode_agent_filters_tool_selection_and_authorization_limits():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = (
            '{"llm":{"provider":"github_copilot","model":"gpt-5-mini","api_key":"OA"},'
            '"github":{"enabled":true,"api_token":"secret","base_url":"https://api.github.com"},'
            '"enabled_tools":["bash"],'
            '"disabled_tools":["webfetch"],'
            '"tool_permissions":{"bash":"ask"},'
            '"allowed_capability_ids":["tool:runtime-only","opencode.skill.existing-skill"],'
            '"allowed_capability_types":["adapter_action","skill","tool"],'
            '"allowed_external_systems":["github"],'
            '"allowed_actions":["runtime.action"],'
            '"allowed_adapter_actions":["runtime.adapter"],'
            '"resolved_action_mappings":{"runtime.action":"runtime.adapter"},'
            '"allowed_skills":["legacy-skill"],'
            '"denied_skills":["legacy-deny"],'
            '"denied_actions":["legacy-action"],'
            '"denied_capability_types":["legacy-type"],'
            '"skill_set":["legacy-set"],'
            '"policy_context":{"mode":"strict"},'
            '"derived_runtime_rules":{"x":true}}'
        )
        running.runtime_type = "opencode"
        db.add_all([rp, running])
        db.commit()
        db.refresh(rp)
        db.refresh(running)

        payload = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None)).build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        assert cfg["llm"]["provider"] == "github-copilot"
        assert cfg["llm"]["model"] == "github-copilot/gpt-5-mini"
        assert cfg["llm"]["api_key"] == "OA"
        assert cfg["llm"]["tools"] == ["*"]
        assert cfg["runtime_type"] == "opencode"
        assert cfg["github"]["enabled"] is True
        _assert_no_opencode_restriction_keys(cfg)
    finally:
        db.close()


def test_safe_body_preview_redacts_github_oauth_tokens():
    preview = RuntimeProfileSyncService._safe_body_preview(b'{"error":"bad","access":"gho_SECRET","refresh":"ghu_SECRET"}')
    assert "gho_SECRET" not in preview
    assert "ghu_SECRET" not in preview
    assert "[REDACTED]" in preview

def test_build_apply_payload_for_agent_adds_runtime_type_agent_id_and_external_sections():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "opencode"
        rp.config_json = '{"llm":{"provider":"github_copilot","api_key":"OA"},"jira":{"enabled":true,"instances":[{"name":"j"}]},"confluence":{"enabled":true,"instances":[{"name":"c"}]},"github":{"enabled":true,"api_token":"gh"},"proxy":{"enabled":true,"password":"pw"},"git":{"user":{"name":"bot"}},"debug":{"enabled":true,"log_level":"INFO"}}'
        db.add_all([running, rp])
        db.commit(); db.refresh(running); db.refresh(rp)
        payload = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None)).build_apply_payload_for_agent(db, running, rp)
        assert payload["runtime_type"] == "opencode"
        assert payload["agent_id"] == running.id
        assert payload["config"]["runtime_type"] == "opencode"
        for key in ["jira", "confluence", "github", "proxy", "git", "debug"]:
            assert key in payload["config"]
        assert payload["config"]["llm"]["api_key"] == "OA"
        assert "oauth" not in payload["config"]["llm"]
    finally:
        db.close()
