"""The launch page of an app (``/apps/launch/{slug}``): the Portal's half of the sign-in hand-off.

The app host sends a visitor without a session of its own here
(docs/CONTRACTS.md, section 6.3). A member who is signed in and may open the
app goes straight back with a one-time code and never sees a page; one who is
not signed in goes through the Portal's sign-in first (company SSO directly
when it is on) and comes back here; one who may not open the app reads who
owns it. Only top-level redirects are used: the Portal offers no
credentialed cross-origin endpoint for this.

``next`` is where the visitor was going inside the app. Only a path under
``/{slug}/`` is ever passed on; the app host checks the same rule again.

``/apps/launch`` without a slug is the same hand-off for the app center, the
page at the root of the apps address that lists what a member may open.

Also here: ``/app/admin/apps/panel``, the frame of Administration > Apps (the
rows come from the API, drawn by ``static/js/apps.js``).
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app import web as portal_web
from app.db import SessionLocal
from app.i18n import N_, t
from app.models.user import User
from app.repositories.app_repo import AppRepository
from app.services.apps import settings as apps_settings
from app.services.apps.access import HANDOFF_APP_CENTER, can_open, display_name_of, handoff_codes, safe_app_next
from app.services.authorization import is_system_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["apps-pages"])

LAUNCH_PATH_PREFIX = "/apps/launch/"
CENTER_LAUNCH_PATH = "/apps/launch"
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

PAGE_MISSING = "missing"
PAGE_DENIED = "denied"
PAGE_UNAVAILABLE = "unavailable"


def launch_path(slug: str, next_path: str = "") -> str:
    """The launch page's own address, with ``next`` percent-encoded whole (what the sign-in pages accept as theirs)."""
    path = f"{LAUNCH_PATH_PREFIX}{slug}"
    if next_path and next_path != f"/{slug}/":
        path += f"?next={quote(next_path, safe='')}"
    return path


def _page(request: Request, kind: str, status_code: int, **context) -> Response:
    titles = {
        PAGE_MISSING: t("App not found"),
        PAGE_DENIED: t("No access to this app"),
        PAGE_UNAVAILABLE: t("Apps are not available"),
    }
    return portal_web.templates.TemplateResponse(
        "app_launch.html",
        {"request": request, "title": titles[kind], "kind": kind, **context},
        status_code=status_code,
        headers=dict(NO_STORE),
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=302, headers=dict(NO_STORE))


@router.get("/app/admin/apps/panel", response_class=HTMLResponse)
def admin_apps_panel(request: Request):
    """Administration > Apps: the frame of the panel, for system administrators.

    Only markup: the page fills it from ``GET /api/apps/admin/overview``
    (app/static/js/apps.js), which checks the role again for the data.
    """
    if not apps_settings.apps_enabled():
        raise HTTPException(status_code=404, detail=N_("Not found"))
    user = portal_web._current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail=N_("Unauthorized"))
    if not is_system_admin(user):
        raise HTTPException(status_code=403, detail=N_("Forbidden"))
    return portal_web.templates.TemplateResponse("partials/admin_apps_panel.html", {"request": request})


@router.get("/apps/launch", response_class=HTMLResponse)
def launch_app_center(request: Request):
    """The app center's sign-in (docs/CONTRACTS.md, section 6.5).

    The apps address lists what a member may open, so it needs to know who
    is looking. A signed-in member goes straight back with a code that names
    them and no app; anybody else signs in first and comes back here.
    """
    if not apps_settings.apps_enabled():
        return _page(request, PAGE_MISSING, 404)
    user, reason = portal_web._session_user_access(request)
    if reason is not None and reason in portal_web.ACCESS_DENIED_REASONS:
        return portal_web._redirect_to_unauthorized()
    if user is None or reason is not None:
        sign_in = "/login/sso" if portal_web.sso_enabled() else "/login"
        return _redirect(f"{sign_in}?next={quote(CENTER_LAUNCH_PATH, safe='')}")
    base = apps_settings.public_base_url()
    if not base:
        return _page(request, PAGE_UNAVAILABLE, 503)
    code = handoff_codes.issue(HANDOFF_APP_CENTER, user.id)
    return _redirect(f"{base}/_efp/callback?code={quote(code, safe='')}&next=%2F")


@router.get("/apps/launch/{slug}", response_class=HTMLResponse)
def launch_app(request: Request, slug: str):
    if not apps_settings.apps_enabled():
        return _page(request, PAGE_MISSING, 404)
    db = SessionLocal()
    try:
        app = AppRepository(db).get_by_slug(slug)
        if app is None:
            return _page(request, PAGE_MISSING, 404)
        next_path = safe_app_next(app.slug, request.query_params.get("next"))
        base = apps_settings.public_base_url()

        user, reason = portal_web._session_user_access(request)
        if reason is not None and reason in portal_web.ACCESS_DENIED_REASONS:
            # Signed in, but not (or no longer) on the allowlist.
            return portal_web._redirect_to_unauthorized()
        if user is None or reason is not None:
            if can_open(None, app, db) and base:
                # Open to anyone: there is nothing to sign in for.
                return _redirect(f"{base}{next_path}")
            sign_in = "/login/sso" if portal_web.sso_enabled() else "/login"
            return _redirect(f"{sign_in}?next={quote(launch_path(app.slug, next_path), safe='')}")

        if not can_open(user, app, db):
            owner = db.get(User, app.owner_user_id)
            return _page(
                request,
                PAGE_DENIED,
                403,
                app_name=app.name,
                owner_name=display_name_of(owner) if owner is not None else t("a member"),
                username=display_name_of(user),
            )
        if not base:
            return _page(request, PAGE_UNAVAILABLE, 503)
        code = handoff_codes.issue(app.id, user.id)
        return _redirect(f"{base}/_efp/callback?code={quote(code, safe='')}&next={quote(next_path, safe='')}")
    finally:
        db.close()
