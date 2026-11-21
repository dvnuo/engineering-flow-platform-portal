"""The deploy pipeline end to end, against a fake host and a fake GitHub."""

from datetime import datetime, timedelta
import hashlib
import logging
import threading
import time

import pytest

from _apps_helpers import (
    MANIFEST,
    PNG,
    REPO_URL,
    SHA_1,
    SHA_2,
    SHA_3,
    FakeHost,
    FakeSource,
    add_user,
    enable_apps,
    failed_job,
    make_database,
    make_deploy_service,
    remove_from_allowlist,
    reset_apps_state,
    running_job,
    succeeded_job,
)
from app.models import AppAuditEvent, AppRelease
from app.services.apps.app_service import AppService
from app.services.apps.deploy import (
    ABANDONED_AFTER_SECONDS,
    HOST_STEPS,
    PORTAL_STEPS,
    DeployService,
    active_release,
    adopt_host_release,
    is_abandoned,
    is_held,
    sniff_icon_type,
)
from app.services.apps.errors import (
    AppConflict,
    AppHostError,
    AppHostUnavailable,
    AppNotFound,
    DeployInProgress,
    SourceTooLarge,
    SourceUnavailable,
)


@pytest.fixture
def world(monkeypatch):
    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    owner = add_user(db, "owner")
    admin = add_user(db, "admin", role="admin")
    host = FakeHost()
    source = FakeSource()
    service = AppService(db, host=host, source=source)
    app = service.create(owner, repo_url=REPO_URL, ref="main", entry_dir="apps/mindmap", name="Mind map", description="Registered words")

    class World:
        pass

    w = World()
    w.db, w.factory, w.owner, w.admin, w.host, w.source, w.service, w.app = db, factory, owner, admin, host, source, service, app
    w.deploys = make_deploy_service(factory, host, source)

    def deploy(ref=None, by=None):
        release = w.deploys.start_deploy(app, ref, by or owner)
        db.refresh(app)
        db.refresh(release)
        return release

    w.deploy = deploy
    try:
        yield w
    finally:
        db.close()
        reset_apps_state()


def _steps(release, source=None):
    return [(step["source"], step["name"], step["status"]) for step in release.steps if source is None or step["source"] == source]


def _actions(db, app):
    return [row.action for row in db.query(AppAuditEvent).filter(AppAuditEvent.app_id == app.id).order_by(AppAuditEvent.id)]


# --- success --------------------------------------------------------------------------


def test_a_deployment_end_to_end(world):
    release = world.deploy()
    app = world.app

    assert release.kind == "deploy" and release.status == "succeeded"
    assert release.commit_sha == SHA_1 and release.ref == "main"
    assert release.commit_message == "commit on main"
    assert release.requested_by_user_id == world.owner.id
    assert release.host_job_id == "job-1"
    assert release.error_summary is None and release.finished_at is not None
    assert release.log_tail == "started\n"
    # The Portal's steps lead, the host's follow, each marked with where it ran.
    assert _steps(release) == [("portal", name, "succeeded") for name in PORTAL_STEPS] + [("host", name, "succeeded") for name in HOST_STEPS]
    assert all(step["started_at"] and step["finished_at"] for step in release.steps)

    # The order the host saw: the app's record, the bundle, then the job until it ended.
    assert world.host.names()[:3] == ["put_app", "deploy", "get_job"]
    assert world.host.bundles == [world.source.tarball]
    assert world.host.deploy_specs == [{"sha": SHA_1, "ref": "main", "entry_dir": "apps/mindmap", "requested_by": "owner"}]
    spec = world.host.specs[app.id]
    assert spec["slug"] == "mind-map" and spec["owner_username"] == "owner"
    assert spec["env"] == {"EFP_APP_TOKEN": app.token_value}

    # The app now points at the release and takes type, description and icon from the manifest.
    assert app.current_release_id == release.id
    assert app.type == "fastapi"
    assert app.description == MANIFEST["description"]
    assert (app.icon_data, app.icon_content_type) == (PNG, "image/png")
    assert app.desired_state == "running"
    assert _actions(world.db, app) == ["app.created", "deploy.requested", "deploy.succeeded"]
    succeeded = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "deploy.succeeded").one()
    assert succeeded.detail == {"release_id": release.id, "sha": SHA_1, "ref": "main"}


def test_the_icon_is_read_from_the_entry_directory(world):
    world.deploy()
    assert ("read_file", SHA_1, "apps/mindmap/icon.png") in world.source.calls
    assert ("read_manifest", SHA_1, "apps/mindmap") in world.source.calls


def test_the_hosts_progress_is_mirrored_while_the_job_runs(world):
    world.host.job_states = [
        {"status": "queued", "steps": [], "error": None, "log_tail": "", "log_size": 0},
        running_job(dependencies="running", start="pending", health="pending", activate="pending"),
        AppHostUnavailable(),  # one missed read is not the end of a deployment
        succeeded_job(),
    ]
    seen = []
    original = world.host.get_job

    def spying(job_id):
        row = world.factory().get(AppRelease, world.db.query(AppRelease).one().id)
        seen.append((row.status, [step["status"] for step in row.steps if step["source"] == "host"]))
        return original(job_id)

    world.host.get_job = spying
    release = world.deploy()
    assert release.status == "succeeded"
    # While the job ran the row said so, with the host's own step states.
    assert seen[0][0] == "running" and set(seen[0][1]) == {"pending"}
    assert seen[2] == ("running", ["succeeded", "succeeded", "succeeded", "running", "pending", "pending", "pending"])
    assert len(seen) == 4


def test_deploying_another_ref_makes_the_app_follow_it(world):
    world.source.refs["release/2"] = SHA_2
    world.deploy()
    release = world.deploy("release/2")
    assert release.status == "succeeded" and release.commit_sha == SHA_2 and release.ref == "release/2"
    assert world.app.repo_ref == "release/2"
    # "Deploy latest" is the stored ref again.
    world.source.refs["release/2"] = SHA_3
    latest = world.deploys.deploy_latest(world.app, world.owner)
    assert latest.ref == "release/2" and latest.commit_sha == SHA_3


def test_the_owners_username_is_checked_whoever_presses_deploy(world):
    release = world.deploy(by=world.admin)
    assert release.status == "succeeded"
    assert release.requested_by_user_id == world.admin.id
    assert world.host.deploy_specs[-1]["requested_by"] == "admin"

    # The owner taken off the manifest: nobody can deploy it for them.
    world.source.manifest = {**MANIFEST, "owners": ["someone-else"]}
    release = world.deploy(by=world.admin)
    assert release.status == "failed"
    failed = next(step for step in release.steps if step["status"] == "failed")
    assert (failed["source"], failed["name"]) == ("portal", "manifest")
    assert failed["error"]["code"] == "not_listed_as_owner"
    assert failed["error"]["params"] == {"username": "owner"}

    # The list taken out of the manifest: it limits nobody any more.
    world.source.manifest = {key: value for key, value in MANIFEST.items() if key != "owners"}
    assert world.deploy().status == "succeeded"


def test_an_app_made_from_a_template_skips_the_ownership_check(world):
    other = add_user(world.db, "other")
    app = world.service.create(other, repo_url=REPO_URL, ref="main", name="From template", template_app_id="tpl-1", skip_ownership_check=True)
    release = world.deploys.start_deploy(app, None, other)
    world.db.refresh(release)
    assert release.status == "succeeded"


# --- the icon -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data, expected",
    [
        (PNG, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"GIF87a" + b"\x00" * 16, "image/gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", None),
        (b"<?xml version='1.0'?><svg/>", None),
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", None),
        (b"", None),
        (b"not an image", None),
    ],
    ids=["png", "jpeg", "gif89", "gif87", "webp", "svg", "xml-svg", "riff-wave", "empty", "text"],
)
def test_icon_types_are_told_by_content_and_never_svg(data, expected):
    assert sniff_icon_type(data) == expected


@pytest.mark.parametrize(
    "arrange, note",
    [
        (lambda source: source.files.update({"icon.png": b"<svg/>"}), "PNG, JPEG, GIF or WebP"),
        (lambda source: source.files.clear(), "was not found"),
        (lambda source: source.fail.update({"read_file": SourceTooLarge(code="file_too_large", path="icon.png", limit_kb=256)}), "larger than 256 KB"),
        (lambda source: source.fail.update({"read_file": SourceUnavailable(reason="HTTP 502")}), "could not be read"),
    ],
    ids=["svg", "missing", "too-large", "unreadable"],
)
def test_a_bad_icon_never_fails_a_deployment(world, arrange, note):
    world.deploy()
    arrange(world.source)
    release = world.deploy()
    assert release.status == "succeeded"
    manifest_step = next(step for step in release.steps if (step["source"], step["name"]) == ("portal", "manifest"))
    assert manifest_step["status"] == "succeeded"
    assert note in manifest_step["note"] and manifest_step["note_key"]
    # The icon the app already had stays.
    assert (world.app.icon_data, world.app.icon_content_type) == (PNG, "image/png")


def test_a_manifest_without_an_icon_clears_it(world):
    world.deploy()
    world.source.manifest = {key: value for key, value in MANIFEST.items() if key != "icon"}
    world.deploy()
    assert world.app.icon_data is None and world.app.icon_content_type is None


# --- failures -------------------------------------------------------------------------


def _assert_untouched(world, release):
    """A failed deployment leaves the app as it was."""
    assert release.status == "failed" and release.finished_at is not None and release.error_summary
    assert world.app.current_release_id is None
    assert world.app.description == "Registered words"
    assert world.app.icon_data is None
    assert _actions(world.db, world.app)[-1] == "deploy.failed"


def test_an_unknown_ref_fails_at_resolve(world):
    release = world.deploy("gone")
    _assert_untouched(world, release)
    assert _steps(release)[:3] == [("portal", "resolve", "failed"), ("portal", "manifest", "skipped"), ("portal", "fetch", "skipped")]
    assert {status for _source, _name, status in _steps(release, "host")} == {"skipped"}
    assert release.steps[0]["error"]["code"] == "ref_not_found"
    assert "gone" in release.error_summary
    assert world.host.calls == []


def test_a_manifest_error_fails_at_manifest_and_nothing_reaches_the_host(world):
    world.source.manifest = {"name": "Mind map", "type": "rails", "owners": ["owner"]}
    release = world.deploy()
    _assert_untouched(world, release)
    assert _steps(release)[:3] == [("portal", "resolve", "succeeded"), ("portal", "manifest", "failed"), ("portal", "fetch", "skipped")]
    error = release.steps[1]["error"]
    assert error["code"] == "manifest_invalid"
    assert '"type" must be one of' in error["message"] and '"type" must be one of' in release.error_summary
    assert release.commit_sha == SHA_1  # what was looked at
    assert world.host.calls == []
    failed = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "deploy.failed").one()
    assert failed.detail["code"] == "manifest_invalid" and failed.detail["release_id"] == release.id


def test_a_missing_manifest_and_invalid_json(world):
    world.source.manifest = None
    release = world.deploy()
    assert release.steps[1]["error"]["code"] == "manifest_missing"
    world.source.manifest = b"{oops"
    release = world.deploy()
    assert release.steps[1]["error"]["code"] == "manifest_invalid"


def test_a_host_that_cannot_be_reached_fails_at_fetch(world):
    world.host.fail["put_app"] = AppHostUnavailable()
    release = world.deploy()
    _assert_untouched(world, release)
    assert _steps(release)[:3] == [("portal", "resolve", "succeeded"), ("portal", "manifest", "succeeded"), ("portal", "fetch", "failed")]
    assert release.steps[2]["error"]["code"] == "host_unreachable"
    assert "cannot be reached" in release.error_summary
    assert release.host_job_id is None


def test_the_host_refusing_the_bundle_fails_at_fetch(world):
    world.host.fail["deploy"] = AppHostError(code="host_deploy_in_progress", reason="deploy_in_progress", host_code="deploy_in_progress", host_status=409)
    release = world.deploy()
    _assert_untouched(world, release)
    assert release.steps[2]["error"]["code"] == "host_deploy_in_progress"


def test_a_bundle_over_the_cap_fails_at_fetch(world):
    world.source.fail["download_tarball"] = SourceTooLarge(limit_mb=200)
    release = world.deploy()
    _assert_untouched(world, release)
    assert release.steps[2]["error"]["code"] == "bundle_too_large"
    assert "deploy" not in world.host.names()


def test_a_failed_host_job_keeps_the_hosts_steps_and_its_error(world):
    world.host.job_states = [running_job(dependencies="running", start="pending", health="pending", activate="pending"), failed_job()]
    release = world.deploy()
    _assert_untouched(world, release)
    assert [status for _source, _name, status in _steps(release, "portal")] == ["succeeded"] * 3
    assert _steps(release, "host") == [
        ("host", "unpack", "succeeded"),
        ("host", "manifest", "succeeded"),
        ("host", "environment", "succeeded"),
        ("host", "dependencies", "failed"),
        ("host", "start", "skipped"),
        ("host", "health", "skipped"),
        ("host", "activate", "skipped"),
    ]
    # pip's own words reach the member as they are.
    assert "dependencies" in release.error_summary and "No matching distribution found for nope==1.0" in release.error_summary
    assert "No matching distribution" in release.log_tail
    failed = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "deploy.failed").one()
    assert failed.detail["code"] == "deploy_failed"


def test_a_failed_deployment_leaves_the_running_release_in_place(world):
    first = world.deploy()
    world.source.refs["main"] = SHA_2
    world.host.job_states = [failed_job("health", "the app did not answer on its port")]
    second = world.deploy()
    assert second.status == "failed" and second.commit_sha == SHA_2
    assert world.app.current_release_id == first.id
    assert world.host.releases[world.app.id] == [SHA_1]


def test_a_host_that_stays_away_during_the_job_ends_the_release(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    monkeypatch.setattr(deploy_module, "POLL_MAX_UNREACHABLE", 3)
    world.host.job_states = [AppHostUnavailable()]
    release = world.deploy()
    _assert_untouched(world, release)
    assert world.host.names().count("get_job") == 4
    assert "cannot be reached" in release.error_summary
    assert release.host_job_id == "job-1"


def test_a_job_the_host_is_still_working_on_is_never_timed_out(world):
    # Pinned the opposite before: the Portal failed the release after 45
    # minutes of its own, while the host (one install at a time, each with its
    # own limit) finished the job and ran it. The host decides how a job ends.
    assert not hasattr(world.deploys, "poll_timeout")
    waiting = running_job(unpack="pending", manifest="pending", environment="pending", dependencies="pending", start="pending", health="pending", activate="pending")
    world.host.job_states = [waiting] * 2000 + [succeeded_job()]
    release = world.deploy()
    assert release.status == "succeeded" and release.error_summary is None
    assert world.host.names().count("get_job") == 2001
    assert world.app.current_release_id == release.id


def test_a_bug_in_the_pipeline_still_ends_the_release(world, caplog):
    caplog.set_level(logging.CRITICAL)

    def boom(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    world.source.resolve_ref = boom
    release = world.deploy()
    assert release.status == "failed"
    assert "unexpected error in the Portal" in release.error_summary


# --- one at a time --------------------------------------------------------------------


def test_one_deployment_per_app_at_a_time(world):
    pending = []
    service = DeployService(
        world.factory,
        host_factory=lambda: world.host,
        source_factory=lambda db: world.source,
        spawn=lambda target, name: pending.append(target),
        sleep=lambda seconds: None,
    )
    first = service.start_deploy(world.app, None, world.owner)
    assert first.status == "queued"
    with pytest.raises(DeployInProgress) as caught:
        service.start_deploy(world.app, None, world.owner)
    assert caught.value.status_code == 409 and caught.value.code == "deploy_in_progress"
    with pytest.raises(DeployInProgress):
        service.start_rollback(world.app, world.owner)
    assert world.db.query(AppRelease).count() == 1

    # Another app is not held up.
    other = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Other")
    assert service.start_deploy(other, None, world.owner).status == "queued"

    pending[0]()
    world.db.refresh(first)
    assert first.status == "succeeded"
    assert service.start_deploy(world.app, None, world.owner).status == "queued"


def test_concurrent_requests_start_exactly_one_deployment(world):
    pending = []
    service = DeployService(
        world.factory,
        host_factory=lambda: world.host,
        source_factory=lambda db: world.source,
        spawn=lambda target, name: pending.append(target),
    )
    from app.models import App

    outcomes = []
    barrier = threading.Barrier(4)
    owner_id = world.owner.id
    # Each request has its own session and its own copy of the app, loaded
    # here so that only start_deploy itself runs side by side.
    sessions = [world.factory() for _ in range(4)]
    apps = [session.get(App, world.app.id) for session in sessions]

    def attempt(app):
        barrier.wait(timeout=5)
        try:
            service.start_deploy(app, None, owner_id)
            outcomes.append("started")
        except DeployInProgress:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(app,)) for app in apps]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    for session in sessions:
        session.close()
    assert sorted(outcomes) == ["refused", "refused", "refused", "started"]
    assert len(pending) == 1
    assert world.db.query(AppRelease).count() == 1


def test_a_queued_release_runs_once(world):
    pending = []
    service = DeployService(
        world.factory,
        host_factory=lambda: world.host,
        source_factory=lambda db: world.source,
        spawn=lambda target, name: pending.append(target),
        sleep=lambda seconds: None,
    )
    release = service.start_deploy(world.app, None, world.owner)
    pending[0]()
    pending[0]()  # a second run of the same release does nothing
    assert world.host.names().count("deploy") == 1
    world.db.refresh(release)
    assert release.status == "succeeded"


# --- rollback -------------------------------------------------------------------------


def test_rollback_returns_to_the_release_before(world):
    first = world.deploy()
    world.source.refs["main"] = SHA_2
    second = world.deploy()
    assert world.app.current_release_id == second.id
    assert world.deploys.rollback_target(world.db, world.app).id == first.id
    world.host.calls.clear()

    rollback = world.deploys.start_rollback(world.app, world.owner)
    world.db.refresh(world.app)
    assert rollback.kind == "rollback" and rollback.status == "succeeded"
    assert rollback.commit_sha == SHA_1 and rollback.ref == "main"
    assert rollback.commit_message == first.commit_message
    assert _steps(rollback) == [("host", "activate", "succeeded")]
    assert world.host.calls == [("activate", world.app.id, SHA_1)]
    assert world.app.current_release_id == rollback.id
    assert world.host.releases[world.app.id][-1] == SHA_1
    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "rollback.succeeded").one()
    assert event.detail == {"release_id": rollback.id, "sha": SHA_1, "from_release_id": first.id}

    # Rolling back again returns to the newer code.
    assert world.deploys.rollback_target(world.db, world.app).id == second.id


def test_rollback_needs_an_earlier_release_with_other_code(world):
    with pytest.raises(AppConflict) as caught:
        world.deploys.start_rollback(world.app, world.owner)
    assert caught.value.code == "no_previous_release"
    world.deploy()
    world.deploy()  # the same commit again is nothing to return to
    with pytest.raises(AppConflict):
        world.deploys.start_rollback(world.app, world.owner)
    # A failed deployment is not a release to return to either.
    world.source.refs["main"] = SHA_2
    world.host.job_states = [failed_job()]
    world.deploy()
    with pytest.raises(AppConflict):
        world.deploys.start_rollback(world.app, world.owner)


def test_a_rollback_the_host_refuses_is_kept_as_failed(world):
    first = world.deploy()
    world.source.refs["main"] = SHA_2
    second = world.deploy()
    world.host.releases[world.app.id] = [SHA_2]  # the host no longer keeps the old one
    with pytest.raises(AppHostError) as caught:
        world.deploys.start_rollback(world.app, world.owner)
    assert caught.value.host_code == "release_not_found"
    world.db.refresh(world.app)
    assert world.app.current_release_id == second.id
    row = world.db.query(AppRelease).filter(AppRelease.kind == "rollback").one()
    assert row.status == "failed" and row.commit_sha == first.commit_sha
    assert _steps(row) == [("host", "activate", "failed")]
    assert _actions(world.db, world.app)[-1] == "rollback.failed"
    # Nothing is left running: the next deployment may start.
    assert world.deploy().status in {"succeeded", "failed"}


# --- commits behind -------------------------------------------------------------------


def test_commits_behind_compares_what_runs_with_the_followed_ref(world):
    assert world.deploys.commits_behind(world.db, world.app) is None  # nothing deployed
    world.deploy()
    world.source.ahead = 3
    assert world.deploys.commits_behind(world.db, world.app) == 3
    assert world.source.calls[-1] == ("commits_ahead", SHA_1, "main")
    # Asked of GitHub once a minute at most.
    world.source.ahead = 9
    assert world.deploys.commits_behind(world.db, world.app) == 3
    assert [call[0] for call in world.source.calls].count("commits_ahead") == 1
    # A new deployment starts the count over.
    world.source.refs["main"] = SHA_2
    world.source.ahead = 0
    world.deploy()
    assert world.deploys.commits_behind(world.db, world.app) == 0


def test_commits_behind_is_unknown_when_github_does_not_answer(world):
    world.deploy()
    world.source.fail["commits_ahead"] = SourceUnavailable(reason="HTTP 502")
    assert world.deploys.commits_behind(world.db, world.app) is None


# --- a Portal restart -----------------------------------------------------------------


def test_releases_left_by_a_restart_are_resumed_or_failed(world):
    app = world.app
    world.host.put_app(app.id, {"slug": app.slug, "enabled": True, "env": {}})
    world.host.jobs["job-9"] = {"app_id": app.id, "sha": SHA_2}
    world.host.calls.clear()
    world.db.add_all(
        [
            # The host had the bundle: the job can be followed to its end.
            AppRelease(id="rel-with-job", app_id=app.id, kind="deploy", status="running", commit_sha=SHA_2, ref="main", host_job_id="job-9", steps_json="[]"),
        ]
    )
    other = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Other")
    third = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Third")
    world.db.add_all(
        [
            # Nothing had reached the host yet.
            AppRelease(id="rel-queued", app_id=other.id, kind="deploy", status="queued", ref="main", steps_json="[]"),
            AppRelease(id="rel-rollback", app_id=third.id, kind="rollback", status="running", commit_sha=SHA_1, steps_json="[]"),
        ]
    )
    world.db.commit()

    assert world.deploys.recover_interrupted() == {"resumed": 1, "failed": 2}
    world.db.expire_all()

    resumed = world.db.get(AppRelease, "rel-with-job")
    assert resumed.status == "succeeded"
    assert [status for _source, _name, status in _steps(resumed, "host")] == ["succeeded"] * 7
    assert app.current_release_id == "rel-with-job"
    assert app.type == "fastapi"  # read back from the host; the manifest is gone
    assert app.description == "Registered words"

    for release_id in ("rel-queued", "rel-rollback"):
        row = world.db.get(AppRelease, release_id)
        assert row.status == "failed" and row.finished_at is not None
        assert "The Portal restarted" in row.error_summary
    assert other.current_release_id is None

    # Nothing is left to recover, and both apps can be deployed again.
    assert world.deploys.recover_interrupted() == {"resumed": 0, "failed": 0}
    assert world.deploys.start_deploy(other, None, world.owner).status == "succeeded"


def test_startup_hook_does_nothing_while_apps_are_off(world, monkeypatch):
    import app.services.apps.deploy as deploy_module
    from app.services.apps import member_hooks
    from app.services.apps.member_hooks import apps_startup

    world.db.add(AppRelease(id="rel-queued", app_id=world.app.id, kind="deploy", status="queued", ref="main", steps_json="[]"))
    world.db.commit()
    monkeypatch.setattr(deploy_module, "deploy_service", world.deploys)
    # The worker that removes the data of long-deleted apps starts with the feature, and only with it.
    from app.services.apps.purge_worker import apps_purge_worker_singleton

    started = []
    monkeypatch.setattr(apps_purge_worker_singleton, "start", lambda: started.append(True))
    # So does the round that tells the host about every app again: handed to a thread, never run by the start itself.
    background = []
    monkeypatch.setattr(member_hooks, "_spawn", lambda target, name: background.append((target, name)))

    enable_apps(monkeypatch, apps_enabled=False)
    apps_startup()
    assert world.db.get(AppRelease, "rel-queued").status == "queued"
    assert started == [] and background == []

    enable_apps(monkeypatch, apps_enabled=True)
    apps_startup()
    world.db.expire_all()
    assert world.db.get(AppRelease, "rel-queued").status == "failed"
    assert started == [True]
    assert background == [(member_hooks.sync_host_after_start, "apps-host-sync")]


# --- a disabled app -------------------------------------------------------------------


def test_a_disabled_app_is_not_deployed_or_rolled_back(world):
    world.deploy()
    world.source.refs["main"] = SHA_2
    world.deploy()
    world.service.disable(world.app, world.admin, "Taken down")
    world.host.calls.clear()
    rows = world.db.query(AppRelease).count()

    attempts = (
        lambda: world.deploys.start_deploy(world.app, None, world.owner),
        lambda: world.deploys.deploy_latest(world.app, world.admin),
        lambda: world.deploys.start_rollback(world.app, world.owner),
    )
    for attempt in attempts:
        # The answer a start or a restart of a disabled app gets.
        with pytest.raises(AppConflict) as caught:
            attempt()
        assert caught.value.code == "app_disabled" and caught.value.status_code == 409
        assert caught.value.params["reason"] == "Taken down"
    # No row, and the host was asked for nothing: it would have run the new code to check it.
    assert world.db.query(AppRelease).count() == rows
    assert world.host.calls == []

    world.service.enable(world.app, world.admin)
    assert world.deploy().status == "succeeded"


def test_an_app_whose_owner_lost_access_is_not_deployed(world):
    remove_from_allowlist(world.db, "owner")
    with pytest.raises(AppConflict) as caught:
        world.deploys.start_deploy(world.app, None, world.admin)
    assert caught.value.code == "app_disabled" and caught.value.params["reason"] == "owner_inactive"
    assert world.db.query(AppRelease).count() == 0


def test_an_app_taken_down_while_its_deployment_waited_is_not_deployed(world):
    pending = []
    service = make_deploy_service(world.factory, world.host, world.source, spawn=lambda target, name: pending.append(target))
    release = service.start_deploy(world.app, None, world.owner)
    world.service.disable(world.app, world.admin, "Taken down")
    world.host.calls.clear()
    world.source.calls.clear()

    pending[0]()
    world.db.refresh(release)
    assert release.status == "failed" and "disabled" in release.error_summary
    # Neither GitHub nor the host was asked for anything.
    assert world.host.calls == [] and world.source.calls == []
    assert not is_held(release.id)


# --- the host decides how a job ends ---------------------------------------------------


def test_a_host_that_answers_with_a_server_error_is_asked_again(world):
    world.host.job_states = [
        AppHostError(code="host_error", reason="HTTP 502", host_status=502),
        running_job(dependencies="running", start="pending", health="pending", activate="pending"),
        AppHostError(code="host_error", reason="HTTP 503", host_status=503),
        # A page of some proxy in between, say: an answer, but not the host's about the job.
        AppHostError(reason="the app host did not answer with JSON"),
        succeeded_job(),
    ]
    release = world.deploy()
    assert release.status == "succeeded" and world.host.names().count("get_job") == 5


def test_an_answer_that_names_no_state_of_the_job_is_not_followed_for_ever(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    # One such answer is asked about again ...
    world.host.job_states = [{"status": "", "steps": [], "error": None}, {"steps": []}, succeeded_job()]
    assert world.deploy().status == "succeeded"
    assert world.host.names().count("get_job") == 3

    # ... and a host that never says more is given up on, like one that does not answer at all.
    monkeypatch.setattr(deploy_module, "POLL_MAX_UNREACHABLE", 3)
    world.source.refs["main"] = SHA_2
    world.host.job_states = [{"status": "paused", "steps": []}]
    release = world.deploy()
    assert release.status == "failed" and "could not be read" in release.error_summary
    assert world.host.names().count("get_job") == 3 + 4


def test_a_host_that_no_longer_knows_the_job_ends_the_release(world):
    world.host.job_states = [AppHostError(code="host_job_not_found", reason="job_not_found", host_code="job_not_found", host_status=404)]
    release = world.deploy()
    assert release.status == "failed" and world.host.names().count("get_job") == 1


def test_a_database_that_is_busy_for_a_moment_does_not_fail_a_job_that_runs(world, monkeypatch, caplog):
    import app.services.apps.deploy as deploy_module

    caplog.set_level(logging.CRITICAL)
    world.host.job_states = [running_job(dependencies="running", start="pending", health="pending", activate="pending"), succeeded_job()]
    original = deploy_module._Steps.save
    left = [2]

    def save(self):
        # Only while the job is followed: the two writes after the first two answers of the host fail.
        if world.host.names().count("get_job") >= 1 and left[0] > 0:
            left[0] -= 1
            raise RuntimeError("database is locked")
        return original(self)

    monkeypatch.setattr(deploy_module._Steps, "save", save)
    release = world.deploy()
    assert left == [0]
    assert release.status == "succeeded" and world.app.current_release_id == release.id
    # What could not be written at once was written with the next answer.
    assert [status for _source, _name, status in _steps(release, "host")] == ["succeeded"] * 7
    assert release.log_tail == "started\n"


def test_a_release_the_portal_gave_up_on_is_adopted_once_the_host_runs_it(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    monkeypatch.setattr(deploy_module, "POLL_MAX_UNREACHABLE", 2)
    world.host.job_states = [AppHostUnavailable()]
    release = world.deploy()
    assert release.status == "failed" and world.app.current_release_id is None
    with pytest.raises(AppConflict):
        world.service.stop(world.app, world.owner)  # "not deployed", as far as the Portal knows

    # The host was only out of reach. It finishes the job and runs the commit.
    world.host.job_states = None
    world.host.get_job(release.host_job_id)
    assert world.host.releases[world.app.id] == [SHA_1]

    # Any read of the host's status takes the release over.
    status = world.service.status(world.app)
    world.db.refresh(release)
    world.db.refresh(world.app)
    assert status["status"]["release"]["sha"] == SHA_1
    assert release.status == "succeeded" and release.error_summary is None
    assert world.app.current_release_id == release.id and world.app.desired_state == "running" and world.app.type == "fastapi"
    assert [state for _source, _name, state in _steps(release)] == ["succeeded"] * (len(PORTAL_STEPS) + len(HOST_STEPS))
    assert release.log_tail == "started\n"
    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "deploy.succeeded").one()
    assert event.detail == {"release_id": release.id, "sha": SHA_1, "adopted": True}

    # Once: the next read changes nothing more.
    world.service.status(world.app)
    assert world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "deploy.succeeded").count() == 1
    world.service.stop(world.app, world.owner)


def test_a_deployment_adopted_later_moves_the_app_to_its_ref_and_template_version(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    world.deploy()
    world.source.refs["release"] = SHA_2
    monkeypatch.setattr(deploy_module, "POLL_MAX_UNREACHABLE", 0)
    world.host.job_states = [AppHostUnavailable()]
    gave_up = world.deploy("release")
    assert gave_up.status == "failed" and world.app.repo_ref == "main"
    gave_up.template_version_id = "version-2"
    world.db.commit()

    world.host.job_states = None
    world.host.get_job(gave_up.host_job_id)
    adopted = adopt_host_release(world.db, world.app, world.host.get_app(world.app.id), host=world.host)
    world.db.refresh(world.app)
    assert adopted.id == gave_up.id and world.app.current_release_id == gave_up.id
    assert world.app.repo_ref == "release" and world.app.template_version_id == "version-2"
    # What the manifest said at the time is gone: the description stays.
    assert world.app.description == MANIFEST["description"]


def test_only_a_result_the_host_really_has_is_adopted(world, monkeypatch):
    import app.services.apps.deploy as deploy_module

    # Nothing to adopt in a status that names no release, or the one the Portal has.
    assert adopt_host_release(world.db, world.app, None) is None
    assert adopt_host_release(world.db, world.app, {"release": None}) is None
    first = world.deploy()
    assert adopt_host_release(world.db, world.app, world.host.get_app(world.app.id), host=world.host) is None

    # A deployment the host itself failed stays failed: the host still runs the old commit.
    world.source.refs["main"] = SHA_2
    world.host.job_states = [failed_job()]
    failed = world.deploy()
    assert adopt_host_release(world.db, world.app, world.host.get_app(world.app.id), host=world.host) is None
    world.db.refresh(failed)
    assert failed.status == "failed" and world.app.current_release_id == first.id

    # A failed release older than the current one is history, whatever the host says it runs.
    world.host.job_states = None
    world.source.refs["main"] = SHA_3
    third = world.deploy()
    assert world.app.current_release_id == third.id
    assert adopt_host_release(world.db, world.app, {"id": world.app.id, "release": {"sha": SHA_2}}, host=world.host) is None

    # While a deployment is in progress its own end says what is current.
    world.source.refs["main"] = "4" * 40
    monkeypatch.setattr(deploy_module, "POLL_MAX_UNREACHABLE", 0)
    world.host.job_states = [AppHostUnavailable()]
    gave_up = world.deploy()
    assert gave_up.status == "failed"
    in_progress = AppRelease(app_id=world.app.id, kind="deploy", status="running", steps_json="[]")
    world.db.add(in_progress)
    world.db.commit()
    runs_it = {"id": world.app.id, "type": "fastapi", "release": {"sha": "4" * 40}}
    assert adopt_host_release(world.db, world.app, runs_it) is None
    in_progress.status = "failed"
    world.db.commit()
    assert adopt_host_release(world.db, world.app, runs_it).id == gave_up.id


def test_a_rollback_the_host_carried_out_without_answering_is_adopted(world):
    world.deploy()
    world.source.refs["main"] = SHA_2
    second = world.deploy()
    original = world.host.activate

    def activate(app_id, sha):
        original(app_id, sha)
        raise AppHostUnavailable()  # the host switched; its answer never arrived

    world.host.activate = activate
    with pytest.raises(AppHostUnavailable):
        world.deploys.start_rollback(world.app, world.owner)
    row = world.db.query(AppRelease).filter(AppRelease.kind == "rollback").one()
    world.db.refresh(world.app)
    assert row.status == "failed" and world.app.current_release_id == second.id
    assert world.host.releases[world.app.id][-1] == SHA_1

    del world.host.activate
    world.service.status(world.app)
    world.db.refresh(row)
    world.db.refresh(world.app)
    assert row.status == "succeeded" and world.app.current_release_id == row.id
    assert _steps(row) == [("host", "activate", "succeeded")] and "error" not in row.steps[0]
    assert _actions(world.db, world.app)[-1] == "rollback.succeeded"


# --- nobody works on it any more -------------------------------------------------------


def test_a_rollback_that_breaks_for_any_reason_does_not_stay_running(world, caplog):
    caplog.set_level(logging.CRITICAL)
    world.deploy()
    world.source.refs["main"] = SHA_2
    second = world.deploy()
    world.host.fail["activate"] = RuntimeError("not an error of the host's")
    with pytest.raises(RuntimeError):
        world.deploys.start_rollback(world.app, world.owner)
    world.db.rollback()
    row = world.db.query(AppRelease).filter(AppRelease.kind == "rollback").one()
    world.db.refresh(world.app)
    assert row.status == "failed" and "unexpected error in the Portal" in row.error_summary
    assert world.app.current_release_id == second.id and not is_held(row.id)

    # Nothing is left in the way of the next one.
    del world.host.fail["activate"]
    assert world.deploys.start_rollback(world.app, world.owner).status == "succeeded"


def test_a_release_nobody_works_on_any_more_counts_as_failed(world):
    from app.services.apps.deploy import _hold, _let_go

    row = AppRelease(app_id=world.app.id, kind="deploy", status="running", steps_json="[]")
    world.db.add(row)
    world.db.commit()
    # Young: whoever made it (another Portal process, a moment ago) may still be on it.
    assert not is_abandoned(row) and active_release(world.db, world.app.id).id == row.id
    with pytest.raises(DeployInProgress):
        world.deploys.start_deploy(world.app, None, world.owner)

    # Older than the limit, and no thread of this Portal holds it: abandoned.
    row.started_at = datetime.utcnow() - timedelta(seconds=ABANDONED_AFTER_SECONDS + 5)
    world.db.commit()
    assert is_abandoned(row) and active_release(world.db, world.app.id) is None

    # Held, it is in progress however long it takes.
    _hold(row.id)
    assert not is_abandoned(row) and active_release(world.db, world.app.id).id == row.id
    with pytest.raises(DeployInProgress):
        world.deploys.start_deploy(world.app, None, world.owner)
    _let_go(row.id)

    # The next deployment ends it and goes ahead.
    release = world.deploy()
    world.db.refresh(row)
    assert release.status == "succeeded"
    assert row.status == "failed" and row.finished_at is not None
    assert "unexpected error in the Portal" in row.error_summary


def test_a_release_is_held_from_its_request_to_its_end(world):
    pending = []
    service = make_deploy_service(world.factory, world.host, world.source, spawn=lambda target, name: pending.append(target))
    release = service.start_deploy(world.app, None, world.owner)
    # Waiting for a worker is being worked on: it is never taken for abandoned.
    release.started_at = datetime.utcnow() - timedelta(days=1)
    world.db.commit()
    assert is_held(release.id) and not is_abandoned(release)
    assert service.settle_abandoned() == {"resumed": 0, "failed": 0}
    pending[0]()
    world.db.refresh(release)
    assert release.status == "succeeded" and not is_held(release.id)


def test_abandoned_releases_are_settled_like_those_a_restart_left(world):
    app = world.app
    world.host.put_app(app.id, {"slug": app.slug, "enabled": True, "env": {}})
    world.host.jobs["job-9"] = {"app_id": app.id, "sha": SHA_2}
    world.host.calls.clear()
    long_ago = datetime.utcnow() - timedelta(hours=1)
    other = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Other")
    third = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Third")
    fourth = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Fourth")
    world.db.add_all(
        [
            # The host has the job: only it knows how the job ends.
            AppRelease(id="rel-with-job", app_id=app.id, kind="deploy", status="running", commit_sha=SHA_2, ref="main", host_job_id="job-9", steps_json="[]", started_at=long_ago),
            AppRelease(id="rel-queued", app_id=other.id, kind="deploy", status="queued", ref="main", steps_json="[]", started_at=long_ago),
            AppRelease(id="rel-rollback", app_id=third.id, kind="rollback", status="running", commit_sha=SHA_1, steps_json="[]", started_at=long_ago),
            # Too young to be called abandoned.
            AppRelease(id="rel-young", app_id=fourth.id, kind="deploy", status="running", ref="main", steps_json="[]"),
        ]
    )
    world.db.commit()

    assert world.deploys.settle_abandoned() == {"resumed": 1, "failed": 2}
    world.db.expire_all()
    assert world.db.get(AppRelease, "rel-with-job").status == "succeeded" and app.current_release_id == "rel-with-job"
    for release_id in ("rel-queued", "rel-rollback"):
        row = world.db.get(AppRelease, release_id)
        assert row.status == "failed" and "unexpected error in the Portal" in row.error_summary
    assert world.db.get(AppRelease, "rel-young").status == "running"
    assert world.deploys.settle_abandoned() == {"resumed": 0, "failed": 0}
    # The caller's own session serves as well (the purge worker passes its own).
    assert world.deploys.settle_abandoned(world.db) == {"resumed": 0, "failed": 0}


def test_a_restart_does_not_touch_what_this_process_is_working_on(world):
    pending = []
    service = make_deploy_service(world.factory, world.host, world.source, spawn=lambda target, name: pending.append(target))
    release = service.start_deploy(world.app, None, world.owner)
    assert service.recover_interrupted() == {"resumed": 0, "failed": 0}
    pending[0]()
    world.db.refresh(release)
    assert release.status == "succeeded"


# --- nothing is held while a pipeline waits -------------------------------------------


def _pooled_world(monkeypatch, tmp_path):
    """A database behind a pool like production's (a file, ``create_engine`` with no pool arguments)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.db import Base

    enable_apps(monkeypatch)
    reset_apps_state()
    engine = create_engine(f"sqlite:///{(tmp_path / 'pool.db').as_posix()}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return engine, factory


def test_a_pipeline_holds_no_database_connection_while_it_waits(monkeypatch, tmp_path):
    engine, factory = _pooled_world(monkeypatch, tmp_path)
    assert type(engine.pool).__name__ == "QueuePool"
    waits = []  # (what the pipeline was waiting for, connections checked out at that moment)

    class WatchedCalls(list):
        def append(self, call):
            waits.append((call[0], engine.pool.checkedout()))
            super().append(call)

    class WatchedHost(FakeHost):
        def _call(self, name, *args):
            waits.append((name, engine.pool.checkedout()))
            super()._call(name, *args)

    host = WatchedHost()
    source = FakeSource()
    waiting = running_job(dependencies="running", start="pending", health="pending", activate="pending")
    host.job_states = [waiting, waiting, waiting, succeeded_job()]
    pending = []
    deploys = make_deploy_service(
        factory,
        host,
        source,
        spawn=lambda target, name: pending.append(target),
        sleep=lambda seconds: waits.append(("sleep", engine.pool.checkedout())),
    )
    db = factory()
    try:
        owner = add_user(db, "owner")
        app = AppService(db, host=host, source=source).create(owner, repo_url=REPO_URL, ref="main", entry_dir="apps/mindmap", name="Mind map")
        release_id = deploys.start_deploy(app, None, owner).id
    finally:
        db.close()  # the request is over: what is checked out from here on is the pipeline's
    source.calls = WatchedCalls()
    waits.clear()
    assert engine.pool.checkedout() == 0

    pending[0]()

    check = factory()
    try:
        assert check.get(AppRelease, release_id).status == "succeeded"
    finally:
        check.close()
    engine.dispose()
    seen = {name for name, _count in waits}
    assert {"resolve_ref", "read_manifest", "read_file", "download_tarball", "put_app", "deploy", "get_job", "get_app", "sleep"} <= seen
    # Not one connection during any call to GitHub or the host, nor during a sleep.
    assert [wait for wait in waits if wait[1] != 0] == []


def test_pipelines_run_on_a_few_threads_and_the_rest_wait_their_turn(caplog):
    from app.services.apps.deploy import _Pipelines

    caplog.set_level(logging.CRITICAL)
    pipelines = _Pipelines(2)
    gate = threading.Event()
    lock = threading.Lock()
    started = threading.Semaphore(0)
    counts = {"running": 0, "most": 0}
    finished = []

    def task(number):
        def run():
            with lock:
                counts["running"] += 1
                counts["most"] = max(counts["most"], counts["running"])
            started.release()
            gate.wait(timeout=30)
            with lock:
                counts["running"] -= 1
                finished.append(number)
            if number == 0:
                raise RuntimeError("a task that crashes does not take its worker with it")

        return run

    for number in range(6):
        pipelines.submit(task(number), f"task-{number}")
    assert started.acquire(timeout=10) and started.acquire(timeout=10)
    # Two run. The third does not start while they do.
    assert not started.acquire(timeout=0.3)
    assert counts == {"running": 2, "most": 2}

    gate.set()
    for _ in range(4):
        assert started.acquire(timeout=10)
    deadline = time.monotonic() + 10
    while len(finished) < 6 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(finished) == [0, 1, 2, 3, 4, 5] and counts["most"] == 2


def test_a_deployment_waits_queued_for_a_free_worker(monkeypatch, tmp_path):
    engine, factory = _pooled_world(monkeypatch, tmp_path)
    gate = threading.Event()

    class WaitingHost(FakeHost):
        """The first job stays running until the gate opens."""

        def get_job(self, job_id):
            self.job_states = None if gate.is_set() else [running_job()]
            self.job_reads[job_id] = 0
            return super().get_job(job_id)

    host = WaitingHost()
    source = FakeSource(manifest={"name": "X", "type": "fastapi", "entry": "main:app"})
    # The default way of running a pipeline: one worker here, so the second deployment has to wait.
    deploys = DeployService(factory, host_factory=lambda: host, source_factory=lambda _db: source, workers=1, poll_interval=0.01)
    db = factory()

    def status_of(release_id):
        session = factory()
        try:
            return session.get(AppRelease, release_id).status
        finally:
            session.close()

    def wait_until(condition):
        deadline = time.monotonic() + 30
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.02)
        return condition()

    try:
        owner = add_user(db, "owner")
        service = AppService(db, host=host, source=source)
        first_app = service.create(owner, repo_url=REPO_URL, ref="main", entry_dir="apps/a", name="First")
        second_app = service.create(owner, repo_url=REPO_URL, ref="main", entry_dir="apps/b", name="Second")
        first = deploys.start_deploy(first_app, None, owner).id
        second = deploys.start_deploy(second_app, None, owner).id
        db.commit()

        assert wait_until(lambda: host.names().count("get_job") >= 3)
        assert status_of(first) == "running" and status_of(second) == "queued"
        assert host.names().count("deploy") == 1 and is_held(second)
        # Queued here is in progress: no second deployment of that app, and the row is nobody's orphan.
        with pytest.raises(DeployInProgress):
            deploys.start_deploy(second_app, None, owner)

        gate.set()
        assert wait_until(lambda: status_of(first) == "succeeded" and status_of(second) == "succeeded")
        assert host.names().count("deploy") == 2
        assert wait_until(lambda: not is_held(first) and not is_held(second))
    finally:
        gate.set()
        db.close()
        engine.dispose()


# --- the icon ---------------------------------------------------------------------------


def test_the_icons_hash_is_kept_with_it_and_lists_do_not_read_the_bytes(world):
    from sqlalchemy import event

    from app.models import App
    from app.repositories.app_repo import AppRepository

    world.deploy()
    assert world.app.icon_hash == hashlib.sha1(PNG).hexdigest()

    statements = []
    engine = world.db.get_bind()

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        fresh = world.factory()
        listed = AppRepository(fresh).list_by_owner(world.owner.id) + AppRepository(fresh).list_where(None) + AppRepository(fresh).list_all()
        assert [row.icon_hash for row in listed] == [hashlib.sha1(PNG).hexdigest()] * 3
        assert not any("icon_data" in statement for statement in statements)
        # The bytes are read when somebody asks for them, and only then.
        assert fresh.get(App, world.app.id).icon_data == PNG
        assert any("icon_data" in statement for statement in statements)
        fresh.close()
    finally:
        event.remove(engine, "before_cursor_execute", before)

    # A manifest without an icon clears the hash with it.
    world.source.manifest = {key: value for key, value in MANIFEST.items() if key != "icon"}
    world.source.refs["main"] = SHA_2
    world.deploy()
    assert world.app.icon_data is None and world.app.icon_content_type is None and world.app.icon_hash is None
