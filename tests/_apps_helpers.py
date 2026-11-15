"""Shared pieces of the Apps tests: an in-memory database, members, and fakes of the host and of GitHub."""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base
from app.models import Group, User, UserAllowlistEntry
from app.services.apps.errors import (
    AppHostError,
    ManifestMissing,
    RefNotFound,
    SourceFileMissing,
)
from app.services.apps.source import RepoRef, ResolvedRef, parse_repo_url

SHA_1 = "1" * 40
SHA_2 = "2" * 40
SHA_3 = "3" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
REPO_URL = "https://github.com/acme/tools"
HOST_TOKEN = "host-secret-token"

MANIFEST = {
    "name": "Mind map",
    "description": "Draw and share mind maps.",
    "icon": "icon.png",
    "type": "fastapi",
    "entry": "main:app",
    "owners": ["owner"],
}


def make_database():
    """``(session factory, engine)`` on one shared in-memory SQLite connection with the whole schema."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session), engine


def enable_apps(monkeypatch, **overrides: Any):
    """Turn the feature on for this test, on the settings object every caller reads."""
    settings = get_settings()
    values = {
        "apps_enabled": True,
        "apps_public_base_url": "https://app.example.com",
        "apps_host_internal_url": "http://efp-apps.efp-apps.svc:9000",
        "apps_host_api_token": HOST_TOKEN,
        "apps_anyone_enabled": False,
        "apps_git_auth_pat": "ghp_service_account_token",
        "apps_github_api_url": "https://api.github.com",
        "apps_max_bundle_mb": 200,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    return settings


def add_group(db, name: str, *, is_default: bool = False) -> Group:
    group = Group(name=name, is_default=is_default, config_json="{}", connector_modes_json="{}")
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def add_user(db, username: str, *, role: str = "user", group: Optional[Group] = None, nickname: Optional[str] = None, allowlisted: bool = True) -> User:
    """A member; on the active allowlist unless told otherwise (that is what "active" means)."""
    user = User(username=username, nickname=nickname, password_hash="x", role=role, is_active=True, group_id=group.id if group else None)
    db.add(user)
    if allowlisted:
        db.add(UserAllowlistEntry(username=username, role=role, is_active=True, group_id=group.id if group else None))
    db.commit()
    db.refresh(user)
    return user


def remove_from_allowlist(db, username: str) -> None:
    entry = db.query(UserAllowlistEntry).filter(UserAllowlistEntry.username == username).one()
    db.delete(entry)
    db.commit()


class FakeSource:
    """GitHub as the deploy pipeline and the app service use it."""

    api_url = "https://api.github.com"

    def __init__(self, manifest: Optional[dict] = None, *, refs: Optional[dict] = None, files: Optional[dict] = None) -> None:
        self.manifest: Any = copy.deepcopy(MANIFEST if manifest is None else manifest)
        self.refs = dict(refs or {"main": SHA_1})
        self.files = dict(files if files is not None else {"icon.png": PNG})
        self.tarball = b"\x1f\x8b" + b"tarball-bytes"
        self.ahead = 0
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    def parse(self, url):
        return parse_repo_url(url, api_url=self.api_url)

    def account_login(self):
        return "efp-bot"

    def ensure_readable(self, repo: RepoRef) -> dict:
        self._maybe_fail("ensure_readable")
        return {"default_branch": "main"}

    def list_branches(self, repo, limit=100):
        return [{"name": name, "sha": sha} for name, sha in self.refs.items()]

    def list_tags(self, repo, limit=100):
        return [{"name": "v1.0.0", "sha": SHA_1}]

    def resolve_ref(self, repo: RepoRef, ref: str) -> ResolvedRef:
        self.calls.append(("resolve_ref", repo.full_name, ref))
        self._maybe_fail("resolve_ref")
        if ref not in self.refs:
            raise RefNotFound(ref=ref, repo=repo.full_name)
        return ResolvedRef(sha=self.refs[ref], ref=ref, message=f"commit on {ref}")

    def read_manifest(self, repo: RepoRef, sha: str, entry_dir: str, *, ref=None) -> bytes:
        self.calls.append(("read_manifest", sha, entry_dir))
        self._maybe_fail("read_manifest")
        if self.manifest is None:
            raise ManifestMissing(path=entry_dir or "/", repo=repo.full_name, ref=ref or sha[:7])
        return self.manifest if isinstance(self.manifest, bytes) else json.dumps(self.manifest).encode("utf-8")

    def read_file(self, repo: RepoRef, sha: str, path: str, *, max_bytes: int) -> bytes:
        self.calls.append(("read_file", sha, path))
        self._maybe_fail("read_file")
        name = path.rsplit("/", 1)[-1]
        if name not in self.files:
            raise SourceFileMissing(path=path, repo=repo.full_name, sha=sha[:7])
        return self.files[name]

    def download_tarball(self, repo: RepoRef, sha: str, dest, *, max_bytes=None) -> int:
        self.calls.append(("download_tarball", sha))
        self._maybe_fail("download_tarball")
        dest.write(self.tarball)
        dest.flush()
        return len(self.tarball)

    def commits_ahead(self, repo: RepoRef, base_sha: str, head_ref: str) -> int:
        self.calls.append(("commits_ahead", base_sha, head_ref))
        self._maybe_fail("commits_ahead")
        return self.ahead


class FakeHost:
    """The app host's manager API, in memory. ``fail[method]`` raises instead; ``job_states`` scripts a job."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.specs: dict[str, dict] = {}
        self.releases: dict[str, list[str]] = {}
        self.env: dict[str, dict] = {}
        self.bundles: list[bytes] = []
        self.deploy_specs: list[dict] = []
        self.deleted: list[tuple] = []
        self.revoked: list[int] = []
        self.fail: dict[str, Exception] = {}
        self.jobs: dict[str, dict] = {}
        self.job_reads: dict[str, int] = {}
        # What get_job answers, one item per read (the last one repeats); None = a plain success.
        self.job_states: Optional[list[Any]] = None
        self.data_bytes = 1024

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def _status(self, app_id: str, state: Optional[str] = None) -> dict:
        shas = self.releases.get(app_id) or []
        spec = self.specs.get(app_id) or {}
        if state is None:
            if spec.get("enabled") is False:
                state = "disabled"
            else:
                state = "running" if shas else "undeployed"
        return {
            "id": app_id,
            "slug": spec.get("slug"),
            "type": "fastapi" if shas else None,
            "state": state,
            "reason": spec.get("disabled_reason") if state == "disabled" else None,
            "release": {"sha": shas[-1], "deployed_at": "2026-11-15T08:30:00Z"} if shas else None,
            "previous_release": {"sha": shas[-2], "deployed_at": "2026-11-14T08:30:00Z"} if len(shas) > 1 else None,
            "uptime_seconds": 5,
            "restarts": 0,
            "rss_bytes": 1000,
            "cpu_percent": 0.5,
            "data_bytes": self.data_bytes,
            "last_activity_at": None,
            "port": 20001,
        }

    def put_app(self, app_id: str, spec: dict) -> dict:
        self._call("put_app", app_id)
        self.specs[app_id] = copy.deepcopy(spec)
        self.env[app_id] = dict(spec.get("env") or {})
        return self._status(app_id)

    def deploy(self, app_id: str, spec: dict, bundle) -> str:
        self._call("deploy", app_id)
        self.bundles.append(bundle.read())
        self.deploy_specs.append(dict(spec))
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs[job_id] = {"app_id": app_id, "sha": spec["sha"]}
        return job_id

    def get_job(self, job_id: str) -> dict:
        self._call("get_job", job_id)
        job = self.jobs[job_id]
        read = self.job_reads.get(job_id, 0)
        self.job_reads[job_id] = read + 1
        if self.job_states is not None:
            state = self.job_states[min(read, len(self.job_states) - 1)]
            if isinstance(state, Exception):
                raise state
        else:
            state = succeeded_job()
        answer = {"id": job_id, "app_id": job["app_id"], "kind": "deploy", "sha": job["sha"], **state}
        if answer.get("status") == "succeeded" and not job.get("activated"):
            job["activated"] = True
            self.releases.setdefault(job["app_id"], []).append(job["sha"])
        return answer

    def get_job_log(self, job_id: str, offset: int = 0):
        self._call("get_job_log", job_id, offset)
        text = "installing\nstarted\n"
        return text[offset:], len(text)

    def activate(self, app_id: str, sha: str) -> dict:
        self._call("activate", app_id, sha)
        shas = self.releases.setdefault(app_id, [])
        if sha not in shas:
            raise AppHostError(code="host_release_not_found", reason="release_not_found", host_code="release_not_found", host_status=404)
        shas.remove(sha)
        shas.append(sha)
        return self._status(app_id)

    def start(self, app_id: str) -> dict:
        self._call("start", app_id)
        return self._status(app_id, "running")

    def stop(self, app_id: str) -> dict:
        self._call("stop", app_id)
        return self._status(app_id, "stopped")

    def restart(self, app_id: str) -> dict:
        self._call("restart", app_id)
        return self._status(app_id, "running")

    def put_env(self, app_id: str, env: dict) -> dict:
        self._call("put_env", app_id)
        if app_id not in self.specs:
            raise AppHostError(code="host_app_not_found", reason="app_not_found", host_code="app_not_found", host_status=404)
        self.env[app_id] = dict(env)
        return self._status(app_id)

    def delete_data(self, app_id: str) -> dict:
        self._call("delete_data", app_id)
        self.data_bytes = 0
        return self._status(app_id)

    def delete_app(self, app_id: str, *, purge_data: bool = False) -> dict:
        self._call("delete_app", app_id, purge_data)
        self.deleted.append((app_id, purge_data))
        self.specs.pop(app_id, None)
        return {"ok": True}

    def get_app(self, app_id: str) -> dict:
        self._call("get_app", app_id)
        if app_id not in self.specs:
            raise AppHostError(code="host_app_not_found", reason="app_not_found", host_code="app_not_found", host_status=404)
        return self._status(app_id)

    def list_apps(self) -> list[dict]:
        self._call("list_apps")
        return [self._status(app_id) for app_id in self.specs]

    def host_info(self) -> dict:
        self._call("host_info")
        return {"version": "0.1.0", "apps_total": len(self.specs)}

    def revoke_sessions(self, user_id: int) -> dict:
        self._call("revoke_sessions", user_id)
        self.revoked.append(int(user_id))
        return {"ok": True}

    def logs(self, app_id: str, tail: int = 200) -> dict:
        self._call("logs", app_id, tail)
        return {"lines": ["line one", "line two"], "truncated": False}


def host_steps(**statuses: str) -> list[dict]:
    """The host's seven steps, all succeeded unless a status is given by name."""
    names = ("unpack", "manifest", "environment", "dependencies", "start", "health", "activate")
    return [
        {"name": name, "status": statuses.get(name, "succeeded"), "started_at": "2026-11-15T08:30:00Z", "finished_at": "2026-11-15T08:30:01Z"}
        for name in names
    ]


def succeeded_job() -> dict:
    return {"status": "succeeded", "steps": host_steps(), "error": None, "log_tail": "started\n", "log_size": 8}


def running_job(**statuses: str) -> dict:
    return {"status": "running", "steps": host_steps(**statuses), "error": None, "log_tail": "installing\n", "log_size": 11}


def failed_job(step: str = "dependencies", message: str = "No matching distribution found for nope==1.0") -> dict:
    names = ("unpack", "manifest", "environment", "dependencies", "start", "health", "activate")
    statuses = {}
    seen = False
    for name in names:
        if name == step:
            statuses[name] = "failed"
            seen = True
        elif seen:
            statuses[name] = "pending"
    return {
        "status": "failed",
        "steps": host_steps(**statuses),
        "error": {"step": step, "message": message},
        "log_tail": f"ERROR: {message}\n",
        "log_size": 60,
    }


def make_deploy_service(session_factory, host: FakeHost, source: FakeSource, **kwargs: Any):
    """A DeployService that runs the pipeline in the calling thread against the fakes."""
    from app.services.apps.deploy import DeployService

    options = {"spawn": lambda target, name: target(), "sleep": lambda seconds: None, "poll_interval": 0.0}
    options.update(kwargs)
    return DeployService(session_factory, host_factory=lambda: host, source_factory=lambda db: source, **options)


def reset_apps_state() -> None:
    """Forget what the feature keeps in memory between requests."""
    from app.services.apps.access import handoff_codes
    from app.services.apps.host_state import host_state
    from app.services.apps.source import reset_account_cache
    from app.services.apps.tokens import reset_use_notes

    handoff_codes.clear()
    host_state.clear()
    reset_account_cache()
    reset_use_notes()

