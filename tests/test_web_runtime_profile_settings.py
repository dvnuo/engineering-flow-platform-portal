import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile


def _build_client(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    agent = Agent(
        name="agent-1",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep",
        service_name="svc",
        pvc_name="pvc",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname=owner.username),
    )

    async def _fake_sync(*_args, **_kwargs):
        return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

    monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

    def _cleanup():
        db.close()

    return TestClient(app), db, agent, _cleanup


def _bind_profile(db, agent, config=None):
    rp = RuntimeProfile(
        owner_user_id=agent.owner_user_id,
        name="rp",
        config_json=json.dumps(config or {}),
        revision=1,
        is_default=True,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    agent.runtime_profile_id = rp.id
    db.add(agent)
    db.commit()
    return rp


def test_settings_panel_removes_subscriptions_ui(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "External Event Subscriptions" not in resp.text
        assert "settings-subscriptions-panel-container" not in resp.text
        assert "External Identities" in resp.text
    finally:
        cleanup()


def test_settings_panel_get_llm_tools_all_mode_by_default(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {})
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert 'name="llm_provider"' in resp.text
        assert 'option value="" selected>Use runtime local default</option>' in resp.text
        assert 'name="llm_tools_mode" value="inherit" checked' in resp.text
        assert 'data-current-value="" data-initial-value=""' in resp.text
        assert 'name="llm_temperature"' in resp.text
    finally:
        cleanup()


def test_settings_panel_get_llm_tools_custom_mode_renders_patterns(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {"llm": {"tools": ["git_clone", "jira_*"]}})
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode" value="custom" checked' in resp.text
        assert 'name="llm_tools_count"' in resp.text
        assert "git_clone" in resp.text
        assert "jira_*" in resp.text
    finally:
        cleanup()


def test_settings_save_ignores_legacy_automation_fields(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent)
        payload = {
            "__touch_github": "1",
            "__touch_jira": "1",
            "__touch_confluence": "1",
            "github_enabled": "on",
            "github_review_requests_enabled": "on",
            "github_review_requests_repos": "org/repo-a\norg/repo-b",
            "github_mentions_enabled": "on",
            "github_mentions_repos": "org/repo-a",
            "github_mentions_include_review_comments": "on",
            "jira_enabled": "on",
            "jira_assignments_enabled": "on",
            "jira_assignments_projects": "ENG",
            "jira_mentions_enabled": "on",
            "jira_mentions_projects": "ENG",
            "confluence_enabled": "on",
            "confluence_mentions_enabled": "on",
            "confluence_mentions_spaces": "DEV",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["github"]["enabled"] is True
        assert cfg["jira"]["enabled"] is True
        assert cfg["confluence"]["enabled"] is True
        assert "automation" not in cfg["github"]
        assert "automation" not in cfg["jira"]
        assert "automation" not in cfg["confluence"]
        assert 'name="github_review_requests_repos"' not in resp.text
        assert "Jira Automation" not in resp.text
        assert "Confluence Automation" not in resp.text
        assert "GitHub Automation" not in resp.text
    finally:
        cleanup()


def test_settings_save_persists_llm_tools_custom_patterns(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "4",
            "llm_tools_0_pattern": " git_clone ",
            "llm_tools_1_pattern": "jira_*",
            "llm_tools_2_pattern": "",
            "llm_tools_3_pattern": "GIT_CLONE",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["tools"] == ["git_clone", "jira_*"]
        assert 'name="llm_tools_count"' in resp.text
        assert 'data-action="add-llm-tool-pattern"' in resp.text
    finally:
        cleanup()


def test_settings_save_persists_llm_tools_none_mode(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"tools": ["*"]}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_tools_mode": "none", "llm_tools_count": "0"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["tools"] == []
    finally:
        cleanup()


def test_settings_save_sparse_llm_tools_none_does_not_inject_provider_or_model(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={
                "__touch_llm": "1",
                "llm_provider": "",
                "llm_model": "",
                "llm_api_key": "",
                "llm_tools_mode": "none",
                "llm_tools_count": "0",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg == {"llm": {"tools": []}}
    finally:
        cleanup()


def test_settings_save_merges_into_raw_profile_without_injecting_hidden_defaults(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_model": "gpt-5",
            "llm_tools_mode": "none",
            "llm_tools_count": "0",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["provider"] == "openai"
        assert cfg["llm"]["model"] == "gpt-5"
        assert cfg["llm"]["tools"] == []
        assert "max_retries" not in cfg["llm"]
        assert "system-prompt" not in cfg["llm"]
        assert "proxy" not in cfg
        assert "jira" not in cfg
        assert "confluence" not in cfg
    finally:
        cleanup()


def test_settings_save_full_form_only_touched_debug_persists_debug_only(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        payload = {
            "__touch_llm": "0",
            "__touch_proxy": "0",
            "__touch_jira": "0",
            "__touch_confluence": "0",
            "__touch_github": "0",
            "__touch_git": "0",
            "__touch_debug": "1",
            "llm_provider": "github_copilot",
            "llm_model": "gpt-5-mini",
            "llm_temperature": "0.2",
            "llm_tools_mode": "all",
            "proxy_enabled": "",
            "proxy_url": "",
            "jira_enabled": "",
            "confluence_enabled": "",
            "github_enabled": "",
            "git_user_name": "",
            "debug_enabled": "on",
            "debug_log_level": "DEBUG",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg == {"debug": {"enabled": True, "log_level": "DEBUG"}}
    finally:
        cleanup()


def test_settings_save_touched_github_blank_api_token_clears_token(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"github": {"api_token": "secret"}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_github": "1", "github_api_token": "", "github_enabled": ""},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert "api_token" not in cfg.get("github", {})
    finally:
        cleanup()


def test_settings_save_touched_git_blank_name_and_email_clears_git_user(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"git": {"user": {"name": "A", "email": "a@example.com"}}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_git": "1", "git_user_name": "", "git_user_email": ""},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert "git" not in cfg
    finally:
        cleanup()


def test_bindings_can_be_configured_without_runtime_profile(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/agents/{agent.id}/external-identities/create",
            data={"system_type": "github", "external_account_id": "acct-1", "enabled": "on"},
        )
        assert resp.status_code == 200
        assert "External identity added" in resp.text
    finally:
        cleanup()


def test_settings_panel_runtime_profile_missing_message_mentions_external_identities(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "This agent has no runtime profile." in resp.text
        assert "External identities can still be configured below." in resp.text
        assert "External Identities" in resp.text
    finally:
        cleanup()


def test_bindings_panel_available_without_runtime_profile(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/external-identities/panel")
        assert resp.status_code == 200
        assert "Add External Identity" in resp.text
        assert "Assign one from Edit Assistant first." not in resp.text
    finally:
        cleanup()


def test_settings_panel_response_flow_controls_render_and_persist(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        panel = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert panel.status_code == 200
        assert "Response Flow" in panel.text
        assert 'name="llm_response_flow_plan_policy"' in panel.text
        assert 'name="llm_response_flow_staging_policy"' in panel.text
        assert 'name="llm_response_flow_default_skill_execution_style"' in panel.text
        assert 'name="llm_response_flow_ask_user_policy"' in panel.text
        assert 'name="llm_response_flow_active_skill_conflict_policy"' in panel.text
        assert 'name="llm_response_flow_complexity_prompt_budget_ratio"' in panel.text
        assert 'name="llm_response_flow_complexity_min_request_tokens"' in panel.text
        assert "Use runtime local default" in panel.text
        assert "Default skill execution style" in panel.text
        assert "ASK_USER policy" in panel.text
        assert "Active skill conflict policy" in panel.text
        assert "Ordinary requests should complete directly" in panel.text
        assert "explicit request or truly complex work" in panel.text
        assert "near/over runtime budget" in panel.text
        assert "not plan-first or staged-first" in panel.text
        assert "Plan policy controls only up-front planning" in panel.text
        assert "phase-by-phase/manifest-first continuation" in panel.text
        assert "independent from ask_user_policy" in panel.text
        assert "skill frontmatter" in panel.text
        assert "active_skill_conflict_policy are global defaults" in panel.text
        assert "does not declare the corresponding field" in panel.text
        assert "direct active skills" in panel.text
        assert "auto_switch_direct switches on clear new requests" in panel.text
        assert "always_ask keeps the current direct skill" in panel.text
        assert "stepwise/required-plan/required-staging active skills" in panel.text
        assert "replying to a blocking skill question" in panel.text
        assert "independent new requests should leave the old active skill" in panel.text
        assert "prior staged generation flow is not session-sticky" in panel.text
        assert "explicit continue/next signals should resume it" in panel.text
        assert "restart staged mode only if the new request explicitly asks for staged output or is truly complex" in panel.text
        assert "not persisted" in panel.text

        save = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={
                "__touch_llm": "1",
                "llm_provider": "openai",
                "llm_response_flow_plan_policy": "explicit_or_complex",
                "llm_response_flow_staging_policy": "always",
                "llm_response_flow_default_skill_execution_style": "direct",
                "llm_response_flow_ask_user_policy": "blocked_only",
                "llm_response_flow_active_skill_conflict_policy": "always_ask",
                "llm_response_flow_complexity_prompt_budget_ratio": "0.85",
                "llm_response_flow_complexity_min_request_tokens": "24000",
            },
        )
        assert save.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["provider"] == "openai"
        assert cfg["llm"]["response_flow"] == {
            "plan_policy": "explicit_or_complex",
            "staging_policy": "always",
            "default_skill_execution_style": "direct",
            "ask_user_policy": "blocked_only",
            "active_skill_conflict_policy": "always_ask",
            "complexity_prompt_budget_ratio": 0.85,
            "complexity_min_request_tokens": 24000,
        }
    finally:
        cleanup()


def test_settings_save_persists_llm_temperature(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "0.2"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["temperature"] == 0.2
    finally:
        cleanup()


def test_settings_save_blank_temperature_removes_override(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.4}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-5.4-mini"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert "temperature" not in cfg["llm"]
    finally:
        cleanup()


def test_settings_save_rejects_invalid_temperature(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.4}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "2.5"},
        )
        assert resp.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." in resp.text
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["temperature"] == 0.4

        resp_negative = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "-0.1"},
        )
        assert resp_negative.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." in resp_negative.text
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["temperature"] == 0.4

        resp_nan = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "NaN"},
        )
        assert resp_nan.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." in resp_nan.text
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["temperature"] == 0.4
    finally:
        cleanup()
