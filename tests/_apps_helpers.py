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
    from app.services.apps.error_events import reset_crash_throttle
    from app.services.apps.host_state import host_state
    from app.services.apps.rate_limit import sdk_rate_limiter
    from app.services.apps.source import reset_account_cache
    from app.services.apps.tokens import reset_use_notes

    handoff_codes.clear()
    host_state.clear()
    reset_account_cache()
    reset_use_notes()
    sdk_rate_limiter.reset()
    reset_crash_throttle()


def add_app(db, owner, *, slug: str = "mindmap", name: str = "Mind map", **fields: Any):
    """An app row as registering one leaves it (nothing deployed), with a token and a visitor key of its own."""
    from app.models import App
    from app.services.apps.tokens import hash_token, new_token, new_visitor_key

    token = new_token()
    values = {
        "slug": slug,
        "name": name,
        "owner_user_id": owner.id,
        "visibility": "private",
        "repo_url": REPO_URL,
        "repo_ref": "main",
        "entry_dir": "apps/mindmap",
        "token_hash": hash_token(token),
        "token_value": token,
        "visitor_key": new_visitor_key(),
    }
    values.update(fields)
    app = App(**values)
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def sign_visitor(app, user, *, expires_at: Optional[int] = None, key: Optional[str] = None, app_id: Optional[str] = None) -> str:
    """An ``X-EFP-Visitor`` value as the gateway signs it (docs/CONTRACTS.md, section 6.4)."""
    import base64
    import hashlib
    import hmac
    import time

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    claims = {
        "app": app_id if app_id is not None else app.id,
        "exp": int(time.time()) + 60 if expires_at is None else int(expires_at),
        "group": None,
        "group_id": getattr(user, "group_id", None),
        "name": getattr(user, "nickname", None) or user.username,
        "sub": int(user.id),
        "username": user.username,
    }
    payload = b64(json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    key_text = (key if key is not None else app.visitor_key).rstrip("=")
    secret = base64.urlsafe_b64decode(key_text + "=" * (-len(key_text) % 4))
    signature = b64(hmac.new(secret, f"v1.{payload}".encode("ascii"), hashlib.sha256).digest())
    return f"v1.{payload}.{signature}"


def owner_api_harness(monkeypatch, *, source: Optional[Any] = None):
    """The Portal's own app behind a test client, signed in as one of five members, with the host and GitHub faked.

    A generator for a fixture: ``yield from owner_api_harness(monkeypatch)``.
    Yields a namespace with ``client``, ``db``, ``factory``, ``users``
    (owner and mate in the group QA, other and admin in Default, gadmin a
    group administrator of QA), ``host``, ``source``, ``as_user(name)``,
    ``create(**fields)`` (registers an app through the API), ``row(app_id)``
    and ``actions(app_id)``.
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import app.api.apps as api
    from app.db import get_db
    from app.deps import get_current_user
    from app.main import app
    from app.models import App, AppAuditEvent

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
    source = source if source is not None else FakeSource()
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
