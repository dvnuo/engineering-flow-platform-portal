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

    calls = {"enqueue": 0}

    def _fake_enqueue(*_args, **_kwargs):
        calls["enqueue"] += 1
        return {"queued_agent_count": 1, "skipped_agent_count": 0, "queued_agent_ids": ["agent-id"]}

    monkeypatch.setattr("app.web.runtime_profile_sync_queue_service.enqueue_profile_to_bound_agents", _fake_enqueue)

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


def test_settings_panel_removes_retired_external_surfaces_ui(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "External Event Subscriptions" not in resp.text
        assert "settings-subscriptions-panel-container" not in resp.text
        assert "External Identities" not in resp.text
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
        assert 'name="llm_tools_mode"' not in resp.text
        assert 'data-current-value="" data-initial-value=""' in resp.text
        assert 'name="llm_temperature"' not in resp.text
    finally:
        cleanup()


def test_settings_panel_renders_llm_request_timeout_ms(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {"llm": {"provider": "openai", "timeout_ms": 300000}})
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert 'name="llm_timeout_ms"' in resp.text
        assert 'Request timeout (ms)' in resp.text
        assert 'value="300000"' in resp.text
    finally:
        cleanup()


def test_runtime_profile_panel_renders_legacy_llm_timeout_as_request_timeout_ms(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai", "timeout": 60000}})
        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="llm_timeout_ms"' in resp.text
        assert 'Request timeout (ms)' in resp.text
        assert 'value="60000"' in resp.text
    finally:
        cleanup()


def test_settings_panel_get_llm_tools_custom_mode_renders_patterns(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {"llm": {"tools": ["bash", "webfetch"]}})
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode"' not in resp.text
        assert 'name="llm_tools_count"' not in resp.text
        assert "bash" not in resp.text
        assert "webfetch" not in resp.text
    finally:
        cleanup()


def test_settings_panel_does_not_render_runtime_internal_controls(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(
            db,
            agent,
            {
                "enabled" + "_tools": ["bash", "read"],
                "tool" + "_permissions": {"bash": {"allowed": True}},
                "max_iterations": 8,
                "enable_plan_tool": False,
                "runtime_mode": "plan",
                "structured_output_schema": {"type": "object"},
            },
        )
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        for marker in [
            'name="__touch_' + 'runtime"',
            'data-managed-section="' + 'runtime"',
            'name="enabled' + '_tools"',
            'name="tool' + '_permissions"',
            'name="max_iterations"',
            'name="enable_plan_tool"',
            'name="runtime_mode"',
            'name="structured_output_schema"',
            'name="track_usage"',
        ]:
            assert marker not in resp.text
    finally:
        cleanup()


def test_settings_view_payload_normalizes_copilot_provider_alias():
    from app.web import _settings_view_payload

    payload = _settings_view_payload(
        {"llm": {"provider": "github-copilot", "model": "gpt-5.4-mini"}},
        {"llm": {"provider": "github-copilot", "model": "gpt-5.4-mini"}},
    )
    assert payload["raw_llm"]["provider"] == "github_copilot"
    assert payload["llm"]["provider"] == "github_copilot"


def test_settings_view_payload_excludes_runtime_internal_view_model():
    from app.web import _settings_view_payload

    payload = _settings_view_payload(
        {
            "enabled" + "_tools": ["bash", "read"],
            "tool" + "_permissions": {"bash": {"allowed": True}},
            "compaction_auto": True,
            "enable_plan_tool": False,
            "runtime_mode": "plan",
            "max_iterations": 6,
        },
        {},
    )

    assert "runtime" not in payload


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


def test_settings_save_ignores_llm_tools_custom_patterns(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "4",
            "llm_tools_0_pattern": " bash ",
            "llm_tools_1_pattern": "webfetch",
            "llm_tools_2_pattern": "",
            "llm_tools_3_pattern": "BASH",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["provider"] == "openai"
        assert "tools" not in cfg["llm"]
        assert 'name="llm_tools_count"' not in resp.text
        assert 'data-action="add-llm-tool-pattern"' not in resp.text
    finally:
        cleanup()


def test_settings_save_drops_existing_llm_tools_on_save(monkeypatch):
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
        assert cfg == {}
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
        assert cfg == {}
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
        assert "tools" not in cfg["llm"]
        assert "max_retries" not in cfg["llm"]
        assert "system-prompt" not in cfg["llm"]
        assert "proxy" not in cfg
        assert "jira" not in cfg
        assert "confluence" not in cfg
    finally:
        cleanup()


def test_settings_save_persists_llm_request_timeout_ms(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai", "timeout": 60000}})
        payload = {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_model": "gpt-5",
            "llm_timeout_ms": "300000",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert cfg["llm"]["provider"] == "openai"
        assert cfg["llm"]["model"] == "gpt-5"
        assert cfg["llm"]["timeout_ms"] == 300000
        assert "timeout" not in cfg["llm"]
    finally:
        cleanup()


def test_jira_api_version_present_in_rendered_and_dynamic_instance_ui():
    from pathlib import Path

    runtime_tpl = Path("app/templates/partials/runtime_profile_panel.html").read_text(encoding="utf-8")
    settings_tpl = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert 'data-field="api_version"' in runtime_tpl
    assert 'data-field="api_version"' in settings_tpl
    assert "REST API v2" in runtime_tpl
    assert "REST API v3" in runtime_tpl
    assert "REST API v2" in settings_tpl
    assert "REST API v3" in settings_tpl
    assert 'data-field="api_version"' in js
    assert "Auto API Version" in js


def test_confluence_instance_ui_blocks_omit_api_version():
    from pathlib import Path

    runtime_tpl = Path("app/templates/partials/runtime_profile_panel.html").read_text(encoding="utf-8")
    settings_tpl = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    runtime_conf_start = runtime_tpl.index('data-instance-container="confluence"')
    runtime_conf_end = runtime_tpl.index('data-action="add-instance" data-group="confluence"')
    runtime_conf_block = runtime_tpl[runtime_conf_start:runtime_conf_end]

    settings_conf_start = settings_tpl.index('data-instance-container="confluence"')
    settings_conf_end = settings_tpl.index('data-action="add-instance" data-group="confluence"')
    settings_conf_block = settings_tpl[settings_conf_start:settings_conf_end]

    assert 'data-field="api_version"' not in runtime_conf_block
    assert 'data-field="api_version"' not in settings_conf_block
    assert "REST API v1" not in runtime_conf_block
    assert "REST API v1" not in settings_conf_block
    confluence_branch = js[js.index('const projectHtml = group === "jira"'):js.index("const usernamePasswordHtml")]
    assert "REST API v1" not in confluence_branch
    assert 'group === "confluence"' not in confluence_branch


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


def test_settings_save_persists_external_cli_config_sections(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        payload = {
            "__touch_llm": "0",
            "__touch_proxy": "0",
            "__touch_jira": "1",
            "__touch_confluence": "1",
            "__touch_github": "1",
            "__touch_git": "1",
            "__touch_debug": "0",
            "jira_enabled": "on",
            "jira_instance_count": "1",
            "jira_instances_0_name": "Jira",
            "jira_instances_0_url": "https://jira.example.com/",
            "jira_instances_0_username": "jira@example.com",
            "jira_instances_0_password": "jira-password",
            "jira_instances_0_token": "jira-token",
            "jira_instances_0_project": "ENG",
            "jira_instances_0_api_version": "3",
            "jira_instances_0_enabled": "1",
            "confluence_enabled": "on",
            "confluence_instance_count": "1",
            "confluence_instances_0_name": "Confluence",
            "confluence_instances_0_url": "https://confluence.example.com/wiki/",
            "confluence_instances_0_username": "conf@example.com",
            "confluence_instances_0_password": "conf-password",
            "confluence_instances_0_token": "conf-token",
            "confluence_instances_0_space": "DOCS",
            "confluence_instances_0_enabled": "1",
            "github_enabled": "on",
            "github_api_token": "github-token",
            "github_base_url": "https://github.example.com/api/v3/",
            "git_user_name": "EFP Bot",
            "git_user_email": "efp-bot@example.com",
            "tool_loop": '{"max_iterations":12}',
            "context_budget": '{"max_prompt_tokens":32000}',
            "runtime_mode": "plan",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)

        assert resp.status_code == 200
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
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
                        "password": "conf-password",
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
            "git": {"user": {"name": "EFP Bot", "email": "efp-bot@example.com"}},
        }
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


def test_settings_panel_runtime_profile_missing_message(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "This agent has no runtime profile." in resp.text
        assert "Assign one from Edit Assistant first." not in resp.text
    finally:
        cleanup()


def test_settings_panel_hides_response_flow_controls_and_ignores_submitted_values(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai"}})
        panel = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert panel.status_code == 200
        assert "Response Flow" not in panel.text
        assert "llm_response_flow_" not in panel.text

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
        assert "tools" not in cfg["llm"]
        assert "response_flow" not in cfg["llm"]
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
        assert "temperature" not in cfg["llm"]
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


def test_settings_save_ignores_invalid_temperature(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.4}})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "2.5"},
        )
        assert resp.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." not in resp.text
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert "temperature" not in cfg["llm"]

        resp_negative = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "-0.1"},
        )
        assert resp_negative.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." not in resp_negative.text
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert "temperature" not in cfg["llm"]

        resp_nan = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4", "llm_temperature": "NaN"},
        )
        assert resp_nan.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." not in resp_nan.text
        db.refresh(rp)
        cfg = json.loads(rp.config_json)
        assert "temperature" not in cfg["llm"]
    finally:
        cleanup()




def _copilot_root_block(text: str) -> str:
    start = text.index('data-copilot-auth-root')
    end = text.index('<div class="portal-settings-section-title"', start)
    return text[start:end]

def test_templates_and_js_include_single_copilot_auth_button_and_api_key_flow():
    from pathlib import Path
    runtime_tpl = Path("app/templates/partials/runtime_profile_panel.html").read_text()
    settings_tpl = Path("app/templates/partials/settings_panel.html").read_text()
    js = Path("app/static/js/chat_ui.js").read_text()
    assert 'name="llm_api_key"' in runtime_tpl
    assert 'name="llm_api_key"' in settings_tpl
    assert 'llm_oauth_native' not in runtime_tpl
    assert 'llm_oauth_opencode' not in runtime_tpl
    assert 'llm_oauth_native' not in settings_tpl
    assert 'llm_oauth_opencode' not in settings_tpl
    assert 'data-copilot-auth-status' not in runtime_tpl
    assert 'data-copilot-status-text' not in runtime_tpl
    assert 'data-copilot-auth-status' not in settings_tpl
    assert 'data-copilot-status-text' not in settings_tpl
    assert 'data-copilot-auth-button="native"' not in runtime_tpl
    assert 'data-copilot-auth-button="opencode"' not in runtime_tpl
    assert 'data-copilot-auth-button="native"' not in settings_tpl
    assert 'data-copilot-auth-button="opencode"' not in settings_tpl
    assert runtime_tpl.count("data-copilot-auth-button") == 1
    assert settings_tpl.count("data-copilot-auth-button") == 1
    assert 'class="space-y-2 hidden" data-copilot-auth-root' in runtime_tpl
    assert 'class="space-y-2 hidden" data-copilot-auth-root' in settings_tpl
    assert "Generate a GitHub Copilot token" in runtime_tpl
    assert "GitHub Copilot authorization always uses github.com" in runtime_tpl
    assert "Generate a GitHub Copilot token" in settings_tpl
    assert "GitHub Copilot authorization always uses github.com" in settings_tpl
    assert "Generate a GitHub Copilot token" in _copilot_root_block(runtime_tpl)
    assert "GitHub Copilot authorization always uses github.com" in _copilot_root_block(runtime_tpl)
    assert "Generate a GitHub Copilot token" in _copilot_root_block(settings_tpl)
    assert "GitHub Copilot authorization always uses github.com" in _copilot_root_block(settings_tpl)
    assert 'setCopilotApiKeyField' in js
    assert 'querySelectorAll("[data-copilot-auth-button]")' in js
    assert 'button.classList.toggle("hidden", !isCopilot)' in js
    assert "JSON.stringify({})" in js
    start_block = js[js.index("async function startCopilotAuth"):js.index("function initializeManagedSettingsRoot")]
    assert "runtime_type" not in start_block
    assert 'Authorization completed, but no token was returned' in js
    assert 'const updated = setCopilotApiKeyField(root, token)' in js
    assert 'setCopilotOAuthFields' not in js
    assert 'clearCopilotOAuthFields' not in js
    assert "Clear saved password" not in js
    assert "Clear saved token" not in js
    assert 'data-clear-field="password"' not in js
    assert 'data-clear-field="token"' not in js


def test_templates_include_copilot_result_summary_notes():
    from pathlib import Path
    runtime_tpl = Path("app/templates/partials/runtime_profile_panel.html").read_text(encoding="utf-8")
    settings_tpl = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")
    assert 'data-copilot-result-summary' in runtime_tpl
    assert 'data-copilot-result-summary' in settings_tpl
    assert 'Saved OAuth credential present' not in runtime_tpl
    assert 'Saved OAuth credential present' not in settings_tpl
