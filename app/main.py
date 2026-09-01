import logging
import time
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.routing import Mount

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.proxy import router as proxy_router
from app.api.runtime_profiles import router as runtime_profiles_router
from app.api.agent_tasks import router as agent_tasks_router
from app.api.internal_session_metadata import router as internal_session_metadata_router
from app.api.delegation_rules import router as delegation_rules_router
from app.api.runtime_capability_catalog import router as runtime_capability_catalog_router
from app.api.agents import router as agents_router
from app.api.assistant_types import router as assistant_types_router
from app.api.git_repos import router as git_repos_router
from app.api.users import router as users_router
from app.api.copilot import router as copilot_router
from app.config import get_settings
from app.db import SessionLocal, engine
from app.log_context import bind_log_context, generate_span_id, generate_trace_id, reset_log_context
from app.logger import setup_logging
from app.services.access_control_service import AccessControlService
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.schema_guard import (
    assert_phase5_schema_compatibility,
    assert_portal_schema_ready,
    assert_runtime_profile_schema_compatibility,
)
from app.web import router as web_router
from app.services.delegation_worker import worker_singleton
from app.services.agent_task_reconcile_worker import agent_task_reconcile_worker_singleton
from app.services.idle_agent_stop_worker import idle_agent_stop_worker_singleton

logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)


@app.middleware("http")
async def bind_request_log_context(request, call_next):
    trace_id = generate_trace_id()
    token = bind_log_context(
        trace_id=trace_id,
        span_id=generate_span_id(),
        parent_span_id="-",
        path=request.url.path,
    )
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        # trace_id is the closed-over local, never re-read from the log
        # contextvar: the contextvar is reset here and stays reset while a
        # StreamingResponse body is drained, so reading it back renders '-'.
        # duration_ms is time-to-response-headers; for SSE the body streams on
        # afterwards, so pair this with the proxy's ttfb_ms/total_ms line.
        logger.info(
            "HTTP request end method=%s path=%s status=%s duration_ms=%s trace_id=%s",
            request.method,
            request.url.path,
            status_code,
            round((time.perf_counter() - started_at) * 1000, 2),
            trace_id,
        )
        reset_log_context(token)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.on_event("startup")
def on_startup() -> None:
    setup_logging(logging.DEBUG if settings.debug else logging.INFO)
    assert_portal_schema_ready(engine)
    assert_phase5_schema_compatibility(engine)
    assert_runtime_profile_schema_compatibility(engine)

    db = SessionLocal()
    try:
        runtime_profile_service = RuntimeProfileService(db)
        admin_user = AccessControlService(db).ensure_configured_access(settings)
        if admin_user:
            runtime_profile_service.ensure_user_has_default_profile(admin_user)
        elif not settings.bootstrap_admin_password:
            logger.warning("BOOTSTRAP_ADMIN_PASSWORD not set; a missing bootstrap admin will not be created")

        runtime_profile_service.ensure_defaults_for_all_users(db)
        # Migrate any persisted profile still on a legacy (non-Copilot) provider:
        # canonicalize now coerces provider/model to GitHub Copilot, and this
        # rewrites existing rows so the UI and stored config stay consistent.
        runtime_profile_service.sanitize_all_persisted_runtime_profiles()
    finally:
        db.close()

    if settings.delegation_rules_worker_enabled:
        worker_singleton.start()
    if settings.agent_task_reconcile_worker_enabled:
        agent_task_reconcile_worker_singleton.start()
    if settings.idle_agent_stop_worker_enabled:
        idle_agent_stop_worker_singleton.start()


@app.on_event("shutdown")
def shutdown_delegation_worker() -> None:
    worker_singleton.stop()
    agent_task_reconcile_worker_singleton.stop()
    idle_agent_stop_worker_singleton.stop()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/actuator/health")
def actuator_health() -> dict[str, str]:
    return {"status": "ok"}

class CachedStaticFiles(StaticFiles):
    """Static assets that always revalidate but rarely re-download.

    Filenames are not content-hashed, so any max-age at all means a deploy can
    serve stale JS until it expires — observed in practice: a browser kept
    running a five-minute-old chat_ui.js after a change shipped. "no-cache" is
    not "no store": the browser still caches the bytes and revalidates with the
    ETag, so an unchanged asset costs a 304 instead of a full transfer.

    Content-hashed filenames from a build step would allow immutable year-long
    caching and drop the revalidation entirely; until then, correctness after a
    deploy is worth more than the conditional requests.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
        return response


# Serve the ~640KB chat bundle and ~1.3MB of vendored libs compressed; without
# this they went out as raw bytes even when the client offered gzip.
#
# Compression is scoped to /static rather than added app-wide on purpose: the
# chat proxy streams text/event-stream, and running those responses through gzip
# risks buffering tokens behind the compressor. Static assets are where
# essentially all of the transfer weight is anyway.
static_app = Starlette(routes=[Mount("/", app=CachedStaticFiles(directory="app/static"))])
static_app.add_middleware(GZipMiddleware, minimum_size=1024)

app.mount("/static", static_app, name="static")

app.include_router(web_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(agents_router)
app.include_router(assistant_types_router)
app.include_router(git_repos_router)
app.include_router(runtime_profiles_router)
app.include_router(admin_router)
app.include_router(proxy_router)
app.include_router(copilot_router)
app.include_router(agent_tasks_router)
app.include_router(delegation_rules_router)
app.include_router(runtime_capability_catalog_router)
app.include_router(internal_session_metadata_router)
