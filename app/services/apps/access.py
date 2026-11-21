"""Who may open an app, who may manage it, and the one-time codes of the sign-in hand-off.

An app's ``visibility`` uses the Portal's words (app/services/work_visibility.py):

- ``private``: the owner only;
- ``public``: every member of the owner's group;
- ``platform``: every member;
- ``anyone``: no sign-in at all, and only while ``APPS_ANYONE_ENABLED`` is on
  and a system administrator approved this app (``anyone_approved_by``).
  Until both hold the app is open to every member, as ``platform`` is.

System administrators open and manage every app; a group administrator is a
member here, as everywhere else in the Portal's work. "Active" means what
the Portal's own session check means by it: the account is on the active
allowlist. The member is always identified by their Portal account, never
by the identity behind a connector's credentials.

A hand-off code proves to the app host that the Portal let a member into an
app (or into the app center, which lists what they may open): random, valid
for 60 seconds, usable once, bound to the app and the member. The Portal is
one process, so the codes live in memory; they sit behind
``HandoffCodeStore`` so they can move to the database.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import or_

from app.models.app import App
from app.services.apps import settings as apps_settings
from app.services.apps.names import DISABLED_OWNER_INACTIVE, VISIBILITY_ANYONE
from app.services.authorization import is_system_admin
from app.services.work_visibility import VISIBILITY_PLATFORM, VISIBILITY_PRIVATE, can_see, visibility_clause

HANDOFF_CODE_TTL_SECONDS = 60
HANDOFF_MAX_CODES = 10000
# The ``app_id`` of a code that lets a member into the app center, which is
# no app (docs/CONTRACTS.md, section 6.5).
HANDOFF_APP_CENTER = ""


# --- members -------------------------------------------------------------------


def is_member_active(db: Any, user: Any) -> bool:
    """Whether the account may still use the Portal: on the active allowlist, as the session check asks."""
    if db is None or user is None:
        return False
    from app.repositories.user_allowlist_repo import UserAllowlistRepository

    return UserAllowlistRepository(db).get_active_by_username(getattr(user, "username", "")) is not None


def display_name_of(user: Any) -> str:
    return str(getattr(user, "nickname", None) or getattr(user, "username", "") or "").strip()


def user_payload(db: Any, user: Any) -> dict[str, Any]:
    """The member as the app host and the SDK know them (docs/CONTRACTS.md, sections 5.1 and 9.1)."""
    from app.repositories.group_repo import GroupRepository

    groups = GroupRepository(db)
    group = groups.get_by_id(getattr(user, "group_id", None)) or groups.get_default()
    return {
        "id": int(user.id),
        "username": str(user.username),
        "display_name": display_name_of(user),
        "group_id": group.id if group is not None else None,
        "group_name": group.name if group is not None else "",
    }


# --- apps ------------------------------------------------------------------------


def effective_visibility(app: Any) -> str:
    """The audience in force: ``anyone`` counts only while the platform allows it and this app is approved."""
    visibility = str(getattr(app, "visibility", "") or VISIBILITY_PRIVATE).strip().lower()
    if visibility != VISIBILITY_ANYONE:
        return visibility
    if apps_settings.anyone_enabled() and getattr(app, "anyone_approved_by", None) is not None:
        return VISIBILITY_ANYONE
    return VISIBILITY_PLATFORM


def anyone_pending_approval(app: Any) -> bool:
    """The owner asked for ``anyone`` and it is not in force (yet)."""
    return str(getattr(app, "visibility", "") or "") == VISIBILITY_ANYONE and effective_visibility(app) != VISIBILITY_ANYONE


def can_manage(user: Any, app: Any) -> bool:
    """The owner or a system administrator: whoever may change the app or read its secrets."""
    if user is None or app is None:
        return False
    return is_system_admin(user) or getattr(app, "owner_user_id", None) == getattr(user, "id", None)


def can_open(user: Any, app: Any, db: Any = None) -> bool:
    """Whether ``user`` (None for a visitor without a session) may open ``app``.

    Says nothing about the app being enabled or deployed, nor about the
    member still being active: the callers check those (``app_enabled``,
    ``is_member_active``). ``db`` defaults to the app's own session.
    """
    if app is None or getattr(app, "deleted_at", None) is not None:
        return False
    visibility = effective_visibility(app)
    if visibility == VISIBILITY_ANYONE:
        return True
    if user is None:
        return False
    if visibility != str(getattr(app, "visibility", "") or ""):
        # ``anyone`` that is not in force: every member, as ``platform``.
        return True
    return can_see(app, user, db)


def openable_clause(user: Any, *, as_member: bool = False):
    """A SQLAlchemy filter for the apps ``user`` may open by the visibility rules alone; None = no filter.

    A system administrator gets no filter, unless ``as_member`` asks for what
    is shared with them the way it would be with any member. ``anyone`` apps
    are open to every member whether or not the audience is in force.
    """
    if as_member and is_system_admin(user):
        user = SimpleNamespace(id=getattr(user, "id", None), role="user", group_id=getattr(user, "group_id", None))
    clause = visibility_clause(App, user)
    if clause is None:
        return None
    return or_(clause, App.visibility == VISIBILITY_ANYONE)


def app_enabled(db: Any, app: Any) -> tuple[bool, Optional[str]]:
    """``(enabled, why not)``: an administrator took it down, or its owner no longer has access."""
    reason = str(getattr(app, "disabled_reason", None) or "").strip()
    if reason:
        return False, reason
    from app.models.user import User

    owner = db.get(User, app.owner_user_id) if db is not None else None
    if owner is None or not is_member_active(db, owner):
        return False, DISABLED_OWNER_INACTIVE
    return True, None


# --- hand-off codes ----------------------------------------------------------------


@dataclass(frozen=True)
class HandoffGrant:
    app_id: str
    user_id: int


class HandoffCodeStore:
    """One-time codes in memory. ``issue`` and ``redeem`` are the whole interface."""

    def __init__(self, *, ttl_seconds: float = HANDOFF_CODE_TTL_SECONDS, clock=time.monotonic) -> None:
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._codes: dict[str, tuple[HandoffGrant, float]] = {}

    def _prune(self, now: float) -> None:
        expired = [code for code, (_grant, expires_at) in self._codes.items() if expires_at <= now]
        for code in expired:
            self._codes.pop(code, None)
        # A flood of launches must not grow the table without bound: the oldest go first.
        overflow = len(self._codes) - HANDOFF_MAX_CODES
        if overflow > 0:
            for code in sorted(self._codes, key=lambda item: self._codes[item][1])[:overflow]:
                self._codes.pop(code, None)

    def issue(self, app_id: str, user_id: int) -> str:
        code = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._prune(now)
            self._codes[code] = (HandoffGrant(app_id=str(app_id), user_id=int(user_id)), now + self._ttl)
        return code

    def redeem(self, code: Any) -> Optional[HandoffGrant]:
        """The grant behind ``code``, once: the code is gone whether or not it was still valid."""
        if not isinstance(code, str) or not code:
            return None
        now = self._clock()
        with self._lock:
            entry = self._codes.pop(code, None)
            self._prune(now)
        if entry is None:
            return None
        grant, expires_at = entry
        return grant if expires_at > now else None

    def clear(self) -> None:
        with self._lock:
            self._codes.clear()


handoff_codes = HandoffCodeStore()


# --- the ``next`` of a launch ---------------------------------------------------------


def safe_app_next(slug: str, value: Any) -> str:
    """Where the visitor wanted to go inside the app: a path under ``/{slug}/``, or ``/{slug}/`` itself.

    The app host checks the same rule before it redirects (docs/CONTRACTS.md,
    section 6.3); the Portal never passes on anything that is not a path of
    this one app.
    """
    home = f"/{slug}/"
    text = str(value or "")
    if not text.startswith(home) or len(text) > 2000:
        return home
    if "\\" in text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return home
    path = text.split("?", 1)[0].split("#", 1)[0]
    # A browser reads "%2e" in a path segment as a dot, so "%2e%2e" climbs like "..".
    if any(segment.lower().replace("%2e", ".") in {".", ".."} for segment in path.split("/")):
        return home
    return text
