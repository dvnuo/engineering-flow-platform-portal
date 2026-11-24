"""What an app was used for: its usage page, and its model calls in the Portal's own usage reporting.

``GET /api/apps/{id}/usage`` reads the gateway's counters, the SDK calls and
the host's last word; the daily rollups carry the model calls of apps to "My
usage", the administration's Usage panel and its CSV, for the app's owner.
"""

import csv
import io
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from _apps_helpers import add_app, add_user, make_database, owner_api_harness
from app.config import get_settings
from app.models import AppExecution, AppTrafficDaily, AppVisit, UsageDailyRollup
from app.services import usage_rollup_service
from app.services.apps import budget as app_budget
from app.services.apps.host_state import host_state
from app.services.member_management_service import MemberManagementService
from app.services.usage_rollup_service import UsageRollupService
from app.services.usage_rollup_worker import UsageRollupWorker
from tests.test_agent_execution_registry import _db_session
from tests.test_usage_rollups import _execution

TODAY = date(2026, 11, 15)
NOON = datetime(2026, 11, 15, 12, 0, 0)
ZERO_TRAFFIC = {"requests": 0, "visitors": 0, "errors_4xx": 0, "errors_5xx": 0, "p95_ms": 0}
ZERO_LLM = {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "credits": 0.0}


@pytest.fixture
def harness(monkeypatch):
    yield from owner_api_harness(monkeypatch)


def call(db, app, *, at=NOON, kind="llm", status="succeeded", model="gpt-6-sol", tokens=(0, 0), credits=None, connector=None,
         error=None, duration_ms=None, owner_id=None, visitor_id=None):
    row = AppExecution(
        app_id=app.id if hasattr(app, "id") else app,
        owner_user_id=owner_id if owner_id is not None else app.owner_user_id,
        visitor_user_id=visitor_id,
        kind=kind,
        status=status,
        model=model if kind == "llm" else None,
        connector_type=connector,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        credits=credits,
        error_code=error,
        duration_ms=duration_ms,
        created_at=at,
    )
    db.add(row)
    return row


def fill(h, app_id):
    """Three days of traffic, model calls, connector requests and visits around 15 November, plus rows outside any period asked for."""
    db = h.db
    app = h.row(app_id)
    app.connectors_enabled = ["jira", "splunk"]
    # A day's visitors are the distinct signed-in members of that day; requests count everyone.
    db.add(AppTrafficDaily(day=date(2026, 11, 15), app_id=app_id, requests=120, visitors=1, errors_4xx=3, errors_5xx=1, p95_ms=240))
    db.add(AppTrafficDaily(day=date(2026, 11, 14), app_id=app_id, requests=40, visitors=2, errors_4xx=0, errors_5xx=0, p95_ms=90))
    db.add(AppTrafficDaily(day=date(2026, 10, 1), app_id=app_id, requests=999, visitors=1, errors_4xx=9, errors_5xx=9, p95_ms=999))
    db.add(AppTrafficDaily(day=date(2026, 11, 15), app_id="another-app", requests=5000, visitors=50))

    call(db, app, tokens=(10, 20), credits=0.5)
    call(db, app, status="failed", error="provider_error")
    call(db, app, status="cancelled", error="client_closed", tokens=(7, 3), at=datetime(2026, 11, 15, 23, 59, 59))
    call(db, app, at=datetime(2026, 11, 14, 0, 0, 0), tokens=(100, 50))
    call(db, app, at=datetime(2026, 10, 1, 12, 0, 0), tokens=(9999, 9999), credits=99.0)
    call(db, "another-app", owner_id=app.owner_user_id, tokens=(5000, 5000), credits=50.0)
    for status in ("succeeded", "succeeded", "failed"):
        call(db, app, kind="connector", connector="jira", status=status, error="upstream_error" if status == "failed" else None)
    call(db, app, kind="connector", connector="github", at=datetime(2026, 11, 13, 8, 0, 0))
    call(db, app, kind="connector", connector="nexus", at=datetime(2026, 10, 1, 8, 0, 0))

    users = h.users
    # mate was there on the 14th and again on the 15th: one member of the period, one row.
    db.add(AppVisit(app_id=app_id, user_id=users["mate"].id, visits=40, last_visited_at=datetime(2026, 11, 15, 10, 0, 0)))
    db.add(AppVisit(app_id=app_id, user_id=users["other"].id, visits=90, last_visited_at=datetime(2026, 11, 14, 9, 30, 0)))
    db.add(AppVisit(app_id=app_id, user_id=users["gadmin"].id, visits=500, last_visited_at=datetime(2026, 10, 1, 9, 0, 0)))
    db.add(AppVisit(app_id=app_id, user_id=424242, visits=3, last_visited_at=datetime(2026, 11, 9, 0, 0, 0)))
    db.add(AppVisit(app_id="another-app", user_id=users["mate"].id, visits=7000, last_visited_at=NOON))
    db.commit()


# --- GET /api/apps/{id}/usage ------------------------------------------------------------------


def test_usage_is_for_the_owner_and_system_administrators(harness, monkeypatch):
    app_id = harness.create(visibility="public")["app"]["id"]

    assert harness.as_user("owner").get(f"/api/apps/{app_id}/usage").status_code == 200
    assert harness.as_user("admin").get(f"/api/apps/{app_id}/usage").status_code == 200
    # A member who may open the app may not read what it is used for.
    assert harness.as_user("mate").get(f"/api/apps/{app_id}/usage").status_code == 403
    assert harness.as_user("gadmin").get(f"/api/apps/{app_id}/usage").status_code == 403
    assert harness.as_user("other").get(f"/api/apps/{app_id}/usage").status_code == 404
    assert harness.as_user("owner").get("/api/apps/no-such-app/usage").status_code == 404

    for days in ("0", "91", "-1", "many"):
        assert harness.as_user("owner").get(f"/api/apps/{app_id}/usage?days={days}").status_code == 422, days

    monkeypatch.setattr(get_settings(), "apps_enabled", False)
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/usage").status_code == 404


def test_usage_of_an_app_nobody_used_is_all_zeros_for_thirty_days(harness, monkeypatch):
    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    app_id = harness.create()["app"]["id"]

    body = harness.as_user("owner").get(f"/api/apps/{app_id}/usage").json()

    assert list(body) == ["days", "traffic", "llm", "connectors", "visitors", "budget", "totals", "process"]
    assert body["days"] == 30
    days = [(TODAY - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
    assert body["traffic"] == [{"day": day, **ZERO_TRAFFIC} for day in days]
    assert body["llm"] == [{"day": day, **ZERO_LLM} for day in days]
    assert body["connectors"] == [] and body["visitors"] == []
    assert body["budget"] == {"daily_credits": 50.0, "used_credits": 0.0, "daily_tokens": 2000000, "used_tokens": 0}
    assert body["totals"] == {"requests": 0, "visitors": 0, "llm_calls": 0, "credits": 0.0, "connector_calls": 0}
    assert body["process"] is None


def test_usage_counts_traffic_calls_visitors_and_the_budget(harness, monkeypatch):
    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    app_id = harness.create()["app"]["id"]
    fill(harness, app_id)

    response = harness.as_user("owner").get(f"/api/apps/{app_id}/usage?days=7")

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7
    days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"]
    assert [item["day"] for item in body["traffic"]] == days and [item["day"] for item in body["llm"]] == days

    traffic = {item["day"]: item for item in body["traffic"]}
    assert traffic["2026-11-15"] == {"day": "2026-11-15", "requests": 120, "visitors": 1, "errors_4xx": 3, "errors_5xx": 1, "p95_ms": 240}
    assert traffic["2026-11-14"] == {"day": "2026-11-14", "requests": 40, "visitors": 2, "errors_4xx": 0, "errors_5xx": 0, "p95_ms": 90}
    assert all(traffic[day] == {"day": day, **ZERO_TRAFFIC} for day in days if day not in ("2026-11-15", "2026-11-14"))

    llm = {item["day"]: item for item in body["llm"]}
    # A cancelled stream is a call, not a failure; its estimated tokens count.
    assert llm["2026-11-15"] == {"day": "2026-11-15", "calls": 3, "failed": 1, "input_tokens": 17, "output_tokens": 23, "credits": 0.5}
    assert llm["2026-11-14"] == {"day": "2026-11-14", "calls": 1, "failed": 0, "input_tokens": 100, "output_tokens": 50, "credits": 0.0}
    assert all(llm[day] == {"day": day, **ZERO_LLM} for day in days[:5])

    # Most called first; a connector the app may call is listed before its first call.
    assert body["connectors"] == [
        {"type": "jira", "calls": 3, "failed": 1},
        {"type": "github", "calls": 1, "failed": 0},
        {"type": "splunk", "calls": 0, "failed": 0},
    ]
    assert body["visitors"] == [
        {"user": {"id": harness.users["other"].id, "username": "other", "display_name": "other"}, "requests": 90, "last_visited_at": "2026-11-14T09:30:00"},
        {"user": {"id": harness.users["mate"].id, "username": "mate", "display_name": "mate"}, "requests": 40, "last_visited_at": "2026-11-15T10:00:00"},
        # An account that is gone keeps its line.
        {"user": {"id": 424242, "username": "", "display_name": ""}, "requests": 3, "last_visited_at": "2026-11-09T00:00:00"},
    ]
    assert body["budget"] == {"daily_credits": 50.0, "used_credits": 0.5, "daily_tokens": 2000000, "used_tokens": 40}
    # Three distinct signed-in members in the week: not the days' counts added up (that would count mate twice).
    assert body["totals"] == {"requests": 160, "visitors": 3, "llm_calls": 4, "credits": 0.5, "connector_calls": 4}

    # A longer period takes the older rows in: thirty days do not reach 1 October, ninety do.
    month = harness.as_user("owner").get(f"/api/apps/{app_id}/usage").json()
    assert month["totals"] == body["totals"] and len(month["visitors"]) == 3
    quarter = harness.as_user("owner").get(f"/api/apps/{app_id}/usage?days=90").json()
    assert len(quarter["traffic"]) == 90 and quarter["traffic"][0]["day"] == "2026-08-18"
    assert quarter["totals"] == {"requests": 1159, "visitors": 4, "llm_calls": 5, "credits": 99.5, "connector_calls": 5}
    assert [item["user"]["username"] for item in quarter["visitors"]] == ["gadmin", "other", "mate", ""]
    assert quarter["connectors"][-2:] == [{"type": "nexus", "calls": 1, "failed": 0}, {"type": "splunk", "calls": 0, "failed": 0}]


def test_the_periods_visitors_are_distinct_signed_in_members(harness, monkeypatch):
    from app.services.apps.usage import members_in_period

    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    app_id = harness.create()["app"]["id"]
    db = harness.db
    # An app open to anyone: plenty of requests from visitors who are not signed in, two members among them.
    db.add(AppTrafficDaily(day=date(2026, 11, 15), app_id=app_id, requests=900, visitors=2))
    db.add(AppTrafficDaily(day=date(2026, 11, 14), app_id=app_id, requests=700, visitors=1))
    db.add(AppTrafficDaily(day=date(2026, 11, 13), app_id=app_id, requests=500, visitors=0))
    db.add(AppVisit(app_id=app_id, user_id=harness.users["mate"].id, visits=12, last_visited_at=NOON))
    db.add(AppVisit(app_id=app_id, user_id=harness.users["other"].id, visits=3, last_visited_at=NOON - timedelta(hours=2)))
    db.commit()

    week = harness.as_user("owner").get(f"/api/apps/{app_id}/usage?days=7").json()
    assert week["totals"]["requests"] == 2100
    assert week["totals"]["visitors"] == 2 and len(week["visitors"]) == 2

    # A report whose member list was cut short: the day's own count is the least it can be.
    db.add(AppTrafficDaily(day=date(2026, 11, 12), app_id=app_id, requests=50, visitors=5))
    db.commit()
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/usage?days=7").json()["totals"]["visitors"] == 5

    assert members_in_period(3, [1, 2, 0]) == 3
    assert members_in_period(0, []) == 0
    assert members_in_period(2, [5, None]) == 5


def test_usage_lists_at_most_fifty_members_the_busiest_first(harness, monkeypatch):
    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    app_id = harness.create()["app"]["id"]
    members = [add_user(harness.db, f"member{index:02d}") for index in range(60)]
    for index, member in enumerate(members):
        harness.db.add(AppVisit(app_id=app_id, user_id=member.id, visits=index + 1, last_visited_at=NOON - timedelta(hours=index)))
    harness.db.commit()

    body = harness.as_user("owner").get(f"/api/apps/{app_id}/usage").json()

    assert len(body["visitors"]) == 50
    assert [item["requests"] for item in body["visitors"]] == list(range(60, 10, -1))
    assert body["visitors"][0]["user"] == {"id": members[59].id, "username": "member59", "display_name": "member59"}
    assert body["totals"]["visitors"] == 60


def test_usage_shows_the_process_as_the_host_last_reported_it(harness, monkeypatch):
    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    app_id = harness.create()["app"]["id"]
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/usage").json()["process"] is None

    # The host knows the app now; what it says is read again once the last read is a few seconds old.
    harness.host.put_app(app_id, {"slug": "mind-map", "enabled": True})
    host_state.clear()
    process = harness.as_user("owner").get(f"/api/apps/{app_id}/usage").json()["process"]
    assert list(process) == ["state", "rss_bytes", "cpu_percent", "data_bytes", "restarts", "reported_at"]
    assert {key: process[key] for key in ("state", "rss_bytes", "cpu_percent", "data_bytes", "restarts")} == {
        "state": "undeployed", "rss_bytes": 1000, "cpu_percent": 0.5, "data_bytes": 1024, "restarts": 0,
    }
    assert process["reported_at"]

    # A host that does not answer: the last report stands.
    from app.services.apps.errors import AppHostUnavailable

    host_state._refreshed_at = None
    harness.host.fail["list_apps"] = AppHostUnavailable()
    again = harness.as_user("owner").get(f"/api/apps/{app_id}/usage")
    assert again.status_code == 200 and again.json()["process"]["rss_bytes"] == 1000

    # Figures the host sends in a shape of its own are left out rather than failing the page.
    host_state.record_status({"id": app_id, "state": "running", "rss_bytes": "big", "cpu_percent": float("nan"), "data_bytes": 2048.0, "restarts": True})
    odd = harness.as_user("owner").get(f"/api/apps/{app_id}/usage").json()["process"]
    assert (odd["state"], odd["rss_bytes"], odd["cpu_percent"], odd["data_bytes"], odd["restarts"]) == ("running", None, None, 2048, None)


# --- the daily rollups -----------------------------------------------------------------------------


def _rollups(db, day=TODAY, **filters):
    statement = select(UsageDailyRollup).where(UsageDailyRollup.day == day)
    for name, value in filters.items():
        statement = statement.where(getattr(UsageDailyRollup, name) == value)
    return list(db.scalars(statement.order_by(UsageDailyRollup.app_id, UsageDailyRollup.model)).all())


def _apps_database():
    factory, _engine = make_database()
    db = factory()
    owner = add_user(db, "owner")
    visitor = add_user(db, "visitor")
    first = add_app(db, owner, slug="mindmap", name="Mind map")
    second = add_app(db, owner, slug="planner", name="Planner")
    return db, owner, visitor, first, second


def test_an_apps_model_calls_roll_up_for_its_owner_as_app_llm():
    db, owner, visitor, first, second = _apps_database()
    try:
        call(db, first, tokens=(1000, 100), credits=0.8, duration_ms=2000, visitor_id=visitor.id)
        call(db, first, tokens=(500, 50), credits=None, duration_ms=1000)
        call(db, first, status="failed", error="provider_error", duration_ms=500)
        call(db, first, status="cancelled", error="client_closed", tokens=(7, 3))
        call(db, first, model="gpt-5.6-sol", tokens=(10, 10), credits=0.1)
        call(db, second, tokens=(20, 20), credits=0.2)
        # Not model calls, or not today's.
        call(db, first, kind="connector", connector="jira")
        call(db, first, at=NOON - timedelta(days=1), tokens=(9, 9), credits=9.0)
        db.commit()

        service = UsageRollupService(db)
        assert service.rollup_day(TODAY) == 3

        rows = _rollups(db)
        assert [(row.app_id, row.model) for row in rows] == sorted(
            [(first.id, "gpt-5.6-sol"), (first.id, "gpt-6-sol"), (second.id, "gpt-6-sol")]
        )
        for row in rows:
            # The owner's, whoever was using the app.
            assert (row.user_id, row.agent_id, row.runtime_type, row.kind) == (owner.id, "", "app", "app_llm")
        main = next(row for row in rows if row.app_id == first.id and row.model == "gpt-6-sol")
        assert (main.executions, main.succeeded, main.failed) == (4, 2, 1)
        assert (main.input_tokens, main.output_tokens, main.cached_input_tokens, main.reasoning_tokens) == (1507, 153, 0, 0)
        assert (main.estimated_credits, main.actual_credits, main.credits) == (0.0, 0.8, 0.8)
        assert main.duration_seconds == 3.5
        other = next(row for row in rows if row.app_id == second.id)
        assert (other.executions, other.credits, other.input_tokens) == (1, 0.2, 20)

        # A rerun replaces the day's rows instead of adding to them.
        assert service.rollup_day(TODAY) == 3 and len(_rollups(db)) == 3
        assert service.rollup_recent(2, today=TODAY) == {"2026-11-14": 1, "2026-11-15": 3}
        assert db.query(UsageDailyRollup).filter(UsageDailyRollup.user_id == visitor.id).count() == 0
    finally:
        db.close()


def test_image_calls_are_model_calls_everywhere_usage_is_read(harness, monkeypatch):
    # An image call is written as kind "llm" with connector_type "image_analysis" (docs/CONTRACTS.md, section 9.4).
    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    app_id = harness.create()["app"]["id"]
    db = harness.db
    app = harness.row(app_id)
    app.connectors_enabled = ["jira", "image_analysis"]
    call(db, app, tokens=(10, 20), credits=0.5)
    call(db, app, model="gpt-5.6-sol", connector="image_analysis", tokens=(1830, 210), duration_ms=4000)
    call(db, app, model="gpt-5.6-sol", connector="image_analysis", status="failed", error="provider_error")
    call(db, app, kind="connector", connector="jira")
    db.commit()

    body = harness.as_user("owner").get(f"/api/apps/{app_id}/usage?days=7").json()

    # Counted with the model calls: the day's figures, the budget and the totals.
    assert body["llm"][-1] == {"day": "2026-11-15", "calls": 3, "failed": 1, "input_tokens": 1840, "output_tokens": 230, "credits": 0.5}
    assert body["budget"]["used_tokens"] == 2070 and body["budget"]["used_credits"] == 0.5
    assert body["totals"]["llm_calls"] == 3 and body["totals"]["connector_calls"] == 1
    # And never among the connector calls: neither its rows, nor its switch being on.
    assert body["connectors"] == [{"type": "jira", "calls": 1, "failed": 0}]

    # The administration's list and the daily rollups read the same rows.
    from app.services.apps.usage import admin_metrics

    assert admin_metrics(db, [app_id], today=TODAY)[app_id]["llm_calls_7d"] == 3
    assert UsageRollupService(db).rollup_day(TODAY) == 2
    rows = {row.model: row for row in _rollups(db, app_id=app_id)}
    assert sorted(rows) == ["gpt-5.6-sol", "gpt-6-sol"]
    images = rows["gpt-5.6-sol"]
    assert (images.kind, images.runtime_type, images.user_id) == ("app_llm", "app", app.owner_user_id)
    assert (images.executions, images.succeeded, images.failed, images.input_tokens, images.output_tokens) == (2, 1, 1, 1830, 210)


def test_assistants_and_apps_roll_up_side_by_side_without_touching_each_other():
    db, _factory, user, agent = _db_session()
    try:
        app = add_app(db, user)
        _execution(db, agent, user, created_at=NOON, estimated=1.0, actual=0.8, tokens=(1000, 100, 400, 10), duration=20)
        call(db, app, tokens=(50, 5), credits=0.25)
        db.commit()
        service = UsageRollupService(db)
        assert service.rollup_day(TODAY) == 2

        chat = _rollups(db, app_id="")
        assert [(row.agent_id, row.kind, row.credits, row.runtime_type) for row in chat] == [(agent.id, "chat", 0.8, "native")]
        assert [(row.app_id, row.kind, row.credits) for row in _rollups(db, runtime_type="app")] == [(app.id, "app_llm", 0.25)]

        # The app's calls are pruned away: its rows stay, the assistant's are recomputed as ever.
        db.query(AppExecution).delete()
        _execution(db, agent, user, created_at=NOON + timedelta(hours=1), estimated=0.5, tokens=(500, 50, 0, 0))
        db.commit()
        assert service.rollup_day(TODAY) == 1
        assert [(row.executions, row.credits) for row in _rollups(db, app_id="")] == [(2, 1.3)]
        assert [(row.app_id, row.credits) for row in _rollups(db, runtime_type="app")] == [(app.id, 0.25)]

        # And the other way round: the assistant's runs are gone, the app has a new call.
        call(db, app, tokens=(1, 1), credits=0.5)
        db.commit()
        assert service.rollup_day(TODAY) == 2
        assert [(row.executions, row.credits) for row in _rollups(db, runtime_type="app")] == [(1, 0.5)]
        assert [(row.executions, row.credits) for row in _rollups(db, app_id="")] == [(2, 1.3)]
    finally:
        db.close()


def test_old_app_calls_are_pruned_after_their_days_are_rolled_up():
    db, _factory, user, agent = _db_session()
    try:
        app = add_app(db, user)
        long_ago = NOON - timedelta(days=400)
        call(db, app, at=long_ago, tokens=(100, 10), credits=1.5)
        call(db, app, at=long_ago + timedelta(days=2), status="failed", error="provider_error")
        call(db, app, at=long_ago, kind="connector", connector="jira")
        call(db, app, at=NOON - timedelta(days=10), tokens=(5, 5), credits=0.5)
        call(db, app, at=NOON - timedelta(days=365), tokens=(7, 7), credits=0.7)
        # An assistant's rollup of one of those days, with nothing left to rebuild it from.
        db.add(UsageDailyRollup(day=long_ago.date(), user_id=user.id, agent_id=agent.id, runtime_type="native", kind="chat", model="gpt", executions=9, credits=9.0))
        db.commit()
        service = UsageRollupService(db)

        assert service.prune_app_executions(0, today=TODAY) == {"rolled_up_days": 0, "deleted": 0}, "0 keeps everything"
        assert db.query(AppExecution).count() == 5

        result = service.prune_app_executions(365, today=TODAY)

        assert result == {"rolled_up_days": 35, "deleted": 3}
        # Exactly a year old is not older than a year.
        assert sorted(row.created_at for row in db.query(AppExecution)) == [NOON - timedelta(days=365), NOON - timedelta(days=10)]
        kept = _rollups(db, long_ago.date(), runtime_type="app")
        assert [(row.app_id, row.executions, row.credits, row.input_tokens) for row in kept] == [(app.id, 1, 1.5, 100)]
        failed = _rollups(db, (long_ago + timedelta(days=2)).date(), runtime_type="app")
        assert [(row.executions, row.failed) for row in failed] == [(1, 1)]
        # The assistant's row of that day was not rebuilt from nothing.
        assert [(row.executions, row.credits) for row in _rollups(db, long_ago.date(), app_id="")] == [(9, 9.0)]

        # Again: nothing left to do, and the sums are still there.
        assert service.prune_app_executions(365, today=TODAY) == {"rolled_up_days": 0, "deleted": 0}
        assert len(_rollups(db, long_ago.date(), runtime_type="app")) == 1
        # The assistants' pruning leaves the apps' rows alone too.
        assert service.prune_executions(365, today=TODAY) == {"rolled_up_days": 0, "deleted": 0}
        assert db.query(AppExecution).count() == 2
    finally:
        db.close()


def test_the_rollup_worker_prunes_app_calls_in_the_same_pass(monkeypatch):
    db, factory, user, _agent = _db_session()
    try:
        app = add_app(db, user)
        now = datetime.utcnow()
        call(db, app, at=now - timedelta(days=500), tokens=(1, 1), credits=1.0)
        call(db, app, at=now, tokens=(2, 2), credits=2.0)
        db.commit()
        monkeypatch.setattr("app.services.usage_rollup_worker.SessionLocal", factory)
        worker = UsageRollupWorker()

        result = worker.run_once()

        assert result["app_executions"] == {"rolled_up_days": result["app_executions"]["rolled_up_days"], "deleted": 1}
        assert result["app_executions"]["rolled_up_days"] >= 1
        assert db.query(AppExecution).count() == 1
        today_rows = _rollups(db, now.date(), runtime_type="app")
        assert [(row.app_id, row.credits) for row in today_rows] == [(app.id, 2.0)]
        assert [(row.credits) for row in _rollups(db, (now - timedelta(days=500)).date(), runtime_type="app")] == [1.0]
    finally:
        db.close()


# --- in the Portal's own usage reporting -------------------------------------------------------------


def _used_today(h, app_id, *, visitor="mate"):
    """Model calls of the app made just now, on behalf of another member, and one connector request."""
    now = datetime.utcnow()
    app = h.row(app_id)
    call(h.db, app, at=now, tokens=(1000, 100), credits=2.5, visitor_id=h.users[visitor].id, duration_ms=1500)
    call(h.db, app, at=now, tokens=(10, 1), credits=0.5, status="failed", error="provider_error")
    call(h.db, app, at=now, kind="connector", connector="jira")
    h.db.commit()


def _fresh(monkeypatch):
    monkeypatch.setattr(usage_rollup_service, "_recent_refreshed_at", 0.0)


def test_my_usage_shows_what_the_members_apps_spent(harness, monkeypatch):
    app_id = harness.create()["app"]["id"]
    _used_today(harness, app_id)
    _fresh(monkeypatch)

    mine = harness.as_user("owner").get("/api/users/me/usage?days=7")

    assert mine.status_code == 200
    body = mine.json()
    assert body["totals"]["executions"] == 2 and body["totals"]["failed"] == 1
    assert body["totals"]["credits"] == 3.0 and body["totals"]["input_tokens"] == 1010 and body["totals"]["output_tokens"] == 101
    assert body["daily"][-1]["credits"] == 3.0 and body["daily"][-1]["executions"] == 2
    assert body["by_kind"] == [
        {"kind": "app_llm", "executions": 2, "failed": 1, "failure_rate": 0.5, "credits": 3.0, "input_tokens": 1010, "output_tokens": 101}
    ]
    assert [row["model"] for row in body["by_model"]] == ["gpt-6-sol"]
    # Where an assistant is named, the app is, by its own name.
    assert body["by_agent"] == [{"agent_id": "", "app_id": app_id, "name": "Mind map", "executions": 2, "credits": 3.0}]
    assert body["notice"]["credits"] == 3.0

    # It is the owner's spend: the member who was using the app sees none of it.
    _fresh(monkeypatch)
    theirs = harness.as_user("mate").get("/api/users/me/usage?days=7").json()
    assert theirs["totals"]["executions"] == 0 and theirs["totals"]["credits"] == 0 and theirs["by_agent"] == []


def test_the_administrations_usage_panel_and_csv_show_app_calls_under_the_apps_name(harness, monkeypatch):
    app_id = harness.create()["app"]["id"]
    _used_today(harness, app_id)
    _fresh(monkeypatch)
    owner = harness.users["owner"]

    overview = harness.as_user("admin").get("/api/admin/usage/overview?days=7")

    assert overview.status_code == 200
    body = overview.json()
    assert body["totals"]["executions"] == 2 and body["totals"]["credits"] == 3.0 and body["totals"]["members_active"] == 1
    assert [(row["kind"], row["executions"], row["credits"]) for row in body["by_kind"]] == [("app_llm", 2, 3.0)]
    assert [(row["runtime_type"], row["executions"]) for row in body["by_runtime"]] == [("app", 2)]
    assert [(row["user_id"], row["username"], row["credits"]) for row in body["top_members"]] == [(owner.id, "owner", 3.0)]
    (top,) = body["top_assistants"]
    assert (top["agent_id"], top["app_id"], top["name"], top["agent_type"]) == ("", app_id, "Mind map", "app")
    assert (top["executions"], top["failed"], top["credits"], top["duration_seconds"]) == (2, 1, 3.0, 1.5)

    _fresh(monkeypatch)
    daily = harness.as_user("admin").get("/api/admin/usage/export.csv?days=7")
    assert daily.status_code == 200
    (row,) = list(csv.DictReader(io.StringIO(daily.text.lstrip(chr(0xFEFF)))))
    assert (row["username"], row["agent_id"], row["runtime_type"], row["kind"], row["model"]) == ("owner", "", "app", "app_llm", "gpt-6-sol")
    assert (row["app_id"], row["app"], row["executions"], row["failed"], row["credits"]) == (app_id, "Mind map", "2", "1", "3.0")
    assert (row["input_tokens"], row["output_tokens"], row["actual_credits"], row["estimated_credits"]) == ("1010", "101", "3.0", "0.0")

    runs = harness.as_user("admin").get("/api/admin/usage/export.csv?days=7&rows=executions")
    rows = list(csv.DictReader(io.StringIO(runs.text.lstrip(chr(0xFEFF)))))
    assert len(rows) == 2, "one line per model call; a connector request is not a run"
    assert {row["agent"] for row in rows} == {"Mind map"} and {row["app_id"] for row in rows} == {app_id}
    assert {(row["username"], row["runtime_type"], row["kind"]) for row in rows} == {("owner", "app", "app_llm")}
    by_status = {row["status"]: row for row in rows}
    assert by_status["succeeded"]["credits"] == "2.5" and by_status["succeeded"]["credits_source"] == "provider"
    assert by_status["succeeded"]["duration_seconds"] == "1.5" and by_status["succeeded"]["execution_id"].startswith("app-")
    assert by_status["failed"]["terminal_reason"] == "provider_error"

    # The member list counts the owner's credits and tokens the same way; a call of an app is not one of their runs.
    members = harness.as_user("admin").get("/api/users/admin-overview?days=30").json()
    listed = {item["username"]: item for item in members["users"]}
    assert (listed["owner"]["credits_period"], listed["owner"]["input_tokens_period"], listed["owner"]["output_tokens_period"]) == (3.0, 1010, 101)
    assert listed["owner"]["execution_count_period"] == 0 and listed["mate"]["credits_period"] == 0
    assert members["summary"]["credits_period_total"] == 3.0


def test_app_calls_stand_next_to_the_assistants_runs_in_the_same_lists():
    db, _factory, user, agent = _db_session()
    try:
        app = add_app(db, user)
        now = datetime.utcnow()
        _execution(db, agent, user, created_at=now, estimated=1.0, tokens=(100, 10, 0, 0), duration=4)
        call(db, app, at=now, tokens=(1000, 100), credits=2.5)
        db.commit()
        service = UsageRollupService(db)
        service.rollup_day(now.date())

        overview = service.overview(7)
        assert overview["totals"]["executions"] == 2 and overview["totals"]["credits"] == 3.5
        assert [(row["name"], row["agent_id"], row["app_id"], row["agent_type"]) for row in overview["top_assistants"]] == [
            ("Mind map", "", app.id, "app"),
            ("Registry Agent", agent.id, "", "workspace"),
        ]
        assert {row["kind"] for row in overview["by_kind"]} == {"chat", "app_llm"}

        usage = MemberManagementService(db).member_usage(user.id, days=7)
        assert usage["totals"]["executions"] == 2 and usage["totals"]["credits"] == 3.5
        assert usage["by_agent"] == [
            {"agent_id": "", "app_id": app.id, "name": "Mind map", "executions": 1, "credits": 2.5},
            {"agent_id": agent.id, "app_id": "", "name": "Registry Agent", "executions": 1, "credits": 1.0},
        ]

        runs = service.execution_rows(7)
        assert {(row["agent"], row["runtime_type"], row["kind"], row["app_id"]) for row in runs} == {
            ("Mind map", "app", "app_llm", app.id),
            ("Registry Agent", "native", "chat", ""),
        }
        assert len(runs) == 2
        assert all(set(row) == set(runs[0]) for row in runs), "one set of columns for the CSV"
        daily = service.daily_rows(7)
        assert {(row["runtime_type"], row["app"]) for row in daily} == {("native", ""), ("app", "Mind map")}

        # A deleted app keeps its name in the reports (its row stays for good).
        app.deleted_at = now
        db.commit()
        assert service.overview(7)["top_assistants"][0]["name"] == "Mind map"
    finally:
        db.close()
