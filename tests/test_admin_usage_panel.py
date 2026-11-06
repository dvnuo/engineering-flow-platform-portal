"""Administration > Usage: the platform's consumption, quality and adoption as a page."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models import AgentExecution, AgentTask, User, UserAllowlistEntry
from app.services import usage_rollup_service
from tests.test_user_admin_management import _database


def _client(monkeypatch, db, admin):
    from app.main import app
    import app.web as web_module

    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: admin)
    monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
    return TestClient(app)


def _seed(db):
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="12345678", nickname="Alex", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.refresh(admin)
    db.refresh(member)
    db.add(UserAllowlistEntry(username="admin", role="admin", is_active=True))
    from app.models import Agent

    agent = Agent(name="Release assistant", owner_user_id=member.id, visibility="private", status="running", image="img", deployment_name="d", service_name="s", pvc_name="p")
    db.add(agent)
    db.commit()
    db.refresh(agent)
    now = datetime.utcnow()
    db.add_all([
        AgentExecution(agent_id=agent.id, kind="chat", status="succeeded", runtime_type="native", owner_user_id=member.id, created_by_user_id=member.id, model="gpt-6.1", input_tokens=1200, output_tokens=300, cached_input_tokens=600, estimated_credits=1.5, actual_credits=1.2, created_at=now - timedelta(hours=1), updated_at=now, started_at=now - timedelta(hours=1), finished_at=now - timedelta(hours=1) + timedelta(seconds=12)),
        AgentExecution(agent_id=agent.id, kind="async_task", status="failed", runtime_type="native", owner_user_id=member.id, created_by_user_id=member.id, model="gpt-6.1", input_tokens=100, estimated_credits=0.1, terminal_reason="budget_paused", created_at=now - timedelta(days=1), updated_at=now),
        AgentTask(assignee_agent_id=agent.id, source="portal", task_type="agent_async_task", status="done", skill_name="code-review", owner_user_id=member.id, created_at=now - timedelta(days=1), updated_at=now),
    ])
    db.commit()
    return admin, member, agent


def test_usage_panel_renders_every_section_for_the_period(monkeypatch):
    db = _database()
    admin, _member, agent = _seed(db)
    # A plain object, not the ORM row: the route commits (rollups) and closes the
    # session, which would detach and expire a User instance between requests.
    current = SimpleNamespace(id=admin.id, username=admin.username, nickname=admin.username, role=admin.role)
    client = _client(monkeypatch, db, current)
    try:
        response = client.get("/app/admin/usage/panel")
        assert response.status_code == 200
        html = response.text
        assert 'id="admin-usage-panel"' in html and 'data-period-days="30"' in html
        for marker in ("Credits per day", "Executions per day", "By model", "Top members", "Top assistants", "Quality", "Adoption", "Connectors enabled", "Failures by model", "Delegation source", "Joined in"):
            assert marker in html, marker
        assert "gpt-6.1" in html and "Release assistant" in html and "Alex" in html and "/code-review" in html
        # The summary counts Copilot's charge for the first run and the estimate for the second.
        assert ">1.3<" in html
        assert "1 budget pauses" in html
        assert 'href="/api/admin/usage/export.csv?days=30&amp;rows=daily"' in html
        assert 'href="/api/admin/usage/export.csv?days=30&amp;rows=executions"' in html
        assert 'hx-get="/app/admin/usage/panel"' in html and '<option value="30" selected>' in html
        assert html.count('<span class="portal-usage-bar') == 60, "two charts, one bar per day of the period"

        week = client.get("/app/admin/usage/panel?days=7").text
        assert 'data-period-days="7"' in week and week.count('<span class="portal-usage-bar') == 14
        assert 'data-period-days="30"' in client.get("/app/admin/usage/panel?days=12").text
    finally:
        db.close()


def test_usage_panel_is_admin_only(monkeypatch):
    db = _database()
    member = User(username="member", password_hash="hash", role="user", is_active=True)
    db.add(member)
    db.commit()
    db.refresh(member)
    client = _client(monkeypatch, db, SimpleNamespace(id=member.id, username=member.username, nickname=member.username, role=member.role))
    try:
        assert client.get("/app/admin/usage/panel").status_code == 403
    finally:
        db.close()


def test_usage_panel_handles_an_empty_period(monkeypatch):
    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    client = _client(monkeypatch, db, SimpleNamespace(id=admin.id, username=admin.username, nickname=admin.username, role=admin.role))
    try:
        html = client.get("/app/admin/usage/panel?days=7").text
        assert "No runs in this period." in html
        assert html.count('class="portal-usage-bar is-empty"') == 14
    finally:
        db.close()


def test_usage_panel_is_wired_into_administration():
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert 'data-admin-panel="usage"' in app_html
    assert 'data-admin-panel-url="/app/admin/usage/panel"' in app_html
    assert '"usage": {' in js and 't("What the platform consumed, how runs went and what members use")' in js
    assert ".portal-usage-bars" in css and ".portal-usage-connector-row" in css
