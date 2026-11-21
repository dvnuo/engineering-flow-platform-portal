"""App tokens: one per app, ``efpa_`` and 43 url-safe characters (docs/CONTRACTS.md, section 1).

The app's Python backend sends the token to the SDK endpoints; that is all
the SDK endpoints accept. The Portal keeps a SHA-256 hash to look an app up
by and the value itself, because the owner may read it on the app's page and
every deployment hands it to the host as ``EFP_APP_TOKEN``. Resetting makes a
new value, which is valid at once (the old one is not), and tells the host,
which restarts the app with it. Neither value is ever logged.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.app import App
from app.repositories.app_repo import AppRepository
from app.services.apps.audit import ACTION_TOKEN_RESET, record_app_event
from app.services.apps.errors import AppHostError
from app.services.apps.host_client import HOST_APP_NOT_FOUND, AppHostClient
from app.services.apps.host_spec import app_env

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "efpa_"
TOKEN_RE = re.compile(r"^efpa_[A-Za-z0-9_-]{43}$")
SOURCE_CLUSTER = "cluster"
SOURCE_EXTERNAL = "external"
TOKEN_SOURCES = (SOURCE_CLUSTER, SOURCE_EXTERNAL)
USE_NOTE_INTERVAL_SECONDS = 60.0

_use_lock = threading.Lock()
_use_noted_at: dict[str, float] = {}


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def new_visitor_key() -> str:
    """32 random bytes as base64url, with its padding so any decoder reads it."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def find_app_by_token(db: Session, value: Any) -> Optional[App]:
    """The app a token belongs to; None for anything else (a deleted app's token included).

    The lookup is by hash, and the row's hash is compared in constant time,
    so neither the database's comparison nor this one tells how close a
    guess was. Whether the app is enabled is the caller's question.
    """
    if not isinstance(value, str) or not TOKEN_RE.match(value):
        return None
    wanted = hash_token(value)
    app = AppRepository(db).get_by_token_hash(wanted)
    if app is None or not hmac.compare_digest(str(app.token_hash or ""), wanted):
        return None
    return app


def note_token_use(db: Session, app: App, source: str, *, now: Optional[datetime] = None) -> bool:
    """Record when and from where the token was last used, at most once a minute per app.

    ``source`` is ``cluster`` or ``external``. Returns whether a row was
    written (and committed, on its own statement: the caller's objects are
    left as they are).
    """
    normalized = source if source in TOKEN_SOURCES else SOURCE_EXTERNAL
    moment = time.monotonic()
    with _use_lock:
        last = _use_noted_at.get(app.id)
        if last is not None and moment - last < USE_NOTE_INTERVAL_SECONDS:
            return False
        _use_noted_at[app.id] = moment
    try:
        db.execute(
            update(App)
            .where(App.id == app.id)
            # updated_at is named so that it keeps its value: left out, the
            # column's own rule would stamp it with every use of the token and
            # "last changed" would come to mean "last called".
            .values(token_last_used_at=now or datetime.utcnow(), token_last_used_source=normalized, updated_at=App.updated_at)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must never fail an SDK call
        db.rollback()
        logger.warning("apps: recording the last use of an app token failed app_id=%s", app.id, exc_info=True)
        return False
    return True


def reset_use_notes() -> None:
    """Forget the once-a-minute bookkeeping (tests)."""
    with _use_lock:
        _use_noted_at.clear()


@dataclass(frozen=True)
class HostSync:
    """Whether the host has what the Portal just changed, and why not."""

    synced: bool
    error: Optional[AppHostError] = None

    @property
    def error_code(self) -> Optional[str]:
        return self.error.code if self.error is not None else None

    @property
    def error_message(self) -> Optional[str]:
        return self.error.message if self.error is not None else None


def push_env(app: App, host: Optional[AppHostClient] = None) -> HostSync:
    """Send the app's environment (its token included) to the host, which restarts the app when it runs.

    A host that has never heard of the app has nothing to update: the next
    deployment brings the environment with it.
    """
    client = host or AppHostClient()
    try:
        client.put_env(app.id, app_env(app))
    except AppHostError as exc:
        if exc.host_code == HOST_APP_NOT_FOUND:
            return HostSync(synced=True)
        return HostSync(synced=False, error=exc)
    return HostSync(synced=True)


def reset_token(db: Session, app: App, actor_user_id: Optional[int], *, host: Optional[AppHostClient] = None) -> HostSync:
    """Give the app a new token. The old one stops working at once.

    The new value is stored first and then sent to the host. When the host
    cannot be told, the app keeps running with the old value (and its SDK
    calls fail) until it is restarted or deployed again; the result says so.
    """
    value = new_token()
    app.token_value = value
    app.token_hash = hash_token(value)
    app.token_last_used_at = None
    app.token_last_used_source = None
    db.add(app)
    db.commit()
    db.refresh(app)
    with _use_lock:
        _use_noted_at.pop(app.id, None)
    sync = push_env(app, host)
    record_app_event(
        db,
        app.id,
        ACTION_TOKEN_RESET,
        actor_user_id=actor_user_id,
        detail={"host_synced": sync.synced, **({"host_error": sync.error_code} if sync.error_code else {})},
    )
    return sync
