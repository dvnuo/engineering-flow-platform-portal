import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile


def _build_env(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    other = User(username="other", password_hash="test", role="user", is_active=True)
    db.add_all([owner, other])
    db.commit()
    db.refresh(owner)
    db.refresh(other)

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

    state = {"user": owner}
    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(
            id=state["user"].id,
            role=state["user"].role,
            username=state["user"].username,
            nickname=state["user"].username,
        ),
    )

    calls = {"apply": 0}

    def _fake_apply(_db, _profile):
        calls["apply"] += 1
        return {
            "bound_agent_count": 1,
            "running_agent_count": 1,
            "restarted_agent_ids": ["agent-id"],
            "pending_agent_ids": [],
            "failed_agent_ids": [],
        }

    monkeypatch.setattr("app.web.runtime_profile_secret_service.apply_profile_save", _fake_apply)

    def _set_user(user):
        state["user"] = user

    def _cleanup():
        db.close()

    return SimpleNamespace(
        client=TestClient(app),
        db=db,
        agent=agent,
        owner=owner,
        other=other,
        calls=calls,
        set_user=_set_user,
        cleanup=_cleanup,
    )


def _build_client(monkeypatch):
    """(client, db, agent, cleanup) for other test modules that reuse this setup."""
    env = _build_env(monkeypatch)
    return env.client, env.db, env.agent, env.cleanup


def _bind_profile(db, agent, config=None):
    """The owner's one settings row, with the agent bound to it."""
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


def _saved(db, rp) -> dict:
    db.refresh(rp)
    return json.loads(rp.config_json)


# --- panel rendering ---------------------------------------------------------


def test_llm_connector_panel_renders_own_routes_and_user_managed_fields(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"llm": {"provider": "openai"}})
        resp = env.client.get("/app/connectors/llm/panel")
        assert resp.status_code == 200
        text = resp.text
        assert 'id="connector-settings-panel-root"' in text
        assert 'data-connector-type="llm"' in text
        assert 'data-copilot-auth-base="/api/copilot/auth"' in text
        assert 'data-copilot-agent-id=' not in text
        assert "Copilot auth proxy" not in text
        assert 'data-test-base="/app/connectors/llm/test"' in text
        assert 'hx-post="/app/connectors/llm/save"' in text
        assert "data-current-value=" in text
        assert 'name="llm_temperature"' not in text
        assert 'name="llm_ai_platform_username"' in text
        assert 'name="llm_ai_platform_password"' in text
        assert 'name="llm_ai_platform_usercase"' in text
        assert 'name="llm_ai_platform_chat_host"' not in text
        assert 'name="llm_ai_platform_ib2b_host"' not in text
        assert 'name="llm_ai_platform_trust_token_header"' not in text
        # Only the connector's own sections carry touch flags.
        assert 'name="__touch_llm"' in text
        for foreign in ("proxy", "jira", "confluence", "github", "git", "debug"):
            assert f'name="__touch_{foreign}"' not in text
    finally:
        env.cleanup()


def test_connector_panel_shows_each_member_their_own_settings_row(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"github": {"enabled": True, "api_token": "owner-secret"}})
        own = env.client.get("/app/connectors/github/panel")
        assert own.status_code == 200
        assert 'value="owner-secret"' in own.text

        env.set_user(env.other)
        foreign = env.client.get("/app/connectors/github/panel")
        assert foreign.status_code == 200
        assert "owner-secret" not in foreign.text
        rows = env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.other.id).all()
        assert len(rows) == 1
    finally:
        env.cleanup()


def test_connector_panel_creates_the_settings_row_on_first_use(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        assert env.db.query(RuntimeProfile).count() == 0
        resp = env.client.get("/app/connectors/jira/panel")
        assert resp.status_code == 200
        assert 'name="jira_instance_count"' in resp.text
        assert "no connection profile" not in resp.text
        rows = env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).all()
        assert len(rows) == 1
        # A second visit reuses the row instead of making another.
        assert env.client.get("/app/connectors/llm/panel").status_code == 200
        assert env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).count() == 1
    finally:
        env.cleanup()


def test_connector_panel_unknown_type_returns_404(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        assert env.client.get("/app/connectors/no-such-connector/panel").status_code == 404
        # The form section name of BrowserStack is not a connector type.
        assert env.client.get("/app/connectors/mobile/panel").status_code == 404
    finally:
        env.cleanup()


def test_settings_panels_remove_retired_external_surfaces_ui(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent)
        for connector_type in ("llm", "jira", "confluence", "github"):
            resp = env.client.get(f"/app/connectors/{connector_type}/panel")
            assert resp.status_code == 200, connector_type
            assert "External Event Subscriptions" not in resp.text, connector_type
            assert "settings-subscriptions-panel-container" not in resp.text, connector_type
            assert "External Identities" not in resp.text, connector_type
    finally:
        env.cleanup()


def test_llm_panel_defaults_to_copilot_without_tools_or_temperature(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        resp = env.client.get("/app/connectors/llm/panel")
        assert resp.status_code == 200
        assert 'name="llm_provider"' in resp.text
        assert 'option value="github_copilot" selected>GitHub Copilot</option>' in resp.text
        assert 'name="llm_tools_mode"' not in resp.text
        assert 'data-current-value="" data-initial-value=""' in resp.text
        assert 'name="llm_temperature"' not in resp.text
    finally:
        env.cleanup()


def test_panels_render_view_defaults_for_sparse_settings(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        llm = env.client.get("/app/connectors/llm/panel")
        assert llm.status_code == 200
        assert '<option value="github_copilot" selected>GitHub Copilot</option>' in llm.text
        assert 'name="llm_reasoning_effort"' in llm.text
        assert '<option value="high" selected>High</option>' in llm.text
        assert 'name="llm_max_context_tokens"' in llm.text
        assert '<option value="256000" selected>256K</option>' in llm.text

        jira = env.client.get("/app/connectors/jira/panel")
        assert 'name="jira_instance_count" value="0"' in jira.text
        confluence = env.client.get("/app/connectors/confluence/panel")
        assert 'name="confluence_instance_count" value="0"' in confluence.text

        github = env.client.get("/app/connectors/github/panel")
        assert "PR review requests" not in github.text
        assert "GitHub Automation" not in github.text
        assert 'name="github_review_requests_enabled"' not in github.text
    finally:
        env.cleanup()


def test_llm_panel_hides_request_timeout_ms(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"llm": {"provider": "openai", "timeout_ms": 300000, "timeout": 60000}})
        resp = env.client.get("/app/connectors/llm/panel")
        assert resp.status_code == 200
        assert 'name="llm_timeout_ms"' not in resp.text
        assert "Request timeout (ms)" not in resp.text
    finally:
        env.cleanup()


def _copilot_card_panels(env):
    """The panels that render partials/copilot_auth_card.html."""
    _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot"}})
    return {
        "llm_connector": env.client.get("/app/connectors/llm/panel"),
        "default_connections": env.client.get("/app/admin/default-connections/panel"),
    }


def test_copilot_cards_show_enterprise_sso_as_step_one_before_authorizing(monkeypatch):
    """Same GITHUB_ENTERPRISE_SSO_URL the login page uses opens every Copilot card."""
    import app.web as web_module

    env = _build_env(monkeypatch)
    try:
        monkeypatch.setattr(web_module.settings, "github_enterprise_sso_url", " https://github.com/enterprises/acme/sso ")
        for name, resp in _copilot_card_panels(env).items():
            assert resp.status_code == 200, name
            html = resp.text
            assert "Step 1: Sign in to GitHub through your enterprise SSO" in html, name
            assert 'href="https://github.com/enterprises/acme/sso"' in html, name
            assert "data-copilot-enterprise-link" in html, name
            # The SSO step sits outside the hidden instructions block, i.e. it is
            # visible before "Authorize GitHub Copilot" mints a device code.
            assert html.index("data-copilot-enterprise-link") < html.index("data-copilot-auth-button") < html.index("data-copilot-instructions"), name
            assert "data-copilot-verify-link" not in html, name
            assert "Step 1: Click the link below to authorize" not in html, name
            assert "Step 2: Copy this code" in html, name
            assert "Step 3: Click to complete authorization" in html, name
            assert "data-copilot-device-link" in html, name
    finally:
        env.cleanup()


def test_copilot_cards_fall_back_to_device_link_without_enterprise_sso(monkeypatch):
    import app.web as web_module

    env = _build_env(monkeypatch)
    try:
        monkeypatch.setattr(web_module.settings, "github_enterprise_sso_url", "")
        for name, resp in _copilot_card_panels(env).items():
            assert resp.status_code == 200, name
            html = resp.text
            assert "Step 1: Click the link below to authorize" in html, name
            assert "data-copilot-verify-link" in html, name
            assert "data-copilot-enterprise-link" not in html, name
            assert "enterprise SSO" not in html, name
    finally:
        env.cleanup()


def test_llm_panel_does_not_render_llm_tools_patterns(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"llm": {"tools": ["bash", "webfetch"]}})
        resp = env.client.get("/app/connectors/llm/panel")
        assert resp.status_code == 200
        assert 'name="llm_tools_mode"' not in resp.text
        assert 'name="llm_tools_count"' not in resp.text
        assert "data-llm-tools-editor" not in resp.text
        assert 'data-action="add-llm-tool-pattern"' not in resp.text
        assert "bash" not in resp.text
        assert "webfetch" not in resp.text
    finally:
        env.cleanup()


def test_connector_panels_do_not_render_runtime_internal_controls(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(
            env.db,
            env.agent,
            {
                "enabled" + "_tools": ["bash", "read"],
                "disabled" + "_tools": ["write"],
                "tool" + "_permissions": {"bash": {"allowed": True}},
                "max_iterations": 8,
                "max_context_tokens": 32000,
                "enable_plan_tool": False,
                "runtime_mode": "plan",
                "tool_output_truncation_direction": "tail",
                "structured_output_schema": {"type": "object"},
            },
        )
        from app.services.connector_registry import SETTINGS_CONNECTORS

        for spec in SETTINGS_CONNECTORS:
            resp = env.client.get(f"/app/connectors/{spec.type}/panel")
            assert resp.status_code == 200, spec.type
            for marker in [
                'name="__touch_' + 'runtime"',
                'data-managed-section="' + 'runtime"',
                'name="__touch_debug"',
                'data-managed-section="debug"',
                'name="enabled' + '_tools"',
                'name="disabled' + '_tools"',
                'name="tool' + '_permissions"',
                'name="max_iterations"',
                'name="max_context_tokens"',
                'name="enable_plan_tool"',
                'name="runtime_mode"',
                'name="tool_output_truncation_direction"',
                'name="structured_output_schema"',
                'name="track_usage"',
            ]:
                assert marker not in resp.text, (spec.type, marker)
    finally:
        env.cleanup()


def test_llm_panel_hides_response_flow_controls(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"llm": {"provider": "openai"}})
        panel = env.client.get("/app/connectors/llm/panel")
        assert panel.status_code == 200
        assert "Response Flow" not in panel.text
        assert "llm_response_flow_" not in panel.text
        assert "plan_policy" not in panel.text
    finally:
        env.cleanup()


def test_jenkins_ui_is_multi_instance_like_jira_and_confluence(monkeypatch):
    """Jenkins gets the same instance-card UI as Jira/Confluence, and the flat
    single-instance inputs are gone."""
    env = _build_env(monkeypatch)
    try:
        _bind_profile(
            env.db,
            env.agent,
            {
                "jenkins": {
                    "enabled": True,
                    "instances": [{"name": "ci", "url": "https://ci.example.com", "username": "ci-bot"}],
                }
            },
        )
        resp = env.client.get("/app/connectors/jenkins/panel")
        assert resp.status_code == 200
        html = resp.text
        assert 'data-instance-container="jenkins"' in html
        assert 'data-instance-count="jenkins"' in html
        assert 'data-action="add-instance" data-group="jenkins"' in html
        assert 'name="jenkins_instance_count"' in html
        assert 'name="jenkins_url"' not in html
        assert 'name="jenkins_username"' not in html
        assert 'name="jenkins_password"' not in html
        assert 'name="jenkins_instances_0_url"' in html or 'data-field="url"' in html
    finally:
        env.cleanup()


def test_panel_lists_running_assistants_that_still_need_a_restart(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})
        rp.revision = 3
        env.agent.profile_revision_applied = 2
        env.db.add_all([rp, env.agent])
        env.db.commit()

        resp = env.client.get("/app/connectors/proxy/panel")
        assert resp.status_code == 200
        assert "data-restart-notice" in resp.text
        assert f'data-restart-agent-id="{env.agent.id}"' in resp.text

        env.agent.profile_revision_applied = 3
        env.db.add(env.agent)
        env.db.commit()
        current = env.client.get("/app/connectors/proxy/panel")
        assert "data-restart-notice" not in current.text
    finally:
        env.cleanup()


# --- view payload ------------------------------------------------------------


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
    assert "debug" not in payload


# --- save --------------------------------------------------------------------


def test_connector_save_response_triggers_connectors_changed(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})
        resp = env.client.post(
            "/app/connectors/proxy/save",
            data={"__touch_proxy": "1", "proxy_enabled": "on", "proxy_url": "http://proxy.example.com:8080"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "connectorsChanged"
        assert "Saved." in resp.text
        assert _saved(env.db, rp)["proxy"]["url"] == "http://proxy.example.com:8080"
        assert rp.revision == 2
        assert env.calls["apply"] == 1
    finally:
        env.cleanup()


def test_connector_save_only_rewrites_its_own_sections(monkeypatch):
    """A connector ignores __touch_ flags for sections other connectors own."""
    env = _build_env(monkeypatch)
    try:
        before = {
            "github": {"enabled": True, "api_token": "keep-me"},
            "git": {"user": {"name": "A", "email": "a@example.com"}},
            "proxy": {"enabled": True, "url": "http://proxy.example.com:8080"},
            "llm": {"provider": "github_copilot", "api_key": "llm-key"},
        }
        rp = _bind_profile(env.db, env.agent, before)
        resp = env.client.post(
            "/app/connectors/jira/save",
            data={
                "__touch_jira": "1",
                "__touch_github": "1",
                "__touch_git": "1",
                "__touch_proxy": "1",
                "__touch_llm": "1",
                "jira_enabled": "on",
                "github_enabled": "",
                "github_api_token": "",
                "git_user_name": "",
                "git_user_email": "",
                "proxy_enabled": "",
                "proxy_url": "",
                "llm_provider": "",
                "llm_api_key": "",
            },
        )
        assert resp.status_code == 200
        cfg = _saved(env.db, rp)
        assert cfg["jira"]["enabled"] is True
        assert cfg["github"] == before["github"]
        assert cfg["git"] == before["git"]
        assert cfg["proxy"] == before["proxy"]
        assert cfg["llm"] == before["llm"]
    finally:
        env.cleanup()


def test_github_connector_save_owns_github_and_git_sections(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"proxy": {"enabled": True, "url": "http://p:8080"}})
        resp = env.client.post(
            "/app/connectors/github/save",
            data={
                "__touch_github": "1",
                "__touch_git": "1",
                "__touch_proxy": "1",
                "github_enabled": "on",
                "github_api_token": "tok",
                "git_user_name": "EFP Bot",
                "git_user_email": "efp-bot@example.com",
                "proxy_enabled": "",
                "proxy_url": "",
            },
        )
        assert resp.status_code == 200
        cfg = _saved(env.db, rp)
        assert cfg["github"] == {"enabled": True, "api_token": "tok"}
        assert cfg["git"] == {"user": {"name": "EFP Bot", "email": "efp-bot@example.com"}}
        assert cfg["proxy"] == {"enabled": True, "url": "http://p:8080"}
    finally:
        env.cleanup()


def test_connector_save_untouched_own_section_is_not_rewritten(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})
        resp = env.client.post(
            "/app/connectors/github/save",
            data={
                "__touch_github": "0",
                "__touch_git": "1",
                "github_enabled": "on",
                "github_api_token": "ignored",
                "git_user_name": "EFP Bot",
                "git_user_email": "efp-bot@example.com",
            },
        )
        assert resp.status_code == 200
        assert _saved(env.db, rp) == {"git": {"user": {"name": "EFP Bot", "email": "efp-bot@example.com"}}}
    finally:
        env.cleanup()


def test_connector_save_unknown_type_returns_404(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        assert env.client.post("/app/connectors/no-such-connector/save", data={}).status_code == 404
        # The local browser connector has no settings form to save.
        assert env.client.post("/app/connectors/local_browser/save", data={}).status_code == 404
    finally:
        env.cleanup()


def test_connector_save_validation_error_keeps_settings_and_shows_message(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot", "reasoning_effort": "high"}})
        resp = env.client.post(
            "/app/connectors/llm/save",
            data={"__touch_llm": "1", "llm_provider": "github_copilot", "llm_reasoning_effort": "bogus"},
        )
        assert resp.status_code == 200
        assert "Thinking level must be a supported value." in resp.text
        assert 'data-settings-status="error"' in resp.text
        assert _saved(env.db, rp) == {"llm": {"provider": "github_copilot", "reasoning_effort": "high"}}
        assert rp.revision == 1
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()


def test_settings_save_ignores_legacy_automation_fields(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent)
        saves = {
            "github": {
                "__touch_github": "1",
                "github_enabled": "on",
                "github_review_requests_enabled": "on",
                "github_review_requests_repos": "org/repo-a\norg/repo-b",
                "github_mentions_enabled": "on",
                "github_mentions_repos": "org/repo-a",
                "github_mentions_include_review_comments": "on",
            },
            "jira": {
                "__touch_jira": "1",
                "jira_enabled": "on",
                "jira_assignments_enabled": "on",
                "jira_assignments_projects": "ENG",
                "jira_mentions_enabled": "on",
                "jira_mentions_projects": "ENG",
            },
            "confluence": {
                "__touch_confluence": "1",
                "confluence_enabled": "on",
                "confluence_mentions_enabled": "on",
                "confluence_mentions_spaces": "DEV",
            },
        }
        for connector_type, payload in saves.items():
            resp = env.client.post(f"/app/connectors/{connector_type}/save", data=payload)
            assert resp.status_code == 200, connector_type
            assert 'name="github_review_requests_repos"' not in resp.text
            assert "Jira Automation" not in resp.text
            assert "Confluence Automation" not in resp.text
            assert "GitHub Automation" not in resp.text
        cfg = _saved(env.db, rp)
        assert cfg["github"]["enabled"] is True
        assert cfg["jira"]["enabled"] is True
        assert cfg["confluence"]["enabled"] is True
        assert "automation" not in cfg["github"]
        assert "automation" not in cfg["jira"]
        assert "automation" not in cfg["confluence"]
    finally:
        env.cleanup()


def test_llm_save_ignores_llm_tools_custom_patterns(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_tools_mode": "custom",
            "llm_tools_count": "4",
            "llm_tools_0_pattern": " bash ",
            "llm_tools_1_pattern": "webfetch",
            "llm_tools_2_pattern": "",
            "llm_tools_3_pattern": "BASH",
        }
        resp = env.client.post("/app/connectors/llm/save", data=payload)
        assert resp.status_code == 200
        cfg = _saved(env.db, rp)
        assert cfg["llm"]["provider"] == "github_copilot"
        assert "tools" not in cfg["llm"]
        assert 'name="llm_tools_count"' not in resp.text
        assert 'data-action="add-llm-tool-pattern"' not in resp.text
    finally:
        env.cleanup()


def test_llm_save_persists_ai_platform_config(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})
        payload = {
            "__touch_llm": "1",
            "llm_provider": "ai_platform",
            "llm_model": "gpt-5.4",
            # Legacy/malicious fixed-field submissions must be ignored.
            "llm_ai_platform_chat_host": "https://chat.int",
            "llm_ai_platform_chat_uri": "/v1/api/v1/chat/completions",
            "llm_ai_platform_ib2b_host": "https://ib2b.int",
            "llm_ai_platform_ib2b_uri": "/dsp/token",
            "llm_ai_platform_username": "u",
            "llm_ai_platform_password": "pw",
            "llm_ai_platform_usercase": "uc",
            "llm_ai_platform_trust_token_header": "X-Trust",
            "llm_ai_platform_tracking_prefix": "EFP",
        }
        resp = env.client.post("/app/connectors/llm/save", data=payload)
        assert resp.status_code == 200
        cfg = _saved(env.db, rp)
        assert cfg["llm"]["provider"] == "ai_platform"
        assert cfg["llm"]["model"] == "gpt-5.4"
        ap = cfg["llm"]["ai_platform"]
        assert ap == {"auth": {"username": "u", "password": "pw", "usercase": "uc"}}
        # The rendered panel exposes only the three user-managed fields.
        assert 'option value="ai_platform" selected' in resp.text
        assert 'name="llm_ai_platform_username"' in resp.text
        assert 'name="llm_ai_platform_password"' in resp.text
        assert 'name="llm_ai_platform_usercase"' in resp.text
        assert 'name="llm_ai_platform_chat_host"' not in resp.text
        assert 'name="llm_ai_platform_ib2b_host"' not in resp.text
        assert 'name="llm_ai_platform_trust_token_header"' not in resp.text
    finally:
        env.cleanup()


def test_connector_save_unchanged_config_skips_restart(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})

        payload = {"__touch_jira": "1", "jira_enabled": "on"}

        # First save changes the config (enables jira): revision bumps and the
        # running bound agent restarts.
        first = env.client.post("/app/connectors/jira/save", data=payload)
        assert first.status_code == 200
        env.db.refresh(rp)
        assert rp.revision == 2
        assert env.calls["apply"] == 1
        assert "Restarting 1 idle assistant to apply it." in first.text

        # Re-saving the byte-identical config is a no-op: no revision bump and no
        # restart of the running agent.
        second = env.client.post("/app/connectors/jira/save", data=payload)
        assert second.status_code == 200
        env.db.refresh(rp)
        assert rp.revision == 2
        assert env.calls["apply"] == 1
        assert "Restarting" not in second.text
        assert "Saved. Nothing changed." in second.text
        assert "runtime profile" not in second.text.lower()
    finally:
        env.cleanup()


def test_llm_save_drops_existing_llm_tools_on_save(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"tools": ["*"]}})
        resp = env.client.post(
            "/app/connectors/llm/save",
            data={"__touch_llm": "1", "llm_tools_mode": "none", "llm_tools_count": "0"},
        )
        assert resp.status_code == 200
        assert _saved(env.db, rp) == {}
    finally:
        env.cleanup()


def test_llm_save_sparse_llm_tools_none_does_not_inject_provider_or_model(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})
        resp = env.client.post(
            "/app/connectors/llm/save",
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
        assert _saved(env.db, rp) == {}
    finally:
        env.cleanup()


def test_llm_save_merges_into_raw_settings_without_injecting_hidden_defaults(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "openai"}})
        payload = {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_model": "gpt-5",
            "llm_tools_mode": "none",
            "llm_tools_count": "0",
        }
        resp = env.client.post("/app/connectors/llm/save", data=payload)
        assert resp.status_code == 200
        cfg = _saved(env.db, rp)
        assert cfg["llm"]["provider"] == "github_copilot"
        assert cfg["llm"]["model"] == "gpt-5.6-terra"
        assert "tools" not in cfg["llm"]
        assert "max_retries" not in cfg["llm"]
        assert "system-prompt" not in cfg["llm"]
        assert "proxy" not in cfg
        assert "jira" not in cfg
        assert "confluence" not in cfg
    finally:
        env.cleanup()


def test_llm_save_clears_llm_request_timeout_overrides(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(
            env.db,
            env.agent,
            {
                "llm": {
                    "provider": "openai",
                    "timeout": 60000,
                    "timeout_ms": 10000,
                    "chunk_timeout_ms": 10000,
                    "chunkTimeout": 10000,
                }
            },
        )
        payload = {
            "__touch_llm": "1",
            "llm_provider": "openai",
            "llm_model": "gpt-5",
        }
        resp = env.client.post("/app/connectors/llm/save", data=payload)
        assert resp.status_code == 200
        cfg = _saved(env.db, rp)
        assert cfg["llm"]["provider"] == "github_copilot"
        assert cfg["llm"]["model"] == "gpt-5.6-terra"
        assert "timeout" not in cfg["llm"]
        assert "timeout_ms" not in cfg["llm"]
        assert "chunk_timeout_ms" not in cfg["llm"]
        assert "chunkTimeout" not in cfg["llm"]
    finally:
        env.cleanup()


def test_llm_save_strips_hidden_fields_and_unsupported_values(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "openai"}})
        resp = env.client.post(
            "/app/connectors/llm/save",
            data={
                "__touch_llm": "1",
                "__touch_proxy": "1",
                "__touch_github": "1",
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
        saved = _saved(env.db, rp)
        assert rp.revision == 2
        assert saved["llm"]["provider"] == "github_copilot"
        assert "temperature" not in saved["llm"]
        assert "tools" not in saved["llm"]
        assert "max_tokens" not in saved["llm"]
        assert "max_retries" not in saved["llm"]
        assert "system-prompt" not in saved["llm"]
        assert "api_key" not in saved["llm"]
        assert "github" not in saved
        assert "proxy" not in saved
    finally:
        env.cleanup()


def test_connector_saves_persist_external_cli_config_sections(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {})
        saves = {
            "jira": {
                "__touch_jira": "1",
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
            },
            "confluence": {
                "__touch_confluence": "1",
                "confluence_enabled": "on",
                "confluence_instance_count": "1",
                "confluence_instances_0_name": "Confluence",
                "confluence_instances_0_url": "https://confluence.example.com/wiki/",
                "confluence_instances_0_username": "conf@example.com",
                "confluence_instances_0_password": "conf-password",
                "confluence_instances_0_token": "conf-token",
                "confluence_instances_0_space": "DOCS",
                "confluence_instances_0_enabled": "1",
            },
            "github": {
                "__touch_github": "1",
                "__touch_git": "1",
                "github_enabled": "on",
                "github_api_token": "github-token",
                "github_base_url": "https://github.example.com/api/v3/",
                "git_user_name": "EFP Bot",
                "git_user_email": "efp-bot@example.com",
            },
            "aws": {
                "__touch_aws": "1",
                "aws_enabled": "on",
                "aws_domain": "HBEU",
                "aws_username": "aws-user",
                "aws_password": "aws-password",
            },
            "jenkins": {
                "__touch_jenkins": "1",
                "jenkins_enabled": "on",
                "jenkins_instance_count": "1",
                "jenkins_instances_0_name": "ci",
                "jenkins_instances_0_url": "https://jenkins.example.com/",
                "jenkins_instances_0_username": "jenkins-user",
                "jenkins_instances_0_password": "jenkins-password",
                "jenkins_instances_0_token": "jenkins-token",
                "jenkins_instances_0_enabled": "1",
            },
        }
        for connector_type, payload in saves.items():
            payload = {
                **payload,
                "tool_loop": '{"max_iterations":12}',
                "context_budget": '{"max_prompt_tokens":32000}',
                "runtime_mode": "plan",
            }
            resp = env.client.post(f"/app/connectors/{connector_type}/save", data=payload)
            assert resp.status_code == 200, connector_type

        assert _saved(env.db, rp) == {
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
            "aws": {
                "enabled": True,
                "domain": "HBEU",
                "username": "aws-user",
                "password": "aws-password",
            },
            "jenkins": {
                "enabled": True,
                "instances": [
                    {
                        "name": "ci",
                        "url": "https://jenkins.example.com",
                        "username": "jenkins-user",
                        "password": "jenkins-password",
                        "token": "jenkins-token",
                        "enabled": True,
                    }
                ],
            },
            "git": {"user": {"name": "EFP Bot", "email": "efp-bot@example.com"}},
        }
    finally:
        env.cleanup()


def test_github_save_blank_api_token_clears_token(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"github": {"api_token": "secret"}})
        resp = env.client.post(
            "/app/connectors/github/save",
            data={"__touch_github": "1", "github_api_token": "", "github_enabled": ""},
        )
        assert resp.status_code == 200
        assert "api_token" not in _saved(env.db, rp).get("github", {})
    finally:
        env.cleanup()


def test_github_save_blank_git_name_and_email_clears_git_user(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"git": {"user": {"name": "A", "email": "a@example.com"}}})
        resp = env.client.post(
            "/app/connectors/github/save",
            data={"__touch_git": "1", "git_user_name": "", "git_user_email": ""},
        )
        assert resp.status_code == 200
        assert "git" not in _saved(env.db, rp)
    finally:
        env.cleanup()


def test_llm_save_ignores_submitted_response_flow_values(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "openai"}})
        save = env.client.post(
            "/app/connectors/llm/save",
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
        assert "Response Flow" not in save.text
        cfg = _saved(env.db, rp)
        assert cfg["llm"]["provider"] == "github_copilot"
        assert "tools" not in cfg["llm"]
        assert "response_flow" not in cfg["llm"]

        cleared = env.client.post(
            "/app/connectors/llm/save",
            data={
                "__touch_llm": "1",
                "llm_provider": "openai",
                "llm_response_flow_plan_policy": "",
                "llm_response_flow_complexity_prompt_budget_ratio": "",
            },
        )
        assert cleared.status_code == 200
        assert "response_flow" not in _saved(env.db, rp)["llm"]
    finally:
        env.cleanup()


def test_llm_save_drops_stale_hidden_advanced_fields(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(
            env.db,
            env.agent,
            {
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "tools": [],
                    "response_flow": {"plan_policy": "always"},
                }
            },
        )
        resp = env.client.post(
            "/app/connectors/llm/save",
            data={
                "__touch_llm": "1",
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
        saved = _saved(env.db, rp)
        assert "tools" not in saved["llm"]
        assert "temperature" not in saved["llm"]
        assert "response_flow" not in saved["llm"]
    finally:
        env.cleanup()


def test_llm_save_never_persists_temperature(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.4}})
        for value in ("0.2", "", "2.5", "-0.1", "NaN"):
            data = {"__touch_llm": "1", "llm_provider": "openai", "llm_model": "gpt-4"}
            if value:
                data["llm_temperature"] = value
            resp = env.client.post("/app/connectors/llm/save", data=data)
            assert resp.status_code == 200, value
            assert "Temperature is only supported for gpt-4 and must be a number between 0 and 2." not in resp.text, value
            assert "temperature" not in _saved(env.db, rp)["llm"], value
    finally:
        env.cleanup()


# --- connection tests --------------------------------------------------------


def test_connector_test_runs_offered_target_against_the_posted_form(monkeypatch):
    import app.web as web_module

    env = _build_env(monkeypatch)
    seen = {}

    async def _fake_run_test(target, config_payload, runtime_type="native"):
        seen["target"] = target
        seen["config"] = config_payload
        seen["runtime_type"] = runtime_type
        return True, "reachable"

    monkeypatch.setattr(web_module.runtime_profile_test_service, "run_test", _fake_run_test)
    try:
        rp = _bind_profile(env.db, env.agent, {"github": {"enabled": True, "api_token": "stored"}})
        resp = env.client.post(
            "/app/connectors/proxy/test/proxy",
            data={
                "__touch_proxy": "1",
                "__touch_github": "1",
                "proxy_enabled": "on",
                "proxy_url": "http://proxy.example.com:8080",
                "github_api_token": "",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "target": "proxy", "message": "reachable"}
        assert seen["target"] == "proxy"
        assert seen["config"]["proxy"]["url"] == "http://proxy.example.com:8080"
        # The github flag is not the proxy connector's to honour.
        assert seen["config"]["github"]["api_token"] == "stored"
        # A test never saves.
        assert _saved(env.db, rp) == {"github": {"enabled": True, "api_token": "stored"}}
    finally:
        env.cleanup()


def test_connector_test_rejects_targets_the_connector_does_not_offer(monkeypatch):
    import app.web as web_module

    env = _build_env(monkeypatch)

    async def _fail_run_test(*_args, **_kwargs):
        raise AssertionError("run_test must not be called for an unoffered target")

    monkeypatch.setattr(web_module.runtime_profile_test_service, "run_test", _fail_run_test)
    try:
        _bind_profile(env.db, env.agent, {})
        # A known test target that belongs to another connector.
        assert env.client.post("/app/connectors/jira/test/github", data={}).status_code == 404
        # AWS offers no connection test at all.
        assert env.client.post("/app/connectors/aws/test/aws", data={}).status_code == 404
        assert env.client.post("/app/connectors/proxy/test/nonsense", data={}).status_code == 404
        assert env.client.post("/app/connectors/no-such-connector/test/proxy", data={}).status_code == 404
    finally:
        env.cleanup()


# --- static template and JS checks ------------------------------------------


def test_jira_api_version_present_in_rendered_and_dynamic_instance_ui():
    from pathlib import Path

    jira_tpl = Path("app/templates/partials/connectors/jira.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert 'data-field="api_version"' in jira_tpl
    assert "REST API v2" in jira_tpl
    assert "REST API v3" in jira_tpl
    assert 'data-field="api_version"' in js
    assert "Auto API Version" in js


def test_confluence_instance_ui_blocks_omit_api_version():
    from pathlib import Path

    confluence_tpl = Path("app/templates/partials/connectors/confluence.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    conf_start = confluence_tpl.index('data-instance-container="confluence"')
    conf_end = confluence_tpl.index('data-action="add-instance" data-group="confluence"')
    conf_block = confluence_tpl[conf_start:conf_end]

    assert 'data-field="api_version"' not in conf_block
    assert "REST API v1" not in conf_block
    confluence_branch = js[js.index('const apiVersionHtml = group === "jira"'):js.index("const urlPlaceholder")]
    assert "REST API v1" not in confluence_branch
    assert 'group === "confluence"' not in confluence_branch


def _with_copilot_card(text: str) -> str:
    """Inline partials/copilot_auth_card.html where a panel includes it.

    The card is shared by the panels, so the static checks below read the
    panel as the server renders it: with the card in place of the include tag.
    """
    from pathlib import Path

    card = Path("app/templates/partials/copilot_auth_card.html").read_text(encoding="utf-8")
    include_tag = '{% include "partials/copilot_auth_card.html" %}'
    assert include_tag in text
    return text.replace(include_tag, card)


def test_templates_and_js_include_single_copilot_auth_button_and_api_key_flow():
    from pathlib import Path

    llm_tpl = _with_copilot_card(Path("app/templates/partials/connectors/llm.html").read_text(encoding="utf-8"))
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert 'name="llm_api_key"' in llm_tpl
    assert 'llm_oauth_native' not in llm_tpl
    assert 'llm_oauth_opencode' not in llm_tpl
    assert 'data-copilot-auth-status' not in llm_tpl
    assert 'data-copilot-status-text' not in llm_tpl
    assert 'data-copilot-auth-button="native"' not in llm_tpl
    assert 'data-copilot-auth-button="opencode"' not in llm_tpl
    assert llm_tpl.count("data-copilot-auth-button") == 1
    assert 'class="space-y-2 hidden" data-copilot-auth-root' in llm_tpl
    assert "Generate a GitHub Copilot token" in llm_tpl
    assert "GitHub Copilot authorization always uses github.com" in llm_tpl
    # The notes are handed to the shared card, which renders them inside the auth root.
    assert "{% for note in copilot_auth_help %}" in llm_tpl
    assert llm_tpl.index("data-copilot-auth-root") < llm_tpl.index("data-copilot-auth-help")
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

    llm_tpl = _with_copilot_card(Path("app/templates/partials/connectors/llm.html").read_text(encoding="utf-8"))
    assert 'data-copilot-result-summary' in llm_tpl
    assert 'Saved OAuth credential present' not in llm_tpl
