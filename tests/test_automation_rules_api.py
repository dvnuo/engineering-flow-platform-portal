import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User


def _build_client_with_overrides():
    from app.main import app
    import app.api.automation_rules as api_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    user = User(username="owner", password_hash="pw", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "secret"}}), is_default=True)
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


def test_automation_rules_api_crud(monkeypatch):
    client, _db, agent, cleanup = _build_client_with_overrides()
    try:
        async def _fake_run(self, rule_id, triggered_by="api"):
            from app.services.automation_rule_service import RunOnceResult
            return RunOnceResult(rule_id=rule_id, status="success", found_count=1, created_task_count=1, skipped_count=0, run_id="run-1", created_task_ids=["task-1"])

        monkeypatch.setattr("app.services.automation_rule_service.AutomationRuleService.run_rule_once", _fake_run)

        payload = {
            "name": "Review EFP PRs",
            "enabled": True,
            "source_type": "github",
            "trigger_type": "github_pr_review_requested",
            "task_type": "github_review_task",
            "target_agent_id": agent.id,
            "owner": "acme",
            "repo": "portal",
            "review_target_type": "team",
            "review_target": "acme/reviewers",
            "interval_seconds": 60,
            "skill_name": "review-pull-request",
            "review_event": "COMMENT",
        }
        create_resp = client.post("/api/automation-rules", json=payload)
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["target_agent_id"] == agent.id

        list_resp = client.get("/api/automation-rules")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        patch_resp = client.patch(f"/api/automation-rules/{created['id']}", json={"enabled": False})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["enabled"] is False

        run_resp = client.post(f"/api/automation-rules/{created['id']}/run-once")
        assert run_resp.status_code == 200
        assert run_resp.json()["created_task_count"] == 1

        delete_resp = client.delete(f"/api/automation-rules/{created['id']}")
        assert delete_resp.status_code == 200
    finally:
        cleanup()


def test_automation_rules_api_validation_and_missing_github_config():
    client, db, agent, cleanup = _build_client_with_overrides()
    try:
        bad = client.post(
            "/api/automation-rules",
            json={
                "name": "x", "target_agent_id": agent.id, "owner": "acme", "repo": "portal", "review_target_type": "invalid", "review_target": "x"
            },
        )
        assert bad.status_code == 422

        rp = db.get(RuntimeProfile, agent.runtime_profile_id)
        rp.config_json = json.dumps({"github": {"enabled": False}})
        db.add(rp); db.commit()

        missing = client.post(
            "/api/automation-rules",
            json={
                "name": "x", "target_agent_id": agent.id, "owner": "acme", "repo": "portal", "review_target_type": "user", "review_target": "alice"
            },
        )
        assert missing.status_code == 400
        assert "GitHub is not enabled" in missing.json()["detail"]
    finally:
        cleanup()
