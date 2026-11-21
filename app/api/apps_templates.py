"""Templates (``/api/apps``): publish versions of an app, create an app from one, upgrade it.

Cookie-authenticated like the owner API (app/api/apps.py), whose error shape,
serialisers and test seams these routes share: the host, the repository
reader and the deployments are reached through that module, so whatever
replaces them there is used here too. The rules are in
app/services/apps/templates.py.

Registered before the owner API, so ``/api/apps/templates`` is not read as an
app id.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api import apps as apps_api
from app.db import get_db
from app.deps import get_current_user
from app.models.app import AppTemplateVersion
from app.models.user import User
from app.schemas.app import (
    AppCreateResponse,
    AppFromTemplateRequest,
    AppReleaseResponse,
    AppResponse,
    AppTemplateCard,
    AppTemplateListResponse,
    AppTemplateRequest,
    AppTemplateTag,
    AppTemplateTagsResponse,
    AppTemplateVersionCreateRequest,
    AppTemplateVersionListResponse,
    AppTemplateVersionResponse,
    AppUpgradeRequest,
    AppUserRef,
)
from app.services.apps.access import can_manage, display_name_of
from app.services.apps.errors import AppError
from app.services.apps.templates import TemplateService

router = APIRouter(
    prefix="/api/apps",
    tags=["apps"],
    route_class=apps_api.AppErrorRoute,
    dependencies=[Depends(apps_api.require_apps_enabled)],
)


def _templates(db: Session) -> TemplateService:
    return TemplateService(db, source=apps_api.source_client(db))


def _user_ref(db: Session, user_id: Optional[int], cache: dict[int, Optional[User]]) -> Optional[AppUserRef]:
    if user_id is None:
        return None
    if user_id not in cache:
        cache[user_id] = db.get(User, user_id)
    user = cache[user_id]
    if user is None:
        return None
    return AppUserRef(id=int(user.id), username=str(user.username), display_name=display_name_of(user))


def _version_response(db: Session, version: AppTemplateVersion, cache: Optional[dict] = None) -> AppTemplateVersionResponse:
    return AppTemplateVersionResponse(
        id=version.id,
        tag=version.tag,
        commit_sha=version.commit_sha,
        notes=version.notes,
        published_at=version.published_at,
        published_by=_user_ref(db, version.published_by_user_id, cache if cache is not None else {}),
    )


# --- the list members create from -------------------------------------------------------


@router.get("/templates", response_model=AppTemplateListResponse)
def list_templates(
    q: Optional[str] = Query(default=None, description="Search in name, address and description."),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The templates the member may create an app from: template apps they may open that have a version."""
    cache: dict[int, Optional[User]] = {}
    cards = []
    for app, latest, count in _templates(db).list_templates(user, q=q):
        card = apps_api._app_response(db, app, user).model_dump()
        cards.append(AppTemplateCard(**card, latest_version=_version_response(db, latest, cache), versions_count=count))
    return AppTemplateListResponse(templates=cards)


@router.post("/from-template", response_model=AppCreateResponse, status_code=status.HTTP_201_CREATED)
def create_from_template(payload: AppFromTemplateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """A new app of the caller's from a template version: the template's repository and directory, pinned to the version's commit."""
    template, version = _templates(db).for_create(user, payload.template_app_id, payload.version_id)
    service = apps_api._service(db)
    app = service.create(
        user,
        repo_url=template.repo_url,
        # The commit the version was published at, never the tag: a tag can be moved.
        ref=version.commit_sha,
        entry_dir=template.entry_dir,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        visibility=payload.visibility,
        template_app_id=template.id,
        template_version_id=version.id,
        skip_ownership_check=True,
    )
    release = None
    deploy_error = None
    if payload.deploy:
        try:
            release = apps_api.deploy_service.start_deploy(app, None, user)
        except AppError as exc:
            deploy_error = exc.to_payload()
    db.refresh(app)
    return AppCreateResponse(
        app=apps_api._app_response(db, app, user, with_env=True),
        release=apps_api._release_response(db, app, release) if release is not None else None,
        deploy_error=deploy_error,
    )


# --- one app as a template ------------------------------------------------------------------


@router.put("/{app_id}/template", response_model=AppResponse)
def set_template(app_id: str, payload: AppTemplateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Offer the app as a template, or stop offering it."""
    app = apps_api._service(db).get_for_manage(app_id, user)
    _templates(db).set_template(app, user, payload.is_template)
    # The page takes this answer as the app it shows: it carries what the single-app read carries.
    return apps_api._app_response(db, app, user, with_env=True)


@router.get("/{app_id}/template/tags", response_model=AppTemplateTagsResponse)
def list_template_tags(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The tags of the app's repository (one page, newest first) and which of them are published already."""
    app = apps_api._service(db).get_for_manage(app_id, user)
    return AppTemplateTagsResponse(tags=[AppTemplateTag(**item) for item in _templates(db).repository_tags(app)])


@router.get("/{app_id}/template/versions", response_model=AppTemplateVersionListResponse)
def list_template_versions(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The app's published versions, newest first. Empty for an app that is not offered as a template (its owner still sees them)."""
    app = apps_api._service(db).get_for_view(app_id, user)
    if not app.is_template and not can_manage(user, app):
        return AppTemplateVersionListResponse(versions=[])
    cache: dict[int, Optional[User]] = {}
    return AppTemplateVersionListResponse(versions=[_version_response(db, version, cache) for version in _templates(db).versions(app)])


@router.post("/{app_id}/template/versions", response_model=AppTemplateVersionResponse, status_code=status.HTTP_201_CREATED)
def publish_template_version(
    app_id: str, payload: AppTemplateVersionCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)
):
    """Publish a tag as a version. The tag is resolved to its commit and the manifest there is checked, now and never again."""
    app = apps_api._service(db).get_for_manage(app_id, user)
    version = _templates(db).publish(app, user, payload.tag, payload.notes)
    return _version_response(db, version)


@router.delete("/{app_id}/template/versions/{version_id}")
def delete_template_version(app_id: str, version_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Withdraw a version. Apps created from it keep running its commit."""
    app = apps_api._service(db).get_for_manage(app_id, user)
    _templates(db).remove_version(app, user, version_id)
    return {"ok": True}


# --- an app created from a template ---------------------------------------------------------


@router.post("/{app_id}/upgrade", response_model=AppReleaseResponse, status_code=status.HTTP_202_ACCEPTED)
def upgrade_app(app_id: str, payload: AppUpgradeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Move an app created from a template to another of the template's versions. The answer is the new release; follow it with GET."""
    app = apps_api._service(db).get_for_manage(app_id, user)
    version = _templates(db).for_upgrade(app, payload.version_id)
    release = apps_api.deploy_service.start_deploy(app, version.commit_sha, user, template_version_id=version.id)
    return apps_api._release_response(db, app, release)
