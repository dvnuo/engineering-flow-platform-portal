"""A member's own usage across assistants, this month's credits and the guideline notice."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models import Agent, AgentExecution, User
from app.services import member_management_service as service_module
from app.services import usage_rollup_service
from app.services.member_management_service import MemberManagementService
from app.services.usage_rollup_service import UsageRollupService
from tests.test_agent_execution_registry import _db_session
from tests.test_user_admin_management import _database, _override_db


NOW = datetime(2026, 11, 5, 12, 0, 0)


def _execution(db, agent, user, *, created_at, credits):
    db.add(
        AgentExecution(
            agent_id=agent.id, kind="chat", status="succeeded", source="portal", runtime_type="native",
            owner_user_id=user.id, created_by_user_id=user.id, model="gpt-6.1", estimated_credits=credits,
            created_at=created_at, updated_at=created_at, started_at=created_at, finished_at=created_at,
        )
    )


def test_month_to_date_notice_and_by_agent(monkeypatch):
    db, _factory, user, agent = _db_session()
    try:
        monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
        other_agent = Agent(name="Second assistant", owner_user_id=user.id, visibility="private", status="running", image="img", deployment_name="d2", service_name="s2", pvc_name="p2")
        db.add(other_agent)
        db.commit()
        db.refresh(other_agent)
        _execution(db, agent, user, created_at=NOW, credits=3.0)
        _execution(db, agent, user, created_at=NOW - timedelta(days=2), credits=2.0)
        _execution(db, other_agent, user, created_at=NOW - timedelta(days=10), credits=1.0)  # October: last month
        _execution(db, agent, user, created_at=NOW - timedelta(days=20), credits=4.0)  # October too
        db.commit()
        UsageRollupService(db).rollup_days([(NOW - timedelta(days=offset)).date() for offset in (0, 2, 10, 20)])
        service = MemberManagementService(db)

        month = service.member_month_to_date(user.id, now=NOW)
        assert month == {"month": "2026-11", "credits": 5.0, "executions": 2}

        monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(member_credit_notice_threshold=0))
        assert service.credit_notice(user.id, now=NOW) == {"month": "2026-11", "credits": 5.0, "executions": 2, "threshold": None, "over": False, "ratio": None}
        monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(member_credit_notice_threshold=4))
        notice = service.credit_notice(user.id, now=NOW)
        assert notice["over"] is True and notice["threshold"] == 4.0 and notice["ratio"] == 1.25
        monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(member_credit_notice_threshold=50))
        assert service.credit_notice(user.id, now=NOW)["over"] is False

        usage = service.member_usage(user.id, days=30, now=NOW)
        assert [row["name"] for row in usage["by_agent"]] == [agent.name, "Second assistant"]
        assert usage["by_agent"][0]["credits"] == 9.0 and usage["by_agent"][1]["executions"] == 1
    finally:
        db.close()


def test_me_usage_api_serves_the_signed_in_member(monkeypatch):
    from app.main import app
    import app.api.users as users_api
    import app.deps as deps_module

    db = _database()
    member = User(username="12345678", nickname="Alex", password_hash="hash", role="user", is_active=True)
    db.add(member)
    db.commit()
    db.refresh(member)
    current = SimpleNamespace(id=member.id, username=member.username, nickname=member.nickname, role="user")
    app.dependency_overrides[users_api.get_db] = _override_db(db)
    app.dependency_overrides[deps_module.get_current_user] = lambda: current
    monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
    monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(member_credit_notice_threshold=20))
    client = TestClient(app)
    try:
        response = client.get("/api/users/me/usage?days=7")
        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == member.id and body["period_days"] == 7 and len(body["daily"]) == 7
        assert body["notice"]["threshold"] == 20.0 and body["notice"]["over"] is False
        assert "by_agent" in body
        assert client.get("/api/users/me/usage?days=12").status_code == 422
        # The admin-only route still answers 403 for a member.
        assert client.get(f"/api/users/{member.id}/usage").status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_usage_panel_opens_with_the_members_own_figures(monkeypatch):
    from app.main import app
    import app.web as web_module

    db = _database()
    member = User(username="12345678", nickname="Alex", password_hash="hash", role="user", is_active=True)
    db.add(member)
    db.commit()
    db.refresh(member)
    agent = Agent(name="Release assistant", owner_user_id=member.id, visibility="private", status="running", image="img", deployment_name="d", service_name="s", pvc_name="p")
    db.add(agent)
    db.commit()
    db.refresh(agent)
    now = datetime.utcnow()
    _execution(db, agent, member, created_at=now - timedelta(hours=1), credits=7.5)
    db.commit()
    # The route commits and closes the session; keep the id, not the row.
    agent_id = agent.id
    current = SimpleNamespace(id=member.id, username=member.username, nickname=member.nickname, role="user")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: current)
    monkeypatch.setattr(web_module.settings, "k8s_enabled", False)
    monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
    monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(member_credit_notice_threshold=5))
    client = TestClient(app)
    try:
        html = client.get(f"/app/agents/{agent_id}/usage/panel").text
        assert "Your usage across assistants" in html and "data-my-usage" in html
        assert ">7.5<" in html and "Release assistant" in html
        assert "data-my-usage-notice" in html and "above the 5-credit guideline" in html

        monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(member_credit_notice_threshold=0))
        calm = client.get(f"/app/agents/{agent_id}/usage/panel").text
        assert "data-my-usage-notice" not in calm and "guideline" not in calm
    finally:
        db.close()


def test_context_panel_carries_the_member_credit_line():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert 'id="context-member-usage"' in js
    assert "async function renderMemberCreditNotice()" in js
    assert 'api("/api/users/me/usage?days=30")' in js
    assert 'void renderMemberCreditNotice();' in js
    assert 'data-member-credit-notice="${notice.over ? "over" : "ok"}"' in js
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert ".portal-member-credit-line.is-warning" in css
