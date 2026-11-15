"""/api/app-host/v1: what the Portal serves to the app host (docs/CONTRACTS.md, section 5)."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from _apps_helpers import (
    HOST_TOKEN,
    REPO_URL,
    SHA_1,
    FakeHost,
    FakeSource,
    add_group,
    add_user,
    enable_apps,
    make_database,
    make_deploy_service,
    remove_from_allowlist,
    reset_apps_state,
)
from app.db import get_db
from app.models import AppAuditEvent, AppTrafficDaily, AppVisit
from app.services.apps import access as access_module
from app.services.apps.access import HandoffCodeStore, handoff_codes
from app.services.apps.app_service import AppService
from app.services.apps.host_state import host_state

PREFIX = "/api/app-host/v1"
AUTH = {"Authorization": f"Bearer {HOST_TOKEN}"}


@pytest.fixture
def harness(monkeypatch):
    from app.main import app

    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    default = add_group(db, "Default", is_default=True)
    qa = add_group(db, "QA")
    users = {
        "owner": add_user(db, "owner", group=qa, nickname="Olive"),
        "mate": add_user(db, "mate", group=qa, nickname="Bob"),
        "other": add_user(db, "other", group=default),
        "admin": add_user(db, "admin", role="admin", group=default),
    }
    host = FakeHost()
    source = FakeSource()
    service = AppService(db, host=host, source=source)
    deploys = make_deploy_service(factory, host, source)
    apps = {
        "private": service.create(users["owner"], repo_url=REPO_URL, ref="main", name="Mind map", slug="mindmap"),
        "public": service.create(users["owner"], repo_url=REPO_URL, ref="main", name="Board", slug="board", visibility="public"),
        "platform": service.create(users["owner"], repo_url=REPO_URL, ref="main", name="Wiki", slug="wiki", visibility="platform"),
    }

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, db=db, users=users, apps=apps, service=service, deploys=deploys, host=host, groups={"qa": qa, "default": default})
    finally:
        app.dependency_overrides.clear()
        db.close()
        reset_apps_state()


def _post(harness, path, body, headers=AUTH):
    return harness.client.post(f"{PREFIX}{path}", json=body, headers=headers)


# --- the bearer token -------------------------------------------------------------------

ROUTES = [
    ("POST", "/handoff/redeem", {"code": "x"}),
    ("POST", "/access/check", {"app_id": "x", "user_id": 1}),
    ("GET", "/apps", None),
    ("POST", "/metrics/ingest", {"traffic": []}),
    ("POST", "/events", {"app_id": "x", "kind": "crashed"}),
]


@pytest.mark.parametrize("method, path, body", ROUTES, ids=[route[1] for route in ROUTES])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": HOST_TOKEN},
        {"Authorization": f"Basic {HOST_TOKEN}"},
        {"Authorization": "Bearer "},
        {"Authorization": f"Bearer {HOST_TOKEN}x"},
        {"Authorization": f"Bearer {HOST_TOKEN[:-1]}"},
        {"Cookie": "portal_session=whatever"},
    ],
    ids=["none", "wrong", "no-scheme", "basic", "empty", "longer", "shorter", "cookie-only"],
)
def test_every_route_needs_the_host_token(harness, method, path, body, headers):
    response = harness.client.request(method, f"{PREFIX}{path}", json=body, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthorized", "message": "A valid host API token is required."}}


@pytest.mark.parametrize("method, path, body", ROUTES, ids=[route[1] for route in ROUTES])
def test_an_empty_configured_token_rejects_everything(harness, monkeypatch, method, path, body):
    enable_apps(monkeypatch, apps_host_api_token="")
    for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer"}, AUTH):
        response = harness.client.request(method, f"{PREFIX}{path}", json=body, headers=headers)
        assert response.status_code == 401, headers


@pytest.mark.parametrize("method, path, body", ROUTES, ids=[route[1] for route in ROUTES])
def test_every_route_is_404_while_apps_are_off(harness, monkeypatch, method, path, body):
    enable_apps(monkeypatch, apps_enabled=False)
    response = harness.client.request(method, f"{PREFIX}{path}", json=body, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_scheme_is_case_insensitive_as_http_says(harness):
    response = harness.client.get(f"{PREFIX}/apps", headers={"Authorization": f"bearer {HOST_TOKEN}"})
    assert response.status_code == 200


# --- 5.1 redeem -----------------------------------------------------------------------


def test_redeem_answers_the_app_and_the_member_once(harness):
    app, mate = harness.apps["public"], harness.users["mate"]
    code = handoff_codes.issue(app.id, mate.id)
    response = _post(harness, "/handoff/redeem", {"code": code})
    assert response.status_code == 200
    assert response.json() == {
        "app_id": app.id,
        "slug": "board",
        "user": {"id": mate.id, "username": "mate", "display_name": "Bob", "group_id": harness.groups["qa"].id, "group_name": "QA"},
    }
    again = _post(harness, "/handoff/redeem", {"code": code})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_code"


def test_a_code_expires_after_sixty_seconds(harness, monkeypatch):
    now = {"value": 100.0}
    store = HandoffCodeStore(clock=lambda: now["value"])
    monkeypatch.setattr("app.api.app_host.handoff_codes", store)
    app, mate = harness.apps["public"], harness.users["mate"]

    fresh = store.issue(app.id, mate.id)
    now["value"] += 59
    assert _post(harness, "/handoff/redeem", {"code": fresh}).status_code == 200

    stale = store.issue(app.id, mate.id)
    now["value"] += 61
    response = _post(harness, "/handoff/redeem", {"code": stale})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_code"
    # Expired is gone for good, not valid again later.
    now["value"] -= 61
    assert _post(harness, "/handoff/redeem", {"code": stale}).status_code == 400


@pytest.mark.parametrize("body", [{}, {"code": ""}, {"code": None}, {"code": 5}, {"code": "never-issued"}, {"code": ["x"]}, [], "text", None], ids=["no-code", "empty", "null", "number", "unknown", "list", "array-body", "string-body", "no-body"])
def test_anything_that_is_not_a_live_code_is_invalid(harness, body):
    response = _post(harness, "/handoff/redeem", body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"invalid_code", "invalid_request"}


def test_a_code_dies_with_the_members_access_or_the_app(harness):
    app, mate = harness.apps["public"], harness.users["mate"]
    code = handoff_codes.issue(app.id, mate.id)
    remove_from_allowlist(harness.db, "mate")
    assert _post(harness, "/handoff/redeem", {"code": code}).status_code == 400

    other = harness.users["other"]
    code = handoff_codes.issue(harness.apps["platform"].id, other.id)
    harness.service.update(harness.apps["platform"], harness.users["owner"], visibility="private")
    assert _post(harness, "/handoff/redeem", {"code": code}).status_code == 400

    code = handoff_codes.issue(app.id, harness.users["owner"].id)
    harness.service.delete(app, harness.users["owner"])
    assert _post(harness, "/handoff/redeem", {"code": code}).status_code == 400


# --- 5.2 access check ---------------------------------------------------------------------

ACCESS = {
    # app: {member: (allowed, reason)}
    "private": {"owner": (True, None), "mate": (False, "no_access"), "other": (False, "no_access"), "admin": (True, None)},
    "public": {"owner": (True, None), "mate": (True, None), "other": (False, "no_access"), "admin": (True, None)},
    "platform": {"owner": (True, None), "mate": (True, None), "other": (True, None), "admin": (True, None)},
}


@pytest.mark.parametrize("app_name", sorted(ACCESS))
@pytest.mark.parametrize("member", ["owner", "mate", "other", "admin"])
def test_access_check_matrix(harness, app_name, member):
    user = harness.users[member]
    response = _post(harness, "/access/check", {"app_id": harness.apps[app_name].id, "user_id": user.id})
    assert response.status_code == 200
    body = response.json()
    assert (body["allowed"], body["reason"]) == ACCESS[app_name][member]
    assert body["user"]["id"] == user.id and body["user"]["username"] == member
    assert set(body["user"]) == {"id", "username", "display_name", "group_id", "group_name"}


def test_access_check_for_a_member_who_lost_access(harness):
    app = harness.apps["platform"]
    remove_from_allowlist(harness.db, "mate")
    body = _post(harness, "/access/check", {"app_id": app.id, "user_id": harness.users["mate"].id}).json()
    assert (body["allowed"], body["reason"]) == (False, "user_inactive")
    assert body["user"]["username"] == "mate"


def test_access_check_for_an_unknown_member_omits_the_user(harness):
    body = _post(harness, "/access/check", {"app_id": harness.apps["platform"].id, "user_id": 99999}).json()
    assert body == {"allowed": False, "reason": "user_inactive"}


def test_access_check_for_unknown_and_deleted_apps(harness):
    mate = harness.users["mate"]
    body = _post(harness, "/access/check", {"app_id": "no-such-app", "user_id": mate.id}).json()
    assert (body["allowed"], body["reason"]) == (False, "app_not_found")
    assert body["user"]["id"] == mate.id
    harness.service.delete(harness.apps["public"], harness.users["owner"])
    body = _post(harness, "/access/check", {"app_id": harness.apps["public"].id, "user_id": mate.id}).json()
    assert (body["allowed"], body["reason"]) == (False, "app_not_found")


def test_access_check_for_a_disabled_app(harness):
    app = harness.apps["platform"]
    harness.service.disable(app, harness.users["admin"], "Under review")
    for member in ("mate", "owner", "admin"):
        body = _post(harness, "/access/check", {"app_id": app.id, "user_id": harness.users[member].id}).json()
        assert (body["allowed"], body["reason"]) == (False, "app_disabled"), member
    harness.service.enable(app, harness.users["admin"])
    assert _post(harness, "/access/check", {"app_id": app.id, "user_id": harness.users["mate"].id}).json()["allowed"] is True

    # An owner who lost access takes their apps down with them.
    remove_from_allowlist(harness.db, "owner")
    body = _post(harness, "/access/check", {"app_id": app.id, "user_id": harness.users["mate"].id}).json()
    assert (body["allowed"], body["reason"]) == (False, "app_disabled")


def test_access_check_follows_a_change_of_audience_at_once(harness):
    app, other = harness.apps["platform"], harness.users["other"]
    check = {"app_id": app.id, "user_id": other.id}
    assert _post(harness, "/access/check", check).json()["allowed"] is True
    harness.service.update(app, harness.users["owner"], visibility="public")
    assert _post(harness, "/access/check", check).json() == {**_post(harness, "/access/check", check).json(), "allowed": False, "reason": "no_access"}


@pytest.mark.parametrize("body", [{}, {"app_id": "x"}, {"user_id": 1}, {"app_id": "", "user_id": 1}, {"app_id": "x", "user_id": "1"}, {"app_id": "x", "user_id": 0}, {"app_id": "x", "user_id": True}, {"app_id": 5, "user_id": 1}, []])
def test_access_check_needs_an_app_and_a_member(harness, body):
    response = _post(harness, "/access/check", body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


# --- 5.3 desired state ----------------------------------------------------------------------


def test_desired_state_lists_every_app_that_is_not_deleted(harness):
    owner = harness.users["owner"]
    harness.deploys.start_deploy(harness.apps["public"], None, owner)
    harness.service.update(harness.apps["public"], owner, env={"MY_SETTING": "x"})
    harness.service.disable(harness.apps["platform"], harness.users["admin"], "Under review")
    harness.service.delete(harness.apps["private"], owner)

    response = harness.client.get(f"{PREFIX}/apps", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = {item["slug"]: item for item in response.json()["apps"]}
    assert set(items) == {"board", "wiki"}

    board = harness.apps["public"]
    harness.db.refresh(board)
    assert items["board"] == {
        "id": board.id,
        "slug": "board",
        "name": "Board",
        "owner_username": "owner",
        "visibility": "public",
        "enabled": True,
        "disabled_reason": None,
        "current_sha": SHA_1,
        "visitor_key": board.visitor_key,
        "env": {"MY_SETTING": "x", "EFP_APP_TOKEN": board.token_value},
        "limits": {"memory_mb": None, "data_mb": None},
    }
    assert items["wiki"]["enabled"] is False and items["wiki"]["disabled_reason"] == "Under review"
    assert items["wiki"]["current_sha"] is None


def test_desired_state_disables_the_apps_of_an_owner_without_access(harness):
    remove_from_allowlist(harness.db, "owner")
    items = harness.client.get(f"{PREFIX}/apps", headers=AUTH).json()["apps"]
    assert items and all(item["enabled"] is False and item["disabled_reason"] for item in items)


def test_desired_state_sends_anyone_only_when_it_is_in_force(harness, monkeypatch):
    enable_apps(monkeypatch, apps_anyone_enabled=True)
    app = harness.apps["platform"]
    harness.service.update(app, harness.users["owner"], visibility="anyone")

    def visibility():
        items = harness.client.get(f"{PREFIX}/apps", headers=AUTH).json()["apps"]
        return next(item["visibility"] for item in items if item["id"] == app.id)

    assert visibility() == "platform"
    harness.service.set_anyone_approval(app, harness.users["admin"], True)
    assert visibility() == "anyone"
    enable_apps(monkeypatch, apps_anyone_enabled=False)
    assert visibility() == "platform"


# --- 5.4 metrics ----------------------------------------------------------------------------


def _metrics(harness, **overrides):
    app = harness.apps["public"]
    payload = {
        "sent_at": "2026-11-15T08:35:00Z",
        "traffic": [
            {
                "day": "2026-11-15",
                "app_id": app.id,
                "requests": 120,
                "errors_4xx": 3,
                "errors_5xx": 1,
                "p95_ms": 240,
                "visitors": 7,
                "users": [
                    {"user_id": harness.users["mate"].id, "requests": 40, "last_at": "2026-11-15T08:30:00Z"},
                    {"user_id": harness.users["other"].id, "requests": 5, "last_at": "2026-11-15T07:00:00Z"},
                ],
            }
        ],
        "apps": [{"id": app.id, "slug": "board", "type": "fastapi", "state": "running", "reason": None, "data_bytes": 2048}],
        "host": {"version": "0.1.0", "apps_total": 3, "apps_running": 1},
    }
    payload.update(overrides)
    return payload


def _traffic(harness):
    harness.db.expire_all()
    return [
        (row.day, row.requests, row.visitors, row.errors_4xx, row.errors_5xx, row.p95_ms)
        for row in harness.db.query(AppTrafficDaily).order_by(AppTrafficDaily.day)
    ]


def _visits(harness):
    harness.db.expire_all()
    return {
        harness.db.get(type(harness.users["mate"]), row.user_id).username: (row.visits, row.last_visited_at)
        for row in harness.db.query(AppVisit).order_by(AppVisit.user_id)
    }


def test_metrics_are_stored_as_sent(harness):
    response = _post(harness, "/metrics/ingest", _metrics(harness))
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert _traffic(harness) == [(date(2026, 11, 15), 120, 7, 3, 1, 240)]
    assert _visits(harness) == {
        "mate": (40, datetime(2026, 11, 15, 8, 30, 0)),
        "other": (5, datetime(2026, 11, 15, 7, 0, 0)),
    }
    # The statuses and the host's own figures are kept for the pages.
    view = host_state.status_of(harness.apps["public"].id)
    assert view.status["state"] == "running" and view.status["data_bytes"] == 2048 and view.live is False
    assert host_state.host()[0] == {"version": "0.1.0", "apps_total": 3, "apps_running": 1}


def test_the_same_post_twice_changes_nothing(harness):
    payload = _metrics(harness)
    for _ in range(3):
        assert _post(harness, "/metrics/ingest", payload).status_code == 200
    assert _traffic(harness) == [(date(2026, 11, 15), 120, 7, 3, 1, 240)]
    assert _visits(harness)["mate"] == (40, datetime(2026, 11, 15, 8, 30, 0))
    assert harness.db.query(AppTrafficDaily).count() == 1 and harness.db.query(AppVisit).count() == 2


def test_running_totals_replace_and_days_add_up(harness):
    app, mate = harness.apps["public"], harness.users["mate"]
    _post(harness, "/metrics/ingest", _metrics(harness))

    # Later the same day: the totals grew; a member's count grows by the difference.
    later = _metrics(harness)
    later["traffic"][0].update({"requests": 300, "visitors": 9, "errors_4xx": 4, "p95_ms": 180})
    later["traffic"][0]["users"] = [{"user_id": mate.id, "requests": 100, "last_at": "2026-11-15T12:00:00Z"}]
    _post(harness, "/metrics/ingest", later)
    assert _traffic(harness) == [(date(2026, 11, 15), 300, 9, 4, 1, 180)]
    assert _visits(harness)["mate"] == (100, datetime(2026, 11, 15, 12, 0, 0))
    assert _visits(harness)["other"] == (5, datetime(2026, 11, 15, 7, 0, 0))

    # A member's earlier, smaller total arriving late takes nothing away from them.
    late = _metrics(harness)
    late["traffic"][0].update({"requests": 300, "visitors": 9, "errors_4xx": 4, "p95_ms": 180})
    late["traffic"][0]["users"] = [{"user_id": mate.id, "requests": 40, "last_at": "2026-11-15T08:30:00Z"}]
    _post(harness, "/metrics/ingest", late)
    assert _visits(harness)["mate"] == (100, datetime(2026, 11, 15, 12, 0, 0))

    # The next day starts from zero and adds to the member's all-time figure.
    next_day = {"day": "2026-11-16", "app_id": app.id, "requests": 10, "visitors": 1, "errors_4xx": 0, "errors_5xx": 0, "p95_ms": 90, "users": [{"user_id": mate.id, "requests": 10, "last_at": "2026-11-16T09:00:00Z"}]}
    for _ in range(2):
        _post(harness, "/metrics/ingest", _metrics(harness, traffic=[next_day]))
    assert _traffic(harness) == [(date(2026, 11, 15), 300, 9, 4, 1, 180), (date(2026, 11, 16), 10, 1, 0, 0, 90)]
    assert _visits(harness)["mate"] == (110, datetime(2026, 11, 16, 9, 0, 0))

    # A late post for the day before only ever moves "last visited" forward, which it does not here.
    _post(harness, "/metrics/ingest", later)
    assert _visits(harness)["mate"] == (110, datetime(2026, 11, 16, 9, 0, 0))


def test_one_post_may_carry_several_days_and_apps(harness):
    board, wiki, mate = harness.apps["public"], harness.apps["platform"], harness.users["mate"]
    traffic = [
        {"day": "2026-11-14", "app_id": board.id, "requests": 8, "users": [{"user_id": mate.id, "requests": 8, "last_at": "2026-11-14T10:00:00Z"}]},
        {"day": "2026-11-15", "app_id": board.id, "requests": 2, "users": [{"user_id": mate.id, "requests": 2, "last_at": "2026-11-15T10:00:00Z"}]},
        {"day": "2026-11-15", "app_id": wiki.id, "requests": 50, "users": [{"user_id": mate.id, "requests": 50, "last_at": "2026-11-15T11:00:00Z"}]},
    ]
    assert _post(harness, "/metrics/ingest", _metrics(harness, traffic=traffic)).status_code == 200
    assert harness.db.query(AppTrafficDaily).count() == 3
    rows = {row.app_id: row for row in harness.db.query(AppVisit)}
    assert (rows[board.id].visits, rows[board.id].last_visited_at) == (10, datetime(2026, 11, 15, 10, 0, 0))
    assert rows[wiki.id].visits == 50


def test_malformed_items_are_skipped_not_fatal(harness):
    app = harness.apps["public"]
    traffic = [
        "junk",
        {"day": "not-a-day", "app_id": app.id, "requests": 1},
        {"day": "2026-11-15", "app_id": "unknown-app", "requests": 1},
        {"day": "2026-11-15", "app_id": app.id, "requests": -5, "visitors": "many", "errors_4xx": True, "p95_ms": 12.0,
         "users": ["junk", {"user_id": "x", "requests": 1}, {"user_id": 0, "requests": 1}, {"user_id": harness.users["mate"].id, "requests": None, "last_at": "yesterday"}]},
    ]
    response = _post(harness, "/metrics/ingest", {"traffic": traffic, "apps": "nope", "host": []})
    assert response.status_code == 200
    assert _traffic(harness) == [(date(2026, 11, 15), 0, 0, 0, 0, 12)]
    assert _visits(harness) == {"mate": (0, None)}
    assert host_state.host() == (None, None)


def test_metrics_with_nothing_in_them(harness):
    assert _post(harness, "/metrics/ingest", {}).json() == {"ok": True}
    assert _post(harness, "/metrics/ingest", []).status_code == 400


# --- 5.5 events -----------------------------------------------------------------------------


def test_events_are_kept_as_audit_rows_without_an_actor(harness):
    app = harness.apps["public"]
    body = {"app_id": app.id, "kind": "crashed", "detail": {"exit_code": 1, "log": ["Traceback ...", "password=hunter2 token=abc123"]}, "at": "2026-11-15T08:30:00Z"}
    response = _post(harness, "/events", body)
    assert response.status_code == 200 and response.json() == {"ok": True}
    event = harness.db.query(AppAuditEvent).filter(AppAuditEvent.action == "host.crashed").one()
    assert event.app_id == app.id and event.actor_user_id is None
    assert event.detail["at"] == "2026-11-15T08:30:00Z"
    assert event.detail["detail"]["exit_code"] == 1
    # What an app printed goes through the redactor before it is stored.
    assert "hunter2" not in event.detail_json and "abc123" not in event.detail_json


@pytest.mark.parametrize("kind", ["crashed", "killed", "idle_stopped", "started", "quota_exceeded"])
def test_every_kind_of_the_contract_is_accepted(harness, kind):
    assert _post(harness, "/events", {"app_id": harness.apps["public"].id, "kind": kind}).status_code == 200
    assert harness.db.query(AppAuditEvent).filter(AppAuditEvent.action == f"host.{kind}").count() == 1


def test_events_for_unknown_apps_and_bad_kinds(harness):
    response = _post(harness, "/events", {"app_id": "no-such-app", "kind": "crashed"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "app_not_found"
    for body in ({"kind": "crashed"}, {"app_id": harness.apps["public"].id}, {"app_id": harness.apps["public"].id, "kind": "Crashed!"}, {"app_id": harness.apps["public"].id, "kind": "x" * 60}):
        response = _post(harness, "/events", body)
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


def test_an_event_about_a_deleted_app_is_still_recorded(harness):
    app = harness.apps["public"]
    harness.service.delete(app, harness.users["owner"])
    assert _post(harness, "/events", {"app_id": app.id, "kind": "idle_stopped"}).status_code == 200


def test_a_huge_event_detail_is_cut(harness):
    app = harness.apps["public"]
    _post(harness, "/events", {"app_id": app.id, "kind": "crashed", "detail": {"log": ["x" * 1000] * 100}})
    event = harness.db.query(AppAuditEvent).filter(AppAuditEvent.action == "host.crashed").one()
    assert event.detail["detail"]["truncated"] is True
    assert len(event.detail_json) < 20000


def test_the_module_store_is_the_one_the_launch_page_issues_from():
    import app.api.app_host as api
    import app.apps_pages as pages

    assert api.handoff_codes is pages.handoff_codes is access_module.handoff_codes
