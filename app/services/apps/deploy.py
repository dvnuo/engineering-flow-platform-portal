"""Deployments and rollbacks of apps.

A deployment is an ``app_releases`` row and a background thread with its own
database session (the request that asked for it returns at once and the
page follows the row):

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

One deployment per app at a time. A rollback asks the host to switch back to
the release it kept (``POST /v1/apps/{id}/activate``) and is recorded as a
release of kind ``rollback``. A Portal that restarts in the middle picks the
host's job up again when one was already started, and fails the row
otherwise: no release stays "running" for ever.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
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
from app.services.apps.error_events import record_deploy_failure
from app.services.apps.errors import (
    AppConflict,
    AppError,
    AppHostError,
    AppHostUnavailable,
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
POLL_INTERVAL_SECONDS = 2.0
# The host gives one deployment 900 seconds by default and may queue it behind another.
POLL_TIMEOUT_SECONDS = 2700.0
# How many polls in a row may find the host unreachable before the Portal gives up on the job.
POLL_MAX_UNREACHABLE = 30
COMMITS_BEHIND_CACHE_SECONDS = 60.0

ICON_MAX_BYTES = 256 * 1024

_start_lock = threading.Lock()


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

    ``session_factory`` makes the background thread's own session;
    ``host_factory`` and ``source_factory`` make the host client and the
    repository reader (tests pass fakes); ``spawn`` runs the pipeline (a
    daemon thread by default, the calling thread in a test).
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Session]] = None,
        *,
        host_factory: Optional[Callable[[], AppHostClient]] = None,
        source_factory: Optional[Callable[[Session], AppSourceService]] = None,
        spawn: Optional[Callable[[Callable[[], None], str], None]] = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        poll_timeout: float = POLL_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._host_factory = host_factory or AppHostClient
        self._source_factory = source_factory or source_for
        self._spawn = spawn or self._spawn_thread
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
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
    def _spawn_thread(target: Callable[[], None], name: str) -> None:
        threading.Thread(target=target, name=name, daemon=True).start()

    @staticmethod
    def _actor_id(requested_by: Any) -> Optional[int]:
        if requested_by is None:
            return None
        if isinstance(requested_by, int):
            return requested_by
        return getattr(requested_by, "id", None)

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
        running for this app.
        """
        db = object_session(app)
        if db is None:
            raise RuntimeError("start_deploy needs an app that belongs to a session")
        wanted_ref = check_ref(ref) if str(ref or "").strip() else app.repo_ref
        if is_from_template(app) and wanted_ref != app.repo_ref and not template_version_id:
            raise template_pinned_error()
        actor_id = self._actor_id(requested_by)
        releases = AppReleaseRepository(db)
        with _start_lock:
            if releases.active_for_app(app.id) is not None:
                raise DeployInProgress()
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
        self._spawn(lambda: self.run_release(release_id), f"app-deploy-{release_id[:8]}")
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
            self._run(db, app, release)
        except Exception:  # noqa: BLE001 - the row must not stay "running" because of a bug
            logger.exception("apps: deployment crashed release_id=%s", release_id)
            self._fail_unexpected(db, release_id)
        finally:
            db.close()

    def _run(self, db: Session, app: App, release: AppRelease) -> None:
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
            resolved = source.resolve_ref(repo, release.ref or app.repo_ref)
            release.commit_sha = resolved.sha
            release.commit_message = resolved.message or None
            steps.finish(step)

            step = "manifest"
            steps.start(step)
            entry_dir = normalize_entry_dir(app.entry_dir)
            manifest = parse_manifest(source.read_manifest(repo, resolved.sha, entry_dir, ref=resolved.ref))
            owner = db.get(User, app.owner_user_id)
            # The owner's Portal username, whoever pressed Deploy.
            check_ownership(manifest, owner.username if owner is not None else "", from_template=bool(app.template_app_id))
            icon, note = self._read_icon(source, repo, resolved.sha, entry_dir, manifest)
            steps.finish(step, note=note)

            step = "fetch"
            steps.start(step)
            host = self._host_factory()
            spec = host_spec(db, app)
            host.put_app(app.id, spec)
            requester = db.get(User, release.requested_by_user_id) if release.requested_by_user_id else None
            with tempfile.TemporaryFile() as bundle:
                source.download_tarball(repo, resolved.sha, bundle)
                bundle.seek(0)
                job_id = host.deploy(
                    app.id,
                    {
                        "sha": resolved.sha,
                        "ref": resolved.ref,
                        "entry_dir": entry_dir,
                        "requested_by": requester.username if requester is not None else "",
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
        """Read the host's job until it ends, mirroring its steps into the row, then finish the release."""
        deadline = time.monotonic() + self.poll_timeout
        unreachable = 0
        job: dict = {}
        while True:
            try:
                job = host.get_job(release.host_job_id)
                unreachable = 0
            except AppHostUnavailable as exc:
                unreachable += 1
                if unreachable > POLL_MAX_UNREACHABLE:
                    self._fail(db, release, app, steps, None, exc)
                    return
                self._sleep(self.poll_interval)
                continue
            except AppHostError as exc:
                self._fail(db, release, app, steps, None, exc)
                return
            tail = job.get("log_tail")
            changed = steps.take_host_steps(job.get("steps"))
            if isinstance(tail, str) and tail != (release.log_tail or ""):
                release.log_tail = tail
                changed = True
            if changed:
                steps.save()
            status = str(job.get("status") or "")
            if status in JOB_ENDED:
                break
            if time.monotonic() > deadline:
                self._fail(
                    db,
                    release,
                    app,
                    steps,
                    None,
                    AppHostError(N_("The app host did not finish the deployment in time."), code="deploy_timeout", status_code=504),
                )
                return
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
            elif not manifest.icon:
                app.icon_data = None
                app.icon_content_type = None
        db.add(app)
        steps.save()
        db.refresh(app)
        audit.record_app_event(
            db,
            app.id,
            audit.ACTION_DEPLOY_SUCCEEDED,
            actor_user_id=release.requested_by_user_id,
            detail={"release_id": release.id, "sha": release.commit_sha, "ref": release.ref},
        )
        with self._behind_lock:
            self._behind_cache = {key: value for key, value in self._behind_cache.items() if key[0] != app.id}
        # What changed while the deployment ran (the audience, a variable) reaches the host now.
        try:
            fresh = host_spec(db, app)
            if spec is None or fresh != spec:
                host.put_app(app.id, fresh)
            status = host.get_app(app.id)
            host_state.record_status(status)
            if manifest is None and status.get("type") and status.get("type") != app.type:
                app.type = str(status["type"])[:16]
                db.add(app)
                db.commit()
        except AppHostError:
            logger.info("apps: the host could not be read back after deploying app_id=%s", app.id)
        logger.info("apps: deployed app_id=%s release_id=%s sha=%s", app.id, release.id, release.commit_sha)

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
            self._fail(
                db,
                release,
                None,
                _Steps(db, release),
                None,
                AppError(N_("The deployment stopped because of an unexpected error in the Portal. Deploy again."), code="internal_error", status_code=500),
            )
        except Exception:  # noqa: BLE001
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
        is kept as failed and the host's error is raised.
        """
        db = object_session(app)
        if db is None:
            raise RuntimeError("start_rollback needs an app that belongs to a session")
        actor_id = self._actor_id(requested_by)
        releases = AppReleaseRepository(db)
        with _start_lock:
            if releases.active_for_app(app.id) is not None:
                raise DeployInProgress()
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
        steps = _Steps(db, release)
        steps.start("activate", SOURCE_HOST)
        try:
            status = self._host_factory().activate(app.id, target.commit_sha)
        except AppHostError as exc:
            self._fail(db, release, app, steps, ("activate", SOURCE_HOST), exc)
            raise
        steps.finish("activate", SOURCE_HOST)
        release.status = RELEASE_SUCCEEDED
        release.finished_at = datetime.utcnow()
        app.current_release_id = release.id
        app.desired_state = DESIRED_RUNNING
        if isinstance(status, dict) and status.get("type"):
            app.type = str(status["type"])[:16]
        db.add(app)
        steps.save()
        db.refresh(app)
        db.refresh(release)
        host_state.record_status(status)
        with self._behind_lock:
            self._behind_cache = {key: value for key, value in self._behind_cache.items() if key[0] != app.id}
        audit.record_app_event(
            db,
            app.id,
            audit.ACTION_ROLLBACK_SUCCEEDED,
            actor_user_id=actor_id,
            detail={"release_id": release.id, "sha": release.commit_sha, "from_release_id": target.id},
        )
        return release

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
        db = self._session()
        try:
            for release in AppReleaseRepository(db).list_active():
                if release.kind == RELEASE_KIND_DEPLOY and release.host_job_id:
                    release_id = release.id
                    self._spawn(lambda release_id=release_id: self.resume_release(release_id), f"app-deploy-{release_id[:8]}")
                    summary["resumed"] += 1
                    continue
                self._fail(
                    db,
                    release,
                    None,
                    _Steps(db, release),
                    None,
                    AppError(N_("The Portal restarted while this deployment was being prepared. Deploy again."), code="portal_restarted", status_code=503),
                )
                summary["failed"] += 1
        finally:
            db.close()
        if summary["resumed"] or summary["failed"]:
            logger.info("apps: releases left by the last run: resumed=%s failed=%s", summary["resumed"], summary["failed"])
        return summary

    def resume_release(self, release_id: str) -> None:
        """Follow a host job that was started before the Portal restarted."""
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
            db.close()


deploy_service = DeployService()
