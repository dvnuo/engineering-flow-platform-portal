import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User

def _build_client(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    other = User(username="other", password_hash="test", role="user", is_active=True)
    db.add_all([owner, other]); db.commit(); db.refresh(owner); db.refresh(other)

    rp = RuntimeProfile(owner_user_id=owner.id, name="Default", description="d", config_json=json.dumps({"llm": {"provider": "openai"}}), revision=1, is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    running = Agent(
        name="runner",
        owner_user_id=owner.id,
        runtime_profile_id=rp.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp",
        deployment_name="dep",
        service_name="svc",
        pvc_name="pvc",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(running); db.commit(); db.refresh(running)

    state = {"user": owner}
    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=state["user"].id, role="user", username=state["user"].username, nickname=state["user"].username),
    )

    calls = {"enqueue": 0}

    def _fake_enqueue(*_args, **_kwargs):
        calls["enqueue"] += 1
        return {"queued_agent_count": 1, "skipped_agent_count": 0, "queued_agent_ids": ["agent-id"]}

    monkeypatch.setattr("app.web.runtime_profile_sync_queue_service.enqueue_profile_to_bound_agents", _fake_enqueue)

    def _set_user(u):
        state["user"] = u

    def _cleanup():
        db.close()

    return TestClient(app), db, owner, other, rp, running, _set_user, _cleanup


def test_runtime_profile_panel_owner_only(monkeypatch):
    client, _db, owner, other, rp, running, set_user, cleanup = _build_client(monkeypatch)
    try:
        ok = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert ok.status_code == 200
        assert "Runtime Profile Metadata" in ok.text
        assert 'data-copilot-auth-base="/api/copilot/auth"' in ok.text
        assert 'data-copilot-agent-id=' not in ok.text
        assert 'Copilot auth proxy' not in ok.text
        assert f'data-test-base=\"/app/runtime-profiles/{rp.id}/test\"' in ok.text
        assert "data-current-value=" in ok.text
        assert 'name="llm_temperature"' not in ok.text

        set_user(other)
        deny = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert deny.status_code == 404
    finally:
        cleanup()


def test_runtime_profile_save_updates_and_triggers(monkeypatch):
    client, db, owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "1",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "1",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": "Renamed",
                "description": "new-desc",
                "is_default": "on",
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4",
                "llm_temperature": "0.2",
                "llm_tools_mode": "all",
                "llm_tools_count": "0",
                "proxy_enabled": "",
                "proxy_url": "",
                "proxy_username": "",
                "proxy_password": "",
                "github_enabled": "",
                "github_base_url": "",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "runtimeProfilesChanged"

        db.refresh(rp)
        assert rp.name == "Renamed"
        assert rp.description == "new-desc"
        assert rp.revision == 2
        saved = json.loads(rp.config_json)
        assert saved["llm"]["provider"] == "anthropic"
        assert "temperature" not in saved["llm"]
        assert "tools" not in saved["llm"]
        assert "max_tokens" not in saved["llm"]
        assert "max_retries" not in saved["llm"]
        assert "system-prompt" not in saved["llm"]
        assert "api_key" not in saved["llm"]
        assert "base_url" not in saved.get("github", {})
        assert "password" not in saved.get("proxy", {})
    finally:
        cleanup()


def test_runtime_profile_save_full_form_only_touched_debug_persists_only_debug(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "0",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "1",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "github_copilot",
                "llm_model": "gpt-5-mini",
                "llm_temperature": "0.3",
                "llm_tools_mode": "all",
                "proxy_enabled": "",
                "jira_enabled": "",
                "confluence_enabled": "",
                "github_enabled": "",
                "debug_enabled": "on",
                "debug_log_level": "ERROR",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json) == {
            "llm": {"provider": "openai"},
            "debug": {"enabled": True, "log_level": "ERROR"},
        }
    finally:
        cleanup()


def test_runtime_profile_save_ignores_hidden_nan_temperature_and_cleans_stale_value(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.4}})
        db.add(rp)
        db.commit()
        db.refresh(rp)

        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "openai",
                "llm_model": "gpt-4",
                "llm_temperature": "NaN",
            },
        )
        assert resp.status_code == 200
        assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." not in resp.text
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert "temperature" not in saved["llm"]
        assert "tools" not in saved["llm"]
    finally:
        cleanup()


def test_runtime_profile_name_only_save_canonicalizes_sparse_config(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()
        db.refresh(rp)

        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "0",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": "Metadata Only",
                "description": "still sparse",
                "is_default": "on",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        assert rp.name == "Metadata Only"
        assert rp.description == "still sparse"
        assert json.loads(rp.config_json) == {}
    finally:
        cleanup()


def test_runtime_profile_panel_renders_view_defaults_for_sparse_profiles(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="jira_instance_count" value="0"' in resp.text
        assert 'name="confluence_instance_count" value="0"' in resp.text
        assert 'option value="" selected>Use runtime local default</option>' in resp.text
        assert 'name="llm_tools_mode"' not in resp.text
        assert 'data-current-value="" data-initial-value=""' in resp.text
        assert "PR review requests" not in resp.text
        assert "GitHub Automation" not in resp.text
        assert 'name="github_review_requests_enabled"' not in resp.text
    finally:
        cleanup()


def test_runtime_profile_panel_does_not_render_runtime_internal_controls(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps(
            {
                "disabled" + "_tools": ["write"],
                "tool" + "_permissions": {"write": {"allowed": False}},
                "max_context_tokens": 32000,
                "enable_plan_tool": True,
                "tool_output_truncation_direction": "tail",
                "structured_output_schema": {"type": "object"},
            }
        )
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        for marker in [
            'name="__touch_' + 'runtime_v2"',
            'data-managed-section="' + 'runtime_v2"',
            'name="disabled' + '_tools"',
            'name="tool' + '_permissions"',
            'name="max_context_tokens"',
            'name="enable_plan_tool"',
            'name="tool_output_truncation_direction"',
            'name="structured_output_schema"',
            'name="track_usage"',
        ]:
            assert marker not in resp.text
    finally:
        cleanup()


def test_runtime_profile_save_sparse_llm_tools_none_does_not_inject_provider_or_model(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()
        db.refresh(rp)
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "",
                "llm_model": "",
                "llm_api_key": "",
                "llm_tools_mode": "none",
                "llm_tools_count": "0",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json) == {}
    finally:
        cleanup()


def test_runtime_profile_panel_get_renders_llm_tools_custom_patterns(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"tools": ["bash", "webfetch"]}})
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode"' not in resp.text
        assert "bash" not in resp.text
        assert "webfetch" not in resp.text
        assert 'data-action="add-llm-tool-pattern"' not in resp.text
    finally:
        cleanup()


def test_runtime_profile_panel_hides_llm_tools_controls(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"tools": []}})
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode"' not in resp.text
        assert 'name="llm_tools_count"' not in resp.text
        assert "data-llm-tools-editor" not in resp.text
    finally:
        cleanup()


def test_runtime_profile_panel_hides_response_flow_controls(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"provider": "openai"}})
        db.add(rp)
        db.commit()

        resp = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert resp.status_code == 200
        assert "Response Flow" not in resp.text
        assert "llm_response_flow_" not in resp.text
        assert "plan_policy" not in resp.text
    finally:
        cleanup()


def test_runtime_profile_save_persists_response_flow_nested_dict(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
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
        assert resp.status_code == 200

        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert "response_flow" not in saved["llm"]
        assert "tools" not in saved["llm"]

        clear_resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "openai",
                "llm_response_flow_plan_policy": "",
                "llm_response_flow_staging_policy": "",
                "llm_response_flow_default_skill_execution_style": "",
                "llm_response_flow_ask_user_policy": "",
                "llm_response_flow_active_skill_conflict_policy": "",
                "llm_response_flow_complexity_prompt_budget_ratio": "",
                "llm_response_flow_complexity_min_request_tokens": "",
            },
        )
        assert clear_resp.status_code == 200
        db.refresh(rp)
        cleared = json.loads(rp.config_json)
        assert "response_flow" not in cleared["llm"]
    finally:
        cleanup()


def test_runtime_profile_save_ignores_hidden_llm_advanced_fields(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps(
            {
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "tools": [],
                    "response_flow": {"plan_policy": "always"},
                }
            }
        )
        db.add(rp)
        db.commit()
        db.refresh(rp)

        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "openai",
                "llm_model": "gpt-4",
                "llm_temperature": "nan",
                "llm_tools_mode": "none",
                "llm_tools_count": "1",
                "llm_tools_0_pattern": "webfetch",
                "llm_response_flow_plan_policy": "always",
                "llm_response_flow_complexity_prompt_budget_ratio": "bad",
                "llm_response_flow_complexity_min_request_tokens": "bad",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert "tools" not in saved["llm"]
        assert "temperature" not in saved["llm"]
        assert "response_flow" not in saved["llm"]
    finally:
        cleanup()


def test_runtime_profile_save_persists_temperature_only_for_exact_gpt4(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "openai",
                "llm_model": "gpt-4",
                "llm_temperature": "0.2",
                "llm_tools_mode": "inherit",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["llm"]["model"] == "gpt-4"
        assert "temperature" not in saved["llm"]
    finally:
        cleanup()


def test_runtime_profile_save_clears_temperature_when_model_not_gpt4_even_if_input_disabled(monkeypatch):
    client, db, _owner, _other, rp, _running, _set_user, cleanup = _build_client(monkeypatch)
    try:
        rp.config_json = json.dumps({"llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.4}})
        db.add(rp)
        db.commit()

        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "0",
                "__touch_jira": "0",
                "__touch_confluence": "0",
                "__touch_github": "0",
                "__touch_git": "0",
                "__touch_debug": "0",
                "name": rp.name,
                "description": rp.description or "",
                "llm_provider": "openai",
                "llm_model": "gpt-5.4-mini",
                "llm_tools_mode": "inherit",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["llm"]["model"] == "gpt-5.4-mini"
        assert "temperature" not in saved["llm"]
    finally:
        cleanup()
