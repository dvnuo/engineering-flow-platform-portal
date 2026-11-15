"""The owner API of Apps: register, deploy, run and look after apps (``/api/apps``).

Cookie-authenticated like the rest of the Portal's JSON API. Anything that
changes an app or reveals something about it beyond its card (the
repository, the environment, the token, logs, releases) is for the app's
owner and system administrators; the card itself is for whoever may open
the app. An app a member may not open answers 404, like one that does not
exist. Every route answers 404 while ``APPS_ENABLED`` is off.

Errors carry ``detail`` (English, placeholders filled), ``code``,
``message_key`` and ``params`` so a page can show them in the member's
language (app/services/apps/errors.py).
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime
import hashlib
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.i18n import N_
from app.models.app import App, AppAuditEvent, AppRelease
from app.models.user import User
from app.repositories.app_repo import AppAuditRepository, AppReleaseRepository, AppTrafficRepository
from app.repositories.user_repo import UserRepository
from app.schemas.app import (
    AppAnyoneApprovalRequest,
    AppCreateRequest,
    AppCreateResponse,
    AppDataResponse,
    AppDeployRequest,
    AppDisableRequest,
    AppEventListResponse,
    AppEventResponse,
    AppInspectRequest,
    AppInspectResponse,
    AppListResponse,
    AppLogsResponse,
    AppManageFields,
    AppMember,
    AppReleaseListResponse,
    AppReleaseLogResponse,
    AppReleaseResponse,
    AppReleaseSummary,
    AppRepoRefsResponse,
    AppResponse,
    AppSettingsResponse,
    AppStatusResponse,
    AppTemplateInfo,
    AppTemplateVersionRef,
    AppTokenResponse,
    AppTransferRequest,
    AppUpdateRequest,
)
from app.services.apps import audit
from app.services.apps import settings as apps_settings
from app.services.apps import tokens as app_tokens
from app.services.apps.access import (
    anyone_pending_approval,
    app_enabled,
    can_manage,
    effective_visibility,
    user_payload,
)
from app.services.apps.app_service import UNSET, AppService
from app.services.apps.deploy import deploy_service as default_deploy_service
from app.services.apps.errors import AppError, AppForbidden, AppHostError, AppNotFound, AppValidationError
from app.services.apps.host_client import HOST_APP_NOT_FOUND, AppHostClient
from app.services.apps.host_spec import current_sha
from app.services.apps.host_state import host_state
from app.services.apps.names import APP_CONNECTOR_TYPES, APP_TYPES, APP_VISIBILITIES, RESERVED_SLUGS, RELEASE_RUNNING
from app.services.apps.source import AppSourceService, parse_repo_url, source_for, web_host_of
from app.services.apps.templates import template_info
from app.services.authorization import is_system_admin

SCOPE_MINE = "mine"
SCOPE_AVAILABLE = "available"
SCOPE_ALL = "all"
SCOPES = (SCOPE_MINE, SCOPE_AVAILABLE, SCOPE_ALL)


class AppErrorRoute(APIRoute):
    """Turns an AppError raised anywhere in a route into its JSON answer."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def app_error_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except AppError as exc:
                return JSONResponse(exc.to_payload(), status_code=exc.status_code)

        return app_error_handler


def require_apps_enabled() -> None:
    if not apps_settings.apps_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("Not found"))


router = APIRouter(
    prefix="/api/apps",
    tags=["apps"],
    route_class=AppErrorRoute,
    dependencies=[Depends(require_apps_enabled)],
)

# Replaced by tests: how the routes reach the host, the repositories and the deployments.
deploy_service = default_deploy_service


def host_client() -> AppHostClient:
    return AppHostClient()


def source_client(db: Session) -> AppSourceService:
    return source_for(db)


def _service(db: Session) -> AppService:
    return AppService(db, host=host_client(), source=source_client(db))


def _require_system_admin(user: Any) -> None:
    if not is_system_admin(user):
        raise AppForbidden(N_("Only a system administrator can do this"))


# --- serialising -------------------------------------------------------------------


def _member(db: Session, user: Optional[User]) -> Optional[AppMember]:
    return AppMember(**user_payload(db, user)) if user is not None else None


def _release_summary(release: Optional[AppRelease]) -> Optional[AppReleaseSummary]:
    if release is None:
        return None
    return AppReleaseSummary(
        id=release.id,
        kind=release.kind,
        status=release.status,
        commit_sha=release.commit_sha,
        ref=release.ref,
        started_at=release.started_at,
        finished_at=release.finished_at,
    )


def _icon_url(app: App) -> Optional[str]:
    if not app.icon_data:
        return None
    version = hashlib.sha1(app.icon_data).hexdigest()[:12]
    return f"/api/apps/{app.id}/icon?v={version}"


def _repo_links(app: App) -> tuple[Optional[str], Optional[str]]:
    """``(the repository's page, where to fork it)`` on GitHub's web pages; None when the stored address cannot be read."""
    try:
        repo = parse_repo_url(app.repo_url)
    except AppError:
        return None, None
    return repo.web_url, f"{repo.web_url}/fork"


def _template_info(db: Session, app: App) -> Optional[AppTemplateInfo]:
    info = template_info(db, app)
    if info is None:
        return None
    owner = db.get(User, info["owner_user_id"]) if info["owner_user_id"] is not None else None
    return AppTemplateInfo(
        app_id=info["app_id"],
        app_name=info["app_name"],
        app_slug=info["app_slug"],
        owner=_member(db, owner),
        version=AppTemplateVersionRef(**info["version"]) if info["version"] else None,
        update_available=AppTemplateVersionRef(**info["update_available"]) if info["update_available"] else None,
    )


def _app_response(db: Session, app: App, user: Any, *, last_visited_at: Optional[datetime] = None) -> AppResponse:
    owner = db.get(User, app.owner_user_id)
    manage = can_manage(user, app)
    enabled, disabled_reason = app_enabled(db, app)
    releases = AppReleaseRepository(db)
    current = releases.get(app.current_release_id) if app.current_release_id else None
    view = host_state.status_of(app.id)
    state = (view.status or {}).get("state")
    manage_fields = None
    if manage:
        repo_web_url, fork_url = _repo_links(app)
        manage_fields = AppManageFields(
            repo_url=app.repo_url,
            repo_web_url=repo_web_url,
            fork_url=fork_url,
            repo_ref=app.repo_ref,
            entry_dir=app.entry_dir or "",
            env=app.env,
            llm_enabled=bool(app.llm_enabled),
            connectors_enabled=list(app.connectors_enabled or []),
            connectors_write=list(app.connectors_write or []),
            daily_credits=app.daily_credits,
            daily_tokens=app.daily_tokens,
            default_daily_credits=apps_settings.default_daily_credits(),
            default_daily_tokens=apps_settings.default_daily_tokens(),
            token_last_used_at=app.token_last_used_at,
            token_last_used_source=app.token_last_used_source,
            anyone_approved_by=app.anyone_approved_by,
            active_release=_release_summary(releases.active_for_app(app.id)),
            latest_release=_release_summary(releases.latest_for_app(app.id)),
        )
    return AppResponse(
        id=app.id,
        slug=app.slug,
        name=app.name,
        description=app.description,
        url=apps_settings.app_public_url(app.slug),
        type=app.type,
        visibility=app.visibility,
        effective_visibility=effective_visibility(app),
        anyone_pending_approval=anyone_pending_approval(app),
        owner=_member(db, owner) or AppMember(id=app.owner_user_id, username="", display_name=""),
        is_owner=app.owner_user_id == getattr(user, "id", None),
        can_manage=manage,
        enabled=enabled,
        disabled_reason=disabled_reason,
        desired_state=app.desired_state,
        is_template=bool(app.is_template),
        template_app_id=app.template_app_id,
        template_version_id=app.template_version_id,
        template=_template_info(db, app),
        has_icon=bool(app.icon_data),
        icon_url=_icon_url(app),
        deployed=bool(app.current_release_id),
        current_release=_release_summary(current),
        state=str(state) if state else None,
        state_reason=(view.status or {}).get("reason") if view.status else None,
        last_visited_at=last_visited_at,
        created_at=app.created_at,
        updated_at=app.updated_at,
        manage=manage_fields,
    )


def _release_response(db: Session, app: App, release: AppRelease, *, live: Optional[dict] = None) -> AppReleaseResponse:
    requester = db.get(User, release.requested_by_user_id) if release.requested_by_user_id else None
    log_tail = release.log_tail
    if live is not None and isinstance(live.get("log_tail"), str):
        log_tail = live["log_tail"]
    return AppReleaseResponse(
        id=release.id,
        app_id=release.app_id,
        kind=release.kind,
        status=release.status,
        commit_sha=release.commit_sha,
        ref=release.ref,
        commit_message=release.commit_message,
        requested_by=_member(db, requester),
        steps=release.steps,
        error_summary=release.error_summary,
        host_job_id=release.host_job_id,
        log_tail=log_tail,
        started_at=release.started_at,
        finished_at=release.finished_at,
        is_current=release.id == app.current_release_id,
    )


def _error_dict(error: Optional[AppError]) -> Optional[dict]:
    return error.to_payload() if error is not None else None


def _status_response(db: Session, app: App, service: AppService) -> AppStatusResponse:
    result = service.status(app)
    host_status = result["status"] if isinstance(result["status"], dict) else None
    enabled, disabled_reason = app_enabled(db, app)
    active = AppReleaseRepository(db).active_for_app(app.id)
    return AppStatusResponse(
        host_reachable=bool(result["host_reachable"]),
        live=bool(result["live"]),
        state=str(host_status.get("state")) if host_status and host_status.get("state") else None,
        reason=host_status.get("reason") if host_status else None,
        status=host_status,
        reported_at=result["reported_at"],
        error=_error_dict(result["error"]),
        enabled=enabled,
        disabled_reason=disabled_reason,
        desired_state=app.desired_state,
        deployed=bool(app.current_release_id),
        current_release_id=app.current_release_id,
        current_sha=current_sha(db, app),
        repo_ref=app.repo_ref,
        commits_behind=deploy_service.commits_behind(db, app),
        deploying=active is not None,
        active_release_id=active.id if active is not None else None,
        rollback_available=deploy_service.rollback_target(db, app) is not None,
        url=apps_settings.app_public_url(app.slug),
    )


# --- settings, inspection ------------------------------------------------------------


@router.get("/settings", response_model=AppSettingsResponse)
def get_apps_settings(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """What a page needs to know before it offers anything: the public address, the audiences, the account that reads repositories."""
    source = source_client(db)
    return AppSettingsResponse(
        public_base_url=apps_settings.public_base_url(),
        anyone_enabled=apps_settings.anyone_enabled(),
        github_host=web_host_of(source.api_url),
        service_account=source.account_login(),
        default_daily_credits=apps_settings.default_daily_credits(),
        default_daily_tokens=apps_settings.default_daily_tokens(),
        connector_types=list(APP_CONNECTOR_TYPES),
        visibilities=[item for item in APP_VISIBILITIES if item != "anyone" or apps_settings.anyone_enabled()],
        app_types=list(APP_TYPES),
        reserved_slugs=sorted(RESERVED_SLUGS),
    )


@router.post("/inspect", response_model=AppInspectResponse)
def inspect_repository(payload: AppInspectRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Read a repository directory the way registering it would, without registering it: the same errors, and the manifest."""
    service = _service(db)
    inspection = service.inspect(user, repo_url=payload.repo_url, ref=payload.ref, entry_dir=payload.entry_dir)
    manifest = inspection.manifest
    return AppInspectResponse(
        repo=inspection.repo.full_name,
        repo_url=inspection.repo.web_url,
        ref=inspection.resolved.ref,
        commit_sha=inspection.resolved.sha,
        commit_message=inspection.resolved.message,
        entry_dir=inspection.entry_dir,
        manifest={
            "name": manifest.name,
            "description": manifest.description,
            "type": manifest.type,
            "icon": manifest.icon,
            "entry": manifest.entry,
            "command": manifest.command,
            "requirements": manifest.requirements,
            "health_path": manifest.health_path,
            "startup_timeout": manifest.startup_timeout,
            "strip_prefix": manifest.strip_prefix,
            "owners": list(manifest.owners),
        },
        suggested_name=manifest.name,
        suggested_slug=service.default_slug(manifest.name),
    )


@router.get("/repo-refs", response_model=AppRepoRefsResponse)
def list_repository_refs(repo_url: str = Query(..., min_length=1), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The branches and tags of a repository the service account can read (one page of each)."""
    source = source_client(db)
    repo = source.parse(repo_url)
    record = source.ensure_readable(repo)
    return AppRepoRefsResponse(
        repo=repo.full_name,
        default_branch=str(record.get("default_branch") or "") or None,
        branches=[item["name"] for item in source.list_branches(repo)],
        tags=[item["name"] for item in source.list_tags(repo)],
    )


# --- list, create, read, change, delete ---------------------------------------------


@router.get("", response_model=AppListResponse)
def list_apps(
    scope: str = Query(default=SCOPE_MINE, description="mine, available (shared with me) or all (system administrators)."),
    q: Optional[str] = Query(default=None, description="Search in name, address and description."),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if scope not in SCOPES:
        raise AppValidationError(N_("scope must be mine, available or all"), code="scope_invalid")
    service = _service(db)
    if scope == SCOPE_ALL:
        _require_system_admin(user)
        apps = service.list_all(q=q)
    elif scope == SCOPE_AVAILABLE:
        apps = service.list_available(user, q=q)
    else:
        apps = service.list_mine(user, q=q)
    # One read of the host for the whole list, a few seconds old at most.
    reachable = host_state.refresh(service.host) if apps else False
    visits = AppTrafficRepository(db).last_visits_of_user(user.id, [app.id for app in apps])
    if scope == SCOPE_AVAILABLE:
        # What the member used last comes first, the rest by name.
        apps = sorted(
            apps,
            key=lambda app: (0, -visits[app.id].timestamp(), "") if app.id in visits else (1, 0.0, app.name.lower()),
        )
    return AppListResponse(
        scope=scope,
        apps=[_app_response(db, app, user, last_visited_at=visits.get(app.id)) for app in apps],
        host_reachable=reachable,
    )


@router.post("", response_model=AppCreateResponse, status_code=status.HTTP_201_CREATED)
def create_app(payload: AppCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(db)
    app = service.create(
        user,
        repo_url=payload.repo_url,
        ref=payload.ref,
        entry_dir=payload.entry_dir,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        visibility=payload.visibility,
    )
    release = None
    deploy_error = None
    if payload.deploy:
        try:
            release = deploy_service.start_deploy(app, None, user)
        except AppError as exc:
            deploy_error = exc.to_payload()
    db.refresh(app)
    return AppCreateResponse(
        app=_app_response(db, app, user),
        release=_release_response(db, app, release) if release is not None else None,
        deploy_error=deploy_error,
    )


@router.get("/{app_id}", response_model=AppResponse)
def get_app(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(db)
    app = service.get_for_view(app_id, user)
    visits = AppTrafficRepository(db).last_visits_of_user(user.id, [app.id])
    return _app_response(db, app, user, last_visited_at=visits.get(app.id))


@router.patch("/{app_id}", response_model=AppResponse)
def update_app(app_id: str, payload: AppUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    sent = payload.model_fields_set

    def field(name: str, *, nullable: bool = False) -> Any:
        if name not in sent:
            return UNSET
        value = getattr(payload, name)
        return UNSET if value is None and not nullable else value

    service.update(
        app,
        user,
        name=field("name"),
        description=field("description", nullable=True),
        visibility=field("visibility"),
        env=field("env"),
        repo_ref=field("repo_ref"),
        llm_enabled=field("llm_enabled"),
        connectors_enabled=field("connectors_enabled"),
        connectors_write=field("connectors_write"),
        daily_credits=field("daily_credits", nullable=True),
        daily_tokens=field("daily_tokens", nullable=True),
    )
    return _app_response(db, app, user)


@router.delete("/{app_id}")
def delete_app(
    app_id: str,
    purge_data: bool = Query(default=False, description="Also remove the app's data on the host."),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    service.delete(app, user, purge_data=purge_data)
    return {"ok": True}


# --- deployments ------------------------------------------------------------------------


@router.post("/{app_id}/deploy", response_model=AppReleaseResponse, status_code=status.HTTP_202_ACCEPTED)
def deploy_app(
    app_id: str,
    payload: Optional[AppDeployRequest] = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deploy a branch or a tag (the app's own when none is given). The answer is the new release; follow it with GET."""
    app = _service(db).get_for_manage(app_id, user)
    release = deploy_service.start_deploy(app, payload.ref if payload is not None else None, user)
    return _release_response(db, app, release)


@router.post("/{app_id}/deploy-latest", response_model=AppReleaseResponse, status_code=status.HTTP_202_ACCEPTED)
def deploy_latest(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Deploy the head of the branch or tag the app follows."""
    app = _service(db).get_for_manage(app_id, user)
    release = deploy_service.deploy_latest(app, user)
    return _release_response(db, app, release)


@router.post("/{app_id}/rollback", response_model=AppReleaseResponse)
def rollback_app(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return to the release before the current one."""
    app = _service(db).get_for_manage(app_id, user)
    release = deploy_service.start_rollback(app, user)
    return _release_response(db, app, release)


@router.get("/{app_id}/releases", response_model=AppReleaseListResponse)
def list_releases(
    app_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = _service(db).get_for_manage(app_id, user)
    target = deploy_service.rollback_target(db, app)
    return AppReleaseListResponse(
        releases=[_release_response(db, app, release) for release in AppReleaseRepository(db).list_for_app(app.id, limit=limit)],
        current_release_id=app.current_release_id,
        rollback_release_id=target.id if target is not None else None,
    )


def _release_or_404(db: Session, app: App, release_id: str) -> AppRelease:
    release = AppReleaseRepository(db).get_for_app(app.id, release_id)
    if release is None:
        raise AppNotFound(N_("Release not found"), code="release_not_found")
    return release


@router.get("/{app_id}/releases/{release_id}", response_model=AppReleaseResponse)
def get_release(app_id: str, release_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """One release: its steps, status and error, and the end of its log (read from the host while it runs)."""
    app = _service(db).get_for_manage(app_id, user)
    release = _release_or_404(db, app, release_id)
    live = None
    if release.status == RELEASE_RUNNING and release.host_job_id:
        try:
            live = host_client().get_job(release.host_job_id)
        except AppHostError:
            live = None
    return _release_response(db, app, release, live=live)


@router.get("/{app_id}/releases/{release_id}/log", response_model=AppReleaseLogResponse)
def get_release_log(
    app_id: str,
    release_id: str,
    offset: int = Query(default=0, ge=0),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The host job's log from byte ``offset`` on, for a page that follows a deployment."""
    app = _service(db).get_for_manage(app_id, user)
    release = _release_or_404(db, app, release_id)
    if not release.host_job_id:
        return AppReleaseLogResponse(text="", offset=offset, size=0)
    text, size = host_client().get_job_log(release.host_job_id, offset)
    return AppReleaseLogResponse(text=text, offset=offset, size=size)


# --- running ----------------------------------------------------------------------------


@router.post("/{app_id}/start", response_model=AppStatusResponse)
def start_app(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    service.start(app, user)
    return _status_response(db, app, service)


@router.post("/{app_id}/stop", response_model=AppStatusResponse)
def stop_app(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    service.stop(app, user)
    return _status_response(db, app, service)


@router.post("/{app_id}/restart", response_model=AppStatusResponse)
def restart_app(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    service.restart(app, user)
    return _status_response(db, app, service)


@router.get("/{app_id}/status", response_model=AppStatusResponse)
def get_status(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The app on the host and how far it is behind its branch. Answers even when the host does not."""
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    return _status_response(db, app, service)


@router.get("/{app_id}/logs", response_model=AppLogsResponse)
def get_logs(
    app_id: str,
    tail: int = Query(default=200, ge=1, le=5000),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The end of the app's log on the host. An app the host has not heard of (never deployed) has no lines yet."""
    app = _service(db).get_for_manage(app_id, user)
    try:
        return AppLogsResponse(**host_client().logs(app.id, tail))
    except AppHostError as exc:
        if exc.host_code != HOST_APP_NOT_FOUND:
            raise
        return AppLogsResponse(lines=[], truncated=False)


@router.get("/{app_id}/logs/stream")
async def stream_logs(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The host's live log stream, passed through as ``text/event-stream`` (``data: {"line": "..."}``).

    For an app the host has not heard of (never deployed) the stream is empty and ends at once.
    """
    app = await run_in_threadpool(lambda: _service(db).get_for_manage(app_id, user))
    target_id = app.id
    stack = AsyncExitStack()
    chunks = None
    try:
        chunks = await stack.enter_async_context(host_client().stream_logs(target_id))
    except AppHostError as exc:
        await stack.aclose()
        if exc.host_code != HOST_APP_NOT_FOUND:
            raise
    except BaseException:
        await stack.aclose()
        raise

    async def body():
        try:
            if chunks is not None:
                async for chunk in chunks:
                    yield chunk
        finally:
            await stack.aclose()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- token, icon, data, history -------------------------------------------------------


def _token_response(app: App, **extra: Any) -> JSONResponse:
    body = AppTokenResponse(
        token=app.token_value,
        last_used_at=app.token_last_used_at,
        last_used_source=app.token_last_used_source,
        **extra,
    )
    return JSONResponse(body.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


@router.get("/{app_id}/token", response_model=AppTokenResponse)
def get_token(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The app's token, for its owner to copy into a local ``EFP_APP_TOKEN``. Reading it is recorded."""
    app = _service(db).get_for_manage(app_id, user)
    audit.record_app_event(db, app.id, audit.ACTION_TOKEN_VIEWED, actor_user_id=user.id)
    return _token_response(app)


@router.post("/{app_id}/token/reset", response_model=AppTokenResponse)
def reset_token(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """A new token: the old one stops working at once and the host restarts the app with the new one."""
    app = _service(db).get_for_manage(app_id, user)
    sync = app_tokens.reset_token(db, app, user.id, host=host_client())
    return _token_response(app, host_synced=sync.synced, host_error=_error_dict(sync.error))


@router.get("/{app_id}/icon")
def get_icon(app_id: str, request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The app's icon as the last deployment read it: a raster image, never markup."""
    app = _service(db).get_for_view(app_id, user)
    if not app.icon_data or not app.icon_content_type:
        raise AppNotFound(N_("This app has no icon"), code="icon_not_found")
    etag = '"' + hashlib.sha1(app.icon_data).hexdigest() + '"'
    headers = {
        "Cache-Control": "private, max-age=86400",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=bytes(app.icon_data), media_type=app.icon_content_type, headers=headers)


@router.get("/{app_id}/data", response_model=AppDataResponse)
def get_data_usage(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """How much the app keeps on the host."""
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    result = service.status(app)
    host_status = result["status"] if isinstance(result["status"], dict) else {}
    size = host_status.get("data_bytes")
    return AppDataResponse(
        data_bytes=int(size) if isinstance(size, (int, float)) else None,
        host_reachable=bool(result["host_reachable"]),
        live=bool(result["live"]),
        reported_at=result["reported_at"],
    )


@router.delete("/{app_id}/data", response_model=AppDataResponse)
def clear_data(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Empty the app's data on the host. Cannot be undone."""
    service = _service(db)
    app = service.get_for_manage(app_id, user)
    host_status = service.clear_data(app, user)
    size = host_status.get("data_bytes") if isinstance(host_status, dict) else None
    return AppDataResponse(
        data_bytes=int(size) if isinstance(size, (int, float)) else None,
        host_reachable=True,
        live=True,
        reported_at=datetime.utcnow(),
    )


def _event_response(db: Session, event: AppAuditEvent, users: dict[int, Optional[User]]) -> AppEventResponse:
    actor = None
    if event.actor_user_id is not None:
        if event.actor_user_id not in users:
            users[event.actor_user_id] = UserRepository(db).get_by_id(event.actor_user_id)
        actor = _member(db, users[event.actor_user_id])
    return AppEventResponse(id=event.id, action=event.action, actor=actor, detail=event.detail, created_at=event.created_at)


@router.get("/{app_id}/events", response_model=AppEventListResponse)
def list_events(
    app_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What was done to the app and what the host reported about it, newest first."""
    app = _service(db).get_for_manage(app_id, user)
    users: dict[int, Optional[User]] = {}
    return AppEventListResponse(
        events=[_event_response(db, event, users) for event in AppAuditRepository(db).list_for_app(app.id, limit=limit)]
    )


# --- system administrators ----------------------------------------------------------------


@router.post("/{app_id}/disable", response_model=AppResponse)
def disable_app(app_id: str, payload: AppDisableRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Take an app down with a reason its visitors read."""
    _require_system_admin(user)
    service = _service(db)
    app = service.get(app_id)
    service.disable(app, user, payload.reason)
    return _app_response(db, app, user)


@router.post("/{app_id}/enable", response_model=AppResponse)
def enable_app(app_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_system_admin(user)
    service = _service(db)
    app = service.get(app_id)
    service.enable(app, user)
    return _app_response(db, app, user)


@router.post("/{app_id}/anyone-approval", response_model=AppResponse)
def set_anyone_approval(
    app_id: str, payload: AppAnyoneApprovalRequest, user=Depends(get_current_user), db: Session = Depends(get_db)
):
    """Approve an app that asks to be open without sign-in, or withdraw the approval."""
    _require_system_admin(user)
    service = _service(db)
    app = service.get(app_id)
    service.set_anyone_approval(app, user, payload.approved)
    return _app_response(db, app, user)


@router.post("/{app_id}/transfer", response_model=AppResponse)
def transfer_app(app_id: str, payload: AppTransferRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Give the app to another member (by Portal username). Its token is replaced and its capabilities are switched off."""
    _require_system_admin(user)
    service = _service(db)
    app = service.get(app_id)
    new_owner = UserRepository(db).get_by_username_case_insensitive(payload.username)
    if new_owner is None:
        raise AppValidationError(N_("No member has the username {username}."), code="owner_invalid", username=payload.username)
    service.transfer_owner(app, user, new_owner)
    return _app_response(db, app, user)
