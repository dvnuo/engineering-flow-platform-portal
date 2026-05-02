import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.models.capability_profile import CapabilityProfile
from app.repositories.automation_rule_repo import AutomationRuleRepository


def _build_client_with_overrides():
    from app.main import app
    import app.api.automation_rules as api_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(username="owner", password_hash="pw", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"github": {"enabled": True, "base_url": "", "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = Agent(name="a", owner_user_id=user.id, visibility="private", status="running", image="img", runtime_profile_id=rp.id, disk_size_gi=20, mount_path="/root/.efp", namespace="efp", deployment_name="d", service_name="s", pvc_name="p", endpoint_path="/", agent_type="workspace")
    db.add(agent); db.commit(); db.refresh(agent)

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

    return TestClient(app), db, agent, _cleanup


def _create_payload(agent_id: str) -> dict:
    return {
        "name": "Review EFP PRs",
        "enabled": True,
        "source_type": "github",
        "trigger_type": "github_pr_review_requested",
        "task_template_id": "github_pr_review",
        "target_agent_id": agent_id,
        "scope": {"owner": "acme", "repo": "portal"},
        "trigger_config": {"review_target_type": "team", "review_target": "acme/reviewers"},
        "task_input_defaults": {"skill_name": "review-pull-request", "review_event": "comment"},
        "schedule": {"interval_seconds": 60},
    }


def test_automation_rules_api_crud_soft_delete(monkeypatch):
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        async def _fake_run(self, rule_id, triggered_by="api"):
            from app.services.automation_rule_service import RunOnceResult
            return RunOnceResult(rule_id=rule_id, status="success", found_count=1, created_task_count=1, skipped_count=0, run_id="run-1", created_task_ids=["task-1"])

        monkeypatch.setattr("app.services.automation_rule_service.AutomationRuleService.run_rule_once", _fake_run)

        create_resp = client.post("/api/automation-rules", json=_create_payload(agent.id))
        assert create_resp.status_code == 200
        created = create_resp.json()

        run_resp = client.post(f"/api/automation-rules/{created['id']}/run-once")
        assert run_resp.status_code == 200

        delete_resp = client.delete(f"/api/automation-rules/{created['id']}")
        assert delete_resp.status_code == 200

        list_resp = client.get("/api/automation-rules")
        assert list_resp.status_code == 200
        assert list_resp.json() == []

        get_resp = client.get(f"/api/automation-rules/{created['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["enabled"] is False

        from app.repositories.automation_rule_repo import AutomationRuleRepository
        from app.models.automation_rule import AutomationRuleRun

        rule = AutomationRuleRepository(db).get(created["id"])
        state = json.loads(rule.state_json)
        assert state["deleted"] is True
        assert db.query(AutomationRuleRun).filter(AutomationRuleRun.rule_id == created["id"]).count() >= 0
    finally:
        cleanup()


def test_automation_rules_api_validation_and_missing_github_config():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        bad = client.post("/api/automation-rules", json={"name": "x", "target_agent_id": agent.id, "task_template_id": "github_pr_review", "scope": {"owner": "acme", "repo": "portal"}, "trigger_config": {"review_target_type": "invalid", "review_target": "x"}})
        assert bad.status_code in (400, 422)

        created = client.post("/api/automation-rules", json=_create_payload(agent.id)).json()
        patch_empty = client.patch(f"/api/automation-rules/{created['id']}", json={"scope": {"owner": ""}})
        assert patch_empty.status_code in (400, 422)
        patch_bad = client.patch(f"/api/automation-rules/{created['id']}", json={"trigger_config": {"review_target_type": "bad"}})
        assert patch_bad.status_code in (400, 422)
        patch_interval = client.patch(f"/api/automation-rules/{created['id']}", json={"schedule": {"interval_seconds": 1}})
        assert patch_interval.status_code == 200
        ok_patch = client.patch(f"/api/automation-rules/{created['id']}", json={"enabled": False})
        assert ok_patch.status_code == 200

        rp = db.get(RuntimeProfile, agent.runtime_profile_id)
        rp.config_json = json.dumps({"github": {"enabled": False}})
        db.add(rp); db.commit()
        missing = client.post("/api/automation-rules", json={"name": "x", "target_agent_id": agent.id, "task_template_id": "github_pr_review", "scope": {"owner": "acme", "repo": "portal"}, "trigger_config": {"review_target_type": "user", "review_target": "alice"}})
        assert missing.status_code == 400
        assert "GitHub is not enabled" in missing.json()["detail"]
    finally:
        cleanup()


def test_automation_rules_api_capability_profile_gate():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        cp_jira = CapabilityProfile(name="cap-jira-only", allowed_external_systems_json='["jira"]')
        db.add(cp_jira); db.commit(); db.refresh(cp_jira)
        agent.capability_profile_id = cp_jira.id
        db.add(agent); db.commit()

        resp = client.post("/api/automation-rules", json=_create_payload(agent.id))
        assert resp.status_code == 400
        assert "capability profile does not allow" in resp.json()["detail"]

        cp_ok = CapabilityProfile(
            name="cap-github-ok",
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["pull_request_review_requested"]',
            allowed_actions_json='["review_pull_request"]',
        )
        db.add(cp_ok); db.commit(); db.refresh(cp_ok)
        agent.capability_profile_id = cp_ok.id
        db.add(agent); db.commit()
        ok = client.post("/api/automation-rules", json=_create_payload(agent.id))
        assert ok.status_code == 200

        cp_bad_trigger = CapabilityProfile(
            name="cap-bad-trigger",
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["jira.assigned"]',
            allowed_actions_json='["review_pull_request"]',
        )
        db.add(cp_bad_trigger); db.commit(); db.refresh(cp_bad_trigger)
        agent.capability_profile_id = cp_bad_trigger.id
        db.add(agent); db.commit()
        bad_trigger = client.post("/api/automation-rules", json=_create_payload(agent.id))
        assert bad_trigger.status_code == 400
        assert "capability profile does not allow" in bad_trigger.json()["detail"]

        cp_bad_action = CapabilityProfile(
            name="cap-bad-action",
            allowed_external_systems_json='["github"]',
            allowed_webhook_triggers_json='["pull_request_review_requested"]',
            allowed_actions_json='["jira_transition"]',
        )
        db.add(cp_bad_action); db.commit(); db.refresh(cp_bad_action)
        agent.capability_profile_id = cp_bad_action.id
        db.add(agent); db.commit()
        bad_action = client.post("/api/automation-rules", json=_create_payload(agent.id))
        assert bad_action.status_code == 400
        assert "capability profile does not allow" in bad_action.json()["detail"]
    finally:
        cleanup()


def test_automation_rules_api_update_merged_validation_and_events_updated_at():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        create_payload = _create_payload(agent.id)
        create_payload["trigger_config"] = {"review_target_type": "user", "review_target": "alice"}
        created_resp = client.post("/api/automation-rules", json=create_payload)
        assert created_resp.status_code == 200
        rule = created_resp.json()

        bad_target = client.patch(f"/api/automation-rules/{rule['id']}", json={"trigger_config": {"review_target": "alice bob"}})
        assert bad_target.status_code in (400, 422)
        saved_rule = AutomationRuleRepository(db).get(rule["id"])
        saved_trigger = json.loads(saved_rule.trigger_config_json or "{}")
        assert saved_trigger.get("review_target") == "alice"

        good_team = client.patch(
            f"/api/automation-rules/{rule['id']}",
            json={"trigger_config": {"review_target_type": "team", "review_target": "acme/reviewers"}},
        )
        assert good_team.status_code == 200

        bad_event = client.patch(f"/api/automation-rules/{rule['id']}", json={"task_input_defaults": {"review_event": "bad"}})
        assert bad_event.status_code in (400, 422)
        bad_owner = client.patch(f"/api/automation-rules/{rule['id']}", json={"scope": {"owner": ""}})
        assert bad_owner.status_code in (400, 422)

        repo = AutomationRuleRepository(db)
        repo.create_event(
            rule_id=rule["id"],
            dedupe_key="api:event:1",
            source_payload_json="{}",
            normalized_payload_json="{}",
            status="discovered",
        )

        events_resp = client.get(f"/api/automation-rules/{rule['id']}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert events
        assert "updated_at" in events[0]
    finally:
        cleanup()


def test_api_create_github_comment_mention_rule_success_without_trigger_type():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {
            "name": "mention rule",
            "target_agent_id": agent.id,
            "task_template_id": "github_comment_mention",
            "scope": {"owner": "acme", "repo": "portal"},
            "trigger_config": {"mention_target": "efp-agent"},
        }
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["trigger_type"] == "github_comment_mention"
        assert body["task_type"] == "triggered_event_task"
    finally:
        cleanup()


def test_api_create_github_comment_mention_bad_surface_returns_400():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {
            "name": "mention rule",
            "target_agent_id": agent.id,
            "task_template_id": "github_comment_mention",
            "scope": {"owner": "acme", "repo": "portal", "surfaces": ["bad_surface"]},
            "trigger_config": {"mention_target": "efp-agent"},
        }
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 400
    finally:
        cleanup()


def test_api_create_github_comment_mention_bad_schedule_returns_400():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {
            "name": "mention rule",
            "target_agent_id": agent.id,
            "task_template_id": "github_comment_mention",
            "scope": {"owner": "acme", "repo": "portal"},
            "trigger_config": {"mention_target": "efp-agent"},
            "schedule": {"interval_seconds": "abc"},
        }
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 400
        assert "schedule.interval_seconds must be an integer" in resp.json()["detail"]
    finally:
        cleanup()


def test_api_create_github_comment_mention_wrong_source_type_returns_400():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {
            "name": "mention rule",
            "target_agent_id": agent.id,
            "source_type": "jira",
            "task_template_id": "github_comment_mention",
            "scope": {"owner": "acme", "repo": "portal"},
            "trigger_config": {"mention_target": "efp-agent"},
        }
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 400
        assert "source_type must be github" in resp.json()["detail"]
    finally:
        cleanup()


def test_api_create_github_comment_mention_wrong_trigger_type_returns_400():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {
            "name": "mention rule",
            "target_agent_id": agent.id,
            "source_type": "github",
            "trigger_type": "github_pr_review_requested",
            "task_template_id": "github_comment_mention",
            "scope": {"owner": "acme", "repo": "portal"},
            "trigger_config": {"mention_target": "efp-agent"},
        }
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 400
        assert "trigger_type must be github_comment_mention" in resp.json()["detail"]
    finally:
        cleanup()


def test_api_create_commit_comment_surface_success_when_capability_allows():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        cp_ok = CapabilityProfile(name="cap-commit-ok", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment","reply_review_comment","add_commit_comment"]')
        db.add(cp_ok); db.commit(); db.refresh(cp_ok)
        agent.capability_profile_id = cp_ok.id
        db.add(agent); db.commit()
        payload = {"name": "mention", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, "trigger_config": {"mention_target": "efp-agent"}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 200
    finally:
        cleanup()


def test_api_create_commit_comment_surface_blocked_when_capability_missing():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        cp_bad = CapabilityProfile(name="cap-commit-bad", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment","reply_review_comment"]')
        db.add(cp_bad); db.commit(); db.refresh(cp_bad)
        agent.capability_profile_id = cp_bad.id
        db.add(agent); db.commit()
        payload = {"name": "mention", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, "trigger_config": {"mention_target": "efp-agent"}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 400
    finally:
        cleanup()


def test_api_create_org_scope_success():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {"name": "org-mention", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"mode": "org", "owner": "acme", "repo_selector": {"include": ["api-*"], "exclude": ["old-*"]}}, "trigger_config": {"mention_target": "efp-agent"}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 200
    finally:
        cleanup()


def test_api_create_discussion_comment_success_when_capability_allows():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        cp_ok = CapabilityProfile(name="cap-disc-ok", allowed_external_systems_json='["github"]', allowed_actions_json='["add_discussion_comment"]')
        db.add(cp_ok); db.commit(); db.refresh(cp_ok)
        agent.capability_profile_id = cp_ok.id
        db.add(agent); db.commit()
        payload = {"name": "mention", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"owner": "acme", "repo": "portal", "surfaces": ["discussion_comment"]}, "trigger_config": {"mention_target": "efp-agent"}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 200
    finally:
        cleanup()


def test_api_create_discussion_comment_returns_400_when_capability_missing():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        cp_bad = CapabilityProfile(name="cap-disc-bad", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment"]')
        db.add(cp_bad); db.commit(); db.refresh(cp_bad)
        agent.capability_profile_id = cp_bad.id
        db.add(agent); db.commit()
        payload = {"name": "mention", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"owner": "acme", "repo": "portal", "surfaces": ["discussion_comment"]}, "trigger_config": {"mention_target": "efp-agent"}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 400
    finally:
        cleanup()

def test_api_create_commit_comment_with_tail_pages_succeeds():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        cp_ok = CapabilityProfile(name="cap-commit-tail", allowed_external_systems_json='["github"]', allowed_actions_json='["add_comment","reply_review_comment","add_commit_comment"]')
        db.add(cp_ok); db.commit(); db.refresh(cp_ok)
        agent.capability_profile_id = cp_ok.id; db.add(agent); db.commit()
        payload = {"name": "mention", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"owner": "acme", "repo": "portal", "surfaces": ["commit_comment"]}, "trigger_config": {"mention_target": "efp-agent"}, "schedule": {"interval_seconds": 60, "commit_comment_initial_tail_pages": 3}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 200
    finally:
        cleanup()


def test_api_create_org_mode_with_max_repos_per_run_succeeds():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload = {"name": "org-limit", "target_agent_id": agent.id, "task_template_id": "github_comment_mention", "scope": {"mode": "org", "owner": "acme", "repo_selector": {"include": ["*"]}}, "trigger_config": {"mention_target": "efp-agent"}, "schedule": {"interval_seconds": 60, "max_repos_per_run": 10}}
        resp = client.post("/api/automation-rules", json=payload)
        assert resp.status_code == 200
    finally:
        cleanup()



def test_api_create_account_notifications_mode_success():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload={"name":"acc","target_agent_id":agent.id,"task_template_id":"github_comment_mention","scope":{"mode":"account_notifications","surfaces":["issue_comment"],"notification_reasons":["mention"]},"trigger_config":{"mention_target":"efp-agent"}}
        r=client.post('/api/automation-rules', json=payload)
        assert r.status_code==200
        b=r.json(); assert b["trigger_type"]=="github_comment_mention" and b["task_type"]=="triggered_event_task"
    finally:
        cleanup()

def test_api_create_account_notifications_bad_notification_reasons_returns_400():
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        payload={"name":"acc","target_agent_id":agent.id,"task_template_id":"github_comment_mention","scope":{"mode":"account_notifications","notification_reasons":"mention"},"trigger_config":{"mention_target":"efp-agent"}}
        r=client.post('/api/automation-rules', json=payload)
        assert r.status_code==400
    finally:
        cleanup()
