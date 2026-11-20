"""The deploy pipeline end to end, against a fake host and a fake GitHub."""

import logging
import threading

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
    reset_apps_state,
    running_job,
    succeeded_job,
)
from app.models import AppAuditEvent, AppRelease
from app.services.apps.app_service import AppService
from app.services.apps.deploy import HOST_STEPS, PORTAL_STEPS, DeployService, sniff_icon_type
from app.services.apps.errors import (
    AppConflict,
    AppHostError,
    AppHostUnavailable,
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


def test_a_job_that_never_ends_times_out(world):
    world.deploys.poll_timeout = -1.0
    world.host.job_states = [running_job(start="running", health="pending", activate="pending")]
    release = world.deploy()
    assert release.status == "failed"
    assert "did not finish the deployment in time" in release.error_summary


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
    from app.services.apps.member_hooks import apps_startup

    world.db.add(AppRelease(id="rel-queued", app_id=world.app.id, kind="deploy", status="queued", ref="main", steps_json="[]"))
    world.db.commit()
    monkeypatch.setattr(deploy_module, "deploy_service", world.deploys)
    # The worker that removes the data of long-deleted apps starts with the feature, and only with it.
    from app.services.apps.purge_worker import apps_purge_worker_singleton

    started = []
    monkeypatch.setattr(apps_purge_worker_singleton, "start", lambda: started.append(True))

    enable_apps(monkeypatch, apps_enabled=False)
    apps_startup()
    assert world.db.get(AppRelease, "rel-queued").status == "queued"
    assert started == []

    enable_apps(monkeypatch, apps_enabled=True)
    apps_startup()
    world.db.expire_all()
    assert world.db.get(AppRelease, "rel-queued").status == "failed"
    assert started == [True]
