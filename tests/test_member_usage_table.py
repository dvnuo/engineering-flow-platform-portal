"""User Management as a table with what each member consumed.

The Portal never kept tokens or credits: both runtimes report them on each
run's final payload and keep their own usage.jsonl, readable only while that
pod runs. Executions now keep the usage the runtime reported, the member
overview sums it per period, and the admin panel is one sortable row per
member with a period select and a CSV export.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models import AgentExecution, User, UserAllowlistEntry
from app.repositories.agent_execution_repo import AgentExecutionRepository
from app.services.agent_execution_registry import (
    ChatStreamExecutionObserver,
    finish_chat_response_best_effort,
    mark_task_execution_status_best_effort,
    usage_fields_from_payload,
)
from app.services.member_management_service import MemberManagementService, normalize_period_days
from tests.test_agent_execution_registry import _db_session
from tests.test_user_admin_management import _database, _override_db


NATIVE_FINAL = {
    "response": "done",
    "session_id": "s-1",
    "model": "gpt-6.1-sol",
    "usage": {"input_tokens": 1200, "output_tokens": 300, "cached_input_tokens": 800, "reasoning_tokens": 40, "estimated_credits": 0.75, "pricing": {"model": "gpt-6.1", "tier": "standard"}},
}


def test_usage_fields_read_both_runtimes_shapes_and_skip_what_is_missing():
    assert usage_fields_from_payload(NATIVE_FINAL) == {
        "model": "gpt-6.1-sol",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cached_input_tokens": 800,
        "reasoning_tokens": 40,
        "estimated_credits": 0.75,
    }
    # OpenAI names, the billed model from pricing when the payload names none, string numbers.
    openai_shape = {"usage": {"prompt_tokens": "10", "completion_tokens": 5, "estimated_credits": 0.1, "pricing": {"model": "claude-4.5"}}}
    assert usage_fields_from_payload(openai_shape) == {"model": "claude-4.5", "input_tokens": 10, "output_tokens": 5, "estimated_credits": 0.1}
    # A task result nests the block; nothing reported means nothing recorded, never zeros.
    assert usage_fields_from_payload({"result": {"usage": {"input_tokens": 3}, "model": "m"}}) == {"model": "m", "input_tokens": 3}
    assert usage_fields_from_payload({"response": "x"}) == {}
    assert usage_fields_from_payload({"usage": {"input_tokens": True, "estimated_credits": "nope"}}) == {}
    assert usage_fields_from_payload("not a dict") == {}


def test_chat_finish_keeps_the_usage_on_the_execution_row_on_both_paths(monkeypatch):
    db, session_factory, _user, agent = _db_session()
    try:
        repo = AgentExecutionRepository(db)
        sync_execution = repo.create(agent_id=agent.id, session_id="s-1", kind="chat", status="running", source="portal")
        finish_chat_response_best_effort(db, execution_id=sync_execution.id, status_code=200, content=json.dumps(NATIVE_FINAL).encode("utf-8"))
        db.refresh(sync_execution)
        assert sync_execution.status == "succeeded"
        assert (sync_execution.model, sync_execution.input_tokens, sync_execution.output_tokens) == ("gpt-6.1-sol", 1200, 300)
        assert (sync_execution.cached_input_tokens, sync_execution.reasoning_tokens, sync_execution.estimated_credits) == (800, 40, 0.75)

        import app.services.agent_execution_registry as registry_module

        monkeypatch.setattr(registry_module, "SessionLocal", session_factory)
        stream_execution = repo.create(agent_id=agent.id, session_id="s-2", kind="chat", status="running", source="portal")
        observer = ChatStreamExecutionObserver(stream_execution.id)
        observer.feed(("event: usage\ndata: " + json.dumps({"usage": NATIVE_FINAL["usage"]}) + "\n\n").encode("utf-8"))
        observer.feed(("event: final\ndata: " + json.dumps(NATIVE_FINAL) + "\n\n").encode("utf-8"))
        observer.feed(b"event: done\ndata: {\"ok\": true}\n\n")
        observer.finish(status_code=200)
        db.expire_all()
        refreshed = db.get(AgentExecution, stream_execution.id)
        assert refreshed.status == "succeeded"
        assert refreshed.estimated_credits == 0.75 and refreshed.input_tokens == 1200 and refreshed.model == "gpt-6.1-sol"

        # A failed run without a payload records nothing and keeps the row otherwise intact.
        failed = repo.create(agent_id=agent.id, session_id="s-3", kind="chat", status="running", source="portal")
        finish_chat_response_best_effort(db, execution_id=failed.id, status_code=502, content=b"bad gateway")
        db.refresh(failed)
        assert failed.status == "failed" and failed.estimated_credits is None and failed.model is None
    finally:
        db.close()


def test_task_status_write_carries_the_task_results_usage():
    from app.models import AgentTask

    db, _factory, user, agent = _db_session()
    try:
        task = AgentTask(id="task-1", assignee_agent_id=agent.id, source="portal", task_type="agent_async_task", status="running", owner_user_id=user.id, created_by_user_id=user.id, task_session_id="agent-task:1")
        db.add(task)
        db.commit()
        repo = AgentExecutionRepository(db)
        execution = repo.create(agent_id=agent.id, session_id="agent-task:1", kind="async_task", status="running", source="portal", task_id=task.id, runtime_task_id=task.id)
        result_payload = {"status": "success", "final_response": "ok", "usage": {"input_tokens": 50, "output_tokens": 7, "estimated_credits": 0.02}, "model": "gpt-6.1"}
        mark_task_execution_status_best_effort(db, task=task, status="done", result_summary="ok", usage=usage_fields_from_payload(result_payload))
        db.refresh(execution)
        assert execution.status == "succeeded"
        assert (execution.model, execution.input_tokens, execution.output_tokens, execution.estimated_credits) == ("gpt-6.1", 50, 7, 0.02)
    finally:
        db.close()

def _execution(agent_id, user_id, *, kind="chat", created_at, credits=None, tokens_in=None, tokens_out=None):
    return AgentExecution(
        agent_id=agent_id,
        kind=kind,
        status="succeeded",
        owner_user_id=user_id,
        created_by_user_id=user_id,
        created_at=created_at,
        updated_at=created_at,
        estimated_credits=credits,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )


def test_member_overview_sums_consumption_over_the_period_only():
    db, _factory, user, agent = _db_session()
    now = datetime(2026, 11, 5, 12, 0, 0)
    try:
        db.add(UserAllowlistEntry(username=user.username, role="admin", is_active=True))
        db.add_all([
            _execution(agent.id, user.id, created_at=now - timedelta(days=1), credits=1.5, tokens_in=1000, tokens_out=100),
            _execution(agent.id, user.id, created_at=now - timedelta(days=1, hours=3), credits=0.5, tokens_in=200, tokens_out=50),
            _execution(agent.id, user.id, kind="async_task", created_at=now - timedelta(days=20), credits=2.0, tokens_in=5000, tokens_out=900),
            # Outside a 30-day window, inside nothing we offer; and a run that reported no usage.
            _execution(agent.id, user.id, created_at=now - timedelta(days=45), credits=9.0, tokens_in=99999, tokens_out=99999),
            _execution(agent.id, user.id, created_at=now - timedelta(hours=2)),
        ])
        db.commit()
        service = MemberManagementService(db)

        month = service.build_overview(days=30, now=now)
        row = month["users"][0]
        assert month["period_days"] == 30 and month["summary"]["period_days"] == 30
        assert row["execution_count"] == 5 and row["chat_count"] == 4, "all-time counts stay"
        assert row["execution_count_period"] == 4 and row["chat_count_period"] == 3
        assert row["credits_period"] == 4.0
        assert (row["input_tokens_period"], row["output_tokens_period"]) == (6200, 1050)
        assert row["active_days_period"] == 3, "two runs on one day count once"
        assert month["summary"]["credits_period_total"] == 4.0
        assert month["summary"]["active_members_period"] == 1
        assert (month["summary"]["input_tokens_period_total"], month["summary"]["output_tokens_period_total"]) == (6200, 1050)

        week = service.build_overview(days=7, now=now)
        assert week["users"][0]["credits_period"] == 2.0
        assert week["users"][0]["execution_count_period"] == 3

        quarter = service.build_overview(days=90, now=now)
        assert quarter["users"][0]["credits_period"] == 13.0

        # An unknown period falls back to 30 days rather than failing the panel.
        assert service.build_overview(days=12, now=now)["period_days"] == 30
    finally:
        db.close()


def test_normalize_period_days():
    assert normalize_period_days("7") == 7
    assert normalize_period_days(90) == 90
    assert normalize_period_days(None) == 30
    assert normalize_period_days("365") == 30
    assert normalize_period_days("x") == 30


def test_admin_overview_api_takes_the_period(monkeypatch):
    from app.main import app
    import app.api.users as users_api
    import app.deps as deps_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    current = SimpleNamespace(id=admin.id, username=admin.username, nickname=admin.username, role=admin.role)
    app.dependency_overrides[users_api.get_db] = _override_db(db)
    app.dependency_overrides[deps_module.require_admin] = lambda: current
    app.dependency_overrides[deps_module.get_current_user] = lambda: current
    client = TestClient(app)
    try:
        default = client.get("/api/users/admin-overview")
        assert default.status_code == 200
        assert default.json()["period_days"] == 30
        row = default.json()["users"][0]
        for field in ("execution_count_period", "chat_count_period", "credits_period", "input_tokens_period", "output_tokens_period", "active_days_period"):
            assert field in row
        assert default.json()["summary"]["credits_period_total"] == 0
        assert client.get("/api/users/admin-overview?days=7").json()["period_days"] == 7
        assert client.get("/api/users/admin-overview?days=12").status_code == 422
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_users_panel_is_a_sortable_table_with_period_and_export(monkeypatch):
    from app.main import app
    import app.web as web_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="member", nickname="Mem Ber", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.refresh(admin)
    db.add_all([UserAllowlistEntry(username="admin", role="admin", is_active=True), UserAllowlistEntry(username="member", role="user", is_active=True)])
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: admin)
    client = TestClient(app)
    try:
        html = client.get("/app/users/panel").text
        assert 'data-period-days="30"' in html
        assert "data-admin-member-table" in html and "data-admin-member-rows" in html
        assert html.count('<tr\n            class="portal-admin-member-row"') == 2
        for key in ("member", "access", "assistants", "executions", "chats", "tasks", "delegations", "credits", "tokens-in", "tokens-out", "active-days", "last-active", "joined"):
            assert f'data-sort-key="{key}"' in html, key
        assert "data-admin-member-period" in html and '<option value="30" selected>' in html
        assert "data-admin-member-export" in html
        assert "Mem Ber" in html and "@member" in html
        # The role toggle and the allowlist actions moved into the last cell unchanged.
        assert html.count("data-admin-role-group") == 2
        assert "data-remove-allowlist" in html

        week = client.get("/app/users/panel?days=7").text
        assert 'data-period-days="7"' in week and '<option value="7" selected>' in week
        assert 'data-period-days="30"' in client.get("/app/users/panel?days=11").text
    finally:
        db.close()


def test_admin_script_sorts_exports_and_reloads_with_the_period():
    js = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
    assert "function sortMemberTable(root, header)" in js
    assert 'th[data-sort-key]' in js and 'aria-sort' in js
    assert "function exportMembersCsv(root)" in js and 'text/csv;charset=utf-8' in js
    assert '"/app/users/panel?days=" + encodeURIComponent(selectedPeriodDays())' in js
    assert 'closest("[data-admin-member-period]")' in js
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert ".portal-admin-member-table th[data-sort-key]" in css
    assert ".portal-admin-usage-grid" not in css, "the card layout is gone"
