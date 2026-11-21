"""Where the Portal and the real app host did not agree, found by running one against the other.

- An app the host has not heard of (registered, never deployed): its logs are
  empty and its data cannot be cleared, instead of a 502.
- Calls in which the host stops a process or removes files get the time that takes.
- A day's traffic counters never go down when the host's counters start again.
- Deleting an app tells the host even when nobody deployed it.
- A failed step of the host carries the host's reason.

The manifest rules, which must be the host's, have their table in
tests/test_apps_manifest.py.
"""

from datetime import date

import httpx
import pytest

from _apps_helpers import HOST_TOKEN, failed_job, host_steps, owner_api_harness
from app.models import AppAuditEvent, AppRelease, AppTrafficDaily
from app.repositories.app_repo import AppTrafficRepository
from app.services.apps.errors import AppHostError, AppHostUnavailable
from app.services.apps.host_client import DEFAULT_TIMEOUT_SECONDS, SLOW_TIMEOUT_SECONDS, UPLOAD_TIMEOUT_SECONDS, AppHostClient

STATUS = {"id": "app-1", "slug": "mind-map", "type": "fastapi", "state": "stopped", "reason": "manual", "data_bytes": 0}


@pytest.fixture
def harness(monkeypatch):
    yield from owner_api_harness(monkeypatch)


def real_client(handler) -> AppHostClient:
    """The Portal's own host client, answered by ``handler`` instead of a host."""
    transport = httpx.MockTransport(handler)
    return AppHostClient(base_url="http://host.test", token="host-token", transport=transport, async_transport=transport)


def unknown_to_the_host(seen):
    """A host that answers every call about an app the way the real one does for an id it does not know."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(404, json={"error": {"code": "app_not_found", "message": "no app with this id"}})

    return real_client(handler)


# --- 1. an app the host has not heard of --------------------------------------------------------------


def test_logs_of_an_app_the_host_has_not_heard_of_are_empty(harness, monkeypatch):
    app_id = harness.create()["app"]["id"]
    seen = []
    monkeypatch.setattr(harness.api, "host_client", lambda: unknown_to_the_host(seen))

    logs = harness.as_user("owner").get(f"/api/apps/{app_id}/logs")
    assert logs.status_code == 200 and logs.json() == {"lines": [], "truncated": False}

    stream = harness.as_user("owner").get(f"/api/apps/{app_id}/logs/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.content == b"", "nothing to follow yet: the stream ends at once"
    assert seen == [("GET", f"/v1/apps/{app_id}/logs"), ("GET", f"/v1/apps/{app_id}/logs/stream")]

    # Still not for those who may not manage the app.
    assert harness.as_user("mate").get(f"/api/apps/{app_id}/logs").status_code == 404
    assert harness.as_user("mate").get(f"/api/apps/{app_id}/logs/stream").status_code == 404


def test_the_data_of_an_app_that_was_never_deployed_cannot_be_cleared(harness, monkeypatch):
    app_id = harness.create()["app"]["id"]
    seen = []
    monkeypatch.setattr(harness.api, "host_client", lambda: unknown_to_the_host(seen))

    response = harness.as_user("owner").delete(f"/api/apps/{app_id}/data")

    assert response.status_code == 409
    assert response.json()["code"] == "not_deployed"
    assert seen == [], "the Portal knows it without asking"
    assert "app.data_cleared" not in harness.actions(app_id)
    # Reading how much it keeps answers as before: nothing known.
    usage = harness.as_user("owner").get(f"/api/apps/{app_id}/data")
    assert usage.status_code == 200 and usage.json()["data_bytes"] is None


def test_an_app_the_host_lost_reads_the_same_way(harness, monkeypatch):
    # Deployed as far as the Portal knows; the host has no record of it (a new volume, say).
    app_id = harness.create(deploy=True)["app"]["id"]
    seen = []
    monkeypatch.setattr(harness.api, "host_client", lambda: unknown_to_the_host(seen))

    assert harness.as_user("owner").get(f"/api/apps/{app_id}/logs").json() == {"lines": [], "truncated": False}
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/logs/stream").content == b""
    cleared = harness.as_user("owner").delete(f"/api/apps/{app_id}/data")
    assert cleared.status_code == 409 and cleared.json()["code"] == "not_deployed"
    assert ("DELETE", f"/v1/apps/{app_id}/data") in seen
    assert "app.data_cleared" not in harness.actions(app_id)


def test_any_other_answer_of_the_host_is_still_told_as_it_is(harness, monkeypatch):
    app_id = harness.create(deploy=True)["app"]["id"]

    def broken(request):
        return httpx.Response(500, json={"error": {"code": "internal", "message": "boom"}})

    monkeypatch.setattr(harness.api, "host_client", lambda: real_client(broken))
    for method, path in (("GET", "/logs"), ("GET", "/logs/stream"), ("DELETE", "/data")):
        response = harness.client.request(method, f"/api/apps/{app_id}{path}")
        assert response.status_code == 502 and response.json()["code"] == "host_internal", path

    def refused(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(harness.api, "host_client", lambda: real_client(refused))
    for method, path in (("GET", "/logs"), ("GET", "/logs/stream"), ("DELETE", "/data")):
        response = harness.client.request(method, f"/api/apps/{app_id}{path}")
        assert response.status_code == 503 and response.json()["code"] == "host_unreachable", path


# --- 3. calls that take the host a while ----------------------------------------------------------------


def _timeouts_seen():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[(request.method, request.url.path)] = request.extensions["timeout"]
        if request.url.path.endswith("/releases"):
            return httpx.Response(202, json={"job_id": "job-1"})
        if request.url.path == "/v1/apps":
            return httpx.Response(200, json={"apps": []})
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"lines": [], "truncated": False})
        return httpx.Response(200, json=dict(STATUS))

    return seen, real_client(handler)


def test_calls_in_which_the_host_stops_a_process_or_removes_files_wait_ninety_seconds():
    import io

    seen, client = _timeouts_seen()

    client.put_app("app-1", {"slug": "mind-map"})
    client.stop("app-1")
    client.restart("app-1")
    client.put_env("app-1", {"A": "1"})
    client.activate("app-1", "1" * 40)
    client.delete_data("app-1")
    client.delete_app("app-1", purge_data=True)
    slow = dict(seen)
    seen.clear()
    client.start("app-1")
    client.get_app("app-1")
    client.list_apps()
    client.logs("app-1")
    client.host_info()
    client.get_job("job-1")
    client.revoke_sessions(7)
    quick = dict(seen)
    seen.clear()
    client.deploy("app-1", {"sha": "1" * 40}, io.BytesIO(b"bundle"))

    assert SLOW_TIMEOUT_SECONDS == 90.0 and DEFAULT_TIMEOUT_SECONDS == 15.0
    assert sorted(slow) == sorted(
        [
            ("PUT", "/v1/apps/app-1"),
            ("POST", "/v1/apps/app-1/stop"),
            ("POST", "/v1/apps/app-1/restart"),
            ("PUT", "/v1/apps/app-1/env"),
            ("POST", "/v1/apps/app-1/activate"),
            ("DELETE", "/v1/apps/app-1/data"),
            ("DELETE", "/v1/apps/app-1"),
        ]
    )
    for call, timeout in slow.items():
        assert (timeout["read"], timeout["write"], timeout["connect"]) == (90.0, 90.0, 5.0), call
    # What the host answers at once keeps the short wait: a host that is down is noticed quickly.
    assert len(quick) == 7
    for call, timeout in quick.items():
        assert (timeout["read"], timeout["connect"]) == (15.0, 5.0), call
    (upload,) = seen.values()
    assert upload["read"] == UPLOAD_TIMEOUT_SECONDS


def test_a_stop_the_host_needs_twenty_seconds_for_is_a_stop_not_an_unreachable_host(harness, monkeypatch):
    app_id = harness.create(deploy=True)["app"]["id"]
    assert harness.row(app_id).desired_state == "running"
    stopped = []

    def slow_host(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/stop"):
            # The host answers after it stopped the process: that takes it twenty seconds here.
            if request.extensions["timeout"]["read"] < 20:
                raise httpx.ReadTimeout("the host was still stopping the app")
            stopped.append(app_id)
        return httpx.Response(200, json={**STATUS, "id": app_id})

    monkeypatch.setattr(harness.api, "host_client", lambda: real_client(slow_host))

    response = harness.as_user("owner").post(f"/api/apps/{app_id}/stop")

    assert response.status_code == 200, response.text
    assert stopped == [app_id]
    assert response.json()["state"] == "stopped" and response.json()["desired_state"] == "stopped"
    assert harness.row(app_id).desired_state == "stopped"
    assert harness.actions(app_id)[-1] == "app.stopped"


# --- 4. a day's traffic never goes down --------------------------------------------------------------------


def _ingest(h, app_id, day="2026-11-15", **counters):
    body = {"sent_at": "2026-11-15T12:00:00Z", "traffic": [{"day": day, "app_id": app_id, **counters}], "apps": [], "host": None}
    response = h.client.post("/api/app-host/v1/metrics/ingest", json=body, headers={"Authorization": f"Bearer {HOST_TOKEN}"})
    assert response.status_code == 200, response.text


def _day(h, app_id, day=date(2026, 11, 15)):
    h.db.expire_all()
    row = h.db.query(AppTrafficDaily).filter(AppTrafficDaily.app_id == app_id, AppTrafficDaily.day == day).one()
    return (row.requests, row.visitors, row.errors_4xx, row.errors_5xx, row.p95_ms)


def test_a_days_traffic_is_not_lowered_when_the_hosts_counters_start_again(harness):
    app_id = harness.create()["app"]["id"]

    _ingest(harness, app_id, requests=120, visitors=7, errors_4xx=3, errors_5xx=1, p95_ms=240)
    assert _day(harness, app_id) == (120, 7, 3, 1, 240)

    # The host lost its state file and counts from zero again: the day keeps what it had.
    _ingest(harness, app_id, requests=0, visitors=0, errors_4xx=0, errors_5xx=0, p95_ms=0)
    assert _day(harness, app_id) == (120, 7, 3, 1, 240)
    _ingest(harness, app_id, requests=5, visitors=1, errors_4xx=0, errors_5xx=2, p95_ms=310)
    # Each count keeps the larger value; the response time is the one that was just measured.
    assert _day(harness, app_id) == (120, 7, 3, 2, 310)

    # Counters that passed the old ones are taken, one by one.
    _ingest(harness, app_id, requests=130, visitors=6, errors_4xx=4, errors_5xx=0, p95_ms=180)
    assert _day(harness, app_id) == (130, 7, 4, 2, 180)
    # The same report twice changes nothing.
    _ingest(harness, app_id, requests=130, visitors=6, errors_4xx=4, errors_5xx=0, p95_ms=180)
    assert _day(harness, app_id) == (130, 7, 4, 2, 180)

    # Another day is its own row and starts from what is sent for it.
    _ingest(harness, app_id, day="2026-11-16", requests=2, visitors=1, errors_4xx=0, errors_5xx=0, p95_ms=40)
    assert _day(harness, app_id, date(2026, 11, 16)) == (2, 1, 0, 0, 40)
    assert _day(harness, app_id) == (130, 7, 4, 2, 180)
    assert harness.db.query(AppTrafficDaily).count() == 2


def test_upsert_day_keeps_the_larger_count_and_the_latest_percentile(harness):
    repo = AppTrafficRepository(harness.db)
    day = date(2026, 11, 15)

    first = repo.upsert_day(day, "app-x", requests=10, visitors=2, errors_4xx=1, errors_5xx=0, p95_ms=100)
    assert (first.requests, first.visitors, first.errors_4xx, first.errors_5xx, first.p95_ms) == (10, 2, 1, 0, 100)
    again = repo.upsert_day(day, "app-x", requests=4, visitors=3, errors_4xx=0, errors_5xx=1, p95_ms=0)
    assert again.id == first.id
    assert (again.requests, again.visitors, again.errors_4xx, again.errors_5xx, again.p95_ms) == (10, 3, 1, 1, 100)
    later = repo.upsert_day(day, "app-x", requests=11, visitors=3, errors_4xx=1, errors_5xx=1, p95_ms=60)
    assert (later.requests, later.p95_ms) == (11, 60)
    harness.db.rollback()


# --- 5. deleting an app nobody deployed ----------------------------------------------------------------------


def test_deleting_an_app_that_was_never_deployed_tells_the_host_too(harness):
    # The host registers every app the Portal lists when it boots, so it may hold a record of this one.
    draft = harness.create(name="Draft", slug="draft")["app"]["id"]
    wiped = harness.create(name="Wiped", slug="wiped")["app"]["id"]
    assert harness.db.query(AppRelease).count() == 0

    assert harness.as_user("owner").delete(f"/api/apps/{draft}").status_code == 200
    assert harness.as_user("owner").delete(f"/api/apps/{wiped}?purge_data=true").status_code == 200

    assert harness.host.deleted == [(draft, False), (wiped, True)]
    assert harness.row(draft).deleted_at is not None and harness.row(wiped).data_purged_at is not None
    assert harness.actions(draft)[-1] == "app.deleted"


def test_a_host_that_cannot_be_reached_stops_the_deletion_of_a_draft_as_of_any_app(harness):
    app_id = harness.create()["app"]["id"]
    harness.host.fail["delete_app"] = AppHostUnavailable()

    response = harness.as_user("owner").delete(f"/api/apps/{app_id}")

    assert response.status_code == 503 and response.json()["code"] == "host_unreachable"
    assert harness.row(app_id).deleted_at is None
    assert "app.deleted" not in harness.actions(app_id)

    # A host that knows nothing of the app has nothing to remove: the deletion goes through.
    harness.host.fail["delete_app"] = AppHostError(code="host_app_not_found", reason="app_not_found", host_code="app_not_found", host_status=404)
    assert harness.as_user("owner").delete(f"/api/apps/{app_id}").status_code == 200
    assert harness.row(app_id).deleted_at is not None


# --- 6. a failed step of the host says why ----------------------------------------------------------------------


def _host_steps_of(release):
    return {step["name"]: step for step in release["steps"] if step["source"] == "host"}


def test_a_failed_host_step_carries_the_hosts_reason(harness):
    app_id = harness.create()["app"]["id"]
    reason = "No matching distribution found for nope==1.0"
    harness.host.job_states = [failed_job("dependencies", reason)]

    release = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()

    assert release["status"] == "failed"
    steps = _host_steps_of(release)
    assert steps["dependencies"]["status"] == "failed"
    assert steps["dependencies"]["error"] == {"code": "host_step_failed", "message": reason}
    # Only that step: what ran before it succeeded, what would have followed was skipped.
    assert [name for name, step in steps.items() if "error" in step] == ["dependencies"]
    assert steps["environment"]["status"] == "succeeded" and steps["start"]["status"] == "skipped"
    assert all("error" not in step for step in release["steps"] if step["source"] == "portal")
    # The summary stays what it was.
    assert release["error_summary"] == f"The deployment failed at the step dependencies: {reason}"

    # It is stored with the release, not only in the answer that started it.
    stored = harness.as_user("owner").get(f"/api/apps/{app_id}/releases/{release['id']}").json()
    assert _host_steps_of(stored)["dependencies"]["error"] == {"code": "host_step_failed", "message": reason}
    listed = harness.as_user("owner").get(f"/api/apps/{app_id}/releases").json()["releases"][0]
    assert _host_steps_of(listed)["dependencies"]["error"]["message"] == reason


def test_the_reason_lands_on_the_failed_step_when_the_job_names_none_or_another(harness):
    app_id = harness.create()["app"]["id"]
    owner = harness.as_user("owner")

    # No step named: the step the job marks failed.
    harness.host.job_states = [{"status": "failed", "steps": host_steps(health="failed", activate="pending"), "error": {"message": "no answer on its port"}, "log_tail": "", "log_size": 0}]
    steps = _host_steps_of(owner.post(f"/api/apps/{app_id}/deploy").json())
    assert steps["health"]["error"] == {"code": "host_step_failed", "message": "no answer on its port"}

    # A step the list does not have: the same.
    harness.host.job_states = [{"status": "failed", "steps": host_steps(start="failed"), "error": {"step": "warm-up", "message": "boom"}, "log_tail": "", "log_size": 0}]
    steps = _host_steps_of(owner.post(f"/api/apps/{app_id}/deploy").json())
    assert steps["start"]["error"] == {"code": "host_step_failed", "message": "boom"}

    # No reason given at all: the step still says that the host failed there.
    harness.host.job_states = [{"status": "failed", "steps": host_steps(unpack="failed"), "error": None, "log_tail": "", "log_size": 0}]
    release = owner.post(f"/api/apps/{app_id}/deploy").json()
    assert _host_steps_of(release)["unpack"]["error"] == {"code": "host_step_failed", "message": "the app host reported a failure"}

    # A very long reason is cut, on the step as in the summary.
    harness.host.job_states = [failed_job("dependencies", "x" * 5000)]
    release = owner.post(f"/api/apps/{app_id}/deploy").json()
    assert len(_host_steps_of(release)["dependencies"]["error"]["message"]) == 2000


def test_a_failed_portal_step_keeps_its_own_error_and_a_success_has_none(harness):
    app_id = harness.create()["app"]["id"]
    harness.source.refs.pop("main")

    failed = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()
    resolve = next(step for step in failed["steps"] if step["source"] == "portal" and step["name"] == "resolve")
    assert resolve["status"] == "failed" and resolve["error"]["code"] == "ref_not_found" and resolve["error"]["message_key"]
    assert all("error" not in step for step in failed["steps"] if step["source"] == "host")

    harness.source.refs["main"] = "2" * 40
    done = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()
    assert done["status"] == "succeeded" and all("error" not in step for step in done["steps"])
    assert harness.db.query(AppAuditEvent).filter(AppAuditEvent.action == "deploy.failed").count() == 1
