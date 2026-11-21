"""Deployments and rollbacks of apps.

A deployment is an ``app_releases`` row and a pipeline run by one of a few
worker threads, with its own database session (the request that asked for it
returns at once and the page follows the row; a deployment that finds every
worker busy stays ``queued`` until one is free):

1. ``resolve``  the app's branch or tag to a commit;
2. ``manifest`` read ``efp-app.json`` at that commit, validate it, check
   that it names the app's owner, and read the icon;
3. ``fetch``    tell the host what the app is (``PUT /v1/apps/{id}``),
   download the commit's tarball and hand it over
   (``POST /v1/apps/{id}/releases``);

then the host's own steps (``unpack`` ... ``activate``), read from its job
until the job ends. ``steps_json`` holds both lists in order, each entry
marked ``"source": "portal"`` or ``"source": "host"`` (both have a step
called ``manifest``). A step that failed carries ``error``: the Portal's own
error for a Portal step, ``{"code": "host_step_failed", "message": ...}`` with
the host's reason for a host step. On success the app points at the new
release and takes its type, description and icon from the manifest; on
failure the app is left as it was, which is also what the host does.

An app created from a template is pinned: its ``repo_ref`` is the commit of
the template version it is on, so "deploy latest" runs that commit again, and
only an upgrade (``template_version_id``) moves it to another commit. A
template's tag is never resolved again after it was published.

One deployment per app at a time, and none of a disabled app (the host would
run its new code to check it). A rollback asks the host to switch back to the
release it kept (``POST /v1/apps/{id}/activate``) and is recorded as a release
of kind ``rollback``.

Waiting costs nothing but the thread: a pipeline ends its database
transaction before every call to GitHub or the host and before every sleep,
so no pooled connection is held while it waits. The pool is small and the
whole Portal shares it.

The host decides how a job ends, not the Portal's patience: a job the host
reports as queued or running is followed for as long as it takes (the host
has its own time limit for one deployment and fails the jobs its own restart
cut short). The Portal stops following only when the host has not answered
for a long while. Whatever it gave up on is taken over later, when the host
turns out to run that commit after all (``adopt_host_release``).

No release stays "running" with nobody working on it. This process knows
which releases its threads hold. A Portal that restarts picks the host's job
up again when one was already started and fails the row otherwise
(``recover_interrupted``); a row whose thread or request died is settled the
same way once it is ``ABANDONED_AFTER_SECONDS`` old (``settle_abandoned``),
and until then it no longer counts as a deployment in progress
(``active_release``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import logging
import queue
import tempfile
import threading
import time
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session, object_session

from app.i18n import N_, interpolate
from app.models.app import App, AppRelease
from app.models.user import User
from app.repositories.app_repo import AppReleaseRepository
from app.services.apps import audit
from app.services.apps.access import app_enabled
from app.services.apps.error_events import record_deploy_failure
from app.services.apps.errors import (
    AppConflict,
    AppError,
    AppHostError,
    AppHostUnavailable,
    AppNotFound,
    DeployInProgress,
    SourceError,
    SourceFileMissing,
    SourceTooLarge,
)
from app.services.apps.host_client import AppHostClient
from app.services.apps.host_spec import current_sha, host_spec
from app.services.apps.host_state import host_state
from app.services.apps.manifest import Manifest, check_ownership, parse_manifest
from app.services.apps.names import (
    DESIRED_RUNNING,
    RELEASE_FAILED,
    RELEASE_KIND_DEPLOY,
    RELEASE_KIND_ROLLBACK,
    RELEASE_QUEUED,
    RELEASE_RUNNING,
    RELEASE_SUCCEEDED,
)
from app.services.apps.source import AppSourceService, check_ref, normalize_entry_dir, source_for
from app.services.apps.templates import is_from_template, template_pinned_error

logger = logging.getLogger(__name__)

SOURCE_PORTAL = "portal"
SOURCE_HOST = "host"
PORTAL_STEPS = ("resolve", "manifest", "fetch")
HOST_STEPS = ("unpack", "manifest", "environment", "dependencies", "start", "health", "activate")
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
# The code of the error a failed host step carries in the release's step list.
HOST_STEP_FAILED = "host_step_failed"

JOB_ENDED = ("succeeded", "failed")
# Every state the host gives a job (docs/CONTRACTS.md, section 4.3).
JOB_STATUSES = ("queued", "running") + JOB_ENDED
POLL_INTERVAL_SECONDS = 2.0
# How many polls in a row the host may leave unanswered (unreachable, or a
# server error) before the Portal stops following the job: five minutes of
# refused connections, more when each attempt has to time out. A job the host
# does answer for is followed without any limit of the Portal's own.
POLL_MAX_UNREACHABLE = 150
# How many polls in a row may end in an error of the Portal's own (a database
# that was busy for a moment, say) before the pipeline gives up.
POLL_MAX_ERRORS = 5
# Pipelines at once. The host installs one deployment at a time by default, so
# more would only wait there instead of here; each one holds a thread and, for
# a moment, a temporary copy of the repository.
PIPELINE_WORKERS = 4
# How old a queued or running release must be before it counts as abandoned
# when no thread of this Portal holds it. What this process starts it holds
# from the moment the row exists; the wait is for a row another Portal process
# made a moment ago (the old pod during a rolling update), which may still be
# worked on there.
ABANDONED_AFTER_SECONDS = 300.0
COMMITS_BEHIND_CACHE_SECONDS = 60.0

ICON_MAX_BYTES = 256 * 1024

# Held while a request decides whether an app may start a deployment or a
# rollback and writes the row that says so, and while a deletion marks the
# app deleted (app_service.delete): the two never interleave.
start_lock = threading.Lock()

# The releases some thread of this process is working on or has in its queue.
_held_lock = threading.Lock()
_held: set[str] = set()


def _hold(release_id: str) -> None:
    with _held_lock:
        _held.add(str(release_id))


def _let_go(release_id: str) -> None:
    with _held_lock:
        _held.discard(str(release_id))


def is_held(release_id: str) -> bool:
    with _held_lock:
        return str(release_id) in _held


def forget_held() -> None:
    """Forget which releases this process holds (tests)."""
    with _held_lock:
        _held.clear()


def is_abandoned(release: AppRelease, *, now: Optional[datetime] = None) -> bool:
    """Whether a release is still marked queued or running although nobody works on it any more.

    Its thread died without writing the end down, or the request that was
    rolling back did. Nothing will ever finish such a row, so it must not
    stand in the way of the next deployment or of a deletion.
    """
    if release.status not in (RELEASE_QUEUED, RELEASE_RUNNING) or is_held(release.id):
        return False
    started = release.started_at or datetime.min
    return (now or datetime.utcnow()) - started > timedelta(seconds=ABANDONED_AFTER_SECONDS)


def active_release(db: Session, app_id: str, *, now: Optional[datetime] = None) -> Optional[AppRelease]:
    """The deployment or rollback of this app that is in progress, if there is one.

    An abandoned row counts as failed: it is not returned.
    ``DeployService.settle_abandoned`` writes that down (or follows the host's
    job again when there is one).
    """
    release = AppReleaseRepository(db).active_for_app(app_id)
    if release is None or is_abandoned(release, now=now):
        return None
    return release


def disabled_error(reason: Optional[str]) -> AppConflict:
    """What a deployment or a rollback of a disabled app answers: the 409 a start gets, in its own words."""
    return AppConflict(N_("This app is disabled and cannot be deployed."), code="app_disabled", reason=reason)


def _unexpected_error() -> AppError:
    """How a release ends that an error of the Portal's own stopped, or that was found abandoned."""
    return AppError(
        N_("The deployment stopped because of an unexpected error in the Portal. Deploy again."), code="internal_error", status_code=500
    )


def _let_connection_go(db: Session) -> None:
    """End the session's transaction, so its connection is back in the pool while the thread waits.

    A session keeps a connection from its first query to its next commit. A
    pipeline waits for minutes on GitHub and on the host, and fifteen
    connections are all the Portal has: one that waited while holding one
    would take it from every request. Commit, not rollback: whatever the
    pipeline changed up to here is kept.
    """
    db.commit()


def _host_did_not_answer(exc: AppHostError) -> bool:
    """Whether an error of a poll says nothing about the job: the host unreachable, a server error, no JSON.

    Only a refusal of the host's own (a 4xx: it has no such job, it does not
    accept the Portal's token) is an answer that ends the release.
    """
    return isinstance(exc, AppHostUnavailable) or not 400 <= exc.host_status < 500


class _Pipelines:
    """Runs what it is given on at most ``size`` threads; the rest waits its turn, first come first served.

    Daemon threads, started as they are first needed and then kept: a Portal
    that stops does not wait for a deployment to end, and the next start picks
    the release up (``recover_interrupted``).
    """

    def __init__(self, size: int = PIPELINE_WORKERS) -> None:
        self._size = max(1, int(size))
        self._tasks: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._started = 0

    def submit(self, target: Callable[[], None], name: str) -> None:
        self._tasks.put((target, name))
        with self._lock:
            if self._started >= self._size:
                return
            self._started += 1
            number = self._started
        threading.Thread(target=self._work, name=f"app-deploy-worker-{number}", daemon=True).start()

    def _work(self) -> None:
        while True:
            target, name = self._tasks.get()
            try:
                target()
            except Exception:  # noqa: BLE001 - one task must not end the worker
                logger.exception("apps: a deployment worker's task crashed name=%s", name)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def sniff_icon_type(data: bytes) -> Optional[str]:
    """The image type by its first bytes: PNG, JPEG, GIF or WebP; None for anything else (SVG included)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def initial_steps(kind: str = RELEASE_KIND_DEPLOY) -> list[dict]:
    if kind == RELEASE_KIND_ROLLBACK:
        return [{"source": SOURCE_HOST, "name": "activate", "status": STEP_PENDING, "started_at": None, "finished_at": None}]
    steps = [
        {"source": SOURCE_PORTAL, "name": name, "status": STEP_PENDING, "started_at": None, "finished_at": None}
        for name in PORTAL_STEPS
    ]
    steps += [
        {"source": SOURCE_HOST, "name": name, "status": STEP_PENDING, "started_at": None, "finished_at": None}
        for name in HOST_STEPS
    ]
    return steps


def error_payload(exc: AppError) -> dict:
    payload = exc.to_payload()
    return {"code": payload["code"], "message": payload["detail"], "message_key": payload["message_key"], "params": payload["params"]}


class _Steps:
    """The release's step list, written back to the row whenever it changes."""

    def __init__(self, db: Session, release: AppRelease) -> None:
        self.db = db
        self.release = release
        self.items = release.steps or initial_steps(release.kind)

    def _find(self, source: str, name: str) -> dict:
        for item in self.items:
            if item.get("source") == source and item.get("name") == name:
                return item
        item = {"source": source, "name": name, "status": STEP_PENDING, "started_at": None, "finished_at": None}
        self.items.append(item)
        return item

    def save(self) -> None:
        self.release.steps_json = json.dumps(self.items, ensure_ascii=True)
        self.db.add(self.release)
        self.db.commit()

    def start(self, name: str, source: str = SOURCE_PORTAL) -> None:
        item = self._find(source, name)
        item["status"] = STEP_RUNNING
        item["started_at"] = _iso()
        self.save()

    def finish(self, name: str, source: str = SOURCE_PORTAL, *, note: Optional[tuple[str, dict]] = None) -> None:
        item = self._find(source, name)
        item["status"] = STEP_SUCCEEDED
        item["finished_at"] = _iso()
        if note is not None:
            item["note"] = interpolate(note[0], note[1])
            item["note_key"] = note[0]
            item["note_params"] = note[1]
        self.save()

    def fail(self, name: str, error: dict, source: str = SOURCE_PORTAL) -> None:
        item = self._find(source, name)
        item["status"] = STEP_FAILED
        item["finished_at"] = _iso()
        item["error"] = error
        self.skip_pending()
        self.save()

    def skip_pending(self) -> None:
        for item in self.items:
            if item.get("status") in (STEP_PENDING, STEP_RUNNING):
                item["status"] = STEP_SKIPPED

    def take_host_steps(self, host_steps: Any) -> bool:
        """Replace the host's part of the list with what its job reports; whether anything changed."""
        if not isinstance(host_steps, list) or not host_steps:
            return False
        fresh = [
            {
                "source": SOURCE_HOST,
                "name": str(step.get("name") or ""),
                "status": str(step.get("status") or STEP_PENDING),
                "started_at": step.get("started_at"),
                "finished_at": step.get("finished_at"),
            }
            for step in host_steps
            if isinstance(step, dict) and str(step.get("name") or "")
        ]
        if not fresh:
            return False
        merged = [item for item in self.items if item.get("source") != SOURCE_HOST] + fresh
        if merged == self.items:
            return False
        self.items = merged
        return True

    def host_step_failed(self, name: str, message: str) -> None:
        """Put the host's reason on the host step that failed, so a page can show it on that step.

        ``name`` is the step the host's job named; without one (or with one
        the list does not have) the reason goes on the host step marked failed.
        """
        host_steps = [item for item in self.items if item.get("source") == SOURCE_HOST]
        target = next((item for item in host_steps if name and item.get("name") == name), None)
        if target is None:
            target = next((item for item in host_steps if item.get("status") == STEP_FAILED), None)
        if target is not None:
            target["error"] = {"code": HOST_STEP_FAILED, "message": message}


class DeployService:
    """Starts, runs, resumes and rolls back deployments.

    ``session_factory`` makes a pipeline's own session; ``host_factory`` and
    ``source_factory`` make the host client and the repository reader (tests
    pass fakes); ``spawn`` runs the pipeline (one of ``workers`` threads by
    default, the calling thread in a test).
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Session]] = None,
        *,
        host_factory: Optional[Callable[[], AppHostClient]] = None,
        source_factory: Optional[Callable[[Session], AppSourceService]] = None,
        spawn: Optional[Callable[[Callable[[], None], str], None]] = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        workers: int = PIPELINE_WORKERS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._host_factory = host_factory or AppHostClient
        self._source_factory = source_factory or source_for
        self._pipelines = _Pipelines(workers)
        self._spawn = spawn or self._pipelines.submit
        self.poll_interval = poll_interval
        self._sleep = sleep
        self._behind_lock = threading.Lock()
        self._behind_cache: dict[tuple[str, str, str], tuple[Optional[int], float]] = {}

    # ------------------------------------------------------------------ plumbing

    def _session(self) -> Session:
        if self._session_factory is not None:
            return self._session_factory()
        from app.db import SessionLocal

        return SessionLocal()

    @staticmethod
    def _actor_id(requested_by: Any) -> Optional[int]:
        if requested_by is None:
            return None
        if isinstance(requested_by, int):
            return requested_by
        return getattr(requested_by, "id", None)

    def _forget_behind(self, app_id: str) -> None:
        with self._behind_lock:
            self._behind_cache = {key: value for key, value in self._behind_cache.items() if key[0] != app_id}

    def _check_startable(self, db: Session, app: App) -> None:
        """Whether a deployment or a rollback of ``app`` may start now. Called with ``start_lock`` held.

        The app is read again: the request loaded it before it had the lock,
        and a deletion or an administrator's take-down may have come between.
        """
        db.refresh(app)
        if app.deleted_at is not None:
            raise AppNotFound()
        enabled, reason = app_enabled(db, app)
        if not enabled:
            # The host starts the new code to check that it answers: an app
            # that was taken down must not run that way either.
            raise disabled_error(reason)
        releases = AppReleaseRepository(db)
        blocking = releases.active_for_app(app.id)
        if blocking is not None and is_abandoned(blocking):
            # Nobody will finish it: settle it now rather than keep the app waiting for the next sweep.
            self._settle(db, blocking, _unexpected_error())
            blocking = releases.active_for_app(app.id)
        if blocking is not None:
            raise DeployInProgress()

    # ----------------------------------------------------------------- starting

    def start_deploy(
        self,
        app: App,
        ref: Optional[str] = None,
        requested_by: Any = None,
        *,
        template_version_id: Optional[str] = None,
    ) -> AppRelease:
        """Record a deployment of ``app`` and start it in the background; the new release row.

        ``ref`` deploys another branch or tag than the app's own, which the
        app then follows once the deployment succeeded. An app created from a
        template takes no other ref than the commit it is pinned to, unless
        the deployment is an upgrade: ``template_version_id`` names the
        version whose commit ``ref`` is, and the app is on that version once
        the deployment succeeded. DeployInProgress when one is already
        running for this app; 409 ``app_disabled`` for a disabled app.
        """
        db = object_session(app)
        if db is None:
            raise RuntimeError("start_deploy needs an app that belongs to a session")
        wanted_ref = check_ref(ref) if str(ref or "").strip() else app.repo_ref
        if is_from_template(app) and wanted_ref != app.repo_ref and not template_version_id:
            raise template_pinned_error()
        actor_id = self._actor_id(requested_by)
        releases = AppReleaseRepository(db)
        with start_lock:
            self._check_startable(db, app)
            release = releases.create(
                app_id=app.id,
                kind=RELEASE_KIND_DEPLOY,
                ref=wanted_ref,
                requested_by_user_id=actor_id,
                status=RELEASE_QUEUED,
                steps_json=json.dumps(initial_steps(), ensure_ascii=True),
                template_version_id=template_version_id or None,
                started_at=datetime.utcnow(),
            )
            # Held from the moment the row exists, also while it waits for a free worker.
            _hold(release.id)
        audit.record_app_event(
            db,
            app.id,
            audit.ACTION_DEPLOY_REQUESTED,
            actor_user_id=actor_id,
            detail={
                "release_id": release.id,
                "ref": wanted_ref,
                **({"template_version_id": template_version_id} if template_version_id else {}),
            },
        )
        release_id = release.id
        try:
            self._spawn(lambda: self.run_release(release_id), f"app-deploy-{release_id[:8]}")
        except BaseException:
            _let_go(release_id)
            raise
        db.refresh(release)
        return release

    def deploy_latest(self, app: App, requested_by: Any = None) -> AppRelease:
        """Deploy the head of the branch or tag the app follows; for an app created from a template, its pinned commit again."""
        return self.start_deploy(app, None, requested_by)

    # ------------------------------------------------------------------ running

    def run_release(self, release_id: str) -> None:
        """The whole pipeline for one queued release, in the calling thread, with a session of its own."""
        db = self._session()
        try:
            release = db.get(AppRelease, release_id)
            if release is None or release.status != RELEASE_QUEUED:
                return
            app = db.get(App, release.app_id)
            if app is None or app.deleted_at is not None:
                self._fail(db, release, None, _Steps(db, release), None, AppConflict(N_("The app no longer exists."), code="app_not_found"))
                return
            # Asked again here: the release may have waited for a worker, and
            # an administrator may have taken the app down in that time.
            enabled, reason = app_enabled(db, app)
            if not enabled:
                self._fail(db, release, app, _Steps(db, release), None, disabled_error(reason))
                return
            self._run(db, app, release)
        except Exception:  # noqa: BLE001 - the row must not stay "running" because of a bug
            logger.exception("apps: deployment crashed release_id=%s", release_id)
            self._fail_unexpected(db, release_id)
        finally:
            _let_go(release_id)
            db.close()

    def _run(self, db: Session, app: App, release: AppRelease) -> None:
        """The Portal's three steps, then the host's job.

        Each step reads what it needs from the database first, lets the
        connection go, and only then calls GitHub or the host.
        """
        steps = _Steps(db, release)
        release.status = RELEASE_RUNNING
        steps.save()
        step = "resolve"
        manifest: Optional[Manifest] = None
        icon: Optional[tuple[bytes, str]] = None
        spec: Optional[dict] = None
        try:
            steps.start(step)
            source = self._source_factory(db)
            repo = source.parse(app.repo_url)
            wanted_ref = release.ref or app.repo_ref
            _let_connection_go(db)
            resolved = source.resolve_ref(repo, wanted_ref)
            release.commit_sha = resolved.sha
            release.commit_message = resolved.message or None
            steps.finish(step)

            step = "manifest"
            steps.start(step)
            entry_dir = normalize_entry_dir(app.entry_dir)
            owner = db.get(User, app.owner_user_id)
            # The owner's Portal username, whoever pressed Deploy.
            owner_username = owner.username if owner is not None else ""
            from_template = bool(app.template_app_id)
            _let_connection_go(db)
            manifest = parse_manifest(source.read_manifest(repo, resolved.sha, entry_dir, ref=resolved.ref))
            check_ownership(manifest, owner_username, from_template=from_template)
            icon, note = self._read_icon(source, repo, resolved.sha, entry_dir, manifest)
            steps.finish(step, note=note)

            step = "fetch"
            steps.start(step)
            host = self._host_factory()
            spec = host_spec(db, app)
            app_id = app.id
            requester = db.get(User, release.requested_by_user_id) if release.requested_by_user_id else None
            requested_by = requester.username if requester is not None else ""
            _let_connection_go(db)
            host.put_app(app_id, spec)
            with tempfile.TemporaryFile() as bundle:
                source.download_tarball(repo, resolved.sha, bundle)
                bundle.seek(0)
                job_id = host.deploy(
                    app_id,
                    {
                        "sha": resolved.sha,
                        "ref": resolved.ref,
                        "entry_dir": entry_dir,
                        "requested_by": requested_by,
                    },
                    bundle,
                )
            release.host_job_id = job_id
            steps.finish(step)
        except AppError as exc:
            self._fail(db, release, app, steps, step, exc)
            return
        self._follow_job(db, app, release, steps, host, manifest=manifest, icon=icon, spec=spec)

    def _read_icon(
        self, source: AppSourceService, repo: Any, sha: str, entry_dir: str, manifest: Manifest
    ) -> tuple[Optional[tuple[bytes, str]], Optional[tuple[str, dict]]]:
        """``((bytes, content type) or None, a note for the step or None)``. A bad icon never fails a deployment."""
        if not manifest.icon:
            return None, None
        path = f"{entry_dir}/{manifest.icon}" if entry_dir else manifest.icon
        try:
            data = source.read_file(repo, sha, path, max_bytes=ICON_MAX_BYTES)
        except SourceTooLarge:
            return None, (N_("The icon is larger than {limit} KB and was left out."), {"limit": ICON_MAX_BYTES // 1024})
        except SourceFileMissing:
            return None, (N_("The icon {path} was not found and was left out."), {"path": manifest.icon})
        except SourceError:
            return None, (N_("The icon could not be read and was left out."), {})
        content_type = sniff_icon_type(data)
        if content_type is None:
            return None, (N_("The icon must be a PNG, JPEG, GIF or WebP image; it was left out."), {})
        return (data, content_type), None

    def _follow_job(
        self,
        db: Session,
        app: App,
        release: AppRelease,
        steps: _Steps,
        host: AppHostClient,
        *,
        manifest: Optional[Manifest],
        icon: Optional[tuple[bytes, str]],
        spec: Optional[dict],
    ) -> None:
        """Read the host's job until it ends, mirroring its steps into the row, then finish the release.

        The Portal sets the job no time limit of its own. The host does (one
        deployment has its time there, and a job its restart cut short is
        failed by it), and a job waiting its turn behind others is not late.
        The release is given up only when the host says it does not know the
        job, or has not answered ``POLL_MAX_UNREACHABLE`` polls in a row; a job
        that ends well after that is taken over by ``adopt_host_release``.
        """
        job_id = release.host_job_id
        last_tail = release.log_tail or ""
        unanswered = 0
        errors = 0
        unsaved = False
        job: dict = {}
        while True:
            # Nothing of the database is held while the host is asked or the thread sleeps.
            _let_connection_go(db)
            try:
                job = host.get_job(job_id)
            except AppHostError as exc:
                if not _host_did_not_answer(exc):
                    # The host answered: it has no such job (any more), or refuses the Portal.
                    self._fail(db, release, app, steps, None, exc)
                    return
                unanswered += 1
                if unanswered > POLL_MAX_UNREACHABLE:
                    self._fail(db, release, app, steps, None, exc)
                    return
                self._sleep(self.poll_interval)
                continue
            status = str(job.get("status") or "")
            if status not in JOB_STATUSES:
                # An answer that names none of a job's states says as little
                # about the job as no answer does, and is counted as one: with
                # no limit of the Portal's own, it must not be followed for ever.
                unanswered += 1
                if unanswered > POLL_MAX_UNREACHABLE:
                    unreadable = AppHostError(reason="its answer about the deployment job could not be read")
                    self._fail(db, release, app, steps, None, unreadable)
                    return
                self._sleep(self.poll_interval)
                continue
            unanswered = 0
            try:
                tail = job.get("log_tail")
                changed = steps.take_host_steps(job.get("steps")) or unsaved
                if isinstance(tail, str) and tail != last_tail:
                    release.log_tail = tail
                    changed = True
                if changed:
                    unsaved = True
                    steps.save()
                    unsaved = False
                    if isinstance(tail, str):
                        last_tail = tail
                errors = 0
            except Exception:  # noqa: BLE001 - see below
                # The Portal could not write down what the host said (a
                # database that was busy for a moment, say). The job is none
                # the worse for it: ask again rather than call it failed.
                errors += 1
                if errors > POLL_MAX_ERRORS:
                    raise
                logger.warning("apps: mirroring a host job failed, asking again job_id=%s", job_id, exc_info=True)
                db.rollback()
                self._sleep(self.poll_interval)
                continue
            if status in JOB_ENDED:
                break
            _let_connection_go(db)
            self._sleep(self.poll_interval)

        if str(job.get("status")) == "failed":
            error = job.get("error") if isinstance(job.get("error"), dict) else {}
            failed_step = str(error.get("step") or "").strip()
            message = str(error.get("message") or "").strip() or "the app host reported a failure"
            exc = AppHostError(
                N_("The deployment failed at the step {step}: {reason}"),
                code="deploy_failed",
                status_code=422,
                step=failed_step or "unknown",
                reason=message[:2000],
            )
            # The host's own reason, on the step it failed at (the summary carries it too).
            steps.host_step_failed(failed_step, message[:2000])
            self._fail(db, release, app, steps, None, exc)
            return
        self._succeed(db, app, release, steps, host, manifest=manifest, icon=icon, spec=spec)

    def _succeed(
        self,
        db: Session,
        app: App,
        release: AppRelease,
        steps: _Steps,
        host: AppHostClient,
        *,
        manifest: Optional[Manifest],
        icon: Optional[tuple[bytes, str]],
        spec: Optional[dict],
    ) -> None:
        release.status = RELEASE_SUCCEEDED
        release.finished_at = datetime.utcnow()
        release.error_summary = None
        app.current_release_id = release.id
        app.desired_state = DESIRED_RUNNING
        if release.ref and release.ref != app.repo_ref:
            app.repo_ref = release.ref
        if release.template_version_id:
            # An upgrade: the app is on the version whose commit now runs.
            app.template_version_id = release.template_version_id
        if manifest is not None:
            app.type = manifest.type
            if manifest.description:
                app.description = manifest.description
            if icon is not None:
                app.icon_data, app.icon_content_type = icon
                app.icon_hash = hashlib.sha1(icon[0]).hexdigest()
            elif not manifest.icon:
                app.icon_data = None
                app.icon_content_type = None
                app.icon_hash = None
        db.add(app)
        steps.save()
        db.refresh(app)
        app_id, release_id, sha = app.id, release.id, release.commit_sha
        audit.record_app_event(
            db,
            app_id,
            audit.ACTION_DEPLOY_SUCCEEDED,
            actor_user_id=release.requested_by_user_id,
            detail={"release_id": release_id, "sha": sha, "ref": release.ref},
        )
        self._forget_behind(app_id)
        # What changed while the deployment ran (the audience, a variable) reaches the host now.
        try:
            fresh = host_spec(db, app)
            known_type = app.type
            _let_connection_go(db)
            if spec is None or fresh != spec:
                host.put_app(app_id, fresh)
            status = host.get_app(app_id)
            host_state.record_status(status)
            if manifest is None and status.get("type") and status.get("type") != known_type:
                app.type = str(status["type"])[:16]
                db.add(app)
                db.commit()
        except AppHostError:
            logger.info("apps: the host could not be read back after deploying app_id=%s", app_id)
        logger.info("apps: deployed app_id=%s release_id=%s sha=%s", app_id, release_id, sha)

    def _fail(
        self,
        db: Session,
        release: AppRelease,
        app: Optional[App],
        steps: _Steps,
        step: Optional[str | tuple[str, str]],
        exc: AppError,
    ) -> None:
        """End the release as failed. ``step`` is the step that failed (a Portal step's name, or ``(name, source)``);
        without one the error belongs to the release as a whole and whatever had not finished is marked skipped."""
        if step is not None:
            name, source = (step, SOURCE_PORTAL) if isinstance(step, str) else step
            steps.fail(name, error_payload(exc), source)
        else:
            steps.skip_pending()
        release.status = RELEASE_FAILED
        release.finished_at = datetime.utcnow()
        release.error_summary = exc.message[:4000]
        steps.save()
        action = audit.ACTION_ROLLBACK_FAILED if release.kind == RELEASE_KIND_ROLLBACK else audit.ACTION_DEPLOY_FAILED
        audit.record_app_event(
            db,
            release.app_id,
            action,
            actor_user_id=release.requested_by_user_id,
            detail={"release_id": release.id, "sha": release.commit_sha, "code": exc.code, "error": exc.message[:500]},
        )
        logger.info("apps: release failed app_id=%s release_id=%s code=%s", release.app_id, release.id, exc.code)
        # One entry in Administration > Errors per failed release.
        record_deploy_failure(db, release, app, exc)

    def _fail_unexpected(self, db: Session, release_id: str) -> None:
        try:
            db.rollback()
            release = db.get(AppRelease, release_id)
            if release is None or release.status not in (RELEASE_QUEUED, RELEASE_RUNNING):
                return
            self._fail(db, release, None, _Steps(db, release), None, _unexpected_error())
        except Exception:  # noqa: BLE001
            # The row stays as it is; once nobody holds it, it counts as abandoned and is settled (settle_abandoned).
            logger.exception("apps: could not record the failure of release_id=%s", release_id)

    # ----------------------------------------------------------------- rollback

    def rollback_target(self, db: Session, app: App) -> Optional[AppRelease]:
        """The release a rollback would return to: the last successful one that ran other code than the current."""
        return AppReleaseRepository(db).previous_succeeded(
            app.id, current_release_id=app.current_release_id, current_sha=current_sha(db, app)
        )

    def start_rollback(self, app: App, requested_by: Any = None) -> AppRelease:
        """Switch the app back to the release before the current one; the ``rollback`` release row.

        Done within the call: the host only switches a symlink and restarts.
        When the host refuses (it no longer keeps that release, say) the row
        is kept as failed and the host's error is raised. Whatever else goes
        wrong fails the row too: it never stays "running" behind a request
        that is over. 409 ``app_disabled`` for a disabled app.
        """
        db = object_session(app)
        if db is None:
            raise RuntimeError("start_rollback needs an app that belongs to a session")
        actor_id = self._actor_id(requested_by)
        releases = AppReleaseRepository(db)
        with start_lock:
            self._check_startable(db, app)
            target = self.rollback_target(db, app)
            if target is None:
                raise AppConflict(N_("There is no earlier release to return to."), code="no_previous_release")
            release = releases.create(
                app_id=app.id,
                kind=RELEASE_KIND_ROLLBACK,
                commit_sha=target.commit_sha,
                ref=target.ref,
                commit_message=target.commit_message,
                requested_by_user_id=actor_id,
                status=RELEASE_RUNNING,
                steps_json=json.dumps(initial_steps(RELEASE_KIND_ROLLBACK), ensure_ascii=True),
                started_at=datetime.utcnow(),
            )
            _hold(release.id)
        # Read before the host is called, so that the session holds no connection during the call.
        release_id, app_id, target_id, target_sha = release.id, app.id, target.id, target.commit_sha
        try:
            steps = _Steps(db, release)
            steps.start("activate", SOURCE_HOST)
            try:
                status = self._host_factory().activate(app_id, target_sha)
            except AppHostError as exc:
                self._fail(db, release, app, steps, ("activate", SOURCE_HOST), exc)
                raise
            steps.finish("activate", SOURCE_HOST)
            release.status = RELEASE_SUCCEEDED
            release.finished_at = datetime.utcnow()
            app.current_release_id = release_id
            app.desired_state = DESIRED_RUNNING
            if isinstance(status, dict) and status.get("type"):
                app.type = str(status["type"])[:16]
            db.add(app)
            steps.save()
            db.refresh(app)
            db.refresh(release)
            host_state.record_status(status)
            self._forget_behind(app_id)
            audit.record_app_event(
                db,
                app_id,
                audit.ACTION_ROLLBACK_SUCCEEDED,
                actor_user_id=actor_id,
                detail={"release_id": release_id, "sha": target_sha, "from_release_id": target_id},
            )
            return release
        except AppHostError:
            raise
        except Exception:
            # Anything else (the database, a bug): the request is over, so the
            # row is ended here. Left "running", it would keep the app from
            # being deployed or deleted. If the host did switch, the release
            # is taken over when the host says so (adopt_host_release).
            logger.exception("apps: rollback crashed release_id=%s", release_id)
            self._fail_unexpected(db, release_id)
            raise
        finally:
            _let_go(release_id)

    # ------------------------------------------------------------ commits behind

    def commits_behind(self, db: Session, app: App) -> Optional[int]:
        """How many commits the app's branch or tag is ahead of what runs; None when that cannot be told.

        Asked of GitHub at most once a minute per app and deployed commit.
        An app created from a template follows no branch: what it is behind
        is the template's newer version, which its payload names.
        """
        sha = current_sha(db, app)
        if not sha or not app.repo_ref or is_from_template(app):
            return None
        key = (app.id, sha, app.repo_ref)
        now = time.monotonic()
        with self._behind_lock:
            cached = self._behind_cache.get(key)
            if cached is not None and now - cached[1] < COMMITS_BEHIND_CACHE_SECONDS:
                return cached[0]
        try:
            source = self._source_factory(db)
            value: Optional[int] = source.commits_ahead(source.parse(app.repo_url), sha, app.repo_ref)
        except AppError:
            value = None
        except Exception:  # noqa: BLE001 - a figure on a page must not break the page
            logger.warning("apps: comparing commits failed app_id=%s", app.id, exc_info=True)
            value = None
        with self._behind_lock:
            self._behind_cache[key] = (value, now)
        return value

    # ------------------------------------------------------------------ recovery

    def recover_interrupted(self) -> dict[str, int]:
        """After a Portal restart: no release may stay queued or running with nobody working on it.

        A deployment whose host job was already started is followed again to
        its end; one that had not reached the host (and any rollback) is
        marked failed with the reason.
        """
        summary = {"resumed": 0, "failed": 0}
        restarted = AppError(
            N_("The Portal restarted while this deployment was being prepared. Deploy again."), code="portal_restarted", status_code=503
        )
        db = self._session()
        try:
            for release in AppReleaseRepository(db).list_active():
                if is_held(release.id):
                    continue
                summary[self._settle(db, release, restarted)] += 1
        finally:
            db.close()
        if summary["resumed"] or summary["failed"]:
            logger.info("apps: releases left by the last run: resumed=%s failed=%s", summary["resumed"], summary["failed"])
        return summary

    def settle_abandoned(self, db: Optional[Session] = None, *, now: Optional[datetime] = None) -> dict[str, int]:
        """While the Portal runs: end what ``is_abandoned`` (a release whose thread or request died).

        Settled as after a restart. Run on the purge worker's round, and for
        one app at once when its abandoned release stands in the way of a
        deployment (``_check_startable``). ``db`` is the caller's session when
        it has one.
        """
        summary = {"resumed": 0, "failed": 0}
        own = db is None
        session = self._session() if db is None else db
        try:
            for release in AppReleaseRepository(session).list_active():
                if is_abandoned(release, now=now):
                    summary[self._settle(session, release, _unexpected_error())] += 1
        finally:
            if own:
                session.close()
        if summary["resumed"] or summary["failed"]:
            logger.info("apps: abandoned releases: resumed=%s failed=%s", summary["resumed"], summary["failed"])
        return summary

    def _settle(self, db: Session, release: AppRelease, error: AppError) -> str:
        """End one release nobody works on: ``"resumed"`` or ``"failed"``.

        A deployment whose job the host already has is followed again: only
        the host knows how that job ends. Anything else (nothing had reached
        the host; a rollback, which is one call) is marked failed with ``error``.
        """
        if release.kind == RELEASE_KIND_DEPLOY and release.host_job_id:
            release_id = release.id
            _hold(release_id)
            try:
                self._spawn(lambda: self.resume_release(release_id), f"app-deploy-{release_id[:8]}")
            except BaseException:
                _let_go(release_id)
                raise
            return "resumed"
        self._fail(db, release, None, _Steps(db, release), None, error)
        return "failed"

    def resume_release(self, release_id: str) -> None:
        """Follow a host job again whose release nobody was following any more (a Portal restart, a thread that died)."""
        db = self._session()
        try:
            release = db.get(AppRelease, release_id)
            if release is None or release.status not in (RELEASE_QUEUED, RELEASE_RUNNING) or not release.host_job_id:
                return
            app = db.get(App, release.app_id)
            steps = _Steps(db, release)
            if app is None or app.deleted_at is not None:
                self._fail(db, release, None, steps, None, AppConflict(N_("The app no longer exists."), code="app_not_found"))
                return
            release.status = RELEASE_RUNNING
            steps.save()
            # The manifest read before the restart is gone: the app keeps its description and icon.
            self._follow_job(db, app, release, steps, self._host_factory(), manifest=None, icon=None, spec=None)
        except Exception:  # noqa: BLE001
            logger.exception("apps: resuming a deployment crashed release_id=%s", release_id)
            self._fail_unexpected(db, release_id)
        finally:
            _let_go(release_id)
            db.close()


def adopt_host_release(db: Session, app: App, status: Any, *, host: Any = None) -> Optional[AppRelease]:
    """Take over a deployment or a rollback the Portal gave up on and the host finished after all.

    ``status`` is the host's status object of the app (docs/CONTRACTS.md,
    section 4.6). The host changes what it runs only when the Portal asks for
    it. So when it runs another commit than the Portal's current release, and
    the newest release of that commit ended failed here, that release did
    succeed: the Portal stopped following it (the host was away too long, an
    error of the Portal's own), or a call timed out that the host carried out.
    The release becomes the app's current one, as it would have at the time.
    Left as it was, the Portal would call an app "not deployed" that visitors
    are using.

    Called wherever the Portal reads a status from the host anyway
    (``AppService.status``, the round that tells the host about every app).
    ``host``, when given, is asked for the job's own steps and log. Returns
    the release that was adopted, or None.
    """
    host_release = status.get("release") if isinstance(status, dict) else None
    sha = str(host_release.get("sha") or "").strip().lower() if isinstance(host_release, dict) else ""
    if not sha or sha == (current_sha(db, app) or ""):
        return None
    releases = AppReleaseRepository(db)
    if active_release(db, app.id) is not None:
        # Somebody is deploying: that deployment's own end says what is current.
        return None
    candidate = releases.latest_with_sha(app.id, sha)
    if candidate is None or candidate.status != RELEASE_FAILED:
        return None
    current = releases.get(app.current_release_id) if app.current_release_id else None
    if current is not None and candidate.started_at <= current.started_at:
        return None

    job: Any = None
    candidate_id, job_id, app_id = candidate.id, candidate.host_job_id, app.id
    if host is not None and candidate.kind == RELEASE_KIND_DEPLOY and job_id:
        _let_connection_go(db)
        try:
            job = host.get_job(job_id)
        except AppHostError:
            job = None

    with start_lock:
        # Looked at again under the lock: two readers of the same status adopt once.
        db.refresh(candidate)
        db.refresh(app)
        if candidate.status != RELEASE_FAILED or app.deleted_at is not None or active_release(db, app_id) is not None:
            return None
        steps = _Steps(db, candidate)
        for item in steps.items:
            if item.get("status") == STEP_FAILED:
                item["status"] = STEP_SUCCEEDED
                item.pop("error", None)
        if isinstance(job, dict) and str(job.get("status") or "") == "succeeded":
            steps.take_host_steps(job.get("steps"))
            if isinstance(job.get("log_tail"), str):
                candidate.log_tail = job["log_tail"]
        candidate.status = RELEASE_SUCCEEDED
        candidate.finished_at = datetime.utcnow()
        candidate.error_summary = None
        app.current_release_id = candidate_id
        app.desired_state = DESIRED_RUNNING
        if candidate.kind == RELEASE_KIND_DEPLOY:
            # What a deployment that ends well does to the app; the manifest
            # read at the time is gone, so description and icon stay.
            if candidate.ref and candidate.ref != app.repo_ref:
                app.repo_ref = candidate.ref
            if candidate.template_version_id:
                app.template_version_id = candidate.template_version_id
        if isinstance(status, dict) and status.get("type"):
            app.type = str(status["type"])[:16]
        db.add(app)
        steps.save()
    action = audit.ACTION_ROLLBACK_SUCCEEDED if candidate.kind == RELEASE_KIND_ROLLBACK else audit.ACTION_DEPLOY_SUCCEEDED
    audit.record_app_event(
        db,
        app_id,
        action,
        actor_user_id=candidate.requested_by_user_id,
        detail={"release_id": candidate_id, "sha": sha, "adopted": True},
    )
    logger.info("apps: adopted a release the host finished app_id=%s release_id=%s sha=%s", app_id, candidate_id, sha)
    return candidate


deploy_service = DeployService()
