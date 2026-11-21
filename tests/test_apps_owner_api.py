"""/api/apps: the owner API. Who may do what, what comes back, and what is written down."""

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from _apps_helpers import (
    MANIFEST,
    PNG,
    REPO_URL,
    SHA_1,
    SHA_2,
    FakeHost,
    FakeSource,
    add_group,
    add_user,
    enable_apps,
    failed_job,
    make_database,
    make_deploy_service,
    remove_from_allowlist,
    reset_apps_state,
)
from app.db import get_db
from app.deps import get_current_user
from app.models import App, AppAuditEvent, AppRelease, AppVisit
from app.services.apps.errors import AppHostUnavailable, RepoNotReadable
from app.services.apps.host_client import AppHostClient
from app.services.apps.host_state import host_state


@pytest.fixture
def harness(monkeypatch):
    from app.main import app
    import app.api.apps as api

    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    default = add_group(db, "Default", is_default=True)
    qa = add_group(db, "QA")
    users = {
        "owner": add_user(db, "owner", group=qa, nickname="Olive"),
        "mate": add_user(db, "mate", group=qa),
        "other": add_user(db, "other", group=default),
        "admin": add_user(db, "admin", role="admin", group=default),
        "gadmin": add_user(db, "gadmin", role="group_admin", group=qa),
    }
    host = FakeHost()
    source = FakeSource()
    current = {"user": users["owner"]}

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def current_user():
        # A fresh copy per request, as the real dependency loads one.
        session = factory()
        try:
            from app.models import User

            user = session.get(User, current["user"].id)
            session.expunge(user)
            return user
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr(api, "host_client", lambda: host)
    monkeypatch.setattr(api, "source_client", lambda _db: source)
    monkeypatch.setattr(api, "deploy_service", make_deploy_service(factory, host, source))
    client = TestClient(app)

    h = SimpleNamespace(client=client, db=db, factory=factory, users=users, host=host, source=source, current=current, api=api)

    def as_user(name):
        current["user"] = users[name]
        return client

    def create(**overrides):
        body = {"repo_url": REPO_URL, "ref": "main", "entry_dir": "apps/mindmap", "name": "Mind map", "deploy": False}
        body.update(overrides)
        owner = body.pop("as_user", "owner")
        response = as_user(owner).post("/api/apps", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def row(app_id):
        db.expire_all()
        return db.get(App, app_id)

    def actions(app_id):
        db.expire_all()
        return [item.action for item in db.query(AppAuditEvent).filter(AppAuditEvent.app_id == app_id).order_by(AppAuditEvent.id)]

    h.as_user, h.create, h.row, h.actions = as_user, create, row, actions
    try:
        yield h
    finally:
        app.dependency_overrides.clear()
        db.close()
        reset_apps_state()


# --- off, and signed out ------------------------------------------------------------------

ROUTES = [
    ("GET", "/api/apps"),
    ("POST", "/api/apps"),
    ("GET", "/api/apps/settings"),
    ("POST", "/api/apps/inspect"),
    ("GET", "/api/apps/repo-refs?repo_url=x"),
    ("GET", "/api/apps/{id}"),
    ("PATCH", "/api/apps/{id}"),
    ("DELETE", "/api/apps/{id}"),
    ("POST", "/api/apps/{id}/deploy"),
    ("POST", "/api/apps/{id}/deploy-latest"),
    ("POST", "/api/apps/{id}/rollback"),
    ("GET", "/api/apps/{id}/releases"),
    ("GET", "/api/apps/{id}/releases/x"),
    ("GET", "/api/apps/{id}/releases/x/log"),
    ("POST", "/api/apps/{id}/start"),
    ("POST", "/api/apps/{id}/stop"),
    ("POST", "/api/apps/{id}/restart"),
    ("GET", "/api/apps/{id}/status"),
    ("GET", "/api/apps/{id}/logs"),
    ("GET", "/api/apps/{id}/logs/stream"),
    ("GET", "/api/apps/{id}/token"),
    ("POST", "/api/apps/{id}/token/reset"),
    ("GET", "/api/apps/{id}/icon"),
    ("GET", "/api/apps/{id}/data"),
    ("DELETE", "/api/apps/{id}/data"),
    ("GET", "/api/apps/{id}/events"),
    ("POST", "/api/apps/{id}/disable"),
    ("POST", "/api/apps/{id}/enable"),
    ("POST", "/api/apps/{id}/anyone-approval"),
    ("POST", "/api/apps/{id}/transfer"),
]


def test_every_route_is_404_while_apps_are_off(harness, monkeypatch):
    app_id = harness.create()["app"]["id"]
    enable_apps(monkeypatch, apps_enabled=False)
    for method, path in ROUTES:
        response = harness.client.request(method, path.replace("{id}", app_id), json={})
        assert response.status_code == 404, (method, path)
    assert harness.host.calls == []


def test_every_route_needs_a_portal_session(harness):
    from app.main import app

    app_id = harness.create()["app"]["id"]
    app.dependency_overrides.pop(get_current_user)
    for method, path in ROUTES:
        response = harness.client.request(method, path.replace("{id}", app_id), json={})
        assert response.status_code == 401, (method, path)
    assert harness.host.calls == []


# --- register -----------------------------------------------------------------------------


def test_create_registers_and_starts_the_first_deployment(harness):
    response = harness.as_user("owner").post("/api/apps", json={"repo_url": "git@github.com:acme/tools.git", "ref": "main", "entry_dir": "apps/mindmap", "name": "Mind map"})
    assert response.status_code == 201, response.text
    body = response.json()
    app = body["app"]
    assert app["slug"] == "mind-map" and app["name"] == "Mind map"
    assert app["url"] == "https://app.example.com/mind-map/"
    assert app["visibility"] == "private" and app["effective_visibility"] == "private" and app["anyone_pending_approval"] is False
    assert app["owner"] == {"id": harness.users["owner"].id, "username": "owner", "display_name": "Olive", "group_id": harness.users["owner"].group_id, "group_name": "QA"}
    assert app["is_owner"] is True and app["can_manage"] is True
    assert app["enabled"] is True and app["disabled_reason"] is None
    assert app["type"] == "fastapi"
    # The deployment ran (the test runs it in the request) and the app shows it.
    assert body["deploy_error"] is None
    assert body["release"]["kind"] == "deploy" and body["release"]["status"] == "succeeded"
    assert body["release"]["commit_sha"] == SHA_1
    assert app["deployed"] is True and app["current_release"]["id"] == body["release"]["id"]
    assert app["has_icon"] is True and app["icon_url"].startswith(f"/api/apps/{app['id']}/icon?v=")
    assert app["state"] == "running"
    manage = app["manage"]
    assert manage["repo_url"] == REPO_URL and manage["repo_ref"] == "main" and manage["entry_dir"] == "apps/mindmap"
    assert manage["env"] == {} and manage["llm_enabled"] is False and manage["connectors_enabled"] == []
    assert manage["default_daily_credits"] == 50.0 and manage["default_daily_tokens"] == 2000000
    assert manage["latest_release"]["id"] == body["release"]["id"] and manage["active_release"] is None
    # The token is never part of an app's own answer.
    assert "token" not in app and "efpa_" not in response.text
    assert harness.actions(app["id"]) == ["app.created", "deploy.requested", "deploy.succeeded"]


def test_create_without_deploying(harness):
    body = harness.create()
    assert body["release"] is None and body["app"]["deployed"] is False and body["app"]["state"] is None
    assert harness.host.calls == []


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"repo_url": "https://gitlab.com/acme/tools"}, 422, "repo_url_invalid"),
        ({"ref": "gone"}, 422, "ref_not_found"),
        ({"slug": "login"}, 422, "slug_reserved"),
        ({"slug": "No Good"}, 422, "slug_invalid"),
        ({"name": "x" * 81}, 422, "name_too_long"),
        ({"visibility": "everyone"}, 422, "visibility_invalid"),
        ({"visibility": "anyone"}, 400, "anyone_disabled"),
        ({"entry_dir": "../x"}, 422, "entry_dir_invalid"),
    ],
    ids=["other-host", "ref", "reserved", "invalid-slug", "long-name", "visibility", "anyone-off", "entry-dir"],
)
def test_create_refusals_say_what_is_wrong(harness, body, status, code):
    payload = {"repo_url": REPO_URL, "ref": "main", "name": "Mind map", "deploy": False, **body}
    response = harness.as_user("owner").post("/api/apps", json=payload)
    assert response.status_code == status, response.text
    error = response.json()
    assert error["code"] == code
    # English text for a log or a fallback, and the key with its values for the member's language.
    assert error["detail"] and error["message_key"] and isinstance(error["params"], dict)
    assert harness.db.query(App).count() == 0


def test_create_is_refused_for_a_member_the_owners_of_the_manifest_leave_out(harness):
    response = harness.as_user("other").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "Mine now"})
    assert response.status_code == 403
    error = response.json()
    assert error["code"] == "not_listed_as_owner" and error["params"] == {"username": "other"}


def test_a_manifest_without_owners_is_checked_and_registered_by_any_member(harness):
    del harness.source.manifest["owners"]
    looked = harness.as_user("other").post("/api/apps/inspect", json={"repo_url": REPO_URL, "ref": "main"})
    assert looked.status_code == 200, looked.text
    assert looked.json()["manifest"]["owners"] == []
    created = harness.as_user("other").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "Mine now", "deploy": False})
    assert created.status_code == 201, created.text
    assert harness.row(created.json()["app"]["id"]).owner_user_id == harness.users["other"].id


def test_an_unreadable_repository_names_the_account_to_add(harness):
    harness.source.fail["resolve_ref"] = RepoNotReadable(
        "The service account {account} cannot read {repo}. Add {account} to the repository as a read-only collaborator, then try again.",
        account="efp-bot",
        repo="acme/tools",
    )
    response = harness.as_user("owner").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "Mind map"})
    assert response.status_code == 422
    error = response.json()
    assert error["code"] == "repo_not_readable"
    assert error["params"] == {"account": "efp-bot", "repo": "acme/tools"}
    assert "efp-bot" in error["detail"] and "{account}" in error["message_key"]


def test_a_taken_slug_is_a_conflict(harness):
    harness.create(slug="board")
    response = harness.as_user("mate").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "x", "slug": "board"})
    assert response.status_code in {403, 409}  # "mate" is not an owner of the manifest either
    harness.source.manifest = {**MANIFEST, "owners": ["owner", "mate"]}
    response = harness.as_user("mate").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "x", "slug": "board"})
    assert response.status_code == 409 and response.json()["code"] == "slug_taken"


def test_a_first_deployment_that_cannot_start_does_not_lose_the_app(harness, monkeypatch):
    def refuse(app, ref=None, requested_by=None):
        from app.services.apps.errors import DeployInProgress

        raise DeployInProgress()

    monkeypatch.setattr(harness.api.deploy_service, "start_deploy", refuse)
    response = harness.as_user("owner").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "Mind map"})
    assert response.status_code == 201
    body = response.json()
    assert body["release"] is None and body["deploy_error"]["code"] == "deploy_in_progress"
    assert harness.db.query(App).count() == 1


# --- read: who sees what ----------------------------------------------------------------------


def test_the_card_is_for_whoever_may_open_the_app_and_the_rest_for_those_who_manage_it(harness):
    private = harness.create()["app"]["id"]
    public = harness.create(name="Board", visibility="public")["app"]["id"]

    card = harness.as_user("mate").get(f"/api/apps/{public}")
    assert card.status_code == 200
    body = card.json()
    assert body["name"] == "Board" and body["owner"]["display_name"] == "Olive"
    assert body["is_owner"] is False and body["can_manage"] is False
    assert body["manage"] is None
    assert "repo_url" not in card.text and "efpa_" not in card.text

    # What a member may not open does not exist for them.
    assert harness.as_user("mate").get(f"/api/apps/{private}").status_code == 404
    assert harness.as_user("other").get(f"/api/apps/{public}").status_code == 404
    assert harness.as_user("gadmin").get(f"/api/apps/{private}").status_code == 404
    assert harness.as_user("owner").get("/api/apps/no-such-app").status_code == 404

    # A system administrator opens and manages every app.
    admin = harness.as_user("admin").get(f"/api/apps/{private}").json()
    assert admin["can_manage"] is True and admin["is_owner"] is False and admin["manage"]["repo_url"] == REPO_URL


MANAGE_ROUTES = [
    ("PATCH", "", {"name": "Renamed"}),
    ("DELETE", "", None),
    ("POST", "/deploy", {}),
    ("POST", "/deploy-latest", None),
    ("POST", "/rollback", None),
    ("GET", "/releases", None),
    ("POST", "/start", None),
    ("POST", "/stop", None),
    ("POST", "/restart", None),
    ("GET", "/status", None),
    ("GET", "/logs", None),
    ("GET", "/logs/stream", None),
    ("GET", "/token", None),
    ("POST", "/token/reset", None),
    ("GET", "/data", None),
    ("DELETE", "/data", None),
    ("GET", "/events", None),
]


@pytest.mark.parametrize("method, suffix, body", MANAGE_ROUTES, ids=[f"{m} {s or '/'}" for m, s, _b in MANAGE_ROUTES])
def test_only_the_owner_or_an_administrator_changes_or_reveals(harness, method, suffix, body):
    app_id = harness.create(visibility="platform")["app"]["id"]
    harness.host.calls.clear()
    before = harness.actions(app_id)
    for name in ("mate", "other", "gadmin"):
        response = harness.as_user(name).request(method, f"/api/apps/{app_id}{suffix}", json=body)
        assert response.status_code == 403, (name, response.text)
        assert response.json()["code"] == "forbidden"
    # Nothing happened: no call to the host, nothing recorded, nothing changed.
    assert harness.host.calls == []
    assert harness.actions(app_id) == before
    assert harness.row(app_id).name == "Mind map" and harness.row(app_id).deleted_at is None

    # For an app they cannot even open, the answer is "not found".
    hidden = harness.create(name="Hidden")["app"]["id"]
    assert harness.as_user("mate").request(method, f"/api/apps/{hidden}{suffix}", json=body).status_code == 404


ADMIN_ROUTES = [
    ("/disable", {"reason": "Under review"}),
    ("/enable", None),
    ("/anyone-approval", {"approved": False}),
    ("/transfer", {"username": "mate"}),
]


@pytest.mark.parametrize("suffix, body", ADMIN_ROUTES, ids=[route[0] for route in ADMIN_ROUTES])
def test_take_downs_approvals_and_transfers_are_a_system_administrators(harness, suffix, body):
    app_id = harness.create(visibility="platform")["app"]["id"]
    for name in ("owner", "mate", "gadmin"):
        response = harness.as_user(name).post(f"/api/apps/{app_id}{suffix}", json=body)
        assert response.status_code == 403, name
    assert harness.as_user("admin").post(f"/api/apps/{app_id}{suffix}", json=body).status_code == 200


# --- lists ------------------------------------------------------------------------------------


def test_lists_mine_available_and_all(harness):
    alpha = harness.create(name="Alpha", visibility="public", deploy=True)["app"]["id"]
    harness.create(name="Beta", visibility="public")  # not deployed
    gamma = harness.create(name="Gamma notes", description="Team notes", deploy=True)["app"]["id"]
    wiki = harness.create(name="Wiki", visibility="platform", deploy=True)["app"]["id"]

    mine = harness.as_user("owner").get("/api/apps").json()
    assert mine["scope"] == "mine" and {app["name"] for app in mine["apps"]} == {"Alpha", "Beta", "Gamma notes", "Wiki"}
    assert all(app["manage"] is not None for app in mine["apps"])
    assert mine["host_reachable"] is True
    assert {app["name"]: app["state"] for app in mine["apps"]}["Alpha"] == "running"

    searched = harness.as_user("owner").get("/api/apps", params={"q": "NOTES"}).json()
    assert [app["id"] for app in searched["apps"]] == [gamma]
    assert harness.as_user("owner").get("/api/apps", params={"q": "wik"}).json()["apps"][0]["id"] == wiki

    available = harness.as_user("mate").get("/api/apps", params={"scope": "available"}).json()
    assert [app["name"] for app in available["apps"]] == ["Alpha", "Wiki"]
    assert all(app["manage"] is None for app in available["apps"])
    assert harness.as_user("mate").get("/api/apps").json()["apps"] == []
    assert [app["id"] for app in harness.as_user("other").get("/api/apps", params={"scope": "available"}).json()["apps"]] == [wiki]
    assert harness.as_user("owner").get("/api/apps", params={"scope": "available"}).json()["apps"] == []

    everything = harness.as_user("admin").get("/api/apps", params={"scope": "all"}).json()
    assert len(everything["apps"]) == 4 and all(app["can_manage"] for app in everything["apps"])
    assert harness.as_user("owner").get("/api/apps", params={"scope": "all"}).status_code == 403
    assert harness.as_user("owner").get("/api/apps", params={"scope": "nope"}).status_code == 422

    # Deleted apps are in no list.
    harness.as_user("owner").delete(f"/api/apps/{alpha}")
    assert alpha not in {app["id"] for app in harness.as_user("admin").get("/api/apps", params={"scope": "all"}).json()["apps"]}


def test_available_puts_what_the_member_used_last_first(harness):
    alpha = harness.create(name="Alpha", visibility="platform", deploy=True)["app"]["id"]
    beta = harness.create(name="Beta", visibility="platform", deploy=True)["app"]["id"]
    gamma = harness.create(name="Gamma", visibility="platform", deploy=True)["app"]["id"]
    mate = harness.users["mate"]
    harness.db.add_all(
        [
            AppVisit(app_id=gamma, user_id=mate.id, visits=3, last_visited_at=datetime(2026, 11, 15, 9, 0, 0)),
            AppVisit(app_id=beta, user_id=mate.id, visits=1, last_visited_at=datetime(2026, 11, 14, 9, 0, 0)),
            AppVisit(app_id=alpha, user_id=harness.users["other"].id, visits=9, last_visited_at=datetime(2026, 11, 15, 10, 0, 0)),
        ]
    )
    harness.db.commit()
    apps = harness.as_user("mate").get("/api/apps", params={"scope": "available"}).json()["apps"]
    assert [app["id"] for app in apps] == [gamma, beta, alpha]
    assert apps[0]["last_visited_at"] == "2026-11-15T09:00:00" and apps[2]["last_visited_at"] is None


def test_a_list_still_answers_when_the_host_does_not(harness):
    harness.create(deploy=True)
    host_state.clear()
    harness.host.fail["list_apps"] = AppHostUnavailable()
    body = harness.as_user("owner").get("/api/apps").json()
    assert body["host_reachable"] is False
    assert len(body["apps"]) == 1 and body["apps"][0]["state"] is None
    # The host is not asked again right away.
    harness.as_user("owner").get("/api/apps")
    assert harness.host.names().count("list_apps") == 1


# --- change -----------------------------------------------------------------------------------


def test_update_changes_what_was_sent_and_writes_it_down(harness):
    app_id = harness.create()["app"]["id"]
    response = harness.as_user("owner").patch(
        f"/api/apps/{app_id}",
        json={"name": "Mind maps", "description": None, "visibility": "public", "env": {"API_BASE": "https://x"}, "llm_enabled": True, "connectors_enabled": ["jira"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Mind maps" and body["description"] is None and body["visibility"] == "public"
    assert body["manage"]["env"] == {"API_BASE": "https://x"}
    assert body["manage"]["llm_enabled"] is True and body["manage"]["connectors_enabled"] == ["jira"]
    assert harness.actions(app_id) == ["app.created", "app.updated", "app.visibility_changed", "app.env_changed"]
    event = harness.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.visibility_changed").one()
    assert event.actor_user_id == harness.users["owner"].id and event.detail == {"from": "private", "to": "public", "in_force": "public"}

    # An empty or unchanged update writes nothing down.
    assert harness.as_user("owner").patch(f"/api/apps/{app_id}", json={}).status_code == 200
    assert harness.as_user("owner").patch(f"/api/apps/{app_id}", json={"name": "Mind maps"}).status_code == 200
    assert len(harness.actions(app_id)) == 4
    # A field left out is left alone; null clears only what can be empty.
    harness.as_user("owner").patch(f"/api/apps/{app_id}", json={"name": None, "visibility": None})
    assert harness.row(app_id).name == "Mind maps" and harness.row(app_id).visibility == "public"


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"name": "   "}, 422, "name_required"),
        ({"visibility": "team"}, 422, "visibility_invalid"),
        ({"visibility": "anyone"}, 400, "anyone_disabled"),
        ({"env": {"EFP_APP_TOKEN": "mine"}}, 422, "env_name_reserved"),
        ({"env": {"PATH": "/x"}}, 422, "env_name_reserved"),
        ({"env": {"bad-name": "1"}}, 422, "env_name_invalid"),
        ({"connectors_enabled": ["aws"]}, 422, "connector_unknown"),
        ({"repo_ref": "a b"}, 422, "ref_invalid"),
        ({"daily_credits": 5}, 403, "forbidden"),
    ],
    ids=["blank-name", "visibility", "anyone-off", "token-name", "path-name", "bad-env-name", "connector", "ref", "limits"],
)
def test_update_refusals(harness, body, status, code):
    app_id = harness.create()["app"]["id"]
    response = harness.as_user("owner").patch(f"/api/apps/{app_id}", json={"description": "would change", **body})
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert harness.row(app_id).description == MANIFEST["description"]
    assert harness.actions(app_id) == ["app.created"]


def test_a_change_the_host_cannot_be_told_is_not_made(harness):
    app_id = harness.create(visibility="platform", deploy=True)["app"]["id"]
    harness.host.fail["put_app"] = AppHostUnavailable()
    response = harness.as_user("owner").patch(f"/api/apps/{app_id}", json={"visibility": "private"})
    assert response.status_code == 503
    error = response.json()
    assert error["code"] == "host_unreachable" and "cannot be reached" in error["detail"]
    assert harness.row(app_id).visibility == "platform"


def test_asking_for_anyone_and_having_it_approved(harness, monkeypatch):
    enable_apps(monkeypatch, apps_anyone_enabled=True)
    app_id = harness.create(deploy=True)["app"]["id"]
    body = harness.as_user("owner").patch(f"/api/apps/{app_id}", json={"visibility": "anyone"}).json()
    assert (body["visibility"], body["effective_visibility"], body["anyone_pending_approval"]) == ("anyone", "platform", True)
    assert harness.host.specs[app_id]["visibility"] == "platform"

    approved = harness.as_user("admin").post(f"/api/apps/{app_id}/anyone-approval", json={"approved": True}).json()
    assert (approved["effective_visibility"], approved["anyone_pending_approval"]) == ("anyone", False)
    assert approved["manage"]["anyone_approved_by"] == harness.users["admin"].id
    assert harness.host.specs[app_id]["visibility"] == "anyone"

    withdrawn = harness.as_user("admin").post(f"/api/apps/{app_id}/anyone-approval", json={"approved": False}).json()
    assert withdrawn["effective_visibility"] == "platform"
    assert harness.host.specs[app_id]["visibility"] == "platform"
    assert harness.actions(app_id)[-3:] == ["app.visibility_changed", "app.anyone_approved", "app.anyone_revoked"]
    assert "anyone" in harness.as_user("owner").get("/api/apps/settings").json()["visibilities"]


def test_an_administrator_takes_an_app_down_and_puts_it_back(harness):
    app_id = harness.create(visibility="platform", deploy=True)["app"]["id"]
    body = harness.as_user("admin").post(f"/api/apps/{app_id}/disable", json={"reason": "Leaks customer data"}).json()
    assert body["enabled"] is False and body["disabled_reason"] == "Leaks customer data"
    assert harness.host.specs[app_id]["enabled"] is False
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/start").status_code == 409
    assert harness.as_user("admin").post(f"/api/apps/{app_id}/disable", json={"reason": " "}).status_code == 422
    body = harness.as_user("admin").post(f"/api/apps/{app_id}/enable").json()
    assert body["enabled"] is True and harness.host.specs[app_id]["enabled"] is True
    assert harness.actions(app_id)[-2:] == ["app.disabled", "app.enabled"]


def test_an_app_whose_owner_lost_access_says_so(harness):
    app_id = harness.create(visibility="platform")["app"]["id"]
    remove_from_allowlist(harness.db, "owner")
    body = harness.as_user("admin").get(f"/api/apps/{app_id}").json()
    assert body["enabled"] is False and body["disabled_reason"] == "owner_inactive"


def test_transfer_by_portal_username(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    assert harness.as_user("admin").post(f"/api/apps/{app_id}/transfer", json={"username": "nobody"}).status_code == 422
    body = harness.as_user("admin").post(f"/api/apps/{app_id}/transfer", json={"username": "MATE"}).json()
    assert body["owner"]["username"] == "mate"
    assert harness.as_user("mate").get(f"/api/apps/{app_id}").json()["is_owner"] is True
    assert harness.as_user("owner").get(f"/api/apps/{app_id}").status_code == 404
    assert harness.actions(app_id)[-1] == "app.owner_changed"


def test_delete_is_soft_and_tells_the_host(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    assert harness.as_user("owner").delete(f"/api/apps/{app_id}", params={"purge_data": "true"}).json() == {"ok": True}
    assert harness.host.deleted == [(app_id, True)]
    assert harness.row(app_id).deleted_at is not None
    assert harness.as_user("owner").get(f"/api/apps/{app_id}").status_code == 404
    assert harness.as_user("owner").delete(f"/api/apps/{app_id}").status_code == 404
    assert harness.actions(app_id)[-1] == "app.deleted"
    # The address stays taken.
    response = harness.as_user("owner").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "name": "x", "slug": "mind-map", "deploy": False})
    assert response.status_code == 409


# --- deployments --------------------------------------------------------------------------------


def test_deploy_deploy_latest_releases_and_rollback(harness):
    app_id = harness.create()["app"]["id"]
    first = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy", json={})
    assert first.status_code == 202, first.text
    release = first.json()
    assert release["status"] == "succeeded" and release["commit_sha"] == SHA_1 and release["is_current"] is True
    assert release["requested_by"]["username"] == "owner"
    assert [(step["source"], step["name"]) for step in release["steps"]][:4] == [("portal", "resolve"), ("portal", "manifest"), ("portal", "fetch"), ("host", "unpack")]
    assert release["log_tail"] == "started\n"

    harness.source.refs["main"] = SHA_2
    second = harness.as_user("admin").post(f"/api/apps/{app_id}/deploy-latest").json()
    assert second["commit_sha"] == SHA_2 and second["requested_by"]["username"] == "admin"

    listing = harness.as_user("owner").get(f"/api/apps/{app_id}/releases").json()
    assert [item["id"] for item in listing["releases"]] == [second["id"], release["id"]]
    assert listing["current_release_id"] == second["id"] and listing["rollback_release_id"] == release["id"]
    assert [item["is_current"] for item in listing["releases"]] == [True, False]

    one = harness.as_user("owner").get(f"/api/apps/{app_id}/releases/{release['id']}").json()
    assert one["id"] == release["id"] and one["error_summary"] is None
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/releases/nope").status_code == 404

    rolled = harness.as_user("owner").post(f"/api/apps/{app_id}/rollback")
    assert rolled.status_code == 200
    assert rolled.json()["kind"] == "rollback" and rolled.json()["commit_sha"] == SHA_1 and rolled.json()["is_current"] is True
    assert harness.row(app_id).current_release_id == rolled.json()["id"]


def test_deploying_another_ref_and_a_bad_one(harness):
    app_id = harness.create()["app"]["id"]
    harness.source.refs["v2"] = SHA_2
    release = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy", json={"ref": "v2"}).json()
    assert release["ref"] == "v2" and release["commit_sha"] == SHA_2
    assert harness.row(app_id).repo_ref == "v2"
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/deploy", json={"ref": "a b"}).status_code == 422
    # A ref that does not exist is a failed release, with the reason on its step.
    failed = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy", json={"ref": "gone"}).json()
    assert failed["status"] == "failed" and "gone" in failed["error_summary"]
    assert failed["steps"][0]["error"]["code"] == "ref_not_found"


def test_a_second_deployment_while_one_runs_is_a_conflict(harness):
    app_id = harness.create()["app"]["id"]
    harness.db.add(AppRelease(app_id=app_id, kind="deploy", status="running", steps_json="[]"))
    harness.db.commit()
    for path in ("/deploy", "/deploy-latest", "/rollback"):
        response = harness.as_user("owner").post(f"/api/apps/{app_id}{path}")
        assert response.status_code == 409 and response.json()["code"] == "deploy_in_progress"
    assert harness.as_user("owner").delete(f"/api/apps/{app_id}").status_code == 409
    body = harness.as_user("owner").get(f"/api/apps/{app_id}").json()
    assert body["manage"]["active_release"]["status"] == "running"


def test_rollback_without_an_earlier_release(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    response = harness.as_user("owner").post(f"/api/apps/{app_id}/rollback")
    assert response.status_code == 409 and response.json()["code"] == "no_previous_release"


def test_a_running_release_shows_the_hosts_live_log(harness):
    app_id = harness.create()["app"]["id"]
    harness.host.jobs["job-live"] = {"app_id": app_id, "sha": SHA_1}
    harness.host.job_states = [{"status": "running", "steps": [], "error": None, "log_tail": "Collecting fastapi\n", "log_size": 19}]
    harness.db.add(AppRelease(id="rel-live", app_id=app_id, kind="deploy", status="running", host_job_id="job-live", log_tail="older\n", steps_json="[]"))
    harness.db.commit()
    body = harness.as_user("owner").get(f"/api/apps/{app_id}/releases/rel-live").json()
    assert body["status"] == "running" and body["log_tail"] == "Collecting fastapi\n"

    # The host away: what the row last stored.
    harness.host.fail["get_job"] = AppHostUnavailable()
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/releases/rel-live").json()["log_tail"] == "older\n"

    harness.host.fail.clear()
    log = harness.as_user("owner").get(f"/api/apps/{app_id}/releases/rel-live/log", params={"offset": 11}).json()
    assert log == {"text": "started\n", "offset": 11, "size": 19}


def test_a_failed_deployment_reads_the_same_through_the_api(harness):
    app_id = harness.create()["app"]["id"]
    harness.host.job_states = [failed_job()]
    release = harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").json()
    assert release["status"] == "failed" and release["is_current"] is False
    assert "No matching distribution found" in release["error_summary"]
    assert harness.as_user("owner").get(f"/api/apps/{app_id}").json()["deployed"] is False


# --- running ------------------------------------------------------------------------------------


def test_start_stop_restart_and_status(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    harness.source.ahead = 2  # the branch moved on since the deployment
    stopped = harness.as_user("owner").post(f"/api/apps/{app_id}/stop")
    assert stopped.status_code == 200 and stopped.json()["desired_state"] == "stopped"
    started = harness.as_user("owner").post(f"/api/apps/{app_id}/start").json()
    assert started["desired_state"] == "running" and started["state"] == "running"
    assert harness.as_user("admin").post(f"/api/apps/{app_id}/restart").status_code == 200
    assert harness.actions(app_id)[-3:] == ["app.stopped", "app.started", "app.restarted"]

    status = harness.as_user("owner").get(f"/api/apps/{app_id}/status").json()
    assert status["host_reachable"] is True and status["live"] is True
    assert status["state"] == "running" and status["status"]["release"]["sha"] == SHA_1
    assert status["deployed"] is True and status["current_sha"] == SHA_1 and status["repo_ref"] == "main"
    assert status["commits_behind"] == 2
    assert status["enabled"] is True and status["deploying"] is False and status["rollback_available"] is False
    assert status["url"] == "https://app.example.com/mind-map/" and status["error"] is None


def test_status_degrades_when_the_host_is_away(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    harness.as_user("owner").get(f"/api/apps/{app_id}/status")
    harness.host.fail["get_app"] = AppHostUnavailable()
    response = harness.as_user("owner").get(f"/api/apps/{app_id}/status")
    assert response.status_code == 200
    status = response.json()
    assert status["host_reachable"] is False and status["live"] is False
    assert status["state"] == "running" and status["reported_at"] is not None  # the last report
    assert status["error"]["code"] == "host_unreachable"
    assert status["deployed"] is True and status["current_sha"] == SHA_1

    # And when GitHub is away too, the figure is simply unknown.
    harness.api.deploy_service._behind_cache.clear()
    from app.services.apps.errors import SourceUnavailable

    harness.source.fail["commits_ahead"] = SourceUnavailable(reason="HTTP 502")
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/status").json()["commits_behind"] is None


def test_status_of_an_app_that_was_never_deployed(harness):
    app_id = harness.create()["app"]["id"]
    status = harness.as_user("owner").get(f"/api/apps/{app_id}/status").json()
    assert status["host_reachable"] is True and status["status"] is None and status["state"] is None
    assert status["deployed"] is False and status["commits_behind"] is None
    response = harness.as_user("owner").post(f"/api/apps/{app_id}/start")
    assert response.status_code == 409 and response.json()["code"] == "not_deployed"


def test_lifecycle_when_the_host_is_away(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    harness.host.fail["restart"] = AppHostUnavailable()
    response = harness.as_user("owner").post(f"/api/apps/{app_id}/restart")
    assert response.status_code == 503 and response.json()["code"] == "host_unreachable"
    assert harness.actions(app_id)[-1] != "app.restarted"


def test_logs_tail(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    response = harness.as_user("owner").get(f"/api/apps/{app_id}/logs", params={"tail": 50})
    assert response.json() == {"lines": ["line one", "line two"], "truncated": False}
    assert ("logs", app_id, 50) in harness.host.calls
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/logs", params={"tail": 0}).status_code == 422
    harness.host.fail["logs"] = AppHostUnavailable()
    assert harness.as_user("owner").get(f"/api/apps/{app_id}/logs").status_code == 503


def test_the_log_stream_is_passed_through(harness, monkeypatch):
    app_id = harness.create(deploy=True)["app"]["id"]
    events = b'data: {"line": "started"}\n\n: keepalive\n\ndata: {"line": "ready"}\n\n'
    seen = []

    async def upstream(request):
        seen.append((request.url.path, request.headers["authorization"]))
        return httpx.Response(200, content=events, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(harness.api, "host_client", lambda: AppHostClient(base_url="http://host.test", token="host-token", async_transport=httpx.MockTransport(upstream)))
    response = harness.as_user("owner").get(f"/api/apps/{app_id}/logs/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == events
    assert seen == [(f"/v1/apps/{app_id}/logs/stream", "Bearer host-token")]

    async def refused(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(harness.api, "host_client", lambda: AppHostClient(base_url="http://host.test", token="host-token", async_transport=httpx.MockTransport(refused)))
    response = harness.as_user("owner").get(f"/api/apps/{app_id}/logs/stream")
    assert response.status_code == 503 and response.json()["code"] == "host_unreachable"


# --- token, icon, data, history -----------------------------------------------------------------


def test_token_view_and_reset(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    view = harness.as_user("owner").get(f"/api/apps/{app_id}/token")
    assert view.status_code == 200 and view.headers["cache-control"] == "no-store"
    token = view.json()["token"]
    assert token == harness.row(app_id).token_value and token.startswith("efpa_")
    assert view.json()["host_synced"] is None

    reset = harness.as_user("owner").post(f"/api/apps/{app_id}/token/reset")
    assert reset.status_code == 200 and reset.headers["cache-control"] == "no-store"
    body = reset.json()
    assert body["token"] != token and body["host_synced"] is True and body["host_error"] is None
    assert harness.host.env[app_id]["EFP_APP_TOKEN"] == body["token"]
    assert harness.actions(app_id)[-2:] == ["token.viewed", "token.reset"]
    # Neither value is written into the audit trail.
    trail = " ".join(item.detail_json or "" for item in harness.db.query(AppAuditEvent))
    assert token not in trail and body["token"] not in trail

    harness.host.fail["put_env"] = AppHostUnavailable()
    away = harness.as_user("admin").post(f"/api/apps/{app_id}/token/reset").json()
    assert away["host_synced"] is False and away["host_error"]["code"] == "host_unreachable"
    assert away["token"] == harness.row(app_id).token_value


def test_icon_is_served_cacheable_and_only_to_those_who_may_open_the_app(harness):
    app_id = harness.create(visibility="public", deploy=True)["app"]["id"]
    response = harness.as_user("mate").get(f"/api/apps/{app_id}/icon")
    assert response.status_code == 200
    assert response.content == PNG and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"
    etag = response.headers["etag"]
    again = harness.as_user("mate").get(f"/api/apps/{app_id}/icon", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    assert harness.as_user("other").get(f"/api/apps/{app_id}/icon").status_code == 404

    plain = harness.create(name="No icon")["app"]
    assert plain["has_icon"] is False and plain["icon_url"] is None
    response = harness.as_user("owner").get(f"/api/apps/{plain['id']}/icon")
    assert response.status_code == 404 and response.json()["code"] == "icon_not_found"


def test_data_size_and_clear(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    size = harness.as_user("owner").get(f"/api/apps/{app_id}/data").json()
    assert size["data_bytes"] == 1024 and size["host_reachable"] is True and size["live"] is True
    cleared = harness.as_user("owner").delete(f"/api/apps/{app_id}/data")
    assert cleared.status_code == 200 and cleared.json()["data_bytes"] == 0
    assert harness.actions(app_id)[-1] == "app.data_cleared"

    harness.host.fail["get_app"] = AppHostUnavailable()
    away = harness.as_user("owner").get(f"/api/apps/{app_id}/data").json()
    assert away["host_reachable"] is False and away["live"] is False and away["data_bytes"] == 0
    harness.host.fail["delete_data"] = AppHostUnavailable()
    assert harness.as_user("owner").delete(f"/api/apps/{app_id}/data").status_code == 503
    assert harness.actions(app_id).count("app.data_cleared") == 1


def test_events_list_names_who_did_what(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    harness.as_user("admin").patch(f"/api/apps/{app_id}", json={"visibility": "public"})
    events = harness.as_user("owner").get(f"/api/apps/{app_id}/events").json()["events"]
    assert [event["action"] for event in events] == ["app.visibility_changed", "deploy.succeeded", "deploy.requested", "app.created"]
    assert events[0]["actor"]["username"] == "admin" and events[0]["detail"]["to"] == "public"
    assert events[-1]["actor"]["username"] == "owner"
    assert len(harness.as_user("owner").get(f"/api/apps/{app_id}/events", params={"limit": 2}).json()["events"]) == 2


# --- settings, inspection ---------------------------------------------------------------------------


def test_settings(harness):
    body = harness.as_user("mate").get("/api/apps/settings").json()
    assert body["public_base_url"] == "https://app.example.com"
    assert body["anyone_enabled"] is False and body["visibilities"] == ["private", "public", "platform"]
    assert body["github_host"] == "github.com" and body["service_account"] == "efp-bot"
    assert body["connector_types"] == ["jira", "confluence", "github", "jenkins", "nexus", "splunk"]
    assert body["app_types"] == ["fastapi", "flask", "streamlit", "gradio", "command", "static"]
    assert "login" in body["reserved_slugs"] and "_efp" in body["reserved_slugs"]
    assert body["default_daily_credits"] == 50.0 and body["default_daily_tokens"] == 2000000


def test_inspect_reads_a_directory_the_way_registering_would(harness):
    harness.create(slug="mind-map")
    response = harness.as_user("owner").post("/api/apps/inspect", json={"repo_url": REPO_URL + ".git", "ref": "main", "entry_dir": "/apps/mindmap/"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["repo"] == "acme/tools" and body["repo_url"] == REPO_URL
    assert body["commit_sha"] == SHA_1 and body["entry_dir"] == "apps/mindmap"
    assert body["manifest"]["name"] == "Mind map" and body["manifest"]["type"] == "fastapi" and body["manifest"]["owners"] == ["owner"]
    assert body["suggested_name"] == "Mind map" and body["suggested_slug"] == "mind-map-2"
    assert harness.db.query(App).count() == 1  # nothing was registered

    refused = harness.as_user("other").post("/api/apps/inspect", json={"repo_url": REPO_URL, "ref": "main"})
    assert refused.status_code == 403 and refused.json()["code"] == "not_listed_as_owner"
    harness.source.manifest = {"name": "x", "type": "rails"}
    invalid = harness.as_user("owner").post("/api/apps/inspect", json={"repo_url": REPO_URL, "ref": "main"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "manifest_invalid" and invalid.json()["problems"][0]["message_key"]


def test_repo_refs(harness):
    body = harness.as_user("owner").get("/api/apps/repo-refs", params={"repo_url": REPO_URL}).json()
    assert body == {"repo": "acme/tools", "default_branch": "main", "branches": ["main"], "tags": ["v1.0.0"]}
    assert harness.as_user("owner").get("/api/apps/repo-refs", params={"repo_url": "nope"}).status_code == 422


def test_a_manifest_nested_too_deeply_is_a_problem_of_the_file_not_an_error_of_the_portal(harness):
    harness.source.manifest = b"[" * 60000
    looked = harness.as_user("owner").post("/api/apps/inspect", json={"repo_url": REPO_URL, "ref": "main"})
    assert looked.status_code == 422, looked.text
    assert looked.json()["code"] == "manifest_invalid" and "nested too deeply" in looked.json()["detail"]
    created = harness.as_user("owner").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main"})
    assert created.status_code == 422 and created.json()["code"] == "manifest_invalid"


def test_reading_unregistered_repositories_is_limited_per_member(harness):
    from app.services.apps.rate_limit import REPO_READS_PER_MINUTE

    # The two routes share one allowance: both are calls of the platform's one service account.
    for number in range(REPO_READS_PER_MINUTE):
        if number % 2:
            response = harness.as_user("owner").get("/api/apps/repo-refs", params={"repo_url": REPO_URL})
        else:
            response = harness.as_user("owner").post("/api/apps/inspect", json={"repo_url": REPO_URL, "ref": "main"})
        assert response.status_code == 200, response.text
    asked = len(harness.source.calls)

    for response in (
        harness.as_user("owner").get("/api/apps/repo-refs", params={"repo_url": REPO_URL}),
        harness.as_user("owner").post("/api/apps/inspect", json={"repo_url": REPO_URL, "ref": "main"}),
    ):
        assert response.status_code == 429
        body = response.json()
        assert body["code"] == "rate_limited" and body["message_key"] == "You are reading repositories too often. Try again in {seconds} seconds."
        assert 1 <= body["params"]["seconds"] <= 60
    # Refused before GitHub was asked anything.
    assert len(harness.source.calls) == asked

    # Another member has an allowance of their own, and registering an app is not counted.
    assert harness.as_user("mate").get("/api/apps/repo-refs", params={"repo_url": REPO_URL}).status_code == 200
    assert harness.as_user("owner").post("/api/apps", json={"repo_url": REPO_URL, "ref": "main", "deploy": False}).status_code == 201


# --- what a list carries and what only the app's own answer does ---------------------------------


def test_lists_name_the_variables_and_only_the_apps_own_answer_carries_their_values(harness):
    app_id = harness.create(deploy=True, visibility="platform")["app"]["id"]
    secret = "s3cr3t-value-nobody-lists"
    patched = harness.as_user("owner").patch(f"/api/apps/{app_id}", json={"env": {"API_KEY": secret, "API_BASE": "https://x"}})
    # The answers about this one app carry the values: its change, its read, the answers of the other calls that change it.
    assert patched.status_code == 200 and patched.json()["manage"]["env"] == {"API_KEY": secret, "API_BASE": "https://x"}
    for reader in ("owner", "admin"):
        manage = harness.as_user(reader).get(f"/api/apps/{app_id}").json()["manage"]
        assert manage["env"] == {"API_KEY": secret, "API_BASE": "https://x"}
        assert manage["env_names"] == ["API_BASE", "API_KEY"]
    template = harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": True})
    assert template.status_code == 200 and template.json()["manage"]["env"]["API_KEY"] == secret
    disabled = harness.as_user("admin").post(f"/api/apps/{app_id}/disable", json={"reason": "Paused"})
    assert disabled.json()["manage"]["env"]["API_KEY"] == secret
    assert harness.as_user("admin").post(f"/api/apps/{app_id}/enable").json()["manage"]["env"]["API_KEY"] == secret

    # Every list names the variables and leaves the values out, for the owner and for an administrator alike.
    lists = (
        harness.as_user("owner").get("/api/apps", params={"scope": "mine"}),
        harness.as_user("admin").get("/api/apps", params={"scope": "all"}),
        harness.as_user("admin").get("/api/apps/admin/overview"),
    )
    for response in lists:
        assert response.status_code == 200, response.text
        assert secret not in response.text
        (item,) = response.json()["apps"]
        assert item["manage"]["env"] is None and item["manage"]["env_names"] == ["API_BASE", "API_KEY"]
    # A template's card is a list entry too.
    harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.0.0"})
    templates = harness.as_user("admin").get("/api/apps/templates")
    assert templates.status_code == 200 and secret not in templates.text
    assert templates.json()["templates"][0]["manage"]["env"] is None

    # Whoever may not manage the app reads neither.
    for reader in ("mate", "other"):
        shared = harness.as_user(reader).get(f"/api/apps/{app_id}")
        assert shared.status_code == 200 and shared.json()["manage"] is None and secret not in shared.text
    available = harness.as_user("mate").get("/api/apps", params={"scope": "available"})
    assert available.json()["apps"][0]["manage"] is None and secret not in available.text


def test_the_commit_and_the_branch_that_run_are_for_whoever_may_manage_the_app(harness):
    app_id = harness.create(deploy=True, visibility="public")["app"]["id"]
    for manager in ("owner", "admin"):
        body = harness.as_user(manager).get(f"/api/apps/{app_id}").json()
        assert body["current_release"]["commit_sha"] == SHA_1 and body["current_release"]["ref"] == "main"

    # A member the app is shared with opens it; what it was built from is not theirs to read.
    body = harness.as_user("mate").get(f"/api/apps/{app_id}").json()
    assert body["can_manage"] is False and body["deployed"] is True
    assert body["current_release"] is None and body["manage"] is None
    assert SHA_1 not in harness.as_user("mate").get(f"/api/apps/{app_id}").text
    listed = harness.as_user("mate").get("/api/apps", params={"scope": "available"})
    (item,) = listed.json()["apps"]
    assert item["deployed"] is True and item["current_release"] is None and SHA_1 not in listed.text


def test_the_icon_is_versioned_and_revalidated_without_its_bytes(harness):
    import hashlib

    from sqlalchemy import event

    app = harness.create(visibility="public", deploy=True)["app"]
    digest = hashlib.sha1(PNG).hexdigest()
    assert app["has_icon"] is True and app["icon_url"] == f"/api/apps/{app['id']}/icon?v={digest[:12]}"
    assert harness.row(app["id"]).icon_hash == digest

    statements = []

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = harness.db.get_bind()
    event.listen(engine, "before_cursor_execute", before)
    try:
        # A list draws the card, the version in its address included, without reading one icon.
        listed = harness.as_user("mate").get("/api/apps", params={"scope": "available"}).json()["apps"][0]
        assert listed["has_icon"] is True and listed["icon_url"] == app["icon_url"]
        assert not any("icon_data" in statement for statement in statements)
        # A browser that has the icon is told so from the stored hash.
        unchanged = harness.as_user("mate").get(f"/api/apps/{app['id']}/icon", headers={"If-None-Match": f'"{digest}"'})
        assert unchanged.status_code == 304 and unchanged.headers["etag"] == f'"{digest}"'
        assert not any("icon_data" in statement for statement in statements)
        # One that has not gets the bytes.
        served = harness.as_user("mate").get(f"/api/apps/{app['id']}/icon")
        assert served.status_code == 200 and served.content == PNG and served.headers["etag"] == f'"{digest}"'
        assert any("icon_data" in statement for statement in statements)
    finally:
        event.remove(engine, "before_cursor_execute", before)


def test_a_disabled_app_is_not_deployed_through_the_api(harness):
    app_id = harness.create(deploy=True)["app"]["id"]
    harness.source.refs["main"] = SHA_2
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/deploy").status_code == 202
    assert harness.as_user("admin").post(f"/api/apps/{app_id}/disable", json={"reason": "Taken down"}).status_code == 200
    harness.host.calls.clear()

    for path in ("/deploy", "/deploy-latest", "/rollback", "/start", "/restart"):
        response = harness.as_user("owner").post(f"/api/apps/{app_id}{path}")
        assert response.status_code == 409, (path, response.text)
        assert response.json()["code"] == "app_disabled" and response.json()["params"]["reason"] == "Taken down"
    assert harness.host.calls == []
    assert harness.db.query(AppRelease).filter(AppRelease.app_id == app_id).count() == 2
