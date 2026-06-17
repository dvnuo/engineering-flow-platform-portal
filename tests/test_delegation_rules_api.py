import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.repositories.delegation_rule_repo import DelegationRuleRepository


def _build_client_with_overrides():
    from app.main import app
    import app.api.delegation_rules as api_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(username="owner", password_hash="pw", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    both_profile = RuntimeProfile(
        owner_user_id=user.id,
        name="both",
        config_json=json.dumps(
            {
                "github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "gh-secret"},
                "jira": {
                    "enabled": True,
                    "instances": [
                        {
                            "name": "jira",
                            "url": "https://jira.local",
                            "username": "bot@example.com",
                            "token": "jira-secret",
                            "enabled": True,
                            "api_version": "2",
                        }
                    ],
                },
            }
        ),
        is_default=True,
    )
    github_profile = RuntimeProfile(
        owner_user_id=user.id,
        name="github",
        config_json=json.dumps({"github": {"enabled": True, "api_token": "gh-secret"}}),
        is_default=False,
    )
    jira_profile = RuntimeProfile(
        owner_user_id=user.id,
        name="jira",
        config_json=json.dumps(
            {
                "jira": {
                    "enabled": True,
                    "instances": [{"url": "https://jira.local", "username": "bot@example.com", "token": "jira-secret"}],
                }
            }
        ),
        is_default=False,
    )
    empty_profile = RuntimeProfile(owner_user_id=user.id, name="empty", config_json="{}", is_default=False)
    db.add_all([both_profile, github_profile, jira_profile, empty_profile])
    db.commit()
    for profile in (both_profile, github_profile, jira_profile, empty_profile):
        db.refresh(profile)

    def add_agent(name: str, runtime_profile_id: str):
        agent = Agent(
            name=name,
            owner_user_id=user.id,
            visibility="private",
            status="running",
            image="img",
            runtime_profile_id=runtime_profile_id,
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp",
            deployment_name=f"d-{name}",
            service_name=f"s-{name}",
            pvc_name=f"p-{name}",
            endpoint_path="/",
            agent_type="workspace",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        return agent

    agents = SimpleNamespace(
        both=add_agent("both", both_profile.id),
        github=add_agent("github", github_profile.id),
        jira=add_agent("jira", jira_profile.id),
        empty=add_agent("empty", empty_profile.id),
    )

    state = {"user": user}

    def _override_user():
        u = state["user"]
        return SimpleNamespace(id=u.id, role=u.role, username=u.username, nickname=u.username)

    def _override_db():
        yield db

    app.dependency_overrides[api_module.get_current_user] = _override_user
    app.dependency_overrides[api_module.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, agents, _cleanup


def _payload(agent_id: str, source: str = "github_pr_review") -> dict:
    return {
        "name": f"Delegation {source}",
        "enabled": True,
        "target_agent_id": agent_id,
        "skill_name": "selected-skill",
        "source": source,
        "interval_seconds": 60,
    }


def test_api_create_accepts_all_sources():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        for source in ["github_pr_review", "github_pr_mention", "jira_assignee", "jira_mention"]:
            resp = client.post("/api/delegation-rules", json=_payload(agents.both.id, source))
            assert resp.status_code == 200
            body = resp.json()
            assert body["source"] == source
            assert body["trigger_type"] == source
            assert body["task_type"] == "agent_async_task"
            assert body["skill_name"] == "selected-skill"
            assert body["interval_seconds"] == 60
    finally:
        cleanup()


def test_api_schedule_preview_for_timer_cron():
    client, _db, _agents, cleanup = _build_client_with_overrides()
    try:
        resp = client.post(
            "/api/delegation-rules/schedule-preview",
            json={"schedule": {"type": "cron", "expression": "30 9 * * 1-5", "timezone": "Asia/Shanghai"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["summary"] == "Every weekday at 09:30 (Asia/Shanghai)"
        assert body["next_run_at"]
        assert body["next_run_local"]
    finally:
        cleanup()


def test_api_create_timer_source_does_not_require_runtime_provider_config():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        payload = _payload(agents.empty.id, "timer")
        payload["task_prompt"] = "Run the scheduled health review."
        payload["schedule"] = {"type": "cron", "expression": "30 9 * * 1-5", "timezone": "Asia/Shanghai"}
        resp = client.post("/api/delegation-rules", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "timer"
        assert body["source_type"] == "timer"
        assert body["schedule"]["type"] == "cron"
        assert body["task_prompt"] == "Run the scheduled health review."
        assert body["source_account_summary"] == "Portal timer"
        assert body["source_config_status"] == "ok"
    finally:
        cleanup()


def test_api_create_timer_source_requires_task_prompt():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        payload = _payload(agents.empty.id, "timer")
        payload["schedule"] = {"type": "cron", "expression": "30 9 * * 1-5", "timezone": "Asia/Shanghai"}
        resp = client.post("/api/delegation-rules", json=payload)
        assert resp.status_code == 400
        assert "task_prompt" in resp.json()["detail"]
    finally:
        cleanup()


def test_api_create_timer_source_rejects_invalid_cron():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        payload = _payload(agents.empty.id, "timer")
        payload["task_prompt"] = "Run the scheduled health review."
        payload["schedule"] = {"type": "cron", "expression": "0 9 * * * *", "timezone": "UTC"}
        resp = client.post("/api/delegation-rules", json=payload)
        assert resp.status_code == 400
        assert "5 fields" in resp.json()["detail"]
    finally:
        cleanup()


def test_api_create_returns_runtime_source_and_condition_summaries():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        payload = _payload(agents.both.id, "github_pr_review")
        payload["source_conditions"] = {
            "repository": "https://github.com/acme/portal",
            "base_branch": "main",
            "labels_include": "backend, review",
            "include_drafts": False,
        }
        resp = client.post("/api/delegation-rules", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_conditions"]["repository"] == "acme/portal"
        assert body["source_conditions"]["labels_include"] == ["backend", "review"]
        assert body["source_conditions"]["include_drafts"] is False
        assert body["source_account_summary"].startswith("GitHub via both")
        assert "repo acme/portal" in body["source_condition_summary"]
        assert "no drafts" in body["source_condition_summary"]
        assert body["source_config_status"] == "ok"
    finally:
        cleanup()


def test_api_source_preview_exposes_jira_runtime_profile_instances():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        resp = client.get(
            "/api/delegation-rules/source-preview",
            params={"target_agent_id": agents.both.id, "source": "jira_assignee"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "jira"
        assert body["status"] == "ok"
        assert body["account_summary"].startswith("Jira jira")
        assert body["options"]["jira_instances"][0]["value"] == "jira"
    finally:
        cleanup()


def test_api_rejects_unknown_source_with_400():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        resp = client.post("/api/delegation-rules", json=_payload(agents.both.id, "unknown_source"))
        assert resp.status_code == 400
        assert "source must be one of" in resp.json()["detail"]
    finally:
        cleanup()


def test_api_create_requires_skill_name():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        payload = _payload(agents.both.id)
        payload.pop("skill_name")
        resp = client.post("/api/delegation-rules", json=payload)
        assert resp.status_code == 422
    finally:
        cleanup()


def test_api_create_github_source_requires_agent_github_runtime_profile():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        ok = client.post("/api/delegation-rules", json=_payload(agents.github.id, "github_pr_review"))
        assert ok.status_code == 200
        missing = client.post("/api/delegation-rules", json=_payload(agents.empty.id, "github_pr_review"))
        assert missing.status_code == 400
        assert "GitHub" in missing.json()["detail"]
    finally:
        cleanup()


def test_api_create_jira_source_requires_agent_jira_runtime_profile():
    client, _db, agents, cleanup = _build_client_with_overrides()
    try:
        ok = client.post("/api/delegation-rules", json=_payload(agents.jira.id, "jira_assignee"))
        assert ok.status_code == 200
        missing = client.post("/api/delegation-rules", json=_payload(agents.empty.id, "jira_assignee"))
        assert missing.status_code == 400
        assert "Jira" in missing.json()["detail"]
    finally:
        cleanup()


def test_delegation_rules_api_crud_update_and_soft_delete(monkeypatch):
    client, db, agents, cleanup = _build_client_with_overrides()
    try:
        async def _fake_run(self, rule_id, triggered_by="api"):
            from app.services.delegation_rule_service import RunOnceResult

            return RunOnceResult(
                rule_id=rule_id,
                status="success",
                found_count=1,
                created_task_count=1,
                skipped_count=0,
                run_id="run-1",
                created_task_ids=["task-1"],
            )

        monkeypatch.setattr("app.services.delegation_rule_service.DelegationRuleService.run_rule_once", _fake_run)

        create_resp = client.post("/api/delegation-rules", json=_payload(agents.both.id, "github_pr_review"))
        assert create_resp.status_code == 200
        created = create_resp.json()

        patch_resp = client.patch(
            f"/api/delegation-rules/{created['id']}",
            json={"skill_name": "other-skill", "interval_seconds": 120, "enabled": False},
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.json()
        assert patched["skill_name"] == "other-skill"
        assert patched["interval_seconds"] == 120
        assert patched["enabled"] is False

        run_resp = client.post(f"/api/delegation-rules/{created['id']}/run-once")
        assert run_resp.status_code == 200

        repo = DelegationRuleRepository(db)
        repo.create_event(
            rule_id=created["id"],
            dedupe_key="source:item:1",
            source_payload_json="{}",
            normalized_payload_json=json.dumps({"source": "github_pr_review", "source_url": "https://example/pr/1"}),
            status="discovered",
        )
        events_resp = client.get(f"/api/delegation-rules/{created['id']}/events")
        assert events_resp.status_code == 200
        assert events_resp.json()[0]["updated_at"] is not None

        delete_resp = client.delete(f"/api/delegation-rules/{created['id']}")
        assert delete_resp.status_code == 200
        list_resp = client.get("/api/delegation-rules")
        assert list_resp.status_code == 200
        assert list_resp.json() == []

        rule = DelegationRuleRepository(db).get(created["id"])
        state = json.loads(rule.state_json)
        assert state["deleted"] is True
    finally:
        cleanup()


def test_delegation_rule_detail_survives_deleted_target_agent():
    client, db, agents, cleanup = _build_client_with_overrides()
    try:
        create_resp = client.post("/api/delegation-rules", json=_payload(agents.both.id, "github_pr_review"))
        assert create_resp.status_code == 200
        created = create_resp.json()

        db.delete(agents.both)
        db.commit()

        detail_resp = client.get(f"/api/delegation-rules/{created['id']}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["target_agent_id"] == agents.both.id
        assert detail["target_agent_missing"] is True
        assert detail["can_manage"] is True
    finally:
        cleanup()


def test_non_owner_can_view_delegation_but_not_manage():
    client, db, agents, cleanup = _build_client_with_overrides()
    try:
        create_resp = client.post("/api/delegation-rules", json=_payload(agents.both.id, "github_pr_review"))
        assert create_resp.status_code == 200
        created = create_resp.json()

        other = User(username="viewer", password_hash="pw", role="viewer", is_active=True)
        db.add(other)
        db.commit()
        db.refresh(other)

        from app.main import app
        import app.api.delegation_rules as api_module

        app.dependency_overrides[api_module.get_current_user] = lambda: SimpleNamespace(
            id=other.id,
            role=other.role,
            username=other.username,
            nickname=other.username,
        )

        list_resp = client.get("/api/delegation-rules")
        assert list_resp.status_code == 200
        listed = {item["id"]: item for item in list_resp.json()}
        assert created["id"] in listed
        assert listed[created["id"]]["can_manage"] is False
        assert listed[created["id"]]["owner_display_name"] == "owner"

        detail_resp = client.get(f"/api/delegation-rules/{created['id']}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["can_manage"] is False

        patch_resp = client.patch(f"/api/delegation-rules/{created['id']}", json={"name": "Nope"})
        assert patch_resp.status_code == 403

        delete_resp = client.delete(f"/api/delegation-rules/{created['id']}")
        assert delete_resp.status_code == 403

        admin_non_owner = User(username="admin-viewer", password_hash="pw", role="admin", is_active=True)
        db.add(admin_non_owner)
        db.commit()
        db.refresh(admin_non_owner)
        app.dependency_overrides[api_module.get_current_user] = lambda: SimpleNamespace(
            id=admin_non_owner.id,
            role=admin_non_owner.role,
            username=admin_non_owner.username,
            nickname=admin_non_owner.username,
        )

        admin_detail_resp = client.get(f"/api/delegation-rules/{created['id']}")
        assert admin_detail_resp.status_code == 200
        assert admin_detail_resp.json()["can_manage"] is False

        admin_patch_resp = client.patch(f"/api/delegation-rules/{created['id']}", json={"name": "Still Nope"})
        assert admin_patch_resp.status_code == 403
    finally:
        cleanup()
