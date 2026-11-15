"""What the rest of the Portal tells the Apps feature: one call each, never in the caller's way.

- A member signs out: their sessions on the app host end too.
- A member loses or regains access to the Portal: the apps they own are
  disabled or enabled again on the host. (The Portal itself already answers
  the host's access checks and its desired-state read with the owner's
  current standing; this makes the host act on it at once, which is what
  counts for an app open to anyone.)
- The Portal starts: deployments the last run left unfinished are picked up
  or failed, and the worker that removes the data of long-deleted apps starts;
  it stops when the Portal does.

Everything here is best effort and off the request's path: a host that is
down must never fail a sign-out or an administrator's change. Nothing runs
while ``APPS_ENABLED`` is off.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from fastapi import Request

from app.services.apps import audit
from app.services.apps import settings as apps_settings
from app.services.apps.errors import AppHostError

logger = logging.getLogger(__name__)

SYNC_ATTEMPTS = 3
SYNC_RETRY_SECONDS = 5.0


def _spawn(target: Callable[[], None], name: str) -> None:
    threading.Thread(target=target, name=name, daemon=True).start()


# --- sign-out -------------------------------------------------------------------


def revoke_app_sessions(user_id: int, *, host: Any = None) -> bool:
    """End every gateway session of the member on the app host; whether the host took it."""
    from app.services.apps.host_client import AppHostClient

    try:
        (host or AppHostClient()).revoke_sessions(int(user_id))
    except AppHostError as exc:
        logger.info("apps: the member's app sessions could not be ended user_id=%s code=%s", user_id, exc.code)
        return False
    except Exception:  # noqa: BLE001
        logger.warning("apps: ending the member's app sessions failed user_id=%s", user_id, exc_info=True)
        return False
    return True


def end_app_sessions_on_sign_out(request: Request) -> None:
    """Dependency of the Portal's sign-out route: the member's app sessions end with their Portal session.

    Reads who is signing out from the session cookie and tells the host in
    the background. Never raises and never waits for the host.
    """
    try:
        if not apps_settings.apps_enabled():
            return
        from app.security.session_cookie import read_session_token
        from app.services.auth_service import parse_session_token

        # The cookie's name depends on the deployment (host-bound over https):
        # the one helper knows it.
        token = read_session_token(request.cookies)
        user_id = parse_session_token(token) if token else None
        if not user_id:
            return
        _spawn(lambda: revoke_app_sessions(int(user_id)), "app-sessions-revoke")
    except Exception:  # noqa: BLE001 - signing out must work whatever happens here
        logger.warning("apps: the sign-out hook failed", exc_info=True)


# --- a member's access changed -----------------------------------------------------


def sync_member_apps(
    username: str,
    *,
    session_factory: Optional[Callable[[], Any]] = None,
    host: Any = None,
    attempts: int = SYNC_ATTEMPTS,
    retry_seconds: float = SYNC_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Tell the host where the apps ``username`` owns stand now (enabled, or disabled because their owner lost access).

    Also ends the member's own app sessions when they lost access. Returns
    ``{"apps", "synced", "failed"}``.
    """
    from app.repositories.app_repo import AppReleaseRepository, AppRepository
    from app.repositories.user_repo import UserRepository
    from app.services.apps.access import is_member_active
    from app.services.apps.host_client import AppHostClient
    from app.services.apps.host_spec import host_spec
    from app.services.apps.host_state import host_state

    summary = {"apps": 0, "synced": 0, "failed": 0}
    if session_factory is None:
        from app.db import SessionLocal as session_factory  # noqa: N813
    db = session_factory()
    try:
        user = UserRepository(db).get_by_username_case_insensitive(username)
        if user is None:
            return summary
        client = host or AppHostClient()
        active = is_member_active(db, user)
        releases = AppReleaseRepository(db)
        for app in AppRepository(db).list_by_owner(user.id):
            if not (app.current_release_id or releases.has_any(app.id)):
                # The host has never heard of it; a deployment will carry its standing.
                continue
            summary["apps"] += 1
            synced = False
            for attempt in range(max(1, attempts)):
                try:
                    host_state.record_status(client.put_app(app.id, host_spec(db, app)))
                    synced = True
                    break
                except AppHostError as exc:
                    logger.info("apps: owner standing not delivered app_id=%s code=%s attempt=%s", app.id, exc.code, attempt + 1)
                    if attempt + 1 < max(1, attempts):
                        sleep(retry_seconds)
            summary["synced" if synced else "failed"] += 1
            audit.record_app_event(
                db,
                app.id,
                audit.ACTION_OWNER_ACTIVE if active else audit.ACTION_OWNER_INACTIVE,
                detail={"host_synced": synced},
            )
        if not active:
            revoke_app_sessions(user.id, host=client)
    except Exception:  # noqa: BLE001
        logger.exception("apps: syncing a member's apps failed")
    finally:
        db.close()
    return summary


def member_access_changed(username: Any) -> None:
    """A member was removed from, or put back on, the allowlist. Returns at once; the host is told in the background."""
    try:
        if not apps_settings.apps_enabled():
            return
        name = str(username or "").strip()
        if not name:
            return
        _spawn(lambda: sync_member_apps(name), "app-owner-sync")
    except Exception:  # noqa: BLE001 - an administrator's change must go through whatever happens here
        logger.warning("apps: the member-access hook failed", exc_info=True)


# --- startup ------------------------------------------------------------------------


def apps_startup() -> None:
    """Called once when the Portal starts: finish or fail the deployments the last run left behind, start the purge worker."""
    try:
        if not apps_settings.apps_enabled():
            return
        from app.services.apps.deploy import deploy_service

        deploy_service.recover_interrupted()
    except Exception:  # noqa: BLE001 - the Portal must start whatever state the releases are in
        logger.exception("apps: recovering unfinished deployments failed")
    try:
        from app.services.apps.purge_worker import apps_purge_worker_singleton

        apps_purge_worker_singleton.start()
    except Exception:  # noqa: BLE001
        logger.exception("apps: the purge worker could not be started")


def apps_shutdown() -> None:
    """Called once when the Portal stops."""
    try:
        from app.services.apps.purge_worker import apps_purge_worker_singleton

        apps_purge_worker_singleton.stop()
    except Exception:  # noqa: BLE001
        logger.warning("apps: the purge worker could not be stopped", exc_info=True)
