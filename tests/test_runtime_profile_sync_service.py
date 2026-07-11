import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService


REMOVED_RESTRICTION_KEYS = {
    "enabled" + "_tools",
    "disabled" + "_tools",
    "tool" + "_permissions",
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


def _assert_no_removed_restriction_keys(config: dict) -> None:
    for key in REMOVED_RESTRICTION_KEYS:
        assert key not in config


def _assert_cli_instruction_texts(instruction_texts):
    assert isinstance(instruction_texts, list)
    assert len(instruction_texts) == 1
    text = instruction_texts[0]
    for expected in ["bash", "jira", "confluence", "gh", "aws", "mobile-auto", "git", "--json", "--dry-run", "--yes", "auth_failed"]:
        assert expected in text


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


def test_build_apply_payload_from_profile_adds_default_llm_timeout():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm": {"provider": "openai"}}'
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert set(payload.keys()) == {"runtime_profile_id", "name", "revision", "config"}
        assert payload["runtime_profile_id"] == rp.id
        assert payload["name"] == rp.name
        assert payload["revision"] == rp.revision
        assert payload["config"] == {
            "llm": {"provider": "openai", "timeout_ms": 300000},
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
        assert set(payload.keys()) == {"runtime_profile_id", "name", "revision", "config"}
        assert payload["config"] == {}
    finally:
        db.close()


def test_build_apply_payload_from_profile_drops_context_budget_when_present():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"openai","context_budget":{"tool_loop":{"max_prompt_tokens":32000}}}}'
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert payload["config"]["llm"] == {"provider": "openai", "timeout_ms": 300000}
    finally:
        db.close()


def test_build_apply_payload_from_profile_drops_old_runtime_internal_config_surface():
    db, rp, _running, _stopped = _build_db()
    try:
        old_fields = {
            "enabled" + "_tools": ["bash", "read"],
            "disabled" + "_tools": ["webfetch"],
            "tool" + "_permissions": {"bash": "ask"},
            "max_iterations": 6,
            "active_skills": ["review"],
            "compaction_auto": True,
            "system_prompt_texts": ["system"],
            "instruction_texts": ["instruction"],
            "runtime_mode": "plan",
            "structured_output_schema": {"type": "object"},
        }
        rp.config_json = json.dumps(
            {
                "llm": {"provider": "github_copilot", "model": "gpt-5-mini"},
                **old_fields,
            }
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        cfg = payload["config"]

        assert cfg["llm"]["provider"] == "github_copilot"
        assert cfg["llm"]["model"] == "gpt-5-mini"
        assert "tools" not in cfg["llm"]
        for key in old_fields:
            assert key not in cfg
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
        assert "tools" not in payload["config"]["llm"]
    finally:
        db.close()


def test_build_apply_payload_for_agent_drops_tool_selection_and_allowlists():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = json.dumps(
            {
                "llm": {"provider": "openai", "tools": ["bash"]},
                "enabled" + "_tools": ["bash", "read"],
                "disabled" + "_tools": ["webfetch"],
                "tool" + "_permissions": {"bash": "ask"},
                "allowed_capability_ids": ["tool:runtime-only"],
                "allowed_capability_types": ["adapter_action", "skill", "tool"],
                "allowed_external_systems": ["github"],
                "allowed_actions": ["runtime.action"],
                "allowed_adapter_actions": ["runtime.adapter"],
                "resolved_action_mappings": {"runtime.action": "runtime.adapter"},
            }
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)
        db.add(running)
        db.commit()
        db.refresh(running)

        service = RuntimeProfileSyncService()
        original_config = rp.config_json
        payload = service.build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]
        assert rp.config_json == original_config
        assert payload["runtime_type"] == "native"
        assert "runtime_type" not in cfg
        assert "tools" not in cfg["llm"]
        _assert_no_removed_restriction_keys(cfg)
    finally:
        db.close()


def test_build_apply_payload_for_agent_does_not_infer_github_authorization_from_credentials():
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

        service = RuntimeProfileSyncService()
        payload = service.build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        assert "allowed_external_systems" not in cfg
        assert "allowed_actions" not in cfg
        assert "allowed_adapter_actions" not in cfg
        assert "allowed_capability_ids" not in cfg
        assert "allowed_capability_types" not in cfg
        assert "resolved_action_mappings" not in cfg
        assert "review_pull_request" not in json.dumps(cfg)
    finally:
        db.close()


def test_build_apply_payload_for_agent_sends_copilot_api_key_for_single_runtime():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"github_copilot","api_key":"gho_A"}}'
        running.runtime_type = "native"
        db.add_all([rp, running]); db.commit(); db.refresh(running)
        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        assert payload["config"]["llm"]["api_key"] == "gho_A"
        assert payload["config"]["llm"]["provider"] == "github_copilot"
        assert "oauth" not in payload["config"]["llm"]
    finally:
        db.close()


def test_build_apply_payload_for_agent_projects_copilot_for_opencode_runtime():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"github_copilot","model":"gpt-5-mini","api_key":"gho_A"}}'
        running.runtime_type = "opencode"
        db.add_all([rp, running]); db.commit(); db.refresh(running)
        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        assert payload["runtime_type"] == "opencode"
        assert payload["config"]["llm"]["provider"] == "github-copilot"
        assert payload["config"]["llm"]["model"] == "github-copilot/gpt-5-mini"
        assert payload["config"]["llm"]["api_key"] == "gho_A"
        assert "oauth" not in payload["config"]["llm"]
        assert "oauth_by_runtime" not in payload["config"]["llm"]
    finally:
        db.close()


def test_build_apply_payload_for_agent_uses_default_llm_timeout_for_native_runtime():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"github_copilot","model":"gpt-5-mini","timeout_ms":10000}}'
        running.runtime_type = "native"
        db.add_all([rp, running])
        db.commit()
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)

        assert payload["runtime_type"] == "native"
        assert payload["config"]["llm"]["provider"] == "github_copilot"
        assert payload["config"]["llm"]["model"] == "gpt-5-mini"
        assert payload["config"]["llm"]["timeout_ms"] == 300000
    finally:
        db.close()


def test_build_apply_payload_for_agent_uses_default_llm_timeout_for_opencode_runtime():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = '{"llm":{"provider":"github_copilot","model":"gpt-5-mini","timeout_ms":10000}}'
        running.runtime_type = "opencode"
        db.add_all([rp, running])
        db.commit()
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)

        assert payload["runtime_type"] == "opencode"
        assert payload["config"]["llm"]["provider"] == "github-copilot"
        assert payload["config"]["llm"]["model"] == "github-copilot/gpt-5-mini"
        assert payload["config"]["llm"]["timeout_ms"] == 300000
    finally:
        db.close()


def test_build_apply_payload_for_agent_strips_opencode_runtime_restrictions():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = json.dumps(
            {
                "llm": {"provider": "openai", "model": "gpt-5-mini"},
                "enabled" + "_tools": ["bash"],
                "disabled" + "_tools": ["webfetch"],
                "tool" + "_permissions": {"bash": "ask"},
                "allowed_capability_ids": ["tool:runtime-only"],
                "allowed_external_systems": ["github"],
                "allowed_actions": ["runtime.action"],
            }
        )
        running.runtime_type = "opencode"
        db.add_all([rp, running])
        db.commit()
        db.refresh(rp)
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)

        assert payload["runtime_type"] == "opencode"
        assert "tools" not in payload["config"]["llm"]
        _assert_no_removed_restriction_keys(payload["config"])
    finally:
        db.close()


def test_build_apply_payload_for_agent_filters_tool_selection_and_authorization_limits():
    db, rp, running, _stopped = _build_db()
    try:
        rp.config_json = json.dumps(
            {
                "llm": {"provider": "github_copilot", "model": "gpt-5-mini", "api_key": "OA", "tools": ["bash"]},
                "github": {"enabled": True, "api_token": "secret", "base_url": "https://api.github.com"},
                "enabled" + "_tools": ["bash"],
                "disabled" + "_tools": ["webfetch"],
                "tool" + "_permissions": {"bash": "ask"},
                "allowed_capability_ids": ["tool:runtime-only"],
                "allowed_capability_types": ["adapter_action", "skill", "tool"],
                "allowed_external_systems": ["github"],
                "allowed_actions": ["runtime.action"],
                "allowed_adapter_actions": ["runtime.adapter"],
                "resolved_action_mappings": {"runtime.action": "runtime.adapter"},
                "allowed_skills": ["legacy-skill"],
                "denied_skills": ["legacy-deny"],
                "denied_actions": ["legacy-action"],
                "denied_capability_types": ["legacy-type"],
                "skill_set": ["legacy-set"],
                "policy_context": {"mode": "strict"},
                "derived_runtime_rules": {"x": True},
            }
        )
        running.runtime_type = "native"
        db.add_all([rp, running])
        db.commit()
        db.refresh(rp)
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        assert cfg["llm"]["provider"] == "github_copilot"
        assert cfg["llm"]["model"] == "gpt-5-mini"
        assert cfg["llm"]["api_key"] == "OA"
        assert "tools" not in cfg["llm"]
        assert "runtime_type" not in cfg
        assert cfg["github"]["enabled"] is True
        _assert_no_removed_restriction_keys(cfg)
    finally:
        db.close()


def test_build_apply_payload_for_agent_includes_external_cli_config_fields_and_secrets():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = json.dumps(
            {
                "jira": {
                    "enabled": True,
                    "instances": [
                        {
                            "name": "Jira",
                            "base_url": "https://jira.example.com/",
                            "email": "jira@example.com",
                            "password": "jira-password",
                            "api_token": "jira-token",
                            "project_key": "ENG",
                            "api_version": "3",
                            "enabled": True,
                            "rest_path": "/rest/api/3",
                        }
                    ],
                },
                "confluence": {
                    "enabled": True,
                    "instances": [
                        {
                            "name": "Confluence",
                            "base_url": "https://confluence.example.com/wiki/",
                            "email": "conf@example.com",
                            "api_token": "conf-token",
                            "space_key": "DOCS",
                            "api_version": "2",
                            "enabled": True,
                            "rest_path": "/rest/api",
                        }
                    ],
                },
                "github": {
                    "enabled": True,
                    "access_token": "github-token",
                    "base_url": "https://github.example.com/api/v3/",
                    "hosts": {"github.example.com": {"oauth_token": "browser-forged"}},
                },
                "aws": {
                    "enabled": True,
                    "profile": "prod",
                    "region": "us-east-1",
                    "output": "json",
                    "username": "adfs-user",
                    "password": "adfs-password",
                },
                "git": {"user": {"name": "EFP Bot", "email": "efp-bot@example.com", "signingkey": "drop"}},
                "tool_loop": {"max_iterations": 12},
                "context_budget": {"max_prompt_tokens": 32000},
                "runtime_mode": "plan",
            }
        )
        db.add_all([rp, running])
        db.commit()
        db.refresh(rp)
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        assert payload["runtime_type"] == "native"
        assert payload["agent_id"] == running.id
        _assert_cli_instruction_texts(cfg.pop("instruction_texts"))
        assert cfg == {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "Jira",
                        "url": "https://jira.example.com",
                        "username": "jira@example.com",
                        "password": "jira-password",
                        "token": "jira-token",
                        "enabled": True,
                        "project": "ENG",
                        "api_version": "3",
                    }
                ],
            },
            "confluence": {
                "enabled": True,
                "instances": [
                    {
                        "name": "Confluence",
                        "url": "https://confluence.example.com/wiki",
                        "username": "conf@example.com",
                        "token": "conf-token",
                        "enabled": True,
                        "space": "DOCS",
                    }
                ],
            },
            "github": {
                "enabled": True,
                "api_token": "github-token",
                "base_url": "https://github.example.com/api/v3",
            },
            "aws": {
                "enabled": True,
                "username": "adfs-user",
                "password": "adfs-password",
            },
            "git": {"user": {"name": "EFP Bot", "email": "efp-bot@example.com"}},
        }
    finally:
        db.close()


def test_build_apply_payload_for_agent_filters_name_only_instances_without_cli_instructions():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = json.dumps(
            {
                "jira": {
                    "enabled": True,
                    "instances": [
                        {"name": "Jira"},
                        {"name": "Empty URI", "uri": "   "},
                        "bad",
                    ],
                },
                "confluence": {
                    "enabled": True,
                    "instances": [
                        {"name": "Confluence"},
                        {"name": "Empty URL", "url": ""},
                    ],
                },
            }
        )
        db.add_all([rp, running])
        db.commit()
        db.refresh(rp)
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        assert "instruction_texts" not in cfg
        assert cfg["jira"]["instances"] == []
        assert cfg["confluence"]["instances"] == []
    finally:
        db.close()


def test_build_apply_payload_for_agent_keeps_endpoint_aliases_and_adds_cli_instructions():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = json.dumps(
            {
                "jira": {
                    "enabled": True,
                    "instances": [
                        {"name": "name-only"},
                        {"name": "Jira", "uri": "https://jira-alias.example.com/", "api_token": "jt"},
                    ],
                },
                "confluence": {
                    "enabled": True,
                    "instances": [
                        {"name": "name-only"},
                        {"name": "Confluence", "baseUrl": "https://conf-alias.example.com/wiki/"},
                    ],
                },
            }
        )
        db.add_all([rp, running])
        db.commit()
        db.refresh(rp)
        db.refresh(running)

        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        cfg = payload["config"]

        _assert_cli_instruction_texts(cfg.pop("instruction_texts"))
        assert cfg["jira"]["instances"] == [
            {"name": "Jira", "url": "https://jira-alias.example.com", "token": "jt"}
        ]
        assert cfg["confluence"]["instances"] == [
            {"name": "Confluence", "url": "https://conf-alias.example.com/wiki"}
        ]
    finally:
        db.close()


def test_build_apply_payload_for_agent_adds_runtime_type_agent_id_and_external_sections():
    db, rp, running, _stopped = _build_db()
    try:
        running.runtime_type = "native"
        rp.config_json = '{"llm":{"provider":"github_copilot","api_key":"OA"},"jira":{"enabled":true,"instances":[{"name":"j"}]},"confluence":{"enabled":true,"instances":[{"name":"c"}]},"github":{"enabled":true,"api_token":"gh"},"aws":{"enabled":true,"region":"us-east-1"},"proxy":{"enabled":true,"password":"pw"},"git":{"user":{"name":"bot"}},"debug":{"enabled":true,"log_level":"INFO"}}'
        db.add_all([running, rp])
        db.commit(); db.refresh(running); db.refresh(rp)
        payload = RuntimeProfileSyncService().build_apply_payload_for_agent(db, running, rp)
        assert payload["runtime_type"] == "native"
        assert payload["agent_id"] == running.id
        assert "runtime_type" not in payload["config"]
        for key in ["jira", "confluence", "github", "aws", "proxy", "git", "debug"]:
            assert key in payload["config"]
        assert payload["config"]["jira"]["instances"] == []
        assert payload["config"]["confluence"]["instances"] == []
        assert payload["config"]["llm"]["api_key"] == "OA"
        _assert_cli_instruction_texts(payload["config"]["instruction_texts"])
        assert "oauth" not in payload["config"]["llm"]
    finally:
        db.close()
