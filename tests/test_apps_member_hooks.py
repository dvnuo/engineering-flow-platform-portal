"""What the rest of the Portal tells the Apps feature: sign-out, a member losing or regaining access."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from _apps_helpers import (
    REPO_URL,
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
from app.security.session_cookie import session_cookie_name
from app.deps import get_current_user
from app.models import AppAuditEvent, UserAllowlistEntry
from app.services.apps import member_hooks
from app.services.apps.app_service import AppService
from app.services.apps.errors import AppHostUnavailable

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(monkeypatch):
    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    group = add_group(db, "Default", is_default=True)
    owner = add_user(db, "owner", group=group)
    admin = add_user(db, "admin", role="admin", group=group)
    host = FakeHost()
    source = FakeSource()
    service = AppService(db, host=host, source=source)
    deploys = make_deploy_service(factory, host, source)
    deployed = service.create(owner, repo_url=REPO_URL, ref="main", name="Mind map", visibility="platform")
    deploys.start_deploy(deployed, None, owner)
    draft = service.create(owner, repo_url=REPO_URL, ref="main", name="Draft")
    db.refresh(deployed)
    host.calls.clear()

    # The hooks run in the background; here they run at once, against the fakes.
    spawned = []

    def run_now(target, name):
        spawned.append(name)
        target()

    monkeypatch.setattr(member_hooks, "_spawn", run_now)
    try:
        yield SimpleNamespace(db=db, factory=factory, owner=owner, admin=admin, host=host, service=service, deployed=deployed, draft=draft, spawned=spawned, group=group)
    finally:
        db.close()
        reset_apps_state()


# --- sign-out ---------------------------------------------------------------------------


def _sign_out(monkeypatch, world, *, cookie="session-token", user_id=None, revoke=None):
    from app.main import app
    import app.services.auth_service as auth_service

    calls = []
    monkeypatch.setattr(auth_service, "parse_session_token", lambda token: user_id if token == "session-token" else None)
    monkeypatch.setattr(member_hooks, "revoke_app_sessions", revoke or (lambda uid, **_kwargs: calls.append(uid) or True))
    client = TestClient(app)
    if cookie:
        from app.config import get_settings

        client.cookies.set(session_cookie_name(), cookie)
    response = client.post("/api/auth/logout")
    return response, calls


def test_sign_out_ends_the_members_app_sessions(world, monkeypatch):
    response, calls = _sign_out(monkeypatch, world, user_id=world.owner.id)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert calls == [world.owner.id]
    assert world.spawned == ["app-sessions-revoke"]
    # The Portal's own cookie is cleared as before.
    assert "portal_session=" in response.headers["set-cookie"] or "session" in response.headers["set-cookie"]


def test_sign_out_without_a_session_tells_nobody(world, monkeypatch):
    response, calls = _sign_out(monkeypatch, world, cookie=None, user_id=world.owner.id)
    assert response.status_code == 200 and calls == []
    response, calls = _sign_out(monkeypatch, world, cookie="forged", user_id=world.owner.id)
    assert response.status_code == 200 and calls == []


def test_sign_out_does_nothing_for_apps_while_they_are_off(world, monkeypatch):
    enable_apps(monkeypatch, apps_enabled=False)
    response, calls = _sign_out(monkeypatch, world, user_id=world.owner.id)
    assert response.status_code == 200 and calls == [] and world.spawned == []


def test_sign_out_never_fails_because_of_the_host(world, monkeypatch):
    def boom(uid, **_kwargs):
        raise RuntimeError("the host fell over")

    # Even a hook that throws leaves the sign-out as it was.
    monkeypatch.setattr(member_hooks, "_spawn", lambda target, name: (_ for _ in ()).throw(RuntimeError("no threads")))
    response, _calls = _sign_out(monkeypatch, world, user_id=world.owner.id, revoke=boom)
    assert response.status_code == 200 and response.json() == {"ok": True}


def test_revoking_sessions_is_best_effort(world):
    assert member_hooks.revoke_app_sessions(world.owner.id, host=world.host) is True
    assert world.host.revoked == [world.owner.id]
    world.host.fail["revoke_sessions"] = AppHostUnavailable()
    assert member_hooks.revoke_app_sessions(world.owner.id, host=world.host) is False

    class Broken:
        def revoke_sessions(self, user_id):
            raise ValueError("unexpected")

    assert member_hooks.revoke_app_sessions(world.owner.id, host=Broken()) is False


def test_the_sign_out_route_is_hooked_by_one_dependency():
    source = (REPO / "app" / "api" / "auth.py").read_text(encoding="utf-8")
    assert '@router.post("/logout", dependencies=[Depends(end_app_sessions_on_sign_out)])' in source


# --- a member loses or regains access ---------------------------------------------------------


def _sync(world, **kwargs):
    options = {"session_factory": world.factory, "host": world.host, "sleep": lambda seconds: None}
    options.update(kwargs)
    return member_hooks.sync_member_apps("owner", **options)


def test_an_owner_without_access_has_their_apps_disabled_on_the_host(world):
    remove_from_allowlist(world.db, "owner")
    assert _sync(world) == {"apps": 1, "synced": 1, "failed": 0}
    spec = world.host.specs[world.deployed.id]
    assert spec["enabled"] is False
    assert "owner" in spec["disabled_reason"].lower() and "no longer has access" in spec["disabled_reason"]
    # An app the host has never seen is left alone, and their own app sessions end.
    assert world.draft.id not in world.host.specs
    assert world.host.revoked == [world.owner.id]
    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.owner_inactive").one()
    assert event.app_id == world.deployed.id and event.actor_user_id is None and event.detail == {"host_synced": True}


def test_an_owner_let_back_in_has_their_apps_enabled_again(world):
    remove_from_allowlist(world.db, "owner")
    _sync(world)
    world.db.add(UserAllowlistEntry(username="owner", role="user", is_active=True))
    world.db.commit()
    world.host.revoked.clear()
    assert _sync(world) == {"apps": 1, "synced": 1, "failed": 0}
    spec = world.host.specs[world.deployed.id]
    assert spec["enabled"] is True and spec["disabled_reason"] is None
    assert world.host.revoked == []
    assert world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.owner_active").count() == 1


def test_an_app_an_administrator_took_down_stays_down_when_its_owner_returns(world):
    world.service.disable(world.deployed, world.admin, "Under review")
    remove_from_allowlist(world.db, "owner")
    _sync(world)
    world.db.add(UserAllowlistEntry(username="owner", role="user", is_active=True))
    world.db.commit()
    _sync(world)
    spec = world.host.specs[world.deployed.id]
    assert spec["enabled"] is False and spec["disabled_reason"] == "Under review"


def test_a_host_that_is_away_is_tried_again_and_the_failure_is_recorded(world):
    remove_from_allowlist(world.db, "owner")
    waits = []
    world.host.fail["put_app"] = AppHostUnavailable()
    assert _sync(world, attempts=3, sleep=waits.append) == {"apps": 1, "synced": 0, "failed": 1}
    assert world.host.names().count("put_app") == 3 and len(waits) == 2
    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.owner_inactive").one()
    assert event.detail == {"host_synced": False}
    # The Portal's own answers to the host do not wait for that push.
    from app.services.apps.access import app_enabled

    world.db.expire_all()
    assert app_enabled(world.db, world.deployed) == (False, "owner_inactive")


def test_an_unknown_member_or_one_without_apps_is_nothing_to_do(world):
    assert member_hooks.sync_member_apps("nobody", session_factory=world.factory, host=world.host) == {"apps": 0, "synced": 0, "failed": 0}
    assert member_hooks.sync_member_apps("admin", session_factory=world.factory, host=world.host) == {"apps": 0, "synced": 0, "failed": 0}
    assert world.host.calls == []


def test_the_hook_returns_at_once_and_is_silent_while_apps_are_off(world, monkeypatch):
    seen = []
    monkeypatch.setattr(member_hooks, "sync_member_apps", lambda username, **_kwargs: seen.append(username))
    member_hooks.member_access_changed("owner")
    member_hooks.member_access_changed("")
    member_hooks.member_access_changed(None)
    assert seen == ["owner"] and world.spawned == ["app-owner-sync"]

    enable_apps(monkeypatch, apps_enabled=False)
    member_hooks.member_access_changed("owner")
    assert seen == ["owner"]

    # A hook that cannot even start a thread never reaches the caller.
    enable_apps(monkeypatch, apps_enabled=True)
    monkeypatch.setattr(member_hooks, "_spawn", lambda target, name: (_ for _ in ()).throw(RuntimeError("no threads")))
    member_hooks.member_access_changed("owner")


# --- where the Portal calls the hooks ---------------------------------------------------------


@pytest.fixture
def users_api(world, monkeypatch):
    from app.main import app

    seen = []
    monkeypatch.setattr("app.api.users.member_access_changed", lambda username: seen.append(username))

    def override_db():
        session = world.factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: world.admin
    try:
        yield SimpleNamespace(client=TestClient(app), seen=seen)
    finally:
        app.dependency_overrides.clear()


def test_removing_a_member_from_the_allowlist_calls_the_hook(world, users_api):
    entry = world.db.query(UserAllowlistEntry).filter(UserAllowlistEntry.username == "owner").one()
    response = users_api.client.delete(f"/api/users/allowlist/{entry.id}")
    assert response.status_code == 200, response.text
    assert users_api.seen == ["owner"]


def test_revoking_a_members_access_calls_the_hook(world, users_api):
    response = users_api.client.delete(f"/api/users/{world.owner.id}")
    assert response.status_code == 200, response.text
    assert users_api.seen == ["owner"]


def test_putting_a_member_back_calls_the_hook(world, users_api):
    remove_from_allowlist(world.db, "owner")
    response = users_api.client.post("/api/users/allowlist", json={"username": "owner", "role": "user", "group_id": world.group.id})
    assert response.status_code == 200, response.text
    assert users_api.seen == ["owner"]


def test_a_refused_removal_does_not_call_the_hook(world, users_api):
    entry = world.db.query(UserAllowlistEntry).filter(UserAllowlistEntry.username == "admin").one()
    response = users_api.client.delete(f"/api/users/allowlist/{entry.id}")
    assert response.status_code == 409
    assert users_api.seen == []


def test_end_to_end_a_removed_owner_takes_the_app_down(world, monkeypatch):
    """The real hook behind the real route: the host hears of it without anyone deploying."""
    from app.main import app

    real = member_hooks.sync_member_apps
    # The real work, with the test's database and host in place of the Portal's own.
    monkeypatch.setattr(
        member_hooks,
        "sync_member_apps",
        lambda username: real(username, session_factory=world.factory, host=world.host, sleep=lambda seconds: None),
    )

    def override_db():
        session = world.factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: world.admin
    try:
        response = TestClient(app).delete(f"/api/users/{world.owner.id}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert world.host.specs[world.deployed.id]["enabled"] is False
    assert world.host.revoked == [world.owner.id]
