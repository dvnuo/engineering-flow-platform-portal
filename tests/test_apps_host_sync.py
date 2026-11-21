"""The host is told again what every app is: at the Portal's start and on the purge worker's round."""

from datetime import datetime, timedelta
import threading

import pytest

from _apps_helpers import (
    REPO_URL,
    SHA_1,
    FakeHost,
    FakeSource,
    add_user,
    enable_apps,
    make_database,
    make_deploy_service,
    remove_from_allowlist,
    reset_apps_state,
)
from app.models import App, AppRelease
from app.services.apps import host_sync, member_hooks
from app.services.apps.app_service import AppService
from app.services.apps.errors import AppHostError, AppHostUnavailable, SourceUnavailable
from app.services.apps.host_spec import host_spec
from app.services.apps.host_sync import sync_host_apps
from app.services.apps.member_hooks import sync_member_apps
from app.services.apps.purge_worker import AppsPurgeWorker

NOTHING = {"apps": 0, "synced": 0, "failed": 0, "adopted": 0}


@pytest.fixture
def world(monkeypatch):
    enable_apps(monkeypatch, apps_anyone_enabled=True)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    owner = add_user(db, "owner")
    admin = add_user(db, "admin", role="admin")
    host = FakeHost()
    source = FakeSource()
    service = AppService(db, host=host, source=source)
    deploys = make_deploy_service(factory, host, source)

    class World:
        pass

    w = World()
    w.db, w.factory, w.owner, w.admin, w.host, w.source, w.service, w.deploys = db, factory, owner, admin, host, source, service, deploys

    def create(name, **fields):
        return service.create(owner, repo_url=REPO_URL, ref="main", entry_dir="apps/mindmap", name=name, **fields)

    def deployed(name, **fields):
        app = create(name, **fields)
        deploys.start_deploy(app, None, owner)
        db.refresh(app)
        assert app.current_release_id
        return app

    def sync(**kwargs):
        return sync_host_apps(session_factory=factory, host=host, **kwargs)

    w.create, w.deployed, w.sync = create, deployed, sync
    try:
        yield w
    finally:
        db.close()
        reset_apps_state()


def _puts(host):
    return [call[1] for call in host.calls if call[0] == "put_app"]


# --- what a round sends ---------------------------------------------------------------


def test_every_app_the_host_knows_is_sent_again_and_nothing_else(world):
    first = world.deployed("First")
    second = world.deployed("Second")
    draft = world.create("Draft")  # nobody deployed it: its first deployment will send it
    gone = world.deployed("Gone")
    world.service.delete(gone, world.owner)
    # A deployment that failed before the host ran anything still made the host hear of the app.
    tried = world.create("Tried")
    world.source.fail["download_tarball"] = SourceUnavailable(reason="HTTP 502")
    world.deploys.start_deploy(tried, None, world.owner)
    del world.source.fail["download_tarball"]
    world.host.calls.clear()

    assert world.sync() == {"apps": 3, "synced": 3, "failed": 0, "adopted": 0}
    assert sorted(_puts(world.host)) == sorted([first.id, second.id, tried.id])
    assert draft.id not in world.host.specs and gone.id not in world.host.specs
    for app in (first, second, tried):
        assert world.host.specs[app.id] == host_spec(world.db, app)
    # Told once each, and asked for nothing more: no deployment, no start, no deletion.
    assert world.host.names() == ["put_app"] * 3


def test_turning_apps_without_sign_in_off_reaches_the_host(world, monkeypatch):
    app = world.deployed("Open")
    world.service.update(app, world.admin, visibility="anyone")
    assert world.host.specs[app.id]["visibility"] == "anyone"

    # The switch is turned off and the Portal restarted: nothing was changed on any app.
    enable_apps(monkeypatch, apps_anyone_enabled=False)
    assert world.host.specs[app.id]["visibility"] == "anyone"
    world.sync()
    assert world.host.specs[app.id]["visibility"] == "platform"


def test_an_owner_who_lost_access_while_the_host_was_away_is_caught_up_with(world):
    app = world.deployed("Open")
    world.service.update(app, world.admin, visibility="anyone")
    remove_from_allowlist(world.db, "owner")
    world.host.fail["put_app"] = AppHostUnavailable()
    assert sync_member_apps("owner", session_factory=world.factory, host=world.host, sleep=lambda _seconds: None)["failed"] == 1
    # The gateway serves an app open to anyone from this record alone.
    assert world.host.specs[app.id]["enabled"] is True

    del world.host.fail["put_app"]
    assert world.sync()["synced"] == 1
    assert world.host.specs[app.id]["enabled"] is False
    assert "no longer has access" in world.host.specs[app.id]["disabled_reason"]


def test_a_host_that_does_not_answer_ends_the_round(world):
    for name in ("One", "Two", "Three"):
        world.deployed(name)
    world.host.calls.clear()
    world.host.fail["put_app"] = AppHostUnavailable()
    # One timeout, not one per app: the next round says it all again.
    assert world.sync() == {"apps": 3, "synced": 0, "failed": 3, "adopted": 0}
    assert world.host.names() == ["put_app"]


def test_an_app_the_host_refuses_does_not_cost_the_others_their_turn(world):
    refused = world.deployed("One")
    kept = world.deployed("Two")
    original = world.host.put_app

    def put_app(app_id, spec):
        if app_id == refused.id:
            raise AppHostError(code="host_slug_taken", reason="slug_taken", host_code="slug_taken", host_status=409)
        return original(app_id, spec)

    world.host.put_app = put_app
    world.host.calls.clear()
    assert world.sync() == {"apps": 2, "synced": 1, "failed": 1, "adopted": 0}
    assert _puts(world.host) == [kept.id]


def test_whatever_goes_wrong_the_round_never_raises(world, caplog):
    import logging

    caplog.set_level(logging.CRITICAL)
    world.deployed("One")
    world.host.fail["put_app"] = RuntimeError("boom")
    assert world.sync() == {"apps": 1, "synced": 0, "failed": 0, "adopted": 0}

    def broken_factory():
        raise RuntimeError("no database")

    assert sync_host_apps(session_factory=broken_factory, host=world.host) == NOTHING


def test_nothing_at_all_while_apps_are_off(world, monkeypatch):
    world.deployed("One")
    world.host.calls.clear()
    enable_apps(monkeypatch, apps_enabled=False)

    def no_session():
        raise AssertionError("the database was opened although Apps are off")

    assert sync_host_apps(session_factory=no_session, host=world.host, after_start=True) == NOTHING
    assert world.host.calls == []
    # The worker's errands are as quiet.
    worker = AppsPurgeWorker(no_session, host_factory=lambda: world.host)
    assert worker.sync_host() == NOTHING
    assert worker.settle_releases() == {"resumed": 0, "failed": 0}
    assert world.host.calls == []


# --- a change during the call -------------------------------------------------------


def test_a_change_saved_while_the_host_was_being_told_is_sent_again(world):
    app = world.deployed("Shared", visibility="platform")
    original = world.host.put_app
    told = []

    def put_app(app_id, spec):
        told.append(spec["visibility"])
        if len(told) == 1:
            # The owner narrows the audience at this very moment, from another request.
            other = world.factory()
            other.get(App, app_id).visibility = "private"
            other.commit()
            other.close()
        return original(app_id, spec)

    world.host.put_app = put_app
    assert world.sync()["synced"] == 1
    # What was read before the change did not stay on the host.
    assert told == ["platform", "private"] and world.host.specs[app.id]["visibility"] == "private"


def test_an_app_deleted_while_the_host_was_being_told_is_taken_off_again(world):
    app = world.deployed("Going")
    app_id = app.id
    original = world.host.put_app

    def put_app(target_id, spec):
        answer = original(target_id, spec)
        # Deleted by its owner between the read and the call: the call has just put the record back.
        other = world.factory()
        row = other.get(App, target_id)
        row.deleted_at = datetime.utcnow()
        other.commit()
        other.close()
        return answer

    world.host.put_app = put_app
    assert world.sync() == {"apps": 1, "synced": 0, "failed": 0, "adopted": 0}
    assert world.host.deleted == [(app_id, False)] and app_id not in world.host.specs


# --- what a round finds out ---------------------------------------------------------


def test_a_release_the_host_finished_after_the_portal_gave_up_is_adopted_on_the_round(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    app = world.create("Late")
    monkeypatch.setattr(deploy_module, "POLL_MAX_UNREACHABLE", 0)
    world.host.job_states = [AppHostUnavailable()]
    release = world.deploys.start_deploy(app, None, world.owner)
    world.db.refresh(release)
    assert release.status == "failed"
    world.host.job_states = None
    world.host.get_job(release.host_job_id)  # the host finishes the job

    assert world.sync() == {"apps": 1, "synced": 1, "failed": 0, "adopted": 1}
    world.db.refresh(release)
    world.db.refresh(app)
    assert release.status == "succeeded" and app.current_release_id == release.id
    assert world.sync() == {"apps": 1, "synced": 1, "failed": 0, "adopted": 0}


# --- after a start --------------------------------------------------------------------


def test_after_a_start_deletions_a_stopped_portal_left_half_done_are_finished(world):
    # Marked deleted, and the Portal stopped before it had called the host.
    left = world.deployed("Left")
    left.deleted_at = datetime.utcnow() - timedelta(minutes=5)
    left.desired_state = "stopped"
    # Deleted properly and its data removed long ago: the host is not asked about it again.
    purged = world.deployed("Purged")
    world.service.delete(purged, world.owner, purge_data=True)
    alive = world.deployed("Alive")
    world.db.commit()
    world.host.calls.clear()
    world.host.deleted.clear()

    # A round of the worker does not look for them.
    world.sync()
    assert world.host.deleted == [] and left.id in world.host.specs

    world.sync(after_start=True)
    assert world.host.deleted == [(left.id, False)]
    assert left.id not in world.host.specs and alive.id in world.host.specs


def test_the_start_hands_the_round_to_a_thread_and_is_not_held_up_by_it(world, monkeypatch):
    from app.services.apps.purge_worker import apps_purge_worker_singleton

    monkeypatch.setattr(apps_purge_worker_singleton, "start", lambda: None)
    monkeypatch.setattr("app.services.apps.deploy.deploy_service", world.deploys)
    asked = []
    monkeypatch.setattr(host_sync, "sync_host_apps", lambda **kwargs: asked.append(kwargs) or dict(NOTHING))

    # The start only hands the round over ...
    handed = []
    monkeypatch.setattr(member_hooks, "_spawn", lambda target, name: handed.append((target, name)))
    member_hooks.apps_startup()
    assert asked == [] and [name for _target, name in handed] == ["apps-host-sync"]
    # ... and what it handed over is the round that also finishes half-done deletions.
    handed[0][0]()
    assert asked == [{"after_start": True}]

    # A thread that cannot be started does not fail the start.
    def no_threads(target, name):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(member_hooks, "_spawn", no_threads)
    member_hooks.apps_startup()


def test_a_host_that_is_down_costs_the_start_nothing(world, monkeypatch):
    """The real hand-over: the round runs on its own thread while the start has long returned."""
    from app.services.apps.purge_worker import apps_purge_worker_singleton

    monkeypatch.setattr(apps_purge_worker_singleton, "start", lambda: None)
    monkeypatch.setattr("app.services.apps.deploy.deploy_service", world.deploys)
    entered = threading.Event()
    release = threading.Event()

    def slow_round(**kwargs):
        entered.set()
        release.wait(timeout=30)  # a host that takes its time to refuse
        return dict(NOTHING)

    monkeypatch.setattr(host_sync, "sync_host_apps", slow_round)
    try:
        member_hooks.apps_startup()  # returns although the round has not ended
        assert entered.wait(timeout=10)
        assert not release.is_set()
    finally:
        release.set()


# --- the worker's round ---------------------------------------------------------------


def test_the_workers_round_tells_the_host_and_settles_abandoned_releases(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    app = world.deployed("One")
    other = world.create("Other")
    world.db.add(
        AppRelease(id="rel-lost", app_id=other.id, kind="deploy", status="running", steps_json="[]", started_at=datetime.utcnow() - timedelta(hours=3))
    )
    world.db.commit()
    world.host.calls.clear()
    monkeypatch.setattr(deploy_module, "deploy_service", world.deploys)
    worker = AppsPurgeWorker(world.factory, host_factory=lambda: world.host)

    assert worker.sync_host()["synced"] == 2  # "Other" has a release row: the host may have heard of it
    assert sorted(_puts(world.host)) == sorted([app.id, other.id])
    assert worker.settle_releases() == {"resumed": 0, "failed": 1}
    world.db.expire_all()
    assert world.db.get(AppRelease, "rel-lost").status == "failed"


def test_one_errand_that_fails_does_not_cost_the_others_their_turn(world, caplog):
    import logging

    caplog.set_level(logging.CRITICAL)
    ran = []
    done = threading.Event()
    worker = AppsPurgeWorker(world.factory, host_factory=lambda: world.host, initial_delay_seconds=0, interval_seconds=3600)

    def purge(**_kwargs):
        ran.append("purge")
        raise RuntimeError("boom")

    def sync():
        ran.append("sync")
        raise RuntimeError("boom")

    def settle():
        ran.append("settle")
        done.set()

    worker.run_once, worker.sync_host, worker.settle_releases = purge, sync, settle
    worker.start()
    try:
        assert done.wait(timeout=10)
    finally:
        worker.stop()
    assert ran == ["purge", "sync", "settle"]
    assert not worker._thread.is_alive()
