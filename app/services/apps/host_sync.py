"""Telling the app host again what every app is.

The host hears about an app when somebody changes it: the change is sent
first and saved when the host accepted it (app/services/apps/app_service.py).
That leaves the changes nobody sent. An owner lost their access while the
host was away for the few seconds the Portal tries (member_hooks). The
platform's switch for apps without sign-in was turned off, which changes the
audience in force of every such app at once. A call timed out that the host
carried out. For most apps the Portal still has the last word, because the
gateway asks it about every signed-in visitor. An app open to anyone is
served from the host's own record alone: there a missed change stays until
somebody says it again.

So the Portal says it again, when it starts and on every round of the purge
worker: ``PUT /v1/apps/{id}`` (docs/CONTRACTS.md, section 4.1) for every app
the host knows and that is not deleted. The call is idempotent: a record that
did not change changes nothing on the host, and an app restarts only when its
environment did. The status the host answers with is also where a deployment
the Portal gave up on shows that it succeeded after all
(``deploy.adopt_host_release``).

Nothing here runs while ``APPS_ENABLED`` is off, and nothing here is in the
way of a request or of the Portal's start: the callers run it on a thread of
its own, and a host that does not answer ends the round at once.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.models.app import App
from app.repositories.app_repo import AppReleaseRepository, AppRepository
from app.services.apps import settings as apps_settings
from app.services.apps.deploy import adopt_host_release
from app.services.apps.errors import AppHostError, AppHostUnavailable
from app.services.apps.host_client import AppHostClient
from app.services.apps.host_spec import host_spec
from app.services.apps.host_state import host_state

logger = logging.getLogger(__name__)


def _put(db: Any, client: Any, app_id: str) -> Optional[dict]:
    """Send one app's record; the host's status of it, or None when the app is gone by now.

    The record is read, the connection let go, and the host called. A change
    saved in the meantime (somebody edited the app while the host was being
    told) would be overwritten on the host by what was read before it, so the
    row is read once more afterwards and sent again when it differs.
    """
    apps = AppRepository(db)
    app = apps.get_by_id(app_id)
    if app is None:
        return None
    spec = host_spec(db, app)
    db.rollback()
    status = client.put_app(app_id, spec)
    app = apps.get_by_id(app_id)
    if app is None:
        # Deleted while the host was being told: the record this call may have put back goes again.
        db.rollback()
        client.delete_app(app_id, purge_data=False)
        return None
    fresh = host_spec(db, app)
    db.rollback()
    if fresh != spec:
        status = client.put_app(app_id, fresh)
    return status


def _finish_deletions(db: Any, client: Any) -> int:
    """Take off the host what the Portal already calls deleted; how many apps the host was asked about.

    A deletion marks the app first and calls the host after
    (``AppService.delete``). A Portal that stopped between the two left an app
    running on the host that no page shows any more. Asked for after a start
    only: that is when such a gap can have opened. The host answers "ok" for
    an app it does not hold, and the data stays where it is.
    """
    app_ids = list(db.scalars(select(App.id).where(App.deleted_at.is_not(None), App.data_purged_at.is_(None))).all())
    db.rollback()
    for app_id in app_ids:
        try:
            client.delete_app(app_id, purge_data=False)
        except AppHostUnavailable:
            raise
        except AppHostError as exc:
            # It does not know the app, or will not let go of it now: the purge worker asks again in its time.
            logger.info("apps: the host did not remove a deleted app app_id=%s code=%s", app_id, exc.code)
    return len(app_ids)


def sync_host_apps(
    *,
    session_factory: Optional[Callable[[], Any]] = None,
    host: Any = None,
    after_start: bool = False,
) -> dict[str, int]:
    """One round: ``{"apps", "synced", "failed", "adopted"}``. Never raises.

    ``after_start`` also finishes the deletions a stopped Portal left half
    done. A host that cannot be reached ends the round (every further app
    would only wait for the same timeout); the next round says it all again.
    """
    summary = {"apps": 0, "synced": 0, "failed": 0, "adopted": 0}
    try:
        if not apps_settings.apps_enabled():
            return summary
        if session_factory is None:
            from app.db import SessionLocal as session_factory  # noqa: N813
        db = session_factory()
    except Exception:  # noqa: BLE001
        logger.exception("apps: the host sync could not start")
        return summary
    try:
        client = host or AppHostClient()
        releases = AppReleaseRepository(db)
        # Only what the host has heard of: an app nobody deployed is sent by its first deployment.
        app_ids = [app.id for app in AppRepository(db).list_all() if app.current_release_id or releases.has_any(app.id)]
        db.rollback()
        summary["apps"] = len(app_ids)
        for position, app_id in enumerate(app_ids):
            try:
                status = _put(db, client, app_id)
            except AppHostUnavailable as exc:
                summary["failed"] += len(app_ids) - position
                logger.info("apps: the host sync stopped, the host does not answer code=%s", exc.code)
                return summary
            except AppHostError as exc:
                summary["failed"] += 1
                logger.info("apps: the host refused an app's record app_id=%s code=%s", app_id, exc.code)
                continue
            if status is None:
                continue
            summary["synced"] += 1
            host_state.record_status(status)
            app = AppRepository(db).get_by_id(app_id)
            if app is not None and adopt_host_release(db, app, status, host=client) is not None:
                summary["adopted"] += 1
            db.rollback()
        if after_start:
            _finish_deletions(db, client)
    except AppHostError as exc:
        logger.info("apps: the host sync ended early code=%s", exc.code)
    except Exception:  # noqa: BLE001 - housekeeping: the next round tries again
        logger.exception("apps: the host sync failed")
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    if summary["apps"]:
        logger.info(
            "apps: host sync: apps=%s synced=%s failed=%s adopted=%s",
            summary["apps"],
            summary["synced"],
            summary["failed"],
            summary["adopted"],
        )
    return summary
