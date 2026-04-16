import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.agent_task import AgentTask
from app.models.runtime_profile import RuntimeProfile
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository


def _build_client(
    monkeypatch,
    *,
    current_user_role="admin",
    current_user_id=None,
    current_user_username=None,
    agent_owner_id=None,
    agent_visibility="private",
):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    admin = User(username="admin", password_hash="test", role="admin", is_active=True)
    viewer = User(username="viewer", password_hash="test", role="user", is_active=True)
    db.add_all([owner, admin, viewer])
    db.commit()
    db.refresh(owner)
    db.refresh(admin)
    db.refresh(viewer)

    user_by_username = {"owner": owner, "admin": admin, "viewer": viewer}
    selected_user = user_by_username.get(current_user_username) if current_user_username else None
    if selected_user is None and current_user_id is not None:
        selected_user = next(
            (candidate for candidate in (owner, admin, viewer) if candidate.id == current_user_id),
            None,
        )
    if selected_user is None:
        selected_user = admin if current_user_role == "admin" else owner

    current_user_id = selected_user.id
    if agent_owner_id is None:
        agent_owner_id = owner.id

    current_user = selected_user

    agent = Agent(
        name="agent-1",
        owner_user_id=agent_owner_id,
        visibility=agent_visibility,
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
        lambda _request: SimpleNamespace(
            id=current_user.id,
            role=current_user.role,
            username=current_user.username,
            nickname=current_user.username,
        ),
    )

    def _cleanup():
        db.close()

    return TestClient(app), db, agent, _cleanup


def _bind_profile(db, agent, name="rp-save", config=None, revision=1):
    rp = RuntimeProfile(
        owner_user_id=agent.owner_user_id,
        name=name,
        config_json=json.dumps(config or {"llm": {"provider": "openai"}}),
        revision=revision,
        is_default=True,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    agent.runtime_profile_id = rp.id
    db.add(agent)
    db.commit()
    return rp


def test_settings_panel_reads_runtime_profile_not_runtime_api(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, name="rp-settings", config={"llm": {"provider": "openai"}}, revision=2)

        async def _should_not_call(**_kwargs):
            raise AssertionError("runtime proxy forward should not be called")

        monkeypatch.setattr("app.web._forward_runtime", _should_not_call)

        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "rp-settings" in resp.text
        assert "Revision: <strong>2</strong>" in resp.text
        assert 'name="original_config_json"' not in resp.text
    finally:
        cleanup()


def test_settings_save_uses_db_profile_as_merge_base_and_sanitizes(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent)

        async def _fake_sync(_db, profile):
            return {"updated_running_count": 1, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        payload = {
            "original_config_json": json.dumps({"ssh": {"hacked": True}}),
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-4",
        }
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=payload)
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert rp.revision == 2
        assert saved["llm"]["provider"] == "anthropic"
        assert saved["llm"]["max_tokens"] == 1000
        assert "ssh" not in saved
    finally:
        cleanup()


def test_settings_save_sync_exception_does_not_break_response(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"llm": {"provider": "openai"}}, revision=1)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("fanout failed")

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _boom)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_provider": "anthropic", "llm_model": "claude"},
        )
        assert resp.status_code == 200
        assert "Runtime profile was saved, but sync fan-out failed" in resp.text
        db.refresh(rp)
        assert rp.revision == 2
        saved = json.loads(rp.config_json)
        assert saved["llm"]["provider"] == "anthropic"
    finally:
        cleanup()


def test_settings_save_preserves_proxy_password_when_field_absent(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"proxy": {"enabled": True, "password": "keep-secret"}}, revision=5)

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"proxy_enabled": "on", "proxy_url": "http://proxy.local", "proxy_username": "u"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["proxy"]["password"] == "keep-secret"
    finally:
        cleanup()


def test_settings_save_does_not_infer_llm_api_base(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"llm": {}}, revision=1)

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_provider": "anthropic", "llm_model": "claude"},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert "api_base" not in saved.get("llm", {})
    finally:
        cleanup()


def test_settings_save_drops_jira_instance_when_name_and_url_blank(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(
            db,
            agent,
            config={
                "jira": {"instances": [{"name": "J", "url": "https://jira", "password": "p", "token": "t"}]},
            },
            revision=1,
        )

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={
                "jira_instance_count": "1",
                "jira_instances_0_name": "",
                "jira_instances_0_url": "",
                "jira_instances_0_username": "",
                "jira_instances_0_password": "",
                "jira_instances_0_token": "",
                "jira_instances_0_project": "",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["jira"]["instances"] == []
    finally:
        cleanup()


def test_settings_save_allowed_for_admin_non_owner(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_role="admin")
    try:
        rp = _bind_profile(db, agent, config={"debug": {"enabled": False}}, revision=2)

        async def _fake_sync(*_args, **_kwargs):
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        resp = client.post(f"/app/agents/{agent.id}/settings/save", data={"debug_enabled": "on"})
        assert resp.status_code == 200
        db.refresh(rp)
        saved = json.loads(rp.config_json)
        assert saved["debug"]["enabled"] is True
    finally:
        cleanup()


def test_settings_panel_without_runtime_profile_shows_message(monkeypatch):
    client, _db, agent, cleanup = _build_client(monkeypatch)
    try:
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "This agent has no runtime profile" in resp.text
        assert "Bind a runtime profile before configuring bindings or subscriptions." in resp.text
    finally:
        cleanup()


def test_render_agent_actions_includes_settings_after_edit_and_before_share():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    start = js_source.find("const actions = [")
    end = js_source.find("if (writable) {", start)
    assert start != -1 and end != -1
    actions_block = js_source[start:end]
    assert 'label: "Edit"' in actions_block
    assert 'label: "Settings"' not in actions_block

    writable_block_end = js_source.find("actions.forEach", end)
    writable_block = js_source[end:writable_block_end]
    assert "if (writable)" in writable_block
    assert "actions.splice(4, 0" in writable_block
    assert 'label: "Settings"' in writable_block
    assert "onClick: () => openSettings()" in writable_block


def test_render_agent_actions_keeps_settings_tied_to_writable_gate():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "if (writable) {" in js_source
    assert "actions.splice(4, 0" in js_source
    assert 'label: "Settings"' in js_source


def test_settings_panel_includes_triggered_work_sections(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, name="rp-triggered", config={"llm": {"provider": "openai"}}, revision=1)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "External Identity Bindings" in resp.text
        assert "External Event Subscriptions" in resp.text
    finally:
        cleanup()


def test_triggered_work_bindings_create_requires_runtime_profile_for_owner(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        response = client.post(
            f"/app/agents/{agent.id}/triggered-work/bindings/create",
            data={"system_type": "github", "external_account_id": "owner-acct", "enabled": "on"},
        )
        assert response.status_code == 200
        assert "Bind a runtime profile before configuring bindings or subscriptions." in response.text
        assert "Binding created" not in response.text
        assert len(AgentIdentityBindingRepository(db).list_by_agent(agent.id)) == 0
    finally:
        cleanup()


def test_triggered_work_subscriptions_create_requires_runtime_profile_for_owner(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        response = client.post(
            f"/app/agents/{agent.id}/triggered-work/subscriptions/create",
            data={"source_type": "github", "event_type": "mention", "mode": "push", "enabled": "on"},
        )
        assert response.status_code == 200
        assert "Bind a runtime profile before configuring bindings or subscriptions." in response.text
        assert "Subscription created" not in response.text
        assert len(ExternalEventSubscriptionRepository(db).list_by_agent(agent.id)) == 0
    finally:
        cleanup()


def test_settings_panel_shows_triggered_work_summary_counts(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-summary", config={"llm": {"provider": "openai"}}, revision=1)
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-enabled",
            username="enabled-user",
            scope_json=None,
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="acct-disabled",
            username="disabled-user",
            scope_json=None,
            enabled=False,
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="mention",
            enabled=True,
            mode="push",
            source_kind="github.mention",
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="push",
            enabled=True,
            mode="push",
            source_kind="github.push",
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="pull_request",
            enabled=False,
            mode="push",
            source_kind="github.pull_request",
        )

        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "Bindings: <strong>1</strong> enabled / <strong>2</strong> total" in resp.text
        assert "Subscriptions: <strong>2</strong> enabled / <strong>3</strong> total" in resp.text
    finally:
        cleanup()


def test_settings_panel_shows_triggered_work_activity_summary(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-activity", config={"llm": {"provider": "openai"}}, revision=1)
        now = datetime.utcnow()
        db.add(
            AgentTask(
                assignee_agent_id=agent.id,
                parent_agent_id=agent.id,
                owner_user_id=agent.owner_user_id,
                source="github",
                task_type="github_review_task",
                status="done",
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
            )
        )
        db.add(
            AgentTask(
                assignee_agent_id=agent.id,
                parent_agent_id=agent.id,
                owner_user_id=agent.owner_user_id,
                source="jira",
                task_type="jira_workflow_review_task",
                status="failed",
                error_message="Transition denied",
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )
        db.commit()

        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        assert "Last triggered task" in resp.text
        assert "Last external event task accepted" in resp.text
        assert "Recent failed trigger" in resp.text
        assert "Transition denied" in resp.text
    finally:
        cleanup()


def test_settings_save_success_keeps_triggered_work_summary_counts(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-save-summary", config={"llm": {"provider": "openai"}}, revision=1)
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="save-binding-1",
            username="save-user-1",
            scope_json=None,
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="jira",
            external_account_id="save-binding-2",
            username="save-user-2",
            scope_json=None,
            enabled=False,
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="push",
            enabled=True,
            mode="push",
            source_kind="github.push",
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="mention",
            enabled=True,
            mode="push",
            source_kind="github.mention",
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="issue_updated",
            enabled=False,
            mode="push",
            source_kind="jira.issue_updated",
        )

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_provider": "anthropic", "llm_model": "claude-sonnet-4"},
        )
        assert resp.status_code == 200
        assert "Bindings: <strong>1</strong> enabled / <strong>2</strong> total" in resp.text
        assert "Subscriptions: <strong>2</strong> enabled / <strong>3</strong> total" in resp.text
    finally:
        cleanup()


def test_settings_save_success_keeps_triggered_work_activity_summary(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-save-activity", config={"llm": {"provider": "openai"}}, revision=1)
        db.add(
            AgentTask(
                assignee_agent_id=agent.id,
                parent_agent_id=agent.id,
                owner_user_id=agent.owner_user_id,
                source="github",
                task_type="github_review_task",
                status="done",
            )
        )
        db.commit()

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_provider": "anthropic", "llm_model": "claude-sonnet-4"},
        )
        assert resp.status_code == 200
        assert "Last triggered task" in resp.text
        assert "Last external event task accepted" in resp.text
    finally:
        cleanup()


def test_settings_save_error_response_keeps_triggered_work_summary_counts(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-save-error-summary", config={"llm": {"provider": "openai"}}, revision=1)
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="err-binding-1",
            username="err-user-1",
            scope_json=None,
            enabled=True,
        )
        AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="jira",
            external_account_id="err-binding-2",
            username="err-user-2",
            scope_json=None,
            enabled=False,
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="push",
            enabled=True,
            mode="push",
            source_kind="github.push",
        )
        ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="jira",
            event_type="issue_updated",
            enabled=False,
            mode="push",
            source_kind="jira.issue_updated",
        )

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_temperature": "not-a-number"},
        )
        assert resp.status_code == 200
        assert "Temperature must be a number." in resp.text
        assert "Bindings: <strong>1</strong> enabled / <strong>2</strong> total" in resp.text
        assert "Subscriptions: <strong>1</strong> enabled / <strong>2</strong> total" in resp.text
    finally:
        cleanup()


def test_settings_save_error_keeps_triggered_work_activity_summary(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-save-error-activity", config={"llm": {"provider": "openai"}}, revision=1)
        db.add(
            AgentTask(
                assignee_agent_id=agent.id,
                parent_agent_id=agent.id,
                owner_user_id=agent.owner_user_id,
                source="jira",
                task_type="jira_workflow_review_task",
                status="failed",
                error_message="Transition denied",
            )
        )
        db.commit()

        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={"llm_temperature": "not-a-number"},
        )
        assert resp.status_code == 200
        assert "Temperature must be a number." in resp.text
        assert "Last triggered task" in resp.text
        assert "Last external event task accepted" in resp.text
        assert "Recent failed trigger" in resp.text
    finally:
        cleanup()


def test_bindings_delete_keeps_profile_missing_guard_after_deleting_legacy_binding(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-legacy-binding", config={"llm": {"provider": "openai"}}, revision=1)
        legacy_binding = AgentIdentityBindingRepository(db).create(
            agent_id=agent.id,
            system_type="github",
            external_account_id="legacy-binding",
            username="legacy-user",
            scope_json=None,
            enabled=True,
        )
        agent.runtime_profile_id = None
        db.add(agent)
        db.commit()

        resp = client.post(f"/app/agents/{agent.id}/triggered-work/bindings/{legacy_binding.id}/delete")
        assert resp.status_code == 200
        assert "Bind a runtime profile before configuring bindings or subscriptions." in resp.text
        assert "/triggered-work/bindings/create" not in resp.text
    finally:
        cleanup()


def test_subscriptions_delete_keeps_profile_missing_guard_after_deleting_legacy_subscription(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch, current_user_username="owner")
    try:
        _bind_profile(db, agent, name="rp-legacy-subscription", config={"llm": {"provider": "openai"}}, revision=1)
        legacy_subscription = ExternalEventSubscriptionRepository(db).create(
            agent_id=agent.id,
            source_type="github",
            event_type="mention",
            enabled=True,
            mode="push",
            source_kind="github.mention",
        )
        agent.runtime_profile_id = None
        db.add(agent)
        db.commit()

        resp = client.post(f"/app/agents/{agent.id}/triggered-work/subscriptions/{legacy_subscription.id}/delete")
        assert resp.status_code == 200
        assert "Bind a runtime profile before configuring bindings or subscriptions." in resp.text
        assert "/triggered-work/subscriptions/create" not in resp.text
    finally:
        cleanup()


def test_shared_non_owner_settings_and_triggered_work_panels_are_read_only(monkeypatch):
    client, db, agent, cleanup = _build_client(
        monkeypatch,
        current_user_username="viewer",
        agent_visibility="public",
    )
    try:
        _bind_profile(db, agent, name="rp-shared-ro", config={"llm": {"provider": "openai"}}, revision=1)

        settings_resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert settings_resp.status_code == 200
        assert "only the owner or an admin can modify them" in settings_resp.text
        assert "id=\"settings-form\"" not in settings_resp.text

        bindings_resp = client.get(f"/app/agents/{agent.id}/triggered-work/bindings/panel")
        assert bindings_resp.status_code == 200
        assert "only the owner or an admin can modify them" in bindings_resp.text
        assert "/triggered-work/bindings/create" not in bindings_resp.text
        assert "/delete" not in bindings_resp.text

        subs_resp = client.get(f"/app/agents/{agent.id}/triggered-work/subscriptions/panel")
        assert subs_resp.status_code == 200
        assert "only the owner or an admin can modify them" in subs_resp.text
        assert "/triggered-work/subscriptions/create" not in subs_resp.text
        assert "/delete" not in subs_resp.text
    finally:
        cleanup()


def test_shared_non_owner_triggered_work_post_create_delete_forbidden(monkeypatch):
    client, db, agent, cleanup = _build_client(
        monkeypatch,
        current_user_username="viewer",
        agent_visibility="public",
    )
    try:
        _bind_profile(db, agent, name="rp-shared-post", config={"llm": {"provider": "openai"}}, revision=1)

        create_binding = client.post(
            f"/app/agents/{agent.id}/triggered-work/bindings/create",
            data={"system_type": "github", "external_account_id": "acct-1", "enabled": "on"},
        )
        assert create_binding.status_code == 403

        create_subscription = client.post(
            f"/app/agents/{agent.id}/triggered-work/subscriptions/create",
            data={"source_type": "github", "event_type": "issue.opened", "mode": "push", "enabled": "on"},
        )
        assert create_subscription.status_code == 403
    finally:
        cleanup()


def test_settings_panel_keeps_triggered_work_containers_outside_outer_form(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, name="rp-layout", config={"llm": {"provider": "openai"}}, revision=1)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        html = resp.text
        form_end = html.find("</form>")
        bindings_idx = html.find("settings-bindings-panel-container")
        subs_idx = html.find("settings-subscriptions-panel-container")
        assert form_end != -1
        assert bindings_idx != -1 and subs_idx != -1
        assert form_end < bindings_idx
        assert form_end < subs_idx
    finally:
        cleanup()


def test_task_detail_panel_shows_bundle_and_dedupe_metadata(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        task = AgentTask(
            assignee_agent_id=agent.id,
            source="github",
            task_type="mention",
            status="queued",
            owner_user_id=agent.owner_user_id,
            task_family="triggered_work",
            provider="github",
            trigger="mention",
            bundle_id="github:issue:octo/portal:7",
            version_key=None,
            dedupe_key="github:mention:octo/portal:7",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        resp = client.get(f"/app/tasks/{task.id}/panel")
        assert resp.status_code == 200
        assert "Bundle ID" in resp.text
        assert "Dedupe Key" in resp.text
        assert "github:issue:octo/portal:7" in resp.text
    finally:
        cleanup()


def test_settings_test_endpoint_does_not_mutate_profile(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, config={"llm": {"provider": "openai", "model": "gpt-4o"}}, revision=7)

        async def _fake_test(_target, _config):
            return False, "expected failure"

        monkeypatch.setattr("app.web.runtime_profile_test_service.run_test", _fake_test)
        before = rp.config_json
        resp = client.post(
            f"/app/agents/{agent.id}/settings/test/llm",
            data={"llm_provider": "openai", "llm_model": "gpt-4.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["target"] == "llm"
        db.refresh(rp)
        assert rp.revision == 7
        assert rp.config_json == before
    finally:
        cleanup()
