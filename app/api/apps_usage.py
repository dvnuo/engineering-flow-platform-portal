"""Usage of apps (``/api/apps``): one app's usage page, and the administration's list of every app.

Cookie-authenticated like the owner API (app/api/apps.py), whose error shape,
serialisers and test seams these routes share. The figures are computed in
app/services/apps/usage.py.

Registered before the owner API, so ``/api/apps/admin/...`` is not read as an
app id.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import apps as apps_api
from app.db import get_db
from app.deps import get_current_user
from app.schemas.app import (
    AppAdminHost,
    AppAdminItem,
    AppAdminMetrics,
    AppAdminOverviewResponse,
    AppUsageResponse,
)
from app.services.apps.errors import AppHostError
from app.services.apps.host_state import host_state
from app.services.apps.usage import DEFAULT_DAYS, MAX_DAYS, admin_metrics, app_usage

logger = logging.getLogger(__name__)

# The host's own figures are asked for again once what is held is this old.
HOST_INFO_MAX_AGE = timedelta(seconds=60)

router = APIRouter(
    prefix="/api/apps",
    tags=["apps"],
    route_class=apps_api.AppErrorRoute,
    dependencies=[Depends(apps_api.require_apps_enabled)],
)


@router.get("/admin/overview", response_model=AppAdminOverviewResponse)
def admin_overview(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Every app that is not deleted with its last seven days and its size, and the host's own health. System administrators."""
    apps_api._require_system_admin(user)
    service = apps_api._service(db)
    apps = service.list_all()
    client = service.host

    # One read of every app's status, a few seconds old at most; it also says whether the host answers.
    reachable = host_state.refresh(client)
    info, reported_at = host_state.host()
    if reachable and (reported_at is None or datetime.utcnow() - reported_at > HOST_INFO_MAX_AGE):
        try:
            host_state.record_host(client.host_info())
        except AppHostError:
            reachable = False
        except Exception:  # noqa: BLE001 - the list must render whatever the host does
            logger.warning("apps: reading the host's own figures failed", exc_info=True)
            reachable = False
        info, reported_at = host_state.host()

    metrics = admin_metrics(db, [app.id for app in apps])
    items = [
        AppAdminItem(**apps_api._app_response(db, app, user).model_dump(), metrics=AppAdminMetrics(**metrics[app.id]))
        for app in apps
    ]
    return AppAdminOverviewResponse(host=AppAdminHost(reachable=reachable, info=info, reported_at=reported_at), apps=items)


@router.get("/{app_id}/usage", response_model=AppUsageResponse)
def get_usage(
    app_id: str,
    days: int = Query(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS, description="How many days back, today included."),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Traffic, model and connector calls, visitors, today's budget and the process, for the app's owner and system administrators."""
    service = apps_api._service(db)
    app = service.get_for_manage(app_id, user)
    # The process figures come from the host's last word; ask again when that is more than a few seconds old.
    host_state.refresh(service.host)
    return AppUsageResponse(**app_usage(db, app, days))
