"""Looking after every app: the administration's list, apps in the error log, and the data of deleted apps.

- ``GET /api/apps/admin/overview``: every app with its week and its size, and the host's own health.
- Failed deployments and crashes show in Administration > Errors without flooding it.
- A deleted app's data is removed from the host once it has been deleted long enough.
"""

import csv
import io
from datetime import date, datetime, timedelta
import threading

import pytest

from _apps_helpers import (
    HOST_TOKEN,
    MANIFEST,
    SHA_2,
    FakeHost,
    add_app,
    add_user,
    enable_apps,
    failed_job,
    make_database,
    owner_api_harness,
    reset_apps_state,
)
from app.config import get_settings
from app.models import App, AppAuditEvent, AppExecution, AppRelease, AppTrafficDaily, AppVisit, ErrorEvent
from app.services.apps import budget as app_budget
from app.services.apps import error_events
from app.services.apps.errors import AppHostError, AppHostUnavailable
from app.services.apps.host_state import host_state
from app.services.apps.purge_worker import AppsPurgeWorker
from app.services.error_log_service import ErrorLogService

NOON = datetime(2026, 11, 15, 12, 0, 0)
GITHUB_PAT = "ghp_" + "A1b2C3d4" * 5


@pytest.fixture
def harness(monkeypatch):
    yield from owner_api_harness(monkeypatch)


def errors(h, **filters):
    h.db.expire_all()
    query = h.db.query(ErrorEvent).order_by(ErrorEvent.id)
    for name, value in filters.items():
        query = query.filter(getattr(ErrorEvent, name) == value)
    return query.all()


def host_event(h, app_id, kind, detail=None, at="2026-11-15T12:00:00Z"):
    response = h.client.post(
        "/api/app-host/v1/events",
        json={"app_id": app_id, "kind": kind, "detail": detail, "at": at},
        headers={"Authorization": f"Bearer {HOST_TOKEN}"},
    )
    assert response.status_code == 200, response.text
    return response


# --- GET /api/apps/admin/overview --------------------------------------------------------------------


def test_the_overview_is_for_system_administrators(harness, monkeypatch):
    harness.create()

    assert harness.as_user("admin").get("/api/apps/admin/overview").status_code == 200
    for name in ("owner", "mate", "other", "gadmin"):
        response = harness.as_user(name).get("/api/apps/admin/overview")
        assert response.status_code == 403, name
        assert response.json()["code"] == "forbidden"

    monkeypatch.setattr(get_settings(), "apps_enabled", False)
    assert harness.as_user("admin").get("/api/apps/admin/overview").status_code == 404


def test_the_overview_lists_every_app_that_is_not_deleted_with_its_week_and_its_size(harness, monkeypatch):
    monkeypatch.setattr(app_budget, "utcnow", lambda: NOON)
    harness.source.manifest["owners"] = ["owner", "mate", "other"]
    mine = harness.create(name="Mind map", slug="mindmap", deploy=True)["app"]["id"]
    private = harness.create(name="Secret notes", slug="secret-notes", as_user="other")["app"]["id"]
    quiet = harness.create(name="Quiet", slug="quiet", as_user="mate")["app"]["id"]
    deleted = harness.create(name="Gone", slug="gone")["app"]["id"]
    assert harness.as_user("owner").delete(f"/api/apps/{deleted}").status_code == 200

    db = harness.db
    owner = harness.users["owner"]
    db.add(AppTrafficDaily(day=date(2026, 11, 15), app_id=mine, requests=120, visitors=7))
    db.add(AppTrafficDaily(day=date(2026, 11, 9), app_id=mine, requests=30, visitors=2))
    # Eight days ago: not this week's.
    db.add(AppTrafficDaily(day=date(2026, 11, 8), app_id=mine, requests=5000, visitors=500))
    db.add(AppTrafficDaily(day=date(2026, 11, 14), app_id=private, requests=4, visitors=1))
    for index in range(9):
        db.add(AppVisit(app_id=mine, user_id=1000 + index, visits=1, last_visited_at=NOON - timedelta(days=index)))
    db.add(AppExecution(app_id=mine, owner_user_id=owner.id, kind="llm", status="succeeded", credits=1.25, created_at=NOON))
    db.add(AppExecution(app_id=mine, owner_user_id=owner.id, kind="llm", status="failed", credits=None, created_at=datetime(2026, 11, 9, 0, 0, 0)))
    db.add(AppExecution(app_id=mine, owner_user_id=owner.id, kind="llm", status="succeeded", credits=50.0, created_at=datetime(2026, 11, 8, 23, 59, 59)))
    db.add(AppExecution(app_id=mine, owner_user_id=owner.id, kind="connector", status="succeeded", connector_type="jira", created_at=NOON))
    db.commit()
    host_state.clear()

    response = harness.as_user("admin").get("/api/apps/admin/overview")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["host", "apps"]
    apps = {item["id"]: item for item in body["apps"]}
    assert set(apps) == {mine, private, quiet}, "others' private apps are in it, deleted ones are not"

    first = apps[mine]
    assert list(first["metrics"]) == ["requests_7d", "visitors_7d", "llm_calls_7d", "credits_7d", "rss_bytes", "data_bytes"]
    # Seven members were seen within the week (two more before it), and the busiest day counted seven visitors.
    assert first["metrics"] == {"requests_7d": 150, "visitors_7d": 7, "llm_calls_7d": 2, "credits_7d": 1.25, "rss_bytes": 1000, "data_bytes": 1024}
    # The app's own card, with what an administrator may manage.
    assert (first["name"], first["slug"], first["owner"]["username"], first["state"], first["deployed"]) == ("Mind map", "mindmap", "owner", "running", True)
    assert first["can_manage"] is True and first["manage"]["repo_ref"] == "main" and first["template"] is None
    assert apps[private]["metrics"] == {"requests_7d": 4, "visitors_7d": 1, "llm_calls_7d": 0, "credits_7d": 0.0, "rss_bytes": None, "data_bytes": None}
    assert (apps[private]["owner"]["username"], apps[private]["visibility"], apps[private]["state"]) == ("other", "private", None)
    assert apps[quiet]["metrics"] == {"requests_7d": 0, "visitors_7d": 0, "llm_calls_7d": 0, "credits_7d": 0.0, "rss_bytes": None, "data_bytes": None}

    host = body["host"]
    assert list(host) == ["reachable", "info", "reported_at"]
    assert host["reachable"] is True and host["info"] == {"version": "0.1.0", "apps_total": 1} and host["reported_at"]
    # Neither a token nor a visitor key is in what the list carries.
    for app in harness.db.query(App):
        assert app.token_value not in response.text and app.visitor_key not in response.text


def test_the_hosts_own_figures_are_asked_for_again_once_they_are_a_minute_old(harness):
    harness.create(deploy=True)
    host_state.clear()
    calls = lambda: harness.host.names().count("host_info")  # noqa: E731

    first = harness.as_user("admin").get("/api/apps/admin/overview").json()["host"]
    assert first["info"]["version"] == "0.1.0" and calls() == 1
    # Fresh: not asked again.
    again = harness.as_user("admin").get("/api/apps/admin/overview").json()["host"]
    assert again == first and calls() == 1

    host_state._host_reported_at = datetime.utcnow() - timedelta(seconds=61)
    aged = harness.as_user("admin").get("/api/apps/admin/overview").json()["host"]
    assert calls() == 2 and aged["reported_at"] != first["reported_at"]

    # What the host itself posted a moment ago is as good as an answer.
    host_state.record_host({"version": "0.2.0", "apps_total": 9, "apps_running": 4})
    pushed = harness.as_user("admin").get("/api/apps/admin/overview").json()["host"]
    assert pushed["info"] == {"version": "0.2.0", "apps_total": 9, "apps_running": 4} and calls() == 2


def test_a_host_that_does_not_answer_leaves_the_list_and_the_last_report(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    host_state.clear()
    assert harness.as_user("admin").get("/api/apps/admin/overview").json()["host"]["reachable"] is True

    harness.host.fail["list_apps"] = AppHostUnavailable()
    harness.host.fail["host_info"] = AppHostUnavailable()
    host_state._refreshed_at = None
    host_state._host_reported_at = datetime.utcnow() - timedelta(hours=1)

    response = harness.as_user("admin").get("/api/apps/admin/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["host"]["reachable"] is False
    assert body["host"]["info"] == {"version": "0.1.0", "apps_total": 1}, "the last report, with the time it was made"
    (app,) = body["apps"]
    assert app["id"] == app_id and app["state"] == "running" and app["metrics"]["rss_bytes"] == 1000

    # It lists its apps but will not say how it is: not reachable either, and the page still renders.
    del harness.host.fail["list_apps"]
    harness.host.fail["host_info"] = AppHostError(reason="boom")
    host_state._failed_at = None
    host_state._refreshed_at = None
    assert harness.as_user("admin").get("/api/apps/admin/overview").json()["host"]["reachable"] is False

    # A Portal that never heard from the host has nothing to show for it.
    host_state.clear()
    harness.host.fail["list_apps"] = AppHostUnavailable()
    never = harness.as_user("admin").get("/api/apps/admin/overview").json()
    assert never["host"] == {"reachable": False, "info": None, "reported_at": None}
    assert never["apps"][0]["state"] is None and never["apps"][0]["metrics"]["rss_bytes"] is None


# --- Administration > Errors: failed deployments -------------------------------------------------------


def test_a_failed_deployment_is_one_entry_in_the_error_log(harness):
    app_id = harness.create()["app"]["id"]
    harness.host.job_states = [failed_job(message=f"No matching distribution found for nope==1.0 (token {GITHUB_PAT})")]

    release = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()

    assert release["status"] == "failed"
    (event,) = errors(harness)
    assert (event.source, event.severity, event.code, event.kind, event.runtime_type) == ("app", "error", "deploy_failed", "deploy", "app")
    assert (event.user_id, event.execution_id, event.count) == (harness.users["owner"].id, release["id"], 1)
    assert event.agent_id is None and event.agent_name is None
    assert event.message.startswith('App "Mind map" (mind-map): deployment failed: The deployment failed at the step dependencies')
    assert "No matching distribution found for nope" in event.message
    assert f"app_id: {app_id}" in event.detail and f"release_id: {release['id']}" in event.detail
    assert "requested_by: owner" in event.detail and "ERROR: No matching distribution" in event.detail
    # What the host's log happened to print is not kept as it came.
    assert GITHUB_PAT not in event.message and GITHUB_PAT not in event.detail

    # A deployment that works adds nothing.
    harness.host.job_states = None
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()["status"] == "succeeded"
    assert len(errors(harness)) == 1


def test_every_failed_deployment_is_its_own_entry_whatever_step_failed(harness):
    app_id = harness.create()["app"]["id"]
    owner = harness.as_user("owner")

    # In the Portal's own steps: the branch is gone, the manifest is broken, the host is away.
    harness.source.refs.pop("main")
    assert owner.post(f"/api/apps/{app_id}/deploy").json()["status"] == "failed"
    harness.source.refs["main"] = SHA_2
    harness.source.manifest = {"name": "Mind map", "type": "teapot", "owners": ["owner"]}
    assert owner.post(f"/api/apps/{app_id}/deploy").json()["status"] == "failed"
    harness.source.manifest = dict(MANIFEST)
    harness.host.fail["put_app"] = AppHostUnavailable()
    assert owner.post(f"/api/apps/{app_id}/deploy").json()["status"] == "failed"
    del harness.host.fail["put_app"]
    # On the host, twice the same way: two deployments, two entries.
    harness.host.job_states = [failed_job("health", "the app did not answer on its port")]
    assert owner.post(f"/api/apps/{app_id}/deploy").json()["status"] == "failed"
    assert owner.post(f"/api/apps/{app_id}/deploy").json()["status"] == "failed"

    events = errors(harness)
    assert [event.code for event in events] == ["ref_not_found", "manifest_invalid", "host_unreachable", "deploy_failed", "deploy_failed"]
    assert all(event.source == "app" and event.kind == "deploy" and event.count == 1 for event in events)
    failed = [release.id for release in harness.db.query(AppRelease).order_by(AppRelease.started_at, AppRelease.id) if release.status == "failed"]
    assert sorted(event.execution_id for event in events) == sorted(failed) and len(set(failed)) == 5
    # The same problem twice is one group on the page, seen twice.
    assert events[3].fingerprint == events[4].fingerprint != events[0].fingerprint


def test_a_failed_rollback_is_an_entry_too(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    harness.source.refs["main"] = SHA_2
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()["status"] == "succeeded"
    harness.host.fail["activate"] = AppHostError(code="host_release_not_found", reason="release_not_found", host_code="release_not_found")

    assert harness.as_user("owner").post(f"/api/apps/{app_id}/rollback").status_code == 502

    (event,) = errors(harness)
    assert (event.source, event.kind, event.code) == ("app", "rollback", "host_release_not_found")
    assert "rollback failed" in event.message


def test_app_errors_show_in_the_administrations_errors_page(harness):
    app_id = harness.create()["app"]["id"]
    harness.host.job_states = [failed_job()]
    harness.as_user("owner").post(f"/api/apps/{app_id}/deploy")
    host_event(harness, app_id, "crashed", {"exit_code": 1, "log": ["Traceback", "boom"]})

    overview = ErrorLogService(harness.db).overview(7)
    assert {item["source"]: item["events"] for item in overview["by_source"]}["app"] == 2
    only = ErrorLogService(harness.db).overview(7, source="app")
    assert only["totals"]["events"] == 2 and only["totals"]["members"] == 1 and only["totals"]["assistants"] == 0
    assert sorted(row["code"] for row in only["recent"]) == ["app_crashed", "deploy_failed"]
    assert {(row["source"], row["member"], row["agent_name"], row["runtime_type"]) for row in only["recent"]} == {("app", "Olive", "", "app")}
    assert len(only["groups"]) == 2

    export = harness.as_user("admin").get("/api/admin/errors/export.csv?days=7&source=app")
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text.lstrip(chr(0xFEFF)))))
    assert sorted(row["code"] for row in rows) == ["app_crashed", "deploy_failed"]
    assert {row["source"] for row in rows} == {"app"} and {row["username"] for row in rows} == {"owner"}
    assert {row["runtime_type"] for row in rows} == {"app"}


# --- Administration > Errors: crashes, thinned out ----------------------------------------------------


def test_a_crashing_app_is_one_entry_in_ten_minutes(harness, monkeypatch):
    clock = {"now": 5000.0}
    monkeypatch.setattr(error_events, "_clock", lambda: clock["now"])
    harness.source.manifest["owners"] = ["owner", "mate"]
    app_id = harness.create()["app"]["id"]
    other_id = harness.create(name="Planner", slug="planner", as_user="mate")["app"]["id"]

    host_event(harness, app_id, "crashed", {"exit_code": 1, "log": ["Traceback (most recent call last):", f"KeyError: 'x' token={GITHUB_PAT}"]})

    (event,) = errors(harness)
    assert (event.source, event.code, event.kind, event.runtime_type, event.severity) == ("app", "app_crashed", "crashed", "app", "error")
    assert (event.user_id, event.count) == (harness.users["owner"].id, 1)
    assert event.message == 'App "Mind map" (mind-map) crashed (exit code 1).'
    assert f"app_id: {app_id}" in event.detail and "KeyError: 'x'" in event.detail and "reported_at: 2026-11-15T12:00:00Z" in event.detail
    assert GITHUB_PAT not in event.detail

    # It restarts and crashes again, and is killed for its memory: counted on the entry that is there.
    for seconds, kind, detail in ((30, "crashed", {"exit_code": 1}), (200, "crashed", {"exit_code": 137}), (599, "killed", {"reason": "memory"})):
        clock["now"] = 5000.0 + seconds
        host_event(harness, app_id, kind, detail)
    (event,) = errors(harness)
    assert event.count == 4 and event.last_seen_at >= event.occurred_at
    assert event.message == 'App "Mind map" (mind-map) crashed (exit code 1).'

    # Another app is another entry.
    host_event(harness, other_id, "killed", {"reason": "memory"})
    assert [(item.code, item.message, item.user_id) for item in errors(harness, code="app_killed")] == [
        ("app_killed", 'App "Planner" (planner) was killed by the app host (memory).', harness.users["mate"].id)
    ]

    # Ten minutes after its entry the first app gets a new one.
    clock["now"] = 5000.0 + 600
    host_event(harness, app_id, "crashed")
    mine = errors(harness, user_id=harness.users["owner"].id)
    assert [(item.count, item.message) for item in mine] == [
        (4, 'App "Mind map" (mind-map) crashed (exit code 1).'),
        (1, 'App "Mind map" (mind-map) crashed.'),
    ]
    clock["now"] += 599
    host_event(harness, app_id, "crashed")
    assert [item.count for item in errors(harness, user_id=harness.users["owner"].id)] == [4, 2]

    # Every one of them is still in the app's own history.
    crashes = harness.db.query(AppAuditEvent).filter(AppAuditEvent.app_id == app_id, AppAuditEvent.action.in_(["host.crashed", "host.killed"])).count()
    assert crashes == 6


def test_what_the_host_reports_that_is_no_failure_is_not_an_error(harness):
    app_id = harness.create()["app"]["id"]

    for kind in ("idle_stopped", "started", "quota_exceeded", "stopped"):
        host_event(harness, app_id, kind, {"note": "x"})

    assert errors(harness) == []
    assert harness.db.query(AppAuditEvent).filter(AppAuditEvent.app_id == app_id, AppAuditEvent.action.like("host.%")).count() == 4


def test_a_crash_of_a_deleted_app_and_a_thinned_out_entry_that_was_pruned_do_not_fail_the_host(harness, monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(error_events, "_clock", lambda: clock["now"])
    app_id = harness.create()["app"]["id"]
    host_event(harness, app_id, "crashed", {"exit_code": 2})
    # The entry is pruned from the error log before the next crash.
    harness.db.query(ErrorEvent).delete()
    harness.db.commit()
    clock["now"] += 10
    host_event(harness, app_id, "crashed", {"exit_code": 2})
    assert errors(harness) == []

    harness.as_user("owner").delete(f"/api/apps/{app_id}")
    clock["now"] += 1000
    host_event(harness, app_id, "killed", "not an object")
    (event,) = errors(harness)
    assert event.message == 'App "Mind map" (mind-map) was killed by the app host.'


def test_the_error_log_never_fails_a_deployment_or_a_host_event(harness, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("the error log is down")

    monkeypatch.setattr(error_events, "record_error", broken)
    app_id = harness.create()["app"]["id"]
    harness.host.job_states = [failed_job()]

    assert harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()["status"] == "failed"
    host_event(harness, app_id, "crashed", {"exit_code": 1})
    # Nothing was written, so the next crash is not counted on an entry that is not there.
    monkeypatch.undo()
    enable_apps(monkeypatch)
    host_event(harness, app_id, "crashed", {"exit_code": 1})


# --- the data of deleted apps -------------------------------------------------------------------------


@pytest.fixture
def deleted(monkeypatch):
    enable_apps(monkeypatch, apps_deleted_retention_days=30)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    owner = add_user(db, "owner")
    host = FakeHost()
    worker = AppsPurgeWorker(factory, host_factory=lambda: host)

    def app(slug, *, days_deleted=None, purged=False, **fields):
        row = add_app(db, owner, slug=slug, name=slug.title(), **fields)
        if days_deleted is not None:
            row.deleted_at = NOON - timedelta(days=days_deleted)
            row.desired_state = "stopped"
            if purged:
                row.data_purged_at = row.deleted_at
            db.commit()
        return row.id

    def purged_at(app_id):
        db.expire_all()
        return db.get(App, app_id).data_purged_at

    try:
        yield type("Deleted", (), {"db": db, "host": host, "worker": worker, "app": staticmethod(app), "purged_at": staticmethod(purged_at), "factory": factory})
    finally:
        db.close()
        reset_apps_state()


def test_the_data_of_an_app_deleted_long_enough_ago_is_removed_and_stamped(deleted):
    old = deleted.app("old", days_deleted=31)
    exactly = deleted.app("exactly", days_deleted=30)
    recent = deleted.app("recent", days_deleted=29)
    already = deleted.app("already", days_deleted=90, purged=True)
    alive = deleted.app("alive")

    assert deleted.worker.run_once(now=NOON) == {"due": 2, "purged": 2, "failed": 0}

    # The host is asked to remove the app with its data, by id, oldest first.
    assert deleted.host.deleted == [(old, True), (exactly, True)]
    assert deleted.purged_at(old) == NOON and deleted.purged_at(exactly) == NOON
    assert deleted.purged_at(recent) is None and deleted.purged_at(alive) is None
    assert deleted.purged_at(already) == NOON - timedelta(days=90)
    events = deleted.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.data_purged").all()
    assert sorted(event.app_id for event in events) == sorted([old, exactly])
    assert all(event.actor_user_id is None and event.detail == {"deleted_days": 30} for event in events)
    # The row stays: the address is never given to another app.
    assert deleted.db.get(App, old).slug == "old" and deleted.db.get(App, old).deleted_at is not None

    # Nothing left to do; a day later the next one is due.
    assert deleted.worker.run_once(now=NOON) == {"due": 0, "purged": 0, "failed": 0}
    assert deleted.worker.run_once(now=NOON + timedelta(days=1)) == {"due": 1, "purged": 1, "failed": 0}
    assert deleted.host.deleted[-1] == (recent, True) and len(deleted.host.deleted) == 3


def test_a_host_that_cannot_be_reached_is_asked_again_on_the_next_round(deleted):
    first = deleted.app("first", days_deleted=40)
    second = deleted.app("second", days_deleted=35)
    deleted.host.fail["delete_app"] = AppHostUnavailable()

    assert deleted.worker.run_once(now=NOON) == {"due": 2, "purged": 0, "failed": 2}
    assert deleted.purged_at(first) is None and deleted.purged_at(second) is None
    assert deleted.db.query(AppAuditEvent).count() == 0

    # It refuses one of them (still busy there, say): the other is done, that one waits.
    def delete_app(app_id, *, purge_data=False):
        if app_id == first:
            raise AppHostError(code="host_app_busy", reason="app_busy", host_code="app_busy", host_status=409)
        deleted.host.deleted.append((app_id, purge_data))
        return {"ok": True}

    del deleted.host.fail["delete_app"]
    deleted.host.delete_app = delete_app
    assert deleted.worker.run_once(now=NOON) == {"due": 2, "purged": 1, "failed": 1}
    assert deleted.purged_at(first) is None and deleted.purged_at(second) == NOON

    del deleted.host.delete_app
    assert deleted.worker.run_once(now=NOON + timedelta(hours=1)) == {"due": 1, "purged": 1, "failed": 0}
    assert deleted.purged_at(first) == NOON + timedelta(hours=1)


def test_a_host_that_no_longer_knows_the_app_has_nothing_left_to_remove(deleted):
    app_id = deleted.app("forgotten", days_deleted=31)
    deleted.host.fail["delete_app"] = AppHostError(code="host_app_not_found", reason="app_not_found", host_code="app_not_found", host_status=404)

    assert deleted.worker.run_once(now=NOON) == {"due": 1, "purged": 1, "failed": 0}
    assert deleted.purged_at(app_id) == NOON


def test_an_unexpected_failure_on_one_app_does_not_stop_the_others(deleted):
    first = deleted.app("first", days_deleted=40)
    second = deleted.app("second", days_deleted=35)

    def delete_app(app_id, *, purge_data=False):
        if app_id == first:
            raise RuntimeError("boom")
        deleted.host.deleted.append((app_id, purge_data))
        return {"ok": True}

    deleted.host.delete_app = delete_app

    assert deleted.worker.run_once(now=NOON) == {"due": 2, "purged": 1, "failed": 1}
    assert deleted.purged_at(first) is None and deleted.purged_at(second) == NOON


@pytest.mark.parametrize("setting, value", [("apps_deleted_retention_days", 0), ("apps_deleted_retention_days", -5), ("apps_enabled", False)])
def test_nothing_is_removed_when_the_retention_is_zero_or_apps_are_off(deleted, monkeypatch, setting, value):
    app_id = deleted.app("old", days_deleted=4000)
    monkeypatch.setattr(get_settings(), setting, value)

    assert deleted.worker.run_once(now=NOON) == {"due": 0, "purged": 0, "failed": 0}
    assert deleted.host.calls == [] and deleted.purged_at(app_id) is None


def test_a_shorter_retention_takes_effect_on_the_next_round(deleted, monkeypatch):
    app_id = deleted.app("week-old", days_deleted=8)
    assert deleted.worker.run_once(now=NOON)["due"] == 0

    monkeypatch.setattr(get_settings(), "apps_deleted_retention_days", 7)
    assert deleted.worker.run_once(now=NOON) == {"due": 1, "purged": 1, "failed": 0}
    assert deleted.purged_at(app_id) == NOON
    assert deleted.db.query(AppAuditEvent).one().detail == {"deleted_days": 7}


def test_deleting_with_its_data_is_stamped_at_once_and_without_it_left_for_the_worker(harness):
    kept = harness.create(name="Kept", slug="kept", deploy=True)["app"]["id"]
    wiped = harness.create(name="Wiped", slug="wiped", deploy=True)["app"]["id"]
    never_deployed = harness.create(name="Draft", slug="draft")["app"]["id"]

    assert harness.as_user("owner").delete(f"/api/apps/{kept}").status_code == 200
    assert harness.as_user("owner").delete(f"/api/apps/{wiped}?purge_data=true").status_code == 200
    assert harness.as_user("owner").delete(f"/api/apps/{never_deployed}").status_code == 200

    assert harness.row(kept).data_purged_at is None and harness.row(never_deployed).data_purged_at is None
    assert harness.row(wiped).data_purged_at == harness.row(wiped).deleted_at
    assert harness.host.deleted == [(kept, False), (wiped, True)]

    # Thirty days on, the worker takes what was kept; what was wiped at deletion is not asked for again.
    worker = AppsPurgeWorker(harness.factory, host_factory=lambda: harness.host)
    later = datetime.utcnow() + timedelta(days=30, minutes=1)
    assert worker.run_once(now=later) == {"due": 2, "purged": 2, "failed": 0}
    assert sorted(harness.host.deleted[2:]) == sorted([(kept, True), (never_deployed, True)])
    assert harness.row(kept).data_purged_at == later and harness.row(wiped).data_purged_at != later


def test_the_worker_thread_starts_once_and_stops_when_asked(deleted):
    ran = threading.Event()
    worker = AppsPurgeWorker(deleted.factory, host_factory=lambda: deleted.host, initial_delay_seconds=0, interval_seconds=3600)
    original = worker.run_once
    worker.run_once = lambda **kwargs: (ran.set(), original(**kwargs))[1]

    worker.start()
    first = worker._thread
    worker.start()
    assert worker._thread is first, "a second start does not start a second thread"
    assert ran.wait(timeout=10), "the first round runs right after the initial delay"
    worker.stop()
    assert not first.is_alive()

    # With a delay it sleeps until it is stopped, and never runs.
    idle = AppsPurgeWorker(deleted.factory, host_factory=lambda: deleted.host, initial_delay_seconds=3600)
    idle.run_once = lambda **kwargs: pytest.fail("the worker ran before its delay was over")
    idle.start()
    idle.stop()
    assert not idle._thread.is_alive()


def test_the_worker_is_stopped_with_the_portal(monkeypatch):
    from app.services.apps.member_hooks import apps_shutdown
    from app.services.apps.purge_worker import apps_purge_worker_singleton

    stopped = []
    monkeypatch.setattr(apps_purge_worker_singleton, "stop", lambda: stopped.append(True))

    apps_shutdown()

    assert stopped == [True]
    # A stop that fails does not fail the shutdown.
    monkeypatch.setattr(apps_purge_worker_singleton, "stop", lambda: 1 / 0)
    apps_shutdown()
