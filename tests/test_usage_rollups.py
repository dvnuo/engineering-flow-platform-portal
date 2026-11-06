"""Daily usage rollups, retention, actual credits and the admin usage API.

Executions keep what each run cost; the Usage panel needs a day's sums per
member, model and runtime over a period, and the sums must outlive the rows
once old executions are pruned. The native runtime also reports Copilot's own
charge next to its estimate, which is the figure to prefer.
"""

import csv
import io
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import AgentExecution, AgentTask, DelegationRule, UsageDailyRollup, User, UserAllowlistEntry
from app.repositories.agent_execution_repo import AgentExecutionRepository
from app.services import usage_rollup_service
from app.services.agent_execution_registry import finish_chat_response_best_effort, usage_fields_from_payload
from app.services.member_management_service import MemberManagementService
from app.services.usage_insights_service import UsageInsightsService
from app.services.usage_rollup_service import UsageRollupService
from app.services.usage_rollup_worker import UsageRollupWorker
from tests.test_agent_execution_registry import _db_session
from tests.test_user_admin_management import _database, _override_db


TODAY = date(2026, 11, 5)
NOON = datetime(2026, 11, 5, 12, 0, 0)


def test_usage_fields_take_copilots_own_charge_and_the_terminal_reason():
    native = {
        "model": "gpt-6.1",
        "credits_source": "copilot_usage",
        "terminal_reason": "budget_paused",
        "usage": {"input_tokens": 100, "output_tokens": 10, "estimated_credits": 0.5, "actual_credits": 0.42},
    }
    fields = usage_fields_from_payload(native)
    assert fields["estimated_credits"] == 0.5 and fields["actual_credits"] == 0.42
    assert fields["credits_source"] == "copilot_usage" and fields["terminal_reason"] == "budget_paused"
    # No label from the runtime: inferred from what it sent.
    assert usage_fields_from_payload({"usage": {"actual_credits": 1.0}})["credits_source"] == "copilot_usage"
    assert usage_fields_from_payload({"usage": {"estimated_credits": 1.0}})["credits_source"] == "estimate"
    assert "credits_source" not in usage_fields_from_payload({"usage": {"input_tokens": 3}})
    assert usage_fields_from_payload({"incomplete_reason": "step_cap", "usage": {}})["terminal_reason"] == "step_cap"


def _execution(db, agent, user, *, created_at, kind="chat", status="succeeded", model="gpt-6.1", estimated=None, actual=None, tokens=(0, 0, 0, 0), duration=None, terminal_reason=None):
    started = created_at
    finished = created_at + timedelta(seconds=duration) if duration is not None else None
    row = AgentExecution(
        agent_id=agent.id,
        kind=kind,
        status=status,
        source="portal",
        runtime_type="native",
        owner_user_id=user.id,
        created_by_user_id=user.id,
        model=model,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cached_input_tokens=tokens[2],
        reasoning_tokens=tokens[3],
        estimated_credits=estimated,
        actual_credits=actual,
        terminal_reason=terminal_reason,
        created_at=created_at,
        updated_at=created_at,
        started_at=started,
        finished_at=finished,
    )
    db.add(row)
    return row


def test_rollup_day_sums_per_member_model_and_kind_and_prefers_actual_credits():
    db, _factory, user, agent = _db_session()
    try:
        other = User(username="other", password_hash="x", role="user", is_active=True)
        db.add(other)
        db.commit()
        db.refresh(other)
        _execution(db, agent, user, created_at=NOON, estimated=1.0, actual=0.8, tokens=(1000, 100, 400, 10), duration=20)
        _execution(db, agent, user, created_at=NOON + timedelta(hours=1), estimated=0.5, tokens=(500, 50, 0, 0), duration=10)
        _execution(db, agent, user, created_at=NOON + timedelta(hours=2), kind="async_task", status="failed", estimated=0.2, tokens=(200, 0, 0, 0))
        _execution(db, agent, other, created_at=NOON, model="claude-4.5", estimated=2.0, tokens=(10, 10, 0, 0))
        _execution(db, agent, user, created_at=NOON - timedelta(days=1), estimated=9.0, tokens=(1, 1, 0, 0))
        db.commit()

        service = UsageRollupService(db)
        assert service.rollup_day(TODAY) == 3
        rows = {(r.user_id, r.kind, r.model): r for r in db.scalars(select(UsageDailyRollup).where(UsageDailyRollup.day == TODAY)).all()}
        chat = rows[(user.id, "chat", "gpt-6.1")]
        assert chat.executions == 2 and chat.succeeded == 2 and chat.failed == 0
        assert (chat.input_tokens, chat.output_tokens, chat.cached_input_tokens, chat.reasoning_tokens) == (1500, 150, 400, 10)
        assert chat.estimated_credits == 1.5 and chat.actual_credits == 0.8
        assert chat.credits == 1.3, "Copilot's charge for the first run, the estimate for the second"
        assert chat.duration_seconds == 30.0
        task = rows[(user.id, "async_task", "gpt-6.1")]
        assert task.executions == 1 and task.failed == 1 and task.credits == 0.2
        assert rows[(other.id, "chat", "claude-4.5")].credits == 2.0

        # A rerun replaces the day's rows instead of adding to them.
        assert service.rollup_day(TODAY) == 3
        assert db.execute(select(func.count()).select_from(UsageDailyRollup).where(UsageDailyRollup.day == TODAY)).scalar() == 3
        assert service.rollup_recent(2, today=TODAY) == {"2026-11-04": 1, "2026-11-05": 3}
    finally:
        db.close()


def test_prune_rolls_old_days_up_before_deleting_their_terminal_executions():
    db, _factory, user, agent = _db_session()
    try:
        old = NOON - timedelta(days=400)
        _execution(db, agent, user, created_at=old, estimated=3.0, tokens=(30, 3, 0, 0))
        _execution(db, agent, user, created_at=old + timedelta(days=1), status="running", estimated=None)
        _execution(db, agent, user, created_at=NOON - timedelta(days=10), estimated=1.0)
        db.commit()
        service = UsageRollupService(db)

        assert service.prune_executions(0, today=TODAY) == {"rolled_up_days": 0, "deleted": 0}, "0 keeps everything"
        result = service.prune_executions(365, today=TODAY)
        assert result["deleted"] == 1, "the running row stays, the recent row stays"
        assert result["rolled_up_days"] >= 1
        kept = db.scalars(select(AgentExecution)).all()
        assert {row.status for row in kept} == {"running", "succeeded"} and len(kept) == 2
        rolled = db.scalars(select(UsageDailyRollup).where(UsageDailyRollup.day == old.date())).one()
        assert rolled.credits == 3.0 and rolled.executions == 1
    finally:
        db.close()


def test_overview_reads_totals_series_and_breakdowns_from_the_rollups():
    db, _factory, user, agent = _db_session()
    try:
        _execution(db, agent, user, created_at=NOON, estimated=1.0, actual=0.8, tokens=(1000, 100, 400, 0), duration=20)
        _execution(db, agent, user, created_at=NOON - timedelta(days=2), model="claude-4.5", status="failed", estimated=0.5, tokens=(100, 0, 0, 0))
        _execution(db, agent, user, created_at=NOON - timedelta(days=40), estimated=100.0)
        db.commit()
        service = UsageRollupService(db)
        service.rollup_days([TODAY, TODAY - timedelta(days=2), TODAY - timedelta(days=40)])

        overview = service.overview(7, today=TODAY)
        assert overview["period_start"] == "2026-10-30" and overview["period_end"] == "2026-11-05"
        totals = overview["totals"]
        assert totals["executions"] == 2 and totals["failed"] == 1 and totals["credits"] == 1.3
        assert totals["failure_rate"] == 0.5 and totals["cache_hit_ratio"] == round(400 / 1100, 4)
        assert totals["members_active"] == 1
        assert len(overview["daily"]) == 7 and overview["daily"][-1]["day"] == "2026-11-05" and overview["daily"][-1]["credits"] == 0.8
        assert overview["daily"][0]["executions"] == 0, "days without runs are present as zeros"
        assert [row["model"] for row in overview["by_model"]] == ["gpt-6.1", "claude-4.5"]
        assert overview["by_kind"][0]["kind"] == "chat"
        assert overview["top_members"][0]["username"] == user.username and overview["top_members"][0]["credits"] == 1.3
        assert overview["top_assistants"][0]["name"] == agent.name

        month = service.overview(90, today=TODAY)
        assert month["totals"]["credits"] == 101.3 and len(month["daily"]) == 90

        daily_rows = service.daily_rows(7, today=TODAY)
        assert {row["day"] for row in daily_rows} == {"2026-11-05", "2026-11-03"}
        execution_rows = service.execution_rows(7, today=TODAY)
        assert len(execution_rows) == 2 and execution_rows[0]["credits"] == 0.8 and execution_rows[0]["duration_seconds"] == 20.0
    finally:
        db.close()


def test_refresh_recent_runs_once_per_window(monkeypatch):
    db, _factory, _user, _agent = _db_session()
    try:
        monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
        service = UsageRollupService(db)
        assert service.refresh_recent(today=TODAY) is True
        assert service.refresh_recent(today=TODAY) is False, "within the window nothing is recomputed"
        assert service.refresh_recent(max_age_seconds=0, today=TODAY) is True
    finally:
        db.close()


def test_worker_pass_rolls_up_and_prunes_with_the_settings(monkeypatch):
    db, factory, user, agent = _db_session()
    try:
        _execution(db, agent, user, created_at=datetime.utcnow() - timedelta(hours=1), estimated=0.3)
        _execution(db, agent, user, created_at=datetime.utcnow() - timedelta(days=500), estimated=5.0)
        db.commit()
        import app.services.usage_rollup_worker as worker_module

        monkeypatch.setattr(worker_module, "SessionLocal", factory)
        worker = UsageRollupWorker()
        worker.settings = SimpleNamespace(usage_rollup_window_days=3, execution_retention_days=365, usage_rollup_worker_interval_seconds=3600)
        result = worker.run_once()
        assert len(result["rolled_up"]) == 3 and result["deleted"] == 1
        assert db.execute(select(func.count()).select_from(AgentExecution)).scalar() == 1
        assert db.execute(select(func.coalesce(func.sum(UsageDailyRollup.credits), 0))).scalar() == 5.3
    finally:
        db.close()


def test_member_table_prefers_copilots_charge_over_the_estimate():
    db, _factory, user, agent = _db_session()
    try:
        db.add(UserAllowlistEntry(username=user.username, role="admin", is_active=True))
        _execution(db, agent, user, created_at=NOON, estimated=1.0, actual=0.8)
        _execution(db, agent, user, created_at=NOON, estimated=0.5)
        db.commit()
        row = MemberManagementService(db).build_overview(days=30, now=NOON + timedelta(hours=1))["users"][0]
        assert row["credits_period"] == 1.3
    finally:
        db.close()


def test_chat_finish_stores_the_charge_and_the_reason(monkeypatch):
    db, _factory, _user, agent = _db_session()
    try:
        repo = AgentExecutionRepository(db)
        execution = repo.create(agent_id=agent.id, session_id="s", kind="chat", status="running", source="portal")
        payload = {"response": "ok", "completion_state": "incomplete", "incomplete_reason": "budget_paused", "usage": {"input_tokens": 5, "estimated_credits": 0.2, "actual_credits": 0.1}}
        import json

        finish_chat_response_best_effort(db, execution_id=execution.id, status_code=200, content=json.dumps(payload).encode("utf-8"))
        db.refresh(execution)
        assert execution.status == "failed" and execution.terminal_reason == "budget_paused"
        assert execution.actual_credits == 0.1 and execution.credits_source == "copilot_usage"
    finally:
        db.close()


def test_quality_and_adoption_insights():
    db, _factory, user, agent = _db_session()
    try:
        _execution(db, agent, user, created_at=NOON, estimated=1.0, tokens=(1000, 100, 500, 0), duration=10)
        _execution(db, agent, user, created_at=NOON - timedelta(hours=1), status="failed", terminal_reason="budget_paused", tokens=(100, 0, 0, 0))
        _execution(db, agent, user, created_at=NOON - timedelta(hours=2), kind="async_task", duration=90)
        db.add_all([
            AgentTask(assignee_agent_id=agent.id, source="portal", task_type="agent_async_task", status="done", skill_name="code-review", owner_user_id=user.id, created_at=NOON, updated_at=NOON),
            AgentTask(assignee_agent_id=agent.id, source="delegation", trigger="timer", task_type="agent_async_task", status="failed", skill_name="code-review", owner_user_id=user.id, created_at=NOON, updated_at=NOON, input_payload_json='{"followup_task": "again"}'),
            AgentTask(assignee_agent_id=agent.id, source="portal", task_type="agent_async_task", status="done", skill_name="report", owner_user_id=user.id, created_at=NOON, updated_at=NOON, retry_count=1),
            DelegationRule(name="nightly", enabled=True, source_type="timer", trigger_type="timer", target_agent_id=agent.id, task_type="agent_async_task", owner_user_id=user.id),
            DelegationRule(name="reviews", enabled=False, source_type="github", trigger_type="github_pr_review", target_agent_id=agent.id, task_type="agent_async_task", owner_user_id=user.id),
        ])
        db.commit()
        insights = UsageInsightsService(db)

        quality = insights.quality(7, today=TODAY)
        assert quality["executions"] == 3 and quality["failed"] == 1 and quality["failure_rate"] == round(1 / 3, 4)
        assert quality["by_status"] == {"succeeded": 2, "failed": 1}
        assert quality["durations"]["chat"]["p50_seconds"] == 10.0 and quality["durations"]["async_task"]["p90_seconds"] == 90.0
        assert quality["cache_hit_ratio"] == round(500 / 1100, 4)
        assert quality["budget_pauses"] == 1
        assert quality["tasks"] == 3 and quality["tasks_reworked"] == 2 and quality["rework_rate"] == round(2 / 3, 4)

        adoption = insights.adoption(7, today=TODAY)
        assert adoption["skills"][0] == {"skill_name": "code-review", "tasks": 2, "done": 1, "done_rate": 0.5}
        sources = {row["source"]: row for row in adoption["delegation_sources"]}
        assert sources["timer"] == {"source": "timer", "rules": 1, "enabled": 1, "tasks_started": 1}
        assert sources["github_pr_review"]["enabled"] == 0
        assert adoption["members"] == 1 and adoption["members_with_a_chat"] == 1
        assert adoption["days_to_first_chat_p50"] is not None
        assert adoption["cohorts"][0]["joined"] == 1 and adoption["cohorts"][0]["chatted"] == 1
        assert [row["connector"] for row in adoption["connectors"]][:2] == ["llm", "jira"]
    finally:
        db.close()


def test_admin_usage_api_is_admin_only_and_exports_csv(monkeypatch):
    from app.main import app
    import app.api.admin as admin_api
    import app.deps as deps_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    current = SimpleNamespace(id=admin.id, username=admin.username, nickname=admin.username, role=admin.role)
    app.dependency_overrides[admin_api.get_db] = _override_db(db)
    app.dependency_overrides[deps_module.require_admin] = lambda: current
    app.dependency_overrides[deps_module.get_current_user] = lambda: current
    monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)
    client = TestClient(app)
    try:
        overview = client.get("/api/admin/usage/overview?days=7")
        assert overview.status_code == 200
        body = overview.json()
        assert body["period_days"] == 7 and len(body["daily"]) == 7
        for key in ("totals", "by_model", "by_kind", "by_runtime", "top_members", "top_assistants", "quality", "adoption"):
            assert key in body, key
        assert client.get("/api/admin/usage/overview?days=12").status_code == 422

        export = client.get("/api/admin/usage/export.csv?days=30")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert 'filename="efp-usage-daily-30d-' in export.headers["content-disposition"]
        text = export.text.lstrip(chr(0xFEFF))
        assert text.splitlines()[0].startswith("day,")
        executions = client.get("/api/admin/usage/export.csv?days=30&rows=executions")
        assert executions.status_code == 200 and executions.text.lstrip(chr(0xFEFF)).startswith("created_at,")
        assert client.get("/api/admin/usage/export.csv?rows=weekly").status_code == 422
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_export_rows_round_trip_through_csv():
    db, _factory, user, agent = _db_session()
    try:
        _execution(db, agent, user, created_at=NOON, estimated=1.25, tokens=(10, 2, 0, 0))
        db.commit()
        service = UsageRollupService(db)
        service.rollup_day(TODAY)
        rows = service.daily_rows(1, today=TODAY)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        parsed = list(csv.DictReader(io.StringIO(buffer.getvalue())))
        assert parsed[0]["username"] == user.username and parsed[0]["credits"] == "1.25" and parsed[0]["executions"] == "1"
    finally:
        db.close()
