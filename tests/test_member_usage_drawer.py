"""The member drawer in User Management: daily credits, models and kinds for one member, plus the inactive filter."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models import AgentExecution, User, UserAllowlistEntry
from app.services import usage_rollup_service
from app.services.member_management_service import MemberManagementService
from app.services.usage_rollup_service import UsageRollupService
from tests.test_agent_execution_registry import _db_session
from tests.test_user_admin_management import _database, _override_db


NOON = datetime(2026, 11, 5, 12, 0, 0)


def _execution(db, agent, user, *, created_at, kind="chat", status="succeeded", model="gpt-6.1", estimated=None, actual=None, tokens=(0, 0)):
    db.add(
        AgentExecution(
            agent_id=agent.id, kind=kind, status=status, source="portal", runtime_type="native",
            owner_user_id=user.id, created_by_user_id=user.id, model=model,
            input_tokens=tokens[0], output_tokens=tokens[1], estimated_credits=estimated, actual_credits=actual,
            created_at=created_at, updated_at=created_at, started_at=created_at, finished_at=created_at,
        )
    )


def test_member_usage_builds_the_series_models_and_kinds_from_the_rollups(monkeypatch):
    db, _factory, user, agent = _db_session()
    try:
        monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
        _execution(db, agent, user, created_at=NOON, estimated=1.0, actual=0.8, tokens=(1000, 100))
        _execution(db, agent, user, created_at=NOON - timedelta(days=3), model="claude-4.5", estimated=0.5, tokens=(200, 20))
        _execution(db, agent, user, created_at=NOON - timedelta(days=3, hours=1), kind="async_task", status="failed", estimated=0.2)
        _execution(db, agent, user, created_at=NOON - timedelta(days=40), estimated=9.0)
        db.commit()
        UsageRollupService(db).rollup_days([NOON.date(), (NOON - timedelta(days=3)).date(), (NOON - timedelta(days=40)).date()])

        usage = MemberManagementService(db).member_usage(user.id, days=7, now=NOON)
        assert usage["period_days"] == 7 and len(usage["daily"]) == 7
        assert usage["daily"][-1] == {"day": "2026-11-05", "credits": 0.8, "executions": 1, "height": 100.0}
        assert usage["daily"][3]["credits"] == 0.7 and usage["daily"][3]["height"] == 87.5
        assert usage["daily"][0]["credits"] == 0 and usage["daily"][0]["height"] == 0
        totals = usage["totals"]
        assert totals["executions"] == 3 and totals["failed"] == 1 and totals["credits"] == 1.5
        assert totals["active_days"] == 2 and (totals["input_tokens"], totals["output_tokens"]) == (1200, 120)
        assert [row["model"] for row in usage["by_model"]] == ["gpt-6.1", "claude-4.5"]
        assert usage["by_model"][0]["credits"] == 1.0, "the chat on gpt-6.1 (Copilot's charge) and the failed task (estimate)"
        kinds = {row["kind"]: row for row in usage["by_kind"]}
        assert kinds["chat"]["executions"] == 2 and kinds["async_task"]["failure_rate"] == 1.0

        # Another member's runs stay out; an unknown period falls back to 30 days.
        other = User(username="other", password_hash="x", role="user", is_active=True)
        db.add(other)
        db.commit()
        db.refresh(other)
        assert MemberManagementService(db).member_usage(other.id, days=30, now=NOON)["totals"]["executions"] == 0
        assert MemberManagementService(db).member_usage(user.id, days=11, now=NOON)["period_days"] == 30
    finally:
        db.close()


def test_member_usage_api_and_drawer_route_are_admin_only(monkeypatch):
    from app.main import app
    import app.api.users as users_api
    import app.deps as deps_module
    import app.web as web_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="12345678", nickname="Alex", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.refresh(admin)
    db.refresh(member)
    current = SimpleNamespace(id=admin.id, username=admin.username, nickname=admin.username, role=admin.role)
    app.dependency_overrides[users_api.get_db] = _override_db(db)
    app.dependency_overrides[deps_module.require_admin] = lambda: current
    app.dependency_overrides[deps_module.get_current_user] = lambda: current
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: current)
    monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
    client = TestClient(app)
    try:
        body = client.get(f"/api/users/{member.id}/usage?days=7").json()
        assert body["user_id"] == member.id and body["display_name"] == "Alex" and body["period_days"] == 7
        assert len(body["daily"]) == 7 and body["totals"]["executions"] == 0
        assert client.get(f"/api/users/{member.id}/usage?days=12").status_code == 422
        assert client.get("/api/users/999/usage").status_code == 404

        drawer = client.get(f"/app/users/{member.id}/usage/panel?days=7")
        assert drawer.status_code == 200
        assert f'data-member-usage="{member.id}"' in drawer.text and 'data-period-days="7"' in drawer.text
        assert drawer.text.count('<span class="portal-member-usage-bar') == 7
        assert "No runs in this period." in drawer.text and "Alex" in drawer.text
        assert client.get("/app/users/999/usage/panel").status_code == 404

        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: SimpleNamespace(id=member.id, username=member.username, nickname=member.username, role="user"))
        assert client.get(f"/app/users/{member.id}/usage/panel").status_code == 403
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_users_panel_offers_the_drawer_and_the_inactive_filter(monkeypatch):
    from app.main import app
    import app.web as web_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    db.add(UserAllowlistEntry(username="admin", role="admin", is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: SimpleNamespace(id=admin.id, username="admin", nickname="admin", role="admin"))
    client = TestClient(app)
    try:
        html = client.get("/app/users/panel").text
        assert f'data-admin-member-detail="{admin.id}"' in html and 'aria-expanded="false"' in html
        assert 'data-member-executions-period="0"' in html
        assert '<option value="inactive">' in html
    finally:
        db.close()


def test_admin_script_toggles_the_drawer_and_filters_inactive_members():
    js = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
    assert "async function toggleMemberDetail(button)" in js
    assert "function closeMemberDetails(root)" in js
    assert '"/app/users/" + encodeURIComponent(memberId) + "/usage/panel?days=" + encodeURIComponent(selectedPeriodDays())' in js
    assert 'access === "inactive" ? Number(card.dataset.memberExecutionsPeriod || 0) === 0' in js
    # Sorting moves member rows, so open drawers close first instead of drifting under another member.
    sort_body = js[js.index("function sortMemberTable(root, header)"):]
    assert "closeMemberDetails(root);" in sort_body[: sort_body.index("var rows = memberRows(root);")]
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert ".portal-admin-member-name" in css and ".portal-member-usage-bars" in css
